from __future__ import annotations

from pathlib import Path

import pytest

from backend import catalog, generation, long_form_policy, model_audits


QWEN_17B_V2_CONTROL = {
    "minimum": 230,
    "maximum": 400,
    "step": 1,
    "default_custom": 280,
    "runtime_default": 280,
    "source": "qwen3-17b-production-v2-audit",
}


@pytest.mark.parametrize(
    ("family", "repo", "section_chars", "join_ms", "source"),
    [
        ("qwen3-tts", "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit", 288, 180, "model_audit"),
        ("qwen3-tts", "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit", 280, 300, "model_audit"),
        ("qwen3-tts", "mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit", 360, 120, "model_audit"),
        ("qwen3-tts", "mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-8bit", 360, 120, "runtime_default"),
        ("chatterbox-mlx", "mlx-community/chatterbox-8bit", 500, 120, "runtime_default"),
        ("chatterbox-mlx", "mlx-community/chatterbox-turbo-4bit", 400, 120, "runtime_default"),
        ("voxcpm-mlx", "mlx-community/VoxCPM2-4bit", 400, 120, "runtime_default"),
        ("kokoro-mlx", "mlx-community/Kokoro-82M-bf16", 3000, 120, "model_audit"),
        ("vibevoice", "mlx-community/VibeVoice-Realtime-0.5B-4bit", 3000, 120, "runtime_default"),
        ("omnivoice", "mlx-community/OmniVoice-bfloat16", 288, 300, "model_audit"),
        ("fish-audio-mlx", "mlx-community/fish-audio-s2-pro-8bit", 300, 120, "runtime_default"),
        ("longcat-audiodit", "mlx-community/LongCat-AudioDiT-1B-4bit", 280, 180, "runtime_default"),
        # Families that previously fell out of the catalogue with no policy at all.
        ("f5-tts", "SWivid/F5-TTS", 280, 120, "conservative_default"),
        ("marvis", "Marvis-AI/marvis-tts-250m-v0.1", 280, 120, "conservative_default"),
        ("kittentts", "mlx-community/kitten-tts-nano-0.8-fp16", 280, 120, "conservative_default"),
        # Engine-cliff families: the number is the point past which the engine stops, not a
        # quality preference, so it is published as a hard cap as well.
        ("orpheus", "mlx-community/orpheus-3b-0.1-ft-4bit", 170, 120, "engine_input_limit"),
        ("bark", "mlx-community/bark", 150, 120, "engine_input_limit"),
        ("voxtral-tts", "mlx-community/Voxtral-4B-TTS-2603-mlx-4bit", 800, 120, "measured_fleet"),
    ],
)
def test_catalog_and_runtime_share_exact_long_form_policy(
    family: str, repo: str, section_chars: int, join_ms: int, source: str
) -> None:
    """The catalogue and the runtime must publish and obey the same budget."""
    entry = catalog.get_model(repo)
    assert entry is not None
    published = catalog.serialize_model(entry)["long_form_delivery"]

    assert published["section_max_characters"] == section_chars
    assert published["source"] == source
    assert published["join_pause_milliseconds"] == join_ms
    assert published["split_method"] == "sentence_safe"
    assert published["customer_submits_complete_script"] is True
    assert published["note"]
    assert generation._long_form_join_pause_s(family, repo) == pytest.approx(
        join_ms / 1000
    )

    # Every sectioning model publishes a control derived from the same policy the
    # runtime obeys, so the number a person sees is the number the engine is given.
    assert published["section_size_control"] == catalog.section_size_control_for(repo)
    if repo == "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit":
        # The one hand-declared control still wins with its exact audited values.
        assert published["section_size_control"] == QWEN_17B_V2_CONTROL
    else:
        control = published["section_size_control"]
        hard_cap = published["hard_cap_characters"]
        assert control == {
            "minimum": 40,
            "maximum": hard_cap if hard_cap else 3000,
            "step": 10,
            "default_custom": section_chars,
            "runtime_default": section_chars,
            "source": source,
        }

    text = "Sentence-safe narration remains complete. " * 400
    chunks = generation._internal_mlx_text_chunks(family, repo, text)
    assert chunks
    assert all(len(chunk) <= section_chars for chunk in chunks)
    assert " ".join(chunks) == " ".join(text.split())


@pytest.mark.parametrize("repo", ["mlx-community/orpheus-3b-0.1-ft-4bit", "mlx-community/bark"])
def test_engine_cap_is_published_and_can_never_be_raised_by_an_audit(
    monkeypatch: pytest.MonkeyPatch, repo: str
) -> None:
    """orpheus/bark stop working past their cap, so an audit may only tighten it."""
    family = "orpheus" if "orpheus" in repo else "bark"
    entry = catalog.get_model(repo)
    assert entry is not None
    base = catalog.serialize_model(entry)["long_form_delivery"]
    cap = base["section_max_characters"]

    assert base["origin"] == "engine_input_limit"
    assert base["hard_cap_characters"] == cap
    assert base["section_size_control"]["maximum"] == cap

    # A finer measured section is honoured.
    monkeypatch.setattr(
        model_audits, "input_limits", lambda _repo: {"private_section_max_characters": 120}
    )
    tightened = catalog.serialize_model(catalog.get_model(repo))["long_form_delivery"]
    assert tightened["section_max_characters"] == 120
    assert tightened["hard_cap_characters"] == cap

    # A coarser one is refused, not clamped to some larger number: the family
    # budget survives because the engine cannot take the audited value.
    monkeypatch.setattr(
        model_audits, "input_limits", lambda _repo: {"private_section_max_characters": cap + 500}
    )
    preserved = catalog.serialize_model(catalog.get_model(repo))["long_form_delivery"]
    assert preserved["section_max_characters"] == cap
    assert all(
        len(chunk) <= cap
        for chunk in generation._internal_mlx_text_chunks(
            family, repo, "Engine-capped sentence delivery. " * 100
        )
    )


def test_exact_model_audit_override_is_published_and_executed(monkeypatch) -> None:
    repo = "mlx-community/chatterbox-8bit"
    monkeypatch.setattr(
        model_audits,
        "input_limits",
        lambda _repo: {"private_section_max_characters": 444},
    )
    entry = catalog.get_model(repo)
    assert entry is not None
    published = catalog.serialize_model(entry)["long_form_delivery"]

    assert published["section_max_characters"] == 444
    assert published["source"] == "model_audit"
    chunks = generation._internal_mlx_text_chunks(
        "chatterbox-mlx", repo, "Audited sentence delivery. " * 100
    )
    assert all(len(chunk) <= 444 for chunk in chunks)


def test_every_family_publishes_a_long_form_policy() -> None:
    """A family must never silently lose its policy and fall back to one request.

    The old name for this test asserted the opposite ("unmanaged family does not
    claim voice studio policy"). It is now wrong by design: a family with no
    measurement of its own gets the 280-character conservative default, and the
    two engines that stop working past their input limit get that limit instead.
    """
    for family in sorted(catalog.FAMILIES):
        policy = long_form_policy.policy_for(family, f"example/{family}")
        assert policy is not None, family
        assert policy["section_max_characters"] >= 40, family

    bark = long_form_policy.policy_for("bark", "mlx-community/bark")
    assert bark is not None
    assert bark["section_max_characters"] == 150
    assert bark["hard_cap_characters"] == 150
    assert bark["source"] == "engine_input_limit"
    entry = catalog.get_model("mlx-community/bark")
    assert entry is not None
    published = catalog.serialize_model(entry)["long_form_delivery"]
    assert published["section_max_characters"] == 150
    assert published["hard_cap_characters"] == 150


def test_models_page_renders_policy_from_catalog_without_frontend_limits() -> None:
    frontend = Path(__file__).resolve().parents[1] / "frontend"
    markup = (frontend / "index.html").read_text(encoding="utf-8")
    script = (frontend / "app.js").read_text(encoding="utf-8")

    assert "m.long_form_delivery.section_max_characters" in markup
    assert "m.long_form_delivery.join_pause_milliseconds" in markup
    assert "m.long_form_delivery.note" in markup
    assert "customer submits one complete script" in markup
    assert "section_max_characters: 400" not in script
    assert "join_pause_milliseconds:" not in script


def test_generate_ui_uses_catalog_driven_section_size_control() -> None:
    frontend = Path(__file__).resolve().parents[1] / "frontend"
    markup = (frontend / "index.html").read_text(encoding="utf-8")
    script = (frontend / "app.js").read_text(encoding="utf-8")

    assert "sectionSizeControl" in script
    assert "selectedModel?.long_form_delivery?.section_size_control" in script
    assert '"section_size_mode", "section_max_characters"' in script
    assert "section_max_characters" in markup
    assert (
        'if (this.sectionSizeControlSupported && this.gen.section_size_mode === "custom")'
        in script
    )
    assert "body.section_max_characters = this.sectionSizeValue" in script
    assert "safeSubmit() {\n      if (!this.canSubmit) return;" in script
    assert "async submitGenerate() {\n      if (!this.canSubmit) return;" in script
