"""Chat Studio model-memory policy.

Publishes the fleet contract the Hub's ``FleetMemoryControl`` reads from every Studio
(``GET/PUT /api/memory-policy``, ``POST /api/memory/release``). Chat is the first local
Studio that can hold a text model between jobs, so unlike Image it has a real choice to
offer: release immediately, or keep the model resident for a while.

The default is **immediate**, for the same measured reason every other Studio defaults to
it: the owner's fleet shares one machine across several Studios, and a model held idle by
one is memory the next cannot use.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

MODES = {
    "performance": {"idle_seconds": None, "label": "Performance"},
    "balanced": {"idle_seconds": 600, "label": "Balanced"},
    "memory_saver": {"idle_seconds": 120, "label": "Memory Saver"},
    "immediate": {"idle_seconds": 0, "label": "Immediate"},
}
DEFAULT_MODE = "immediate"
CHECK_INTERVAL_SECONDS = 5


def machine_bytes() -> int:
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return 0


class MemoryPolicy:
    def __init__(self, data_dir: Path, engine) -> None:
        self._engine = engine
        self._settings = Path(data_dir) / "memory_policy.json"
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._process_started_at = time.time()
        self._last_release_at: float | None = None
        self._last_release_reason: str | None = None
        self._last_error: str | None = None
        self._release_count = 0

    # ---- settings --------------------------------------------------------
    def mode(self) -> str:
        try:
            raw = json.loads(self._settings.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            raw = {}
        value = raw.get("mode") if isinstance(raw, dict) else None
        return value if value in MODES else DEFAULT_MODE

    def save(self, mode: object) -> dict:
        if not isinstance(mode, str) or mode not in MODES:
            raise ValueError(f"mode must be one of: {', '.join(MODES)}")
        self._settings.parent.mkdir(parents=True, exist_ok=True)
        partial = self._settings.with_suffix(".json.tmp")
        partial.write_text(json.dumps({"mode": mode}, indent=2) + "\n", encoding="utf-8")
        os.replace(partial, self._settings)
        return self.status()

    # ---- shared status shape --------------------------------------------
    def status(self) -> dict:
        mode = self.mode()
        idle_seconds = MODES[mode]["idle_seconds"]
        loaded = self._engine.loaded()
        idle_for = None
        last = self._engine.last_activity_at()
        if loaded and last is not None:
            idle_for = max(0.0, time.time() - last)
        next_release = None
        if loaded and idle_seconds is not None and idle_for is not None and idle_seconds > 0:
            next_release = time.time() + max(0.0, idle_seconds - idle_for)
        return {
            "mode": mode,
            "default_mode": DEFAULT_MODE,
            "idle_seconds": idle_seconds,
            "options": [{"mode": key, **value} for key, value in MODES.items()],
            "loaded_models": [[self._engine.model_id]] if loaded else [],
            "loaded_model": self._engine.model_id if loaded else None,
            "model_idle_seconds": idle_for,
            "active_work": bool(self._engine.status()["busy"]),
            "busy": bool(self._engine.status()["busy"]),
            "next_release_at": next_release,
            "last_release_at": self._last_release_at,
            "last_release_reason": self._last_release_reason,
            "last_release_details": None,
            "last_error": self._last_error,
            "release_count": self._release_count,
            "counters_since": self._process_started_at,
            "resident": loaded,
            "machine_bytes": machine_bytes(),
            "release_policy": "Released when the idle policy says so, or on request.",
        }

    # ---- release ---------------------------------------------------------
    def release(self, reason: str = "manual") -> dict:
        try:
            outcome = self._engine.release(reason=reason)
        except Exception as exc:  # busy: never report a release that did not happen
            self._last_error = str(exc)
            raise
        if outcome.get("released"):
            self._last_release_at = time.time()
            self._last_release_reason = reason
            self._release_count += 1
        return self.status()

    def tick(self) -> None:
        mode = self.mode()
        idle_seconds = MODES[mode]["idle_seconds"]
        if idle_seconds is None or not self._engine.loaded():
            return
        if self._engine.status()["busy"]:
            return
        last = self._engine.last_activity_at()
        if last is not None and (time.time() - last) >= idle_seconds:
            try:
                self.release("idle")
            except Exception:
                pass

    def start(self) -> None:
        if self._thread is not None:
            return

        def loop() -> None:
            while not self._stop.wait(CHECK_INTERVAL_SECONDS):
                self.tick()

        self._thread = threading.Thread(target=loop, name="chat-memory-idle", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
