"""Restart resilience for the standalone worker.

This module used to drive `components/voice/voicestudio-watchdog.sh`, the shell
watchdog of the retired Pinokio service model: a state file, a `curl` health
probe, and `launchctl kickstart` after three consecutive failures. That script is
not part of the standalone app, so the tests could only ever fail on a missing
file.

The property it guarded still exists and is worth guarding — a worker must not
restart on a single transient failure, and a success must clear the streak — but
it now lives in `GenerationManager`: memory failures are counted, caches are
evicted, the packaged worker deliberately does not restart itself because the
app host process supervises it, and a promoted output resets the streak. The
tests below assert that contract instead. Two tests are skipped: the old
shell-script surface (an environment-variable threshold override and a
repo-local state file) has no standalone equivalent.
"""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from backend import generation


def _manager(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> generation.GenerationManager:
    manager = object.__new__(generation.GenerationManager)
    manager._lock = threading.RLock()
    manager._jobs = {}
    manager._consecutive_memory_failures = 0
    manager._restart_scheduled = False
    manager._last_memory_event = None
    monkeypatch.setattr(manager, "_evict_loaded_models", lambda *a, **k: {})
    monkeypatch.setattr(manager, "_persist", lambda *a, **k: None)
    # The real restart path ends in `os._exit(75)` after a short delay. It must be
    # visible to the test without being able to kill the test runner.
    monkeypatch.setattr(generation.threading, "Timer", _RecordingTimer)
    _RecordingTimer.armed.clear()
    return manager


class _RecordingTimer:
    """Stands in for the self-restart timer without ever firing it."""

    armed: list[tuple[float, object]] = []

    def __init__(self, interval: float, function, *args, **kwargs) -> None:
        type(self).armed.append((interval, function))

    def start(self) -> None:
        return None

    def cancel(self) -> None:
        return None


def _job(job_id: str) -> generation.GenerationJob:
    return generation.GenerationJob(job_id=job_id, mode="txt2speech", params={})


def test_restart_requires_consecutive_memory_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One failure is not enough; the threshold admits exactly that many."""
    manager = _manager(monkeypatch, tmp_path)
    monkeypatch.setattr(manager, "_service_installed", staticmethod(lambda: True))

    for attempt in range(1, generation.MEMORY_RESTART_FAILURES):
        manager._record_memory_failure(_job(f"fail-{attempt}"), RuntimeError("out of memory"))
        assert manager.memory_status()["consecutive_failures"] == attempt
        assert manager.memory_status()["restart_scheduled"] is False

    manager._record_memory_failure(_job("fail-last"), RuntimeError("out of memory"))

    status = manager.memory_status()
    assert status["consecutive_failures"] == generation.MEMORY_RESTART_FAILURES
    assert status["restart_scheduled"] is True
    assert status["last_event"]["job_id"] == "fail-last"
    # The supervised restart is armed exactly once, not per failure.
    assert len(_RecordingTimer.armed) == 1


def test_packaged_worker_never_restarts_itself_because_the_host_supervises_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The standalone worker exits for nobody: its host process owns recovery."""
    assert generation.GenerationManager._service_installed() is False

    manager = _manager(monkeypatch, tmp_path)
    for attempt in range(generation.MEMORY_RESTART_FAILURES + 2):
        manager._record_memory_failure(_job(f"unsupervised-{attempt}"), RuntimeError("oom"))

    status = manager.memory_status()
    assert status["consecutive_failures"] == generation.MEMORY_RESTART_FAILURES + 2
    assert status["restart_scheduled"] is False
    assert status["service_supervised"] is False


def test_a_promoted_output_resets_the_failure_streak(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager = _manager(monkeypatch, tmp_path)
    monkeypatch.setattr(manager, "_service_installed", staticmethod(lambda: True))
    manager._record_memory_failure(_job("before-success"), RuntimeError("oom"))
    assert manager.memory_status()["consecutive_failures"] == 1

    partial = tmp_path / "partial.wav"
    final = tmp_path / "final.wav"
    partial.write_bytes(b"RIFF-bytes")
    job = _job("recovered")
    job.partial_output_path = str(partial)

    assert manager._promote_or_cancel_output(job, partial, final) == final
    assert job.state == "done"
    assert final.exists()
    assert manager.memory_status()["consecutive_failures"] == 0


def test_a_cancelled_output_does_not_count_as_a_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Cancellation must not clear a real failure streak."""
    manager = _manager(monkeypatch, tmp_path)
    monkeypatch.setattr(manager, "_service_installed", staticmethod(lambda: True))
    manager._record_memory_failure(_job("before-cancel"), RuntimeError("oom"))

    partial = tmp_path / "partial.wav"
    partial.write_bytes(b"RIFF-bytes")
    job = _job("cancelled")
    job.cancel_event.set()

    assert manager._promote_or_cancel_output(job, partial, tmp_path / "final.wav") is None
    assert job.state == "cancelled"
    assert manager.memory_status()["consecutive_failures"] == 1


@pytest.mark.skip(
    reason=(
        "The threshold is the MEMORY_RESTART_FAILURES constant, not an environment "
        "variable: the VOICESTUDIO_WATCHDOG_FAILURES_REQUIRED surface belonged to the "
        "removed shell watchdog and has no standalone equivalent."
    )
)
def test_watchdog_accepts_a_valid_numeric_threshold_override() -> None:  # pragma: no cover
    pass


@pytest.mark.skip(
    reason=(
        "There is no shell watchdog to parse a threshold, so there is no invalid "
        "override to reject; the standalone worker has no env-var tuning surface."
    )
)
@pytest.mark.parametrize("invalid_value", ["", "not-a-number", "1", "-3"])
def test_watchdog_rejects_invalid_or_unsafe_threshold_overrides(
    invalid_value: str,
) -> None:  # pragma: no cover
    pass


@pytest.mark.skip(
    reason=(
        "The repo-local `service/.watchdog-failures` state file and its .gitignore rule "
        "belonged to the removed shell watchdog; the standalone streak is in-process state."
    )
)
def test_default_state_is_repo_local_and_git_ignored() -> None:  # pragma: no cover
    pass
