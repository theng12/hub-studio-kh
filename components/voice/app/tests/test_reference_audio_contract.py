from __future__ import annotations

import io
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from backend import catalog, generation, main, reference_audio


# The 1.7B Base tier the app ships. The 8-bit checkpoint was pruned on
# 2026-09-24 (ST-13.B); a test that used it as the cloning model would assert
# the rule against a repo the app cannot select.
QWEN_17B_BASE = "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-4bit"


def _wav(seconds: float = 8.0, sample_rate: int = 24_000) -> bytes:
    silence = np.zeros(sample_rate, dtype=np.float32)
    count = max(1, round((seconds - 1.0) * sample_rate))
    t = np.arange(count, dtype=np.float32) / sample_rate
    speech = 0.2 * np.sin(2 * np.pi * 220 * t)
    out = io.BytesIO()
    sf.write(out, np.concatenate((silence, speech)), sample_rate, format="WAV", subtype="PCM_16")
    return out.getvalue()


def test_reference_preparation_uses_audited_duration_profile(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(reference_audio, "ROOT", tmp_path / "references")
    monkeypatch.setattr(
        reference_audio.model_audits,
        "input_limits",
        lambda _repo: {"reference_audio": {
            "minimum_duration_seconds": 2,
            "maximum_duration_seconds": 4,
            "sample_rate_hz": 16_000,
            "transcript": "optional",
        }},
    )

    prepared = reference_audio.prepare(
        audio_bytes=_wav(), filename="customer.wav", model_id="model/a"
    )

    assert prepared["source_duration_seconds"] == pytest.approx(8.0, abs=0.02)
    assert prepared["duration_seconds"] == pytest.approx(4.0, abs=0.02)
    assert prepared["sample_rate_hz"] == 16_000
    assert Path(prepared["path"]).is_file()
    assert prepared["source_sha256"] != prepared["derived_sha256"]


def test_required_transcript_cannot_be_reused_after_unaligned_cut(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(reference_audio, "ROOT", tmp_path / "references")
    monkeypatch.setattr(
        reference_audio.model_audits,
        "input_limits",
        lambda _repo: {"reference_audio": {
            "minimum_duration_seconds": 2,
            "maximum_duration_seconds": 4,
            "transcript": "required",
        }},
    )

    with pytest.raises(reference_audio.ReferenceAudioError) as error:
        reference_audio.prepare(
            audio_bytes=_wav(), filename="customer.wav", model_id="model/a",
            transcript="This transcript describes the complete recording.",
        )

    assert error.value.code == "REFERENCE_TRANSCRIPT_ALIGNMENT_REQUIRED"


def test_timed_transcript_is_sliced_with_the_selected_reference(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(reference_audio, "ROOT", tmp_path / "references")
    monkeypatch.setattr(
        reference_audio.model_audits,
        "input_limits",
        lambda _repo: {"reference_audio": {
            "minimum_duration_seconds": 2,
            "maximum_duration_seconds": 4,
            "transcript": "required",
        }},
    )

    prepared = reference_audio.prepare(
        audio_bytes=_wav(), filename="customer.wav", model_id="model/a",
        transcript_segments=[
            {"start": 1.0, "end": 2.5, "text": "First sentence."},
            {"start": 2.5, "end": 4.5, "text": "Second sentence."},
            {"start": 4.5, "end": 7.5, "text": "Third sentence."},
        ],
    )

    assert prepared["duration_seconds"] <= 4.01
    assert prepared["transcript"] == "First sentence. Second sentence."


def test_qwen_audit_drives_reference_preparation_contract(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(reference_audio, "ROOT", tmp_path / "references")
    model_id = "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit"

    prepared = reference_audio.prepare(
        audio_bytes=_wav(seconds=10),
        filename="customer.wav",
        model_id=model_id,
        transcript="These exact words cover the selected ten second reference.",
    )

    assert 8 <= prepared["duration_seconds"] <= 10
    assert prepared["sample_rate_hz"] == 24_000
    assert prepared["profile"]["target_duration_seconds"] == 8
    assert prepared["profile"]["recommended_duration_seconds"] == {
        "minimum": 8,
        "maximum": 12,
    }
    assert prepared["profile"]["transcript"] == "required"


def test_qwen_17b_private_reference_derives_its_contract_when_the_audit_is_absent(
    tmp_path: Path, monkeypatch,
) -> None:
    """Withholding the audit no longer refuses; the model's own facts carry it.

    The audited profile still wins where it exists (the tests above). This is the
    un-audited half: the row publishes 24 kHz, and the model's own published
    guardrails say the clone path aligns the clip to an exact transcript, so the
    derived contract is the model's statement rather than a generic default.
    """
    monkeypatch.setattr(reference_audio, "ROOT", tmp_path / "references")
    monkeypatch.setattr(reference_audio.model_audits, "input_limits", lambda _repo: {})

    prepared = reference_audio.prepare(
        audio_bytes=_wav(),
        filename="customer.wav",
        model_id=QWEN_17B_BASE,
        transcript="Exact words.",
    )

    assert prepared["profile_audited"] is False
    assert prepared["profile"]["sample_rate_hz"] == 24_000
    assert prepared["profile"]["transcript"] == "required"
    assert prepared["profile_sources"]["sample_rate_hz"].startswith(
        "catalog.ModelEntry.sample_rate_hz"
    )
    assert prepared["profile_sources"]["transcript"].startswith(
        "catalog.execution_contract.quality_guardrails"
    )
    assert Path(prepared["path"]).is_file()


def test_private_reference_path_never_enters_public_job_payload(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(
        generation.model_audits,
        "input_limits",
        lambda _repo: {"long_form_strategy": "adapter_managed_long_form"},
    )
    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"private")
    job = generation.GenerationJob(
        job_id="private-reference",
        mode="txt2speech",
        params={
            "repo": "model/a",
            "text": "hello",
            "_reference_audio_path": str(reference),
            "_reference_audio_sha256": "a" * 64,
            "_reference_source_sha256": "b" * 64,
            "_reference_preparation_revision": "prep-v1",
            "_reference_duration_s": 4.0,
        },
        chunk_total=3,
    )

    result = job.serialize()

    assert "_reference_audio_path" not in result["params"]
    assert str(reference) not in str(result)
    assert result["reference_audio_sha256"] == "a" * 64
    assert result["reference_source_sha256"] == "b" * 64
    assert result["long_form_strategy"] == "adapter_managed_long_form"
    assert result["chunk_total"] == 3


def test_private_reference_is_injected_without_a_library_voice(tmp_path: Path) -> None:
    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"private")
    manager = object.__new__(generation.GenerationManager)
    kwargs: dict = {}

    manager._inject_voice_clone(
        "",
        {"_reference_audio_path": str(reference), "ref_transcript": "Exact words."},
        kwargs,
        voices_module=None,
        fallback_transcript=".",
    )

    assert kwargs == {"ref_audio": str(reference), "ref_text": "Exact words."}


def test_qwen_17b_private_reference_preserves_preparation_evidence(
    tmp_path: Path,
) -> None:
    """A prepared private upload must reach the adapter unchanged and auditable.

    Removing any of the private preparation fields would make the serialized
    GenStudio result lose the prepared duration or its source/derived hashes.
    """
    reference = tmp_path / "prepared-reference.wav"
    reference.write_bytes(b"prepared-private-reference")
    manager = object.__new__(generation.GenerationManager)
    params = {
        "repo": QWEN_17B_BASE,
        "_reference_audio_path": str(reference),
        "_reference_audio_sha256": "a" * 64,
        "_reference_source_sha256": "b" * 64,
        "_reference_preparation_revision": "reference-v1",
        "_reference_duration_s": 8.0,
        "ref_transcript": "Exact prepared words.",
    }
    kwargs: dict = {}

    manager._mlx_kwargs_qwen3(
        SimpleNamespace(repo=QWEN_17B_BASE), params, kwargs, voices_module=None
    )
    result = generation.GenerationJob(
        job_id="prepared-private-reference",
        mode="txt2speech",
        params=params,
    ).serialize()

    assert kwargs == {
        "ref_audio": str(reference),
        "ref_text": "Exact prepared words.",
    }
    assert result["reference_audio_sha256"] == "a" * 64
    assert result["reference_source_sha256"] == "b" * 64
    assert result["reference_preparation_revision"] == "reference-v1"
    assert result["reference_duration_s"] == 8.0


def test_qwen_17b_fleet_voice_is_prepared_before_the_adapter_and_auditable(
    tmp_path: Path, monkeypatch,
) -> None:
    """A raw fleet file must never be the Qwen adapter's reference input.

    This would fail if the saved-voice branch again injected ``reference_path``
    directly, bypassing the same bounded preparation used for private uploads.
    """
    source = tmp_path / "fleet-source.wav"
    source_bytes = b"fleet-owned-source-bytes"
    source.write_bytes(source_bytes)
    prepared = tmp_path / "prepared-reference.wav"
    prepared.write_bytes(b"bounded-normalized-wav")
    source_sha = hashlib.sha256(source_bytes).hexdigest()
    calls: list[dict] = []

    def fake_prepare(**kwargs):
        calls.append(kwargs)
        return {
            "path": str(prepared),
            "source_sha256": source_sha,
            "derived_sha256": "c" * 64,
            "preparation_revision": "reference-v1",
            "duration_seconds": 8.0,
            "transcript": "Fleet reference words.",
        }

    voice = SimpleNamespace(audio_sha256=source_sha)
    library = SimpleNamespace(
        get=lambda voice_id: voice if voice_id == "fleet-voice" else None,
        reference_path=lambda voice_id: source,
        transcript=lambda voice_id: "Fleet reference words.",
        # The saved timings must travel with the voice: without them a clip longer
        # than the model's window is refused, so a fleet-shared voice would install
        # and then fail to clone everywhere. A library that stored none passes [].
        segments=lambda voice_id: [
            {"text": "Fleet reference words.", "start": 0.0, "end": 2.0},
        ],
    )
    monkeypatch.setattr(reference_audio, "prepare", fake_prepare)
    params = {"repo": QWEN_17B_BASE, "voice_library_id": "fleet-voice"}
    kwargs: dict = {}

    manager = object.__new__(generation.GenerationManager)
    manager._mlx_kwargs_qwen3(
        SimpleNamespace(repo=QWEN_17B_BASE),
        params,
        kwargs,
        SimpleNamespace(library=library),
    )
    result = generation.GenerationJob(
        job_id="prepared-fleet-reference",
        mode="txt2speech",
        params=params,
    ).serialize()

    assert calls == [{
        "audio_bytes": source_bytes,
        "filename": "fleet-source.wav",
        "model_id": QWEN_17B_BASE,
        "transcript": "Fleet reference words.",
        "transcript_segments": [
            {"text": "Fleet reference words.", "start": 0.0, "end": 2.0},
        ],
    }]
    assert kwargs == {"ref_audio": str(prepared), "ref_text": "Fleet reference words."}
    assert result["voice_library_id"] == "fleet-voice"
    assert result["reference_audio_sha256"] == "c" * 64
    assert result["reference_source_sha256"] == source_sha
    assert result["reference_preparation_revision"] == "reference-v1"
    assert result["reference_duration_s"] == 8.0


def test_fleet_voice_without_stored_timings_still_prepares_with_an_empty_segment_list(
    tmp_path: Path, monkeypatch,
) -> None:
    """A voice saved before timings existed must prepare, not fail on a missing key."""
    source = tmp_path / "fleet-source.wav"
    source_bytes = b"fleet-owned-source-bytes"
    source.write_bytes(source_bytes)
    prepared = tmp_path / "prepared-reference.wav"
    prepared.write_bytes(b"bounded-normalized-wav")
    source_sha = hashlib.sha256(source_bytes).hexdigest()
    calls: list[dict] = []

    def fake_prepare(**kwargs):
        calls.append(kwargs)
        return {
            "path": str(prepared),
            "source_sha256": source_sha,
            "derived_sha256": "c" * 64,
            "preparation_revision": "reference-v1",
            "duration_seconds": 8.0,
            "transcript": "Fleet reference words.",
        }

    library = SimpleNamespace(
        get=lambda voice_id: SimpleNamespace(audio_sha256=source_sha),
        reference_path=lambda voice_id: source,
        transcript=lambda voice_id: "Fleet reference words.",
    )
    monkeypatch.setattr(reference_audio, "prepare", fake_prepare)

    manager = object.__new__(generation.GenerationManager)
    manager._mlx_kwargs_qwen3(
        SimpleNamespace(repo=QWEN_17B_BASE),
        {"repo": QWEN_17B_BASE, "voice_library_id": "fleet-voice"},
        {},
        SimpleNamespace(library=library),
    )

    assert calls[0]["transcript_segments"] == []


def test_qwen_17b_fleet_voice_fails_closed_when_preparation_rejects(
    tmp_path: Path, monkeypatch,
) -> None:
    """The adapter must not fall back to the unbounded saved source on reject."""
    source = tmp_path / "fleet-source.wav"
    source.write_bytes(b"fleet-owned-source-bytes")
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    library = SimpleNamespace(
        get=lambda _voice_id: SimpleNamespace(audio_sha256=source_sha),
        reference_path=lambda _voice_id: source,
        transcript=lambda _voice_id: "Fleet reference words.",
    )

    def reject(**_kwargs):
        raise reference_audio.ReferenceAudioError(
            "REFERENCE_AUDIO_DURATION_UNSUPPORTED", "The bounded contract rejected it."
        )

    monkeypatch.setattr(reference_audio, "prepare", reject)
    manager = object.__new__(generation.GenerationManager)

    with pytest.raises(ValueError, match="bounded contract rejected"):
        manager._mlx_kwargs_qwen3(
            SimpleNamespace(repo=QWEN_17B_BASE),
            {"repo": QWEN_17B_BASE, "voice_library_id": "fleet-voice"},
            {},
            SimpleNamespace(library=library),
        )


def test_audited_private_section_budget_overrides_family_fallback(monkeypatch) -> None:
    monkeypatch.setattr(
        generation.model_audits,
        "input_limits",
        lambda _repo: {"private_section_max_characters": 120},
    )
    text = "Sentence number one is deliberately substantial. " * 10

    chunks = generation._internal_mlx_text_chunks("chatterbox-mlx", "model/a", text)

    assert len(chunks) > 1
    assert all(len(chunk) <= 120 for chunk in chunks)


def test_terminal_long_form_strategy_comes_from_audit_not_chunk_count(monkeypatch) -> None:
    monkeypatch.setattr(
        generation.model_audits,
        "input_limits",
        lambda _repo: {"long_form_strategy": "adapter_managed_long_form"},
    )
    job = generation.GenerationJob(
        job_id="job-audited-strategy",
        mode="txt2speech",
        params={"repo": "model/a", "text": "A short request."},
        chunk_total=1,
    )

    assert job.serialize()["long_form_strategy"] == "adapter_managed_long_form"


# ─── A9 + its follow-up: the contract is the model's own published facts ───

VOXCPM_4BIT = "mlx-community/VoxCPM2-4bit"


def _catalog_row(**overrides) -> catalog.ModelEntry:
    """A row shaped like a new cloning family, for the refusal case."""
    fields: dict = {
        "repo": "example/new-cloning-family-4bit",
        "label": "A new cloning family 4-bit",
        "family": "voxcpm-mlx",
        "size_gb": 1.0,
        "gated": False,
        "capabilities": ("tts", "voice-cloning"),
    }
    fields.update(overrides)
    return catalog.ModelEntry(**fields)


def test_every_cloning_model_is_governed_by_its_published_capability() -> None:
    """The predicate is the model's own `capabilities`, not a name substring.

    It used to be `"qwen3-tts" in name and "base" in name`, which a new family —
    or a new Qwen tier not spelled *-Base-* — fell outside while still being
    prepared with guessed duration, rate and transcript rules.
    """
    cloning = [entry for entry in catalog.CATALOG if "voice-cloning" in entry.capabilities]
    assert cloning, "no cloning model is catalogued; this test would be vacuous"

    for entry in catalog.CATALOG:
        declares = "voice-cloning" in entry.capabilities
        assert reference_audio._requires_published_profile(entry.repo) is declares, entry.repo

    # A model the registry does not know is not a documented cloning model, and
    # the public routes refuse it before preparation ever runs.
    assert reference_audio._requires_published_profile("example/not-in-this-catalog") is False


def test_every_cloning_model_prepares_without_an_audit_from_its_own_published_facts(
    tmp_path: Path, monkeypatch,
) -> None:
    """The invariant that catches the next cloning model.

    With every audit withheld, each catalogued cloning model must still reach a
    real execution copy, labelled `profile_audited: false`, with every value it
    used traced to a published field. This is the half A9's first fix got wrong:
    it refused the work instead of deriving it, so the recommended VoxCPM2 4-bit
    tier could not clone from a chosen clip at all.
    """
    monkeypatch.setattr(reference_audio, "ROOT", tmp_path / "references")
    monkeypatch.setattr(reference_audio.model_audits, "input_limits", lambda _repo: {})
    cloning = [entry for entry in catalog.CATALOG if "voice-cloning" in entry.capabilities]
    assert cloning, "no cloning model is catalogued; this test would be vacuous"

    for entry in catalog.CATALOG:
        prepared = reference_audio.prepare(
            audio_bytes=_wav(),
            filename="customer.wav",
            model_id=entry.repo,
            transcript="Exact words for this reference clip.",
        )
        assert Path(prepared["path"]).is_file(), entry.repo
        if "voice-cloning" not in entry.capabilities:
            # A family that cannot clone keeps the generic preparation: the
            # derivation must not tighten an unrelated model.
            assert prepared["profile"] == {}, entry.repo
            assert prepared["profile_audited"] is False, entry.repo
            continue
        assert prepared["profile_audited"] is False, entry.repo
        assert prepared["profile"]["sample_rate_hz"] == entry.sample_rate_hz, entry.repo
        assert prepared["sample_rate_hz"] == entry.sample_rate_hz, entry.repo
        assert prepared["profile"]["transcript"] in {"required", "optional", "not_used"}, entry.repo
        # No value is published without the field it came from, and no bound the
        # model does not state is invented: it is recorded as unpublished.
        for name in prepared["profile"]:
            assert prepared["profile_sources"].get(name), (entry.repo, name)
        assert "not published" in prepared["profile_sources"]["maximum_duration_seconds"]
        assert "not published" in prepared["profile_sources"]["channels"]
        # The derived rule is the rule preparation enforces: only a model whose
        # contract says `required` may refuse a transcript-less clip.
        try:
            reference_audio.prepare(
                audio_bytes=_wav(), filename="customer.wav", model_id=entry.repo,
            )
        except reference_audio.ReferenceAudioError as error:
            assert prepared["profile"]["transcript"] == "required", entry.repo
            assert error.code == "REFERENCE_TRANSCRIPT_REQUIRED", entry.repo
        else:
            assert prepared["profile"]["transcript"] != "required", entry.repo


def test_the_recommended_voxcpm2_tier_resamples_to_its_own_published_rate(
    tmp_path: Path, monkeypatch,
) -> None:
    """VoxCPM2 publishes 48 kHz; an audit-less clone must not fall back to 24 kHz.

    This is the owner-visible half of A9: VoxCPM2 4-bit, the recommended voice
    model, has no audit record, so a generic 24 kHz execution copy was wrong and
    a refusal was not an answer either.
    """
    monkeypatch.setattr(reference_audio, "ROOT", tmp_path / "references")
    monkeypatch.setattr(reference_audio.model_audits, "input_limits", lambda _repo: {})

    prepared = reference_audio.prepare(
        audio_bytes=_wav(), filename="customer.wav", model_id=VOXCPM_4BIT,
    )

    entry = catalog.get_model(VOXCPM_4BIT)
    assert entry is not None
    assert entry.sample_rate_hz == 48_000
    assert prepared["sample_rate_hz"] == 48_000
    assert prepared["profile"] == {"sample_rate_hz": 48_000, "transcript": "optional"}
    assert prepared["profile_sources"]["transcript"].startswith("catalog.FAMILIES")
    assert Path(prepared["path"]).is_file()


def test_a_cloning_model_that_publishes_nothing_derivable_refuses_by_name(
    tmp_path: Path, monkeypatch,
) -> None:
    """The only surviving refusal names the model, the field and the file to fix.

    A refusal with a named field is a to-do; a refusal because a record does not
    exist is a dead end. The exported error carries both so the route can answer
    with something an operator can act on.
    """
    monkeypatch.setattr(reference_audio, "ROOT", tmp_path / "references")
    stub = _catalog_row(sample_rate_hz=0)
    monkeypatch.setattr(catalog, "CATALOG", catalog.CATALOG + (stub,))

    with pytest.raises(reference_audio.ReferenceAudioError) as error:
        reference_audio.prepare(
            audio_bytes=_wav(), filename="customer.wav", model_id=stub.repo,
        )

    assert error.value.code == "REFERENCE_AUDIO_CONTRACT_UNAVAILABLE"
    assert stub.repo in error.value.detail
    assert "sample_rate_hz" in error.value.detail
    assert "components/voice/app/backend/catalog.py" in error.value.detail
    assert "model-audits" in error.value.detail
    assert error.value.model_id == stub.repo
    assert error.value.missing_field == "sample_rate_hz"
    assert error.value.fix_in == (
        "components/voice/app/backend/catalog.py",
        "components/voice/model-audits/<date>-<name>/<org>--<repo>.audit.json",
    )
    # The refusal is the missing *field*, and the route surfaces it as such.
    assert error.value.as_detail()["missing_field"] == "sample_rate_hz"


def test_an_audited_model_keeps_its_audited_contract_verbatim(
    tmp_path: Path, monkeypatch,
) -> None:
    """The audited path must not be re-derived or re-labelled by the follow-up."""
    monkeypatch.setattr(reference_audio, "ROOT", tmp_path / "references")
    monkeypatch.setattr(
        reference_audio.model_audits,
        "input_limits",
        lambda _repo: {"reference_audio": {
            "minimum_duration_seconds": 2,
            "maximum_duration_seconds": 4,
            "sample_rate_hz": 16_000,
            "transcript": "optional",
        }},
    )

    prepared = reference_audio.prepare(
        audio_bytes=_wav(), filename="customer.wav", model_id="model/a",
    )

    assert prepared["profile_audited"] is True
    assert prepared["sample_rate_hz"] == 16_000
    assert prepared["duration_seconds"] == pytest.approx(4.0, abs=0.02)
    assert prepared["profile_sources"]["sample_rate_hz"] == (
        "audit contract.input_limits.reference_audio.sample_rate_hz"
    )


# ─── the route behind the Clone tab's Choose Clip… → Prepare Voice ─────────

def _reference_client(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """The real route, with the engine mocked out and every clip prepared for real."""
    queued: list[dict] = []
    monkeypatch.setattr(main.gen_manager, "is_available", lambda: True)
    monkeypatch.setattr(main.cache, "cache_state", lambda _repo: "cached")
    monkeypatch.setattr(
        main.gen_manager,
        "start_txt2speech",
        lambda params, **_kwargs: (
            queued.append(params) or SimpleNamespace(serialize=lambda: {})
        ),
    )
    return queued


def test_reference_route_prepares_an_unaudited_cloning_model_and_labels_it(
    tmp_path: Path, monkeypatch,
) -> None:
    """The path the owner reported broken must reach a generation again.

    `POST /api/generate/txt2speech/reference` for VoxCPM2 4-bit (the recommended
    voice model, with no audit record) must answer 200 and hand the job a real
    prepared copy, while publishing that this model's cloning path is un-audited.
    """
    monkeypatch.setattr(reference_audio, "ROOT", tmp_path / "references")
    monkeypatch.setattr(reference_audio.model_audits, "input_limits", lambda _repo: {})
    queued = _reference_client(monkeypatch)
    client = TestClient(main.app, headers={"X-Studio-Token": main.FLEET_TOKEN})

    response = client.post(
        "/api/generate/txt2speech/reference",
        data={"request_json": json.dumps({
            "repo": VOXCPM_4BIT,
            "text": "A cloned line of speech.",
        })},
        files={"audio": ("chosen-clip.wav", _wav(), "audio/wav")},
    )

    assert response.status_code == 200, response.text
    reference = response.json()["reference"]
    assert reference["profile_audited"] is False
    assert reference["profile"]["sample_rate_hz"] == 48_000
    assert reference["sample_rate_hz"] == 48_000
    assert reference["profile_sources"]["sample_rate_hz"].startswith("catalog.")
    assert Path(reference["derived_sha256"] and queued[0]["_reference_audio_path"]).is_file()
    # The job carries the same provenance, so the job record shows the
    # un-audited cloning path instead of hiding it.
    assert queued[0]["_reference_profile_audited"] is False
    assert queued[0]["_reference_profile_sources"]["transcript"].startswith("catalog.")
    assert queued[0]["ref_transcript"] is None


def test_reference_route_answers_409_when_a_cloning_model_publishes_no_rate(
    tmp_path: Path, monkeypatch,
) -> None:
    """A refusal is a to-do: 409 with the model, the field and the file to fix.

    It is deliberately not the 422 that means "record a different clip": the
    clip is fine, the model's published contract is incomplete.
    """
    monkeypatch.setattr(reference_audio, "ROOT", tmp_path / "references")
    stub = _catalog_row(repo="example/new-cloning-family-4bit", sample_rate_hz=0)
    monkeypatch.setattr(catalog, "CATALOG", catalog.CATALOG + (stub,))
    queued = _reference_client(monkeypatch)
    client = TestClient(main.app, headers={"X-Studio-Token": main.FLEET_TOKEN})

    response = client.post(
        "/api/generate/txt2speech/reference",
        data={"request_json": json.dumps({
            "repo": stub.repo,
            "text": "A cloned line of speech.",
        })},
        files={"audio": ("chosen-clip.wav", _wav(), "audio/wav")},
    )

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "REFERENCE_AUDIO_CONTRACT_UNAVAILABLE"
    assert detail["model_id"] == stub.repo
    assert detail["missing_field"] == "sample_rate_hz"
    assert "components/voice/app/backend/catalog.py" in detail["detail"]
    assert queued == []
