"""HTTP registration for the Hub's Render tab (owner, 2026-10-26).

*"there's no render tab so there's no way to see the resulting video and how many has been
processed, and the duration records etc..."*

Three reads and no writes: a render's output belongs to Story Studio and its lifecycle is owned
by `render_service.py`. This module only shows what the Hub already recorded.

* `GET /api/hub/renders` — this Mac's renders, and on a controller also the renders of every Mac
  in its group. The Macs are asked over the Fleet token with the bounded, never-queued read the
  rest of the fleet reads use (`fleet_nodes.read_node`). One that does not answer is named as not
  answering, never silently dropped and never the reason the page fails.
* `GET /api/hub/renders/{job_id}` — one render.
* `GET /api/hub/fleet/nodes/{node_id}/renders` — one Mac's own list, `local` for this Mac. This
  is both the node side of the aggregation and the per-Mac read a controller or the Gateway can
  make with the same shape.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import FastAPI, HTTPException, Query

from .backend import render_history


def _source(machine: str, name: str, *, renders: int = 0,
            error: str | None = None) -> dict[str, Any]:
    """One Mac in the page's own list of where its renders came from."""
    return {"machine": machine, "machine_name": name, "renders": renders,
            "ok": error is None, "error": error}


async def site_view(
    database_path: str | Path,
    *,
    limit: int,
    pool: Callable[[], Awaitable[dict[str, Any]]],
    read_node: Callable[[str, str], Awaitable[dict[str, Any]]],
    local_machine: Callable[[], dict[str, str]],
    local_role: Callable[[], str],
    now: float | None = None,
) -> dict[str, Any]:
    """This Mac's renders, plus — on a controller — every joined Mac's, merged newest first.

    A node answers with the same `render_history.read` shape this Mac does, so one Mac's list and
    the site's list cannot disagree about what a render is. Totals are added per Mac and the rates
    derived from the sums, so no Mac's average is averaged with another's.
    """
    local = render_history.read(database_path, limit=limit, now=now)
    me = local_machine()
    my_name = str(me.get("name") or "This Mac")
    rows: list[dict[str, Any]] = [
        {**row, "machine": row.get("machine") or "local", "machine_name": my_name}
        for row in local["renders"]
    ]
    sources = [_source(str(me.get("id") or "local"), my_name,
                       renders=len(local["renders"]))]
    parts = [local["totals"]]

    # A node has no group to ask about: its own role says so, and dialling the controller for a
    # roster it will not use is a wait for nothing. Only a controller fans out.
    role = local_role()
    group = await pool() if role == "controller" else {"role": role, "nodes": []}
    if role == "controller":
        # A Mac the controller's own probe could not reach is named as not answering without being
        # dialled again: the pool already asked it, and the answer was no.
        reachable: list[tuple[str, str]] = []
        for node in group.get("nodes") or []:
            if not isinstance(node, dict) or not node.get("machine"):
                continue
            name = str(node["machine"])
            label = str(node.get("label") or node.get("name") or name)
            if node.get("state") == "reachable":
                reachable.append((name, label))
            else:
                sources.append(_source(name, label, error="that Mac did not answer"))
        answers = await asyncio.gather(*(
            read_node(name, "/renders") for name, _label in reachable),
            return_exceptions=True)
        for (name, label), answer in zip(reachable, answers):
            if isinstance(answer, BaseException) or not isinstance(answer, dict):
                sources.append(_source(name, label,
                                       error="that Mac's answer could not be read"))
                continue
            if not answer.get("ok"):
                sources.append(_source(name, label, error=str(
                    answer.get("detail") or answer.get("reason") or "that Mac did not answer")))
                continue
            body = answer.get("body") or {}
            node_renders = body.get("renders") if isinstance(body.get("renders"), list) else []
            sources.append(_source(name, label, renders=len(node_renders)))
            if isinstance(body.get("totals"), dict):
                parts.append(body["totals"])
            rows.extend({**row, "machine": name, "machine_name": label}
                        for row in node_renders if isinstance(row, dict))

    rows.sort(key=lambda row: row.get("created_at") or 0, reverse=True)
    return {
        "ok": True,
        "role": role,
        "retention_days": local["retention_days"],
        "renders": rows[:limit],
        "totals": render_history.merge_totals(parts),
        "machines": sources,
    }


def register_routes(
    app: FastAPI,
    *,
    database_path: str | Path,
    pool: Callable[[], Awaitable[dict[str, Any]]],
    read_node: Callable[[str, str], Awaitable[dict[str, Any]]],
    local_machine: Callable[[], dict[str, str]],
    settings: Callable[[], dict[str, Any]],
) -> None:
    def local_role() -> str:
        return str(settings().get("role") or "standalone")

    @app.get("/api/hub/renders")
    async def hub_renders(limit: int = Query(200, ge=1, le=1000)) -> dict[str, Any]:
        """Every final render this Hub ran, newest first, and the totals over the kept window."""
        return await site_view(database_path, limit=limit, pool=pool, read_node=read_node,
                               local_machine=local_machine, local_role=local_role)

    @app.get("/api/hub/renders/{job_id}")
    async def hub_render(job_id: str) -> dict[str, Any]:
        """One render in full: the profile, the hash, the failure reason, and where the file is.

        Read from this Mac's own store, which is where a render accepted here lives. A controller
        showing a node's render uses the row the list gave it, which carries every field this does.
        """
        row = render_history.detail(database_path, job_id)
        if row is None:
            raise HTTPException(404, "render not found")
        return row

    @app.get("/api/hub/fleet/nodes/{node_id}/renders")
    async def fleet_node_renders(node_id: str,
                                 limit: int = Query(200, ge=1, le=1000)) -> dict[str, Any]:
        """One Mac's own renders: `local` here, or a joined node's over the fleet credential."""
        wanted = node_id.strip().lower()
        me = local_machine()
        if wanted in ("local", str(me.get("id") or "").lower()):
            local = render_history.read(database_path, limit=limit)
            return {"ok": True, "machine": str(me.get("id") or "local"),
                    "machine_name": str(me.get("name") or "This Mac"), **local}
        if str(settings().get("role") or "") != "controller":
            raise HTTPException(404, "this Mac is not a controller, so it has no nodes to reach")
        answer = await read_node(wanted, f"/renders?limit={limit}")
        if not answer.get("ok"):
            raise HTTPException(502, str(answer.get("detail") or "that Mac did not answer"))
        return answer["body"]
