"""The pull channel: work a controller cannot dial still reaches its node.

**Why this exists.** Three Shops Macs reach their controller through Tailscale *node sharing*,
which works one way: the shared Mac can dial out, the sharer cannot dial back. Every Shops Mac
therefore joins and then never answers a single probe, so it reads `never_connected` for ever
while it is switched on and healthy. The controller's only evidence of life was a dial it could
not make.

**What this adds.** The node's own authenticated call home stops being read-only. It becomes the
channel: the node reports what it is, and the controller hands back the requests it would
otherwise have dialled. Dialling stays the first choice — `PullTransport` dials first and only
falls back when the dial fails — so nothing gets slower for a Mac the controller can reach.

**The command envelope is the request itself.** A queued command carries `method`, `path` and
the body the controller was going to send, and the node replays it against its own Hub with its
own Hub token. Nothing here has to understand what a command *means*, and a pull command can
never drift from the dialled call it replaces, because it is that call.

**Delivered once.** Every command has an id; the node acks the ids it has executed, and an acked
id is never re-issued. A command that is delivered twice is executed once.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import secrets
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from .registry import DATA_DIR

log = logging.getLogger("studiohub.fleet_pull")

FILE_NAME = "fleet_pull.json"
#: How long a queued command stays worth delivering. A node that is off for a day must not be
#: handed a Lite Mode change from yesterday as though it were the owner's latest word.
COMMAND_TTL_S = 900.0
#: How long a delivered result waits for the caller that is still holding the connection open.
RESULT_TTL_S = 900.0
#: The most commands held for one node, and the most results held per node. A node that never
#: comes back must not grow this file without limit.
MAX_COMMANDS = 64
MAX_RESULTS = 64
#: The largest body a command may carry. A worker dispatch with a reference image is the biggest
#: thing that travels here; a body beyond this is not something a node should be asked to replay.
MAX_BODY_BYTES = 8 * 1024 * 1024
#: A node is **check-in alive** for this long after its last check-in: three missed 60 s ticks.
CHECKIN_FRESH_S = 180.0

#: Headers the node must never be handed, and never hand back. The controller's own credential is
#: not the node's, the node recomputes the length, and hop-by-hop headers mean nothing on a replay.
_STRIPPED_REQUEST_HEADERS = frozenset({
    "host", "content-length", "x-hub-token", "authorization", "connection",
    "accept-encoding", "transfer-encoding", "keep-alive", "proxy-authorization",
})


def store_path() -> Path:
    return DATA_DIR / FILE_NAME


def _empty() -> dict[str, Any]:
    return {"version": 1, "nodes": {}}


def _document() -> dict[str, Any]:
    try:
        value = json.loads(store_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty()
    if not isinstance(value, dict) or not isinstance(value.get("nodes"), dict):
        return _empty()
    return {"version": 1, "nodes": {str(k): v for k, v in value["nodes"].items()
                                    if isinstance(v, dict)}}


def _write(document: dict[str, Any]) -> None:
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
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


def _row(document: dict[str, Any], node_id: str, *, create: bool = False) -> dict[str, Any]:
    node_id = str(node_id or "").strip().lower()
    row = document["nodes"].get(node_id)
    if row is None and create:
        row = {"commands": [], "results": []}
        document["nodes"][node_id] = row
    return row if isinstance(row, dict) else {"commands": [], "results": []}


# ── the queue ────────────────────────────────────────────────────────────────
def _live(commands: list[Any], now: float) -> list[dict[str, Any]]:
    """The commands still worth delivering: well formed and not past their expiry."""
    kept: list[dict[str, Any]] = []
    for row in commands:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        expires = row.get("expires_at")
        if isinstance(expires, (int, float)) and not isinstance(expires, bool) and expires <= now:
            continue
        kept.append(row)
    return kept


def queue(node_id: str, method: str, path: str, *,
          headers: dict[str, str] | None = None,
          content: bytes | None = None,
          content_type: str = "",
          kind: str = "relay",
          ttl_s: float = COMMAND_TTL_S) -> dict[str, Any]:
    """Hold one request for a node to replay. Returns the command, id included.

    The id is the whole of the no-double-execution rule: the node acks the ids it has run, and
    `take_commands` never hands an acked id back.
    """
    node_id = str(node_id or "").strip().lower()
    body = content or b""
    if len(body) > MAX_BODY_BYTES:
        raise ValueError(f"a queued command body may be at most {MAX_BODY_BYTES} bytes")
    now = time.time()
    command: dict[str, Any] = {
        "id": f"c_{secrets.token_hex(12)}",
        "kind": str(kind or "relay"),
        "method": str(method or "POST").upper(),
        "path": str(path or "/"),
        "headers": {key: value for key, value in (headers or {}).items()
                    if str(key).lower() not in _STRIPPED_REQUEST_HEADERS},
        "json": None,
        "content_b64": None,
        "content_type": str(content_type or ""),
        "created_at": now,
        "expires_at": now + max(0.0, float(ttl_s)),
    }
    document = _document()
    row = _row(document, node_id, create=True)
    commands = _live(row.get("commands") or [], now)
    if body:
        command["content_b64"] = base64.b64encode(body).decode("ascii")
    # Newest last: a node drains in the order the controller asked, so a Lite Mode change made
    # after an earlier one is the one that lands last.
    commands.append(command)
    # A backlog is a node that is not coming back. The newest survive, because the owner's latest
    # word is the one that matters - the same rule the Gateway's own pending queue follows.
    row["commands"] = commands[-MAX_COMMANDS:]
    _write(document)
    return command


def queue_json(node_id: str, method: str, path: str, payload: Any, **kwargs: Any) -> dict[str, Any]:
    """Queue a JSON request, rendered here so the node receives exactly the bytes intended."""
    body = b"" if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers = dict(kwargs.pop("headers", None) or {})
    if body:
        headers.setdefault("Content-Type", "application/json")
    return queue(node_id, method, path, headers=headers, content=body, **kwargs)


def take_commands(node_id: str, seen: list[str] | None = None) -> list[dict[str, Any]]:
    """The commands this node may still run, dropping the ones it has already acked or that expired."""
    node_id = str(node_id or "").strip().lower()
    now = time.time()
    acked = {str(value) for value in (seen or []) if value}
    document = _document()
    row = _row(document, node_id)
    commands = _live(row.get("commands") or [], now)
    before = len(row.get("commands") or [])
    remaining = [row_ for row_ in commands if str(row_["id"]) not in acked]
    if before != len(remaining):
        row["commands"] = remaining
        document["nodes"][node_id] = row
        _write(document)
    return remaining


def ack(node_id: str, ids: list[str] | None = None) -> int:
    """Forget the commands this node says it has run or refused. Returns how many went."""
    wanted = {str(value) for value in (ids or []) if value}
    if not wanted:
        return 0
    node_id = str(node_id or "").strip().lower()
    document = _document()
    row = _row(document, node_id)
    commands = row.get("commands") or []
    kept = [row_ for row_ in commands if not (isinstance(row_, dict) and str(row_.get("id")) in wanted)]
    gone = len(commands) - len(kept)
    if gone:
        row["commands"] = kept
        document["nodes"][node_id] = row
        _write(document)
    return gone


# ── results ─────────────────────────────────────────────────────────────────
def record_result(node_id: str, command_id: str, status: int, body: Any) -> None:
    """Store what the node's replay answered, and wake whoever is still waiting for it."""
    node_id = str(node_id or "").strip().lower()
    command_id = str(command_id or "")
    if not command_id:
        return
    now = time.time()
    document = _document()
    row = _row(document, node_id, create=True)
    results = [entry for entry in (row.get("results") or [])
               if isinstance(entry, dict) and entry.get("id") != command_id]
    results.append({"id": command_id, "status": int(status), "json": body, "at": now})
    row["results"] = results[-MAX_RESULTS:]
    # The command is done: it is never handed out again, whichever side noticed first.
    row["commands"] = [entry for entry in (row.get("commands") or [])
                       if not (isinstance(entry, dict) and str(entry.get("id")) == command_id)]
    _write(document)
    waiter = _WAITERS.get(command_id)
    if waiter is not None:
        waiter.set()


def result_of(node_id: str, command_id: str) -> dict[str, Any] | None:
    """What the node answered for one command, or `None` while it has not answered yet."""
    row = _document()["nodes"].get(str(node_id or "").strip().lower()) or {}
    for entry in row.get("results") or []:
        if isinstance(entry, dict) and str(entry.get("id")) == str(command_id):
            return entry
    return None


def clear_results(node_id: str) -> None:
    """Forget a node's delivered results: the node has taken them, so the caller was answered."""
    node_id = str(node_id or "").strip().lower()
    document = _document()
    row = document["nodes"].get(node_id)
    if not isinstance(row, dict) or not row.get("results"):
        return
    row["results"] = []
    _write(document)


#: The callers holding a connection open for a command's answer, by command id. Process-local and
#: deliberately so: the caller is the transport in this same process, and a result that arrives
#: with nobody waiting is read from the file instead.
_WAITERS: dict[str, asyncio.Event] = {}


async def await_result(node_id: str, command_id: str, timeout: float) -> dict[str, Any] | None:
    """Wait for the node's answer to one command, or `None` when it did not come in time."""
    existing = result_of(node_id, command_id)
    if existing is not None:
        return existing
    event = asyncio.Event()
    _WAITERS[command_id] = event
    try:
        try:
            await asyncio.wait_for(event.wait(), timeout=max(0.1, float(timeout)))
        except (asyncio.TimeoutError, TimeoutError):
            return result_of(node_id, command_id)
    finally:
        _WAITERS.pop(command_id, None)
    return result_of(node_id, command_id)


def forget(node_id: str) -> None:
    """Drop everything held for one node — it was removed, or it left."""
    node_id = str(node_id or "").strip().lower()
    document = _document()
    if document["nodes"].pop(node_id, None) is not None:
        _write(document)


def summary(node_id: str) -> dict[str, int]:
    """How much is waiting for one node, for the roster row and the operator's eye."""
    row = _document()["nodes"].get(str(node_id or "").strip().lower()) or {}
    return {"queued": len(_live(row.get("commands") or [], time.time())),
            "results": len(row.get("results") or [])}


# ── who may be pulled from ──────────────────────────────────────────────────
#: `host:port` -> `(node id, is the dial known to be failing)`, held for a couple of seconds.
#: The transport asks on **every** request the controller makes, and the roster is a file: reading
#: and parsing it per request would put a disk read on the worker health poll. Two seconds is short
#: enough that a node that just joined or just went away is noticed on the next tick.
_ORIGIN_CACHE: tuple[float, str, dict[tuple[str, int], tuple[str, bool]]] | None = None
_ORIGIN_TTL_S = 2.0


def _origin_index() -> dict[tuple[str, int], tuple[str, bool]]:
    global _ORIGIN_CACHE
    now = time.time()
    # Keyed by the store it was built from as well as by time: a test (or a data directory that
    # moved) must never read another roster's answers out of this cache.
    where = str(store_path())
    if _ORIGIN_CACHE is not None and _ORIGIN_CACHE[1] == where \
            and now - _ORIGIN_CACHE[0] < _ORIGIN_TTL_S:
        return _ORIGIN_CACHE[2]
    from . import fleet_nodes
    index: dict[tuple[str, int], tuple[str, bool]] = {}
    for node_id, row in fleet_nodes._read().items():
        try:
            parsed = urlsplit(str(row.get("address") or ""))
        except ValueError:
            continue
        if parsed.hostname and parsed.port:
            index[(parsed.hostname, parsed.port)] = (node_id, fleet_nodes.dial_is_failing(row))
    _ORIGIN_CACHE = (now, where, index)
    return index


def node_for_origin(origin: tuple[str, int] | None) -> tuple[str, bool] | None:
    """The joined node whose stored address is this host:port, and whether its dial is failing.

    The roster is the only list of machines a controller may queue work for, and the address in
    it is the one the node itself declared and `fleet_nodes.validate_address` checked. Nothing
    else may be reached this way: a pull channel that could be pointed anywhere would be a
    request forgery with a queue attached.
    """
    if origin is None:
        return None
    try:
        wanted = (str(origin[0]), int(origin[1]))
    except (TypeError, ValueError):
        return None
    return _origin_index().get(wanted)


def _request_timeout(request: httpx.Request) -> float:
    """How long the caller is willing to wait, from the timeout httpx put on the request.

    **Deliberately the caller's own timeout, with no floor of its own.** A job dispatch already
    says it will wait minutes; a peer poll says five seconds. Giving the poll a longer wait than
    it asked for would stall every poll cycle on a node that is switched off, which is exactly the
    fleet-wide stall `inventory` was fixed for. A caller that runs out of patience gets the dial's
    own failure, which is what it would have got before this channel existed.
    """
    timeout = request.extensions.get("timeout")
    read = getattr(timeout, "read", None)
    if isinstance(read, (int, float)) and not isinstance(read, bool) and read > 0:
        return min(float(read), 1800.0)
    values = [getattr(timeout, name, None) for name in ("read", "write", "connect", "pool")]
    total = max((float(value) for value in values
                 if isinstance(value, (int, float)) and not isinstance(value, bool) and value),
                default=0.0)
    return max(1.0, min(total, 1800.0))


async def pull_request(node_id: str, request: httpx.Request, body: bytes) -> httpx.Response | None:
    """Hand one already-built request to a node through the queue and wait for its answer."""
    path = request.url.raw_path.decode("ascii", "replace")
    command = queue(node_id, request.method, path,
                    headers=dict(request.headers), content=body,
                    content_type=str(request.headers.get("content-type") or ""))
    log.info("node %s is not dialable; queued %s %s as %s",
             node_id, request.method, path, command["id"])
    result = await await_result(node_id, command["id"], _request_timeout(request))
    if result is None:
        return None
    payload = result.get("json")
    response = httpx.Response(int(result.get("status") or 502),
                              json=payload if payload is not None else {})
    response.request = request
    response.extensions["studiohub_pull"] = True
    return response


class PullTransport(httpx.AsyncHTTPTransport):
    """Dial first; when the dial fails, reach a joined node through its own check-in.

    Installed on the controller's shared outbound client (`monitor._client`), so every call site
    that reaches a node through it — the broker's dispatch, Chat's relay, Subtitle's, the peer
    poll, a shared-voice sync, a model-memory release — gets the fallback without being rewritten.
    The explicit relay helpers (`fleet_nodes.relay_*`) keep their own client and their own
    queue-and-answer path, because a settings change must be answered at once rather than waited
    on.

    A machine the controller *can* dial is untouched: the dial is attempted first, and only a
    transport failure reaches the queue.
    """

    def __init__(self, *, dial: httpx.AsyncBaseTransport | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # The transport the dial is attempted on. In production this is the real network; a test
        # hands in one that cannot connect, which is the only honest way to exercise "the dial
        # failed" without unplugging a Mac.
        self._dial = dial

    async def _attempt_dial(self, request: httpx.Request) -> httpx.Response:
        if self._dial is None:
            return await super().handle_async_request(request)
        return await self._dial.handle_async_request(request)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        origin = request.url.raw_host and (request.url.host, request.url.port)
        node = node_for_origin(origin)
        # **A request that is not aimed at a joined node is not touched at all.** Almost every
        # request this client makes goes to this Mac's own workers, and the pull channel must not
        # put a body read or a rebuild in front of them.
        if node is None:
            return await self._attempt_dial(request)
        node_id, dial_fails = node
        # **Read the body once, then dial with the request unchanged.** `httpx.Request.aread()`
        # caches the bytes and re-arms the stream as a re-readable one, so the request that goes out
        # on the wire is byte-for-byte the one the caller built - headers, content-length and all.
        # Rebuilding it (which an earlier version did) produced a request whose declared length and
        # body disagreed, and a multipart voice push arrived at the node truncated.
        body = await request.aread()
        # **Dial first, unless the dial is already known to be failing.** A Mac on a foreign
        # tailnet is not going to start answering because this caller waited out a job's timeout,
        # and a 960 s wait to be told what the roster already knows is worse than useless. The
        # moment a dial succeeds the reason goes back to `probe` and this dials again, so nothing
        # is stuck behind a stale belief.
        if dial_fails:
            pulled = await pull_request(node_id, request, body)
            if pulled is not None:
                return pulled
            raise httpx.ConnectError("the node did not answer through its own check-in",
                                     request=request)
        try:
            response = await self._attempt_dial(request)
        except httpx.TransportError:
            pulled = await pull_request(node_id, request, body)
            if pulled is None:
                raise
            return pulled
        response.request = request
        return response


def client(**kwargs: Any) -> httpx.AsyncClient:
    """An `httpx.AsyncClient` that falls back to the pull channel. Used wherever a controller
    builds its own client to reach a node."""
    kwargs.setdefault("follow_redirects", False)
    kwargs.setdefault("transport", PullTransport())
    return httpx.AsyncClient(**kwargs)


def reset_store() -> None:
    """Drop this process's view of the queue: the file has been archived by a fleet reset.

    Nothing is written here — the caller owns the file (`fleet_nodes.reset_fleet` moves it into the
    data directory's `_trash` first), so what is left is the two pieces of state this module keeps
    in memory: which origin belongs to which node, and which callers are holding a connection open
    for an answer that will never come because the node is no longer on the roster.
    """
    global _ORIGIN_CACHE
    _ORIGIN_CACHE = None
    _WAITERS.clear()


def reset_for_tests() -> None:
    global _ORIGIN_CACHE
    _ORIGIN_CACHE = None
    _WAITERS.clear()
    try:
        store_path().unlink(missing_ok=True)
    except OSError:
        pass
