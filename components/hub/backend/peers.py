"""Peer Hubs — the fleet layer.

Each Mac runs its own Studio Hub. A Hub is the local authority for its machine:
it alone can read that machine's host RAM, per-studio process memory, and run
`pterm` to start/stop its studios. So to see a remote Mac's specs or control its
servers, the primary Hub asks THAT machine's Hub.

A "peer" is derived automatically from any remote studio entry: the peer Hub
lives at http://<studio.host>:<hub_port default 47873>. No separate peer list —
if you registered a machine's studios, its Hub is reachable at the same host.

Auth across Hubs uses a shared **fleet token**: set the same token on every
Mac's Hub (dashboard → Remote, or STUDIOHUB_FLEET_TOKEN env). Each Hub accepts
it; the primary presents it when calling peers. Recursion is prevented with
?local_only=true so a peer returns only its own machine and never fans back out.

Peer resource data is cached (short TTL) and refreshed from the monitor poll
loop, so the 5s dashboard poll never blocks on slow/offline peers.
"""

import asyncio
import json
import os
import secrets
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from . import registry
from .registry import DATA_DIR, LAUNCHER_ROOT

FLEET_TOKEN_FILE = DATA_DIR / ".fleet_token"
SHARED_STUDIO_TOKEN_FILE = (
    LAUNCHER_ROOT.parent / ".kh_studio_token"
    if DATA_DIR == LAUNCHER_ROOT else DATA_DIR / ".kh_studio_token"
)
#: Nodes that were off when the Fleet token was rotated, and the token each of them still holds.
#: `{"nodes": {node_id: {"token": <the one it holds>, "until": <epoch>}}}`, 0600, next to the
#: current token. Rotating used to switch the controller over regardless, which stranded those
#: nodes until someone re-joined each by hand; now the controller keeps presenting a node the token
#: it holds, pushes the new one when it answers, and forgets the old one once every node confirmed
#: or the window ended.
PREVIOUS_TOKENS_FILE = DATA_DIR / ".fleet_token_previous"
PREVIOUS_TOKEN_GRACE_S = 30 * 24 * 3600
DEFAULT_HUB_PORT = 47873
PEER_TTL_S = 12.0
# How old a snapshot may get before the sweep reads it again. It must be well under PEER_TTL_S,
# the age at which `dispatch_ready` stops trusting it: the sweep runs every monitor poll (5 s), so
# refreshing only at the TTL left every remote Mac "not ready" for the last ~3 s of every ~15 s
# cycle - a freed node sat idle, and the scarcest-first pool shrank, a fifth of the time.
PEER_REFRESH_S = 6.0
PEER_TIMEOUT_S = 5.0

# Changes to the local fleet credential invalidate any peer response that began
# under the previous credential.  Per-machine proxy rejections live in the
# cache as a tombstone so normal cache clearing also clears test/process state.
_auth_generation = 0
_DISPATCH_REJECTED = "_dispatch_rejected"
_DISPATCH_MODALITY = "_dispatch_modality"

# machine -> (ts, {"host": {...}|None, "studios": {modality: stats}, "reachable": bool})
_cache: dict[str, tuple[float, dict]] = {}
_peer_alert_state: dict[str, dict] = {}
PEER_FAILURES_TO_ALERT = 3


def fleet_token() -> str | None:
    env = os.environ.get("STUDIOHUB_FLEET_TOKEN")
    if env and env.strip():
        return env.strip()
    if FLEET_TOKEN_FILE.exists():
        os.chmod(FLEET_TOKEN_FILE, 0o600)
        t = FLEET_TOKEN_FILE.read_text().strip()
        if t:
            return t
    # A packaged Hub owns a fleet credential of its own, distinct from its Hub token. The
    # separation is the point: this value is what a joining Mac adopts and what a controller then
    # presents back to it, so it has to be a value the node has seen - and it is also the whole
    # reason a node's owner cannot administer the controller that owns them.
    token = secrets.token_urlsafe(24)
    set_fleet_token(token)
    return token


def current_fleet_token() -> str | None:
    """Return the currently configured fleet token without changing state."""
    env = os.environ.get("STUDIOHUB_FLEET_TOKEN")
    if env and env.strip():
        return env.strip()
    try:
        value = FLEET_TOKEN_FILE.read_text().strip()
    except (OSError, UnicodeError):
        return None
    return value or None


def set_fleet_token(token: str):
    global _auth_generation
    value = (token or "").strip() or secrets.token_urlsafe(24)
    previous = current_fleet_token()
    for path in (FLEET_TOKEN_FILE, SHARED_STUDIO_TOKEN_FILE):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value + "\n")
        os.chmod(path, 0o600)
    if previous and previous != value:
        _auth_generation += 1
        _cache.clear()


# -- nodes still on the previous Fleet token --------------------------------
def _held_tokens(now: float | None = None) -> dict[str, dict]:
    """`{node_id: {"token", "until"}}` for nodes not yet confirmed on the current token."""
    now = time.time() if now is None else now
    try:
        document = json.loads(PREVIOUS_TOKENS_FILE.read_text(encoding="utf-8"))
        rows = document["nodes"]
    except (OSError, ValueError, KeyError, TypeError):
        return {}
    if not isinstance(rows, dict):
        return {}
    current = current_fleet_token()
    return {str(node): row for node, row in rows.items()
            if isinstance(row, dict) and isinstance(row.get("token"), str) and row["token"]
            and row["token"] != current and isinstance(row.get("until"), (int, float))
            and row["until"] > now}


def _write_held_tokens(rows: dict[str, dict]) -> None:
    if not rows:
        PREVIOUS_TOKENS_FILE.unlink(missing_ok=True)
        return
    PREVIOUS_TOKENS_FILE.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".fleet_token_previous-",
                                        dir=PREVIOUS_TOKENS_FILE.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"nodes": rows}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o600)
        temporary.replace(PREVIOUS_TOKENS_FILE)
    finally:
        temporary.unlink(missing_ok=True)


def held_token(node_id: str) -> str | None:
    """The older Fleet token this node still holds, while its grace window is open."""
    row = _held_tokens().get(str(node_id))
    return row["token"] if row else None


def note_nodes_on_previous_token(held: dict[str, str], *, window_s: float = PREVIOUS_TOKEN_GRACE_S,
                                 confirmed: tuple[str, ...] = ()) -> None:
    """Record which nodes did not get a rotation (`held`: node -> the token it holds) and which did.

    A node already waiting keeps its original deadline: a second rotation must not extend how long
    an old token stays good.
    """
    rows = _held_tokens()
    for node in confirmed:
        rows.pop(str(node), None)
    until = time.time() + window_s
    for node, token in held.items():
        rows[str(node)] = {"token": token, "until": rows.get(str(node), {}).get("until", until)}
    _write_held_tokens(rows)


def confirm_node(node_id: str) -> None:
    """This node now holds the current token: it needs the previous one no more."""
    rows = _held_tokens()
    if rows.pop(str(node_id), None) is not None:
        _write_held_tokens(rows)


def nodes_on_previous_token() -> dict[str, float]:
    """`{node_id: deadline}` for every node that has not confirmed the current token yet."""
    return {node: float(row["until"]) for node, row in _held_tokens().items()}


def clear_previous_tokens() -> int:
    """Forget every node still waiting on a previous Fleet token. Returns how many went.

    A rotation keeps the old token good for a bounded window so an offline Mac is not stranded -
    that is the whole point of the file. A group that has been **disassembled** has no such Mac to
    strand: its nodes are no longer on the roster, the group token they held has just been replaced,
    and keeping their old value good would leave a working group credential on this Mac for machines
    it has deliberately forgotten.

    **The file is cleared, not archived** — the same treatment `set_node_credential(None)` and a
    cleared database URL get, and for the same reason: `_trash` is for records the owner may want
    back, and a retired credential is not one. It stores the *plaintext* Fleet token each waiting
    node still holds, so a copy under `_trash` would be a copy of the group secret on disk.
    """
    gone = len(_held_tokens())
    PREVIOUS_TOKENS_FILE.unlink(missing_ok=True)
    return gone


def accepts_previous_token(offered: str | None) -> bool:
    """Whether `offered` is a token a not-yet-confirmed node still holds (constant-time)."""
    if not offered:
        return False
    return any([secrets.compare_digest(offered, row["token"]) for row in _held_tokens().values()])


def studio_headers(studio: dict | None = None) -> dict[str, str]:
    token = (studio or {}).get("studio_token") or fleet_token()
    return {"X-Studio-Token": token} if token else {}


def _peer_url(studio: dict) -> str:
    return f"http://{studio['host']}:{studio.get('hub_port', DEFAULT_HUB_PORT)}"


def _peer_token(studio: dict) -> str | None:
    node = studio.get("fleet_node")
    if node:
        # A joined node is asked with whatever token *it* holds, read now rather than baked into
        # the registry entry at load: the entry outlives a rotation, and a node that missed one
        # still answers only to the older token until it is told the new one.
        return held_token(node) or fleet_token()
    return studio.get("hub_token") or fleet_token()


def is_local_studio(studio: dict) -> bool:
    return (studio.get("machine", "local") == "local"
            or (os.environ.get("STUDIOHUB_PACKAGED") == "1" and studio.get("local") is True))


def note_dispatch(studio: dict) -> None:
    """One of this Mac's own queues started a job on this Mac: Lite Mode's "Today: N jobs" counts it,
    as it counts a controller's job at the peer door (`standalone`). A job sent to a node is that
    node's to count."""
    if is_local_studio(studio):
        from components.hub import workstation
        workstation.note_job_accepted()


def studio_request(studio: dict, path_or_url: str) -> tuple[str, dict[str, str]]:
    """Return the safest URL + credentials for a Studio API request.

    A peer Hub is the local authority for every remote machine. Always route
    remote Studio traffic through it, even before the short-lived peer-status
    cache has populated. Falling back to the Studio during that window creates
    an authentication race: a worker with an older in-memory credential can
    consume and fail a batch before its local Hub is marked connected.
    """
    parsed = urlsplit(path_or_url)
    path = parsed.path.lstrip("/")
    if parsed.query:
        path += "?" + parsed.query
    if not is_local_studio(studio):
        token = _peer_token(studio)
        headers = {"X-Hub-Token": token} if token else {}
        return f"{_peer_url(studio)}/studio/{studio['modality']}/{path}", headers
    if os.environ.get("STUDIOHUB_PACKAGED") == "1" and (parsed.scheme or parsed.netloc):
        try:
            same_origin = (
                parsed.scheme == "http" and parsed.hostname == studio["host"]
                and parsed.port == studio["port"] and not parsed.username
                and not parsed.password and not parsed.fragment
            )
        except ValueError:
            same_origin = False
        if not same_origin:
            raise ValueError("packaged worker URL must use its private endpoint")
    direct = path_or_url if parsed.scheme in {"http", "https"} else (
        f"http://{studio['host']}:{studio['port']}/{path}")
    return direct, studio_headers(studio)


def _remote_machines(registry: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for s in registry:
        if not is_local_studio(s):
            out.setdefault(s["machine"], []).append(s)
    return out


# Guard so overlapping refreshes never pile up (the poll loop fires this every
# few seconds; a slow/offline fleet must not stack N in-flight refreshes).
_inflight = {"v": False}


def _checkin_fallback(studio: dict, snapshot: dict) -> dict:
    """A dial that failed is not the last word when the Mac is calling home itself.

    Sharing a Tailscale node works one way, so a Shops Mac can reach its controller and the
    controller can never reach it. Its own check-in carries the same `/api/hub/resources` answer
    the poll was dialling for, so the poll uses that instead of filing the Mac as unreachable —
    which is what kept it out of the scheduler while it was up and idle.
    """
    node_id = studio.get("fleet_node")
    if not node_id:
        return snapshot
    from . import fleet_nodes
    pulled = fleet_nodes.checkin_snapshot(str(node_id))
    return pulled if pulled is not None else snapshot


async def refresh(registry: list[dict], client: httpx.AsyncClient):
    """Refresh each remote machine's host+studio stats from its peer Hub.
    TTL-guarded so it only actually hits the network once a snapshot is PEER_REFRESH_S old. Safe to
    fire-and-forget: an in-flight guard prevents overlap, and per-peer errors
    are swallowed so one slow machine can't break the sweep."""
    if _inflight["v"]:
        return
    machines = _remote_machines(registry)
    now = time.time()
    stale = [m for m, studios in machines.items()
             if now - _cache.get(m, (0, None))[0] >= PEER_REFRESH_S]
    if not stale:
        return
    _inflight["v"] = True
    try:
        await _refresh_stale(machines, stale, client, now)
    finally:
        _inflight["v"] = False


async def _refresh_stale(machines, stale, client, now):

    def store(machine: str, snapshot: dict, entry, auth_generation: int,
              *, clears_rejection: bool = False) -> bool:
        # A 401 proxy quarantine or fleet-token change must win over a resource
        # request that was already in flight.
        if auth_generation != _auth_generation or _cache.get(machine) is not entry:
            return False
        if (entry and entry[1].get(_DISPATCH_REJECTED)
                and not clears_rejection):
            snapshot[_DISPATCH_REJECTED] = True
            snapshot[_DISPATCH_MODALITY] = entry[1].get(_DISPATCH_MODALITY)
        _cache[machine] = (now, snapshot)
        return True

    def note_reachability(machine: str, snapshot: dict) -> None:
        state = _peer_alert_state.setdefault(
            machine, {"failures": 0, "alerted": False},
        )
        if snapshot.get("reachable") and snapshot.get("auth", True):
            if state["alerted"]:
                from . import alerts
                alerts.emit(
                    "agent_recovered",
                    f"Agent Hub on {machine} is reachable again",
                    {"machine": machine, "status": snapshot.get("status")},
                )
            state.update(failures=0, alerted=False)
            return
        state["failures"] += 1
        if state["failures"] < PEER_FAILURES_TO_ALERT or state["alerted"]:
            return
        state["alerted"] = True
        from . import alerts
        alerts.emit(
            "agent_unreachable",
            f"Agent Hub on {machine} is unreachable",
            {
                "machine": machine,
                "status": snapshot.get("status"),
                "consecutive_failures": state["failures"],
            },
        )

    async def one(machine: str, studios: list[dict]):
        entry = _cache.get(machine)
        s0 = studios[0]
        url = _peer_url(s0)
        token = _peer_token(s0)
        auth_generation = _auth_generation
        headers = {"X-Hub-Token": token} if token else {}
        # **A Mac the controller's dial is known to fail is not dialled again here.** The poll runs
        # every few seconds with a five-second budget, and a Shops Mac answers none of it: the
        # roster already holds the answer the Mac sent on its own call home, so the poll takes that
        # rather than stalling the whole sweep on a dial that cannot work. The moment a dial
        # succeeds the roster says so and this dials again.
        if s0.get("fleet_node"):
            from . import fleet_nodes
            node_id = str(s0["fleet_node"])
            if fleet_nodes.dial_is_failing(fleet_nodes._read().get(node_id)):
                pulled = fleet_nodes.checkin_snapshot(node_id)
                if pulled is not None:
                    if store(machine, pulled, entry, auth_generation):
                        note_reachability(machine, pulled)
                    return
        # A node that missed a rotation is asked with the older token it still holds.
        on_older = bool(s0.get("fleet_node") and held_token(s0["fleet_node"]) == token)
        try:
            r = await client.get(f"{url}/api/hub/resources?local_only=true",
                                  headers=headers, timeout=PEER_TIMEOUT_S)
            if r.status_code in (401, 403) and on_older:
                # It may already hold the current token (the push reached it, the answer did not).
                current = current_fleet_token()
                retry = await client.get(f"{url}/api/hub/resources?local_only=true",
                                         headers={"X-Hub-Token": current} if current else {},
                                         timeout=PEER_TIMEOUT_S)
                if retry.status_code == 200:
                    confirm_node(s0["fleet_node"])
                    r, token, on_older = retry, current, False
                    headers = {"X-Hub-Token": current}
            if r.status_code in (401, 403):
                # Hub is reachable but rejected the token → clearest possible signal
                # that the fleet tokens don't match on that machine.
                snapshot = {"host": None, "studios": {},
                            "reachable": True, "auth": False,
                            "status": ("no_token" if not token
                                       else "token_rejected")}
                if r.status_code == 401 and token:
                    foreign = await _foreign_site(client, url)
                    if foreign:
                        snapshot.update(status="foreign_site", **foreign)
                if store(machine, snapshot, entry, auth_generation):
                    note_reachability(machine, snapshot)
                return
            data = r.json()
            if (r.status_code != 200 or not isinstance(data, dict)
                    or not isinstance(data.get("host"), dict)
                    or not isinstance(data.get("studios"), dict)):
                snapshot = {"host": None, "studios": {},
                            "reachable": True, "auth": False,
                            "status": "resource_error"}
                if store(machine, snapshot, entry, auth_generation):
                    note_reachability(machine, snapshot)
                return
            order = (
                registry.normalize_priority_order(data.get("priority_order"))
                if "priority_order" in data
                else (["chat"] if data.get("chat_priority") is True else [])
            )
            snapshot = {
                "host": data.get("host"),
                "studios": data.get("studios", {}),
                "proxy": data.get("proxy"),
                "version": data.get("version"),
                # What starts when that Mac starts, as it reported it. Absent from an older Hub.
                "startup": data.get("startup") if isinstance(data.get("startup"), dict) else None,
                # The job that Mac is on right now, as it reported it. Absent from an older Hub.
                "current_job": data.get("current_job") if isinstance(data.get("current_job"), dict) else None,
                # Why that Mac's Hub last stopped, as it recorded it. Absent from an older Hub.
                "last_stop": data.get("last_stop") if isinstance(data.get("last_stop"), dict) else None,
                # Who that Mac says it is and what it is called now (`{id, name}`). Absent from an older Hub.
                "machine": data.get("machine") if isinstance(data.get("machine"), dict) else None,
                # Its workstation mode, settings and state (`components/hub/workstation.py`). Absent from
                # an older Hub, which is then never standing by.
                "workstation": data.get("workstation") if isinstance(data.get("workstation"), dict) else None,
                "priority_order": order,
                "chat_priority": bool(order and order[0] == "chat"),
                # **The work order's on/off switches, as that Mac saved them** (owner, 2026-10-28).
                # A controller dispatches to a joined Mac's Studios, so it must know a kind that
                # Mac switched off or it would keep sending that kind there. Absent from an older
                # Hub, which then reads as all-on — exactly what that build does.
                "function_enabled": registry.normalize_function_enabled(
                    data.get("function_enabled")),
                # The Mac's own idle-hold preference and its bounded remaining window
                # (the node owns the clock; only the remaining seconds cross the wire).
                # Absent (or None) from an older Hub, which then reads as OFF.
                "preferred_work": data.get("preferred_work") if isinstance(data.get("preferred_work"), dict) else None,
                "preferred_hold_remaining_s": (
                    float(data["preferred_hold_remaining_s"])
                    if isinstance(data.get("preferred_hold_remaining_s"), (int, float))
                    and not isinstance(data.get("preferred_hold_remaining_s"), bool)
                    else 0.0),
                "reachable": True, "auth": True, "status": "connected",
                # **Whether this answer came through the Mac's own call home.** A Shops Mac cannot
                # be dialled at all, so the poll reached it through the pull channel; the roster
                # keeps the reason distinct so an operator can tell a dialled Mac from one that
                # called in, and so the dial is not attempted again with a job's timeout.
                "pulled": bool(getattr(r, "extensions", {}).get("studiohub_pull")),
            }
            clears_rejection = False
            if entry and entry[1].get(_DISPATCH_REJECTED):
                modality = entry[1].get(_DISPATCH_MODALITY) or s0["modality"]
                probe = await client.get(
                    f"{url}/studio/{modality}/api/catalog",
                    headers=headers, timeout=PEER_TIMEOUT_S,
                )
                try:
                    clears_rejection = (
                        probe.status_code == 200 and isinstance(probe.json(), dict)
                    )
                except (AttributeError, ValueError):
                    clears_rejection = False
            if store(machine, snapshot, entry, auth_generation,
                     clears_rejection=clears_rejection):
                note_reachability(machine, snapshot)
            if on_older:
                # It is back and answering to the older token: hand it the current one.
                await push_current_token(s0, client)
        except httpx.ConnectError:
            # TCP refused: nothing is listening on :47873 there — the Studio Hub
            # isn't actually running on that machine (even if its studios are).
            snapshot = _checkin_fallback(s0, {"host": None, "studios": {},
                                              "reachable": False, "auth": True,
                                              "status": "no_hub"})
            if store(machine, snapshot, entry, auth_generation):
                note_reachability(machine, snapshot)
        except (httpx.TimeoutException, httpx.ConnectTimeout):
            # Packets dropped: a firewall is blocking :47873, or the Mac is asleep/off.
            snapshot = _checkin_fallback(s0, {"host": None, "studios": {},
                                              "reachable": False, "auth": True,
                                              "status": "unreachable"})
            if store(machine, snapshot, entry, auth_generation):
                note_reachability(machine, snapshot)
        except Exception:
            snapshot = _checkin_fallback(s0, {"host": None, "studios": {},
                                              "reachable": False, "auth": True,
                                              "status": "unreachable"})
            if store(machine, snapshot, entry, auth_generation):
                note_reachability(machine, snapshot)

    await asyncio.gather(*(one(m, machines[m]) for m in stale))


async def _foreign_site(client, url: str) -> dict | None:
    """A Hub that rejects our fleet token but reports a different site_id was
    enrolled at another site — the Mac was moved and never removed here.
    Enrollment never tells the previous controller, so this is the only place
    it can be noticed. /api/health is unauthenticated; the extra GET happens
    only after a token rejection."""
    from . import control_plane
    try:
        r = await client.get(f"{url}/api/health", timeout=PEER_TIMEOUT_S)
        plane = (r.json() or {}).get("control_plane") or {}
    except Exception:
        return None
    theirs = str(plane.get("site_id") or "").strip()
    ours = str(control_plane.load_settings().get("site_id") or "").strip()
    if not theirs or theirs == ours:
        return None
    return {"site_id": theirs, "controller_id": str(plane.get("controller_id") or "")}


def cached(machine: str) -> dict | None:
    entry = _cache.get(machine)
    return entry[1] if entry and not entry[1].get(_DISPATCH_REJECTED) else None


#: Workstation states in which a Mac takes no new work: someone is using it, it just handed a job
#: back, or it is in its quiet hours. A busy Mac for routing - not offline, not failed.
STANDING_BY_STATES = frozenset({"standing_by", "yielded", "quiet_hours"})


def _workstation(machine: str) -> dict:
    """That Mac's Lite Mode as it reported it - or, for this Mac (``"local"``), read live from its own
    settings, so a controller or standalone treats its own Mac like any other Lite Mode Mac."""
    if machine == "local":
        from components.hub import workstation
        return workstation.status()
    snapshot = cached(machine) or {}
    value = snapshot.get("workstation")
    return value if isinstance(value, dict) else {}


def standing_by(machine: str) -> bool:
    """Whether that Mac's workstation mode says it takes no new work right now."""
    return _workstation(machine).get("state") in STANDING_BY_STATES


def lite_mode_allows(studio: dict, model: str | None) -> bool:
    """False only for this Mac's own studio when its Lite Mode is on and ``model`` is not a Lite
    model. Another Mac answers that at its own door (``WORKSTATION_MODEL_NOT_ALLOWED``)."""
    if not model or not is_local_studio(studio):
        return True
    from components import workstation_models
    from components.hub import workstation
    return not workstation.load()["enabled"] or workstation_models.is_workstation_model(model)


def lite_mode_wait(model: str | None) -> str | None:
    """Why this Mac's own studios take no new work right now in Lite Mode, or None - the plain
    "waiting" sentence a queue shows when nothing else can take its work."""
    from components.hub import workstation
    refusal = workstation.admission_refusal(model or "")
    return None if refusal is None else f"Waiting: Lite Mode - {refusal['detail']}"


def workstation_start_free_gb(machine: str) -> float | None:
    """That Mac's own "free memory needed to start a job", when its owner set one and Lite Mode is on."""
    mode = _workstation(machine)
    value = mode.get("start_free_gb") if mode.get("enabled") else None
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def fresh_snapshot(machine: str) -> dict | None:
    """The cached peer snapshot for ``machine``, but only while it is still current.

    A caller that gets a snapshot back is looking at the same evidence
    :func:`dispatch_ready` would accept, so a roster read and a dispatch decision cannot
    disagree about whether a machine is usable. ``None`` means "ask the machine directly":
    nothing has been read yet, the last read is older than ``PEER_TTL_S``, or the entry is a
    proxy-rejection tombstone rather than a snapshot.
    """
    entry = _cache.get(machine)
    if not entry:
        return None
    try:
        observed_at, snapshot = entry
        age = time.time() - observed_at
    except (TypeError, ValueError):
        return None
    if not isinstance(snapshot, dict) or snapshot.get(_DISPATCH_REJECTED):
        return None
    if not 0 <= age < PEER_TTL_S:
        return None
    return snapshot


def reports_zero_running(machine: str) -> bool:
    """Whether this peer's current live snapshot says it has no running work.

    The refusal text can only claim the contradiction when the peer has actually reported
    idleness; a missing or stale snapshot proves nothing and must not clear a lease.
    """
    snapshot = fresh_snapshot(machine)
    if snapshot is None or snapshot.get("current_job") is not None:
        return False
    studios = snapshot.get("studios")
    if isinstance(studios, dict) and any(
            isinstance(row, dict) and (row.get("running") or row.get("state") == "running")
            for row in studios.values()):
        return False
    return True


def dispatch_ready(studio: dict, generation=None) -> bool:
    """Whether a Studio has authority to receive newly dispatched work."""
    if is_local_studio(studio):
        return not standing_by("local")   # this Mac's own Lite Mode, whatever its role
    entry = _cache.get(studio.get("machine"))
    if not entry:
        return False
    if generation is not None and generation != (_auth_generation, id(entry)):
        return False
    try:
        observed_at, snapshot = entry
        age = time.time() - observed_at
    except (TypeError, ValueError):
        return False
    return (
        isinstance(snapshot, dict)
        and not snapshot.get(_DISPATCH_REJECTED)
        and 0 <= age < PEER_TTL_S
        and snapshot.get("reachable") is True
        and snapshot.get("auth") is True
        and snapshot.get("status") == "connected"
        and (snapshot.get("workstation") or {}).get("state") not in STANDING_BY_STATES
    )


def dispatch_generation(studio: dict):
    """Capture the authority checked immediately before a scheduler commits work."""
    if is_local_studio(studio):
        return None
    entry = _cache.get(studio.get("machine"))
    if not dispatch_ready(studio) or not entry:
        return None
    return _auth_generation, id(entry)


def invalidate(machine: str, *, rejected: bool = False,
               modality: str | None = None) -> None:
    """Forget a peer snapshot, retaining a proxy-auth quarantine when rejected."""
    if rejected:
        _cache[machine] = (0, {_DISPATCH_REJECTED: True,
                               _DISPATCH_MODALITY: modality})
        return
    entry = _cache.get(machine)
    if entry and entry[1].get(_DISPATCH_REJECTED):
        # Force a resource refresh without erasing a worker-auth quarantine.
        _cache[machine] = (0, entry[1])
        return
    _cache.pop(machine, None)


def forget_machine(machine: str) -> None:
    """Drop a removed machine's peer-resource snapshot immediately."""
    _cache.pop(machine, None)
    _peer_alert_state.pop(machine, None)


async def control_remote(client: httpx.AsyncClient, studio: dict, action: str) -> dict:
    """Proxy a start/stop to the studio's own machine's Hub, which runs pterm
    locally there. The peer addresses the studio by its local id = modality."""
    url = _peer_url(studio)
    token = _peer_token(studio)
    if not token:
        return {"ok": False, "error": "no fleet token set — set one on this Hub "
                "and the same on the remote Hub to control remote studios"}
    headers = {"X-Hub-Token": token}
    local_id = studio.get("modality")
    try:
        r = await client.post(f"{url}/api/hub/studios/{local_id}/{action}",
                              headers=headers, timeout=10.0)
        if r.status_code == 401:
            return {"ok": False, "error": "remote Hub rejected the fleet token "
                    "(set the SAME fleet token on that machine's Hub)"}
        if r.status_code == 404:
            return {"ok": False, "error": f"remote Hub has no '{local_id}' studio "
                    "(is a Studio Hub running on that machine?)"}
        return {"ok": r.status_code < 400, "status": r.status_code,
                "remote": r.json() if r.headers.get("content-type", "").startswith("application/json") else None}
    except httpx.HTTPError as e:
        return {"ok": False, "error": f"can't reach the Hub on {studio['host']} "
                f"— run Studio Hub on that Mac ({e})"}


async def startup_services_status(
    registry: list[dict], client: httpx.AsyncClient,
) -> dict[str, dict]:
    """Read each machine's local startup-service audit through its peer Hub."""
    machines = _remote_machines(registry)

    async def one(machine: str, studios: list[dict]) -> tuple[str, dict]:
        studio = studios[0]
        token = _peer_token(studio)
        headers = {"X-Hub-Token": token} if token else {}
        try:
            response = await client.get(
                f"{_peer_url(studio)}/api/hub/startup-services?local_only=true",
                headers=headers, timeout=PEER_TIMEOUT_S,
            )
            if response.status_code in {401, 403}:
                return machine, {
                    "machine": machine, "reachable": True, "supported": False,
                    "services": [], "detail": "Peer rejected the fleet credential",
                }
            if response.status_code == 404:
                return machine, {
                    "machine": machine, "reachable": True, "supported": False,
                    "services": [], "detail": "Update this machine's Studio Hub to add startup control",
                }
            if response.status_code >= 400:
                return machine, {
                    "machine": machine, "reachable": True, "supported": False,
                    "services": [], "detail": f"Peer returned HTTP {response.status_code}",
                }
            payload = response.json()
            return machine, {
                **payload, "machine": machine, "reachable": True,
                "supported": payload.get("supported", True),
            }
        except (httpx.HTTPError, ValueError) as exc:
            return machine, {
                "machine": machine, "reachable": False, "supported": False,
                "services": [],
                "detail": (str(exc).strip() or type(exc).__name__)[:180],
            }

    rows = await asyncio.gather(*(one(machine, studios)
                                  for machine, studios in machines.items()))
    return dict(rows)


async def install_remote_startup_service(
    client: httpx.AsyncClient, studio: dict, modality: str,
) -> dict:
    """Ask one peer Hub to install one sibling's service on its own Mac."""
    token = _peer_token(studio)
    if not token:
        return {"ok": False, "error": "no fleet token set"}
    try:
        response = await client.post(
            f"{_peer_url(studio)}/api/hub/startup-services/local/{modality}/install",
            headers={"X-Hub-Token": token}, timeout=260.0,
        )
        payload = (response.json()
                   if response.headers.get("content-type", "").startswith("application/json")
                   else {})
        if response.status_code in {401, 403}:
            return {"ok": False, "error": "remote Hub rejected the fleet credential"}
        if response.status_code == 404:
            return {"ok": False, "error": "update the remote Studio Hub before installing startup services"}
        if response.status_code >= 400:
            return {"ok": False, "error": str(payload.get("detail") or
                                                f"remote Hub returned HTTP {response.status_code}")}
        return payload
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"can't reach the remote Hub ({exc})"}


async def retire_remote_startup_service(
    client: httpx.AsyncClient, studio: dict, modality: str,
) -> dict:
    """Ask one peer Hub to retire one legacy sibling on its own Mac."""
    # Strict fleet-service routes accept only the controller's current fleet
    # credential. A legacy per-row Hub token may still authenticate ordinary
    # proxy traffic, but must never override this exact service contract.
    token = current_fleet_token()
    if not token:
        return {"ok": False, "error": "no fleet token set"}
    try:
        response = await client.post(
            f"{_peer_url(studio)}/api/hub/service/startup-services/local/{modality}/retire",
            headers={"X-Hub-Token": token}, timeout=260.0,
        )
        payload = (response.json()
                   if response.headers.get("content-type", "").startswith("application/json")
                   else {})
        if response.status_code in {401, 403}:
            return {"ok": False, "error": "remote Hub rejected the fleet credential"}
        if response.status_code == 404:
            return {"ok": False, "error": "update the remote Studio Hub before retiring startup services"}
        if response.status_code >= 400:
            return {"ok": False, "error": str(payload.get("detail") or
                                                f"remote Hub returned HTTP {response.status_code}")}
        return payload
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"can't reach the remote Hub ({exc})"}


async def remove_remote_studio(
    client: httpx.AsyncClient, studio: dict, modality: str,
) -> dict:
    """Ask one peer Hub to fully remove an unused sibling on its own Mac."""
    token = current_fleet_token()
    if not token:
        return {"ok": False, "error": "no fleet token set"}
    try:
        response = await client.post(
            f"{_peer_url(studio)}/api/hub/service/startup-services/local/{modality}/remove",
            headers={"X-Hub-Token": token}, timeout=260.0,
        )
        payload = (response.json()
                   if response.headers.get("content-type", "").startswith("application/json")
                   else {})
        if response.status_code in {401, 403}:
            return {"ok": False, "error": "remote Hub rejected the fleet credential"}
        if response.status_code == 404:
            return {"ok": False, "error": "update the remote Studio Hub before removing Studios"}
        if response.status_code >= 400:
            return {"ok": False, "error": str(payload.get("detail") or
                                                f"remote Hub returned HTTP {response.status_code}")}
        return payload
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"can't reach the remote Hub ({exc})"}


async def install_remote_generation(
    client: httpx.AsyncClient, studio: dict,
) -> dict:
    """Ask a peer Hub to reinstall one Studio's generation environment.

    The peer Hub remains the only process that executes its local Pinokio
    installer.  The response is a durable peer job reference; callers poll it
    rather than assuming that a request accepted by the peer means success.
    """
    token = _peer_token(studio)
    if not token:
        return {"ok": False, "error": "no fleet token set"}
    try:
        response = await client.post(
            f"{_peer_url(studio)}/api/hub/maintenance/generation-installs",
            headers={"X-Hub-Token": token},
            json={"studio_ids": [studio.get("modality")], "local_only": True},
            timeout=30.0,
        )
        payload = (response.json()
                   if response.headers.get("content-type", "").startswith("application/json")
                   else {})
        if response.status_code in {401, 403}:
            return {"ok": False, "error": "remote Hub rejected the fleet credential"}
        if response.status_code == 404:
            return {"ok": False, "error": "update the remote Studio Hub before installing generation"}
        if response.status_code >= 400:
            return {"ok": False, "error": str(payload.get("detail") or
                                                f"remote Hub returned HTTP {response.status_code}")}
        return {"ok": True, "job": payload}
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"can't reach the remote Hub ({exc})"}


async def start_remote_studio_update_repair(
    client: httpx.AsyncClient, studio: dict,
) -> dict:
    """Start one fixed local Studio repair on the Studio's Agent Hub."""
    token = _peer_token(studio)
    if not token:
        return {"ok": False, "retryable": False, "error": "no fleet token set"}
    headers = {"X-Hub-Token": token}
    try:
        capability = await client.get(
            f"{_peer_url(studio)}/api/version", headers=headers, timeout=15.0,
        )
        payload = (capability.json()
                   if capability.headers.get("content-type", "").startswith("application/json")
                   else {})
        schema = payload.get("studio_update_repair_schema") if isinstance(payload, dict) else None
        if capability.status_code in {401, 403}:
            return {"ok": False, "retryable": False,
                    "error": "remote Hub rejected the fleet credential"}
        if capability.status_code >= 400 or type(schema) is not int or schema != 1:
            return {"ok": False, "retryable": True,
                    "error": "update the Agent Hub before repairing Studio updates remotely"}
        response = await client.post(
            f"{_peer_url(studio)}/api/hub/maintenance/studio-update-repairs",
            headers=headers,
            json={"studio_ids": [studio.get("modality")], "local_only": True},
            timeout=30.0,
        )
        result = (response.json()
                  if response.headers.get("content-type", "").startswith("application/json")
                  else {})
        if response.status_code in {401, 403}:
            return {"ok": False, "retryable": False,
                    "error": "remote Hub rejected the fleet credential"}
        if response.status_code == 404:
            return {"ok": False, "retryable": True,
                    "error": "update the Agent Hub before repairing Studio updates remotely"}
        if response.status_code >= 400:
            return {"ok": False, "retryable": response.status_code >= 500,
                    "error": str(result.get("detail") or
                                 f"remote Hub returned HTTP {response.status_code}")}
        return {"ok": True, "job": result}
    except httpx.HTTPError as exc:
        return {"ok": False, "retryable": True,
                "error": f"can't reach the remote Hub ({exc})"}


async def push_current_token(studio: dict, client: httpx.AsyncClient) -> bool:
    """Give a node that missed a rotation the current Fleet token, authenticated with its old one.

    The write is the one a rotation makes. The node saves it before it answers, so an answer below
    400 means it holds the new token and the older one is no longer needed for it.
    """
    node = studio.get("fleet_node")
    held, current = held_token(node) if node else None, current_fleet_token()
    if not held or not current:
        return False
    try:
        response = await client.post(f"{_peer_url(studio)}/api/hub/fleet",
                                     headers={"X-Hub-Token": held},
                                     json={"token": current, "sync": False}, timeout=PEER_TIMEOUT_S)
    except httpx.HTTPError:
        return False
    if response.status_code >= 400:
        return False
    confirm_node(node)
    return True


async def sync_owner_password(
    registry: list[dict], client: httpx.AsyncClient, verifier: dict,
) -> dict:
    """Offer this controller's owner-password verifier to every connected Agent.

    Reuses the fleet transport that already rotates the fleet credential: each
    write is authenticated with the current fleet token and the Agent decides
    whether to take it. An Agent with its own chosen password refuses, and an
    unreachable Agent simply keeps what it has.
    """
    token = fleet_token()
    machines = _remote_machines(registry)
    headers = {"X-Hub-Token": token} if token else {}
    body = {"schema_version": 1, "verifier": verifier}

    async def one(machine: str, studios: list[dict]) -> tuple[str, dict]:
        url = _peer_url(studios[0])
        try:
            response = await client.post(
                f"{url}/api/hub/fleet/owner-password", headers=headers,
                json=body, timeout=PEER_TIMEOUT_S)
        except httpx.HTTPError as exc:
            return machine, {"ok": False, "status": "unreachable",
                             "detail": (str(exc).strip() or type(exc).__name__)[:180]}
        if response.status_code in {401, 403}:
            return machine, {"ok": False, "status": "refused",
                             "detail": "peer rejected this controller's credential"}
        if response.status_code >= 400:
            return machine, {"ok": False, "status": "failed",
                             "detail": f"peer returned HTTP {response.status_code}"}
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        installed = bool(isinstance(payload, dict) and payload.get("installed"))
        return machine, {
            "ok": installed,
            "status": "installed" if installed else "kept_own_password",
            "detail": ("owner password inherited from this controller" if installed
                       else "peer keeps the password its owner chose"),
        }

    rows = await asyncio.gather(*(one(machine, studios)
                                  for machine, studios in machines.items()))
    results = dict(rows)
    installed = sum(row["ok"] for row in results.values())
    return {
        "total": len(results), "installed": installed,
        "unchanged": len(results) - installed, "machines": results,
    }


async def sync_fleet_token(
    registry: list[dict], client: httpx.AsyncClient, new_token: str,
    *, local_commit: Callable[[str], None] = set_fleet_token,
    controller_url: str | None = None,
) -> dict:
    """Rotate connected peer Hubs from the current credential to ``new_token``.

    Each write is authenticated with the old token, then verified with the new
    one. The local token changes last so a partial failure remains recoverable
    and can be reported as a one-time manual save on that machine.

    **`controller_url` is the same call doing the other half of its job.** A4 rotates the
    credential in place; A3's handover moves every node to a *different* controller at the same
    time, and both are "set this Mac's group membership". One route, one push, two callers — so a
    handover passes a `local_commit` that does nothing (the retiring controller keeps its own
    credential; it is not joining anyone) and a `controller_url` naming the new head.
    """
    current_token = fleet_token()
    machines = _remote_machines(registry)
    # What each node holds right now: the current token, or the older one it is still waiting to
    # be moved off. Filled by `one` so the rotation can record who did not get the new one.
    holding: dict[str, str | None] = {}
    saved: set[str] = set()

    async def one(machine: str, studios: list[dict]) -> tuple[str, dict]:
        studio = studios[0]
        url = _peer_url(studio)
        old_token = held_token(machine) or current_token
        holding[machine] = old_token
        headers = {"X-Hub-Token": old_token} if old_token else {}
        body: dict[str, object] = {"token": new_token, "sync": False}
        if controller_url:
            body["controller_url"] = controller_url
            # **Where this Mac answers, so it can introduce itself to its new head** (A3 step 3).
            # The old controller is the authority on that: it has been probing this machine at
            # exactly this address, and a node cannot reliably derive its own — which interface
            # (Tailscale or LAN) it is reachable on is the *app's* decision at join time, and the
            # Hub must not guess it. Without this the cluster follows the credential but the new
            # controller's roster stays empty, so the site's capacity collapses to one Mac: a
            # rehearsal measured exactly that (`verified: 1`, new head `total: 0`).
            body["self_address"] = url
        try:
            response = await client.post(
                f"{url}/api/hub/fleet", headers=headers,
                json=body, timeout=PEER_TIMEOUT_S)
            if response.status_code in {401, 403}:
                return machine, {"ok": False, "status": "manual",
                                 "detail": "peer rejected the current credential"}
            if response.status_code >= 400:
                return machine, {"ok": False, "status": "failed",
                                 "detail": f"peer returned HTTP {response.status_code}"}
            saved.add(machine)
            verified = await client.get(
                f"{url}/api/hub/resources?local_only=true",
                headers={"X-Hub-Token": new_token}, timeout=PEER_TIMEOUT_S)
            if verified.status_code == 200:
                return machine, {"ok": True, "status": "verified",
                                 "detail": "saved and verified"}
            return machine, {"ok": False, "status": "failed",
                             "detail": f"saved but verification returned HTTP {verified.status_code}"}
        except httpx.HTTPError as exc:
            return machine, {"ok": False, "status": "unreachable",
                             "detail": (str(exc).strip() or type(exc).__name__)[:180]}

    rows = await asyncio.gather(*(one(machine, studios)
                                  for machine, studios in machines.items()))
    results = dict(rows)
    if not controller_url:
        # **A node that did not get the new token keeps the one it has until it does.** Without
        # this the controller switched over anyway and a Mac that was off at that moment was
        # stranded: it answered only to a token nobody presented any more. A node that refused
        # the old token ("manual") holds something else entirely, so there is nothing to keep.
        waiting = {machine: token for machine, token in holding.items()
                   if token and machine not in saved and results[machine]["status"] != "manual"}
        note_nodes_on_previous_token(waiting, confirmed=tuple(saved))
    local_commit(new_token)
    _cache.clear()
    verified = sum(row["ok"] for row in results.values())
    manual = sum(row["status"] == "manual" for row in results.values())
    return {
        "total": len(results), "verified": verified,
        "manual": manual, "pending": len(results) - verified - manual,
        "machines": results,
    }


#: How long a controller waits for one node to answer a repoint. Longer than the 5 s peer call
#: because the node itself dials the destination before it answers (its own probe budget is 6 s), and
#: shorter than a job's timeout because nothing here is generation work.
REPOINT_TIMEOUT_S = 30.0


async def sync_repoint(registry: list[dict], client: httpx.AsyncClient, destination: str,
                       migration: str, expected: str) -> dict:
    """Push an **address-only repoint** to every node this controller can reach.

    The delivery path is the one a rotation already uses — dial the Mac, and let the pull transport
    hand the same request to a Mac that cannot be dialled, through its own check-in — so an
    unreachable node is *queued*, not forgotten. `fleet_pull`'s transport is installed on the client
    the caller passes, which is what makes the second half true.

    **Nothing is retired and no token is rotated.** This is not a handover: the old controller keeps
    its credential, its roster and its work, and the nodes move their parent address and nothing
    else. That is what lets a controller keep generating alongside the one that took its nodes, and
    what keeps every node's own identity intact.

    `expected` is the address the caller believes these Macs are on — the fence, and **the caller's
    to state**, exactly as `POST /api/hub/fleet` takes the new head's address from its caller rather
    than deriving it. This controller cannot derive it: which of its own addresses a node recorded
    at join (Tailscale, LAN, loopback) is the *app's* decision, and the route that would tell it —
    `GET /api/hub/controller` — is deliberately outside the Fleet credential's scope, because it
    carries this Mac's settings. A wrong fence is safe: the node compares it with its own membership
    and refuses, which is the whole point of it.

    Per-node status is one of:

    * `repointed` — the Mac moved and its own answer says so. A 200 from a direct dial is the Mac
      itself saying it wrote the destination (it writes before it answers, and in the same process),
      which is the strongest evidence this side can get; there is no second read that could prove
      more, for the reason above;
    * `already_there` — the Mac was on the destination before this push, and wrote nothing;
    * `pending` — the request was queued or the Mac did not answer. **Never read as moved**: the Mac
      still holds its old address, the same operation can be pushed again, and that is what the
      owner's fallback (or the next push) is for;
    * `failed` — the Mac refused, and `detail` is the node's own sentence saying why.
    """
    machines = _remote_machines(registry)
    results: dict[str, dict] = {}

    async def one(machine: str, studios: list[dict]) -> tuple[str, dict]:
        studio = studios[0]
        url = _peer_url(studio)
        token = held_token(machine) or fleet_token()
        headers = {"X-Hub-Token": token} if token else {}
        operation = f"{migration}:{machine}"
        try:
            answer = await client.post(f"{url}/api/hub/fleet/repoint", headers=headers,
                                       json={"operation_id": operation,
                                             "expected_controller_url": expected,
                                             "controller_url": destination},
                                       timeout=REPOINT_TIMEOUT_S)
        except httpx.HTTPError as exc:
            return machine, {"ok": False, "status": "pending", "from_url": expected,
                             "to_url": destination, "operation_id": operation,
                             "detail": ("the request was held for that Mac or it did not answer "
                                        f"({str(exc).strip() or type(exc).__name__}); it keeps "
                                        f"the controller it has and the same operation can be "
                                        f"pushed again")}
        body: dict = {}
        try:
            body = answer.json() or {}
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        if answer.status_code == 202 or body.get("queued"):
            return machine, {"ok": False, "status": "pending", "from_url": expected,
                             "to_url": destination, "operation_id": operation,
                             "detail": "the request is waiting for that Mac's next check-in"}
        if answer.status_code in {401, 403}:
            return machine, {"ok": False, "status": "failed", "from_url": expected,
                             "to_url": destination, "operation_id": operation,
                             "detail": "that Mac refused this controller's credential, so it was "
                                       "not moved (it holds a different Fleet token)"}
        if not answer.is_success:
            return machine, {"ok": False, "status": "failed", "from_url": expected,
                             "to_url": destination, "operation_id": operation,
                             "code": body.get("code"),
                             "detail": str(body.get("detail") or
                                           f"that Mac answered HTTP {answer.status_code}")}
        if body.get("already_there"):
            return machine, {"ok": True, "status": "already_there", "from_url": expected,
                             "to_url": destination, "operation_id": operation,
                             "detail": f"already on the controller at {destination}"}
        moved_to = str(body.get("controller_url") or "").strip()
        if not body.get("repointed") or moved_to.rstrip("/") != destination.rstrip("/"):
            return machine, {"ok": False, "status": "pending", "from_url": expected,
                             "to_url": destination, "operation_id": operation,
                             "detail": ("that Mac accepted the move but does not report the new "
                                        f"controller (it says {moved_to or 'nothing'}); it will "
                                        f"be confirmed on its next check-in")}
        return machine, {"ok": True, "status": "repointed", "from_url": expected,
                         "to_url": destination, "operation_id": operation,
                         "detail": f"moved from {expected} to {destination}"}

    rows = await asyncio.gather(*(one(machine, studios)
                                  for machine, studios in machines.items()))
    results = dict(rows)
    counted = {status: sum(row["status"] == status for row in results.values())
               for status in ("repointed", "already_there", "pending", "failed")}
    # This controller's own view of each Mac is a cache keyed by address family, and a node does not
    # move until its next dispatch — but its *reported* facts are what the roster reads, so the peer
    # cache is dropped rather than left to its TTL.
    _cache.clear()
    return {"total": len(results), **counted, "machines": results}
