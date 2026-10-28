"""Small scheduler controls for pausing registered Hub workers."""

from __future__ import annotations

from fastapi import HTTPException

from .backend import broker, chat_jobs, peers, preferred_hold, registry, transcription_jobs, vocabulary


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
    # **Final render is rankable and has no registry row** (owner, 2026-10-28). It is this Mac's
    # own FFmpeg lane rather than a worker, so it is listed from the vocabulary whenever this Mac
    # can render at all — the title comes from the one place that owns it, never from
    # capitalising the id into "Render".
    render_authority = None
    try:
        from .backend import render_executor
        render_authority = render_executor.capability()
    except Exception:  # noqa: BLE001 - a Hub with no render lane is a Hub that never lists it
        render_authority = None
    if render_authority is not None:
        functions.append({"id": vocabulary.RENDER_MODALITY,
                          "title": vocabulary.PRIORITY_TITLES[vocabulary.RENDER_MODALITY]})
        seen.add(vocabulary.RENDER_MODALITY)
    order = registry.priority_order("local")
    saved = registry.preferred_work("local")
    hold = registry.preferred_hold("local")
    policy = preferred_hold.normalized(saved)
    # The simple actual state, from this Mac's own running-job evidence: which function is on
    # the Mac right now (any lane), and the bounded remaining hold. Never a guessed warm model.
    running = None
    try:
        from .backend import activity as activity_view
        jobs = activity_view.current_jobs(local, monitor.status,
                                          activity_view.live_batches())
        job = next(iter(jobs.values()), None)
        running = str((job or {}).get("modality") or "") or None
    except Exception:
        running = None
    state = preferred_hold.state(policy, hold, running,
                                 machine_prefers=bool(policy and running == policy["modality"]))
    return {"machine_enabled": registry.machine_enabled("local"),
            "priority_order": order,
            "priority_functions": functions,
            "chat_priority": bool(order and order[0] == "chat"),
            "preferred_choices": preferred_hold.choices_for(seen),
            "preferred_work": ({"enabled": bool(saved and saved.get("enabled")),
                                "modality": (saved or {}).get("modality"),
                                "idle_seconds": (policy or (saved or {})).get(
                                    "idle_seconds",
                                    registry.PREFERRED_WORK_DEFAULT_S)}
                               if saved else {"enabled": False, "modality": None,
                                              "idle_seconds": registry.PREFERRED_WORK_DEFAULT_S}),
            "preferred_state": state,
            "studios": [{"id": row["id"], "title": row.get("title") or row["id"],
                         "modality": row.get("modality"),
                         "enabled": registry.studio_enabled(
                             str(row.get("machine", "local")), row["id"])}
                        for row in local]}


def _validate_priority_order(monitor, order: object) -> list[str]:
    """The validated order, or HTTP 400 — shared by the order-only and combined saves."""
    if not isinstance(order, list) \
            or any(not isinstance(item, str) or not item for item in order) \
            or len(order) != len(set(order)):
        raise HTTPException(400, "priority_order must contain unique registered modalities")
    allowed = {row["id"] for row in work_policy(monitor)["priority_functions"]}
    if any(item not in allowed for item in order):
        raise HTTPException(400, "priority_order must contain unique registered modalities")
    return order


def save_work_policy(monitor, body: dict) -> dict:
    if set(body) == {"chat_priority"} and isinstance(body.get("chat_priority"), bool):
        order = ["chat"] if body["chat_priority"] else []
        registry.set_priority_order("local", order)
    elif set(body) == {"priority_order"} and isinstance(body.get("priority_order"), list):
        registry.set_priority_order("local", _validate_priority_order(
            monitor, body["priority_order"]))
    elif set(body) == {"preferred_work"}:
        _save_preferred_work(body["preferred_work"])
    elif set(body) == {"priority_order", "preferred_work"}:
        # Request-atomic: both halves validate (and the preference resolves) before either
        # write, so an invalid order can never leave a half-applied combined save. The two
        # rows are written by the registry's existing atomic per-key writes; a crash between
        # them leaves the first key saved, and a retry is idempotent.
        payload = body["preferred_work"]
        if not isinstance(payload, dict):
            raise HTTPException(400, "preferred_work must be an object")
        order = _validate_priority_order(monitor, body["priority_order"])
        _save_preferred_work(payload)
        registry.set_priority_order("local", order)
    else:
        raise HTTPException(400, "provide only priority_order, preferred_work, both, "
                                 "or the legacy chat_priority boolean")
    _wake_dispatchers()
    return work_policy(monitor)


def _save_preferred_work(payload: object) -> None:
    """Validate and persist one idle-hold preference; rejects anything partial."""
    if not isinstance(payload, dict):
        raise HTTPException(400, "preferred_work must be an object")
    unknown = set(payload) - {"enabled", "modality", "idle_seconds"}
    if unknown:
        raise HTTPException(400, f"unknown preferred_work field(s): {', '.join(sorted(unknown))}")
    enabled = payload.get("enabled")
    if not isinstance(enabled, bool):
        raise HTTPException(400, "preferred_work.enabled must be true or false")
    modality = payload.get("modality")
    if enabled:
        if modality not in preferred_hold.choices_for(_local_modalities()):
            raise HTTPException(400, "preferred_work.modality must be a generation function "
                                     "this Mac serves (render is never a preference)")
    elif modality is not None:
        raise HTTPException(400, "disabled preferred_work must not name a modality")
    seconds = payload.get("idle_seconds", registry.PREFERRED_WORK_DEFAULT_S)
    if not isinstance(seconds, int) or isinstance(seconds, bool):
        raise HTTPException(400, "preferred_work.idle_seconds must be a whole number of seconds")
    try:
        registry.set_preferred_work("local",
                                    modality if enabled else None,
                                    seconds if enabled else registry.PREFERRED_WORK_DEFAULT_S,
                                    enabled=enabled)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def _local_modalities() -> list[str]:
    from .backend import vocabulary as vocab
    return [worker.get("modality") for worker in broker._monitor().registry
            if peers.is_local_studio(worker) and worker.get("modality") in vocab.NODE_MODALITIES]


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
