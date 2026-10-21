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


def test_a_standing_by_lite_mode_mac_is_not_capacity_until_it_is_free(fixture_monitor, monkeypatch):
    """D5 (owner, 2026-10-04): a Lite Mode Mac standing by, stepped aside or in quiet hours takes no
    new work, so it is not in the group's eligible total either - counting it let the Gateway queue
    work here for a Mac that was not coming. It re-enters the moment it is free again."""
    from components.hub.backend import peers

    monitor = fixture_monitor
    _serve(monitor, "image", [_row("AITRADER/FLUX2-klein-4B-mlx-4bit")])
    for state in sorted(peers.STANDING_BY_STATES):
        monkeypatch.setattr(peers, "_workstation", lambda machine, state=state: {"state": state})
        snapshot = _build(monitor)
        supply = next(row for row in snapshot["model_supply"] if row["operation"] == "image.text_to_image")
        assert supply["eligible_physical_slots_total"] == 0, state
        assert snapshot["capacity"]["by_operation"]["image.text_to_image"][
            "eligible_physical_machine_slots_total"] == 0, state

    monkeypatch.setattr(peers, "_workstation", lambda machine: {"state": "working"})
    snapshot = _build(monitor)
    supply = next(row for row in snapshot["model_supply"] if row["operation"] == "image.text_to_image")
    assert supply["eligible_physical_slots_total"] == 1
    assert snapshot["capacity"]["by_operation"]["image.text_to_image"][
        "eligible_physical_machine_slots_total"] == 1


def _leaf_capability(*, qualification: bool = True) -> dict:
    """One joined leaf's render row, exactly as its executor publishes it."""
    return {
        "operation": "video.final_render",
        "physical_machine_id": "local",
        "hardware_class": "m4-16gb",
        "profile_digest": "a1" * 32,
        "tools_digest": "b2" * 32,
        "compiler_digest": "c3" * 32,
        "qualification_digest": "d4" * 32,
        "selected_tier": "H.264 · hardware (videotoolbox, q:v 65)",
        "exact_encoder": "h264_videotoolbox",
        "font_support": False,
        "bounds": {
            "max_rss_bytes": 10737418240, "max_wall_seconds": 14400,
            "min_available_memory_bytes": 2147483648, "output_limit_bytes": 8589934592,
        },
        "profile_execution_bounds": {"max_duration_sec": 10800},
        "production_eligible": False,
        "production_enabled": False,
        "available_capacity": {"slots": 0, "slots_total": 1},
        "qualification_candidates": [{
            "vector_id": "intent-1",
            "hardware_class": "m4-16gb",
            "profile_digest": "a1" * 32,
            "tools_digest": "b2" * 32,
            "compiler_digest": "c3" * 32,
            "qualification_digest": "d4" * 32,
            "selected_tier": "H.264 · hardware (videotoolbox, q:v 65)",
            "exact_encoder": "h264_videotoolbox",
            "font_support": False,
            "bounds": {
                "max_rss_bytes": 10737418240, "max_wall_seconds": 14400,
                "min_available_memory_bytes": 2147483648, "output_limit_bytes": 8589934592,
            },
            "profile_execution_bounds": {"max_duration_sec": 10800},
            "production_eligible": False,
            "qualification_only": True,
            "available_capacity": {"slots": 1, "slots_total": 1},
        }] if qualification else [],
    }


def test_a_joined_leaf_render_worker_is_published_as_the_gateway_requires(
        fixture_monitor, monkeypatch):
    """The Gateway reads render workers only from a controller's capabilities.

    A leaf that is not republished here can never be selected, so the row is checked against
    Gateway `fleet.render_candidates`'s own worker and qualification-vector predicates. It
    stays a qualification worker: `production_eligible` is false and the only authority it
    can carry is the live one-use intent.
    """
    from components.hub.backend import fleet_nodes

    monkeypatch.setattr(
        fleet_nodes, "render_capabilities",
        lambda: {"shop-m4": _leaf_capability()})

    worker = next(row for row in _build(fixture_monitor)["workers"]
                  if row["service_id"] == "shop-m4:hub-final-render")

    # Gateway `_trim_worker` copies these through; `render_candidates` then requires them.
    assert worker["studio_type"] == "render"
    assert worker["physical_machine_id"] == "shop-m4"
    assert worker["online"] is True and worker["ready"] is True
    assert worker["busy"] is False and worker["drained"] is False
    assert worker["maintenance"] is False and worker["machine_quarantined"] is False
    assert worker["supported_operations"] == ["video.final_render"]
    assert worker["available_capacity"] == {"slots": 1, "slots_total": 1}

    candidate = worker["qualification_candidates"][0]
    assert set(candidate) == {
        "vector_id", "hardware_class", "profile_digest", "tools_digest",
        "compiler_digest", "qualification_digest", "selected_tier", "exact_encoder",
        "font_support", "bounds", "profile_execution_bounds", "production_eligible",
        "qualification_only", "available_capacity", "encode",
    }
    # The Gateway intersects this fact over the candidate it selects; without it Story
    # sees `encode: null` and keeps the run local instead of using the intent.
    assert candidate["encode"]["format"] == "studio.render.encode-authority.v1"
    assert candidate["encode"]["entries"]
    assert all(entry["requested_effort"] == "hardware"
               for entry in candidate["encode"]["entries"])
    # Advertising what can execute never grants production.
    assert candidate["production_eligible"] is False
    assert candidate["qualification_only"] is True
    assert candidate["available_capacity"] == {"slots": 1, "slots_total": 1}
    assert len(candidate["profile_digest"]) == 64
    assert set(candidate["bounds"]) == {
        "max_rss_bytes", "max_wall_seconds", "min_available_memory_bytes",
        "output_limit_bytes",
    }


def test_a_leaf_with_no_intent_and_no_authority_is_published_then_not_selected(
        fixture_monitor, monkeypatch):
    """Absent authority and a spent intent, the leaf is present and honest, not a candidate."""
    from components.hub.backend import fleet_nodes

    monkeypatch.setattr(
        fleet_nodes, "render_capabilities",
        lambda: {"shop-m4": _leaf_capability(qualification=False)})

    worker = next(row for row in _build(fixture_monitor)["workers"]
                  if row["service_id"] == "shop-m4:hub-final-render")

    assert worker["ready"] is False
    assert worker["drained"] is True
    assert worker["available_capacity"] == {"slots": 0, "slots_total": 1}
    assert worker["qualification_candidates"] == []


def test_a_live_qualification_intent_is_render_ready_without_production_authority(
        fixture_monitor, monkeypatch):
    """Gateway only looks at a candidate when the worker row is ready and not drained.

    A qualification-only Hub has no production authority, so `production_enabled` stays
    false; the live one-use intent is what makes this worker selectable for that one run.
    """
    from components.hub.backend import render_executor

    monkeypatch.setattr(render_executor, "capability", lambda: {
        "operation": "video.final_render",
        "physical_machine_id": "local",
        "hardware_class": None,
        "profile_digest": None,
        "tools_digest": None,
        "compiler_digest": None,
        "qualification_digest": None,
        "selected_tier": None,
        "exact_encoder": None,
        "font_support": False,
        "bounds": None,
        "profile_execution_bounds": None,
        "production_eligible": False,
        "production_enabled": False,
        "available_capacity": {"slots": 0, "slots_total": 1},
        "qualification_candidates": [{
            "available_capacity": {"slots": 1, "slots_total": 1},
        }],
    })

    snapshot = _build(fixture_monitor)
    worker = next(row for row in snapshot["workers"]
                  if row["service_id"] == "hub-final-render")

    assert worker["ready"] is True
    assert worker["drained"] is False
    assert worker["available_capacity"] == {"slots": 1, "slots_total": 1}
    assert worker["supported_operations"] == ["video.final_render"]


def test_a_qualification_candidate_publishes_the_encode_it_was_qualified_for(
        fixture_monitor, monkeypatch):
    """A qualification-only Hub has no worker-level authority, so the candidate answers.

    The Gateway intersects `encode` over the qualification candidate it selects and Story
    keeps the run local on an absent or differing answer. Publishing the candidate's own
    compiler-derived fact is what lets the one-use intent take exactly its qualified run.
    """
    from components.hub.backend import render_executor

    tier = "H.264 · hardware (videotoolbox, q:v 65)"
    monkeypatch.setattr(render_executor, "capability", lambda: {
        "operation": "video.final_render",
        "physical_machine_id": "local",
        "hardware_class": None,
        "profile_digest": None,
        "tools_digest": None,
        "compiler_digest": None,
        "qualification_digest": None,
        "selected_tier": None,
        "exact_encoder": None,
        "font_support": False,
        "bounds": None,
        "profile_execution_bounds": None,
        "production_eligible": False,
        "production_enabled": False,
        "available_capacity": {"slots": 0, "slots_total": 1},
        "qualification_candidates": [{
            "vector_id": "intent-1",
            "selected_tier": tier,
            "exact_encoder": "h264_videotoolbox",
            "production_eligible": False,
            "qualification_only": True,
            "available_capacity": {"slots": 1, "slots_total": 1},
        }],
    })

    worker = next(row for row in _build(fixture_monitor)["workers"]
                  if row["service_id"] == "hub-final-render")

    assert worker["encode"] is None
    candidate = worker["qualification_candidates"][0]
    assert candidate["encode"]["format"] == "studio.render.encode-authority.v1"
    assert {
        (entry["container"], entry["requested_codec"], entry["crf"],
         entry["quality_cpu_preset"])
        for entry in candidate["encode"]["entries"]
    } >= {("mp4", "h264", 23, "medium")}
    assert all(entry["requested_effort"] == "hardware"
               for entry in candidate["encode"]["entries"])
    assert candidate["production_eligible"] is False


def test_a_render_only_joined_leaf_gets_a_machines_row(fixture_monitor, monkeypatch):
    """The Gateway selects by (controller, physical_machine_id) and relays to that machine.

    `render_candidates` refuses any worker whose `physical_machine_id` has no matching
    `machines[]` row (the row must say `reachable`). A leaf that runs no worker studio
    has no machine in `monitor.registry`, so its republished render worker used to stand
    alone and the leaf was invisible to the router even though the worker row read ready.
    The machines list must carry the leaf's row beside the worker row.
    """
    from components.hub.backend import fleet_nodes
    monkeypatch.setattr(
        fleet_nodes, "render_capabilities",
        lambda: {"shop-m4": _leaf_capability()})

    snapshot = _build(fixture_monitor)

    worker = next(row for row in snapshot["workers"]
                  if row["service_id"] == "shop-m4:hub-final-render")
    assert worker["physical_machine_id"] == "shop-m4"

    machine = next(row for row in snapshot["machines"]
                   if row["physical_machine_id"] == "shop-m4")
    assert machine["online"] is True
    assert machine["enabled"] is True
    assert machine["drained"] is False
    assert machine["available_capacity"]["worker_slots"] == 1

    # Every worker row must resolve to a machines row; that is the contract the
    # Gateway's selector holds the render seam to.
    machine_ids = {row["physical_machine_id"] for row in snapshot["machines"]}
    for row in snapshot["workers"]:
        assert row["physical_machine_id"] in machine_ids
