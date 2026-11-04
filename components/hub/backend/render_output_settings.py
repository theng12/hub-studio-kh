"""Authenticated unsupported negotiation for the revision-4 Best contract."""
from __future__ import annotations

import time
from typing import Any, Callable

from fastapi import FastAPI, Request

from .render_service import _actor

CAPABILITIES_FORMAT = "studio.render.hub-output-settings-capabilities.v1"
CAPABILITIES_REASON = (
    "Best for this hardware is unavailable: verified comparison, quality and selection-policy "
    "evidence contracts are not ready. Use explicit output settings."
)


def register_routes(
    app: FastAPI,
    *,
    hub_token: str,
    fleet_token: Callable[[], str | None],
    settings: Callable[[], dict[str, Any]],
) -> None:
    @app.get("/api/hub/final-renders/output-settings/capabilities")
    async def capabilities(request: Request) -> dict[str, Any]:
        _actor(request, hub_token, fleet_token, settings)
        # The whole protocol is unqualified; no selection, descriptor or measurement is offered.
        return {
            "format": CAPABILITIES_FORMAT,
            "private": True,
            "chargeable": False,
            "supported": False,
            "reason": CAPABILITIES_REASON,
            "profile_descriptor_utf8": None,
            "profile_digest": None,
            "server_time": int(time.time()),
        }
