from __future__ import annotations

from pathlib import Path

import pytest

from backend import catalog, generation, long_form_policy, model_audits


@pytest.mark.parametrize(
    ("family", "repo", "section_chars", "join_ms", "source"),
    [
        ("qwen3-tts", "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit", 288, 180, "model_audit"),
        # The shipped 1.7B Base tier: its derived record reaches the measured
            # production audit, so the Auto 280 section size governs and 400 is the ceiling.
            ("qwen3-tts", "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-4bit", 280, 300, "model_audit"),
        ("chatterbox-mlx", "mlx-community/chatterbox-4bit", 500, 120, "model_audit"),
        ("chatterbox-mlx", "mlx-community/chatterbox-8bit", 500, 120, "runtime_default"),
        ("voxcpm-mlx", "mlx-community/VoxCPM2-4bit", 400, 120, "runtime_default"),
        ("voxcpm-mlx", "mlx-community/VoxCPM2-8bit", 400, 120, "runtime_default"),
        ("voxcpm-mlx", "mlx-community/VoxCPM2-bf16", 400, 120, "runtime_default"),
        ("omnivoice", "mlx-community/OmniVoice-bfloat16", 288, 300, "model_audit"),
        ("fish-audio-mlx", "mlx-community/fish-audio-s2-pro-8bit", 300, 120, "runtime_default"),
        ("fish-audio-mlx", "mlx-community/fish-audio-s2-pro-bf16", 300, 120, "runtime_default"),
        # The Khmer VITS families have no measurement of their own, so they get the
        # conservative default. They are still catalogued, so they still publish one.
        ("khmer-tts-mms", "khmerttsopensource/khmer-tts", 280, 120, "conservative_default"),
        ("khmer-tts-onnx", "sengtha/khmer-tts-female-v2", 280, 120, "conservative_default"),
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

    A family with no measurement of its own gets the 280-character conservative
    default. The engine-cliff families (Bark, Orpheus) were cut with the menu, so
    no catalogued family publishes a hard cap any more.
    """
    for family in sorted(catalog.FAMILIES):
        policy = long_form_policy.policy_for(family, f"example/{family}")
        assert policy is not None, family
        assert policy["section_max_characters"] >= 40, family
        assert policy["hard_cap_characters"] is None, family


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
