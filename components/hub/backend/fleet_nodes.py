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
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
from pathlib import Path
import tempfile
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from .registry import DATA_DIR
from .peers import fleet_token, fresh_snapshot
from .vocabulary import NODE_MODALITIES

log = logging.getLogger("studiohub.fleet_nodes")

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

#: Where **this Mac** keeps the credential its controller minted for it, when this Mac is a
#: node. It is this Mac's own membership secret and never leaves it: a controller stores only
#: the hash (see `join`), so revoking one machine cannot touch another.
NODE_CREDENTIAL_FILE = DATA_DIR / ".node_credential"

#: The sentence a removed Mac is refused with, by name and with the date. Kept as one string
#: because three places say it — the join refusal, the roster read, and the node's own UI — and
#: a reader who sees two different sentences cannot tell they are the same decision.
REMOVED_DETAIL = ("this controller removed this Mac on {when}; re-admit it from Connections "
                  "to join again")


NEVER_CONNECTED_AFTER_S = 24 * 3600


def store_path() -> Path:
    return DATA_DIR / FILE_NAME


def _empty() -> dict[str, Any]:
    return {"version": 2, "nodes": {}, "removed": {}}


def _document() -> dict[str, Any]:
    """The whole file: the live roster **and** the machines this controller has removed.

    The removed set is not bookkeeping. The owner removes machines to **sell** them, so a
    removed Mac must never re-add itself, and a controller that forgets the removal the
    moment the row is deleted cannot tell "never joined" from "evicted". Keeping both in one
    file means one write keeps them consistent.
    """
    try:
        value = json.loads(store_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty()
    if not isinstance(value, dict):
        return _empty()
    if "nodes" in value or "removed" in value:
        nodes = value.get("nodes") if isinstance(value.get("nodes"), dict) else {}
        removed = value.get("removed") if isinstance(value.get("removed"), dict) else {}
    else:
        # The file used to be the roster itself: `{node_id: row}`. Read it as one rather than
        # discarding a controller's whole fleet on the first read after an update.
        nodes, removed = value, {}
    clean_nodes = {key: row for key, row in nodes.items()
                   if isinstance(row, dict) and ID_PATTERN.fullmatch(str(key))}
    clean_removed = {key: row for key, row in removed.items()
                     if isinstance(row, dict) and ID_PATTERN.fullmatch(str(key))}
    return {"version": 2, "nodes": clean_nodes, "removed": clean_removed}


def _read() -> dict[str, dict[str, Any]]:
    return _document()["nodes"]


def _removed() -> dict[str, dict[str, Any]]:
    return _document()["removed"]


def removed() -> dict[str, dict[str, Any]]:
    """The machines this controller has evicted, newest first."""
    rows = {key: {k: v for k, v in row.items() if k != "revoked_hash"}
            for key, row in _removed().items()}
    return dict(sorted(rows.items(), key=lambda item: item[1].get("removed_at") or 0,
                       reverse=True))


def _write(nodes: dict[str, dict[str, Any]],
           removed_rows: dict[str, dict[str, Any]] | None = None) -> None:
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {"version": 2, "nodes": nodes,
                "removed": _removed() if removed_rows is None else removed_rows}
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(document, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


# -- this Mac's own node credential -----------------------------------------
def node_credential() -> str | None:
    """The credential this Mac was issued when it joined its controller, or `None`.

    Held `0600` in this Mac's own data directory and presented on every call this Mac makes
    *to* its controller. It is deliberately not the Fleet token: the Fleet token is the
    group's shared enrolment secret, and a machine that is sold must not take a working
    copy of the group's credential with it.
    """
    try:
        if NODE_CREDENTIAL_FILE.exists():
            os.chmod(NODE_CREDENTIAL_FILE, 0o600)
            value = NODE_CREDENTIAL_FILE.read_text(encoding="utf-8").strip()
            return value or None
    except OSError:
        return None
    return None


def set_node_credential(value: str | None) -> None:
    """Store (or clear) this Mac's own node credential."""
    text = (value or "").strip()
    if not text:
        NODE_CREDENTIAL_FILE.unlink(missing_ok=True)
        return
    NODE_CREDENTIAL_FILE.parent.mkdir(parents=True, exist_ok=True)
    NODE_CREDENTIAL_FILE.write_text(text + "\n", encoding="utf-8")
    os.chmod(NODE_CREDENTIAL_FILE, 0o600)


def hash_credential(value: str) -> str:
    """What the controller stores for a node's credential: its hash, never the value.

    A controller that kept the plaintext could authenticate *as* the node, and a stolen
    `fleet_nodes.json` would hand over every machine's credential at once. A plain SHA-256 is
    right here where a password hash would not be: the value is 32 bytes of `secrets` entropy,
    so there is no dictionary to run.
    """
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _mint_credential() -> str:
    return secrets.token_urlsafe(32)


def verify_node_credential(node_id: str, offered: str | None) -> bool:
    """Whether `offered` is the credential this controller issued to `node_id`."""
    row = _read().get(str(node_id or "").lower()) or {}
    stored = str(row.get("credential_hash") or "")
    if not stored or not offered:
        return False
    return hmac.compare_digest(stored, hash_credential(offered))


def note_seen(node_ids: list[str], startups: dict[str, dict[str, Any]] | None = None) -> float | None:
    """Stamp `last_seen_at` for the nodes that just answered. Returns the stamp, or `None`.

    Batched deliberately: `inventory()` probes every node on every fleet read, and a write
    per node would turn one page refresh into fifty-three file rewrites. `last_seen_at` is
    the answer to *"is this Mac switched off, or gone for a month?"* — B1, and M20 before
    it: without it a machine that is off and a machine that was scrapped look the same.

    The caller stamps the rows it is about to publish with the returned value, so the read
    that *caused* the stamp is the read that reports it. Leaving the rows alone made the
    field always one fleet read behind, which is the kind of number that looks live and is
    not.
    """
    wanted = {str(node_id).lower() for node_id in node_ids if node_id}
    if not wanted:
        return None
    nodes = _read()
    now = time.time()
    changed = False
    for node_id in wanted:
        row = nodes.get(node_id)
        if row is None:
            continue
        row["last_seen_at"] = now
        known = (startups or {}).get(node_id)
        if isinstance(known, dict):
            # Kept so a Mac that later goes quiet is still described by what it last said about its
            # own startup - the Macs that matter most here are the ones nobody can ask any more.
            row["last_startup"] = {
                "open_at_login": known.get("open_at_login"),
                "start_services_on_launch": known.get("start_services_on_launch"),
                "observed_at": now,
            }
        changed = True
    if changed:
        _write(nodes)
    return now


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
# vocabulary the fleet routing understands - and it is the Hub's own routing vocabulary
# (`backend/vocabulary.py`), imported rather than restated here.
#
# It has been restated twice, and both times the copy fell behind. Music joined the app on
# 2026-09-22; while it was missing here, a node reporting its Music worker was refused outright
# at join time, so no current build could connect to a controller at all. Then `chat` was
# missing from the same copy, with the same two consequences: a node running the shipped Chat
# worker was refused at join, and an already-joined node's chat studio was silently skipped when
# the controller built its registry. There is now one table, so a modality the Hub routes is a
# modality a node may offer, by construction.
NODE_MODALITIES = frozenset(NODE_MODALITIES)


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
            raise ValueError(
                f"studio modality {modality!r} is not one of {sorted(NODE_MODALITIES)}; "
                f"register it in WORKER_FOR_MODALITY in components/hub/backend/vocabulary.py "
                f"so the Hub routes it too")
        seen.append({"id": worker, "modality": modality})
    return seen


class NodeRemovedError(ValueError):
    """A join from a machine this controller has evicted.

    Its own type because it is not a malformed request: the asking Mac is well formed, known
    by name, and refused on purpose. The route answers 403 (not 400) and the node's app stops
    its re-join loop on it instead of retrying every five minutes for ever.
    """

    def __init__(self, node_id: str, removed_at: float | None) -> None:
        self.node_id = node_id
        self.removed_at = removed_at
        when = (time.strftime("%Y-%m-%d", time.localtime(removed_at))
                if removed_at else "an unrecorded date")
        super().__init__(REMOVED_DETAIL.format(when=when))


def _proves_identity(node_id: str, payload: dict[str, Any],
                     actor: dict[str, Any] | None,
                     existing: dict[str, Any] | None = None) -> bool:
    """Whether a re-join of an existing id comes from the machine that already holds it.

    Four ways, and no fifth. The controller's **own operator** may re-add a machine
    deliberately. The **node credential** — either the one the middleware already authenticated
    (`actor`) or one offered in the body — proves the caller is that machine. And a row from
    **before per-node credentials existed** has no hash, so a Fleet-token re-join may adopt it:
    that is the no-flag-day half of A8, and it is deliberately one-shot — the join issues a
    credential, so from the next join onwards the machine has to present it.

    Anything else is a second Mac claiming an id that is already taken.
    """
    kind = str((actor or {}).get("kind") or "")
    if kind == "hub":
        return True
    if kind == "node" and str((actor or {}).get("node_id") or "").lower() == node_id:
        return True
    if verify_node_credential(node_id, str(payload.get("credential") or "")):
        return True
    # A roster written by a build that had no per-node credentials. Adopting it is how an
    # existing fleet upgrades without anyone touching a Mac; it cannot be used to take over a
    # row that a credential already guards.
    return bool(existing) and not existing.get("credential_hash")


def join(payload: dict[str, Any], *,
         actor: dict[str, Any] | None = None) -> dict[str, Any]:
    """Record one node, or refresh what a rejoining node reports about itself.

    **Returns the machine's own credential, once.** The shared Fleet token enrols a Mac; it
    does not authorise it for ever, because a token every member holds cannot be withdrawn
    from one member. So the controller mints a credential for this node here, keeps only its
    hash, and hands the value back in this answer for the node to store `0600`. Every later
    node-originated call presents that credential instead.

    **A removed machine is refused by name.** The owner removes Macs to sell them, so a
    removal has to survive the machine's next start: `removed()` holds the id, and this
    raises `NodeRemovedError` before anything is written. Re-admitting is a deliberate action
    on the controller (`readmit`), not something the removed Mac can do for itself.

    **Backwards compatible on purpose.** A node that joins with only the Fleet token still
    works: it is issued its own credential on that first join, so the rollout needs no flag
    day and no node has to be touched by hand.
    """
    node_id = str(payload.get("node_id") or "").strip().lower()
    if not ID_PATTERN.fullmatch(node_id):
        raise ValueError("node_id must be lowercase letters, numbers, dots, dashes or underscores")
    document = _document()
    gone = document["removed"].get(node_id)
    if gone is not None:
        raise NodeRemovedError(node_id, gone.get("removed_at"))
    name = str(payload.get("name") or "").strip()
    if not 1 <= len(name) <= 120:
        raise ValueError("name must be between 1 and 120 characters")
    address = validate_address(payload.get("address"))
    studios = _studios(payload.get("studios"))
    nodes = document["nodes"]
    if node_id not in nodes and len(nodes) >= MAX_NODES:
        raise ValueError(f"this controller already has {MAX_NODES} nodes")
    existing = nodes.get(node_id) or {}
    # **A second Mac may not take over an existing node id silently.** `controller_id`
    # defaults to `<hostname>-hub`, so two identically named Mac minis — or a re-imaged Mac —
    # would otherwise share one roster row, one credential and one place in routing. A
    # re-join that cannot prove it is the machine already on the roster has to be re-admitted
    # by the operator first (H12, owner decision 9).
    if existing and not _proves_identity(node_id, payload, actor, existing):
        raise ValueError(
            f"this controller already has a machine called '{node_id}' "
            f"({existing.get('name') or 'unnamed'}). Two Macs cannot share one node id: "
            f"rename this Mac in Connections, or remove the other one first.")
    credential = _mint_credential()
    now = time.time()
    nodes[node_id] = {
        "id": node_id,
        "name": name,
        "address": address,
        "role": "agent",
        "studios": studios,
        "joined_at": existing.get("joined_at") or now,
        "updated_at": now,
        # The first moment this controller heard from the Mac. `last_seen_at` is the last
        # time it answered a probe, and is stamped by `note_seen`.
        "last_seen_at": existing.get("last_seen_at") or now,
        "credential_hash": hash_credential(credential),
        "credential_issued_at": now,
    }
    _write(nodes, document["removed"])
    # The plaintext is returned here and **nowhere else**: it is not written to the roster,
    # not logged, and not published on any read route.
    return {"node": dict(nodes[node_id]), "credential": credential}


def leave(node_id: str) -> bool:
    """Forget a node — the operator's own removal, with no eviction record.

    Deliberately kept separate from `remove`. This is the old path and it is still what a
    *re-admit then remove again* cycle uses internally; the route the operator presses goes
    through `remove`, which is the one that makes the removal stick.
    """
    nodes = _read()
    if node_id not in nodes:
        return False
    del nodes[node_id]
    _write(nodes)
    return True


def status(node_id: str) -> dict[str, Any]:
    """Whether this controller still lists `node_id`: `member`, `removed` (with when) or `unknown`.

    What a node asks so that a removal made on the controller reaches the Mac itself. Read-only,
    and it mints nothing: a join used as a heartbeat would issue a fresh credential on every call.
    """
    node_id = str(node_id or "").strip().lower()
    document = _document()
    if node_id in document["nodes"]:
        return {"id": node_id, "state": "member"}
    gone = document["removed"].get(node_id)
    if gone is not None:
        at = gone.get("removed_at")
        return {"id": node_id, "state": "removed", "removed_at": at,
                "detail": str(NodeRemovedError(node_id, at))}
    return {"id": node_id, "state": "unknown"}


def revoked_detail(offered: str | None) -> str | None:
    """The removal sentence for a credential this controller revoked by removing its Mac, else `None`.

    A revoked node credential used to fall through to a generic 401, which a node cannot tell from
    a controller that lost its roster. The tombstone keeps the hash, so the refusal can say what
    happened and the date.
    """
    if not offered:
        return None
    digest = hash_credential(offered)
    for node_id, row in _removed().items():
        stored = str(row.get("revoked_hash") or "")
        if stored and hmac.compare_digest(stored, digest):
            return str(NodeRemovedError(node_id, row.get("removed_at")))
    return None


def rename(node_id: str, name: str) -> bool:
    """Refresh a node's stored label. The roster name is written at join, so a rename on the Mac
    reached the controller only when that Mac re-joined. Returns whether anything changed."""
    name = str(name or "").strip()
    nodes = _read()
    row = nodes.get(str(node_id or "").lower())
    if row is None or not 1 <= len(name) <= 120 or row.get("name") == name:
        return False
    row["name"] = name
    _write(nodes)
    return True


def remove(node_id: str, *, by: str = "") -> dict[str, Any] | None:
    """**Evict** a node: revoke its credential and remember the removal for good.

    This is the difference between "removed" and "gone". Deleting the roster row alone left
    the machine holding the group credential and re-joining on its next start (B3), and with
    one shared Fleet token there was nothing to revoke (B2/A8). Both halves are here: the
    row goes, the credential hash goes with it, and the id is recorded in `removed` so
    `join` can refuse it by name for ever.
    """
    node_id = str(node_id or "").strip().lower()
    document = _document()
    row = document["nodes"].get(node_id)
    if row is None:
        return None
    del document["nodes"][node_id]
    document["removed"][node_id] = {
        "id": node_id,
        "name": row.get("name") or node_id,
        "address": row.get("address"),
        "removed_at": time.time(),
        "removed_by": by,
        # The hash of the credential this removal revoked, kept so the Mac that still holds it is
        # told it was removed (`revoked_detail`) instead of a bare 401. Never published:
        # `removed()` strips it.
        "revoked_hash": row.get("credential_hash"),
        # What the machine was, kept for the operator's benefit: a list of bare ids is not
        # something a person can check against a shelf of Macs.
        "role": row.get("role"),
        "studios": row.get("studios") or [],
        "joined_at": row.get("joined_at"),
    }
    _write(document["nodes"], document["removed"])
    return dict(document["removed"][node_id])


def readmit(node_id: str) -> dict[str, Any] | None:
    """Undo an eviction, deliberately: the machine may join again from now on."""
    node_id = str(node_id or "").strip().lower()
    document = _document()
    row = document["removed"].pop(node_id, None)
    if row is None:
        return None
    _write(document["nodes"], document["removed"])
    return dict(row)


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
                # Join already refuses an unrouted modality, so this is only reachable for a
                # registry written by another build. It must not be silent: a studio that
                # disappears here is a Mac the controller will never send work to.
                log.warning(
                    "skipping the studio %r on node %s: modality %r is not routed by this "
                    "Hub (%s); register it in WORKER_FOR_MODALITY in "
                    "components/hub/backend/vocabulary.py",
                    worker, node_id, modality, sorted(NODE_MODALITIES))
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


def _unreachable_reason(exc: BaseException) -> str:
    """Why a node did not answer, in the same vocabulary the peer poll uses.

    **A field, not a sentence, because the two callers used to disagree.** `_from_snapshot`
    writes `could not reach the node (no_hub)` — the token is right there — while this path
    wrote `could not reach http://…: ConnectTimeout`, which carries no token at all. A reader
    that wanted the reason therefore had to parse prose that is spelled two different ways,
    which is how the gateway came to tell an operator to *"fix Studio Hub on that Mac"* for a
    machine that was simply switched off.

    The distinction is the one `peers.py` already draws and documents:

    * **`no_hub`** — TCP refused. Something answered the network and nothing is listening on
      :47873, so the Mac is up and its Studio Hub is not running.
    * **`unreachable`** — packets dropped. A firewall is blocking the port, or the Mac is
      asleep or off. Nothing about this says the Hub is at fault.
    """
    if isinstance(exc, httpx.ConnectError):
        return "no_hub"
    return "unreachable"


def _startup_of(value: object, row: dict[str, Any]) -> dict[str, Any] | None:
    """The ``startup`` object a node reported, or - when it said nothing - what it last said."""
    return value if isinstance(value, dict) else _last_known_startup(row)


def _last_known_startup(row: dict[str, Any]) -> dict[str, Any] | None:
    """What this controller last heard about the node's startup settings, marked stale.

    ``None`` when it never heard: unknown is shown as unknown, never as off.
    """
    known = row.get("last_startup")
    if not isinstance(known, dict):
        return None
    return {"open_at_login": known.get("open_at_login"),
            "start_services_on_launch": known.get("start_services_on_launch"),
            "stale": True, "observed_at": known.get("observed_at"),
            "services_running": None, "app_reporting": False}


def _from_snapshot(row: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    """A probe result derived from a peer poll's snapshot.

    The poll reads the same route a direct probe reads, so the states line up one for one: a
    rejected credential is `unauthorized` (the node answered and refused *this controller*), a
    connected machine is `reachable`, and anything else is a machine that is not answering now.
    The host facts travel with it, so a roster read still shows what the node is.
    """
    host = snapshot.get("host") if isinstance(snapshot.get("host"), dict) else {}
    carried = {"host": host, "version": snapshot.get("version"),
               "startup": _startup_of(snapshot.get("startup"), row)}
    if snapshot.get("auth") is False:
        return {**row, **carried, "state": "unauthorized",
                "detail": _CREDENTIAL_DETAIL.format(
                    why=str(snapshot.get("status") or "the credential was refused"))}
    if snapshot.get("reachable") and snapshot.get("auth", True):
        return {**row, **carried, "state": "reachable", "detail": None}
    return {**row, **carried, "state": "unreachable", "startup": _last_known_startup(row),
            "reason": str(snapshot.get("status") or "unreachable"),
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
    row = {key: node.get(key) for key in ("id", "name", "address", "role", "studios",
                                          "joined_at", "last_seen_at", "updated_at")}
    row["credential_issued_at"] = node.get("credential_issued_at")
    row["last_startup"] = node.get("last_startup")
    # Whether this controller holds a credential for the machine, never the credential. A
    # roster row with no hash is a row from before per-node credentials existed, and it reads
    # as "the next join of this id has to prove itself" rather than as "trusted".
    row["has_credential"] = bool(node.get("credential_hash"))
    # Joined more than a day ago and never once answered a probe since. `last_seen_at` is seeded
    # with the join time, so "never" means it has not moved past it. The row still offers Remove;
    # this only lets the page say why it is worth removing.
    joined_at = node.get("joined_at") or 0
    row["never_connected"] = bool(
        joined_at and time.time() - joined_at > NEVER_CONNECTED_AFTER_S
        and (node.get("last_seen_at") or 0) - joined_at < 60)
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
            return {**row, "state": "error",
                    "detail": (f"{node.get('address') or 'the node'} answered "
                               f"HTTP {response.status_code} on {PROBE_PATH}")}
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
                "version": body.get("version"), "source": body.get("source"),
                "startup": _startup_of(body.get("startup"), row)}
    except (httpx.HTTPError, OSError) as exc:
        # A message that stops at the colon ("could not reach the node: ") reads as though the
        # reason were withheld, and it happens whenever the exception carries an empty message -
        # which is the common case for a connect timeout or a reset. Always name what was tried,
        # what failed, and why when the exception says.
        reason = str(exc).strip()
        detail = f"could not reach {node.get('address') or 'the node'} ({PROBE_PATH}): {type(exc).__name__}"
        if reason:
            detail = f"{detail}: {reason}"
        return {**row, "state": "unreachable", "reason": _unreachable_reason(exc), "detail": detail,
                "startup": _last_known_startup(row)}
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
    # **Last seen.** One batched write for the machines that answered, so a page refresh is not
    # one file rewrite per Mac — and so "switched off" and "gone a month" stop looking the same
    # (M20/B1). The rows being published carry the same stamp, so this read reports it.
    seen_at = note_seen(
        [row["id"] for row in rows if row.get("state") == "reachable"],
        {row["id"]: row["startup"] for row in rows
         if row.get("state") == "reachable" and isinstance(row.get("startup"), dict)
         and not row["startup"].get("stale")})
    if seen_at is not None:
        for row in rows:
            if row.get("state") == "reachable":
                row["last_seen_at"] = seen_at
                row["never_connected"] = False
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
        # **The evicted machines, published rather than hidden.** The operator removes Macs to
        # sell them, so the list has to survive the removal and be visible — otherwise the only
        # way to re-admit one is to remember its id. Each row carries the date it was removed.
        "removed": list(removed().values()),
        "summary": {
            "total": len(rows),
            "reachable": sum(row["state"] == "reachable" for row in rows),
            "unreachable": sum(row["state"] == "unreachable" for row in rows),
            "unauthorized": sum(row["state"] == "unauthorized" for row in rows),
            "error": sum(row["state"] == "error" for row in rows),
            "removed": len(_removed()),
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

    `token` is **this Mac's own node credential** when it has one, and the group Fleet token only
    before the first join has been answered. Presenting the node credential is what makes an
    eviction real: a sold Mac's copy is revoked on the controller, so this read stops working for
    it and nothing else changes for its neighbours.
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


async def refusal_from(address: str, token: str,
                       client: httpx.AsyncClient | None = None) -> dict[str, Any] | None:
    """Why the controller refused this Mac, when it answered at all.

    The roster read above collapses every failure into `None`, which is right for rendering a
    group nobody can read and wrong for the one failure the operator has to act on: **this Mac
    was removed**. The controller answers 403 with a sentence naming the date, and a node that
    only showed "unreachable" would keep retrying for ever against a decision that was made
    deliberately. One extra request, only on the failure path.
    """
    owned = client is None
    if owned:
        client = httpx.AsyncClient(follow_redirects=False)
    try:
        response = await client.get(f"{address}/api/hub/fleet/nodes", timeout=PROBE_TIMEOUT,
                                    headers={"X-Hub-Token": token} if token else {})
        if response.status_code not in (401, 403):
            return None
        try:
            detail = str((response.json() or {}).get("detail") or "")
        except ValueError:
            detail = ""
        if not detail:
            return None
        return {"status": response.status_code, "detail": detail,
                "removed": "removed this Mac" in detail}
    except (httpx.HTTPError, OSError, ValueError):
        return None
    finally:
        if owned:
            await client.aclose()


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
        # What starts when that Mac starts - its own report, or the last one this side heard.
        "startup": row.get("startup") if isinstance(row.get("startup"), dict) else None,
    }


STARTUP_RELAY_TIMEOUT = httpx.Timeout(30.0, connect=4.0)


class StartupRelayError(Exception):
    def __init__(self, status: int, body: dict[str, Any]) -> None:
        super().__init__(str(body.get("detail") or body.get("code") or status))
        self.status, self.body = status, body


async def relay_startup(node_id: str, payload: dict[str, Any], *, actor: str = "fleet",
                        client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """Pass a startup request to one joined node, with the credential a controller holds.

    The same Fleet token, on the same header, that every other controller-to-node call presents:
    nothing new is trusted and the node's own allowlist decides whether to accept. Raises
    ``StartupRelayError`` with the status and body the caller should return - including the one case
    that has no remedy from here: a node whose Studio Hub is not running (``no_hub``), because
    nothing is listening to receive the request.
    """
    from . import startup_control
    wanted = str(node_id or "").strip().lower()
    node = _read().get(wanted)
    if node is None:
        raise StartupRelayError(404, {"ok": False, "code": "UNKNOWN_NODE",
                                      "detail": f"this controller has no joined Mac {node_id!r}"})
    address = str(node.get("address") or "")
    fields = {key: payload.get(key) for key in ("action", "value", "force", "requested_by")
              if key in payload}
    startup_control.audit("relay", {"node": wanted, "action": fields.get("action"),
                                    "value": fields.get("value"), "force": fields.get("force"),
                                    "by": fields.get("requested_by"), "actor": actor})
    owned = client is None
    if owned:
        client = _probe_client()
    try:
        response = await client.post(f"{address}/api/hub/startup/request", json=fields,
                                     timeout=STARTUP_RELAY_TIMEOUT)
    except httpx.HTTPError as exc:
        reason = _unreachable_reason(exc)
        detail = ("no Studio Hub is running on that Mac, so nothing can receive the request - its "
                  "services are off. Start Studio Hub there once (Start Services); a joined Mac "
                  "then keeps them on."
                  if reason == "no_hub" else
                  "that Mac did not answer - it is asleep, off or off the network")
        startup_control.audit("relay_failed", {"node": wanted, "reason": reason})
        raise StartupRelayError(502, {"ok": False, "code": "NODE_UNREACHABLE", "reason": reason,
                                      "detail": detail}) from exc
    finally:
        if owned:
            await client.aclose()
    try:
        body = response.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    if response.status_code in (401, 403):
        raise StartupRelayError(response.status_code if response.status_code == 403 else 502, {
            "ok": False, "code": "NODE_REFUSED",
            "detail": "that Mac refused this controller's credential for startup changes "
                      "(an older Studio Hub, or a rotated credential)"})
    if not response.is_success:
        body.setdefault("ok", False)
        body.setdefault("detail", f"that Mac answered HTTP {response.status_code}")
        raise StartupRelayError(response.status_code, body)
    return {**body, "node": wanted}


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
    # This Mac's own credential when it has one; the shared Fleet token only before the first
    # join has been answered.
    credential = node_credential() or fleet_token() or ""
    remote = await remote_inventory(member["controller_url"], credential, client)
    if remote is None:
        # The group is known but its head did not answer. The controller is still listed, as a
        # machine that is not answering, which is the difference between a broken node and an
        # empty group.
        #
        # **Unless it answered and refused us.** A removal is deliberate and permanent, and a
        # node that renders it as "unreachable" keeps retrying for ever against a decision the
        # operator already made. One more request, only on this path, to find out which it is.
        result["refusal"] = await refusal_from(member["controller_url"], credential, client)
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
