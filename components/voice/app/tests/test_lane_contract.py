"""The published lane contract — checked against the code that enforces it.

`docs/lane-execution-contract.md` is the shape; this module is the voice side of
it. A contract that promises a parameter the worker drops, or a bound the worker
does not clamp, is worse than no contract: a scheduler builds against it and the
failure appears as a silently ignored field.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend import catalog, lane_contract, long_form_policy


REPO_ROOT = Path(__file__).resolve().parents[4]


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
    for repo in lane_contract._MEASURED_RUNTIME:
        assert repo in shipped, repo
    # A model with no measurement publishes no key rather than a guess.
    assert lane_contract.runtime_seconds_for("example/not-a-model") is None


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
