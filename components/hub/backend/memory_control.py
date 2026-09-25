"""Fleet controller for the sibling Studios' opt-in memory policies."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from components import memory_modes

from . import peers, vocabulary
from .registry import label_for


# Every Studio that may hold a model resident and therefore publishes the memory-policy
# contract. The routed vocabulary is derived from the Hub's own table rather than restated: the
# copy that used to live here named {"image","chat","video","music","voice","render",
# "transcription"} and had already fallen behind once (leaving `transcription` out kept the
# Subtitle model out of the Hub's machine-wide release pass entirely, so a Whisper model could
# stay resident across a handoff to another Studio on an 8 GB Mac).
#
# `render` and `video` are a *deliberate* superset, named explicitly for a documented reason
# rather than derived: neither is a worker modality in this app - `render` is the Hub's own local
# FFmpeg step (`monitor.LOCAL_ONLY_MODALITIES`) and no video worker ships - but a registry
# written by another build, or read from a GenStudio peer, may still list a studio for one of
# them, and this is the read that stops every Studio holding a model from keeping it across a
# handoff. Dropping them would silently stop releasing those studios' models. They are inert for
# a packaged fleet: both admission rules (`registry.TRACKED_MODALITIES`, `fleet_nodes._studios`)
# refuse either modality, so such a studio cannot appear here today.
SUPPORTED_MODALITIES = frozenset(vocabulary.WORKER_FOR_MODALITY) | {"render", "video"}
# Hub validates mode names and renders their labels, and deliberately carries no thresholds:
# each Studio owns the idle window it actually runs with and reports it back in its own
# `status()`, so a second table of numbers here could only disagree with the worker that
# enforces them. The names and labels come from the one shared table
# (`components/memory_modes.py`) rather than a hand-written list, so `auto` — or a sixth
# mode added later — cannot exist in every Studio and nowhere in the Hub.
MODES = {mode: {"label": value["label"]} for mode, value in memory_modes.MODES.items()}
# The Hub's bulk draft is the same selection a fresh install makes: `auto`. The Hub does not
# resolve it and must not try to — the rule and its 16 GiB line live in
# `components/memory_modes.py`, and each Studio answers for its own Mac. That is the whole
# reason one draft can be right on an 8 GB and a 32 GB Mac at once: the 8 GB machine behaves
# as `immediate`, the 32 GB machine as `balanced`, and neither is a special case here. A
# Studio that has already chosen keeps its saved policy; this is only what the Hub offers to
# a Studio the operator has not set, and what the Settings screen shows as the default.
DEFAULT_MODE = memory_modes.DEFAULT_MODE
REQUEST_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


def _error_detail(response: httpx.Response) -> str:
    try:
        value = response.json()
        if isinstance(value, dict):
            return str(value.get("detail") or value.get("message") or "").strip()
    except (ValueError, TypeError):
        pass
    return response.text.strip()[:300] or f"HTTP {response.status_code}"


class FleetMemoryControl:
    """Read and change memory policy without owning worker state itself."""

    def __init__(self, monitor, client=None):
        self.monitor = monitor
        self.client = client or monitor._client

    def targets(self) -> list[dict[str, Any]]:
        return sorted(
            [studio for studio in self.monitor.registry
             if studio.get("modality") in SUPPORTED_MODALITIES],
            key=lambda row: (
                str(row.get("machine", "local")),
                str(row.get("modality", "")),
                str(row.get("id", "")),
            ),
        )

    def _selected(self, studio_ids: list[str] | None) -> list[dict[str, Any]]:
        targets = self.targets()
        if studio_ids is None:
            return targets
        if not studio_ids:
            raise ValueError("select at least one Studio")
        requested = set(studio_ids)
        known = {studio["id"] for studio in targets}
        unknown = sorted(requested - known)
        if unknown:
            raise ValueError("unknown memory-control studio(s): " + ", ".join(unknown))
        return [studio for studio in targets if studio["id"] in requested]

    async def _request(self, studio: dict[str, Any], method: str, path: str,
                       payload: dict[str, Any] | None = None) -> httpx.Response:
        url, headers = peers.studio_request(studio, path)
        return await self.client.request(
            method, url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT,
        )

    def _base_row(self, studio: dict[str, Any]) -> dict[str, Any]:
        state = self.monitor.status.get(studio["id"], {})
        return {
            "id": studio["id"],
            "title": studio.get("title", studio["id"]),
            "modality": studio.get("modality"),
            "machine": studio.get("machine", "local"),
            "machine_label": studio.get("machine_label") or label_for(
                studio.get("machine", "local")),
            "url": peers.studio_request(studio, "/")[0].rsplit("/", 1)[0],
            "health": state.get("status", "unknown"),
        }

    async def _status_one(self, studio: dict[str, Any]) -> dict[str, Any]:
        row = self._base_row(studio)
        if row["health"] == "down":
            return {**row, "state": "offline", "supported": None,
                    "detail": "Studio is offline"}
        try:
            response = await self._request(studio, "GET", "/api/memory-policy")
        except (httpx.HTTPError, OSError) as exc:
            return {**row, "state": "offline", "supported": None,
                    "detail": f"Could not reach Studio: {exc}"}
        if response.status_code == 404:
            return {**row, "state": "update_required", "supported": False,
                    "detail": "Run Update on this Studio to add memory controls"}
        if not response.is_success:
            return {**row, "state": "error", "supported": None,
                    "detail": _error_detail(response)}
        try:
            policy = response.json()
        except ValueError:
            return {**row, "state": "error", "supported": None,
                    "detail": "Studio returned an invalid memory-policy response"}
        if not isinstance(policy, dict) or policy.get("mode") not in MODES:
            return {**row, "state": "error", "supported": None,
                    "detail": "Studio returned an invalid memory policy"}
        return {**row, "state": "ready", "supported": True, "detail": None,
                "policy": policy}

    async def inventory(self) -> dict[str, Any]:
        studios = await asyncio.gather(*(self._status_one(s) for s in self.targets()))
        return {
            "default_mode": DEFAULT_MODE,
            "options": [{"mode": mode, **value} for mode, value in MODES.items()],
            "studios": studios,
            "summary": {
                "total": len(studios),
                "ready": sum(row["state"] == "ready" for row in studios),
                "offline": sum(row["state"] == "offline" for row in studios),
                "update_required": sum(row["state"] == "update_required" for row in studios),
            },
        }

    # The fields a Studio uses to report that it is still holding something. A release that
    # returns 200 while one of these is non-empty freed nothing, and the app must not be told
    # otherwise: the same check already decides the fleet handoff's `released` count, so a
    # single response could otherwise be a success to the app and a failure to the scheduler.
    RESIDENT_FIELDS = ("loaded_model", "loaded_models", "loaded_pipeline")

    @classmethod
    def _still_resident(cls, policy: Any) -> bool:
        return bool(isinstance(policy, dict)
                    and any(policy.get(field) for field in cls.RESIDENT_FIELDS))

    async def _change_one(self, studio: dict[str, Any], method: str, path: str,
                          payload: dict[str, Any] | None, success: str) -> dict[str, Any]:
        row = self._base_row(studio)
        try:
            response = await self._request(studio, method, path, payload)
        except (httpx.HTTPError, OSError) as exc:
            return {**row, "ok": False, "result": "offline",
                    "detail": f"Could not reach Studio: {exc}"}
        if response.status_code == 404:
            return {**row, "ok": False, "result": "update_required",
                    "detail": "Run Update on this Studio to add memory controls"}
        if response.status_code == 409:
            return {**row, "ok": False, "result": "busy",
                    "detail": _error_detail(response)}
        if not response.is_success:
            return {**row, "ok": False, "result": "error",
                    "detail": _error_detail(response)}
        try:
            policy = response.json()
        except ValueError:
            policy = {}
        policy = policy if isinstance(policy, dict) else {}
        if success == "released" and self._still_resident(policy):
            return {**row, "ok": False, "result": "still_loaded", "policy": policy,
                    "detail": "The Studio reported success but is still holding "
                              + ", ".join(str(model) for model in
                                          policy.get("loaded_models") or [])}
        return {**row, "ok": True, "result": success, "detail": None, "policy": policy}

    @staticmethod
    def _operation(results: list[dict[str, Any]], action: str) -> dict[str, Any]:
        succeeded = sum(bool(row.get("ok")) for row in results)
        return {
            "ok": succeeded == len(results),
            "action": action,
            "selected": len(results),
            "succeeded": succeeded,
            "failed": len(results) - succeeded,
            "results": results,
        }

    async def set_mode(self, mode: str, studio_ids: list[str] | None = None) -> dict[str, Any]:
        if mode not in MODES:
            raise ValueError("mode must be one of: " + ", ".join(MODES))
        targets = self._selected(studio_ids)
        # A Studio may declare its policy fixed — Image builds and drops mflux inside every
        # job, so it has no idle timer to configure. Asking it to change would fail the whole
        # operation for a Studio whose desired end state is already met, so report it as
        # already-satisfied instead of sending a request that must be rejected.
        statuses = await asyncio.gather(*(self._status_one(studio) for studio in targets))
        settled, changeable = [], []
        for studio, state in zip(targets, statuses):
            if state.get("state") == "ready" and (state.get("policy") or {}).get("fixed"):
                settled.append({**state, "ok": True, "result": "fixed"})
            else:
                changeable.append(studio)
        results = await asyncio.gather(*(
            self._change_one(studio, "PUT", "/api/memory-policy", {"mode": mode}, "updated")
            for studio in changeable
        ))
        operation = self._operation([*results, *settled], "set_mode")
        operation["mode"] = mode
        return operation

    async def release(self, studio_ids: list[str] | None = None) -> dict[str, Any]:
        targets = self._selected(studio_ids)
        results = await asyncio.gather(*(
            self._change_one(studio, "POST", "/api/memory/release", None, "released")
            for studio in targets
        ))
        return self._operation(results, "release")
