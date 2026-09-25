"""The Voice worker's hardware probe must read the machine it is running on.

Every model's `fit` verdict starts here, and so does the below-tier warning the Audio page draws.
The probe called a bare `sysctl`, which lives in `/usr/sbin` — **not on PATH** for a packaged
worker launched by the Hub or from Finder, and not on PATH in this project's own development shell
either. `_sysctl` swallowed the failure and returned None, so `detect_memory_gb()` and
`detect_chip()` were both None and **every model's fit verdict read "unknown", fleet-wide, for as
long as that has been true.** The warning could never have fired even once it was published.

This is the same defect the chat tier harness carries a warning about: a bare `sysctl` that failed
silently once made a swap sampler report a peak of 0 MB, which looked like a measurement. A probe
that cannot fail loudly must at least be found by absolute path.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend import system_info  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_probe_caches():
    """Both probes are `lru_cache`d, so a case would otherwise read the case before it."""
    system_info.detect_memory_gb.cache_clear()
    system_info.detect_chip.cache_clear()
    yield
    system_info.detect_memory_gb.cache_clear()
    system_info.detect_chip.cache_clear()


def test_the_memory_probe_reads_this_machine() -> None:
    assert system_info.detect_memory_gb() is not None, (
        "the probe read no memory on a machine that has some; every fit verdict depends on it")


def test_the_probe_survives_a_path_without_sysctl(monkeypatch) -> None:
    """The case that was live: a minimal PATH, which is what a packaged worker gets."""
    monkeypatch.setenv("PATH", "/nonexistent")
    assert system_info.detect_memory_gb() is not None, (
        "`sysctl` is not on PATH for a packaged worker; it has to be looked up by absolute path")
    assert system_info.detect_chip() is not None


def test_a_below_floor_model_is_told_and_still_offered(monkeypatch) -> None:
    """The verdict the Audio page now draws: advice, never a refusal."""
    monkeypatch.setattr(system_info, "detect_memory_gb", lambda: 8)
    risky = system_info.fit_for(16)
    assert risky["state"] == "risky"
    # Both numbers, so the operator can see what they have and what it wants.
    assert "8" in risky["hint"] and "16" in risky["hint"]
    # And a host that meets the floor is not warned at all.
    monkeypatch.setattr(system_info, "detect_memory_gb", lambda: 64)
    assert system_info.fit_for(16)["state"] == "ok"
