"""The published lane contract — checked against the code that enforces it.

`docs/lane-execution-contract.md` is the shape; this module is the voice side of
it. A contract that promises a parameter the worker drops, or a bound the worker
does not clamp, is worse than no contract: a scheduler builds against it and the
failure appears as a silently ignored field.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from backend import catalog, generation, lane_contract, long_form_policy


REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from components import model_defaults  # noqa: E402


def _rows() -> list[dict]:
    return [catalog.serialize_model(model) for model in catalog.CATALOG]


def test_every_catalog_row_publishes_the_lane_contract() -> None:
    rows = _rows()
    assert rows
    for row in rows:
        contract = row["lane_contract"]
        assert contract["schema"] == "studiohub.lane-contract.v1"
        assert contract["modality"] == "voice"
        assert contract["operation"] == "voice.tts"
        assert contract["dispatch"] == lane_contract.DISPATCH_ROUTE
        assert contract["artifact"] == {
            "modality": "voice", "media_type": "audio/wav", "extension": "wav", "per_item": 1,
        }
        # Presentation facts live in `execution_contract`; they must not be
        # folded into the dispatch contract's audience. `text_max_characters`
        # there is the audit's ceiling and is None for an unaudited model, which
        # is exactly the honest value.
        assert "text_max_characters" not in contract
        assert "qualification_source" in row["execution_contract"]


def test_states_and_cancel_are_the_hub_vocabulary_verbatim() -> None:
    """Copied from the spec, which copies `broker` - not paraphrased here."""
    spec = (REPO_ROOT / "docs" / "lane-execution-contract.md").read_text(encoding="utf-8")
    assert json.dumps(lane_contract.STATES["accepted"]) == '["queued"]'
    assert lane_contract.STATES["active"] == ["running", "cancel_requested"]
    assert lane_contract.STATES["terminal"] == ["done", "error", "cancelled"]
    assert lane_contract.STATES["uncertain"] == "uncertain"
    assert lane_contract.STATES["uncertain_is_terminal"] is False
    assert lane_contract.CANCEL["request"] == "DELETE /api/hub/jobs/{batch_id}"
    for fragment in (
        "asks every studio holding an item of this batch to stop",
        "may still land an artifact",
    ):
        assert fragment in lane_contract.CANCEL["semantics"]
        assert fragment in spec


def test_no_worker_restates_the_hubs_deadline() -> None:
    """`deadline_seconds` is the Hub's own proxy timeout, added when it echoes."""
    for row in _rows():
        assert "deadline_seconds" not in row["lane_contract"]


def test_a_model_that_cannot_clone_never_advertises_a_reference_parameter() -> None:
    for row in _rows():
        names = {parameter["name"] for parameter in row["lane_contract"]["parameters"]}
        if "voice-cloning" in row["capabilities"]:
            assert "voice_library_id" in names, row["repo"]
        else:
            assert "voice_library_id" not in names, row["repo"]


# The roster-publishing preset model: Qwen3-TTS CustomVoice. No *shipped* row both offers a
# preset and publishes a roster today — ST-13.B pruned the CustomVoice tier on 2026-09-24 and
# the owner left Pocket TTS 4-bit's roster unbuilt (docs/tasks/ST-20.md) — so the row is built
# from the catalogue's own family here. It is the model this roster belongs to, and a
# CustomVoice tier returning to the catalogue lands on the same contract with no code change.
CUSTOM_VOICE = "mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit"


def _custom_voice_model() -> catalog.ModelEntry:
    return catalog.ModelEntry(
        repo=CUSTOM_VOICE,
        label="Qwen3-TTS 0.6B CustomVoice",
        family="qwen3-tts",
        size_gb=1.9,
        gated=False,
        capabilities=("tts", "multilingual", "expressive"),
    )


def _parameter(parameters: list[dict], name: str) -> dict:
    return next(parameter for parameter in parameters if parameter["name"] == name)


def test_a_preset_model_with_a_roster_publishes_the_roster_as_its_enum() -> None:
    """SH-16: how a speaking voice is chosen is answerable from the contract alone."""
    model = _custom_voice_model()
    assert catalog.voice_modes(model) == ["preset"], "the fixture must be the preset form"
    roster = catalog.family_for(model).preset_roster
    assert roster is not None
    parameter = _parameter(lane_contract.parameters_for(model), "preset_speaker")
    assert parameter["type"] == "string"
    assert parameter["required"] is False
    assert parameter["enum"] == [speaker["id"] for speaker in roster.speakers]
    # The note says where the roster comes from, read from the roster's own record rather than
    # a second sentence here.
    assert roster.source in parameter["note"]


def test_the_roster_the_picker_reads_is_the_one_the_contract_publishes() -> None:
    """One roster, two producers. A second copy is free to disagree with the picker."""
    assert generation.QWEN3_PRESET_SPEAKERS is catalog.QWEN3_PRESET_SPEAKERS, (
        "generation must re-export the roster, not restate it"
    )
    roster = catalog.FAMILIES["qwen3-tts"].preset_roster
    payload = generation.availability()
    assert payload[f"{roster.key}_preset_speakers"] is catalog.QWEN3_PRESET_SPEAKERS
    # And the ids themselves are written down in exactly one producer module.
    backend = REPO_ROOT / "components" / "voice" / "app" / "backend"
    holders = [
        name for name in ("catalog.py", "generation.py", "lane_contract.py")
        if "Uncle_Fu" in (backend / name).read_text(encoding="utf-8")
    ]
    assert holders == ["catalog.py"], holders


def test_a_single_speaker_preset_model_publishes_no_preset_speaker() -> None:
    """The key this lane discards is not advertised: Khmer's voice is built in."""
    model = catalog.get_model("khmerttsopensource/khmer-tts")
    assert catalog.voice_modes(model) == ["preset"], "the fixture must be the preset form"
    assert catalog.family_for(model).preset_roster is None
    names = {parameter["name"] for parameter in lane_contract.parameters_for(model)}
    assert "preset_speaker" not in names


def test_a_cloning_only_model_publishes_no_preset_speaker() -> None:
    """The sharp case: the family publishes a roster, this tier offers no preset."""
    model = catalog.get_model("mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit")
    assert catalog.voice_modes(model) == ["cloning"]
    assert catalog.family_for(model).preset_roster is not None, (
        "the fixture is only sharp while its family publishes a roster"
    )
    names = {parameter["name"] for parameter in lane_contract.parameters_for(model)}
    assert "preset_speaker" not in names


def test_preset_speaker_is_offered_exactly_where_a_roster_can_be_chosen() -> None:
    """The invariant, not today's membership: the rule catches the next model."""
    for model in (*catalog.CATALOG, _custom_voice_model()):
        names = {parameter["name"] for parameter in lane_contract.parameters_for(model)}
        expected = (
            "preset" in catalog.voice_modes(model)
            and catalog.family_for(model).preset_roster is not None
        )
        assert ("preset_speaker" in names) is expected, model.repo
        if expected:
            assert _parameter(lane_contract.parameters_for(model), "preset_speaker")["enum"]


def test_the_published_default_is_the_value_in_force(tmp_path) -> None:
    """A stored default is what a scheduler reads, and no default is invented without one."""
    model = _custom_voice_model()
    parameters = lane_contract.parameters_for(model)
    assert "default" not in _parameter(parameters, "preset_speaker"), (
        "no value is in force, so the lane states none rather than guessing a first entry"
    )

    chosen = catalog.QWEN3_PRESET_SPEAKERS[-1]["id"]
    model_defaults.save(tmp_path, model.repo, {"preset_speaker": chosen}, parameters)
    in_force, not_used = model_defaults.resolved(tmp_path, model.repo, parameters)
    assert not_used == {}
    assert in_force["preset_speaker"] == chosen
    published = _parameter(lane_contract.parameters_for(model, in_force), "preset_speaker")
    assert published["default"] == chosen

    # The enum is what the stored value was checked against: a name the family does not
    # publish is refused rather than stored (the two phantom speakers this roster once had).
    with pytest.raises(ValueError) as refused:
        model_defaults.save(tmp_path, model.repo, {"preset_speaker": "Ethan"}, parameters)
    assert "Ethan" in str(refused.value)

    # And with nothing stored the key is absent again, not frozen at a stale value.
    model_defaults.clear(tmp_path, model.repo)
    in_force, _ = model_defaults.resolved(tmp_path, model.repo, parameters)
    assert "default" not in _parameter(
        lane_contract.parameters_for(model, in_force), "preset_speaker")


def test_every_published_parameter_is_a_real_request_field_with_its_enforced_bound() -> None:
    body = (REPO_ROOT / "components/voice/app/backend/main.py").read_text(encoding="utf-8")
    section = body[body.index("class Txt2SpeechBody"):]
    section = section[:section.index("\n\n\n")]
    for row in _rows():
        for parameter in row["lane_contract"]["parameters"]:
            assert f"{parameter['name']}:" in section, parameter["name"]
            if parameter["name"] == "text":
                assert parameter["max_characters"] <= lane_contract.API_TEXT_MAX_CHARACTERS
            if parameter["name"] == "speed":
                assert parameter["minimum"] == long_form_policy.SPEED_MINIMUM
                assert parameter["maximum"] == long_form_policy.SPEED_MAXIMUM
            if parameter["name"] == "section_max_characters":
                control = catalog.section_size_control_for(row["repo"])
                assert parameter["minimum"] == control["minimum"]
                assert parameter["maximum"] == control["maximum"]
                assert parameter["default"] == control["runtime_default"]


def test_text_ceiling_is_the_models_own_audited_number() -> None:
    """A model whose audit states a smaller ceiling publishes that number."""
    qwen = catalog.serialize_model(
        catalog.get_model("mlx-community/Qwen3-TTS-12Hz-1.7B-Base-4bit")
    )["lane_contract"]
    text = next(p for p in qwen["parameters"] if p["name"] == "text")
    assert text["max_characters"] == 10_000
    assert "audit" in text["bound_source"]

    voxcpm = catalog.serialize_model(
        catalog.get_model("mlx-community/VoxCPM2-4bit")
    )["lane_contract"]
    text = next(p for p in voxcpm["parameters"] if p["name"] == "text")
    assert text["max_characters"] == lane_contract.API_TEXT_MAX_CHARACTERS
    assert "Txt2SpeechBody" in text["bound_source"]


def test_runtime_seconds_is_measured_rows_or_absent() -> None:
    seen = 0
    for row in _rows():
        runtime = row["lane_contract"].get("runtime_seconds")
        if runtime is None:
            continue
        seen += 1
        assert runtime["evidence"] == "measured", row["repo"]
        assert runtime["measured_on"], row["repo"]
        assert isinstance(runtime["measured"], list) and runtime["measured"], row["repo"]
        for measurement in runtime["measured"]:
            assert measurement["seconds"] > 0, row["repo"]
            assert measurement["scene"], row["repo"]
    assert seen, "no model publishes a measured runtime; the key would be dead weight"


def test_a_measured_runtime_names_a_shipped_model(monkeypatch) -> None:
    """A runtime row for a model nobody ships is a number with no model."""
    shipped = {model.repo for model in catalog.CATALOG}
    record = lane_contract.stored_measurements()
    assert record, (
        "the stored runtime record was not reachable from this checkout: "
        f"{lane_contract._RUNTIME_RECORD}"
    )
    for repo in record:
        assert repo in shipped, repo
    # A model with no measurement publishes no key rather than a guess.
    assert lane_contract.runtime_seconds_for("example/not-a-model") is None


def _audit_canary_seconds(repo: str) -> set[float]:
    """The canary runtimes stored for one model in `components/voice/model-audits/`.

    Read from the files themselves rather than through `model_audits.audit_record`, so a
    change to the selection rule cannot make this test agree with the code by construction.
    """
    root = REPO_ROOT / "components" / "voice" / "model-audits"
    found: set[float] = set()
    for path in root.glob(f"*/{repo.replace('/', '--')}.audit.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        canary = (record.get("evidence") or {}).get("five_thousand_character_canary") or {}
        value = canary.get("runtime_seconds")
        if isinstance(value, (int, float)) and value > 0:
            found.add(round(float(value), 3))
    return found


def test_every_published_runtime_row_traces_to_a_stored_record() -> None:
    """A number a scheduler sets a deadline from is re-read from where it is stated.

    Three halves, and all must hold: the published row equals the row in the stored
    runtime record (or the canary evidence of the model's own stored audit), the stored
    record's row equals the probe file its `source` names, and a model nothing measured
    publishes nothing at all. A curated number that lives only in this repository's Python
    fails the first half the moment it disagrees; a record that has drifted from its own
    receipt fails the second.
    """
    record = lane_contract.stored_measurements()
    assert record, (
        "the stored runtime record was not reachable from this checkout: "
        f"{lane_contract._RUNTIME_RECORD}"
    )
    published_any = 0
    for row in _rows():
        published = row["lane_contract"].get("runtime_seconds")
        if published is None:
            continue
        published_any += 1
        stored = record.get(row["repo"])
        if stored is None:
            audited = _audit_canary_seconds(row["repo"])
            assert audited, (
                f"{row['repo']} publishes runtime_seconds that neither the stored runtime "
                "record nor a stored model audit states; publish the run's own receipt or "
                "publish nothing"
            )
            assert published["evidence"] == "measured" and published["measured_on"], row["repo"]
            for measurement in published["measured"]:
                assert round(float(measurement["seconds"]), 3) in audited, (
                    f"{row['repo']}: {measurement['seconds']} is not in any stored audit"
                )
            continue
        assert published["evidence"] == stored["evidence"] == "measured", row["repo"]
        assert published["measured_on"] == stored["measured_on"], row["repo"]
        assert len(published["measured"]) == len(stored["measured"]), row["repo"]
        for measurement in published["measured"]:
            assert any(
                candidate["seconds"] == measurement["seconds"]
                and candidate["scene"] == measurement["scene"]
                for candidate in stored["measured"]
            ), f"{row['repo']}: {measurement!r} is not in the stored record"
    assert published_any, "no model publishes a measured runtime; the key would be dead weight"
    # And every stored row names the probe receipt its number was read from.
    for repo, entry in record.items():
        for measurement in entry["measured"]:
            source = measurement.get("source")
            assert source, repo
            relative, _, field = source.partition(" ")
            probe_path = REPO_ROOT / relative
            assert probe_path.is_file(), f"{repo}: {relative} does not exist"
            probe = json.loads(probe_path.read_text(encoding="utf-8"))
            assert probe[field] == measurement["seconds"], (
                f"{repo}: the stored record says {measurement['seconds']} but {relative} "
                f"states {probe[field]}"
            )


def test_a_run_with_no_stored_receipt_publishes_no_runtime() -> None:
    """Pocket TTS 4-bit (MLX 4-bit)'s 0.30 s survives only as prose, so it is not published.

    The measurement happened - the owner's review, this row's own former comment and the
    package's manifest all record it - but no probe receipt is stored, and a curated copy
    of a number is the defect this record removes. This test is the pin: re-admitting the
    number means storing the run, not editing a table.
    """
    row = next(row for row in _rows() if row["repo"] == "mlx-community/pocket-tts-4bit")
    assert "runtime_seconds" not in row["lane_contract"]
    assert lane_contract.runtime_seconds_for("mlx-community/pocket-tts-4bit") is None


def test_a_family_that_declares_an_unknown_lane_parameter_fails_loudly(monkeypatch) -> None:
    model = catalog.get_model("khmerttsopensource/khmer-tts")
    monkeypatch.setitem(
        catalog.FAMILIES,
        "khmer-tts-mms",
        catalog.Family(
            **{
                **{
                    field: getattr(catalog.FAMILIES["khmer-tts-mms"], field)
                    for field in (
                        "id", "label", "engine", "requirements", "summary", "how_to_use",
                        "text_guidance", "section_max_characters",
                    )
                },
                "lane_parameters": ("not_a_request_field",),
            }
        ),
    )
    with pytest.raises(ValueError, match="not_a_request_field"):
        lane_contract.for_model(model)


def test_every_parameter_shape_reaches_a_published_contract() -> None:
    """No shape in the table may be dead.

    `max_output_duration_s` sat in `_PARAMETER_SHAPES` while **no family named it**, so no lane
    contract offered it — and meanwhile the worker was enforcing it: the dispatcher converts it
    into Qwen's audio-token ceiling and the quality path refuses a finished WAV that exceeds it.
    A scheduler reading contracts could not discover a bound that was being applied. The other
    direction already fails loudly (a family naming an unknown shape raises, and names the file);
    only this one was silent, which is why it survived.

    Written as a set difference rather than a list of names on purpose: a list would have to be
    edited by whoever adds the next shape, and the whole point is that nobody has to remember.
    """
    published: set[str] = set()
    for model in catalog.CATALOG:
        for parameter in lane_contract.parameters_for(model):
            published.add(parameter["name"])
    dead = sorted(set(lane_contract._PARAMETER_SHAPES) - published)
    assert not dead, (
        f"these shapes are published by no shipped model, so no lane contract offers them: {dead}. "
        "Either a family that honours the parameter declares it in its `lane_parameters`, or the "
        "shape is removed — a bound nobody can discover is not a contract."
    )


def test_every_roster_consumer_joins_on_the_published_key_shape() -> None:
    """A consumer may not name one family's roster key.

    `components/voice/app/frontend/app.js` read the exact key `qwen3_preset_speakers` in three
    places while the worker publishes each family's roster as `<family>_preset_speakers`. A second
    roster family would have been published, shipped and correct — and simply invisible in that
    picker, which is the defect that once hid six voice packages from the Audio picker. The native
    app has always joined on the key *shape*; this checks that neither consumer names a family.
    """
    frontend = (REPO_ROOT / 'components/voice/app/frontend/app.js').read_text(encoding='utf-8')
    assert "_preset_speakers$/.test(k)" in frontend, (
        'the web picker must discover rosters by the shape of the key, not by one family name')
    assert 'preset_rosters' in frontend, 'and it must keep what it found'
    native = (REPO_ROOT / 'native/StudioAPI.swift').read_text(encoding='utf-8')
    assert 'hasSuffix("_preset_speakers")' in native or 'hasSuffix("preset_speakers")' in native, (
        'the native app must keep joining rosters on the key shape')
    # And no shipped consumer may ask for one family by name again.
    # The *code* shape, not the phrase: a comment explaining why the branch was removed quotes it.
    for path, text in (('components/voice/app/frontend/app.js', frontend),
                       ('native/StudioAPI.swift', native)):
        assert 'isQwen = family' not in text and 'isQwen=' not in text, (
            f'{path} decides something from a family name again; that is the hardcoded-consumer rule')


def test_a_timed_model_publishes_a_request_size_that_fits_the_budget_on_its_slowest_mac() -> None:
    """`maximum_input_characters` is what Story splits narration by (through the Gateway). It must
    make one request finish inside the budget on the slowest Mac allowed, and exist only where that
    Mac class was timed. Breeze on an M1 16 GB (RTF 5.13) killed 7,649-character chapters at 1,800 s."""
    seen = 0
    for model in catalog.CATALOG:
        contract = lane_contract.for_model(model)
        size = contract.get("maximum_input_characters")
        if model.slowest_mac_speed is None:
            assert size is None, model.repo
            continue
        seen += 1
        speed = model.slowest_mac_speed
        assert type(size) is int and 0 < size <= contract["parameters"][0]["max_characters"], model.repo
        worst_run = size / speed.characters_per_audio_second * speed.realtime_factor
        assert worst_run <= lane_contract.REQUEST_BUDGET_SECONDS, (model.repo, worst_run)
        assert contract["maximum_input_characters_source"]["evidence"] == "measured"
    assert seen, "no model publishes a request size; Story would send whole chapters"
    breeze = catalog.get_model("mlx-community/Breeze-TTS-2-mlx-4bit")
    assert lane_contract.for_model(breeze)["maximum_input_characters"] == 1500
