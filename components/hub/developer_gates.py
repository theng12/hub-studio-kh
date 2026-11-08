"""The seven fixed, authenticated developer-gate operations.

Only the current Fleet header credential authenticates these routes. A node credential, a
browser session and the loopback shortcut cannot grant gate authority. The controller relays
the same fixed routes to the target's ``local`` path over the existing ``_probe_client``;
source and result bodies are streamed, never buffered whole.
"""

from __future__ import annotations

import secrets
import shutil
import tempfile
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from . import developer_gate_contract as contract
from . import developer_gate_runner as gate_runner
from . import developer_gate_service as service_mod

SAFE = contract.SAFE_SEGMENT
BODY_CEILING = contract.ABSOLUTE_MAX_JSON_BYTES
RESPONSE_CEILING = contract.ABSOLUTE_MAX_JSON_BYTES
RESULT_CEILING = contract.ABSOLUTE_MAX_AGGREGATE_LOG_BYTES


class GateRefused(Exception):
    def __init__(self, status: int, code: str, detail: str,
                 *, acceptance: dict | None = None):
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail[:2048]
        self.acceptance = acceptance


def _error(exc: GateRefused) -> JSONResponse:
    return JSONResponse(
        {"format": contract.ERROR_FORMAT,
         "reason": {"code": exc.code, "detail": exc.detail},
         "acceptance": exc.acceptance},
        status_code=exc.status)


def _no_query(request: Request) -> None:
    if request.url.query:
        raise GateRefused(400, "invalid_request", "this route takes no query")


def _safe_key(key: str) -> str:
    """Keys are interpolated into relay URLs, so validate them before any remote path."""
    if not SAFE.fullmatch(key or ""):
        raise GateRefused(400, "invalid_request", "key is not a safe segment")
    return key


def _pinned_length(request: Request, expected: int) -> int:
    """Accept only a plain Content-Length that already equals the pinned source size."""
    if request.headers.get("transfer-encoding"):
        raise GateRefused(400, "invalid_request",
                          "Transfer-Encoding is not accepted; send Content-Length")
    encoding = (request.headers.get("content-encoding") or "").strip().lower()
    if encoding and encoding != "identity":
        raise GateRefused(400, "invalid_request",
                          "a content-encoded source is not the pinned archive")
    length = request.headers.get("content-length")
    if length is None or not length.isdigit():
        raise GateRefused(400, "invalid_request", "Content-Length is required and must be an integer")
    value = int(length)
    if value != expected:
        raise GateRefused(400, "invalid_request",
                          "Content-Length must equal the pinned source size")
    return value


def _source_temp_dir(service: Any) -> Path:
    # The store root is the only gate-owned volume; no public temporary-file seam exists.
    return Path(service._store.root_dir)


def _ensure_source_space(directory: Path, reservation: int, chunk: int = 0,
                         minimum: int = gate_runner.MINIMUM_FREE_BYTES) -> None:
    """Refuse unless the accepted reservation plus the next chunk still fits."""
    try:
        free = int(shutil.disk_usage(directory).free)
    except OSError as exc:
        raise GateRefused(503, "capacity", "free disk is unreadable") from exc
    if free < reservation + chunk:
        raise GateRefused(507, "capacity",
                          f"free disk is below the {minimum} byte gate floor plus the source reservation")


async def _body(request: Request) -> bytes:
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > BODY_CEILING:
            raise GateRefused(413, "bounds", "request body is over the wire ceiling")
        chunks.append(chunk)
    return b"".join(chunks)


async def _stream_to_temp(request: Request, expected_size: int, directory: Path,
                          reservation: int, minimum: int):
    """Stream one bounded raw body to the gate's own volume, rechecking the floor per write."""
    temp = tempfile.TemporaryFile(dir=directory)
    total = 0
    try:
        async for chunk in request.stream():
            total += len(chunk)
            if total > expected_size:
                raise GateRefused(413, "bounds", "source body exceeds its pinned size")
            _ensure_source_space(directory, reservation, len(chunk), minimum)
            temp.write(chunk)
        if total != expected_size:
            raise GateRefused(400, "invalid_request",
                              "source body is shorter than its pinned size")
        temp.seek(0)
        return temp
    except BaseException:
        temp.close()
        raise


def _declared_length(headers: Any, ceiling: int, *, required: bool) -> int | None:
    """Read a strict Content-Length, rejecting malformed or over-ceiling answers first."""
    encoding = (headers.get("Content-Encoding") or "").strip().lower()
    if encoding and encoding != "identity":
        raise GateRefused(502, "transport_error",
                          "that Mac's answer was content-encoded")
    value = headers.get("Content-Length")
    if value is None:
        if required:
            raise GateRefused(502, "transport_error",
                              "that Mac's answer declared no bounded Content-Length")
        return None
    if not value.isdigit():
        raise GateRefused(502, "transport_error",
                          "that Mac's answer had a malformed Content-Length")
    length = int(value)
    if length > ceiling:
        raise GateRefused(502, "transport_error",
                          "that Mac's answer was over the bounded response ceiling")
    return length


async def _bounded_bytes(response: Any, ceiling: int, *, required: bool) -> bytes:
    declared = _declared_length(response.headers, ceiling, required=required)
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_raw():
        total += len(chunk)
        if total > ceiling or (declared is not None and total > declared):
            raise GateRefused(502, "transport_error",
                              "that Mac's answer was over its declared size")
        chunks.append(chunk)
    if declared is not None and total != declared:
        raise GateRefused(502, "transport_error",
                          "that Mac's answer was shorter than its declared size")
    return b"".join(chunks)


def _controller_identity(request: Request, node_id: str) -> tuple[str, str, bool, dict | None]:
    """Authenticate the current Fleet header and resolve persisted node/controller identity."""
    actor = getattr(request.state, "fleet_actor", None)
    if not isinstance(actor, dict) or actor.get("kind") != "fleet":
        raise GateRefused(403, "unauthorized",
                          "only the controller's current Fleet credential may use developer gates")
    offered = request.headers.get("X-Hub-Token")
    from .backend import peers
    current = peers.current_fleet_token()
    if not offered or not current or not secrets.compare_digest(offered, current):
        raise GateRefused(403, "unauthorized",
                          "the current Fleet header credential is required")
    wanted = str(node_id or "").strip().lower()
    if not SAFE.fullmatch(wanted):
        raise GateRefused(400, "invalid_request", "node_id is not a safe segment")
    try:
        from . import developer_tests
        node, controller = developer_tests._identities()
    except Exception as exc:  # noqa: BLE001
        raise GateRefused(503, "release_unknown",
                          "this Mac's node/controller identity is unreadable") from exc
    if not isinstance(node, str) or not SAFE.fullmatch(node):
        raise GateRefused(503, "release_unknown", "this Mac has no persisted node identity")
    if wanted in ("local", node.lower()):
        if not isinstance(controller, str) or not SAFE.fullmatch(controller):
            raise GateRefused(503, "release_unknown",
                              "this Mac's controller identity is not persisted")
        return node, controller, True, None
    # A controller relays only to a node it has actually joined.
    from .backend import control_plane, fleet_nodes
    roster = fleet_nodes._read().get(wanted)
    if not isinstance(roster, dict):
        raise GateRefused(404, "unknown_node",
                          f"this controller has no joined Mac {wanted!r}")
    controller = str(control_plane.load_settings().get("controller_id") or "").strip()
    if not controller or not SAFE.fullmatch(controller):
        raise GateRefused(503, "release_unknown",
                          "this controller's persisted identity is missing")
    return wanted, controller, False, roster


def _node_url(node: dict, suffix: str) -> str:
    address = str(node.get("address") or "").rstrip("/")
    if not address:
        raise GateRefused(404, "unknown_node", "the joined Mac has no usable address")
    return f"{address}/api/hub/fleet/nodes/local/developer-gates{suffix}"


def _relay_headers(request: Request) -> dict[str, str]:
    headers: dict[str, str] = {}
    for name in ("Content-Type", "Content-Length", "Idempotency-Key"):
        value = request.headers.get(name)
        if value:
            headers[name] = value
    return headers


async def _relay_json(node: dict, method: str, suffix: str, request: Request,
                      body: bytes | None = None) -> Response:
    from .backend import fleet_nodes
    client = fleet_nodes._probe_client()
    try:
        async with client.stream(
                method, _node_url(node, suffix), content=body,
                headers=_relay_headers(request),
                timeout=fleet_nodes.STARTUP_RELAY_TIMEOUT) as response:
            raw = await _bounded_bytes(response, RESPONSE_CEILING, required=False)
            status = response.status_code
    except httpx.HTTPError as exc:
        raise GateRefused(502, "transport_error",
                          "that Mac did not answer, so the result is unknown") from exc
    finally:
        await client.aclose()
    return Response(content=raw, status_code=status, media_type="application/json")


async def _relay_source(node: dict, suffix: str, request: Request) -> Response:
    from .backend import fleet_nodes
    client = fleet_nodes._probe_client()
    try:
        async with client.stream(
                "PUT", _node_url(node, suffix), content=request.stream(),
                headers=_relay_headers(request),
                timeout=fleet_nodes.STARTUP_RELAY_TIMEOUT) as response:
            raw = await _bounded_bytes(response, RESPONSE_CEILING, required=False)
            status = response.status_code
    except httpx.HTTPError as exc:
        raise GateRefused(502, "transport_error",
                          "that Mac did not answer, so source acceptance is unknown") from exc
    finally:
        await client.aclose()
    return Response(content=raw, status_code=status, media_type="application/json")


async def _relay_result(node: dict, suffix: str, request: Request) -> Response:
    from .backend import fleet_nodes
    client = fleet_nodes._probe_client()
    stream = client.stream("GET", _node_url(node, suffix), headers=_relay_headers(request))
    try:
        response = await stream.__aenter__()
    except httpx.HTTPError as exc:
        await client.aclose()
        raise GateRefused(502, "transport_error",
                          "that Mac did not answer, so the result is unknown") from exc
    except BaseException:
        await client.aclose()
        raise
    try:
        declared = _declared_length(response.headers, RESULT_CEILING, required=True)
    except BaseException:
        try:
            await stream.__aexit__(None, None, None)
        finally:
            await client.aclose()
        raise
    media_type = response.headers.get("Content-Type") or "application/x-tar"

    async def body() -> AsyncIterator[bytes]:
        total = 0
        try:
            async for chunk in response.aiter_raw():
                total += len(chunk)
                if total > declared:
                    raise GateRefused(502, "transport_error",
                                      "that Mac's result was over its declared size")
                yield chunk
            if total != declared:
                raise GateRefused(502, "transport_error",
                                  "that Mac's result was shorter than its declared size")
        finally:
            try:
                await stream.__aexit__(None, None, None)
            finally:
                await client.aclose()

    return StreamingResponse(body(), status_code=response.status_code,
                             media_type=media_type,
                             headers={"Content-Length": str(declared)})


def _file_body(path: Path):
    with path.open("rb") as stream:
        while chunk := stream.read(contract.STREAM_CHUNK_BYTES):
            yield chunk


def register_routes(app, service=None) -> None:
    """Register exactly the seven frozen operations and nothing else."""

    def get_service():
        return service if service is not None else service_mod.get_service()

    def refusal(exc: BaseException) -> JSONResponse:
        if isinstance(exc, GateRefused):
            return _error(exc)
        if isinstance(exc, service_mod.GateServiceError):
            return _error(GateRefused(exc.status, exc.code, exc.detail,
                                      acceptance=exc.acceptance))
        if isinstance(exc, contract.GateContractError):
            return _error(GateRefused(exc.status, exc.code, exc.detail))
        from . import developer_gate_store as store_mod
        if isinstance(exc, store_mod.GateStoreError):
            return _error(GateRefused(getattr(exc, "status_code", 400), "invalid_request",
                                      str(exc)))
        return _error(GateRefused(500, "internal_error", "developer gate operation failed"))

    @app.get("/api/hub/fleet/nodes/{node_id}/developer-gates/capabilities")
    async def developer_gates_capabilities(node_id: str, request: Request) -> Response:
        try:
            _no_query(request)
            target, _controller, local, node = _controller_identity(request, node_id)
            if not local:
                return await _relay_json(node, "GET", "/capabilities", request)
            return JSONResponse(get_service().capabilities())
        except Exception as exc:  # noqa: BLE001 - closed error shape
            return refusal(exc)

    @app.post("/api/hub/fleet/nodes/{node_id}/developer-gates")
    async def developer_gates_submit(node_id: str, request: Request) -> Response:
        try:
            _no_query(request)
            target, controller, local, node = _controller_identity(request, node_id)
            raw = await _body(request)
            key = str(request.headers.get("Idempotency-Key") or "")
            if not key:
                raise GateRefused(400, "invalid_request", "Idempotency-Key is required")
            if not local:
                return await _relay_json(node, "POST", "", request, raw)
            actor = {"kind": "fleet", "node_id": None}
            transport = {"source": request.client.host if request.client else None,
                         "credential": "fleet-header"}
            return JSONResponse(get_service().accept(
                raw, key, controller, target, actor, transport))
        except Exception as exc:  # noqa: BLE001
            return refusal(exc)

    @app.get("/api/hub/fleet/nodes/{node_id}/developer-gates/by-key/{key}")
    async def developer_gates_status(node_id: str, key: str, request: Request) -> Response:
        try:
            _no_query(request)
            target, controller, local, node = _controller_identity(request, node_id)
            _safe_key(key)
            if not local:
                return await _relay_json(node, "GET", f"/by-key/{key}", request)
            return JSONResponse(get_service().status(key, controller, target))
        except Exception as exc:  # noqa: BLE001
            return refusal(exc)

    @app.put("/api/hub/fleet/nodes/{node_id}/developer-gates/by-key/{key}/source")
    async def developer_gates_source(node_id: str, key: str, request: Request) -> Response:
        try:
            _no_query(request)
            target, controller, local, node = _controller_identity(request, node_id)
            _safe_key(key)
            if not local:
                return await _relay_source(node, f"/by-key/{key}/source", request)
            gate_service = get_service()
            declared = gate_service.declared_source(key, controller, target)
            content_length = _pinned_length(request, declared["size_bytes"])
            accepted = gate_service._request_of(
                gate_service._attempt(key, controller, target))
            limits = accepted["requested_limits"]
            minimum = max(int(limits["minimum_free_bytes"]),
                          gate_runner.MINIMUM_FREE_BYTES)
            # The accepted need already counts the source bytes this upload will consume.
            reservation = (gate_service._free_need(accepted)
                           - int(accepted["source"]["size_bytes"]))
            directory = _source_temp_dir(gate_service)
            _ensure_source_space(directory, reservation, 0, minimum)
            temp = await _stream_to_temp(
                request, declared["size_bytes"], directory, reservation, minimum)
            try:
                return JSONResponse(get_service().source(
                    key, temp, content_length, controller, target))
            finally:
                temp.close()
        except Exception as exc:  # noqa: BLE001
            return refusal(exc)

    @app.post("/api/hub/fleet/nodes/{node_id}/developer-gates/by-key/{key}/cancel")
    async def developer_gates_cancel(node_id: str, key: str, request: Request) -> Response:
        try:
            _no_query(request)
            target, controller, local, node = _controller_identity(request, node_id)
            _safe_key(key)
            raw = await _body(request)
            if not local:
                return await _relay_json(node, "POST", f"/by-key/{key}/cancel", request, raw)
            return JSONResponse(get_service().cancel(key, raw, controller, target))
        except Exception as exc:  # noqa: BLE001
            return refusal(exc)

    @app.get("/api/hub/fleet/nodes/{node_id}/developer-gates/by-key/{key}/result")
    async def developer_gates_result(node_id: str, key: str, request: Request) -> Response:
        try:
            _no_query(request)
            target, controller, local, node = _controller_identity(request, node_id)
            _safe_key(key)
            if not local:
                return await _relay_result(node, f"/by-key/{key}/result", request)
            path, descriptor = get_service().result(key, controller, target)
            return StreamingResponse(
                _file_body(path), media_type="application/x-tar",
                headers={"Content-Length": str(descriptor["size_bytes"])})
        except Exception as exc:  # noqa: BLE001
            return refusal(exc)

    @app.post("/api/hub/fleet/nodes/{node_id}/developer-gates/by-key/{key}/ack")
    async def developer_gates_ack(node_id: str, key: str, request: Request) -> Response:
        try:
            _no_query(request)
            target, controller, local, node = _controller_identity(request, node_id)
            _safe_key(key)
            raw = await _body(request)
            if not local:
                return await _relay_json(node, "POST", f"/by-key/{key}/ack", request, raw)
            return JSONResponse(get_service().ack(key, raw, controller, target))
        except Exception as exc:  # noqa: BLE001
            return refusal(exc)
