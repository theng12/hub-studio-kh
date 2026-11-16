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
import threading
from dataclasses import dataclass
from pathlib import Path
import tempfile
import time
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from . import registry
from .registry import DATA_DIR, machine_enabled
from .peers import confirm_node, fleet_token, fresh_snapshot, held_token, nodes_on_previous_token
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
# A node's own final-render worker row, so a controller can republish it and let the Gateway
# see a joined leaf's render capacity. A fixed path on purpose: it is read over the fleet
# credential like the probe above, and an older node answers 404, which is "no render lane".
PROBE_CAPABILITY_PATH = "/api/hub/final-renders/capability"
_RENDER_CAPABILITY_LIMIT_BYTES = 64 * 1024
# A short-lived hint, refreshed by every probe. It is never production authority: production
# still comes only from the node's own `render-authority/enabled/<chip>-<mem>gb.json`.
_RENDER_CAPABILITY_TTL_SECONDS = 45.0
# The peer snapshot answers reachability without a dial but says nothing about a render worker,
# so that row is re-read on this interval rather than on every roster pass. It also rate-limits
# the read for a node with no lane at all (an older Hub answers 404 every time).
_RENDER_CAPABILITY_REFRESH_SECONDS = 30.0
_RENDER_CAPABILITY_KEYS = frozenset((
    "operation", "physical_machine_id", "hardware_class", "profile_digest", "tools_digest",
    "compiler_digest", "qualification_digest", "selected_tier", "exact_encoder", "font_support",
    "bounds", "profile_execution_bounds", "production_eligible", "production_enabled",
    "available_capacity", "qualification_candidates",
))
# The one key an owner-opted-in leaf adds (render_executor.RenderExecutor.capability). It is
# optional by design, and that is the whole mixed-fleet story: a leaf with the setting off
# publishes exactly _RENDER_CAPABILITY_KEYS, byte for byte what an unmodified Hub sends, so this
# controller and an older one both accept it; a controller of this build accepts either shape,
# so an older leaf keeps working; only an opted-in leaf under an older controller is dropped
# from the render roster (as "no render worker here"), which is the same zero capacity it had
# before the opt-in existed.
_RENDER_OPT_IN_KEY = "accepted_unqualified"
_RENDER_OPT_IN_EVIDENCE_KEYS = frozenset(("provenance", "accepted_by", "accepted_at"))
#: The one further key a leaf may add: its owner let its own pause exempt final render
#: (owner, 2026-10-28). Optional and dropped rather than guessed - a row carrying it in a shape
#: this controller cannot read is treated as a row without it, exactly as an older leaf's row is.
#:
#: A measured render speed is deliberately NOT a key here. `_RENDER_CAPABILITY_KEYS` is the
#: mixed-fleet contract, and an older controller drops a row carrying a key it does not know; for
#: the owner's opt-in that is a documented trade, but for a *measurement* it would silently remove
#: every Mac that has ever rendered from an older controller's roster. The speed travels on the
#: machine row instead (`standalone.fleet_overview` -> `machines[].render_speed`).
_RENDER_WHILE_PAUSED_KEY = "render_while_paused"
_OPTIONAL_RENDER_KEYS = frozenset((_RENDER_OPT_IN_KEY, _RENDER_WHILE_PAUSED_KEY, "encode_support"))
_RENDER_CANDIDATE_KEYS = frozenset((
    "vector_id", "hardware_class", "profile_digest", "tools_digest", "compiler_digest",
    "qualification_digest", "selected_tier", "exact_encoder", "font_support", "bounds",
    "profile_execution_bounds", "production_eligible", "qualification_only", "available_capacity",
))
_render_capability_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}
_render_capability_lock = threading.Lock()
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


def note_seen(node_ids: list[str], startups: dict[str, dict[str, Any]] | None = None,
              last_stops: dict[str, dict[str, Any]] | None = None, *,
              via: str = "probe") -> float | None:
    """Stamp `last_seen_at` for the nodes that just answered. Returns the stamp, or `None`.

    `via` is **why** this controller believes the Mac is there: `"probe"` when it dialled the
    stored address and got an answer, `"check-in"` when the Mac's own authenticated call home is
    the evidence. Both are evidence; the reason is kept distinct because the two fail in
    different ways, and an operator looking at a Mac that never answers a dial needs to know the
    controller is hearing from it at all.

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
        row["last_seen_via"] = via
        known = (startups or {}).get(node_id)
        if isinstance(known, dict):
            # Kept so a Mac that later goes quiet is still described by what it last said about its
            # own startup - the Macs that matter most here are the ones nobody can ask any more.
            row["last_startup"] = {
                "open_at_login": known.get("open_at_login"),
                "start_services_on_launch": known.get("start_services_on_launch"),
                "macos_trash_auto_empty": known.get("macos_trash_auto_empty"),
                "observed_at": now,
            }
        stop = last_stop_of((last_stops or {}).get(node_id))
        if stop is not None:
            row["last_stop"] = stop
        changed = True
    if changed:
        _write(nodes)
    return now


def _answered(snapshot: dict[str, Any] | None) -> bool:
    """Whether this is a snapshot the node answered with this controller's credential."""
    return isinstance(snapshot, dict) and bool(snapshot.get("reachable")) and snapshot.get("auth") is not False


# -- the node's own call home: evidence of life, and the pull channel's door -----------------
#
# A node behind one-way Tailscale node sharing can reach its controller but not be dialled by it,
# so a dial-only liveness check leaves it reading `never_connected` for ever. The node's own
# authenticated call is evidence, recorded with the reason kept distinct from a probe.

#: How long a Mac may go without checking in before it stops counting as there. A node checks in
#: every 60 s, so this is three missed ticks.
CHECKIN_FRESH_S = 180.0

#: What a check-in carries into the roster row, so a controller that cannot dial still knows what
#: the Mac is: its own name for itself, what it runs, and what it says it is doing.
_CHECKIN_KEYS = ("last_checkin_at", "checkin_version", "checkin_workstation",
                 "checkin_current_job", "checkin_startup", "checkin_last_stop",
                 "checkin_resources")

#: What a node's own `/api/hub/resources?local_only=true` answer may carry into the roster. The
#: controller is reading another machine's word about itself, so only the fields a roster row and a
#: dispatch decision actually use travel — the same rule `_current_job_of` and `_workstation_of`
#: already follow.
_RESOURCE_KEYS = ("host", "studios", "version", "startup", "current_job", "last_stop",
                  "workstation", "priority_order", "chat_priority", "function_enabled", "source",
                  "developer_gate_inventory", "work_volume")


def _gate_inventory_of(value: object, node_id: str,
                       controller_id: str | None = None) -> dict[str, Any] | None:
    """The closed developer-gate inventory, or absent when it is not exactly this node's.

    A node's optional field is trusted only after the frozen schema and the identities the
    controller can know agree. Missing, malformed or another Mac's answer is absent, never null.
    """
    if not isinstance(value, dict):
        return None
    try:
        from ..developer_gate_contract import validate_inventory_schema
        validate_inventory_schema(value)
    except Exception:  # noqa: BLE001 - a node's optional field never breaks the roster
        return None
    wanted = str(node_id or "").strip().lower()
    if not wanted or str(value.get("node_id") or "").strip().lower() != wanted:
        return None
    if controller_id is None:
        try:
            from . import control_plane
            controller_id = str(control_plane.load_settings().get("controller_id") or "")
        except Exception:  # noqa: BLE001 - an unreadable controller identity is not a match
            controller_id = ""
    controller_id = str(controller_id or "").strip().lower()
    if controller_id and str(value.get("controller_id") or "").strip().lower() != controller_id:
        return None
    return value


def _resources_of(value: object, node_id: str,
                  controller_id: str | None = None) -> dict[str, Any] | None:
    """A node's own resources snapshot, cut to the contract, or `None` when it did not send one.

    A node that names a *different* Mac in the snapshot it sent is not describing itself, and its
    answer is dropped rather than filed against this row.
    """
    if not isinstance(value, dict):
        return None
    machine = value.get("machine")
    if isinstance(machine, dict):
        named = str(machine.get("id") or "").strip().lower()
        if named and named != node_id:
            return None
    out: dict[str, Any] = {key: value.get(key) for key in _RESOURCE_KEYS if key in value}
    # An optional frozen developer-gate inventory travels only when it is the closed shape and
    # names this node and this controller. Missing or malformed is absent, never zero.
    if "developer_gate_inventory" in out:
        inventory = _gate_inventory_of(out["developer_gate_inventory"], node_id, controller_id)
        if inventory is None:
            out.pop("developer_gate_inventory", None)
        else:
            out["developer_gate_inventory"] = inventory
    # The node answered through its own authenticated call, so the two facts `dispatch_ready`
    # reads are true by construction: it is there, and it is this controller's credential it
    # answered with. Marked `pulled` so every reader can see this was not a dial.
    out.update({"reachable": True, "auth": True, "status": "connected", "pulled": True})
    return out


def checkin_alive(row: dict[str, Any] | None, *, now: float | None = None) -> bool:
    """Whether this Mac's own call home is recent enough to count as being there.

    Deliberately the *only* question asked of it. A check-in is the Mac's word about itself, so it
    is treated exactly as a probe answer is: a fact about now, with a window, not a flag that stays
    true once set.
    """
    at = (row or {}).get("last_checkin_at")
    if isinstance(at, bool) or not isinstance(at, (int, float)) or at <= 0:
        return False
    return (time.time() if now is None else now) - float(at) <= CHECKIN_FRESH_S


def dial_is_failing(row: dict[str, Any] | None) -> bool:
    """Whether this controller's most recent evidence about the Mac came from its own call home.

    **The rule that keeps "dial first" from meaning "wait ten minutes for a dial that cannot
    work".** `probe` dials every node on every roster read, and the reason it last succeeded or
    failed is recorded as `last_seen_via`. While that reads `check-in`, the controller's dial is
    known to be failing *now*, so a relay goes straight to the queue — a Mac on a foreign tailnet
    is not going to start answering because the caller waited out a 960 s read timeout.

    It is not a permanent verdict. The moment a dial succeeds the reason goes back to `probe` and
    every relay dials again, so nothing is stuck behind a stale belief.
    """
    return checkin_alive(row) and str((row or {}).get("last_seen_via") or "") == "check-in"


def record_checkin(node_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    """Take one node's authenticated call home. Returns the roster row, or `None` if unknown.

    Everything here is the node's word about itself and is validated as such: the address it
    reports goes through the same `validate_address` a join does, so a Mac can never point this
    controller at an arbitrary host by checking in. A field the node does not send is left alone —
    an older Hub that reports only that it is alive still stamps `last_seen_at`.
    """
    node_id = str(node_id or "").strip().lower()
    nodes = _read()
    row = nodes.get(node_id)
    if row is None:
        return None
    now = time.time()
    row["last_checkin_at"] = now
    row["last_seen_at"] = now
    row["last_seen_via"] = "check-in"

    address = payload.get("address")
    if address:
        try:
            clean = validate_address(address)
        except ValueError as exc:
            log.info("node %s reported an address this controller will not use (%s)", node_id, exc)
        else:
            if clean != row.get("address"):
                # **The address stops being frozen at join time.** It was written once, at join,
                # and a Mac never re-joins on its own — so a Mac whose address changed was stuck at
                # the old one for ever, on a roster nobody could correct. The Mac knows where it
                # answers; it says so every minute now.
                log.info("node %s now answers at %s (the roster said %s)",
                         node_id, clean, row.get("address"))
                row["address"] = clean
                row["address_changed_at"] = now
    name = payload.get("name")
    if name:
        try:
            row["name"] = valid_name(name)
        except ValueError:
            pass
    studios = _studios(payload.get("studios")) if "studios" in payload else []
    if studios:
        # Never emptied: a Mac mid-start, or one that says nothing, must not erase what it runs.
        row["studios"] = studios
    version = str(payload.get("version") or "").strip()
    if version:
        row["checkin_version"] = version[:64]
    # **The node's own answer to the probe the controller cannot make.** Its app reads its own
    # Hub's `/api/hub/resources?local_only=true` — the exact route `PROBE_PATH` dials — and sends
    # the answer with the check-in. One field carries what a dial would have returned, so the
    # roster, the peer cache and the scheduler all see the same facts on a Mac nobody can dial.
    resources = _resources_of(payload.get("resources"), node_id)
    if resources is not None:
        row["checkin_resources"] = resources
    workstation = _workstation_of(payload.get("workstation"))
    if workstation is not None:
        row["checkin_workstation"] = workstation
    current_job = _current_job_of(payload.get("current_job"))
    row["checkin_current_job"] = current_job
    startup = payload.get("startup")
    if isinstance(startup, dict):
        row["checkin_startup"] = {
            "open_at_login": startup.get("open_at_login"),
            "start_services_on_launch": startup.get("start_services_on_launch"),
            "macos_trash_auto_empty": startup.get("macos_trash_auto_empty"),
            "observed_at": now,
        }
        row["last_startup"] = dict(row["checkin_startup"])
    stop = last_stop_of(payload.get("last_stop"))
    if stop is not None:
        row["checkin_last_stop"] = stop
        row["last_stop"] = stop
    _write(nodes)
    return row


def note_status_seen(node_id: str, at: float | None = None) -> bool:
    """Record that a node asked, with its own credential, whether it is still listed.

    `GET …/status` is the call a node makes every 60 s on builds predating check-in; the route
    admits a node credential only for the Mac that holds it, so the call proves that Mac reached
    this controller — presence, and nothing else. Kept in ``last_status_at`` and **never
    overwriting** a ``check-in``/``probe`` reason, or `dial_is_failing` would stop recognising a
    Mac it knows it cannot dial. ``last_seen_at`` moves forward only; unknown and removed ids are
    not stamped. Returns whether a live roster row was stamped.
    """
    node_id = str(node_id or "").strip().lower()
    if not node_id:
        return False
    nodes = _read()
    row = nodes.get(node_id)
    if row is None:
        return False
    now = float(at if at is not None else time.time())
    row["last_status_at"] = now
    if now > float(row.get("last_seen_at") or 0):
        row["last_seen_at"] = now
    if str(row.get("last_seen_via") or "") not in ("check-in", "probe"):
        row["last_seen_via"] = "status"
    _write(nodes)
    return True


def _checkin_reported(row: dict[str, Any]) -> dict[str, Any]:
    """The carried fields a check-in gave, in the shape a probe answer uses.

    Read by `probe` when the dial fails, so a Mac the controller cannot dial still reports its
    version, its Lite Mode and what it is working on — the same fields, from the same kind of
    source, as a machine that answered. The node's own full resources snapshot wins when it sent
    one: it is the same answer the dial would have returned, so nothing is approximated.
    """
    snapshot = row.get("checkin_resources")
    if isinstance(snapshot, dict):
        return {**snapshot, "pulled": True}
    startup = row.get("checkin_startup")
    startup = dict(startup) if isinstance(startup, dict) else None
    stop = row.get("checkin_last_stop")
    return {
        "version": row.get("checkin_version"),
        "startup": startup,
        "current_job": row.get("checkin_current_job"),
        "last_stop": dict(stop) if isinstance(stop, dict) else _last_known_stop(row),
        "workstation": row.get("checkin_workstation"),
    }


def _forget_pull(node_id: str) -> None:
    """Drop the work held for a Mac this controller no longer has.

    A removed Mac must not keep a queue on its old controller: it is somebody else's machine now,
    and the queued requests carry what this group asked of it.
    """
    from . import fleet_pull
    fleet_pull.forget(node_id)


def checkin_snapshot(node_id: str) -> dict[str, Any] | None:
    """A check-in-alive node's snapshot as a peer snapshot, or `None`.

    What `peers` reads when its own poll of a Mac failed: the Mac is calling home, so the answer
    it sent with the call stands in for the dial. Without this a Shops Mac would read as online on
    the roster and still be invisible to the scheduler, which is a Mac that is up and never given
    work — the second half of the same fault.
    """
    row = _read().get(str(node_id or "").strip().lower())
    if row is None or not checkin_alive(row):
        return None
    reported = _checkin_reported(row)
    reported.setdefault("reachable", True)
    reported.setdefault("auth", True)
    reported.setdefault("status", "connected")
    # A complete snapshot shape even when the node sent no `resources`: every consumer of the peer
    # cache reads `host` and `studios`, and a missing key would be a KeyError on a healthy Mac.
    reported.setdefault("host", None)
    reported.setdefault("studios", {})
    reported["pulled"] = True
    return reported


def valid_name(value: object) -> str:
    """A Mac's display name: 1-120 printable characters, trimmed. Raises `ValueError` otherwise."""
    name = str(value or "").strip()
    if not 1 <= len(name) <= 120 or not name.isprintable():
        raise ValueError("name must be between 1 and 120 printable characters")
    return name


def _name_reported(node_id: str, snapshot: dict[str, Any] | None) -> str | None:
    """The name a node's own current snapshot gives itself, or `None` when it did not say.

    Only when the snapshot names **this** node id: an address now answered by a different Mac must not
    hand its name to this row. A Hub older than the field says nothing, and nothing said changes nothing.
    """
    machine = snapshot.get("machine") if _answered(snapshot) else None
    if not isinstance(machine, dict) or str(machine.get("id") or "").strip().lower() != node_id:
        return None
    try:
        return valid_name(machine.get("name"))
    except ValueError:
        return None


def _studios_reported(snapshot: dict[str, Any] | None) -> list[dict[str, str]] | None:
    """The studios a node's own current snapshot says it runs, or `None` when it did not say.

    Only a snapshot the node answered with this controller's credential counts, and only a
    non-empty list: an older Hub, an error or a Mac mid-start says nothing, and "nothing said" must
    never empty a roster row.
    """
    if not _answered(snapshot):
        return None
    studios = snapshot.get("studios")
    if not isinstance(studios, dict):
        return None
    reported: list[dict[str, str]] = []
    for modality, row in sorted(studios.items()):
        worker = str((row or {}).get("id") or "").strip().lower() if isinstance(row, dict) else ""
        modality = str(modality or "").strip().lower()
        if ID_PATTERN.fullmatch(worker) and modality in NODE_MODALITIES:
            reported.append({"id": worker, "modality": modality})
    return reported or None


def sync_roster_from_snapshots() -> bool:
    """Make each node's roster row what that node currently says: its studios and its name.
    True when a row changed.

    **The roster was frozen at join time.** A node names its studios once, when it joins, and a Mac
    never re-joins on its own; so when an update stopped shipping a studio (Video, after its only
    pack was retired) every Mac that joined before it kept a `video` entry on its controller for ever. The
    controller then polled a worker that did not exist: the Gateway showed "10 workers are offline",
    every idle Mac read "needs attention" (a reachable machine with a studio `down`) and its activity
    "partially unavailable" — on a healthy, idle fleet. The node's peer snapshot
    (`/api/hub/resources?local_only=true`, already read every poll for dispatch) names the studios
    it runs, so the roster follows it, in both directions, the same way a join would have recorded.

    **The name follows the Mac the same way (live 2026-10-02).** A Mac renamed after it joined kept
    its old name here and on the Gateway: the only other path, the node's minute status call, counts
    only with that Mac's own node credential, and a Mac whose credential this controller no longer
    recognises falls back to the group token, which may not rename. The node's snapshot names it
    (`machine`, in the release after 0.6.132), so the name is taken from there. A name set from the Gateway is pushed
    to the Mac itself first (`relay_name`), so the Mac and its roster row never disagree for long.
    """
    nodes = _read()
    changed = False
    for node_id, row in nodes.items():
        snapshot = fresh_snapshot(node_id)
        name = _name_reported(node_id, snapshot)
        if name and row.get("name") != name:
            log.info("node %s is now called %r (the roster said %r)", node_id, name, row.get("name"))
            row["name"] = name
            changed = True
        reported = _studios_reported(snapshot)
        if reported is None:
            continue
        current = [{"id": str(s.get("id")), "modality": str(s.get("modality"))}
                   for s in (row.get("studios") or []) if isinstance(s, dict)]
        if sorted(map(str, current)) != sorted(map(str, reported)):
            log.info("node %s now runs %s (the roster listed %s)", node_id,
                     [s["id"] for s in reported], [s["id"] for s in current])
            row["studios"] = reported
            changed = True
    if changed:
        _write(nodes)
    return changed


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
    name = valid_name(payload.get("name"))
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
    # The controller's own roster identity, so the joining Mac can record the actual controller
    # rather than infer one from an address or a label. It is the same id its roster row uses.
    return {"node": dict(nodes[node_id]), "credential": credential,
            "controller": this_machine()}


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
    confirm_node(node_id)
    _forget_pull(node_id)
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
    try:
        name = valid_name(name)
    except ValueError:
        return False
    nodes = _read()
    row = nodes.get(str(node_id or "").lower())
    if row is None or row.get("name") == name:
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
    confirm_node(node_id)   # a removed Mac is not waited for: it is not in the group any more
    _forget_pull(node_id)
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


def reset_fleet(*, by: str = "operator") -> dict[str, Any]:
    """**Disassemble this controller's group**: forget every node, revoke every credential, and keep
    the record.

    The owner's clean-slate action (2026-10-25), after a move: *"make sure we can disassemble the
    nodes and controllers so I can restart anew."* Every Mac he owns is then re-enrolled against a
    controller he chooses, which is a day of work he wants to do once rather than repair a group
    whose addresses and credentials he no longer trusts.

    **It is deliberately not the eviction path.** `remove` leaves a tombstone in `removed` that
    refuses that id for ever, which is right for a Mac being sold and wrong here: the same Macs
    re-enrol minutes later, and a controller that refused its own roster by name would make the
    reset one-way. Nothing here is recorded as removed, so every id may join again at once.

    **Nothing is deleted.** The roster and the pull queue are moved into the data directory's
    `_trash` (`backend/state_trash`), because a roster is the only record of which Macs were in the
    group and a disassembled group is exactly the thing worth reading afterwards. A move that fails
    raises, and the reset does not happen: "the old roster was kept" is not a promise to break
    quietly.

    The caller mints the new group token (`peers.set_fleet_token("")`) and forgets the machines
    still waiting on a previous one (`peers.clear_previous_tokens`) — this function owns the roster,
    not the credentials — and reloads the dispatch registry.
    """
    from . import fleet_pull
    from . import state_trash

    document = _document()
    forgotten = [{"id": node_id, "name": str(row.get("name") or node_id)}
                 for node_id, row in sorted(document["nodes"].items())]
    # Archive **first**, and both files, while they still hold everything: the write below opens a
    # fresh empty roster, and a queue archived afterwards would be an empty one.
    archived = {}
    for label, path, reason in (("roster", store_path(), "reset-fleet"),
                                ("queue", fleet_pull.store_path(), "reset-fleet")):
        where = state_trash.move_to_trash(path, reason=reason)
        if where:
            archived[label] = where
    # Both sets empty: no live rows, and no tombstones — see the docstring.
    _write({}, {})
    # The queue's in-process view of the roster (which origin belongs to which node, and who is
    # waiting on an answer) is keyed by nothing the reset rewrote, so it is dropped explicitly
    # rather than left to its two-second TTL.
    fleet_pull.reset_store()
    with _render_capability_lock:
        _render_capability_cache.clear()
    log.info("fleet reset by %s: %d node(s) forgotten, kept in %s",
             by, len(forgotten), ", ".join(archived.values()) or "nothing to keep")
    return {"ok": True, "reset": True, "by": by, "total": len(forgotten),
            "forgotten": forgotten, "archived": archived}


#: What an operation id may look like: `repoint-<anything>` a controller or the app chose, at least
#: eight characters and at most 120. Deliberately its own pattern rather than the node-id one - this
#: is an opaque label for one migration, not a machine, and a driver is free to date it or separate
#: its parts with `:`.
REPOINT_OPERATION_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,119}")

#: A repoint this Mac will not apply, carrying the status the route should answer with. Its own type
#: because these are not malformed requests: the asking controller is well formed and refused on
#: purpose, and the difference between "your request was wrong" (400) and "this Mac is not in the
#: state you think it is" (409) is the difference between fixing the caller and re-reading the Mac.
class RepointRefused(ValueError):
    def __init__(self, status: int, code: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail


async def _destination_knows_us(address: str, node_id: str, credential: str,
                                *, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """Whether the controller at `address` already knows this Mac, asked with this Mac's **own**
    credential and nothing else.

    This is the whole trust check of an address-only repoint, and it is deliberately narrow: the
    node presents the credential its *current* controller issued, and only a controller holding the
    transferred roster - the same credential hashes - can recognise it. A group Fleet token is not
    offered as a fallback, because the shared token is exactly what cannot tell one controller from
    another, and accepting it here would turn "the roster moved" into "anyone with the group secret
    can claim this Mac".

    Redirects are off. An address that answers 302 is a controller that has moved again, and
    following it silently would store an address this Mac never verified.
    """
    owned = client is None
    if owned:
        client = httpx.AsyncClient(follow_redirects=False)
    try:
        response = await client.get(f"{address}/api/hub/fleet/nodes/{quote(node_id)}/status",
                                    headers={"X-Hub-Token": credential}, timeout=PROBE_TIMEOUT)
        try:
            body = response.json()
        except ValueError:
            body = {}
        # A controller that answers 200 with a bare string, a list or a number is not answering the
        # question. Treated as no answer rather than trusted or crashed on: the caller then refuses
        # the repoint, which is the safe direction.
        if not isinstance(body, dict):
            body = {}
    except (httpx.HTTPError, OSError, ValueError) as exc:
        return {"ok": False, "code": "DESTINATION_UNREACHABLE",
                "detail": (f"the controller at {address} answered nothing "
                           f"({type(exc).__name__}), so this Mac's membership was left alone")}
    finally:
        if owned:
            await client.aclose()
    state = str((body or {}).get("state") or "")
    if response.status_code == 200 and state == "member":
        return {"ok": True, "state": state}
    if state == "removed":
        return {"ok": False, "code": "DESTINATION_REMOVED_THIS_MAC",
                "detail": (f"the controller at {address} has this Mac on its removed list, so it "
                           f"cannot be repointed to it - re-admit this Mac there first, or join "
                           f"it again with that controller's Fleet token")}
    if response.status_code in (401, 403):
        said = str((body or {}).get("detail") or "").strip()
        return {"ok": False, "code": "DESTINATION_DOES_NOT_KNOW_THIS_MAC",
                "detail": (f"the controller at {address} refused this Mac's own credential "
                           f"(HTTP {response.status_code}): it does not hold this Mac's roster row "
                           f"with the credential it was issued, so a repoint would leave the Mac "
                           f"answering a controller that does not recognise it. Transfer the "
                           f"roster first, or use a full re-join."
                           + (f" That controller said: {said[:300]}" if said else ""))}
    return {"ok": False, "code": "DESTINATION_UNKNOWN_ANSWER",
            "detail": (f"the controller at {address} answered HTTP {response.status_code} "
                       f"({state or 'no state'}) for this Mac, which is not proof that it knows it")}


async def repoint(payload: dict[str, Any], *,
                  client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """**Move this Mac to a different controller without re-joining it.**

    A continuation of one trust domain, not a new membership: the controller changed address (or
    the owner moved a Mac to another of his own controllers that already holds the group's roster),
    and every leaf of this Mac's identity has to survive it - its Machine ID, its per-node
    credential, the group Fleet token, its release pins, its model library and its owner settings.

    Which is why this is a separate operation from joining. `join`/`adopt` are how a Mac *enters* a
    group: they mint a new credential, and the Fleet-token push clears the node credential outright.
    Re-joining forty Macs to hand two of them to another controller reissues forty credentials and
    re-registers forty machines, and every one of those is a chance to lose an identity. This writes
    exactly two keys.

    The body carries no credential, no node id and no settings - only the three things the operation
    needs:

    * `operation_id` - the durable identity of *this* migration, so a retry after a lost reply
      returns what happened instead of doing it again;
    * `expected_controller_url` - the controller this Mac is believed to be on now. It is the fence
      against a stale instruction: a queued repoint that arrives after the Mac has already moved
      elsewhere names the address it left, not the one it is on, and is refused rather than applied;
    * `controller_url` - where to move to, checked by the same private-IPv4 validator every other
      address this Hub stores goes through.

    **Nothing is written before every check has passed**, and the destination must already recognise
    this Mac's own credential (see `_destination_knows_us`). If any of that fails, this Mac keeps the
    controller it has and says why - a half-moved Mac is worse than an unmoved one, because it
    answers a controller that thinks it is somewhere else.

    Authorisation is deliberately the group Fleet credential or this Mac's own operator, and
    **never a node credential** (`auth.FLEET_ALLOWED` carries it, `auth.NODE_ALLOWED` does not), the
    same boundary `POST /api/hub/fleet` has. It is not a new power: a Fleet token can already set
    this Mac's parent address through that route, destructively. This is the version that keeps the
    Mac intact.
    """
    from . import control_plane

    operation_id = str(payload.get("operation_id") or "").strip()
    if not REPOINT_OPERATION_PATTERN.fullmatch(operation_id):
        raise ValueError("operation_id must be 8-120 letters, numbers, dots, dashes, colons or "
                         "underscores, starting with a letter or number")
    # Both addresses go through the one validator that already decides what this Hub may store.
    expected = validate_address(payload.get("expected_controller_url"))
    destination = validate_address(payload.get("controller_url"))

    current = membership()
    if not current["member"]:
        raise RepointRefused(409, "NOT_A_MEMBER",
                             "this Mac is not in a group, so there is no controller to move away "
                             "from. Join the new controller instead - a repoint only ever continues "
                             "a membership this Mac already has.")
    # **The receipt is consulted before the fence, and that order is the whole point.** An operation
    # that already applied leaves the Mac on the address it moved *to*, so its own
    # `expected_controller_url` — the address it came from — no longer matches the current
    # membership. Checking the fence first would refuse every honest retry of an operation whose
    # reply was lost, which is the one case the receipt exists for. A *stale* operation (a different
    # id, naming an address this Mac has left) is still refused below, untouched.
    receipt = control_plane.load_settings().get("repoint_receipt")
    if isinstance(receipt, dict) and str(receipt.get("operation_id") or ""):
        if str(receipt["operation_id"]) == operation_id:
            if (str(receipt.get("from_url") or "") == expected
                    and str(receipt.get("to_url") or "") == destination):
                return {"ok": True, "repointed": True, "already_applied": True,
                        "controller_url": destination, "operation_id": operation_id,
                        "previous_controller_url": expected,
                        "applied_at": receipt.get("applied_at")}
            raise RepointRefused(
                409, "OPERATION_ID_REUSED",
                f"operation {operation_id!r} was already applied to a different pair of addresses "
                f"({receipt.get('from_url')} → {receipt.get('to_url')}). One operation id means one "
                f"migration: choose a new id for a new move, so a replay can never be mistaken for "
                f"a second instruction.")
    if current["controller_url"] != expected:
        raise RepointRefused(
            409, "STALE_EXPECTED_URL",
            f"this Mac's controller is {current['controller_url']}, not {expected}. The operation "
            f"names an address this Mac has already left, so it was not applied - a repoint that "
            f"overwrote a newer move would take this Mac back to a controller it left on purpose.")
    if destination == current["controller_url"]:
        # Already there. Reported as its own outcome rather than as a fresh success, and nothing is
        # written: there is no state change to record, and a receipt for a no-op would be a record
        # of a migration that never happened.
        return {"ok": True, "repointed": False, "already_there": True,
                "controller_url": destination, "operation_id": operation_id,
                "detail": f"this Mac is already on the controller at {destination}"}

    node_id = str(control_plane.load_settings().get("controller_id") or "").strip().lower()
    credential = node_credential()
    if not credential:
        raise RepointRefused(
            409, "NO_NODE_CREDENTIAL",
            "this Mac holds no node credential, so there is nothing for a new controller to "
            "recognise: this Mac would arrive unknown and refuse to answer it. Re-join instead.")
    confirmed = await _destination_knows_us(destination, node_id, credential, client=client)
    if not confirmed["ok"]:
        raise RepointRefused(409, confirmed["code"], confirmed["detail"])

    # **One write, carrying the receipt and the new address together.** Two writes could land one
    # without the other, which is exactly the state the app cannot recover from: an address whose
    # migration has no record, or a record whose address was never stored. `save_settings` writes
    # atomically and updates the process cache with the same values, so every reader in this process
    # sees the new controller on its next call rather than after a restart.
    applied_at = time.time()
    control_plane.save_settings({
        "parent_controller_url": destination,
        "repoint_receipt": {"operation_id": operation_id, "from_url": expected,
                            "to_url": destination, "applied_at": applied_at,
                            "state": "applied"},
    })
    log.info("node %s repointed from %s to %s (operation %s)",
             node_id, expected, destination, operation_id)
    return {"ok": True, "repointed": True, "already_applied": False,
            "controller_url": destination, "previous_controller_url": expected,
            "operation_id": operation_id, "applied_at": applied_at,
            # Named because the promise of this operation is what it *did not* touch.
            "kept": ["this Mac's Machine ID", "its per-node credential", "the group Fleet token",
                     "its release pins", "its model library", "its owner work settings"]}


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
            "macos_trash_auto_empty": known.get("macos_trash_auto_empty"),
            "stale": True, "observed_at": known.get("observed_at"),
            "services_running": None, "app_reporting": False}


_CURRENT_JOB_KEYS = ("job_id", "modality", "model", "label", "started_at")
#: Why a Mac's Studio Hub last stopped, as that Mac recorded it. ``stopped_answering`` is the app's
#: record of services that were running and went silent (it ends them to start a fresh set); the
#: app itself did not stop, and the next app stop overwrites the record with its own reason.
#: ``services_exited`` (0.6.243) is the same for services that exited on their own; its label
#: carries the exit code or signal.
LAST_STOP_REASONS = frozenset({"quit", "crashed", "updated", "force_quit", "low_memory", "mac_restarted",
                              "stopped_answering", "services_exited"})


def last_stop_of(value: object) -> dict[str, Any] | None:
    """Why a Mac's Studio Hub last stopped, as that Mac recorded it - or ``None``.

    Written by the app (`StopRecord` in native/ShellCore.swift) to ``last-stop.json``: why the app
    ended, or - while the app is still up - why it ended services that had stopped answering. It is
    another machine's word, so only the four contract keys travel, with a known reason and a real
    time.
    """
    if not isinstance(value, dict) or value.get("reason") not in LAST_STOP_REASONS:
        return None
    at = value.get("at")
    if isinstance(at, bool) or not isinstance(at, (int, float)) or at <= 0:
        return None
    return {"reason": value["reason"], "at": float(at),
            "label": str(value.get("label") or "")[:80] or None,
            "relaunched": value.get("relaunched") is True}


def this_last_stop() -> dict[str, Any] | None:
    """This Mac's own ``last_stop``: the file the app writes, named by `hub_worker.py`."""
    path = os.environ.get("STUDIOHUB_LAST_STOP_FILE")
    if not path:
        return None
    try:
        return last_stop_of(json.loads(Path(path).read_text()))
    except (OSError, ValueError):
        return None


def _last_known_stop(row: dict[str, Any]) -> dict[str, Any] | None:
    """What an unreachable node last said about its last stop, marked stale; ``None`` if never."""
    known = last_stop_of(row.get("last_stop"))
    return {**known, "stale": True} if known else None


def _current_job_of(value: object) -> dict[str, Any] | None:
    """The job a node reports it is on, cut to the five fields the fleet views publish.

    A node is another machine's word, so nothing beyond the contract is carried through: no
    prompt text or extra fields a future node might add reach the controller's own answers.
    """
    if not isinstance(value, dict) or not value.get("job_id"):
        return None
    return {key: value.get(key) for key in _CURRENT_JOB_KEYS}


_WORKSTATION_KEYS = ("enabled", "idle_only", "idle_minutes", "step_aside", "resume_after_minutes",
                     "start_free_gb", "quiet_hours", "hide_menu_bar_icon", "state", "reason",
                     "yields_today", "jobs_today")


def _workstation_of(value: object) -> dict[str, Any] | None:
    """A node's workstation report, cut to the contract keys (it is another machine's word)."""
    if not isinstance(value, dict) or not isinstance(value.get("state"), str):
        return None
    return {key: value.get(key) for key in _WORKSTATION_KEYS}


def _priority_order_of(snapshot: dict[str, Any]) -> list[str]:
    if "priority_order" in snapshot:
        return registry.normalize_priority_order(snapshot.get("priority_order"))
    return ["chat"] if snapshot.get("chat_priority") is True else []


def _function_enabled_of(snapshot: dict[str, Any]) -> dict[str, bool]:
    """A node's own work-order switches, cut to real booleans on real functions.

    Beside `priority_order` because it is the other half of the same Mac's work order (owner,
    2026-10-28): the order says *when*, this says *whether*. A node that never published it (an
    older Hub) contributes nothing, and every kind therefore reads as ON, which is what that
    build does.
    """
    return registry.normalize_function_enabled(snapshot.get(registry.FUNCTION_ENABLED_KEY))


def _from_snapshot(row: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    """A probe result derived from a peer poll's snapshot.

    The poll reads the same route a direct probe reads, so the states line up one for one: a
    rejected credential is `unauthorized` (the node answered and refused *this controller*), a
    connected machine is `reachable`, and anything else is a machine that is not answering now.
    The host facts travel with it, so a roster read still shows what the node is.
    """
    host = snapshot.get("host") if isinstance(snapshot.get("host"), dict) else {}
    order = _priority_order_of(snapshot)
    carried = {"host": host, "version": snapshot.get("version"),
               "startup": _startup_of(snapshot.get("startup"), row),
               "current_job": _current_job_of(snapshot.get("current_job")),
               "last_stop": last_stop_of(snapshot.get("last_stop")) or _last_known_stop(row),
               "workstation": _workstation_of(snapshot.get("workstation")),
               "priority_order": order,
               "chat_priority": bool(order and order[0] == "chat"),
               "function_enabled": _function_enabled_of(snapshot),
               "preferred_work": (snapshot.get("preferred_work")
                                  if isinstance(snapshot.get("preferred_work"), dict) else None),
               "preferred_hold_remaining_s": (
                   float(snapshot["preferred_hold_remaining_s"])
                   if isinstance(snapshot.get("preferred_hold_remaining_s"), (int, float))
                   and not isinstance(snapshot.get("preferred_hold_remaining_s"), bool)
                   else 0.0)}
    inventory = _gate_inventory_of(snapshot.get("developer_gate_inventory"),
                                   str(row.get("id") or ""))
    if inventory is not None:
        carried["developer_gate_inventory"] = inventory
    if snapshot.get("auth") is False:
        return {**row, **carried, "state": "unauthorized",
                "detail": _CREDENTIAL_DETAIL.format(
                    why=str(snapshot.get("status") or "the credential was refused"))}
    if snapshot.get("reachable") and snapshot.get("auth", True):
        return {**row, **carried, "state": "reachable", "detail": None,
                "via": "check-in" if snapshot.get("pulled") else "probe"}
    if checkin_alive(row):
        # The same rule as `probe`'s own fallback: a poll that did not come back is not a Mac that
        # is gone while the Mac is calling home every minute.
        return {**row, **carried, "state": "reachable", "detail": None, "via": "check-in"}
    return {**row, **carried, "state": "unreachable", "startup": _last_known_startup(row),
            "reason": str(snapshot.get("status") or "unreachable"),
            "detail": f"could not reach the node ({snapshot.get('status') or 'unreachable'})"}


def _render_capability(value: object) -> dict[str, Any] | None:
    """Accept one node's closed final-render worker row, or `None` for anything else.

    A node is authenticated by the probe that carried this, but its answer is still data: the
    keys are closed here so a malformed or older answer can never reach the controller's
    capabilities as a worker row.
    """
    if not isinstance(value, dict):
        return None
    extra = set(value) - _RENDER_CAPABILITY_KEYS
    if not extra <= _OPTIONAL_RENDER_KEYS:
        return None
    if "encode_support" in extra:
        from .render_encode import FORMAT, normalize_encode
        support = value["encode_support"]
        if (type(support) is not dict or set(support) != {"format", "entries"}
                or support["format"] != FORMAT or type(support["entries"]) is not list
                or len(support["entries"]) > 32
                or any(normalize_encode(entry) is None for entry in support["entries"])):
            return None
    opted_in = _RENDER_OPT_IN_KEY in extra
    if value.get("operation") != "video.final_render":
        return None
    if opted_in:
        # An unmeasured authority is still a production-shaped row (that is what lets the
        # Gateway select it), so the evidence of who accepted it has to be well formed and the
        # row has to actually be the production row it claims to be.
        evidence = value[_RENDER_OPT_IN_KEY]
        if (not isinstance(evidence, dict)
                or set(evidence) != _RENDER_OPT_IN_EVIDENCE_KEYS
                or evidence["provenance"] != "owner-opt-in-unqualified"
                or not isinstance(evidence["accepted_by"], str)
                or not 0 < len(evidence["accepted_by"]) <= 120
                or type(evidence["accepted_at"]) is not int or evidence["accepted_at"] < 1
                or value.get("production_eligible") is not True
                or value.get("production_enabled") is not True
                or not isinstance(value.get("selected_tier"), str)
                or not isinstance(value.get("exact_encoder"), str)):
            return None
    if (not isinstance(value.get("physical_machine_id"), str)
            or not value["physical_machine_id"]
            or type(value.get("font_support")) is not bool
            or type(value.get("production_eligible")) is not bool
            or type(value.get("production_enabled")) is not bool):
        return None
    for name in ("hardware_class", "profile_digest", "tools_digest", "compiler_digest",
                 "qualification_digest", "selected_tier", "exact_encoder"):
        if value[name] is not None and not isinstance(value[name], str):
            return None
    capacity = value.get("available_capacity")
    if (not isinstance(capacity, dict) or set(capacity) != {"slots", "slots_total"}
            or type(capacity["slots"]) is not int or type(capacity["slots_total"]) is not int
            or capacity["slots"] < 0 or capacity["slots_total"] < 1):
        return None
    bounds = value.get("bounds")
    if bounds is not None and (
            not isinstance(bounds, dict)
            or set(bounds) != {"max_rss_bytes", "max_wall_seconds",
                               "min_available_memory_bytes", "output_limit_bytes"}
            or any(type(bounds[key]) is not int or bounds[key] <= 0 for key in bounds)):
        return None
    if value.get("profile_execution_bounds") is not None \
            and not isinstance(value["profile_execution_bounds"], dict):
        return None
    candidates = value.get("qualification_candidates")
    if not isinstance(candidates, list):
        return None
    for candidate in candidates:
        if (not isinstance(candidate, dict) or set(candidate) != _RENDER_CANDIDATE_KEYS
                or candidate.get("production_eligible") is not False
                or candidate.get("qualification_only") is not True
                or not isinstance(candidate.get("vector_id"), str)
                or not candidate["vector_id"]):
            return None
        slot = candidate.get("available_capacity")
        if (not isinstance(slot, dict) or set(slot) != {"slots", "slots_total"}
                or type(slot["slots"]) is not int or type(slot["slots_total"]) is not int
                or slot["slots"] not in (0, 1) or slot["slots_total"] != 1):
            return None
    # The optional exemption, validated where it is read rather than trusted. A value that is not
    # exactly `True` is removed from the row rather than used to refuse the whole worker: the
    # row's capacity is a separate fact, and losing a render Mac because one flag was malformed
    # would be the worse answer.
    if _RENDER_WHILE_PAUSED_KEY in value and value[_RENDER_WHILE_PAUSED_KEY] is not True:
        value = {key: item for key, item in value.items() if key != _RENDER_WHILE_PAUSED_KEY}
    return value


def _remember_render_capability(node_id: str, value: dict[str, Any] | None) -> None:
    """Record the answer, including "asked, there is no lane", so a node with none is not re-asked
    on every roster pass. The stamp is the read time either way; `None` is never published."""
    with _render_capability_lock:
        _render_capability_cache[node_id] = (time.time(), value)


def _render_capability_due(node_id: str, *, now: float | None = None) -> bool:
    at = time.time() if now is None else now
    with _render_capability_lock:
        stamped = _render_capability_cache.get(node_id, (0.0, None))[0]
    return at - stamped >= _RENDER_CAPABILITY_REFRESH_SECONDS


def render_capabilities(*, now: float | None = None) -> dict[str, dict[str, Any]]:
    """Fresh render worker rows for joined nodes; a hint that expires, never authority."""
    at = time.time() if now is None else now
    with _render_capability_lock:
        return {node_id: value
                for node_id, (stamped, value) in _render_capability_cache.items()
                if value is not None and at - stamped <= _RENDER_CAPABILITY_TTL_SECONDS}


async def _probe_render_capability(client: httpx.AsyncClient, node: dict[str, Any],
                                   older: str | None) -> dict[str, Any] | None:
    """One bounded extra read of the node's own render worker row; absent on any answer
    this controller cannot use. It is evidence for a worker row, never for reachability, so
    a 404 from an older Hub, a refused credential or a body that is not that row all read
    as "no render worker here" and leave the probe's own answer untouched."""
    try:
        response = await client.get(
            f"{node['address']}{PROBE_CAPABILITY_PATH}", timeout=PROBE_TIMEOUT,
            **({"headers": {"X-Hub-Token": older}} if older else {}))
        if not response.is_success or len(response.text) > _RENDER_CAPABILITY_LIMIT_BYTES:
            return None
        return _render_capability(response.json())
    except Exception:  # noqa: BLE001 - a side read must never change the probe's answer
        return None


async def _refresh_render_capability(node: dict[str, Any],
                                     client: httpx.AsyncClient | None = None) -> None:
    """Read and remember one node's render row; owns a client only when given none."""
    node_id = str(node.get("id") or "")
    owned = client is None
    if owned:
        client = _probe_client()
    try:
        _remember_render_capability(
            node_id, await _probe_render_capability(client, node, held_token(node_id)))
    finally:
        if owned:
            await client.aclose()


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
    row["last_stop"] = node.get("last_stop")
    # What this Mac last said about itself on its own call home. It is the whole of what a
    # controller that cannot dial knows, so it travels on every roster row.
    for key in _CHECKIN_KEYS:
        row[key] = node.get(key)
    row["last_seen_via"] = node.get("last_seen_via")
    # The last authenticated status ask: presence only, kept apart from `last_seen_at`/`last_seen_via`.
    row["last_status_at"] = node.get("last_status_at")
    # Whether this controller holds a credential for the machine, never the credential. A
    # roster row with no hash is a row from before per-node credentials existed, and it reads
    # as "the next join of this id has to prove itself" rather than as "trusted".
    row["has_credential"] = bool(node.get("credential_hash"))
    # Joined more than a day ago and never once heard from since — neither a dial the controller
    # made (`last_seen_at` past the join seed) nor a call home (`check-in`, or the status ask an
    # older build makes). The row still offers Remove; this only lets the page say why.
    joined_at = node.get("joined_at") or 0
    heard = ((node.get("last_seen_at") or 0) - joined_at >= 60
             or bool(node.get("last_status_at")))
    row["never_connected"] = bool(
        joined_at and time.time() - joined_at > NEVER_CONNECTED_AFTER_S
        and not heard
        and not checkin_alive(node))
    node_id = str(node.get("id") or "")
    snapshot = fresh_snapshot(node_id)
    if snapshot is not None:
        # The peer snapshot answers reachability without a dial, but it carries no render row,
        # so that one bounded read is refreshed here on its own interval.
        if _render_capability_due(node_id):
            await _refresh_render_capability(node, client)
        return _from_snapshot(row, snapshot)
    owned = client is None
    if owned:
        client = _probe_client()
    try:
        # A node still on the previous Fleet token is asked with that one (a client that carries
        # the current token would read it as "unauthorized" for a rotation it simply was off for).
        older = held_token(str(node.get("id") or ""))
        response = await client.get(f"{node['address']}{PROBE_PATH}", timeout=PROBE_TIMEOUT,
                                    **({"headers": {"X-Hub-Token": older}} if older else {}))
        if response.status_code in (401, 403):
            # The node answered, so it is up - it refused *this controller*. Reported as plain
            # "unreachable", the operator goes looking at the network and at the wrong Mac, when the
            # fix is the credential: a node re-joined with its own token, or a controller whose
            # fleet token was replaced. The state is its own so the Fleet page can say so and the
            # counts do not hide it.
            _remember_render_capability(node_id, None)
            return {**row, "state": "unauthorized",
                    "detail": _CREDENTIAL_DETAIL.format(why=f"HTTP {response.status_code}")}
        if not response.is_success:
            _remember_render_capability(node_id, None)
            return {**row, "state": "error",
                    "detail": (f"{node.get('address') or 'the node'} answered "
                               f"HTTP {response.status_code} on {PROBE_PATH}")}
        try:
            body = response.json()
        except ValueError:
            _remember_render_capability(node_id, None)
            return {**row, "state": "error", "detail": "node returned an invalid response"}
        if not isinstance(body, dict):
            _remember_render_capability(node_id, None)
            return {**row, "state": "error", "detail": "node returned an invalid response"}
        # The probe's answer is also the host snapshot the Fleet page shows for this machine, so
        # one pass over the roster feeds both routes rather than the page waiting for the poll.
        host = body.get("host") if isinstance(body.get("host"), dict) else {}
        order = _priority_order_of(body)
        # A second, bounded read of the node's own render worker row. A node without the lane
        # (or an older Hub) answers 404 and this stays absent; it never affects the probe's own
        # reachability answer or the node's state.
        if _render_capability_due(node_id):
            _remember_render_capability(
                node_id, await _probe_render_capability(client, node, older))
        result = {**row, "state": "reachable", "detail": None, "host": host,
                # **Why this controller believes the Mac is there.** A node it cannot dial answers
                # through the pull channel, and that answer travelled on the node's own check-in
                # connection: the state is the same, the reason is not, and an operator reading a
                # Shops Mac needs to be able to tell the two apart.
                "via": ("check-in" if getattr(response, "extensions", {}).get("studiohub_pull")
                        else "probe"),
                "version": body.get("version"), "source": body.get("source"),
                "startup": _startup_of(body.get("startup"), row),
                "current_job": _current_job_of(body.get("current_job")),
                "last_stop": last_stop_of(body.get("last_stop")) or _last_known_stop(row),
                "workstation": _workstation_of(body.get("workstation")),
                "priority_order": order,
                "chat_priority": bool(order and order[0] == "chat"),
                "function_enabled": _function_enabled_of(body)}
        inventory = _gate_inventory_of(body.get("developer_gate_inventory"), node_id)
        if inventory is not None:
            result["developer_gate_inventory"] = inventory
        return result
    except (httpx.HTTPError, OSError) as exc:
        # **A Mac this controller cannot dial is not a Mac that is gone.** Sharing a Tailscale node
        # works one way, so three Shops Macs could never be dialled at all — while each of them
        # called home every 60 s. When the node's own authenticated call is recent, that call is the
        # evidence: the state is `reachable` (the Mac is there and talking), and the reason is kept
        # distinct so the roster and the Gateway can say *why* they believe it.
        if checkin_alive(node):
            _remember_render_capability(node_id, None)
            return {**row, "state": "reachable", "detail": None, "via": "check-in",
                    **_checkin_reported(node)}
        # A message that stops at the colon ("could not reach the node: ") reads as though the
        # reason were withheld, and it happens whenever the exception carries an empty message -
        # which is the common case for a connect timeout or a reset. Always name what was tried,
        # what failed, and why when the exception says.
        reason = str(exc).strip()
        detail = f"could not reach {node.get('address') or 'the node'} ({PROBE_PATH}): {type(exc).__name__}"
        if reason:
            detail = f"{detail}: {reason}"
        _remember_render_capability(node_id, None)
        return {**row, "state": "unreachable", "reason": _unreachable_reason(exc), "detail": detail,
                "startup": _last_known_startup(row), "last_stop": _last_known_stop(row)}
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
    #
    # A Mac that is reachable only because it checked in is stamped by `record_checkin` when the
    # call arrives, and is deliberately not stamped again here: `last_seen_via` has to keep saying
    # which evidence this controller actually has, and a probe it could not make is not evidence.
    probed = [row for row in rows
              if row.get("state") == "reachable" and row.get("via") != "check-in"]
    seen_at = note_seen(
        [row["id"] for row in probed],
        {row["id"]: row["startup"] for row in probed
         if isinstance(row.get("startup"), dict) and not row["startup"].get("stale")},
        {row["id"]: row["last_stop"] for row in probed
         if isinstance(row.get("last_stop"), dict) and not row["last_stop"].get("stale")})
    if seen_at is not None:
        for row in probed:
            row["last_seen_at"] = seen_at
    for row in rows:
        if row.get("state") == "reachable":
            row["never_connected"] = False
            row["last_seen_via"] = row.get("via") or row.get("last_seen_via") or "probe"
    # **Which Macs are still on the previous Fleet token** (a rotation they were off for). Named
    # on the row and counted, so the app can say "N nodes still on the previous token" instead of
    # the operator finding out when the old token stops working.
    waiting = nodes_on_previous_token()
    for row in rows:
        row["on_previous_token"] = row["id"] in waiting
        # The owner's hand-pause (`POST .../pause`): no new work to this Mac, it stays joined.
        row["paused"] = not machine_enabled(row["id"])
    on_previous = [row["id"] for row in rows if row["on_previous_token"]]
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
            # How many of those this controller believes because the Mac called in, not because it
            # managed to dial it. The Gateway reads this to tell "we can reach it" from "it is
            # reaching us", which is the whole of the Shops problem.
            "check_in": sum(row["state"] == "reachable" and row.get("via") == "check-in"
                            for row in rows),
            "unreachable": sum(row["state"] == "unreachable" for row in rows),
            "unauthorized": sum(row["state"] == "unauthorized" for row in rows),
            "error": sum(row["state"] == "error" for row in rows),
            "removed": len(_removed()),
            "on_previous_token": len(on_previous),
            # When the previous token stops being accepted for the last Mac still on it.
            "previous_token_until": max((waiting[node] for node in on_previous), default=None),
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
    order = _priority_order_of(row)
    return {
        "machine": machine,
        "label": str(row.get("name") or machine),
        "role": role,
        "address": row.get("address"),
        "state": row.get("state"),
        # Why this controller believes it: "probe" (it dialled and was answered) or "check-in"
        # (the Mac called in and this controller cannot dial it at all).
        "via": row.get("via"),
        "detail": row.get("detail"),
        "version": row.get("version"),
        # The probe's host snapshot travels with the row: the overview shows one machine's chip
        # and memory from the same read that decided it was reachable.
        "host": row.get("host") or {},
        "studios": row.get("studios") or [],
        # What starts when that Mac starts - its own report, or the last one this side heard.
        "startup": row.get("startup") if isinstance(row.get("startup"), dict) else None,
        # What that Mac said it is working on in the snapshot this row came from; None when idle
        # or when it runs a Hub that does not report one.
        "current_job": row.get("current_job") if isinstance(row.get("current_job"), dict) else None,
        # Why that Mac's Hub last stopped, as it recorded it (stale when it is not answering now).
        "last_stop": row.get("last_stop") if isinstance(row.get("last_stop"), dict) else None,
        # Its workstation mode as it reported it (`components/hub/workstation.py`); None from an
        # older Hub or a Mac that is not answering.
        "workstation": row.get("workstation") if isinstance(row.get("workstation"), dict) else None,
        "priority_order": order,
        "chat_priority": bool(order and order[0] == "chat"),
        # The other half of that Mac's work order: the kinds it switched off (owner, 2026-10-28).
        "function_enabled": _function_enabled_of(row),
        # The Mac's own idle-hold preference and remaining window, carried from its snapshot.
        # None/0.0 from an older Hub, which then arbitrates exactly as before.
        "preferred_work": row.get("preferred_work") if isinstance(row.get("preferred_work"), dict) else None,
        "preferred_hold_remaining_s": (
            float(row["preferred_hold_remaining_s"])
            if isinstance(row.get("preferred_hold_remaining_s"), (int, float))
            and not isinstance(row.get("preferred_hold_remaining_s"), bool)
            else 0.0),
    }


STARTUP_RELAY_TIMEOUT = httpx.Timeout(30.0, connect=4.0)


class StartupRelayError(Exception):
    def __init__(self, status: int, body: dict[str, Any]) -> None:
        super().__init__(str(body.get("detail") or body.get("code") or status))
        self.status, self.body = status, body


#: The answer a relay gives when it could not dial the Mac and held the request for it instead.
#: 202, not 200: nothing has happened on the Mac yet, and a caller that reads `ok: true` as
#: "done" would be reading a promise as a fact.
QUEUED_STATUS = 202


def _dial_timeout(node: dict[str, Any], timeout: httpx.Timeout) -> httpx.Timeout:
    """A dial that is already known to be failing is not given a job's timeout to fail in.

    A relay's timeout is the time the *work* may take (a transcription waits 960 s). Handing that
    same number to a dial of a Mac on a foreign tailnet means a caller waits sixteen minutes to be
    told what the roster already knew: the dial does not work there. The Mac's own call home is
    the channel, so the dial is given the probe's short budget and the queue takes over.
    """
    return PROBE_TIMEOUT if dial_is_failing(node) else timeout


def _timeout_seconds(timeout: httpx.Timeout) -> float:
    """The wait a relay's own timeout asks for, in seconds, capped so a bad number cannot hang.

    The **read** budget is the answer: it is how long the caller said it would wait for the Mac to
    answer. Summing every phase (read + write + connect + pool) triples it, and a 30 s relay turned
    into a 94 s wait — measured on this very fallback before it was written down.
    """
    read = getattr(timeout, "read", None)
    if isinstance(read, (int, float)) and not isinstance(read, bool) and read > 0:
        return min(float(read), 1800.0)
    values = [getattr(timeout, name, None) for name in ("read", "write", "connect", "pool")]
    total = max((float(value) for value in values
                 if isinstance(value, (int, float)) and not isinstance(value, bool) and value),
                default=0.0)
    return max(0.0, min(total, 1800.0))


async def _queue_for_unreachable(node_id: str, method: str, path: str, *, payload: Any = None,
                                 content: bytes | None = None, content_type: str = "",
                                 kind: str = "relay",
                                 wait_s: float = 0.0) -> "StartupRelayError | dict[str, Any] | None":
    """Hold a request for a Mac this controller cannot dial, or `None` when it is not checking in.

    The whole of the relay fallback. Dialling is tried first, always; this runs only in the branch
    where the dial failed. It queues **the request the dial would have sent** — same method, same
    path, same body — so the node's own Hub answers it exactly as it would have answered the
    controller, and nothing here has to know what the request means.

    **Two answers, because there are two kinds of request.**

    * A **settings change** (Lite Mode, a rename, startup, job priority) is answered at once with
      `202` and a command id: the Mac applies it on its next call home, and nothing is waiting for
      a result. A caller that read `ok: true` as "done" would be reading a promise as a fact, so
      the status is 202 and `queued` is set.
    * **Work** (a test transcription, a picture analysis, a model install, a Trash listing) is
      something the caller expects an answer for, so `wait_s` is the relay's own timeout and the
      Mac's answer is returned when it arrives — the same wait the dial would have cost.

    `None` means "this Mac is not calling home either", which is the honest answer that keeps the
    old `NODE_UNREACHABLE` behaviour for a Mac that really is off.
    """
    from . import fleet_pull
    if not checkin_alive(_read().get(node_id)):
        return None
    try:
        command = fleet_pull.queue_json(node_id, method, path, payload, kind=kind) \
            if content is None else fleet_pull.queue(node_id, method, path, content=content,
                                                     content_type=content_type, kind=kind)
    except (OSError, ValueError) as exc:
        log.warning("could not queue %s %s for node %s: %s", method, path, node_id, exc)
        return None
    if wait_s > 0:
        result = await fleet_pull.await_result(node_id, command["id"], wait_s)
        if result is not None:
            body = result.get("json")
            if isinstance(body, dict):
                return {**body, "queued": False, "via": "check-in"}
            return {"ok": True, "queued": False, "via": "check-in", "result": body}
    return StartupRelayError(QUEUED_STATUS, {
        "ok": True, "queued": True, "node": node_id, "command": command["id"],
        "detail": ("this controller cannot dial that Mac, so the request was held for it: it "
                   "checks in every minute and applies it then.")})


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
                                     timeout=_dial_timeout(node, STARTUP_RELAY_TIMEOUT))
    except httpx.HTTPError as exc:
        held = await _queue_for_unreachable(wanted, "POST", "/api/hub/startup/request",
                                            payload=fields, kind="startup")
        if held is not None:
            startup_control.audit("relay_queued", {"node": wanted, "action": fields.get("action")})
            if isinstance(held, StartupRelayError):
                raise held from exc
            return {**held, "node": wanted}
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


async def relay_name(node_id: str, name: object, *,
                     client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """Rename one joined Mac **on that Mac**, then on this roster. Raises `StartupRelayError`.

    The rule (owner request 2026-10-02, rename from the Gateway): **a Mac's name lives on the Mac.**
    The roster follows what each Mac reports about itself (`sync_roster_from_snapshots`), so a name
    written here alone would be overwritten by the Mac's next snapshot. The rename therefore goes to the
    Mac first (`POST /api/hub/machine-label`, the route its own Connections page uses, with this
    controller's Fleet token) and the roster row changes only when the Mac took it. A Mac that is off,
    or on a Hub that does not accept a fleet rename, keeps its name and the caller is told why.
    """
    try:
        clean = valid_name(name)
    except ValueError as exc:
        raise StartupRelayError(400, {"ok": False, "code": "BAD_NAME", "detail": str(exc)}) from exc
    wanted = str(node_id or "").strip().lower()
    node = _read().get(wanted)
    if node is None:
        raise StartupRelayError(404, {"ok": False, "code": "UNKNOWN_NODE",
                                      "detail": f"this controller has no joined Mac {node_id!r}"})
    owned = client is None
    if owned:
        client = _probe_client()
    try:
        response = await client.post(f"{node.get('address') or ''}/api/hub/machine-label",
                                     json={"name": clean}, timeout=PROBE_TIMEOUT)
    except httpx.HTTPError as exc:
        held = await _queue_for_unreachable(wanted, "POST", "/api/hub/machine-label",
                                            payload={"name": clean}, kind="rename")
        if isinstance(held, StartupRelayError):
            # Held for the Mac, so the roster keeps the old name: the Mac has not taken the new
            # one, and its own call home will carry it here the moment it has.
            raise held from exc
        if held is not None:
            return {**held, "node": wanted}
        raise StartupRelayError(502, {"ok": False, "code": "NODE_UNREACHABLE",
                                      "reason": _unreachable_reason(exc),
                                      "detail": "that Mac did not answer, so it was not renamed - "
                                                "rename it when it is back"}) from exc
    finally:
        if owned:
            await client.aclose()
    if response.status_code in (401, 403):
        raise StartupRelayError(502, {
            "ok": False, "code": "NODE_REFUSED",
            "detail": "that Mac refused the rename: its Studio Hub is older than fleet renaming, or it "
                      "holds a different Fleet token. Rename it on that Mac (Connections) instead."})
    if not response.is_success:
        raise StartupRelayError(502, {"ok": False, "code": "NODE_ERROR",
                                      "detail": f"that Mac answered HTTP {response.status_code}"})
    rename(wanted, clean)
    # The snapshot already held was taken before the rename; dropped, so the next sync cannot put the
    # old name back before the next poll reads the new one.
    from . import peers
    peers.invalidate(wanted)
    return {"ok": True, "node": wanted, "name": clean}


async def relay_workstation(node_id: str, changes: object, *,
                            client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """Change one joined Mac's workstation settings **on that Mac**. Raises `StartupRelayError`.

    The settings are the node's own (it enforces them), so - like a rename - they are written there,
    with this controller's Fleet token, and nowhere else; the roster learns them from the next poll.
    """
    if not isinstance(changes, dict):
        raise StartupRelayError(400, {"ok": False, "code": "BAD_SETTING",
                                      "detail": "Lite Mode settings must be an object"})
    wanted = str(node_id or "").strip().lower()
    node = _read().get(wanted)
    if node is None:
        raise StartupRelayError(404, {"ok": False, "code": "UNKNOWN_NODE",
                                      "detail": f"this controller has no joined Mac {node_id!r}"})
    owned = client is None
    if owned:
        client = _probe_client()
    try:
        response = await client.post(f"{node.get('address') or ''}/api/hub/workstation",
                                     json=changes, timeout=PROBE_TIMEOUT)
    except httpx.HTTPError as exc:
        held = await _queue_for_unreachable(wanted, "POST", "/api/hub/workstation",
                                            payload=changes, kind="workstation")
        if isinstance(held, StartupRelayError):
            raise held from exc
        if held is not None:
            return {**held, "node": wanted}
        raise StartupRelayError(502, {"ok": False, "code": "NODE_UNREACHABLE",
                                      "reason": _unreachable_reason(exc),
                                      "detail": "that Mac did not answer, so its settings did not "
                                                "change - try again when it is back"}) from exc
    finally:
        if owned:
            await client.aclose()
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code == 400 and isinstance(body, dict) and body.get("code") == "BAD_SETTING":
        raise StartupRelayError(400, body)
    if response.status_code in (401, 403, 404, 405):
        raise StartupRelayError(502, {
            "ok": False, "code": "NODE_REFUSED",
            "detail": "that Mac refused: its Studio Hub is older than Lite Mode, or it holds a "
                      "different Fleet token. Update it first."})
    if not response.is_success or not isinstance(body, dict):
        raise StartupRelayError(502, {"ok": False, "code": "NODE_ERROR",
                                      "detail": f"that Mac answered HTTP {response.status_code}"})
    from . import peers
    peers.invalidate(wanted)
    return {"ok": True, "node": wanted, "workstation": _workstation_of(body.get("workstation"))}


async def relay_work_policy(node_id: str, method: str, body: object = None, *,
                            client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """Read or change one joined Mac's policy on that Mac."""
    wanted = str(node_id or "").strip().lower()
    node = _read().get(wanted)
    if node is None:
        raise StartupRelayError(404, {"ok": False, "code": "UNKNOWN_NODE",
                                      "detail": f"this controller has no joined Mac {node_id!r}"})
    if method not in {"GET", "POST"}:
        raise ValueError("work-policy relay supports GET or POST")
    owned = client is None
    if owned:
        client = _probe_client()
    try:
        response = await client.request(
            method, f"{node.get('address') or ''}/api/hub/work-policy",
            json=body if method == "POST" else None, timeout=PROBE_TIMEOUT)
    except httpx.HTTPError as exc:
        held = await _queue_for_unreachable(wanted, method, "/api/hub/work-policy",
                                            payload=body if method == "POST" else None,
                                            kind="work_policy")
        if isinstance(held, StartupRelayError):
            raise held from exc
        if held is not None:
            return {**held, "node": wanted}
        raise StartupRelayError(502, {"ok": False, "code": "NODE_UNREACHABLE",
                                      "reason": _unreachable_reason(exc),
                                      "detail": "that Mac did not answer, so its priority did not change"}) from exc
    finally:
        if owned:
            await client.aclose()
    try:
        answer = response.json()
    except ValueError:
        answer = {}
    if response.status_code == 400 and isinstance(answer, dict):
        raise StartupRelayError(400, answer)
    if response.status_code in (401, 403, 404, 405):
        raise StartupRelayError(409, {
            "ok": False, "code": "WORK_POLICY_UNSUPPORTED",
            "detail": "that Mac must update and restart Studio Hub before its job priority can be edited"})
    if not response.is_success or not isinstance(answer, dict):
        raise StartupRelayError(502, {"ok": False, "code": "NODE_ERROR",
                                      "detail": f"that Mac answered HTTP {response.status_code}"})
    if method == "POST":
        from . import peers
        peers.invalidate(wanted)
    return {"ok": True, "node": wanted, "work_policy": answer}


# The memory-admission route's own refusal codes are all Upper_Snake; a 404 body whose code
# matches is this route speaking, not an unknown-Hub default.
_ADMISSION_404_CODE = re.compile(r"^[A-Z][A-Z0-9_]*$")


async def relay_memory_admission(node_id: str, method: str, body: object = None, *,
                                 query: dict[str, Any] | None = None,
                                 client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """Read or change one joined Mac's memory admission **on that Mac**.

    The target answers with its own effective state; the controller never re-derives it. A PUT
    is queued only when the dial provably never sent the request (connect failure); any later
    failure is ``outcome: unknown`` and is never queued or replayed, because the write may have
    applied. A GET is never queued.
    """
    wanted = str(node_id or "").strip().lower()
    node = _read().get(wanted)
    if node is None:
        raise StartupRelayError(404, {"ok": False, "code": "UNKNOWN_NODE",
                                      "detail": f"this controller has no joined Mac {node_id!r}"})
    if method not in {"GET", "PUT"}:
        raise ValueError("memory-admission relay supports GET or PUT")
    path = f"/api/hub/fleet/nodes/{wanted}/memory/admission"
    if method == "GET":
        pairs = [("model", str((query or {}).get("model") or ""))]
        if (query or {}).get("mode") is not None:
            pairs.append(("mode", str(query["mode"])))
        path = path + "?" + "&".join(f"{key}={quote(value, safe='')}"
                                     for key, value in pairs)
    owned = client is None
    if owned:
        client = _probe_client()
    try:
        response = await client.request(
            method, f"{node.get('address') or ''}{path}",
            json=body if method == "PUT" else None, timeout=PROBE_TIMEOUT)
    except httpx.HTTPError as exc:
        reason = _unreachable_reason(exc)
        if method == "GET":
            raise StartupRelayError(502, {
                "ok": False, "code": "NODE_UNREACHABLE", "reason": reason,
                "detail": "that Mac did not answer, so its memory admission was not read"}) from exc
        if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
            held = await _queue_for_unreachable(wanted, "PUT", path, payload=body,
                                                kind="memory_admission")
            if isinstance(held, StartupRelayError):
                # r5 says pending for both a held write and the target's own 202.
                raise StartupRelayError(held.status, {**held.body, "outcome": "pending"}) from exc
            if held is not None:
                return {**held, "node": wanted}
            raise StartupRelayError(502, {
                "ok": False, "code": "NODE_UNREACHABLE", "reason": reason,
                "detail": "that Mac did not answer and is not checking in, so nothing was "
                          "sent or queued"}) from exc
        raise StartupRelayError(502, {
            "ok": False, "code": "NODE_UNREACHABLE", "outcome": "unknown", "reason": reason,
            "detail": "that Mac did not answer after the request may have been sent; nothing "
                      "was queued or replayed"}) from exc
    finally:
        if owned:
            await client.aclose()
    try:
        answer = response.json()
    except ValueError:
        answer = {}
    if response.status_code == 200 and isinstance(answer, dict):
        return answer
    if response.status_code == 202 and isinstance(answer, dict):
        return {**answer, "outcome": "pending"}
    if response.status_code in (400, 409) and isinstance(answer, dict):
        # A CAS conflict must stay a 409 with its fresh readback; a 400 keeps code/field/detail.
        raise StartupRelayError(response.status_code, answer)
    if response.status_code in (401, 403, 405):
        raise StartupRelayError(502, {
            "ok": False, "code": "NODE_REFUSED",
            "detail": "that Mac refused: its Studio Hub is older than this route, or it holds "
                      "a different Fleet token. Update it first."})
    if response.status_code == 404:
        # This route names its own misses (UNKNOWN_MODEL). A body this route's vocabulary
        # produced passes through unchanged so that code survives the relay; an older Mac that
        # does not know the route still answers a bare string and reads as NODE_REFUSED.
        if (isinstance(answer, dict) and answer.get("ok") is False
                and _ADMISSION_404_CODE.fullmatch(str(answer.get("code") or "")) is not None):
            raise StartupRelayError(404, answer)
        raise StartupRelayError(502, {
            "ok": False, "code": "NODE_REFUSED",
            "detail": "that Mac refused: its Studio Hub is older than this route, or it holds "
                      "a different Fleet token. Update it first."})
    raise StartupRelayError(502, {"ok": False, "code": "NODE_ERROR",
                                  "detail": f"that Mac answered HTTP {response.status_code}"})


async def relay_render_authority(node_id: str, method: str, body: object = None, *,
                                client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """Read or change one joined Mac's final-render opt-in **on that Mac**.

    The setting is the node's own (it is the one that offers the capacity), so it is written
    there with this controller's Fleet token and kept nowhere else - no SSH, no new port, the
    same dial-then-check-in-queue path as Lite Mode and work priority. Raises `StartupRelayError`.
    """
    wanted = str(node_id or "").strip().lower()
    node = _read().get(wanted)
    if node is None:
        raise StartupRelayError(404, {"ok": False, "code": "UNKNOWN_NODE",
                                      "detail": f"this controller has no joined Mac {node_id!r}"})
    if method not in {"GET", "POST"}:
        raise ValueError("render-authority relay supports GET or POST")
    owned = client is None
    if owned:
        client = _probe_client()
    try:
        response = await client.request(
            method, f"{node.get('address') or ''}/api/hub/render-authority",
            json=body if method == "POST" else None, timeout=PROBE_TIMEOUT)
    except httpx.HTTPError as exc:
        held = await _queue_for_unreachable(wanted, method, "/api/hub/render-authority",
                                            payload=body if method == "POST" else None,
                                            kind="render_authority")
        if isinstance(held, StartupRelayError):
            raise held from exc
        if held is not None:
            return {**held, "node": wanted}
        raise StartupRelayError(502, {"ok": False, "code": "NODE_UNREACHABLE",
                                      "reason": _unreachable_reason(exc),
                                      "detail": "that Mac did not answer, so its remote "
                                                "rendering setting did not change"}) from exc
    finally:
        if owned:
            await client.aclose()
    try:
        answer = response.json()
    except ValueError:
        answer = {}
    if response.status_code == 400 and isinstance(answer, dict):
        raise StartupRelayError(400, answer)
    if response.status_code in (401, 403, 404, 405):
        raise StartupRelayError(409, {
            "ok": False, "code": "RENDER_AUTHORITY_UNSUPPORTED",
            "detail": "that Mac must update and restart Studio Hub before remote rendering "
                      "can be allowed on it"})
    if not response.is_success or not isinstance(answer, dict):
        raise StartupRelayError(502, {"ok": False, "code": "NODE_ERROR",
                                      "detail": f"that Mac answered HTTP {response.status_code}"})
    if method == "POST":
        # Read the leaf's row again now, so the roster does not keep advertising the old
        # answer for the rest of its refresh interval.
        await _refresh_render_capability(node)
    return {"ok": True, "node": wanted, "render_authority": answer.get("render_authority")}


async def relay_render_while_paused(node_id: str, method: str, body: object = None, *,
                                    client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """Read or change one joined Mac's "my own pause exempts final render" switch **on that Mac**.

    The setting belongs to the Mac that renders, so it is kept there and reached the same way the
    render opt-in is: this controller's Fleet token, a dial, and the check-in queue when a Mac
    cannot be dialled. Never a file written on the Mac from here.
    """
    wanted = str(node_id or "").strip().lower()
    node = _read().get(wanted)
    if node is None:
        raise StartupRelayError(404, {"ok": False, "code": "UNKNOWN_NODE",
                                      "detail": f"this controller has no joined Mac {node_id!r}"})
    if method not in {"GET", "POST"}:
        raise ValueError("render-while-paused relay supports GET or POST")
    owned = client is None
    if owned:
        client = _probe_client()
    try:
        response = await client.request(
            method, f"{node.get('address') or ''}/api/hub/render-while-paused",
            json=body if method == "POST" else None, timeout=PROBE_TIMEOUT)
    except httpx.HTTPError as exc:
        held = await _queue_for_unreachable(wanted, method, "/api/hub/render-while-paused",
                                            payload=body if method == "POST" else None,
                                            kind="render_while_paused")
        if isinstance(held, StartupRelayError):
            raise held from exc
        if held is not None:
            return {**held, "node": wanted}
        raise StartupRelayError(502, {"ok": False, "code": "NODE_UNREACHABLE",
                                      "reason": _unreachable_reason(exc),
                                      "detail": "that Mac did not answer, so whether its pause "
                                                "exempts final render did not change"}) from exc
    finally:
        if owned:
            await client.aclose()
    try:
        answer = response.json()
    except ValueError:
        answer = {}
    if response.status_code == 400 and isinstance(answer, dict):
        raise StartupRelayError(400, answer)
    if response.status_code in (401, 403, 404, 405):
        raise StartupRelayError(409, {
            "ok": False, "code": "RENDER_WHILE_PAUSED_UNSUPPORTED",
            "detail": "that Mac must update and restart Studio Hub before its pause can exempt "
                      "final render"})
    if not response.is_success or not isinstance(answer, dict):
        raise StartupRelayError(502, {"ok": False, "code": "NODE_ERROR",
                                      "detail": f"that Mac answered HTTP {response.status_code}"})
    if method == "POST":
        # Its render row carries the switch, so read it again rather than advertising the old
        # answer for the rest of this refresh interval.
        await _refresh_render_capability(node)
    return {"ok": True, "node": wanted,
            "render_while_paused": answer.get("render_while_paused")}


#: A relayed test transcription waits for the node's answer: the node's own Subtitle deadline
#: (900 s, `standalone.PEER_PROXY_TIMEOUT_FLOOR_S`) plus a minute for the hop.
TRANSCRIBE_RELAY_TIMEOUT = httpx.Timeout(960.0, connect=4.0)
#: A relayed picture analysis: four outlines on a slow 8 GB Mac stay well inside this.
VISION_RELAY_TIMEOUT = httpx.Timeout(150.0, connect=4.0)


@dataclass(frozen=True)
class RenderRelayResponse:
    status_code: int
    headers: dict[str, str]
    body: bytes


_RENDER_RELAY_RESPONSE_HEADERS = frozenset({
    "content-range", "content-type", "content-length", "etag", "x-content-sha256",
    "accept-ranges",
})
_RENDER_JOB_ID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
_RENDER_BLOB_SHA = re.compile(r"[0-9a-f]{64}\Z")
_RENDER_DISPATCH_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@+=,~-]{0,255}\Z")
_RENDER_RANGE = re.compile(r"bytes=(\d+)-(\d+)\Z")
_RENDER_IF_MATCH = re.compile(r'"[0-9a-f]{64}"\Z')
_MAX_RENDER_RELAY_RESPONSE = 8 * 1024 * 1024


def _render_relay_segment(operation: str, identity: str) -> str:
    if operation in {"submit", "encode_probe_get", "encode_probe_post",
                     "output_settings_capabilities", "late_label_capabilities"}:
        if identity:
            raise ValueError("this operation does not accept a render identity")
        return ""
    patterns = {
        "blob_put": _RENDER_BLOB_SHA,
        "blob_status": _RENDER_BLOB_SHA,
        "key_get": _RENDER_DISPATCH_KEY,
        "key_cancel": _RENDER_DISPATCH_KEY,
        "status": _RENDER_JOB_ID,
        "cancel": _RENDER_JOB_ID,
        "output": _RENDER_JOB_ID,
        "ack": _RENDER_JOB_ID,
    }
    pattern = patterns.get(operation)
    if pattern is None:
        raise ValueError("unsupported final-render relay operation")
    if (type(identity) is not str or pattern.fullmatch(identity) is None
            or ".." in identity
            or any(value in identity for value in ("/", "\\", "%", "?", "#"))):
        raise ValueError("final-render relay identity is invalid")
    return quote(identity, safe="")


async def relay_final_render(
    node_id: str,
    operation: str,
    *,
    identity: str = "",
    body: bytes = b"",
    headers: dict[str, str] | None = None,
    size_bytes: int | None = None,
    requested_encode: str | None = None,
    requested_contract: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> RenderRelayResponse:
    """Relay one fixed final-render operation to one joined node; no caller path or host."""
    wanted = str(node_id or "").strip().lower()
    node = _read().get(wanted)
    if node is None:
        raise StartupRelayError(404, {"ok": False, "code": "UNKNOWN_NODE",
                                      "detail": f"this controller has no joined Mac {node_id!r}"})
    segment = _render_relay_segment(operation, identity)
    paths = {
        "blob_put": f"/api/hub/final-renders/blobs/{segment}",
        "blob_status": f"/api/hub/final-renders/blobs/{segment}/status",
        "submit": "/api/hub/final-renders",
        "encode_probe_get": "/api/hub/final-renders/encode-probes",
        "encode_probe_post": "/api/hub/final-renders/encode-probes",
        "output_settings_capabilities": "/api/hub/final-renders/output-settings/capabilities",
        "late_label_capabilities": "/api/hub/final-renders/capabilities",
        "key_get": f"/api/hub/final-renders/by-idempotency-key/{segment}",
        "key_cancel": f"/api/hub/final-renders/by-idempotency-key/{segment}/cancel",
        "status": f"/api/hub/final-renders/{segment}",
        "cancel": f"/api/hub/final-renders/{segment}/cancel",
        "output": f"/api/hub/final-renders/{segment}/output",
        "ack": f"/api/hub/final-renders/{segment}/ack",
    }
    methods = {
        "blob_put": "PUT", "blob_status": "GET", "submit": "POST",
        "encode_probe_get": "GET", "encode_probe_post": "POST",
        "output_settings_capabilities": "GET", "late_label_capabilities": "GET",
        "key_get": "GET", "key_cancel": "POST", "status": "GET",
        "cancel": "POST", "output": "GET", "ack": "POST",
    }
    request_headers = {
        name: value for name, value in (headers or {}).items()
        if name.lower() in {
            "range", "content-range", "content-length", "content-type",
            "if-match", "idempotency-key", "x-studio-render-qualification-vector",
        }
    }
    params = {"size_bytes": size_bytes} if operation == "blob_status" else None
    if operation == "encode_probe_get":
        from .render_encode import normalize_encode
        from .render_hash_v1 import decode_render_json
        if type(requested_encode) is not str or len(requested_encode.encode("utf-8")) > 4096:
            raise ValueError("one bounded requested_encode is required")
        if normalize_encode(decode_render_json(requested_encode.encode("utf-8"))) is None:
            raise ValueError("invalid requested_encode")
        params = {"requested_encode": requested_encode}
    elif requested_encode is not None:
        raise ValueError("requested_encode is only valid on probe discovery")
    if requested_contract is not None:
        if (type(requested_contract) is not str or not requested_contract
                or len(requested_contract.encode("utf-8")) > 256):
            raise ValueError("one bounded requested_contract is required")
        if operation == "late_label_capabilities":
            params = {"requested_contract": requested_contract}
        elif operation in {"encode_probe_get", "encode_probe_post"}:
            params = {**(params or {}), "requested_contract": requested_contract}
        else:
            raise ValueError("requested_contract is only valid on a scoped render query")
    elif operation == "late_label_capabilities":
        raise ValueError("late-label capabilities requires requested_contract")
    requested_range = None
    if operation == "output":
        range_value = next((value for key, value in request_headers.items()
                            if key.lower() == "range"), "")
        match = _RENDER_RANGE.fullmatch(range_value)
        if match is None:
            raise ValueError("relayed render output requires one bounded Range")
        start, end = map(int, match.groups())
        if start > end or end - start + 1 > _MAX_RENDER_RELAY_RESPONSE:
            raise ValueError("relayed render output requires one bounded Range")
        if_match = next((value for key, value in request_headers.items()
                         if key.lower() == "if-match"), "")
        if _RENDER_IF_MATCH.fullmatch(if_match) is None:
            raise ValueError("relayed render output requires one exact If-Match")
        requested_range = (start, end)
    owned = client is None
    if owned:
        client = _probe_client()
    try:
        async with client.stream(
                methods[operation], f"{node.get('address') or ''}{paths[operation]}",
                content=body, headers=request_headers, params=params,
                timeout=httpx.Timeout(60.0, connect=4.0)) as response:
            response_headers = {
                key.lower(): value for key, value in response.headers.items()
            }
            declared = response_headers.get("content-length")
            try:
                declared_bytes = int(declared) if declared is not None else None
            except ValueError as exc:
                raise StartupRelayError(502, {
                    "ok": False, "code": "NODE_ERROR",
                    "detail": "that Mac returned an invalid render Content-Length",
                }) from exc
            if (declared_bytes is not None
                    and (declared_bytes < 0
                         or declared_bytes > _MAX_RENDER_RELAY_RESPONSE)):
                raise StartupRelayError(502, {
                    "ok": False, "code": "NODE_ERROR",
                    "detail": "that Mac returned an oversized render response",
                })
            if operation == "output" and 200 <= response.status_code < 300:
                content_range = response_headers.get("content-range") or ""
                match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", content_range)
                if match is None or response.status_code != 206:
                    raise StartupRelayError(502, {
                        "ok": False, "code": "NODE_ERROR",
                        "detail": "that Mac ignored the bounded render output range",
                    })
                start, end, total = map(int, match.groups())
                expected = end - start + 1
                if (requested_range != (start, end) or start > end or end >= total
                        or expected > _MAX_RENDER_RELAY_RESPONSE
                        or (declared_bytes is not None and declared_bytes != expected)):
                    raise StartupRelayError(502, {
                        "ok": False, "code": "NODE_ERROR",
                        "detail": "that Mac returned an invalid render output range",
                    })
            content = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                if len(content) + len(chunk) > _MAX_RENDER_RELAY_RESPONSE:
                    raise StartupRelayError(502, {
                        "ok": False, "code": "NODE_ERROR",
                        "detail": "that Mac returned an oversized render response",
                    })
                content.extend(chunk)
            if declared_bytes is not None and len(content) != declared_bytes:
                raise StartupRelayError(502, {
                    "ok": False, "code": "NODE_ERROR",
                    "detail": "that Mac returned an incomplete render response",
                })
            status_code = response.status_code
            allowed_headers = {
                key: value for key, value in response.headers.items()
                if key.lower() in _RENDER_RELAY_RESPONSE_HEADERS
            }
    except httpx.HTTPError as exc:
        raise StartupRelayError(502, {
            "ok": False, "code": "NODE_UNREACHABLE", "reason": _unreachable_reason(exc),
            "detail": "that Mac did not answer; the render outcome remains unknown",
        }) from exc
    finally:
        if owned:
            await client.aclose()
    return RenderRelayResponse(
        status_code=status_code, headers=allowed_headers, body=bytes(content),
    )


async def relay_model(node_id: str, method: str, path: str, *, payload: Any = None,
                      content: bytes | None = None, content_type: str = "",
                      timeout: httpx.Timeout = STARTUP_RELAY_TIMEOUT,
                      feature: str = "installing models from the Gateway (needs Studio Hub 0.6.143)",
                      client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """Pass a model install (or its status read, or a test transcription) to one joined node's own
    fleet route, ``local``.

    ``path`` is the part after ``/api/hub/fleet/nodes/local``. ``payload`` travels as JSON;
    ``content`` (with its ``content_type``) travels as-is, which is how a multipart upload is relayed.
    The node checks and audits the request itself; its refusal (unknown package, disk,
    memory, a model it cannot run) comes back with its own status and sentence. Raises
    `StartupRelayError`.
    """
    wanted = str(node_id or "").strip().lower()
    node = _read().get(wanted)
    if node is None:
        raise StartupRelayError(404, {"ok": False, "code": "UNKNOWN_NODE",
                                      "detail": f"this controller has no joined Mac {node_id!r}"})
    owned = client is None
    if owned:
        client = _probe_client()
    try:
        extra: dict[str, Any] = {"json": payload} if payload is not None else {}
        if content is not None:
            extra = {"content": content, "headers": {"Content-Type": content_type}}
        response = await client.request(
            method, f"{node.get('address') or ''}/api/hub/fleet/nodes/local{path}",
            timeout=_dial_timeout(node, timeout), **extra)
    except httpx.HTTPError as exc:
        # A model install, a status read, a test transcription, a vision analysis or a trash
        # operation: the same request, held for the Mac to replay on its own Hub.
        # **Work, not a setting**: the caller is waiting for an answer (a transcript, an
        # analysis, a Trash listing), so the wait is the relay's own timeout and the Mac's answer
        # is returned when it arrives. A Mac that does not answer in time is reported as queued
        # rather than as a failure - the request is not lost, it is held.
        held = await _queue_for_unreachable(
            wanted, method, f"/api/hub/fleet/nodes/local{path}",
            payload=payload if content is None else None,
            content=content, content_type=content_type, kind="model",
            wait_s=_timeout_seconds(timeout))
        if isinstance(held, StartupRelayError):
            raise held from exc
        if held is not None:
            return {**held, "node": wanted}
        raise StartupRelayError(502, {"ok": False, "code": "NODE_UNREACHABLE",
                                      "reason": _unreachable_reason(exc),
                                      "detail": "that Mac did not answer, so nothing ran there - "
                                                "try again when it is back"}) from exc
    finally:
        if owned:
            await client.aclose()
    try:
        body = response.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    if response.status_code in (401, 403, 405):
        raise StartupRelayError(502, {
            "ok": False, "code": "NODE_REFUSED",
            "detail": f"that Mac refused: its Studio Hub is older than {feature}, or it holds a "
                      "different Fleet token. Update it first."})
    if not response.is_success:
        body = {"ok": False, **body}
        body.setdefault("detail", f"that Mac answered HTTP {response.status_code}")
        raise StartupRelayError(response.status_code, body)
    return {**body, "node": wanted}


async def read_node(node_id: str, path: str, *, limit_bytes: int = 4 * 1024 * 1024,
                    timeout: httpx.Timeout = PROBE_TIMEOUT,
                    client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """One bounded, **read-only** GET of a joined node's own fleet path. **Never queued.**

    `relay_model` is the route for work and for settings: a request it cannot deliver is held
    for the Mac's next check-in, and something is waiting for its answer. A history page is
    neither. Its rows are a snapshot of what already happened, so a Mac that cannot be dialled
    right now is reported as not answering — a page that queued one request per Mac per open
    would fill the check-in queue with reads nobody is waiting for.

    Returns `{"ok": True, "body": <the node's JSON>}`, or `{"ok": False, "reason": ...,
    "detail": ...}` with the same vocabulary `_unreachable_reason` gives a probe.
    """
    wanted = str(node_id or "").strip().lower()
    node = _read().get(wanted)
    if node is None:
        return {"ok": False, "reason": "unknown_node",
                "detail": f"this controller has no joined Mac {node_id!r}"}
    owned = client is None
    if owned:
        client = _probe_client()
    try:
        response = await client.get(
            f"{node.get('address') or ''}/api/hub/fleet/nodes/local{path}",
            timeout=_dial_timeout(node, timeout))
        if not response.is_success:
            return {"ok": False, "reason": f"http_{response.status_code}",
                    "detail": f"that Mac answered HTTP {response.status_code}"}
        if len(response.content) > limit_bytes:
            return {"ok": False, "reason": "answer_too_large",
                    "detail": "that Mac's answer was larger than this read accepts"}
        body = response.json()
    except httpx.HTTPError as exc:
        return {"ok": False, "reason": _unreachable_reason(exc),
                "detail": "that Mac did not answer"}
    except ValueError:
        return {"ok": False, "reason": "invalid_answer",
                "detail": "that Mac did not answer with JSON"}
    finally:
        if owned:
            await client.aclose()
    if not isinstance(body, dict):
        return {"ok": False, "reason": "invalid_answer",
                "detail": "that Mac's answer was not an object"}
    return {"ok": True, "body": body}


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
