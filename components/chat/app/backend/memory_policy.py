"""Chat Studio model-memory policy.

Publishes the fleet contract the Hub's ``FleetMemoryControl`` reads from every Studio
(``GET/PUT /api/memory-policy``, ``POST /api/memory/release``). Chat is the first local
Studio that can hold a text model between jobs, so unlike Image it has a real choice to
offer: release immediately, or keep the model resident for a while.

The default is **auto**: the mode table and the rule behind it come from
:mod:`components.memory_modes`, so a Mac below 16 GiB of unified memory behaves exactly as
it did under the old ``immediate`` default — a model held idle by one Studio is memory the
next cannot use, which is the measured 8 GB tier failure — while a 16/24/32 GB Mac holds it
for the balanced window instead of paying a model load per job. A machine whose memory
cannot be measured resolves to ``immediate``: a Mac that cannot prove it has room must not
hold a model. An operator's explicit choice always wins.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from components import memory_modes

# The fleet's one mode table, imported rather than copied: `auto`'s answer is the contract,
# and four hand-maintained copies of it is how the studios would come to disagree.
MODES = memory_modes.MODES
DEFAULT_MODE = memory_modes.DEFAULT_MODE
CHECK_INTERVAL_SECONDS = 5


def machine_bytes() -> int:
    """This Mac's unified memory in bytes, through the one reader every Studio shares."""
    return memory_modes.machine_bytes()


def machine_mode() -> str:
    """What ``auto`` resolves to on this Mac: ``balanced`` or ``immediate``."""
    return memory_modes.machine_mode(machine_bytes())


def resolved_mode(mode: str) -> str:
    """The mode actually in force, with ``auto`` answered by this machine."""
    return memory_modes.resolved_mode(mode, machine_mode())


def idle_seconds(mode: str) -> int | None:
    """The threshold ``mode`` runs with; never ``None`` for ``auto``."""
    return memory_modes.idle_seconds(mode, machine_mode())


def options() -> list[dict]:
    """The five modes, each with its own resolved threshold and a sentence to act on."""
    return memory_modes.options(machine_mode())


def default_mode() -> str:
    """The mode selected when the operator has not chosen one: ``auto``."""
    return DEFAULT_MODE


class MemoryPolicy:
    def __init__(self, data_dir: Path, engine) -> None:
        self._engine = engine
        # This worker's own state directory, public because it is more than the policy's: the
        # per-model defaults file lives beside this one, and the API layer needs the same answer
        # to "where does this worker keep its settings" rather than a second copy of the path.
        self.data_dir = Path(data_dir)
        self._settings = self.data_dir / "memory_policy.json"
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

    def machine_mode(self) -> str:
        """What ``auto`` resolves to on the Mac this worker runs on."""
        return memory_modes.machine_mode(machine_bytes())

    def resolved_mode(self) -> str:
        """The mode actually in force, with ``auto`` answered by this machine."""
        return memory_modes.resolved_mode(self.mode(), self.machine_mode())

    def idle_seconds(self) -> int | None:
        """The threshold in force; never ``None`` when the selection is ``auto``."""
        return MODES[self.resolved_mode()]["idle_seconds"]

    def options(self) -> list[dict]:
        """Every mode, each with its own resolved threshold and a sentence to act on."""
        return memory_modes.options(self.machine_mode())

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
        machine = self.machine_mode()
        resolved = memory_modes.resolved_mode(mode, machine)
        threshold = MODES[resolved]["idle_seconds"]
        loaded = self._engine.loaded()
        idle_for = None
        last = self._engine.last_activity_at()
        if loaded and last is not None:
            idle_for = max(0.0, time.time() - last)
        next_release = None
        if loaded and threshold is not None and threshold > 0 and idle_for is not None:
            next_release = time.time() + max(0.0, threshold - idle_for)
        return {
            "mode": mode,
            # The resolution, not just the name: `mode` may be "auto", `resolved_mode` is
            # what is actually in force here, and `idle_seconds` is its threshold — never
            # None for auto, which a UI would otherwise render as "keeps the model loaded
            # until you release it".
            "resolved_mode": resolved,
            "default_mode": default_mode(),
            "idle_seconds": threshold,
            "machine_mode": machine,
            "machine_bytes": machine_bytes(),
            "options": memory_modes.options(machine),
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
        # The threshold in force: `auto` answers with this Mac's mode, so the timer idles out
        # exactly where `status()` says it will.
        idle_seconds = MODES[memory_modes.resolved_mode(mode, self.machine_mode())]["idle_seconds"]
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
