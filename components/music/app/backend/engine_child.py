"""The spawned ACE-Step engine process.

This module is imported in the worker process but its ``main`` only ever runs
inside a ``multiprocessing`` *spawn* child.  The child owns the model weights
and the MLX context; the parent (``generation.py``) owns job state and the
artifact.  That split is what lets a cancel kill a wedged native call without
taking the HTTP server down, and it mirrors the intent of
``components/voice/app/backend/native_executor.py``.

Protocol
--------
``commands``  parent -> child, ``{"job_id": str, "payload": {...}}``
``events``    child -> parent, one dict per line of progress/terminal state:

    {"kind": "loading"}                       models are being loaded
    {"kind": "ready", "load_seconds": float}  ready to accept commands
    {"kind": "started", "job_id": str}
    {"kind": "progress", "job_id": str, "ratio": float, "desc": str}
    {"kind": "done", "job_id": str, "path": str, "sample_rate": int}
    {"kind": "cancelled", "job_id": str}
    {"kind": "error", "job_id": str, "error": str, "traceback": str}

The engine is addressed through its public programmatic API
(``acestep.handler.AceStepHandler`` + ``acestep.llm_inference.LLMHandler`` +
``acestep.inference.generate_music``), the same path ``cli.py`` uses, rather
than through its Gradio UI or HTTP server.
"""
from __future__ import annotations

import os
import sys
import time
import traceback


class EngineCancelled(RuntimeError):
    """Raised inside the engine callback when the parent asks for a stop."""


def _configure_environment(config: dict) -> None:
    """Point the pinned engine source at the installer-owned checkpoint layout."""
    engine_root = str(config["engine_root"])
    checkpoints = str(config["checkpoints_dir"])
    # The engine resolves ``<project_root>/checkpoints`` and its progress cache
    # from this variable when no explicit path is passed.
    os.environ["ACESTEP_PROJECT_ROOT"] = engine_root
    os.environ["ACESTEP_CHECKPOINTS_DIR"] = checkpoints
    # Never let an inherited proxy or telemetry variable reach the engine.
    for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        os.environ.pop(key, None)
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


def _load_handlers(config: dict, events) -> tuple[object, object]:
    from acestep.handler import AceStepHandler
    from acestep.llm_inference import LLMHandler

    events.put({"kind": "loading"})
    started = time.time()
    dit = AceStepHandler()
    status, ok = dit.initialize_service(
        project_root=str(config["engine_root"]),
        config_path=str(config["dit_model"]),
        device=str(config.get("device") or "auto"),
        offload_to_cpu=False,
    )
    if not ok:
        raise RuntimeError(f"DiT initialization failed: {status}")

    llm = LLMHandler()
    status, ok = llm.initialize(
        checkpoint_dir=str(config["checkpoints_dir"]),
        lm_model_path=str(config["lm_model"]),
        backend=str(config.get("backend") or "mlx"),
        device=str(config.get("device") or "auto"),
        offload_to_cpu=False,
        dtype=None,
    )
    if not ok:
        raise RuntimeError(f"LM initialization failed: {status}")

    events.put({"kind": "ready", "load_seconds": round(time.time() - started, 2)})
    return dit, llm


def _generate(dit, llm, payload: dict, cancel_event, events) -> dict:
    from acestep.inference import GenerationConfig, GenerationParams, generate_music

    job_id = payload["job_id"]

    def progress(ratio, desc: str = ""):
        if cancel_event.is_set():
            raise EngineCancelled("generation cancelled")
        try:
            value = max(0.0, min(1.0, float(ratio)))
        except (TypeError, ValueError):
            return
        events.put({"kind": "progress", "job_id": job_id,
                    "ratio": value, "desc": str(desc or "")})

    params = GenerationParams(
        task_type="text2music",
        thinking=True,
        caption=str(payload["prompt"]),
        lyrics=str(payload.get("lyrics") or "[Instrumental]"),
        instrumental=bool(payload.get("instrumental", True)),
        vocal_language=str(payload.get("vocal_language") or "unknown"),
        duration=float(payload["duration"]),
        inference_steps=int(payload.get("steps") or 8),
        guidance_scale=float(payload.get("guidance_scale") or 1.0),
        shift=float(payload.get("shift") or 3.0),
        seed=int(payload["seed"]),
        use_cot_metas=False,
        use_cot_caption=False,
        use_cot_lyrics=False,
        use_cot_language=False,
    )
    config = GenerationConfig(
        batch_size=1,
        audio_format="wav",
        use_random_seed=False,
    )
    result = generate_music(dit, llm, params=params, config=config,
                            save_dir=str(payload["staging_dir"]), progress=progress)
    if cancel_event.is_set():
        raise EngineCancelled("generation cancelled")
    if not getattr(result, "success", False):
        raise RuntimeError(str(getattr(result, "status_message", "") or "generation failed"))
    audios = list(getattr(result, "audios", []) or [])
    if not audios:
        raise RuntimeError("engine returned no audio")
    audio = audios[0]
    path = str(audio.get("path") or "")
    if not path:
        raise RuntimeError("engine did not write an audio file")
    return {"path": path, "sample_rate": int(audio.get("sample_rate") or 48000),
            "key": str(audio.get("key") or "")}


def main(commands, events, cancel_event, config) -> None:
    """Child entry point.  Loads once, then serves commands until told to stop."""
    # Own process group, so the parent's forced stop cannot signal the server's
    # group and cannot leave a helper child of the engine behind.
    if os.name == "posix":
        try:
            os.setsid()
        except OSError:
            pass
    _configure_environment(config)
    try:
        dit, llm = _load_handlers(config, events)
    except BaseException as exc:  # noqa: BLE001 - the parent must see the cause
        events.put({"kind": "error", "job_id": None,
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc()[-8000:]})
        return

    while True:
        command = commands.get()
        if command is None:
            return
        job_id = str(command.get("job_id") or "")
        payload = dict(command.get("payload") or {})
        payload["job_id"] = job_id
        started = time.time()
        try:
            events.put({"kind": "started", "job_id": job_id})
            outcome = _generate(dit, llm, payload, cancel_event, events)
            if cancel_event.is_set():
                raise EngineCancelled("generation cancelled")
            events.put({"kind": "done", "job_id": job_id, "path": outcome["path"],
                        "sample_rate": outcome["sample_rate"],
                        "engine_seconds": round(time.time() - started, 3)})
        except EngineCancelled as exc:
            events.put({"kind": "cancelled", "job_id": job_id, "error": str(exc)})
        except BaseException as exc:  # noqa: BLE001 - forward the real text
            events.put({"kind": "error", "job_id": job_id,
                        "error": f"{type(exc).__name__}: {exc}",
                        "traceback": traceback.format_exc()[-8000:]})


if __name__ == "__main__":  # pragma: no cover - defensive, never the entry path
    sys.stderr.write("engine_child is a spawned target, not a script\n")
    raise SystemExit(2)
