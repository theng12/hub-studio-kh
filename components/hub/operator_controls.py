"""Small scheduler controls for pausing registered Hub workers."""

from __future__ import annotations

from fastapi import HTTPException

from .backend import broker, chat_jobs, peers, registry, transcription_jobs, vocabulary


def _enabled(body: dict) -> bool:
    if not isinstance(body.get("enabled"), bool):
        raise HTTPException(400, "enabled must be true or false")
    return body["enabled"]


def _wake_dispatchers() -> None:
    broker.wake_dispatcher()
    transcription_jobs._wakeup.set()
    chat_jobs._wakeup.set()


def work_policy(monitor) -> dict:
    local = [worker for worker in monitor.registry if peers.is_local_studio(worker)]
    functions = []
    seen = set()
    for worker in local:
        modality = worker.get("modality")
        if modality in seen or modality not in vocabulary.NODE_MODALITIES:
            continue
        seen.add(modality)
        functions.append({"id": modality,
                          "title": worker.get("title") or worker["id"]})
    order = registry.priority_order("local")
    return {"machine_enabled": registry.machine_enabled("local"),
            "priority_order": order,
            "priority_functions": functions,
            "chat_priority": bool(order and order[0] == "chat"),
            "studios": [{"id": row["id"], "title": row.get("title") or row["id"],
                         "modality": row.get("modality"),
                         "enabled": registry.studio_enabled(
                             str(row.get("machine", "local")), row["id"])}
                        for row in local]}


def save_work_policy(monitor, body: dict) -> dict:
    if set(body) == {"chat_priority"} and isinstance(body.get("chat_priority"), bool):
        order = ["chat"] if body["chat_priority"] else []
    elif set(body) == {"priority_order"} and isinstance(body.get("priority_order"), list):
        order = body["priority_order"]
        allowed = {row["id"] for row in work_policy(monitor)["priority_functions"]}
        if (any(not isinstance(item, str) or not item or item not in allowed
                for item in order) or len(order) != len(set(order))):
            raise HTTPException(400, "priority_order must contain unique registered modalities")
    else:
        raise HTTPException(400, "provide only priority_order, or the legacy chat_priority boolean")
    registry.set_priority_order("local", order)
    _wake_dispatchers()
    return work_policy(monitor)


def register_routes(app, monitor) -> None:
    """Register pause/resume controls without interrupting active work."""

    @app.get("/api/hub/work-policy")
    async def get_work_policy():
        return work_policy(monitor)

    @app.post("/api/hub/work-policy")
    async def set_work_policy(body: dict):
        return save_work_policy(monitor, body)

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
