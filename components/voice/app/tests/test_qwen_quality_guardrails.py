from pathlib import Path
import threading

import numpy as np
import pytest
import soundfile as sf

from backend import catalog, generation, qwen_quality, transcription

REPO_ROOT = Path(__file__).resolve().parents[4]
MANIFESTS = REPO_ROOT / "model-manifests"
QWEN_06B_BASE = "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit"


@pytest.fixture(autouse=True)
def _packaged_manifests(monkeypatch):
    """A packaged install always carries its manifests; the gate reads them."""
    monkeypatch.setenv("STUDIOHUB_MODEL_MANIFESTS", str(MANIFESTS))


def _validator_repo() -> str:
    """The validator ASR the Qwen Base manifest declares, not a constant."""
    return qwen_quality.validator_repo(QWEN_06B_BASE)


QWEN_17B_BASE = "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit"
LONG_TEXT = " ".join(
    f"Section {index} holds controlled narration for an independent duration budget."
    for index in range(1, 40)
)


def _stt(text: str, *, duration: float = 12.0, words: bool = True) -> dict:
    return {
        "text": text,
        "model": _validator_repo(),
        "duration": duration,
        "segments": [{
            "start": 0.0,
            "end": duration,
            "text": text,
            "words": ([{"word": word, "start": 0.0, "end": 0.2} for word in text.split()]
                      if words else []),
        }],
    }


def test_matching_reference_requires_and_preserves_word_alignment() -> None:
    expected = "Aiden reads this clean reference at a calm and natural pace."
    evidence = qwen_quality.validate_reference(expected, _stt(expected))

    assert evidence["accepted"] is True
    assert evidence["word_timestamp_count"] == len(expected.split())
    assert "Aiden" not in str(evidence)


def test_reference_mismatch_is_a_stable_machine_readable_failure() -> None:
    with pytest.raises(qwen_quality.QwenQualityError) as caught:
        qwen_quality.validate_reference(
            "The exact reference transcript belongs here.",
            _stt("Completely unrelated words were spoken in the audio."),
        )

    assert caught.value.code == "QWEN_REFERENCE_TRANSCRIPT_MISMATCH"
    assert caught.value.evidence["token_error_rate"] > 0.35


def test_reference_without_word_timestamps_is_rejected() -> None:
    with pytest.raises(qwen_quality.QwenQualityError) as caught:
        qwen_quality.validate_reference("Aligned words are required.", _stt(
            "Aligned words are required.", words=False
        ))

    assert caught.value.code == "QWEN_REFERENCE_ALIGNMENT_UNAVAILABLE"


def test_automatic_budget_stops_the_observed_96_second_exhaustion() -> None:
    text = (
        "A professional narrator reads a clear production passage with stable "
        "pacing, accurate wording, and no repeated phrases. " * 2
    )
    limit = qwen_quality.automatic_duration_limit(text)
    tokens = qwen_quality.max_tokens_for_text(text)

    assert 20.0 < limit < 70.0
    assert tokens < 1200


def test_catalog_publishes_qwen_guardrail_dependency() -> None:
    model = catalog.get_model("mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit")
    guardrails = catalog.serialize_model(model)["execution_contract"]["quality_guardrails"]

    assert guardrails["output_transcript_validation"] is True
    assert guardrails["required_local_models"] == [_validator_repo()]
    assert guardrails["max_local_quality_retries"] == 1


def test_output_repetition_is_rejected_even_inside_duration_budget() -> None:
    expected = "The ship crossed the quiet sea before sunrise."
    repeated = " ".join(["The ship crossed the quiet sea"] * 8)
    with pytest.raises(qwen_quality.QwenQualityError) as caught:
        qwen_quality.validate_output(
            expected,
            _stt(repeated, duration=20.0),
            max_duration_s=30.0,
        )

    assert caught.value.code in {
        "QWEN_OUTPUT_TEXT_MISMATCH",
        "QWEN_OUTPUT_REPETITION_DETECTED",
    }


def test_long_form_comparison_uses_bounded_ngram_evidence() -> None:
    expected = "Ancient sailors crossed the open sea under a field of stars. " * 700
    evidence = qwen_quality.validate_output(
        expected,
        _stt(expected, duration=2_400.0),
        max_duration_s=3_000.0,
    )

    assert evidence["accepted"] is True
    assert evidence["comparison_method"]["tokens"] == "2-gram-multiset"
    assert evidence["comparison_method"]["characters"] == "4-gram-multiset"


def _long_form_with_sentinel(*, repeated: bool = False) -> str:
    body = (
        "The harbor keeper records each lantern before the evening tide returns. "
        * 320
        if repeated
        else " ".join(
            f"Marker {index} records a distinct harbor observation before sunset."
            for index in range(1, 420)
        )
    )
    sentinel = (
        "At dawn the final courier carries amber maps beyond quiet cliffs while "
        "patient sailors count twenty four silver bells beside the northern beacon."
    )
    return f"{body} {sentinel}"


def test_long_form_repeated_source_accepts_exact_ordered_terminal_words() -> None:
    expected = _long_form_with_sentinel(repeated=True)

    evidence = qwen_quality.validate_output(
        expected,
        _stt(expected, duration=2_400.0),
        max_duration_s=3_000.0,
    )

    assert evidence["accepted"] is True
    assert evidence["validator_revision"] == "voicestudio.qwen-clone-quality.v1"
    assert evidence["terminal_gate"] == {
        "revision": "voicestudio.qwen-clone-quality.v2",
        "window_size": 24,
        "aligned_word_count": len(expected.split()),
        "spelling_variant_count": 0,
        "hard_mismatch_count": 0,
        "accepted": True,
    }


def test_long_form_terminal_spelling_variant_is_accepted_without_transcript_evidence() -> None:
    expected = _long_form_with_sentinel(repeated=True)
    observed = expected.replace("sailors", "sailours", 1)

    evidence = qwen_quality.validate_output(
        expected,
        _stt(observed, duration=2_400.0),
        max_duration_s=3_000.0,
    )

    assert evidence["accepted"] is True
    assert evidence["terminal_gate"]["spelling_variant_count"] == 1
    assert evidence["terminal_gate"]["hard_mismatch_count"] == 0
    assert "sailors" not in str(evidence)
    assert "sailours" not in str(evidence)


@pytest.mark.parametrize(
    ("observed_builder", "minimum_hard_mismatches"),
    [
        (lambda text: " ".join(text.split()[:-3]), 3),
        (lambda text: f"{text} this was a pretty tight", 5),
        (
            lambda text: " ".join(
                [*text.split()[:-2], text.split()[-1], text.split()[-2]]
            ),
            2,
        ),
    ],
    ids=["missing-final-words", "extra-terminal-phrase", "terminal-order-corruption"],
)
def test_long_form_terminal_misalignment_is_rejected(
    observed_builder, minimum_hard_mismatches: int
) -> None:
    expected = _long_form_with_sentinel(repeated=True)

    with pytest.raises(qwen_quality.QwenQualityError) as caught:
        qwen_quality.validate_output(
            expected,
            _stt(observed_builder(expected), duration=2_400.0),
            max_duration_s=3_000.0,
        )

    assert caught.value.code == "QWEN_OUTPUT_TEXT_MISMATCH"
    assert caught.value.evidence["terminal_gate"]["hard_mismatch_count"] >= minimum_hard_mismatches
    assert caught.value.evidence["terminal_gate"]["accepted"] is False


def test_long_form_gate_uses_retained_word_timestamps_not_padded_segment_text() -> None:
    expected = _long_form_with_sentinel(repeated=True)
    result = _stt(expected, duration=2_400.0)
    result["segments"][0]["text"] = f"{expected} this was a pretty tight"

    evidence = qwen_quality.validate_output(expected, result, max_duration_s=3_000.0)

    assert evidence["accepted"] is True
    result["segments"][0]["words"].extend(
        {"word": word, "start": 2_399.0, "end": 2_399.1}
        for word in "this was a pretty tight".split()
    )
    with pytest.raises(qwen_quality.QwenQualityError) as caught:
        qwen_quality.validate_output(expected, result, max_duration_s=3_000.0)

    assert caught.value.code == "QWEN_OUTPUT_TEXT_MISMATCH"


def test_long_form_without_retained_word_timestamps_fails_closed() -> None:
    expected = _long_form_with_sentinel(repeated=True)

    with pytest.raises(qwen_quality.QwenQualityError) as caught:
        qwen_quality.validate_output(
            expected,
            _stt(expected, duration=2_400.0, words=False),
            max_duration_s=3_000.0,
        )

    assert caught.value.code == "QWEN_OUTPUT_TEXT_MISMATCH"
    assert caught.value.evidence["terminal_gate"] == {
        "revision": "voicestudio.qwen-clone-quality.v2",
        "window_size": 24,
        "aligned_word_count": 0,
        "spelling_variant_count": 0,
        "hard_mismatch_count": 24,
        "accepted": False,
    }


def test_approximately_25k_long_form_uses_bounded_terminal_comparison(monkeypatch) -> None:
    expected = _long_form_with_sentinel()
    assert 20_000 < len(expected) < 30_000
    original_distance = qwen_quality._distance

    def reject_request_sized_distance(left, right):
        if max(len(left), len(right)) > 24:
            raise AssertionError("long-form terminal validation must stay bounded")
        return original_distance(left, right)

    monkeypatch.setattr(qwen_quality, "_distance", reject_request_sized_distance)
    evidence = qwen_quality.validate_output(
        expected,
        _stt(expected, duration=12_000.0),
        max_duration_s=13_000.0,
    )

    assert evidence["accepted"] is True
    assert evidence["comparison_method"] == {
        "tokens": "2-gram-multiset",
        "characters": "4-gram-multiset",
    }
    assert evidence["terminal_gate"]["window_size"] == 24
    with pytest.raises(qwen_quality.QwenQualityError) as caught:
        qwen_quality.validate_output(
            expected,
            _stt(f"{expected} this was a pretty tight", duration=12_000.0),
            max_duration_s=13_000.0,
        )

    assert caught.value.code == "QWEN_OUTPUT_TEXT_MISMATCH"


def test_existing_short_form_qwen_audit_contract_remains_v1() -> None:
    expected = "The existing audited Qwen clone path retains its short response contract."

    evidence = qwen_quality.validate_output(
        expected,
        _stt(expected, duration=12.0),
        max_duration_s=30.0,
    )

    assert evidence["accepted"] is True
    assert evidence["validator_revision"] == "voicestudio.qwen-clone-quality.v1"
    assert "terminal_gate" not in evidence


@pytest.mark.parametrize(
    "mutate_word",
    [
        lambda word, _duration: word.pop("start"),
        lambda word, _duration: word.pop("end"),
        lambda word, _duration: word.update(start=float("nan")),
        lambda word, _duration: word.update(end=float("inf")),
        lambda word, duration: word.update(start=duration),
        lambda word, duration: word.update(end=duration + 0.1),
        lambda word, _duration: word.update(start=1.0, end=0.9),
    ],
    ids=[
        "missing-start",
        "missing-end",
        "nonfinite-start",
        "nonfinite-end",
        "start-at-eof",
        "end-after-eof",
        "reversed-range",
    ],
)
def test_long_form_gate_fails_closed_for_invalid_word_timestamps(mutate_word) -> None:
    expected = _long_form_with_sentinel(repeated=True)
    result = _stt(expected, duration=2_400.0)
    mutate_word(result["segments"][0]["words"][-1], result["duration"])

    with pytest.raises(qwen_quality.QwenQualityError) as caught:
        qwen_quality.validate_output(expected, result, max_duration_s=3_000.0)

    assert caught.value.code == "QWEN_OUTPUT_TEXT_MISMATCH"
    assert caught.value.evidence["terminal_gate"]["accepted"] is False


def test_long_form_gate_fails_closed_for_descending_word_timestamps() -> None:
    expected = _long_form_with_sentinel(repeated=True)
    result = _stt(expected, duration=2_400.0)
    words = result["segments"][0]["words"]
    words[-2].update(start=1.0, end=1.1)
    words[-1].update(start=0.9, end=1.0)

    with pytest.raises(qwen_quality.QwenQualityError) as caught:
        qwen_quality.validate_output(expected, result, max_duration_s=3_000.0)

    assert caught.value.code == "QWEN_OUTPUT_TEXT_MISMATCH"
    assert caught.value.evidence["terminal_gate"]["accepted"] is False


@pytest.mark.parametrize(
    "mutate_word",
    [
        lambda word: word.pop("start"),
        lambda word: word.pop("end"),
        lambda word: word.update(start=float("nan")),
        lambda word: word.update(end=float("inf")),
        lambda word: word.update(start=1.0, end=0.9),
    ],
    ids=[
        "missing-start",
        "missing-end",
        "nonfinite-start",
        "nonfinite-end",
        "reversed-range",
    ],
)
def test_normalization_drops_invalid_word_timestamp_before_long_form_gate(mutate_word) -> None:
    expected = _long_form_with_sentinel(repeated=True)
    raw_words = [
        {"word": word, "start": 0.0, "end": 0.2}
        for word in expected.split()
    ]
    mutate_word(raw_words[-1])
    normalized_text, segments = transcription._normalize_segments(
        [{
            "start": 0.0,
            "end": 2_400.0,
            "text": expected,
            "words": raw_words,
        }],
        word_timestamps=True,
        audio_duration=2_400.0,
    )

    with pytest.raises(qwen_quality.QwenQualityError) as caught:
        qwen_quality.validate_output(
            expected,
            {
                "text": normalized_text,
                "duration": 2_400.0,
                "segments": segments,
            },
            max_duration_s=3_000.0,
        )

    assert caught.value.code == "QWEN_OUTPUT_TEXT_MISMATCH"
    assert caught.value.evidence["terminal_gate"]["accepted"] is False


def test_job_owned_reference_validation_attaches_redacted_evidence(
    monkeypatch, tmp_path: Path
) -> None:
    from backend import transcription

    reference = tmp_path / "aiden.wav"
    reference.write_bytes(b"immutable-reference")
    transcript = "Aiden reads a calm and accurately aligned reference."
    manager = object.__new__(generation.GenerationManager)
    manager._lock = threading.Lock()
    manager._mlx_audio_model = None
    manager._mlx_audio_model_repo = None
    manager._f5_tts_model = None
    manager._f5_tts_model_repo = None
    monkeypatch.setattr(manager, "_evict_loaded_models", lambda _reason: {})
    monkeypatch.setattr(qwen_quality, "cached_reference_evidence", lambda _key: None)
    monkeypatch.setattr(qwen_quality, "save_reference_evidence", lambda _key, _value: None)
    monkeypatch.setattr(
        transcription.manager,
        "transcribe_locked",
        lambda *_args, **_kwargs: _stt(transcript),
    )
    monkeypatch.setattr(
        transcription.manager,
        "release_memory_locked",
        lambda _reason: {},
    )
    job = generation.GenerationJob(
        job_id="reference-proof",
        mode="txt2speech",
        params={
            "repo": "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit",
            "text": "Generate this line.",
            "ref_transcript": transcript,
            "_reference_audio_path": str(reference),
        },
    )

    with generation._GEN_LOCK:
        manager._validate_qwen_reference_locked(job)

    evidence = job.params["_qwen_reference_validation"]
    assert evidence["accepted"] is True
    assert transcript not in str(evidence)


class _Sampler:
    def __init__(self, publish):
        self.publish = publish

    def start(self):
        return self

    def finish(self, **kwargs):
        return {"outcome": kwargs}


@pytest.mark.parametrize("budget", [400, 280, 230])
def test_qwen_duration_validation_uses_resolved_section_budget(budget: int) -> None:
    job = generation.GenerationJob(
        job_id=f"duration-{budget}",
        mode="txt2speech",
        params={
            "repo": QWEN_17B_BASE,
            "text": LONG_TEXT,
            "speed": 1.0,
            "_resolved_section_max_characters": budget,
        },
    )
    chunks = generation._internal_mlx_text_chunks(
        "qwen3-tts", QWEN_17B_BASE, LONG_TEXT, max_chars_override=budget,
    )
    expected = round(
        sum(qwen_quality.automatic_duration_limit(chunk, 1.0) for chunk in chunks)
        + (len(chunks) - 1) * 0.3,
        3,
    )

    assert generation.GenerationManager._qwen_job_duration_limit(job) == expected


def test_qwen_duration_validation_matches_the_chunker_without_a_resolved_budget() -> None:
    """A clone tier with no published section control must still be measurable.

    ``mlx-community/Qwen3-TTS-12Hz-1.7B-Base-4bit`` carries no
    ``section_size_control`` and is not a 0.6B build, so its jobs reach output
    validation without ``_resolved_section_max_characters``. Reading that key
    directly raised KeyError after a successful transcription, which surfaced as
    "Whisper Large could not validate the Qwen output" on every attempt. The
    ceiling must come from the same fallback the dispatch path used.
    """
    repo = "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-4bit"
    job = generation.GenerationJob(
        job_id="duration-no-resolved-budget",
        mode="txt2speech",
        params={"repo": repo, "text": LONG_TEXT, "speed": 1.0},
    )
    chunks = generation._internal_mlx_text_chunks("qwen3-tts", repo, LONG_TEXT)
    expected = round(
        sum(qwen_quality.automatic_duration_limit(chunk, 1.0) for chunk in chunks)
        + (len(chunks) - 1)
        * generation._long_form_join_pause_s("qwen3-tts", repo),
        3,
    )

    assert len(chunks) > 1
    assert generation.GenerationManager._qwen_job_duration_limit(job) == expected


@pytest.mark.parametrize("initial_budget", [400, 280, 230])
def test_rejected_local_qwen_output_retries_once_with_safer_settings(
    monkeypatch, tmp_path: Path, initial_budget: int
) -> None:
    manager = object.__new__(generation.GenerationManager)
    manager._lock = threading.Lock()
    manager._mlx_audio_model = None
    manager._mlx_audio_model_repo = None
    manager._f5_tts_model = None
    manager._f5_tts_model_repo = None
    manager._last_model_activity_at = None
    manager._consecutive_memory_failures = 0
    manager._restart_scheduled = False
    manager._loaded_model = None
    attempts = []

    def dispatch(job, path):
        attempts.append((
            job.params.get("_qwen_attempt_seed", job.params.get("seed")),
            job.params.get("_resolved_section_max_characters"),
        ))
        sf.write(path, np.zeros(2400, dtype=np.float32), 24000)

    def validate(job, _path):
        if len(attempts) == 1:
            raise qwen_quality.QwenQualityError(
                "QWEN_OUTPUT_TEXT_MISMATCH",
                "rejected control output",
                {"validator_revision": qwen_quality.VALIDATOR_REVISION},
            )
        job.quality_validation = {"accepted": True}

    monkeypatch.setattr(generation, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(generation, "TTS_AVAILABLE", True)
    monkeypatch.setattr(generation.resource_telemetry, "JobResourceSampler", _Sampler)
    monkeypatch.setattr(manager, "_dispatch_txt2speech", dispatch)
    monkeypatch.setattr(manager, "_validate_qwen_reference_locked", lambda _job: None)
    monkeypatch.setattr(manager, "_validate_qwen_output_locked", validate)
    monkeypatch.setattr(manager, "_record_local_revision_evidence", lambda _job: None)
    monkeypatch.setattr(manager, "_record_final_audio_evidence", lambda _job, _path: None)
    monkeypatch.setattr(manager, "_evict_loaded_models", lambda _reason: {})
    monkeypatch.setattr(manager, "_persist", lambda: None)

    job = generation.GenerationJob(
        job_id="quality-retry",
        mode="txt2speech",
        params={
            "repo": "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit",
            "voice_library_id": "aiden",
            "text": "A controlled local result.",
            "seed": 41,
            "_resolved_section_max_characters": initial_budget,
            "_qwen_reference_validation": {"accepted": True},
        },
    )
    manager._run_txt2speech(job)

    assert job.state == "done"
    assert job.quality_retry_count == 1
    assert job.quality_validation == {"accepted": True}
    assert attempts == [(41, initial_budget), (42, 230)]
    assert [entry["accepted"] for entry in job.quality_retry_history] == [False, True]
    assert job.quality_retry_history[0]["rejection_code"] == "QWEN_OUTPUT_TEXT_MISMATCH"


def test_qwen_section_control_copy_and_bounds_are_v2_truthful() -> None:
    frontend = Path(__file__).resolve().parents[1] / "frontend"
    markup = (frontend / "index.html").read_text(encoding="utf-8")
    script = (frontend / "app.js").read_text(encoding="utf-8")

    assert "Auto uses the audited 280-character policy" in markup
    assert "this.sectionSizeValue <= c.maximum" in script
    assert "this.sectionSizeValue <= c.runtime_default" not in script


def test_qwen_output_mismatch_on_retry_is_terminal_and_preserves_attempt_evidence(
    monkeypatch, tmp_path: Path
) -> None:
    manager = object.__new__(generation.GenerationManager)
    manager._lock = threading.Lock()
    manager._mlx_audio_model = None
    manager._mlx_audio_model_repo = None
    manager._f5_tts_model = None
    manager._f5_tts_model_repo = None
    manager._last_model_activity_at = None
    manager._consecutive_memory_failures = 0
    manager._restart_scheduled = False
    manager._loaded_model = None
    attempts = []
    text = " ".join(
        f"Controlled recovery sentence {index} remains transcript bound."
        for index in range(1, 9)
    )

    def dispatch(job, path):
        attempts.append({
            "seed": job.params.get("_qwen_attempt_seed", job.params.get("seed")),
            "section_max": job.params.get("_resolved_section_max_characters"),
            "repo": job.params["repo"],
            "text": job.params["text"],
            "voice_library_id": job.params.get("voice_library_id"),
        })
        sf.write(path, np.zeros(2400, dtype=np.float32), 24000)

    def validate(_job, _path):
        raise qwen_quality.QwenQualityError(
            "QWEN_OUTPUT_TEXT_MISMATCH",
            "deterministic recovery control mismatch",
            {
                "validator_revision": qwen_quality.VALIDATOR_REVISION,
                "kind": "output",
                "expected_tokens": len(text.split()),
                "observed_tokens": 0,
            },
        )

    monkeypatch.setattr(generation, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(generation, "TTS_AVAILABLE", True)
    monkeypatch.setattr(generation.resource_telemetry, "JobResourceSampler", _Sampler)
    monkeypatch.setattr(manager, "_dispatch_txt2speech", dispatch)
    monkeypatch.setattr(manager, "_validate_qwen_reference_locked", lambda _job: None)
    monkeypatch.setattr(manager, "_validate_qwen_output_locked", validate)
    monkeypatch.setattr(manager, "_record_local_revision_evidence", lambda _job: None)
    monkeypatch.setattr(manager, "_record_final_audio_evidence", lambda _job, _path: None)
    monkeypatch.setattr(manager, "_evict_loaded_models", lambda _reason: {})
    monkeypatch.setattr(manager, "_persist", lambda: None)

    job = generation.GenerationJob(
        job_id="quality-retry-terminal",
        mode="txt2speech",
        params={
            "repo": "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit",
            "voice_library_id": "aiden",
            "text": text,
            "seed": 41,
            "_resolved_section_max_characters": 400,
            "_qwen_reference_validation": {"accepted": True},
        },
    )
    manager._run_txt2speech(job)

    assert job.state == "error"
    assert job.error_code == "QWEN_OUTPUT_TEXT_MISMATCH"
    assert job.quality_retry_count == 1
    assert job.output_path is None
    assert list(tmp_path.iterdir()) == []
    assert [attempt["seed"] for attempt in attempts] == [41, 42]
    assert [attempt["section_max"] for attempt in attempts] == [
        400,
        230,
    ]
    assert all(attempt["repo"] == job.params["repo"] for attempt in attempts)
    assert all(attempt["text"] == text for attempt in attempts)
    assert all(attempt["voice_library_id"] == "aiden" for attempt in attempts)
    assert len(job.quality_retry_history) == 2
    assert [entry["attempt"] for entry in job.quality_retry_history] == [1, 2]
    assert [entry["rejection_code"] for entry in job.quality_retry_history] == [
        "QWEN_OUTPUT_TEXT_MISMATCH",
        "QWEN_OUTPUT_TEXT_MISMATCH",
    ]
    assert all("text" not in entry and "path" not in entry for entry in job.quality_retry_history)
    assert generation.GenerationManager._to_disk(job)["quality_retry_history"] == (
        job.quality_retry_history
    )
    assert job.serialize()["quality_retry_history"] == job.quality_retry_history


# ── numeral canonicalization ────────────────────────────────────────────────
# Fixtures below are the app's own recorded transcripts for the real probes
# (recovered exactly by verifying the `observed_sha256` the validator stored).
# Before canonicalization every one of the first two lines was rejected: the
# clone read the script correctly and Whisper wrote the number as digits.

PROSE_372 = (
    "The morning light came across the harbour slowly, touching the roofs of the old town "
    "and turning the water into a sheet of pale gold. Fishermen were already moving along the "
    "quay, coiling ropes and calling to one another in voices that carried easily over the "
    "still air. It promised to be a warm day, the kind that makes everyone linger a little "
    "longer than they meant to."
)
PROSE_372_TRANSCRIPT = PROSE_372.replace("harbour", "harbor")


def _stt_words(words: list[str], *, duration: float = 2_400.0) -> dict:
    """A Whisper result whose word timestamps are exactly ``words``, in order."""
    return {
        "text": " ".join(words),
        "model": _validator_repo(),
        "duration": duration,
        "segments": [{
            "start": 0.0,
            "end": duration,
            "text": " ".join(words),
            "words": [{"word": word, "start": 0.0, "end": 0.2} for word in words],
        }],
    }


def _legacy_error_rates(expected: str, observed: str) -> tuple[float, float]:
    """The pre-canonicalization arithmetic, so the regression stays visible."""
    token_error, _ = qwen_quality._error_rate(
        qwen_quality._tokens(expected), qwen_quality._tokens(observed), ngram_size=2
    )
    character_error, _ = qwen_quality._error_rate(
        list(qwen_quality._normalized_text(expected).replace(" ", "")),
        list(qwen_quality._normalized_text(observed).replace(" ", "")),
        ngram_size=4,
    )
    return round(token_error, 4), round(character_error, 4)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("one two three", ["123"]),
        ("one two three four", ["1234"]),
        ("oh one five", ["15"]),
        ("forty five", ["45"]),
        ("seven forty five", ["745"]),
        ("nineteen eighty four", ["1984"]),
        ("three hundred twenty four", ["324"]),
        ("one hundred and five", ["105"]),
        ("one thousand two hundred", ["1200"]),
        ("twenty four", ["24"]),
        ("zero", ["0"]),
        ("007", ["7"]),
        ("a1b2c3", ["a1b2c3"]),
        ("MP3 and B2B", ["mp3", "and", "b2b"]),
        ("你好世界", ["你", "好", "世", "界"]),
        # A number word followed by an ordinary word must not swallow it.
        ("one silver bell", ["1", "silver", "bell"]),
        ("twenty four silver bells", ["24", "silver", "bells"]),
        # "and" only joins a run that a scale word opened.
        ("one and two", ["1", "and", "2"]),
    ],
)
def test_numeral_canonicalization_units(text: str, expected: list[str]) -> None:
    assert qwen_quality._canonical_tokens(qwen_quality._tokens(text)) == expected


@pytest.mark.parametrize(
    ("name", "expected", "observed", "legacy", "after"),
    [
        (
            "studio-short-37",
            "Studio Hub smoke test, one two three.",
            "Studio Hub Smoke Test 123",
            (0.4286, 0.3793),
            {"token_error_rate": 0.0, "character_error_rate": 0.0,
             "expected_tokens": 5, "observed_tokens": 5},
        ),
        (
            "studio-short-37-split-digits",
            "Studio Hub smoke test, one two three.",
            "Studio Hub Smoke Test 1 2 3",
            (0.4286, 0.3793),
            {"token_error_rate": 0.6, "character_error_rate": 0.0,
             "expected_tokens": 5, "observed_tokens": 7},
        ),
        (
            "capture-poller-41",
            "Capture poller smoke test, one two three.",
            "Capture Polar Smoke Test 123",
            (0.5714, 0.3939),
            {"token_error_rate": 0.2, "character_error_rate": 0.08,
             "expected_tokens": 5, "observed_tokens": 5},
        ),
        (
            "studio-nine-token-49",
            "Studio Hub smoke test, one two three. Run a1b2c3.",
            "Studio Hub Smoke Test 123 Run A1B2C3",
            (0.3333, 0.2895),
            {"token_error_rate": 0.0, "character_error_rate": 0.0,
             "expected_tokens": 7, "observed_tokens": 7},
        ),
        (
            "clean-prose-372",
            PROSE_372,
            PROSE_372_TRANSCRIPT,
            (0.0147, 0.0033),
            {"token_error_rate": 0.0147, "character_error_rate": 0.0034,
             "expected_tokens": 68, "observed_tokens": 68},
        ),
        (
            "train-time-62",
            "The train left the station at seven forty five in the morning.",
            "The train left the station at 7.45 in the morning.",
            (0.25, 0.28),
            {"token_error_rate": 0.2, "character_error_rate": 0.0,
             "expected_tokens": 10, "observed_tokens": 11},
        ),
    ],
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_real_clone_transcripts_pass_the_unchanged_thresholds(
    name: str, expected: str, observed: str,
    legacy: tuple[float, float], after: dict,
) -> None:
    """Every one of these is correct audio; only the spelling of the number differed."""
    similarity = qwen_quality._similarity(expected, observed)

    assert _legacy_error_rates(expected, observed) == legacy, name
    assert similarity == {**similarity, **after}, name

    evidence = qwen_quality.validate_output(
        expected,
        _stt(observed, duration=8.0) if len(observed.split()) < 200
        else _stt(observed, duration=17.4),
        max_duration_s=90.0,
    )

    assert evidence["accepted"] is True, name
    # The unchanged gate is an AND: at least one side of it must stay clear.
    assert not (
        evidence["token_error_rate"] > 0.40
        and evidence["character_error_rate"] > 0.25
    ), name
    if name in {"studio-short-37", "studio-nine-token-49"}:
        assert evidence["token_error_rate"] == 0.0
        assert evidence["character_error_rate"] == 0.0


@pytest.mark.parametrize(
    ("name", "expected", "observed", "code", "minimums"),
    [
        (
            "unrelated-speech",
            "Studio Hub smoke test, one two three.",
            "Completely different words about weather and traffic patterns today.",
            "QWEN_OUTPUT_TEXT_MISMATCH",
            {"token_error_rate": 1.8, "character_error_rate": 2.3333},
        ),
        (
            "half-the-words-missing",
            "The morning light came across the harbour slowly, touching the roofs of the old town.",
            "The morning light came across the harbour slowly.",
            "QWEN_OUTPUT_TEXT_MISMATCH",
            {"token_error_rate": 0.4667, "character_error_rate": 0.4058},
        ),
        (
            "hallucinated-tail",
            "The ship crossed the quiet sea before sunrise.",
            "The ship crossed the quiet sea before sunrise and then it kept going on and on "
            "about nothing at all.",
            "QWEN_OUTPUT_TEXT_MISMATCH",
            {"token_error_rate": 1.5, "character_error_rate": 1.1053},
        ),
        (
            "repeated-output",
            "The ship crossed the quiet sea before sunrise.",
            " ".join(["The ship crossed the quiet sea"] * 8),
            "QWEN_OUTPUT_TEXT_MISMATCH",
            {"token_error_rate": 5.25, "character_error_rate": 4.3421},
        ),
        (
            "unrelated-numerals-do-not-buy-a-pass",
            "The harbour light moved slowly across the quiet water.",
            "Seven forty five hundred unrelated spoken words were generated instead.",
            "QWEN_OUTPUT_TEXT_MISMATCH",
            {"token_error_rate": 1.0, "character_error_rate": 0.8444},
        ),
    ],
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_bad_output_is_still_rejected_after_canonicalization(
    name: str, expected: str, observed: str, code: str, minimums: dict,
) -> None:
    similarity = qwen_quality._similarity(expected, observed)

    assert similarity["token_error_rate"] == minimums["token_error_rate"], name
    assert similarity["character_error_rate"] == minimums["character_error_rate"], name

    with pytest.raises(qwen_quality.QwenQualityError) as caught:
        qwen_quality.validate_output(
            expected, _stt(observed, duration=8.0), max_duration_s=90.0,
        )

    assert caught.value.code == code, name
    assert caught.value.evidence.get("accepted") is not True, name
    assert caught.value.evidence["token_error_rate"] > 0.40, name


def _collapse_observed_run(words: list[str], first: str, second: str, joined: str) -> list[str]:
    """Replace a two-word observed run with the single digit token Whisper emits."""
    observed: list[str] = []
    index = 0
    while index < len(words):
        if words[index] == first and index + 1 < len(words) and words[index + 1] == second:
            observed.append(joined)
            index += 2
            continue
        observed.append(words[index])
        index += 1
    return observed


def test_terminal_gate_matches_a_collapsed_run_with_its_digits() -> None:
    """Expected spells "twenty four"; Whisper emits one token "24" for the same run."""
    expected = _long_form_with_sentinel(repeated=True)
    words = expected.split()
    observed = _collapse_observed_run(words, "twenty", "four", "24")
    assert len(observed) == len(words) - 1

    evidence = qwen_quality.validate_output(
        expected, _stt_words(observed), max_duration_s=3_000.0,
    )

    assert evidence["accepted"] is True
    assert evidence["terminal_gate"]["hard_mismatch_count"] == 0
    assert evidence["terminal_gate"]["spelling_variant_count"] == 0
    # The alignment evidence stays the raw Whisper word count.
    assert evidence["terminal_gate"]["aligned_word_count"] == len(observed)


def test_terminal_gate_folds_spelled_words_toward_the_expected_digits() -> None:
    """Expected already carries "24"; the observed stream still spells it out."""
    expected = _long_form_with_sentinel(repeated=True).replace("twenty four", "24")
    observed = _long_form_with_sentinel(repeated=True).split()

    evidence = qwen_quality.validate_output(
        expected, _stt_words(observed), max_duration_s=3_000.0,
    )

    assert evidence["accepted"] is True
    assert evidence["terminal_gate"]["hard_mismatch_count"] == 0


def test_canonicalized_run_does_not_shrink_the_gate_window_below_its_floor() -> None:
    """A run just short of the window still fails closed rather than passing."""
    expected = " ".join(
        f"Marker {index} records a distinct harbor observation before sunset."
        for index in range(1, 60)
    ) + " count one two three"
    words = expected.split()

    with pytest.raises(qwen_quality.QwenQualityError) as caught:
        qwen_quality.validate_output(
            expected, _stt_words(words[:20]), max_duration_s=3_000.0,
        )

    assert caught.value.code == "QWEN_OUTPUT_TEXT_MISMATCH"
