"""Core memory admission contracts; packaged management routes are excluded.

Owner decision 2026-09-30: a model's RAM requirement is a hard eligibility floor (measured peak,
else manifest, else catalog; an operator override wins). These cases used to pin the opposite
premise - "a tier is not a wall, the effective floor is always 0" - so each one below says what
its old assertion assumed and why it changed.
"""

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

    # The catalog says 16 GB and the fleet measured 8 GB. The measurement is the requirement
    # (it outranks the manifest and the catalog), and it is now enforced: this used to assert an
    # effective floor of 0.0 because a tier was advice, not a wall.
    assert policy["catalog_min_total_memory_gb"] == 16
    assert policy["default_min_total_memory_gb"] == 8
    assert policy["effective_min_total_memory_gb"] == 8.0
    assert policy["min_total_source"] == "measured"
    # 2.8 GB of FREE memory before a job starts (not its peak): the owner's choice 2026-10-02, for headroom over the
    # real 8 GB M1+M2 runs, which started cleanly from 3.09 GB free. The fleet default outranks the catalog for this,
    # so it must carry the 2.8 itself.
    assert policy["effective_min_free_memory_gb"] == 2.8
    assert policy["default_min_free_memory_gb"] == 2.8
    assert policy["source"] == "fleet_default"
    assert policy["overridden"] is False
    requirements = broker._admission_requirements(FLUX_REPO, _flux_entry())
    assert requirements == {"min_total": 8.0, "min_free": 2.8}
    assert broker._memory_gate(
        requirements, {"total_gb": 8.59, "available_gb": 2.4}
    )[0] == "wait"
    assert broker._memory_gate(
        requirements, {"total_gb": 8.0, "available_gb": 2.7}
    )[0] == "wait"
    assert broker._memory_gate(
        requirements, {"total_gb": 8.0, "available_gb": 2.8}
    )[0] == "run"
    assert broker._memory_gate(
        requirements, {"total_gb": 8.0, "available_gb": 3.1}
    )[0] == "run"
    # The same 2.8 holds when the worker's own catalog row publishes it (the shipped image row does).
    published = dict(_flux_entry(), min_free_memory_gb=2.8)
    assert memory_admission.describe(FLUX_REPO, published)["effective_min_free_memory_gb"] == 2.8
    # The old case dispatched to a Mac "far below every published tier". Below the *measured*
    # 8 GB is now refused outright (a 4 GB Mac is not a candidate), never queued behind memory.
    assert broker._memory_gate(
        requirements, {"total_gb": 4.0, "available_gb": 3.0}
    )[0] == "skip"


def test_a_model_is_refused_a_mac_below_its_requirement(reset):
    """Was `test_no_model_is_refused_for_the_size_of_the_machine`: its premise (24 GB is a tier a
    16 GB Mac may still try) is reversed - with nothing measured and no manifest, the catalog's
    24 GB is the requirement and a 16 GB Mac is skipped, not run."""
    entry = {"repo": "org/other-model", "min_unified_memory_gb": 24}
    policy = memory_admission.describe(entry["repo"], entry)

    assert policy["catalog_min_total_memory_gb"] == 24
    assert policy["effective_min_total_memory_gb"] == 24.0
    assert policy["min_total_source"] == "catalog"
    assert policy["effective_min_free_memory_gb"] == 2
    assert policy["source"] == "catalog"
    requirements = {"min_total": 24.0, "min_free": 2}
    assert broker._memory_gate(requirements, {"total_gb": 17.2, "available_gb": 10})[0] == "skip"
    # A Mac that meets the floor still waits on the live free-memory guard.
    assert broker._memory_gate(requirements, {"total_gb": 25.8, "available_gb": 4})[0] == "run"
    assert broker._memory_gate(requirements, {"total_gb": 25.8, "available_gb": 1})[0] == "wait"


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
    assert changed["min_total_source"] == "operator_override"
    stored = json.loads(memory_admission.SETTINGS_FILE.read_text())
    assert stored[FLUX_REPO.lower()]["min_total_memory_gb"] == 7.5

    memory_admission.reset_for_tests()
    assert memory_admission.describe(FLUX_REPO, entry)[
        "effective_min_total_memory_gb"
    ] == 7.5

    reset_policy = memory_admission.reset_override(FLUX_REPO, entry)
    assert reset_policy["source"] == "fleet_default"
    # Back to the measured 8 GB requirement. This used to assert 0.0 ("no floor in force: only an
    # explicit override can put one back"); the requirement is always in force now and the
    # override is only the operator's way to change the number.
    assert reset_policy["effective_min_total_memory_gb"] == 8.0
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
    # The Qwen3-TTS 0.6B manifest qualifies it at 8 GB, so 8 GB is the requirement (was 0.0).
    assert policy["effective_min_total_memory_gb"] == 8.0
    assert policy["min_total_source"] == "manifest"
    assert policy["effective_min_free_memory_gb"] == 3.2
    # `catalog`, not `fleet_default`: the floor is the model's own published number now. The
    # compatibility string `fleet_default` used to mean "the Hub applied a production floor",
    # which is precisely the shape this release removed.
    assert policy["source"] == "catalog"
    assert policy["default_reason"] == (
        "No fleet measurement names this model; its worker publishes a 3.20 GB live-free floor "
        "and that is what is enforced")
    requirements = broker._admission_requirements(repo, entry)
    assert requirements == {"min_total": 8.0, "min_free": 3.2}
    assert broker._memory_gate(requirements, {"total_gb": 8, "available_gb": 4})[0] == "run"
    assert broker._memory_gate(requirements, {"total_gb": 8, "available_gb": 2.5})[0] == "wait"


def test_the_owner_approved_floor_has_a_producer_home(reset):
    """The live-free floor must come from the worker's own row, so a new model needs no Hub edit.

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
    assert published["min_free_memory_gb"] == 2.8, (
        "the Voice worker no longer publishes the 2.8 GB live-free floor (owner's choice 2026-10-02) for "
        f"{row.repo}; the Hub now has no source for it and would fall back to 2.0 GB")
    assert published["min_unified_memory_gb"] == 8
    # And the Hub reads exactly that, with no table of its own.
    assert not hasattr(workload_policy, "_HUB_PRODUCTION_FREE_GB")
    assert not hasattr(workload_policy, "_HUB_PRODUCTION_TOTAL_GB")


def _shipped_worker_rows():
    """Every model row a shipped Voice, Image or Music worker publishes, as it publishes its tier."""
    from components.image.app.backend import generation as image_generation
    from components.music.app.backend import catalog as music_catalog
    from components.voice.app.backend import catalog as voice_catalog

    rows = [voice_catalog.serialize_model(m) for m in voice_catalog.CATALOG]
    rows += [music_catalog.serialize_model(m) for m in music_catalog.CATALOG]
    rows += [{"repo": m.repo, "min_unified_memory_gb": m.qualified_minimum_memory_gb}
             for m in vars(image_generation).values() if type(m).__name__ == "ImageModel"]
    return rows


def test_published_tier_and_eligibility_are_the_admission_floor_for_every_shipped_model(reset):
    """0.6.143: Voice published 16 GB for VoxCPM2 4-bit while dispatch enforced its manifest's
    8 GB, so all 33 8 GB Macs read `memory_eligible: false` and capacity counted only 16 GB Macs.
    Image published 16 GB for FLUX.2 Klein (measured 8) and Z-Image-Turbo (manifest 8). The Hub
    now publishes the floor it enforces, whatever literal a worker carries."""
    rows = _shipped_worker_rows()
    assert {"mlx-community/VoxCPM2-4bit", FLUX_REPO,
            "filipstrand/Z-Image-Turbo-mflux-4bit"} <= {row["repo"] for row in rows}
    for row in rows:
        floor = memory_admission.requirement_gb(row["repo"], row, use_override=False)[0]
        for total in (8.59, 17.18, 25.77, 34.36):
            # The worker's own verdict, from its own tier (Voice's rule).
            tier = row.get("min_unified_memory_gb")
            worker = dict(row, memory_eligible=tier is None or total >= tier)
            published = memory_admission.publish_floor(worker, total)
            if floor is None:
                assert published == worker
                continue
            assert published["min_unified_memory_gb"] == floor, row["repo"]
            assert published["memory_eligible"] is (total >= floor), (row["repo"], total)
            # Idempotent: a published row read back gives the same floor.
            assert memory_admission.requirement_gb(row["repo"], published, use_override=False)[0] == floor


def test_voxcpm2_4bit_is_published_eligible_on_an_8gb_mac(reset, fixture_monitor):
    repo = "mlx-community/VoxCPM2-4bit"
    fixture_monitor.status["voice"]["health"] = {"memory": {"total_gb": 8.59}}
    voice = next(s for s in fixture_monitor.registry if s["id"] == "voice")
    image = next(s for s in fixture_monitor.registry if s["id"] == "image")
    catalogs = [None] * len(fixture_monitor.registry)
    catalogs[fixture_monitor.registry.index(voice)] = {"models": [
        {"repo": repo, "min_unified_memory_gb": 16, "memory_eligible": False}]}
    # Image's `memory_eligible` is "Apple Silicon, macOS 14": no floor overrules a false one.
    catalogs[fixture_monitor.registry.index(image)] = {"models": [
        {"repo": FLUX_REPO, "min_unified_memory_gb": 16, "memory_eligible": False}]}
    fixture_monitor.status["image"]["health"] = {"memory": {"total_gb": 17.18}}
    rows = {row["repo"]: row for row in
            fixture_monitor._assemble_aggregate_catalog(catalogs, [])["models"]}
    assert rows[repo]["min_unified_memory_gb"] == 8.0
    assert rows[repo]["memory_eligible"] is True
    assert rows[FLUX_REPO]["min_unified_memory_gb"] == 8.0
    assert rows[FLUX_REPO]["memory_eligible"] is False
