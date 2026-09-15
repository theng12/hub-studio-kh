"""Small scheduler controls for pausing registered Hub workers."""

from __future__ import annotations

from fastapi import HTTPException

from .backend import broker, registry, transcription_jobs


def _enabled(body: dict) -> bool:
    if not isinstance(body.get("enabled"), bool):
        raise HTTPException(400, "enabled must be true or false")
    return body["enabled"]


def _wake_dispatchers() -> None:
    broker.wake_dispatcher()
    transcription_jobs._wakeup.set()


def register_routes(app, monitor) -> None:
    """Register pause/resume controls without interrupting active work."""

    @app.post("/api/hub/registry/machines/{machine}/enabled")
    async def set_machine_enabled(machine: str, body: dict):
        machines = {
            str(worker.get("machine", "local"))
            for worker in monitor.registry
            if isinstance(worker, dict)
        }
        if machine not in machines:
            raise HTTPException(404, f"no registered machine {machine!r}")
        enabled = _enabled(body)
        registry.set_machine_enabled(machine, enabled)
        _wake_dispatchers()
        return {"ok": True, "machine": machine, "enabled": enabled}

    @app.post("/api/hub/registry/studios/{studio_id:path}/enabled")
    async def set_studio_enabled(studio_id: str, body: dict):
        studio = next(
            (worker for worker in monitor.registry
             if isinstance(worker, dict) and worker.get("id") == studio_id),
            None,
        )
        if studio is None:
            raise HTTPException(404, f"no registered studio {studio_id!r}")
        enabled = _enabled(body)
        machine = str(studio.get("machine", "local"))
        registry.set_studio_enabled(machine, studio_id, enabled)
        _wake_dispatchers()
        return {
            "ok": True, "studio": studio_id, "machine": machine, "enabled": enabled,
        }
