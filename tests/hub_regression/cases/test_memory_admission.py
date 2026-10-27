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
    """The shipped Image worker's own row for FLUX.2 Klein 4-bit, as that worker publishes it.

    ``min_free_memory_gb`` is on the row because the worker publishes it there
    (``components/image/app/backend/generation.py``, ``FLUX2_KLEIN``); the Hub keeps no copy.
    """
    return {
        "repo": FLUX_REPO,
        "label": "FLUX.2 klein 4B — MLX 4-bit",
        "min_unified_memory_gb": 16,
        "min_free_memory_gb": 2.8,
        "cache": {"state": "cached"},
    }


def test_transcription_is_subject_to_memory_admission(reset):
    assert memory_admission.applies_to("transcription") is True


def test_flux_4bit_uses_measured_8gb_fleet_default(reset):
    """The 8 GB the fleet measured is enforced, and it now comes from the manifest.

    Owner decision 2026-10-28: the floor's one home is the model's own manifest. FLUX.2 Klein 4-bit
    moved 16 -> 8 there with the measurement in `memory_evidence`, so `_FLEET_DEFAULTS` no longer
    carries the same number a second time (and cannot outrank an edit to the manifest). This used
    to assert `min_total_source == "measured"`; it is now "manifest", which is the more honest of
    the two - the number is one a person can edit, with the evidence beside it.
    """
    policy = memory_admission.describe(FLUX_REPO, _flux_entry())

    assert policy["catalog_min_total_memory_gb"] == 16
    assert policy["default_min_total_memory_gb"] == 8
    assert policy["effective_min_total_memory_gb"] == 8.0
    assert policy["min_total_source"] == "manifest"
    # 2.8 GB of FREE memory before a job starts (not its peak): the owner's choice 2026-10-02, for headroom over the
    # real 8 GB M1+M2 runs, which started cleanly from 3.09 GB free. It is the Image worker's own published floor
    # (`min_free_memory_gb` on its row); the Hub keeps no copy of it any more.
    assert policy["effective_min_free_memory_gb"] == 2.8
    assert policy["default_min_free_memory_gb"] == 2.8
    assert policy["min_free_source"] == "model_default"
    assert policy["source"] == "catalog"
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


def test_the_free_floor_has_one_home_and_it_is_the_models_own_row(reset):
    """The Hub keeps no live-free number of its own; the row is the one place, the override the only other.

    `_FLEET_DEFAULTS` carried 2.8 for FLUX.2 Klein 4-bit and 3.2 for Breeze TTS 2 4-bit *as well
    as* each model's own catalog row, and `describe` gave the table precedence — so an edit to the
    row was silently ignored, and two copies of one measurement could disagree with only one of
    them enforced. The owner's rule (2026-09-24, "nothing about models is hardcoded") is one home
    per number, and for the free floor that home is the model's own published row.
    """
    # The table is empty since 2026-10-28 (its two entries are in the models' own manifests now),
    # and nothing may put a live-free number in it in any case.
    for repo, entry in memory_admission._FLEET_DEFAULTS.items():
        assert "min_free_memory_gb" not in entry, (
            f"{repo} carries a live-free number in the Hub's own table again. The model's own row "
            "(`components/image/app/backend/generation.py`, `components/voice/app/backend/catalog.py`) "
            "is the one home for it, with the operator's override on top.")
    for repo in ("aitrader/flux2-klein-4b-mlx-4bit", "mlx-community/breeze-tts-2-mlx-4bit"):
        assert repo.lower() not in memory_admission._FLEET_DEFAULTS, (
            f"{repo} is back in the Hub's own total-floor table. Its floor is its manifest's "
            f"`qualification.minimum_unified_memory_gb`, with the measurement in that block's "
            f"memory_evidence - one home, so a re-capture is the edit.")

    # An edit to the row IS the edit: nothing in the Hub overrides it back.
    edited = dict(_flux_entry(), min_free_memory_gb=3.4)
    policy = memory_admission.describe(FLUX_REPO, edited)
    assert policy["effective_min_free_memory_gb"] == 3.4
    assert policy["default_min_free_memory_gb"] == 3.4
    assert policy["min_free_source"] == "model_default"
    assert broker._admission_requirements(FLUX_REPO, edited) == {"min_total": 8.0, "min_free": 3.4}

    # A row that publishes none gets the generic floor, and says so instead of claiming the model.
    quiet = memory_admission.describe(FLUX_REPO, {"repo": FLUX_REPO, "min_unified_memory_gb": 16})
    assert quiet["effective_min_free_memory_gb"] == memory_admission.DEFAULT_MIN_FREE_MEMORY_GB
    assert quiet["min_free_source"] == "default"


def test_the_shipped_flux_and_breeze_rows_publish_the_floors_the_fleet_measured(reset):
    """2.8 GB (FLUX.2 Klein 4-bit) and 3.2 GB (Breeze TTS 2 4-bit) still resolve — from the rows now.

    These are the two numbers the Hub used to keep its own copy of. The jobs that ran on 8 GB Macs
    must stay admitted, so the numbers are pinned where they now live.
    """
    from components.image.app.backend import generation as image_generation
    from components.voice.app.backend import catalog as voice_catalog

    klein = image_generation.FLUX2_KLEIN
    assert klein.min_free_memory_gb == 2.8, (
        "FLUX.2 Klein 4-bit no longer publishes the 2.8 GB live-free floor (owner's choice "
        "2026-10-02), so the Hub would fall back to its generic 2.0 GB default")
    klein_row = {"repo": klein.repo,
                 "min_unified_memory_gb": klein.qualified_minimum_memory_gb,
                 "min_free_memory_gb": klein.min_free_memory_gb}
    klein_policy = memory_admission.describe(klein.repo, klein_row)
    assert klein_policy["effective_min_free_memory_gb"] == 2.8
    assert klein_policy["min_free_source"] == "model_default"

    breeze = next(m for m in voice_catalog.CATALOG if m.repo == "mlx-community/Breeze-TTS-2-mlx-4bit")
    published = voice_catalog.serialize_model(breeze)
    assert published["min_free_memory_gb"] == 3.2, (
        "Breeze TTS 2 4-bit no longer publishes the 3.2 GB live-free floor (measured on an 8 GB M1, "
        "2026-10-04), so the Hub would fall back to its generic 2.0 GB default")
    breeze_policy = memory_admission.describe(breeze.repo, published)
    assert breeze_policy["effective_min_free_memory_gb"] == 3.2
    assert breeze_policy["min_free_source"] == "model_default"
    # The 8 GB *total* is a different fact and stays a Hub-side measurement: the manifest still
    # says 16 GB for Breeze, and the manifest is read before the catalog row.
    assert published["min_unified_memory_gb"] == 8
    assert breeze_policy["effective_min_total_memory_gb"] == 8.0


def test_breeze_4bit_runs_on_8gb_macs_from_its_measurement(reset):
    """Owner decision 2026-10-04 from the 8 GB M1 run (each clip started with 3.31-3.46 GB free, peak
    4.69 GB, never critical): 8 GB total and 3.2 GB free to start.

    The 3.2 GB is the row's own published floor and reaches the Hub through the row
    (`workload_policy.required_free_memory_gb`); the Hub keeps no copy of it. The Voice worker's own
    preflight reads the same row, so the two guards cannot disagree about the same job and the Hub
    forwards an operator override only.
    """
    from components.voice.app.backend import catalog as voice_catalog
    from components.voice.app.backend import generation as voice_generation

    repo = "mlx-community/Breeze-TTS-2-mlx-4bit"
    row = {"repo": repo, "min_unified_memory_gb": 8, "min_free_memory_gb": 3.2}
    policy = memory_admission.describe(repo, row)
    # The 8 GB total is the manifest's own number since 2026-10-28 (it said 16 before, and the
    # Hub's `_FLEET_DEFAULTS` carried the measured 8 beside it). One home, so a re-capture of the
    # manifest is the edit.
    assert (policy["effective_min_total_memory_gb"], policy["min_total_source"]) == (8.0, "manifest")
    assert policy["effective_min_free_memory_gb"] == 3.2
    assert policy["min_free_source"] == "model_default"
    assert policy["fleet_default_measured"] is False, (
        "no Hub-side measurement is needed any more: the manifest states the floor dispatch uses")
    requirements = broker._admission_requirements(repo, row)
    assert requirements == {"min_total": 8.0, "min_free": 3.2}
    assert broker._memory_gate(requirements, {"total_gb": 8.59, "available_gb": 3.31})[0] == "run"
    assert broker._memory_gate(requirements, {"total_gb": 8.59, "available_gb": 3.1})[0] == "wait"
    assert broker._memory_gate(requirements, {"total_gb": 4.0, "available_gb": 3.5})[0] == "skip"
    # One floor, two readers, one row: the worker's own preflight asks its row for the same 3.2 the
    # Hub enforces (it used to ask 2.9 + 1.25 = 4.15, which no 8 GB Mac ever had).
    shipped = next(m for m in voice_catalog.CATALOG if m.repo == repo)
    assert voice_generation._required_free_memory_gb(shipped, loaded=False) == 3.2
    # Only an override is forwarded now; the row needs no forwarding.
    assert memory_admission.worker_free_floor_gb(repo) is None
    assert memory_admission.worker_free_floor_gb("mlx-community/VoxCPM2-4bit") is None, \
        "a model with no override keeps the worker's own published floor"


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


def test_the_operator_override_wins_over_the_row_and_reset_returns_to_it(reset):
    """The Models screen's own pair of numbers: what is in force, and whose it is.

    The row is the default (`min_free_source` "model_default"); the operator's override replaces it
    ("operator_override"), and clearing the override returns to the row — never to a Hub-side copy
    of the number, which is what made a row edit invisible.
    """
    entry = dict(_flux_entry(), min_free_memory_gb=3.4)

    changed = memory_admission.set_override(
        FLUX_REPO, min_total_memory_gb=8.0, min_free_memory_gb=1.5, catalog_entry=entry)

    assert changed["effective_min_free_memory_gb"] == 1.5
    assert changed["min_free_source"] == "operator_override"
    assert changed["overridden"] is True
    assert changed["default_min_free_memory_gb"] == 3.4, "the row's number stays visible beside it"
    assert broker._admission_requirements(FLUX_REPO, entry) == {"min_total": 8.0, "min_free": 1.5}
    # The override is also what the Voice worker's own preflight is given; the row's number needs no
    # forwarding, because the worker reads that same row itself.
    assert memory_admission.worker_free_floor_gb(FLUX_REPO) == 1.5

    restored = memory_admission.reset_override(FLUX_REPO, entry)
    assert restored["overridden"] is False
    assert restored["min_free_source"] == "model_default"
    assert restored["effective_min_free_memory_gb"] == 3.4
    assert broker._admission_requirements(FLUX_REPO, entry)["min_free"] == 3.4
    assert memory_admission.worker_free_floor_gb(FLUX_REPO) is None


def test_the_catalog_row_carries_the_free_floor_and_whose_number_it_is(reset, fixture_monitor):
    """What the Models screen reads: the enforced floor and its owner, on the row the app already has.

    The app reads `/api/hub/catalog` once per poll and builds the Models screen from it, so the
    number in force and whose it is travel with that row rather than in a request per model. The
    keys are `hub_*` on purpose: `workload_policy` reads `min_free_memory_gb` back, so the enforced
    number must never be written into the worker's own key.
    """
    repo = "mlx-community/Breeze-TTS-2-mlx-4bit"
    voice = next(s for s in fixture_monitor.registry if s["id"] == "voice")
    catalogs = [None] * len(fixture_monitor.registry)
    catalogs[fixture_monitor.registry.index(voice)] = {"models": [
        {"repo": repo, "min_unified_memory_gb": 8, "min_free_memory_gb": 3.2}]}

    def row():
        return {r["repo"]: r for r in
                fixture_monitor._assemble_aggregate_catalog(catalogs, [])["models"]}[repo]

    assert row()["hub_free_memory_gb"] == 3.2
    assert row()["hub_free_memory_source"] == "model_default"
    assert row()["min_free_memory_gb"] == 3.2, "the worker's own key is left exactly as it published it"

    memory_admission.set_override(repo, min_total_memory_gb=8.0, min_free_memory_gb=1.5,
                                  catalog_entry={"repo": repo, "min_unified_memory_gb": 8,
                                                 "min_free_memory_gb": 3.2})
    assert row()["hub_free_memory_gb"] == 1.5
    assert row()["hub_free_memory_source"] == "operator_override"
    assert row()["min_free_memory_gb"] == 3.2, "the site-local override is never written into the row"

    memory_admission.reset_override(repo, {})
    assert row()["hub_free_memory_gb"] == 3.2
    assert row()["hub_free_memory_source"] == "model_default"


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


def test_qwen_image_tiers_and_gemma_admit_only_their_qualified_macs(reset):
    """Owner decisions 2026-10-08: Qwen Image 2.1 (native) holds a 24 GB Mac, Qwen Image 2.1
    Low-RAM holds 16 GB and up (8 GB stays out), and Gemma 4 26B stays 32 GB only. Totals are
    what the fleet's Macs report: GiB as decimal GB (8.59 / 17.18 / 25.77 / 34.36)."""
    from components.image.app.backend import generation as image_generation

    native = image_generation.QWEN_IMAGE_21
    low_ram = image_generation.QWEN_IMAGE_21_LOW_RAM
    gemma = "mlx-community/gemma-4-26B-A4B-it-qat-4bit"
    expected = {
        native.repo: (24.0, {8.59: False, 17.18: False, 25.77: True, 34.36: True}),
        low_ram.repo: (16.0, {8.59: False, 17.18: True, 25.77: True, 34.36: True}),
        gemma: (32.0, {17.18: False, 25.77: False, 34.36: True}),
    }
    workers = {native.repo: native.qualified_minimum_memory_gb,
               low_ram.repo: low_ram.qualified_minimum_memory_gb, gemma: None}
    for repo, (floor, by_total) in expected.items():
        row = {"repo": repo}
        if workers[repo] is not None:
            row["min_unified_memory_gb"] = workers[repo]
        assert memory_admission.requirement_gb(repo, row, use_override=False) == (floor, "manifest")
        for total, eligible in by_total.items():
            published = memory_admission.publish_floor(dict(row, memory_eligible=True), total)
            assert published["memory_eligible"] is eligible, (repo, total)
            assert broker._memory_gate({"min_total": floor}, {
                "total_gb": total, "available_gb": total})[0] == ("run" if eligible else "skip")


def test_a_floor_this_mac_can_never_reach_is_skipped_not_waited_on(reset):
    """A Mac whose whole RAM is below the free floor is not a candidate; a short Mac still waits.

    Free memory is always strictly less than the machine's own RAM — macOS and the workers are
    resident — so a floor at or above this Mac's shipped memory can never be met, and `wait` there
    parks the item behind a machine that will never clear it.

    The shape is real and measured: the fleet is ~35 8 GB M1/M2 Macs (`research/owner-hardware.md`)
    and Fish Audio S2 Pro 4-bit publishes an 8.4 GB free-before-start floor
    (`components/voice/app/backend/catalog.py`, measured 2026-10-02 on the 32 GB M2 Max). Job
    `97bdce60-90f4-4f47-b789-073744e87ea7` is the same defect one class up: the Hub answered
    `wait` on `studio-mac-0107-m2-8-256-hub` ("needs about 3.5 GB free and has about 3.4 GB free")
    and `studio-mac-1401-m2-8-256-hub` (3.3 GB) — 126 attempts, 3,068 `INSUFFICIENT_MEMORY`
    hand-backs in `gateway-kh.log.1`, against a Gateway whose only mitigation is a 15-minute
    in-memory cooldown (`fleet.MEMORY_COOLDOWN_S`).

    Totals are what the fleet's Macs report: GiB as decimal GB (8.59 / 17.18).
    """
    fish_floor = 8.4
    requirements = {"min_total": 8.0, "min_free": fish_floor}

    # An 8 GB Mac: the floor is above its whole memory, so it is not a candidate at all.
    decision, note = broker._memory_gate(requirements, {"total_gb": 8.59, "available_gb": 3.4})
    assert decision == "skip", note
    assert "8.4" in note and "8GB" in note

    # A 16 GB Mac can hold the floor; being short right now is a wait, not a skip.
    assert broker._memory_gate(requirements, {"total_gb": 17.18, "available_gb": 3.4})[0] == "wait"
    assert broker._memory_gate(requirements, {"total_gb": 17.18, "available_gb": 9.0})[0] == "run"

    # The floor that actually refused in production is below an 8 GB Mac's memory, so it is a
    # wait there — which is what makes the class check a guard and not a substitute for the floor
    # being the right number (`_required_free_memory_gb` in the Voice worker, VoxCPM2 4-bit).
    voxcpm = {"min_total": 8.0, "min_free": 3.55}
    assert broker._memory_gate(voxcpm, {"total_gb": 8.59, "available_gb": 3.4})[0] == "wait"


def test_memory_class_is_the_shipped_size_not_the_reported_total(reset):
    """The number a floor is judged against is the Mac's shipped memory, not its byte total."""
    assert broker.memory_class_gb(8.59) == 8.0
    assert broker.memory_class_gb(17.18) == 16.0
    assert broker.memory_class_gb(25.77) == 24.0
    assert broker.memory_class_gb(34.36) == 32.0
    assert broker.memory_class_gb(None) is None
    assert broker.mac_class("Apple M1", 8.59) == "M1 · 8 GB"


def test_the_voice_worker_and_the_hub_enforce_one_free_floor(reset):
    """One model, one admission floor — the invariant the VoxCPM2 4-bit split broke.

    The Voice worker publishes its row's measured floor under BOTH names the Hub reads:
    `min_free_memory_gb` from the catalog row (`catalog.serialize_model`) and
    `required_free_memory_gb` from `model_runtime_status`. `workload_policy` takes `max` over the
    two, so a *formula* published beside the measured number is a second, stricter gate — the Hub
    admitted 8 GB Macs the worker then refused ("needs about 3.5 GB free and has about 3.4 GB
    free", job `97bdce60`, 3,068 INSUFFICIENT_MEMORY hand-backs).

    The row's own measurement, for the record: an 8 GB M1+M2 started cleanly from 3.25 GB free,
    peak 4.30-4.41 GB, ~0 swap (owner's choice 2026-10-02, `min_free_memory_note`).
    """
    row = {"repo": "mlx-community/VoxCPM2-4bit", "min_unified_memory_gb": 16,
           "min_free_memory_gb": 2.8, "required_free_memory_gb": 2.8}
    policy = memory_admission.describe("mlx-community/VoxCPM2-4bit", row)
    assert policy["effective_min_free_memory_gb"] == 2.8
    assert broker._admission_requirements("mlx-community/VoxCPM2-4bit", row)["min_free"] == 2.8
    # An 8 GB Mac sitting at the free memory the measurement recorded is admitted, not refused.
    assert broker._memory_gate({"min_total": 8.0, "min_free": 2.8},
                               {"total_gb": 8.59, "available_gb": 3.4})[0] == "run"
    # The size formula's 3.55 (2.3 GB of weights + 1.25 GB headroom) is not a floor any more.
    assert broker._memory_gate({"min_total": 8.0, "min_free": 3.55},
                               {"total_gb": 8.59, "available_gb": 3.4})[0] == "wait"
