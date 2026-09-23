"""Model health is a closed vocabulary derived from direct evidence.

Absence has to stay absent: a row with no evidence is ``unknown``, never a
plausible "ready", and every state comes from the published set.
"""
from __future__ import annotations

import time

ROW_KEYS = {"model_id", "machine", "state", "reason", "hint", "observed_at"}


def _row(repo: str, **extra) -> dict:
    row = {
        "repo": repo,
        "label": repo,
        "cache": {"state": "cached"},
        "available": True,
        "runtime_ready": True,
        "memory_eligible": True,
    }
    row.update(extra)
    return row


def test_each_evidence_condition_maps_to_one_closed_vocabulary_state(fixture_monitor):
    from components.hub.backend import model_health

    monitor = fixture_monitor
    monitor._catalog_cache["image"] = (time.time(), {"models": [
        _row("fixture/ready"),
        _row("fixture/uncached", cache={"state": "missing"}, available=False),
        _row("fixture/runtime", available=False, runtime_ready=False),
        _row("fixture/memory", available=False, memory_eligible=False),
        {"repo": "fixture/unknown", "cache": {"state": "cached"}},
    ]})

    rows = model_health.snapshot(monitor)
    by_id = {row["model_id"]: row for row in rows}
    assert by_id["fixture/ready"]["state"] == "ready"
    assert by_id["fixture/uncached"]["state"] == "download_required"
    assert by_id["fixture/runtime"]["state"] == "runtime_missing"
    assert by_id["fixture/memory"]["state"] == "insufficient_memory"
    # Nothing said this model was usable, so it must not be reported as ready.
    assert by_id["fixture/unknown"]["state"] == "unknown"
    for row in rows:
        assert set(row) == ROW_KEYS
        assert row["state"] in model_health.STATES
        assert row["machine"] == "local"


def test_a_missing_evidence_row_keeps_null_reason_and_hint(fixture_monitor):
    from components.hub.backend import model_health

    monitor = fixture_monitor
    now = time.time()
    monitor._catalog_cache["image"] = (now, {"models": [
        {"repo": "fixture/no-evidence", "cache": {"state": "cached"}},
    ]})
    row = next(r for r in model_health.snapshot(monitor)
               if r["model_id"] == "fixture/no-evidence")
    assert row["state"] == "unknown"
    assert row["reason"] is None
    assert row["hint"] is None
    assert isinstance(row["observed_at"], float)


def test_a_paused_model_is_quarantined_with_its_own_reason(fixture_monitor):
    from components.hub.backend import broker, model_health

    monitor = fixture_monitor
    monitor._catalog_cache["voice"] = (time.time(), {"models": [
        _row("fixture/paused"),
    ]})
    broker._model_protection[("voice", "fixture/paused")] = {
        "studio": "voice", "model": "fixture/paused", "blocked": True,
        "reason": "weights failed to load", "error_code": "MODEL_FILES_UNAVAILABLE",
        "failures": 3,
    }
    row = next(r for r in model_health.snapshot(monitor)
               if r["model_id"] == "fixture/paused")
    assert row["state"] == "quarantined"
    assert row["reason"] == "MODEL_FILES_UNAVAILABLE"
    assert row["hint"] == "weights failed to load"


def test_the_readiness_os_gate_is_used(fixture_monitor, monkeypatch):
    from pathlib import Path

    from components.hub import standalone_config
    from components.hub.backend import model_health

    monkeypatch.setenv(
        "STUDIOHUB_MODEL_MANIFESTS",
        str(Path(__file__).resolve().parents[3] / "model-manifests"),
    )
    monitor = fixture_monitor
    monitor._catalog_cache["image"] = (time.time(), {"models": [
        _row("AITRADER/FLUX2-klein-4B-mlx-4bit"),
    ]})
    monkeypatch.setattr(standalone_config.platform, "mac_ver",
                        lambda: ("10.0", "", ""))
    row = next(r for r in model_health.snapshot(monitor)
               if r["model_id"] == "AITRADER/FLUX2-klein-4B-mlx-4bit")
    assert row["state"] == "unsupported_os"
    assert row["reason"] == "macos_below_minimum"


def test_the_readiness_chip_gate_is_used(fixture_monitor, monkeypatch):
    from pathlib import Path

    from components.hub import standalone_config
    from components.hub.backend import model_health

    monkeypatch.setenv(
        "STUDIOHUB_MODEL_MANIFESTS",
        str(Path(__file__).resolve().parents[3] / "model-manifests"),
    )
    monitor = fixture_monitor
    monitor._catalog_cache["image"] = (time.time(), {"models": [
        _row("AITRADER/FLUX2-klein-4B-mlx-4bit"),
    ]})
    monkeypatch.setattr(standalone_config.platform, "mac_ver",
                        lambda: ("27.0", "", ""))
    monkeypatch.setattr(standalone_config.platform, "processor",
                        lambda: "intel")
    row = next(r for r in model_health.snapshot(monitor)
               if r["model_id"] == "AITRADER/FLUX2-klein-4B-mlx-4bit")
    assert row["state"] == "unsupported_chip"
    assert row["reason"] == "chip_unsupported"
