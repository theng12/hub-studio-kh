"""Core memory admission contracts; packaged management routes are excluded."""

import json

from components.hub.backend import broker, memory_admission, workload_policy


FLUX_REPO = "AITRADER/FLUX2-klein-4B-mlx-4bit"


def _flux_entry():
    return {
        "repo": FLUX_REPO,
        "label": "FLUX.2 klein 4B — MLX 4-bit",
        "min_unified_memory_gb": 16,
        "cache": {"state": "cached"},
    }


def test_transcription_is_subject_to_memory_admission(reset):
    assert memory_admission.applies_to("transcription") is True


def test_flux_4bit_uses_measured_8gb_fleet_default(reset):
    policy = memory_admission.describe(FLUX_REPO, _flux_entry())

    # The catalog says 16 GB and the fleet default says 8 GB; both are tiers the
    # operator can read, and neither refuses a smaller Mac.
    assert policy["catalog_min_total_memory_gb"] == 16
    assert policy["default_min_total_memory_gb"] == 8
    assert policy["effective_min_total_memory_gb"] == 0.0
    assert policy["effective_min_free_memory_gb"] == 2
    assert policy["source"] == "fleet_default"
    assert policy["overridden"] is False
    requirements = broker._admission_requirements(FLUX_REPO, _flux_entry())
    assert requirements == {"min_total": 0.0, "min_free": 2}
    assert broker._memory_gate(
        requirements, {"total_gb": 8.59, "available_gb": 2.4}
    )[0] == "run"
    # A Mac far below every published tier is still dispatched to, because the
    # machine's size is the operator's business and not a refusal.
    assert broker._memory_gate(
        requirements, {"total_gb": 8.0, "available_gb": 3.0}
    )[0] == "run"


def test_no_model_is_refused_for_the_size_of_the_machine(reset):
    entry = {"repo": "org/other-model", "min_unified_memory_gb": 24}
    policy = memory_admission.describe(entry["repo"], entry)

    # 24 GB is what the model is qualified at, not what it demands: a 16 GB Mac
    # may run it, and the free-memory guard is what stops a job that cannot start.
    assert policy["catalog_min_total_memory_gb"] == 24
    assert policy["effective_min_total_memory_gb"] == 0.0
    assert policy["effective_min_free_memory_gb"] == 2
    assert policy["source"] == "catalog"
    assert broker._memory_gate(
        {"min_total": 0.0, "min_free": 2}, {"total_gb": 16, "available_gb": 4}
    )[0] == "run"
    assert broker._memory_gate(
        {"min_total": 0.0, "min_free": 2}, {"total_gb": 16, "available_gb": 1}
    )[0] == "wait"


def test_operator_override_persists_and_reset_restores_default(reset):
    entry = _flux_entry()
    changed = memory_admission.set_override(
        FLUX_REPO, min_total_memory_gb=7.5, min_free_memory_gb=1.5,
        catalog_entry=entry,
    )

    assert changed["source"] == "operator_override"
    assert changed["effective_min_total_memory_gb"] == 7.5
    assert changed["effective_min_free_memory_gb"] == 1.5
    assert broker._admission_requirements(FLUX_REPO, entry) == {
        "min_total": 7.5, "min_free": 1.5,
    }
    stored = json.loads(memory_admission.SETTINGS_FILE.read_text())
    assert stored[FLUX_REPO.lower()]["min_total_memory_gb"] == 7.5

    memory_admission.reset_for_tests()
    assert memory_admission.describe(FLUX_REPO, entry)[
        "effective_min_total_memory_gb"
    ] == 7.5

    reset_policy = memory_admission.reset_override(FLUX_REPO, entry)
    assert reset_policy["source"] == "fleet_default"
    # Back to "no floor in force": only an explicit override can put one back.
    assert reset_policy["effective_min_total_memory_gb"] == 0.0
    assert not memory_admission.SETTINGS_FILE.exists()


def test_qwen_base_uses_owner_approved_8gb_floor_with_live_headroom_guard(reset):
    """The owner-approved floor survives — now published by the model, not kept by the Hub.

    This case used to pass against a Hub-side table keyed by the exact repo id
    (`workload_policy._HUB_PRODUCTION_FREE_GB`), which meant the policy existed only for the one
    model somebody had remembered to add. The floor now lives on the model's own row in
    `components/voice/app/backend/catalog.py` and arrives through its catalog row, so the entry
    below carries what that row actually publishes and
    `test_the_owner_approved_floor_has_a_producer_home` proves the producer really says it.
    """
    repo = "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit"
    entry = {"repo": repo, "min_unified_memory_gb": 8, "min_free_memory_gb": 3.2}

    policy = memory_admission.describe(repo, entry)

    assert policy["catalog_min_total_memory_gb"] == 8
    assert policy["effective_min_total_memory_gb"] == 0.0
    assert policy["effective_min_free_memory_gb"] == 3.2
    # `catalog`, not `fleet_default`: the floor is the model's own published number now. The
    # compatibility string `fleet_default` used to mean "the Hub applied a production floor",
    # which is precisely the shape this release removed.
    assert policy["source"] == "catalog"
    assert policy["default_reason"] == (
        "No fleet measurement names this model; its worker publishes a 3.20 GB live-free floor "
        "and that is what is enforced")
    requirements = broker._admission_requirements(repo, entry)
    assert requirements == {"min_total": 0.0, "min_free": 3.2}
    assert broker._memory_gate(requirements, {"total_gb": 8, "available_gb": 4})[0] == "run"
    assert broker._memory_gate(requirements, {"total_gb": 8, "available_gb": 2.5})[0] == "wait"


def test_the_owner_approved_floor_has_a_producer_home(reset):
    """The 3.2 GB floor must come from the worker's own row, so a new model needs no Hub edit.

    Deleting the Hub's model-keyed table is only safe while this holds: the shipped Voice row for
    the 8 GB production voice-cloning tier publishes the owner-approved live-free floor, and its
    tier is published too. A prune that drops the row, or a field rename that stops publishing it,
    would silently put the Hub back on its 2.0 GB default for the fleet's 8 GB machines.
    """
    from components.voice.app.backend import catalog as voice_catalog

    row = next((m for m in voice_catalog.CATALOG
                if m.repo == "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit"), None)
    assert row is not None, "the 8 GB production voice-cloning tier left the voice catalog"
    published = voice_catalog.serialize_model(row)
    assert published["min_free_memory_gb"] == 3.2, (
        "the Voice worker no longer publishes the owner-approved 3.2 GB live-free floor for "
        f"{row.repo}; the Hub now has no source for it and would fall back to 2.0 GB")
    assert published["min_unified_memory_gb"] == 8
    # And the Hub reads exactly that, with no table of its own.
    assert not hasattr(workload_policy, "_HUB_PRODUCTION_FREE_GB")
    assert not hasattr(workload_policy, "_HUB_PRODUCTION_TOTAL_GB")
