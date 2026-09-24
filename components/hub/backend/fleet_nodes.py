"""The controller's list of Macs that have joined it.

A node joins by telling its controller who it is and where it answers. The controller records
that, and then reports each node's state by asking it directly — a stored "joined" flag is not
evidence that a Mac is still there, so it is never shown as one.

This is deliberately the smallest thing that makes one app a controller and another a node:
identity, address, and a live reachability check. Scheduling work onto a joined node is the
next step and is not claimed here.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
import tempfile
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from .registry import DATA_DIR
from .peers import fleet_token, fresh_snapshot

FILE_NAME = "fleet_nodes.json"
MAX_NODES = 64
ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
# A join request is authenticated with the controller's own credential, so it is not a public
# endpoint. The address is still validated: a controller must never be told to fetch an
# arbitrary URL, which would turn a join into a request forgery against this Mac's network.
PROBE_TIMEOUT = httpx.Timeout(6.0, connect=3.0)
# How a controller asks a node about itself. `/api/version` is in `auth.PUBLIC_PATHS`, so it
# answers 200 even to a credential the node rejects - which made a node that had taken a
# different Fleet Token read as reachable while no dispatch could reach it. This is the route a
# peer Hub reads for the same purpose, it is in `auth.FLEET_ALLOWED`, and its answer carries the
# host facts a controller needs to decide where work fits.
PROBE_PATH = "/api/hub/resources?local_only=true"
_CREDENTIAL_DETAIL = ("the node answered but rejected this controller's credential ({why}) - "
                      "re-join it with the current Fleet Token")


def store_path() -> Path:
    return DATA_DIR / FILE_NAME


def _read() -> dict[str, dict[str, Any]]:
    try:
        value = json.loads(store_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(value, dict):
        return {}
    return {key: row for key, row in value.items()
            if isinstance(row, dict) and ID_PATTERN.fullmatch(str(key))}


def _write(nodes: dict[str, dict[str, Any]]) -> None:
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(nodes, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def validate_address(value: object) -> str:
    """Accept only a plain http address on a private or loopback IP.

    A controller is asked to contact whatever a node claims. Restricting this to literal
    private addresses keeps a join from pointing this Mac at an arbitrary host, and keeps the
    stored value something a human can read back and recognise.
    """
    import ipaddress
    from urllib.parse import urlsplit

    text = str(value or "").strip()
    parsed = urlsplit(text)
    if parsed.scheme != "http" or not parsed.hostname or parsed.port is None:
        raise ValueError("address must be http://<ip>:<port>")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("address must not carry credentials, a query or a fragment")
    if parsed.path not in ("", "/"):
        raise ValueError("address must not carry a path")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError as exc:
        raise ValueError("address must use a literal IP, not a name") from exc
    if address.version != 4:
        raise ValueError("address must be IPv4")
    allowed = (address.is_loopback or address.is_private or address.is_link_local
               or address in ipaddress.ip_network("100.64.0.0/10"))
    if not allowed:
        raise ValueError("address must be a private or Tailscale address")
    return f"http://{address}:{parsed.port}"


# The modalities a node may offer. A controller dispatches by modality, so this is the only
# vocabulary the fleet routing understands. Music joined the app on 2026-09-22; while it was
# missing here, a node reporting its Music worker was refused outright at join time, so no
# current build could connect to a controller at all.
NODE_MODALITIES = {"image", "voice", "transcription", "music"}


def _studios(value: object) -> list[dict[str, str]]:
    """Validate what a node says it runs.

    A node reports its own studios rather than the controller assuming all three: a Mac whose
    Image worker failed to start must not be handed image work, and a future build may run
    fewer studios than this one.
    """
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 8:
        raise ValueError("studios must be a list of at most 8 entries")
    seen: list[dict[str, str]] = []
    for entry in value:
        if not isinstance(entry, dict):
            raise ValueError("each studio must be an object")
        worker = str(entry.get("id") or "").strip().lower()
        modality = str(entry.get("modality") or "").strip().lower()
        if not ID_PATTERN.fullmatch(worker):
            raise ValueError("studio id must be lowercase letters, numbers, dots, dashes or underscores")
        if modality not in NODE_MODALITIES:
            raise ValueError(f"studio modality must be one of {sorted(NODE_MODALITIES)}")
        seen.append({"id": worker, "modality": modality})
    return seen


def join(payload: dict[str, Any]) -> dict[str, Any]:
    """Record one node, or refresh what a rejoining node reports about itself."""
    node_id = str(payload.get("node_id") or "").strip().lower()
    if not ID_PATTERN.fullmatch(node_id):
        raise ValueError("node_id must be lowercase letters, numbers, dots, dashes or underscores")
    name = str(payload.get("name") or "").strip()
    if not 1 <= len(name) <= 120:
        raise ValueError("name must be between 1 and 120 characters")
    address = validate_address(payload.get("address"))
    studios = _studios(payload.get("studios"))
    nodes = _read()
    if node_id not in nodes and len(nodes) >= MAX_NODES:
        raise ValueError(f"this controller already has {MAX_NODES} nodes")
    existing = nodes.get(node_id) or {}
    nodes[node_id] = {
        "id": node_id,
        "name": name,
        "address": address,
        "role": "agent",
        "studios": studios,
        "joined_at": existing.get("joined_at") or time.time(),
        "updated_at": time.time(),
    }
    _write(nodes)
    return dict(nodes[node_id])


def leave(node_id: str) -> bool:
    nodes = _read()
    if node_id not in nodes:
        return False
    del nodes[node_id]
    _write(nodes)
    return True


def remote_studios() -> list[dict[str, Any]]:
    """Dispatchable studio entries for every joined node.

    These take the shape the Hub's own workers use, with two differences that decide how the
    existing routing treats them: `machine` is the node's identity rather than "local", which
    sends the request down the peer path, and the credential is the fleet token, which is the
    only thing a controller holds for someone else's Mac.
    """
    entries: list[dict[str, Any]] = []
    nodes = _read()
    token = fleet_token() or ""
    for node_id in sorted(nodes):
        node = nodes[node_id]
        address = node.get("address") or ""
        try:
            parsed = urlsplit(address)
            host, port = parsed.hostname, parsed.port
        except ValueError:
            continue
        if not host or not port:
            continue
        for studio in node.get("studios") or []:
            worker, modality = studio.get("id"), studio.get("modality")
            if not worker or modality not in NODE_MODALITIES:
                continue
            entries.append({
                "id": f"{node_id}:{worker}",
                "title": f"{node.get('name') or node_id} · {worker.title()}",
                "modality": modality,
                "host": host,
                "port": port,
                "hub_port": port,
                "machine": node_id,
                "local": False,
                "hub_token": token,
                "studio_token": token,
                "runtime_identity": {"id": f"fleet:{node_id}", "compatible": True},
                "managed_output_roots": [],
                "base_url": f"http://{host}:{port}",
                "emoji": "🛰️",
                "fleet_node": node_id,
            })
    return entries


def _probe_client() -> httpx.AsyncClient:
    """The client a probe owns, presenting the only credential a controller holds.

    A node is somebody else's Mac: the Hub token stays on the Mac that owns it, so a controller
    authenticates with the Fleet Token. A caller that injects its own client is responsible for
    the credential that client carries - the peer poll and `remote_inventory` set the header per
    request themselves.
    """
    token = fleet_token()
    headers = {"X-Hub-Token": token} if token else {}
    return httpx.AsyncClient(follow_redirects=False, headers=headers)


def _from_snapshot(row: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    """A probe result derived from a peer poll's snapshot.

    The poll reads the same route a direct probe reads, so the states line up one for one: a
    rejected credential is `unauthorized` (the node answered and refused *this controller*), a
    connected machine is `reachable`, and anything else is a machine that is not answering now.
    The host facts travel with it, so a roster read still shows what the node is.
    """
    host = snapshot.get("host") if isinstance(snapshot.get("host"), dict) else {}
    carried = {"host": host, "version": snapshot.get("version")}
    if snapshot.get("auth") is False:
        return {**row, **carried, "state": "unauthorized",
                "detail": _CREDENTIAL_DETAIL.format(
                    why=str(snapshot.get("status") or "the credential was refused"))}
    if snapshot.get("reachable") and snapshot.get("auth", True):
        return {**row, **carried, "state": "reachable", "detail": None}
    return {**row, **carried, "state": "unreachable",
            "detail": f"could not reach the node ({snapshot.get('status') or 'unreachable'})"}


async def probe(node: dict[str, Any], client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """Ask one node whether it is there, and report what it says about itself.

    Reachability is asked for, never assumed: a node that was joined last week and has been
    switched off must read as unreachable rather than as available.

    The machine is asked about itself on the route the peer poll already reads, and while that
    poll's snapshot is still current this reuses it: the roster then reports the same evidence
    the dispatch path acts on instead of paying for a second read of the same node. A machine
    with no current snapshot - including one that has just joined - is dialled directly, so a
    join is still visible immediately rather than at the next poll.
    """
    # What the node said about itself when it joined, alongside what it answers now.
    #
    # `role` and `studios` were recorded at join time and then dropped here, so a Fleet page could
    # list a node's name and reachability but not what it is or what it can run. A controller
    # deciding where to send work needs both, and re-reading them from the node would mean the
    # roster depended on every node being awake.
    row = {key: node.get(key) for key in ("id", "name", "address", "role", "studios", "joined_at")}
    snapshot = fresh_snapshot(str(node.get("id") or ""))
    if snapshot is not None:
        return _from_snapshot(row, snapshot)
    owned = client is None
    if owned:
        client = _probe_client()
    try:
        response = await client.get(f"{node['address']}{PROBE_PATH}", timeout=PROBE_TIMEOUT)
        if response.status_code in (401, 403):
            # The node answered, so it is up - it refused *this controller*. Reported as plain
            # "unreachable", the operator goes looking at the network and at the wrong Mac, when the
            # fix is the credential: a node re-joined with its own token, or a controller whose
            # fleet token was replaced. The state is its own so the Fleet page can say so and the
            # counts do not hide it.
            return {**row, "state": "unauthorized",
                    "detail": _CREDENTIAL_DETAIL.format(why=f"HTTP {response.status_code}")}
        if not response.is_success:
            return {**row, "state": "error", "detail": f"node answered HTTP {response.status_code}"}
        try:
            body = response.json()
        except ValueError:
            return {**row, "state": "error", "detail": "node returned an invalid response"}
        if not isinstance(body, dict):
            return {**row, "state": "error", "detail": "node returned an invalid response"}
        # The probe's answer is also the host snapshot the Fleet page shows for this machine, so
        # one pass over the roster feeds both routes rather than the page waiting for the poll.
        host = body.get("host") if isinstance(body.get("host"), dict) else {}
        return {**row, "state": "reachable", "detail": None, "host": host,
                "version": body.get("version"), "source": body.get("source")}
    except (httpx.HTTPError, OSError) as exc:
        return {**row, "state": "unreachable", "detail": f"could not reach the node: {exc}"}
    finally:
        if owned:
            await client.aclose()


async def inventory(client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """Every joined node with its live state, plus a count of what answered."""
    nodes = _read()
    node_ids = sorted(nodes)
    # Probed together, not one after another. Each probe waits out its own timeout, so a fleet read
    # used to cost one timeout per silent node: ten switched-off Macs turned the Fleet page into a
    # ten-timeout stall, which the app sees as the Hub not answering at all. Order is preserved.
    rows = list(await asyncio.gather(*(probe(nodes[node_id], client) for node_id in node_ids)))
    # The role this Hub is actually in, not a constant.
    #
    # This was hardcoded to "controller", so a standalone Mac answered "controller" here while
    # `/api/hub/capabilities` and `/api/hub/controller` answered "standalone" from the saved
    # setting. Measured on a standalone Mac on 2026-09-18, and it matters directly: gateway-kh
    # decides "can this site own nodes" from this field, so a standalone location was advertised as
    # able to accept enrolments it would never manage.
    from . import control_plane
    return {
        "role": control_plane.load_settings()["role"],
        # Who is answering. A node reads this route to find out which group it is in, and a group
        # with no name on its head is a list of addresses rather than a pool.
        "machine": this_machine(),
        "nodes": rows,
        "summary": {
            "total": len(rows),
            "reachable": sum(row["state"] == "reachable" for row in rows),
            "unreachable": sum(row["state"] == "unreachable" for row in rows),
            "unauthorized": sum(row["state"] == "unauthorized" for row in rows),
            "error": sum(row["state"] == "error" for row in rows),
        },
    }


def this_machine() -> dict[str, str]:
    """How this Mac is named in any fleet listing: the key it routes by, and the name it shows.

    `controller_id` is the identity a join registers and the rest of the Hub routes by, and
    `machine_name` is what the operator renamed this Mac to. A pool listing needs both, because
    a row has to be matched against routing by the key and recognised by a person by the name.
    """
    from . import control_plane
    settings = control_plane.public_settings()
    return {"id": str(settings.get("controller_id") or "local"),
            "name": str(settings.get("machine_name") or "This Mac")}


def membership() -> dict[str, Any]:
    """Which group this Mac is in, from this Mac's own record rather than the app's.

    Two things have to agree for this Mac to be a node: it holds a credential it did not mint,
    and it recorded the controller it took that credential from. The credential is the test, not
    the role - joining a controller and being switched to the Node role are separate decisions,
    and a Mac that has done the first is already in the group whatever its role still says.
    """
    from . import control_plane
    settings = control_plane.load_settings()
    role = str(settings.get("role") or "standalone")
    parent = settings.get("parent_controller_url")
    adopted = str(settings.get("fleet_credential_origin") or "minted") == "adopted"
    if not adopted or not parent:
        return {"member": False, "role": role, "controller_url": None}
    try:
        address = validate_address(parent)
    except ValueError:
        # A stored address that no longer validates is not a reason to fail the page: it is a
        # reason to say this Mac cannot name its controller, which the caller renders as such.
        return {"member": False, "role": role, "controller_url": None}
    return {"member": True, "role": role, "controller_url": address}


async def remote_inventory(address: str, token: str,
                           client: httpx.AsyncClient | None = None) -> dict[str, Any] | None:
    """Read a controller's roster: the pool this Mac was joined into, as its head sees it.

    Best-effort by design. A node whose controller is asleep is still a node, and it still has to
    render its own state, so an unreachable or refusing controller answers ``None`` here and the
    caller reports the group as unread rather than showing an empty one.
    """
    owned = client is None
    if owned:
        client = httpx.AsyncClient(follow_redirects=False)
    try:
        response = await client.get(f"{address}/api/hub/fleet/nodes", timeout=PROBE_TIMEOUT,
                                    headers={"X-Hub-Token": token} if token else {})
        if not response.is_success:
            return None
        body = response.json()
    except (httpx.HTTPError, OSError, ValueError):
        return None
    finally:
        if owned:
            await client.aclose()
    if not isinstance(body, dict) or not isinstance(body.get("nodes"), list):
        return None
    return body


def _member(row: dict[str, Any], *, role: str) -> dict[str, Any]:
    """One machine in a pool, in the one shape every caller reads.

    `probe` names a machine `id`/`name` because that is what the roster stores; the overview names
    it `machine`/`label` because that is what a row shows. Converting in one place is what keeps a
    controller from appearing in the pool as an entry with no name at all.
    """
    machine = str(row.get("id") or "")
    return {
        "machine": machine,
        "label": str(row.get("name") or machine),
        "role": role,
        "address": row.get("address"),
        "state": row.get("state"),
        "detail": row.get("detail"),
        "version": row.get("version"),
        # The probe's host snapshot travels with the row: the overview shows one machine's chip
        # and memory from the same read that decided it was reachable.
        "host": row.get("host") or {},
        "studios": row.get("studios") or [],
    }


async def pool(client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """Every machine in the group this Mac belongs to, from whichever end of it this Mac is.

    A controller knows its group because it holds the roster. A node does not - it holds a
    credential and a controller address - so it reads the roster back from the controller it
    joined. Both ends then answer the same list, which is what makes "my pool" mean the same
    thing on the Mac doing the work and the Mac handing it out.

    Every row is probed before it is reported, so a machine that is off reads as off rather than
    as absent: belonging to a group is a fact about the roster, and being up is a fact about now.
    """
    mine = await inventory(client)
    rows: list[dict[str, Any]] = [_member(row, role="agent") for row in mine["nodes"]]
    known = {row["machine"] for row in rows}
    result: dict[str, Any] = {
        "machine": mine["machine"], "role": mine["role"], "nodes": rows,
        "controller": None, "readable": True,
    }
    member = membership()
    if not member["member"]:
        # A controller's pool is exactly its roster. `readable` stays true: nothing failed.
        return result
    remote = await remote_inventory(member["controller_url"], fleet_token() or "", client)
    if remote is None:
        # The group is known but its head did not answer. The controller is still listed, as a
        # machine that is not answering, which is the difference between a broken node and an
        # empty group.
        result["readable"] = False
        result["controller"] = _member(await probe(
            {"id": "controller", "name": "Controller", "address": member["controller_url"]},
            client), role="controller")
        return result
    head = remote.get("machine") if isinstance(remote.get("machine"), dict) else {}
    controller_id = str(head.get("id") or "controller")
    result["controller"] = _member(await probe(
        {"id": controller_id, "name": str(head.get("name") or "Controller"),
         "address": member["controller_url"]}, client), role="controller")
    known.add(controller_id)
    for row in remote["nodes"]:
        entry = _member(row, role="agent")
        if not entry["machine"] or entry["machine"] == mine["machine"]["id"] \
                or entry["machine"] in known:
            continue
        known.add(entry["machine"])
        rows.append(entry)
    return result
