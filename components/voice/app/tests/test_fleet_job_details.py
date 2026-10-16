import base64
import hashlib
import io
import json
import logging
import os
import shutil
import threading
import time
import wave
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend import fleet_auth, generation, job_details, main, tts_quality
from backend.generation import GenerationJob, GenerationManager
from backend.job_details import JobMediaError, build_job_details, resolve_job_media


WAV_BYTES = b"RIFF" + bytes(range(64))


def voice_job(tmp_path):
    output = tmp_path / "output" / "job-1.wav"
    prepared = tmp_path / "prepared" / "reference.wav"
    saved = tmp_path / "voices" / "voice-1" / "reference.wav"
    output.parent.mkdir()
    prepared.parent.mkdir()
    saved.parent.mkdir(parents=True)
    output.write_bytes(WAV_BYTES)
    prepared.write_bytes(WAV_BYTES)
    saved.write_bytes(WAV_BYTES)
    return GenerationJob(
        "job-1", "txt2speech", {
            "repo": "org/model", "text": "Narration text",
            "ref_transcript": "Reference words", "voice_library_id": "voice-1",
            "language": "en", "speed": 1.0, "instruct": "calm",
            "_reference_audio_path": str(prepared),
            "_reference_audio_sha256": "a" * 64,
            "_reference_source_sha256": "b" * 64,
            "client_request_id": "studiohub:private-request",
            "token": "private-credential",
        }, state="done", output_path=str(output), origin="api",
        started_at=10.0, finished_at=18.0, chunk_index=1, chunk_total=3,
        sha256="c" * 64, client_request_params={"secret": "private"},
        quality_validation={"internal": "private"},
        quality_retry_history=[{"internal": "private"}],
    )


@pytest.fixture
def configured_job(tmp_path, monkeypatch):
    job = voice_job(tmp_path)
    monkeypatch.setattr(job_details, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(job_details.reference_audio, "ROOT", tmp_path / "prepared")
    monkeypatch.setattr(job_details.voices, "VOICES_DIR", tmp_path / "voices")
    monkeypatch.setattr(
        job_details.voices.library,
        "reference_path",
        lambda voice_id: (
            tmp_path / "voices" / voice_id / "reference.wav"
            if voice_id == "voice-1" else None
        ),
    )
    return job


def test_voice_detail_projection_is_allowlisted_and_path_free(configured_job):
    details = build_job_details(configured_job, "fleet-secret", now=100)

    assert details["schema"] == "kh-studio.job-details.v1"
    assert details["studio"] == "voice"
    assert details["job"] == {
        "id": "job-1",
        "state": "done",
        "model": "org/model",
        "operation": "txt2speech",
        "created_at": configured_job.created_at,
        "started_at": 10.0,
        "finished_at": 18.0,
        "runtime_s": 8.0,
        "origin": "api",
        "origin_device": None,
    }
    assert details["inputs"] == {
        "prompt": None,
        "negative_prompt": None,
        "text": "Narration text",
        "reference_transcript": "Reference words",
        "parameters": {
            "voice_library_id": "voice-1",
            "language": "en",
            "speed": 1.0,
            "instruct": "calm",
            "chunk_index": 1,
            "chunk_total": 3,
        },
    }
    assert len(details["references"]) == 1
    assert len(details["outputs"]) == 1
    for item, kind, name in (
        (details["references"][0], "reference", "reference.wav"),
        (details["outputs"][0], "output", "job-1.wav"),
    ):
        assert item == {
            "kind": kind,
            "name": name,
            "media_type": "audio/wav",
            "size_bytes": len(WAV_BYTES),
            "duration_s": None,
            "handle": item["handle"],
            "expires_at": 400,
        }
        assert item["handle"]
        assert "job-1" not in item["handle"]
        assert name not in item["handle"]

    serialized = json.dumps(details)
    for private in (
        "_reference_audio_path", "_reference_audio_sha256",
        "_reference_source_sha256", "client_request_id",
        "client_request_params", "quality_validation", "quality_retry_history",
        "private-credential", configured_job.params["_reference_audio_path"],
        configured_job.output_path, "a" * 64, "b" * 64, "c" * 64,
    ):
        assert private not in serialized


@pytest.mark.parametrize(
    ("state", "progress", "expected"),
    (
        ("running", 0.42, 0.42),
        ("queued", 2.0, 1.0),
        ("running", -1.0, 0.0),
        ("running", float("nan"), 0.0),
        ("running", "legacy", 0.0),
    ),
)
def test_active_voice_detail_progress_is_bounded(
    configured_job, state, progress, expected,
):
    configured_job.state = state
    configured_job.progress = progress

    details = build_job_details(configured_job, "fleet-secret", now=100)

    assert details["job"]["progress"] == expected


def test_transcription_details_label_the_result_as_transcription_without_media():
    from backend.transcription import TranscriptionActivityJob

    job = TranscriptionActivityJob(
        job_id="stt-1", model="org/whisper", state="done",
        params={
            "repo": "org/whisper", "text": "Subtitle transcript",
            "language": "en", "word_timestamps": True,
            "input_filename": "episode.wav",
        },
        origin="local_ui", created_at=10.0, started_at=11.0, finished_at=13.0,
    )

    details = build_job_details(job, "fleet-secret", now=20.0)

    assert details["job"]["operation"] == "transcription"
    assert details["inputs"]["text"] == "Subtitle transcript"
    assert details["inputs"]["parameters"] == {
        "language": "en", "word_timestamps": True, "input_filename": "episode.wav",
    }
    assert details["references"] == []
    assert details["outputs"] == []


def test_saved_library_reference_is_used_only_without_a_private_reference(configured_job):
    saved_job = GenerationJob(
        "saved-job", "txt2speech", {
            "repo": "org/model", "text": "Saved voice",
            "voice_library_id": "voice-1",
        }, state="done",
    )

    details = build_job_details(saved_job, "fleet-secret", now=100)
    target = resolve_job_media(
        saved_job, details["references"][0]["handle"], "fleet-secret", now=100,
    )

    assert details["references"][0]["name"] == "reference.wav"
    assert target.path == (job_details.voices.VOICES_DIR / "voice-1" / "reference.wav").resolve()


@pytest.mark.parametrize(
    ("collection", "root", "expected"),
    (
        ("references", "prepared", "reference.wav"),
        ("outputs", "output", "job-1.wav"),
    ),
)
def test_signed_handles_resolve_only_the_recorded_media(
    configured_job, collection, root, expected,
):
    details = build_job_details(configured_job, "fleet-secret", now=100)
    handle = details[collection][0]["handle"]

    target = resolve_job_media(configured_job, handle, "fleet-secret", now=100)

    expected_root = (
        job_details.reference_audio.ROOT if root == "prepared" else job_details.OUTPUT_DIR
    )
    assert target.path == (expected_root / expected).resolve()
    assert target.media_type == "audio/wav"
    assert target.name == expected


def test_handle_expires_after_exactly_300_seconds(configured_job):
    handle = build_job_details(configured_job, "fleet-secret", now=100)["outputs"][0]["handle"]

    assert resolve_job_media(configured_job, handle, "fleet-secret", now=399.999).path.name == "job-1.wav"
    with pytest.raises(JobMediaError, match="handle_expired"):
        resolve_job_media(configured_job, handle, "fleet-secret", now=400)
    with pytest.raises(JobMediaError, match="handle_expired"):
        resolve_job_media(configured_job, handle, "fleet-secret", now=401)


def test_tampered_handle_is_permission_denied(configured_job):
    handle = build_job_details(configured_job, "fleet-secret", now=100)["outputs"][0]["handle"]
    tampered = handle[:-1] + ("A" if handle[-1] != "A" else "B")

    with pytest.raises(JobMediaError, match="permission_denied"):
        resolve_job_media(configured_job, tampered, "fleet-secret", now=100)


def test_pruned_media_returns_media_removed_even_with_fresh_handle(configured_job):
    output = job_details.OUTPUT_DIR / "job-1.wav"
    output.unlink()
    fresh_handle = build_job_details(configured_job, "fleet-secret", now=100)["outputs"][0]["handle"]

    with pytest.raises(JobMediaError, match="media_removed"):
        resolve_job_media(configured_job, fresh_handle, "fleet-secret", now=100)


def test_outside_root_media_is_never_issued_a_handle(configured_job, tmp_path, monkeypatch):
    outside = tmp_path / "outside.wav"
    outside.write_bytes(WAV_BYTES)
    private_job = GenerationJob(
        "outside-private", "txt2speech",
        {"_reference_audio_path": str(outside)}, output_path=str(outside),
    )
    saved_job = GenerationJob(
        "outside-saved", "txt2speech", {"voice_library_id": "voice-1"},
    )
    monkeypatch.setattr(
        job_details.voices.library, "reference_path", lambda _voice_id: outside,
    )

    private_details = build_job_details(private_job, "fleet-secret", now=100)
    saved_details = build_job_details(saved_job, "fleet-secret", now=100)

    assert private_details["references"] == []
    assert private_details["outputs"] == []
    assert saved_details["references"] == []


def test_symlink_under_approved_root_is_rejected_at_access(configured_job, tmp_path):
    reference = job_details.reference_audio.ROOT / "reference.wav"
    handle = build_job_details(configured_job, "fleet-secret", now=100)["references"][0]["handle"]
    outside = tmp_path / "outside.wav"
    outside.write_bytes(WAV_BYTES)
    reference.unlink()
    reference.symlink_to(outside)

    with pytest.raises(JobMediaError, match="media_removed"):
        resolve_job_media(configured_job, handle, "fleet-secret", now=100)


def test_symlinked_approved_root_is_rejected_at_access(tmp_path, monkeypatch):
    outside_root = tmp_path / "outside"
    outside_root.mkdir()
    output = outside_root / "job-root-link.wav"
    output.write_bytes(WAV_BYTES)
    linked_root = tmp_path / "linked-output"
    linked_root.symlink_to(outside_root, target_is_directory=True)
    monkeypatch.setattr(job_details, "OUTPUT_DIR", linked_root)
    job = GenerationJob(
        "job-root-link", "txt2speech", {}, state="done",
        output_path=str(linked_root / output.name),
    )
    handle = build_job_details(job, "fleet-secret", now=100)["outputs"][0]["handle"]

    with pytest.raises(JobMediaError, match="media_removed"):
        resolve_job_media(job, handle, "fleet-secret", now=100)


def test_handle_is_bound_to_job_kind_and_index(configured_job):
    handle = build_job_details(configured_job, "fleet-secret", now=100)["references"][0]["handle"]
    other = GenerationJob(
        "job-2", configured_job.mode, configured_job.params,
        state="done", output_path=configured_job.output_path,
    )

    with pytest.raises(JobMediaError, match="permission_denied"):
        resolve_job_media(other, handle, "fleet-secret", now=100)

    payload, signature = handle.split(".", 1)
    decoded = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    for key, value in (("k", "output"), ("i", 1)):
        changed = dict(decoded, **{key: value})
        encoded = base64.urlsafe_b64encode(
            json.dumps(changed, separators=(",", ":"), sort_keys=True).encode()
        ).rstrip(b"=").decode()
        with pytest.raises(JobMediaError, match="permission_denied"):
            resolve_job_media(configured_job, f"{encoded}.{signature}", "fleet-secret", now=100)


def test_fleet_detail_and_media_http_contract(configured_job, monkeypatch, caplog):
    manager = GenerationManager.__new__(GenerationManager)
    manager._jobs = {configured_job.job_id: configured_job}
    monkeypatch.setattr(main, "gen_manager", manager)
    monkeypatch.setattr(fleet_auth, "load_token", lambda: "fleet-secret")

    assert TestClient(main.app).get(
        "/api/fleet/jobs/job-1/details",
        headers={"host": "worker.example"},
    ).status_code == 401

    client = TestClient(
        main.app,
        headers={"X-Studio-Token": "fleet-secret", "host": "worker.example"},
    )
    missing = client.get("/api/fleet/jobs/missing/details")
    assert missing.status_code == 404
    assert missing.json() == {"detail": {"code": "job_not_found"}}

    details_response = client.get("/api/fleet/jobs/job-1/details")
    assert details_response.status_code == 200
    handle = details_response.json()["outputs"][0]["handle"]

    inline = client.get(f"/api/fleet/jobs/job-1/media/{handle}")
    assert inline.status_code == 200
    assert inline.content == WAV_BYTES
    assert inline.headers["content-disposition"].startswith("inline;")

    ranged = client.get(
        f"/api/fleet/jobs/job-1/media/{handle}",
        headers={"Range": "bytes=0-0"},
    )
    assert ranged.status_code == 206
    assert ranged.content == WAV_BYTES[:1]
    assert ranged.headers["content-range"] == f"bytes 0-0/{len(WAV_BYTES)}"

    download = client.get(f"/api/fleet/jobs/job-1/media/{handle}?download=true")
    assert download.status_code == 200
    assert download.headers["content-disposition"].startswith("attachment;")

    for response in (details_response, inline, ranged, download):
        assert response.headers["cache-control"] == "no-store, private, max-age=0"
        assert response.headers["pragma"] == "no-cache"
        assert response.headers["x-content-type-options"] == "nosniff"

    (job_details.OUTPUT_DIR / "job-1.wav").unlink()
    removed = client.get(f"/api/fleet/jobs/job-1/media/{handle}")
    assert removed.status_code == 410
    assert removed.json() == {"detail": {"code": "media_removed"}}

    private_values = (
        configured_job.params["text"], configured_job.output_path,
        "fleet-secret", handle,
    )
    with caplog.at_level(logging.WARNING):
        denied = client.get(f"/api/fleet/jobs/job-1/media/{handle}x")
    assert denied.status_code == 403
    assert denied.json() == {"detail": {"code": "permission_denied"}}
    for value in private_values:
        assert value not in denied.text
        assert value not in caplog.text


def test_fleet_detail_and_media_errors_always_set_private_no_store_headers(
    configured_job, monkeypatch,
):
    manager = GenerationManager.__new__(GenerationManager)
    manager._jobs = {configured_job.job_id: configured_job}
    monkeypatch.setattr(main, "gen_manager", manager)
    monkeypatch.setattr(fleet_auth, "load_token", lambda: "fleet-secret")
    unauthorized = TestClient(main.app).get(
        "/api/fleet/jobs/job-1/details", headers={"host": "worker.example"},
    )
    client = TestClient(
        main.app,
        headers={"X-Studio-Token": "fleet-secret", "host": "worker.example"},
    )
    missing_details = client.get("/api/fleet/jobs/missing/details")
    missing_media = client.get("/api/fleet/jobs/missing/media/not-a-handle")
    handle = client.get("/api/fleet/jobs/job-1/details").json()["outputs"][0]["handle"]
    denied = client.get(f"/api/fleet/jobs/job-1/media/{handle}x")
    expired_handle = build_job_details(
        configured_job, "fleet-secret", now=0,
    )["outputs"][0]["handle"]
    expired = client.get(f"/api/fleet/jobs/job-1/media/{expired_handle}")
    (job_details.OUTPUT_DIR / "job-1.wav").unlink()
    removed = client.get(f"/api/fleet/jobs/job-1/media/{handle}")

    assert [response.status_code for response in (
        unauthorized, missing_details, missing_media, denied, expired, removed,
    )] == [401, 404, 404, 403, 410, 410]
    assert missing_details.json() == {"detail": {"code": "job_not_found"}}
    assert missing_media.json() == {"detail": {"code": "job_not_found"}}
    assert denied.json() == {"detail": {"code": "permission_denied"}}
    assert expired.json() == {"detail": {"code": "handle_expired"}}
    assert removed.json() == {"detail": {"code": "media_removed"}}
    for response in (
        unauthorized, missing_details, missing_media, denied, expired, removed,
    ):
        assert response.headers["cache-control"] == "no-store, private, max-age=0"
        assert response.headers["pragma"] == "no-cache"
        assert response.headers["x-content-type-options"] == "nosniff"


# ── Rejected-section review candidates ────────────────────────────────────────────────────────

# Pronom PR0010 Mark, Chapter 05 part 1, internal Section 6 of 7 (203 code points, 80 marks).
KHMER_SECTION = (
    "វិបុលបានស្តាប់ឮការពិតដ៏រន្ធត់នេះ ប៉ុន្តែគាត់មិនអាចនិយាយតបតអ្វីបានទៀតទេ។ "
    "បំពង់ករបស់គាត់ប្រែជាតូច ហើយគាត់អាចបញ្ចេញបានត្រឹមតែសំឡេងយំខ្សាវៗតែប៉ុណ្ណោះ។ "
    "សម្លៀកបំពាក់មនុស្សចាស់បានគ្របដណ្តប់លើខ្លួនគាត់ទាំងស្រុង។"
)
REVIEW_JOB = "c85e8c5d0a1b"
SILENT = {"voiced_s": 2.54, "min_voiced_s": 4.06, "characters": 203,
          "accepted": False, "rejection_code": "TTS_SECTION_SILENT"}


def wav_bytes(frames: int = 2400, rate: int = 24000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x01\x02" * frames)
    return buffer.getvalue()


@pytest.fixture
def review(tmp_path, monkeypatch):
    """A manager whose final Section 6 render is refused, run through the real outer runner."""
    monkeypatch.setattr(generation, "REVIEW_ROOT", tmp_path / "private-jobs" / "rejected-sections")
    monkeypatch.setattr(generation, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(generation, "HISTORY_FILE", tmp_path / "output" / ".history.json")
    monkeypatch.setattr(generation, "TTS_AVAILABLE", True)
    monkeypatch.setattr(fleet_auth, "load_token", lambda: "fleet-secret")
    manager = GenerationManager.__new__(GenerationManager)
    manager._lock = threading.RLock()
    manager._jobs = {}
    manager._restart_scheduled = False
    manager._consecutive_memory_failures = 0
    manager._last_model_activity_at = None
    for name in ("_validate_qwen_reference_locked", "_evict_loaded_models",
                 "_finish_native_resource_usage"):
        monkeypatch.setattr(manager, name, lambda *args, **kwargs: None)
    monkeypatch.setattr(manager, "has_loaded_model", lambda: False)
    monkeypatch.setattr(manager, "_section_transcript_locked",
                        lambda *args, **kwargs: (None, "script", None))
    monkeypatch.setattr(tts_quality, "audio_metrics", lambda path: {})

    def refuse(*args, **kwargs):
        raise tts_quality.SectionQualityError("TTS_SECTION_SILENT", "came out silent", dict(SILENT))

    monkeypatch.setattr(tts_quality, "check_section", refuse)
    model = SimpleNamespace(family="voxcpm-mlx", repo="mlx-community/VoxCPM2-4bit")
    pcm = wav_bytes()
    state = {"pcm": pcm, "barrier": None, "manager": manager, "tmp": tmp_path}

    def dispatch(job, output_path, *, attempt=tts_quality.SECTION_RETRIES, at_barrier=None,
                 symlink=False):
        workspace = tmp_path / f"voice_sections_{job.job_id}"
        workspace.mkdir()
        segment = workspace / "section_006.wav"
        (workspace / "real.wav" if symlink else segment).write_bytes(pcm)
        if symlink:
            segment.symlink_to(workspace / "real.wav")
        job.section_quality = {"revision": tts_quality.REVISION, "sections": [
            {"section": 6, "attempt": n, **SILENT} for n in range(1, attempt + 1)]}
        try:
            manager._section_accepted_locked(job, model, KHMER_SECTION, segment, 6, 7, attempt)
        except tts_quality.SectionQualityError:
            if at_barrier:
                state["barrier"] = at_barrier(job)
            raise
        finally:
            shutil.rmtree(workspace)   # the worker's own workspace cleanup

    def run(job_id=REVIEW_JOB, **kwargs):
        job = GenerationJob(job_id, "txt2speech", {
            "repo": model.repo, "text": KHMER_SECTION, "voice_library_id": "voice-af496dcef199",
            "speed": 1.0, "client_request_id": "studiohub:batch-1:0", "token": "private",
            "_reference_audio_sha256": "e" * 64,
        })
        manager._jobs[job.job_id] = job
        monkeypatch.setattr(manager, "_dispatch_txt2speech",
                            lambda job, output_path: dispatch(job, output_path, **kwargs))
        manager._run_txt2speech(job)
        return job

    state["run"] = run
    return state


def review_folder(state, job_id=REVIEW_JOB):
    return generation.REVIEW_ROOT / job_id


def test_review_candidate_is_published_only_after_the_terminal_error_is_durable(review):
    def barrier(job):
        folder = review_folder(review)
        with pytest.raises(JobMediaError, match="review_candidate_not_found"):
            job_details.review_candidate_details(REVIEW_JOB, "fleet-secret")
        return {"state": job.state, "pcm": (folder / "section.wav").read_bytes(),
                "receipt": (folder / "receipt.json").exists(), "pending": job.review_pending is not None}

    job = review["run"](at_barrier=barrier)

    assert review["barrier"] == {"state": "running", "pcm": review["pcm"],
                                 "receipt": False, "pending": True}
    assert job.state == "error" and job.error_code == "TTS_SECTION_SILENT"
    assert job.error.startswith("SectionQualityError: Section 6 of 7 came out silent on all 4 attempts")
    assert job.output_path is None and job.review_pending is None
    details = job_details.review_candidate_details(REVIEW_JOB, "fleet-secret", now=100)
    receipt = details["receipt"]
    assert receipt["active"] is False and receipt["disposition"] == "review_only"
    assert receipt["worker_job_id"] == REVIEW_JOB
    assert receipt["client_request_id"] == "studiohub:batch-1:0"
    assert receipt["section"] == {
        "index": 6, "total": 7, "attempt": 4, "text": KHMER_SECTION, "codepoints": 203,
        "utf8_bytes": 601,
        "text_sha256": "711790fe1d97d3597641a0244f84d25b0c9aaf5db11afa6d7b44c210c44cc607",
    }
    final = job.section_quality["sections"][-1]
    assert "check_seconds" in final and receipt["quality"]["final"] == final
    assert receipt["quality"]["rejection_code"] == "TTS_SECTION_SILENT"
    assert receipt["settings"]["voice_library_id"] == "voice-af496dcef199"
    assert receipt["revisions"] == {"reference_audio_sha256": "e" * 64}
    assert receipt["audio"] == {
        "media_type": "audio/wav", "bytes": len(review["pcm"]),
        "sha256": hashlib.sha256(review["pcm"]).hexdigest(),
        "sample_rate_hz": 24000, "channels": 1, "duration_s": 0.1,
    }
    terminal = receipt["terminal"]
    assert terminal["state"] == "error" and terminal["finished_at"] == job.finished_at
    assert terminal["error_sha256"] == hashlib.sha256(job.error.encode()).hexdigest()
    assert terminal["final_quality_sha256"] == generation.review_sha256(final)
    history = json.loads(generation.HISTORY_FILE.read_text())["jobs"][0]
    assert history["state"] == "error" and history["section_quality"]["sections"][-1] == final
    serialized = json.dumps(details)
    for private in ("private", str(review["tmp"]), "fleet-secret"):
        assert private not in serialized
    folder = review_folder(review)
    assert sorted(os.listdir(folder)) == ["receipt.json", "section.wav"]
    assert oct(os.stat(folder).st_mode & 0o777) == "0o700"
    for name in ("receipt.json", "section.wav"):
        assert oct(os.stat(folder / name).st_mode & 0o777) == "0o600"


def test_between_the_visible_error_and_the_receipt_the_worker_says_pending(review, monkeypatch):
    """The Hub sees `error` first; until the receipt is durable it must hear "ask again", not 404."""
    monkeypatch.setattr(main, "gen_manager", review["manager"])
    client = TestClient(main.app, headers={"X-Studio-Token": "fleet-secret", "host": "worker.example"})
    route = f"/api/fleet/jobs/{REVIEW_JOB}/review-candidate"
    seen = []

    def ask(stage):
        response = client.get(route)
        seen.append((stage, review["manager"]._jobs[REVIEW_JOB].state, response.status_code,
                     response.json().get("detail")))

    monkeypatch.setattr(review["manager"], "_evict_loaded_models", lambda *args, **kwargs: ask("evicting"))
    readback = generation._durable_history_row
    monkeypatch.setattr(generation, "_durable_history_row",
                        lambda job_id: (ask("history readback"), readback(job_id))[1])

    review["run"]()

    pending = {"code": "review_candidate_pending"}
    assert seen == [("evicting", "error", 409, pending), ("history readback", "error", 409, pending)]
    assert client.get(route).status_code == 200
    assert client.get("/api/fleet/jobs/ffffffffffff/review-candidate").status_code == 404


def test_a_refused_publication_stops_saying_pending(review, monkeypatch):
    monkeypatch.setattr(main, "gen_manager", review["manager"])
    client = TestClient(main.app, headers={"X-Studio-Token": "fleet-secret", "host": "worker.example"})

    def mutate(job):
        with open(review_folder(review) / "section.wav", "ab") as handle:
            handle.write(b"x")

    review["run"](at_barrier=mutate)

    assert review["manager"]._jobs[REVIEW_JOB].review_pending is None
    assert client.get(f"/api/fleet/jobs/{REVIEW_JOB}/review-candidate").json() == {
        "detail": {"code": "review_candidate_not_found"}}


@pytest.mark.parametrize("crash", ("staged", "receipt_tmp", "partial_receipt"))
def test_a_crash_before_the_receipt_never_makes_a_candidate_readable(review, crash, monkeypatch):
    monkeypatch.setattr(GenerationManager, "_publish_review_candidate",
                        staticmethod(lambda job, persisted: None))   # the process died here
    review["run"]()
    folder = review_folder(review)
    if crash == "receipt_tmp":
        (folder / "receipt.json.tmp").write_text("{}")
    if crash == "partial_receipt":
        (folder / "receipt.json").write_text('{"schema": "studiohub.rejected-voice-review')

    restarted = GenerationManager.__new__(GenerationManager)
    restarted._lock = threading.RLock()
    restarted._jobs = {}
    restarted._load_history()   # a new process: nothing infers, promotes or repairs the folder

    assert restarted._jobs[REVIEW_JOB].state == "error"
    with pytest.raises(JobMediaError, match="review_candidate_not_found"):
        job_details.review_candidate_details(REVIEW_JOB, "fleet-secret")
    assert (folder / "section.wav").read_bytes() == review["pcm"]


@pytest.mark.parametrize("attempt", (0, 1, 2))
def test_earlier_attempts_capture_nothing(review, attempt, tmp_path, monkeypatch):
    segment = tmp_path / "section_006.wav"
    segment.write_bytes(review["pcm"])
    job = GenerationJob(REVIEW_JOB, "txt2speech", {"text": KHMER_SECTION}, state="running")
    job.section_quality = {"revision": tts_quality.REVISION, "sections": []}
    review["manager"]._jobs[REVIEW_JOB] = job
    model = SimpleNamespace(family="voxcpm-mlx", repo="mlx-community/VoxCPM2-4bit")

    accepted = review["manager"]._section_accepted_locked(
        job, model, KHMER_SECTION, segment, 6, 7, attempt)

    assert accepted is False and job.review_pending is None
    assert not generation.REVIEW_ROOT.exists()


def test_a_cancelled_job_never_publishes_its_staged_candidate(review):
    def cancel(job):
        job.cancel_event.set()

    job = review["run"](at_barrier=cancel)

    assert job.state == "cancelled"
    assert not (review_folder(review) / "receipt.json").exists()
    with pytest.raises(JobMediaError):
        job_details.review_candidate_details(REVIEW_JOB, "fleet-secret")


def test_capture_refusals_keep_the_original_quality_error(review, monkeypatch):
    job = review["run"](job_id="not-hex-job")
    assert job.error_code == "TTS_SECTION_SILENT" and not generation.REVIEW_ROOT.exists()

    generation.REVIEW_ROOT.mkdir(parents=True)
    for n in range(generation.REVIEW_MAX_ENTRIES):
        (generation.REVIEW_ROOT / f"{n:012x}").mkdir()
    job = review["run"](job_id="aaaaaaaaaaaa")
    assert job.error_code == "TTS_SECTION_SILENT"
    assert not (generation.REVIEW_ROOT / "aaaaaaaaaaaa").exists()

    shutil.rmtree(generation.REVIEW_ROOT)
    monkeypatch.setattr(generation, "REVIEW_MAX_PCM_BYTES", len(review["pcm"]) - 1)
    job = review["run"](job_id="bbbbbbbbbbbb")
    assert job.error_code == "TTS_SECTION_SILENT"
    assert not (generation.REVIEW_ROOT / "bbbbbbbbbbbb").exists()


def test_an_existing_folder_is_never_replaced(review):
    existing = review_folder(review)
    existing.mkdir(parents=True)
    (existing / "section.wav").write_bytes(b"owner kept this")

    job = review["run"]()

    assert job.error_code == "TTS_SECTION_SILENT"
    assert sorted(os.listdir(existing)) == ["section.wav"]
    assert (existing / "section.wav").read_bytes() == b"owner kept this"


def test_a_symlinked_root_or_source_is_refused(review, tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    generation.REVIEW_ROOT.parent.mkdir(parents=True)
    generation.REVIEW_ROOT.symlink_to(outside, target_is_directory=True)
    review["run"]()
    assert os.listdir(outside) == []

    generation.REVIEW_ROOT.unlink()
    job = review["run"](job_id="cccccccccccc", symlink=True)
    assert job.error_code == "TTS_SECTION_SILENT"
    assert not (generation.REVIEW_ROOT / "cccccccccccc").exists()


def test_a_mutated_or_moved_staged_pcm_is_never_published(review):
    def mutate(job):
        with open(review_folder(review) / "section.wav", "ab") as handle:
            handle.write(b"x")

    review["run"](at_barrier=mutate)
    assert not (review_folder(review) / "receipt.json").exists()

    def move(job):
        shutil.move(review_folder(review, "dddddddddddd"), review["tmp"] / "_trash-moved")
        review_folder(review, "dddddddddddd").mkdir()

    review["run"](job_id="dddddddddddd", at_barrier=move)
    assert not (review_folder(review, "dddddddddddd") / "receipt.json").exists()


def test_review_candidate_http_contract(review, monkeypatch):
    review["run"]()
    review["manager"]._jobs.clear()   # cleared Voice history is not the authority
    monkeypatch.setattr(main, "gen_manager", review["manager"])
    assert TestClient(main.app).get(
        f"/api/fleet/jobs/{REVIEW_JOB}/review-candidate", headers={"host": "worker.example"},
    ).status_code == 401
    client = TestClient(main.app, headers={"X-Studio-Token": "fleet-secret", "host": "worker.example"})

    details = client.get(f"/api/fleet/jobs/{REVIEW_JOB}/review-candidate")
    assert details.status_code == 200
    handle = details.json()["media"]["handle"]
    media = client.get(f"/api/fleet/jobs/{REVIEW_JOB}/review-candidate/media/{handle}")
    assert media.status_code == 200 and media.content == review["pcm"]
    assert media.headers["content-type"] == "audio/wav"
    assert media.headers["content-length"] == str(len(review["pcm"]))
    assert media.headers["x-content-sha256"] == hashlib.sha256(review["pcm"]).hexdigest()
    assert media.headers["content-disposition"].startswith("inline;")

    expired = job_details._make_handle(REVIEW_JOB, job_details.REVIEW_KIND, 0, time.time() - 1, "fleet-secret")
    other = job_details._make_handle("eeeeeeeeeeee", job_details.REVIEW_KIND, 0, time.time() + 60, "fleet-secret")
    output_kind = job_details._make_handle(REVIEW_JOB, "output", 0, time.time() + 60, "fleet-secret")
    answers = {
        "tampered": client.get(f"/api/fleet/jobs/{REVIEW_JOB}/review-candidate/media/{handle}x"),
        "expired": client.get(f"/api/fleet/jobs/{REVIEW_JOB}/review-candidate/media/{expired}"),
        "cross_job": client.get(f"/api/fleet/jobs/{REVIEW_JOB}/review-candidate/media/{other}"),
        "output_kind": client.get(f"/api/fleet/jobs/{REVIEW_JOB}/review-candidate/media/{output_kind}"),
        "missing": client.get("/api/fleet/jobs/ffffffffffff/review-candidate"),
        "traversal": client.get("/api/fleet/jobs/..%2F..%2Foutput/review-candidate"),
        "post": client.post(f"/api/fleet/jobs/{REVIEW_JOB}/review-candidate"),
        "delete": client.delete(f"/api/fleet/jobs/{REVIEW_JOB}/review-candidate"),
    }
    assert {name: response.status_code for name, response in answers.items()} == {
        "tampered": 403, "expired": 410, "cross_job": 403, "output_kind": 403,
        "missing": 404, "traversal": 404, "post": 405, "delete": 405,
    }
    for response in (details, media, *answers.values()):
        assert response.headers["cache-control"] == "no-store, private, max-age=0"
    # A review handle never opens the ordinary media route either.
    review["manager"]._jobs[REVIEW_JOB] = GenerationJob(REVIEW_JOB, "txt2speech", {}, state="error")
    assert client.get(f"/api/fleet/jobs/{REVIEW_JOB}/media/{handle}").status_code == 403

    (review_folder(review) / "section.wav").write_bytes(b"RIFF" + b"\0" * (len(review["pcm"]) - 4))
    assert client.get(f"/api/fleet/jobs/{REVIEW_JOB}/review-candidate").status_code == 404
    assert client.get(f"/api/fleet/jobs/{REVIEW_JOB}/review-candidate/media/{handle}").status_code == 404


def test_owner_move_to_trash_ends_access_without_deleting_bytes(review, tmp_path):
    review["run"]()
    trash = tmp_path / "_trash" / "rejected-sections"
    trash.mkdir(parents=True)
    shutil.move(review_folder(review), trash / REVIEW_JOB)

    with pytest.raises(JobMediaError, match="review_candidate_not_found"):
        job_details.review_candidate_details(REVIEW_JOB, "fleet-secret")
    assert (trash / REVIEW_JOB / "section.wav").read_bytes() == review["pcm"]
