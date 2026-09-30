"""Capability publication from direct worker evidence, not the audit gate.

A worker's catalogue row already says which models it serves. These cases prove
that supply and per-operation capacity are populated from that evidence, that a
machine count can never exceed the number of joined machines, and that no
machine fitting a model stays an honest zero rather than an invented value.
"""
from __future__ import annotations

import asyncio
import time


def _row(repo: str, **extra) -> dict:
    row = {
        "repo": repo,
        "label": repo.rsplit("/", 1)[-1],
        "family": "fixture",
        "cache": {"state": "cached"},
        "available": True,
        "runtime_ready": True,
        "memory_eligible": True,
    }
    row.update(extra)
    return row


def _serve(monitor, studio: str, models: list[dict], *, transcription: bool = False) -> None:
    monitor.status[studio] = {
        "status": "up", "last_seen": time.time(), "last_checked": time.time(),
        "app_version": "0.6.47", "health": {}, "health_busy": False,
    }
    cache = monitor._transcribe_cache if transcription else monitor._catalog_cache
    payload = {"models": models}
    if transcription:
        payload["available"] = True
    cache[studio] = (time.time(), payload)


def _build(monitor):
    from components.hub.backend import capabilities, control_plane
    return asyncio.run(capabilities.build_snapshot(
        monitor,
        app_version="0.6.47",
        settings=control_plane.public_settings(),
        readiness={"ready": True},
        base_capacity={"queue_depth": 0},
    ))


def test_a_serving_studio_publishes_models_operations_and_supply(fixture_monitor):
    monitor = fixture_monitor
    _serve(monitor, "image", [_row("AITRADER/FLUX2-klein-4B-mlx-4bit")])

    snapshot = _build(monitor)
    worker = next(row for row in snapshot["workers"] if row["service_id"] == "image")

    assert worker["models"], "a serving studio must publish the models it serves"
    assert worker["models"][0]["operation"] == "image.text_to_image"
    assert "image.text_to_image" in worker["supported_operations"]

    supply = [row for row in snapshot["model_supply"]
              if row["operation"] == "image.text_to_image"]
    assert supply, "model_supply must contain the local model"
    row = supply[0]
    assert row["internal_model_id"] == "AITRADER/FLUX2-klein-4B-mlx-4bit"
    # One joined machine, so no count can exceed one.
    assert row["machine_ids"] == ["local"]
    for count in ("installed_machine_count", "online_machine_count",
                  "ready_machine_count", "available_physical_slots",
                  "eligible_physical_slots_total"):
        assert 0 <= row[count] <= 1, count

    by_operation = snapshot["capacity"]["by_operation"]["image.text_to_image"]
    assert by_operation["workers_total"] == 1
    assert by_operation["available_worker_slots"] == 1
    assert by_operation["available_physical_machine_slots"] == 1
    assert by_operation["eligible_physical_machine_slots_total"] == 1


def test_a_modality_maps_to_the_peer_operation_vocabulary(fixture_monitor):
    monitor = fixture_monitor
    _serve(monitor, "music", [_row("stabilityai/stable-audio-3-optimized")])
    # A Voice row as the worker publishes it now: `modes` is the producer's own fact.
    _serve(monitor, "voice", [_row("mlx-community/VoxCPM2-4bit", modes=["cloning", "design"])])
    _serve(monitor, "subtitle", [_row("mlx-community/whisper-base-mlx")],
           transcription=True)

    snapshot = _build(monitor)
    operations = {
        row["service_id"]: set(row["supported_operations"])
        for row in snapshot["workers"]
    }
    assert "music.generation" in operations["music"]
    assert "voice.tts" in operations["voice"]
    assert "audio.transcription" in operations["subtitle"]


def test_a_voice_row_is_offered_in_exactly_the_modes_its_catalog_declared(fixture_monitor):
    """The Hub renames the producer's `modes`; it does not re-derive them from the row.

    VoxCPM2 4-bit (MLX) publishes `["cloning", "design"]` and declares no `voice-design`
    capability, so any consumer-side derivation loses the design mode it offers.
    """
    from components.hub.backend import capabilities

    monitor = fixture_monitor
    _serve(monitor, "voice", [_row("mlx-community/VoxCPM2-4bit", modes=["cloning", "design"])])

    snapshot = _build(monitor)
    worker = next(row for row in snapshot["workers"] if row["service_id"] == "voice")
    assert worker["models"][0]["controls"]["voice_modes"] == [
        capabilities.VOICE_CONTROL_FOR_MODE["cloning"],
        capabilities.VOICE_CONTROL_FOR_MODE["design"],
    ]


def test_a_voice_row_with_unpublished_modes_advertises_no_mode_and_no_failure(
        fixture_monitor, caplog):
    """A Voice build that predates the `modes` contract must not blank the site contract.

    Refusing here would take `/api/hub/capabilities` - and every modality's capacity evidence -
    down because one studio is stale, which is the 0.6.57 shape. The row is still published,
    with no voice mode advertised at all, and the model and the file to update are named in
    the log: guessing `preset_voice` is the defect this replaced.
    """
    monitor = fixture_monitor
    _serve(monitor, "voice", [_row("org/voice-worker-from-an-older-release")])

    with caplog.at_level("WARNING", logger="studiohub.capabilities"):
        snapshot = _build(monitor)

    worker = next(row for row in snapshot["workers"] if row["service_id"] == "voice")
    assert "voice_modes" not in worker["models"][0]["controls"]
    assert worker["models"][0]["operation"] == "voice.tts"
    assert snapshot["capacity"]["by_operation"]["voice.tts"]["workers_total"] == 1
    assert any("org/voice-worker-from-an-older-release" in record.getMessage()
               and "components/voice/app/backend/catalog.py" in record.getMessage()
               for record in caplog.records), caplog.text


def test_no_machine_fitting_the_model_is_zero_not_an_invented_value(fixture_monitor):
    monitor = fixture_monitor
    # The catalogue is present, but no studio is answering.
    monitor._catalog_cache["image"] = (
        time.time(), {"models": [_row("AITRADER/FLUX2-klein-4B-mlx-4bit")]},
    )

    snapshot = _build(monitor)
    worker = next(row for row in snapshot["workers"] if row["service_id"] == "image")
    assert worker["models"], "evidence of the model is still published"
    assert worker["available_capacity"]["slots"] == 0

    row = next(row for row in snapshot["model_supply"]
               if row["operation"] == "image.text_to_image")
    assert row["ready_machine_count"] == 0
    assert row["available_physical_slots"] == 0
    assert row["online_machine_count"] == 0

    by_operation = snapshot["capacity"]["by_operation"]["image.text_to_image"]
    assert by_operation["available_worker_slots"] == 0
    assert by_operation["available_physical_machine_slots"] == 0
    assert snapshot["capacity"]["available_physical_machine_slots"] == 0
