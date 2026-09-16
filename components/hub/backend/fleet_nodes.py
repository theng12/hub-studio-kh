"""The controller's list of Macs that have joined it.

A node joins by telling its controller who it is and where it answers. The controller records
that, and then reports each node's state by asking it directly — a stored "joined" flag is not
evidence that a Mac is still there, so it is never shown as one.

This is deliberately the smallest thing that makes one app a controller and another a node:
identity, address, and a live reachability check. Scheduling work onto a joined node is the
next step and is not claimed here.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
import tempfile
import time
from typing import Any

import httpx

from .registry import DATA_DIR

FILE_NAME = "fleet_nodes.json"
MAX_NODES = 64
ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
# A join request is authenticated with the controller's own credential, so it is not a public
# endpoint. The address is still validated: a controller must never be told to fetch an
# arbitrary URL, which would turn a join into a request forgery against this Mac's network.
PROBE_TIMEOUT = httpx.Timeout(6.0, connect=3.0)


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


def join(payload: dict[str, Any]) -> dict[str, Any]:
    """Record one node, or refresh what a rejoining node reports about itself."""
    node_id = str(payload.get("node_id") or "").strip().lower()
    if not ID_PATTERN.fullmatch(node_id):
        raise ValueError("node_id must be lowercase letters, numbers, dots, dashes or underscores")
    name = str(payload.get("name") or "").strip()
    if not 1 <= len(name) <= 120:
        raise ValueError("name must be between 1 and 120 characters")
    address = validate_address(payload.get("address"))
    nodes = _read()
    if node_id not in nodes and len(nodes) >= MAX_NODES:
        raise ValueError(f"this controller already has {MAX_NODES} nodes")
    existing = nodes.get(node_id) or {}
    nodes[node_id] = {
        "id": node_id,
        "name": name,
        "address": address,
        "role": "agent",
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


async def probe(node: dict[str, Any], client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """Ask one node whether it is there, and report what it says about itself.

    Reachability is asked for, never assumed: a node that was joined last week and has been
    switched off must read as unreachable rather than as available.
    """
    row = {key: node.get(key) for key in ("id", "name", "address", "joined_at")}
    owned = client is None
    if owned:
        client = httpx.AsyncClient(follow_redirects=False)
    try:
        response = await client.get(f"{node['address']}/api/version", timeout=PROBE_TIMEOUT)
        if not response.is_success:
            return {**row, "state": "error", "detail": f"node answered HTTP {response.status_code}"}
        try:
            body = response.json()
        except ValueError:
            return {**row, "state": "error", "detail": "node returned an invalid response"}
        if not isinstance(body, dict):
            return {**row, "state": "error", "detail": "node returned an invalid response"}
        return {**row, "state": "reachable", "detail": None,
                "version": body.get("version"), "source": body.get("source")}
    except (httpx.HTTPError, OSError) as exc:
        return {**row, "state": "unreachable", "detail": f"could not reach the node: {exc}"}
    finally:
        if owned:
            await client.aclose()


async def inventory(client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """Every joined node with its live state, plus a count of what answered."""
    nodes = _read()
    rows = []
    for node_id in sorted(nodes):
        rows.append(await probe(nodes[node_id], client))
    return {
        "role": "controller",
        "nodes": rows,
        "summary": {
            "total": len(rows),
            "reachable": sum(row["state"] == "reachable" for row in rows),
            "unreachable": sum(row["state"] == "unreachable" for row in rows),
            "error": sum(row["state"] == "error" for row in rows),
        },
    }
