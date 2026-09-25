"""The voice smoke's recorded plan, checked against the catalog and the models' own data.

`qualification/model-inputs.json` planned the 0.1.0 proof around
`mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit`, which ST-13.B cut from the catalog.
Nothing failed at the time: `packaging/smoke_voice.py` read the record, sent the request,
and the packaged catalogue refused the model - so the smoke reported the TTS role as
unexercised and the packaging receipt kept a hole where the speech proof belongs.

These tests are stated over the record and the catalog, not over today's repo name:

* the recorded `tts` entry resolves in the catalog the app ships, so the next prune cannot
  leave the smoke planning a run around a model nobody offers;
* the recorded `smoke_params` are facts the model itself publishes - no speaker name is
  invented, and no key is sent that the worker does not read;
* the historical 0.1.0 entry stays in the file as evidence rather than being rewritten.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from backend import catalog

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "packaging"))
import smoke_voice  # noqa: E402


def _record() -> dict:
    return json.loads(smoke_voice.PILOT_INPUTS.read_text(encoding="utf-8"))


def _shipped_tts() -> dict:
    entries = [
        entry for entry in smoke_voice.selected_pilot_models(_record())
        if entry.get("role") == "tts"
    ]
    assert len(entries) == 1, f"the smoke plans {len(entries)} TTS entries; expected one"
    return entries[0]


def test_the_smoke_plans_a_shipped_tts_model() -> None:
    """The defect this fixes: the plan named a model the catalog had cut."""
    entry = _shipped_tts()
    model = catalog.get_model(entry["repo"])
    assert model is not None, (
        f"the smoke plans {entry['repo']!r}, which the packaged catalog does not ship - "
        "the smoke would report the TTS role as unexercised again"
    )
    assert "tts" in model.capabilities


def test_the_smoke_plans_a_shipped_asr_model() -> None:
    """The same rule for the other role: a pruned ASR model must not stay in the plan.

    The smoke raises when its ASR leg exercises nothing, so a record naming a model the
    transcription registry no longer serves fails a run rather than a test - this is the test.
    """
    from backend import transcription

    record = _record()
    entries = [
        entry for entry in smoke_voice.selected_pilot_models(record)
        if entry.get("role") == "asr"
    ]
    assert len(entries) == 1, f"the smoke plans {len(entries)} ASR entries; expected one"
    served = {model.repo for model in transcription.TRANSCRIPTION_MODELS}
    assert entries[0]["repo"] in served, (
        f"the smoke plans {entries[0]['repo']!r}, which the transcription registry does not "
        f"serve: {sorted(served)}"
    )


def test_every_shipped_smoke_entry_is_the_packaged_manifest() -> None:
    """Every recorded file is the shipped manifest's own file, at its own size and hash.

    The two layouts mean different things and are checked differently:

    * `hf-cache` (what this record uses) is a copied snapshot under
      `hub/models--<repo>/snapshots/<revision>/`, whose paths are the publisher's
      (`source_path` in the manifest). A snapshot entry may record a subset - the Whisper
      processor lives under its own repository - so every recorded file must appear in the
      manifest, not the reverse.
    * `package` is the app's own verified install, whose paths are the manifest's package
      paths; there the sets must be equal, because the whole package is the thing verified.

    Either way a record that invents a file or a hash fails here rather than at run time.
    """
    record = _record()
    for entry in record["shipped_smoke"]["models"]:
        manifest = json.loads(
            (ROOT / "model-manifests" / f"{_package_id(entry)}.json").read_text(encoding="utf-8")
        )
        assert manifest["model_id"] == entry["repo"], entry["repo"]
        assert manifest["revision"] == entry["revision"], entry["repo"]
        layout = entry.get("layout") or "hf-cache"
        shipped = (
            {(f["path"], f["size"], f["sha256"]) for f in manifest["files"]}
            if layout == "package" else _snapshot_rows(manifest)
        )
        recorded = {(f["path"], f["bytes"], f["sha256"]) for f in entry["files"]}
        assert recorded <= shipped, (
            f"{entry['repo']}: {sorted(recorded - shipped)} is not in the shipped manifest"
        )
        if layout == "package":
            assert recorded == shipped, (
                f"{entry['repo']}: a package-layout entry must record the whole package"
            )


def _package_id(entry: dict) -> str:
    """The package id for a recorded repo, read from the manifests rather than restated."""
    for path in sorted((ROOT / "model-manifests").glob("*.json")):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest.get("model_id") == entry["repo"]:
            return str(manifest["package_id"])
    raise AssertionError(f"no shipped manifest offers {entry['repo']}")


def _snapshot_rows(manifest: dict) -> set[tuple[str, int, str]]:
    """Where each manifest file sits inside a copied Hugging Face snapshot.

    The manifest records the package layout (`model/config.json`); a snapshot holds the
    publisher's own paths - `source_path` where the capture recorded one, and otherwise the
    package path with its leading `model/` directory removed.
    """
    rows = set()
    for entry in manifest["files"]:
        path = entry.get("source_path") or entry["path"]
        if not entry.get("source_path") and path.startswith("model/"):
            path = path[len("model/"):]
        rows.add((path, entry["size"], entry["sha256"]))
    return rows


def test_the_recorded_smoke_parameters_are_the_models_own_published_facts() -> None:
    """No invented speaker, and no request key the lane does not read.

    Qwen3-TTS Base publishes no preset-speaker roster - `QWEN3_PRESET_SPEAKERS` belongs to
    the CustomVoice family this catalog does not ship - so a `preset_speaker` here would be
    a name the model cannot resolve. `language` must be one the row publishes, and every
    key must be a real request field.
    """
    entry = _shipped_tts()
    model = catalog.get_model(entry["repo"])
    params = entry.get("smoke_params") or {}
    assert params, "a TTS entry with no parameters sends the model no voice at all"
    assert "preset_speaker" not in params, (
        f"{entry['repo']} is a cloning model and publishes no preset-speaker roster; a "
        "speaker name here would be invented"
    )
    # A cloning model needs a reference; the recorded one is the project's tracked clip.
    assert "cloning" in (catalog.serialize_model(model)["modes"] or [])
    reference = entry.get("reference") or {}
    assert reference.get("file") and reference.get("sha256"), (
        "a cloning TTS entry must record the reference its run stages, or the run cannot "
        "happen"
    )
    if "language" in params:
        support = model.language_support or catalog.LanguageSupport(codes=model.languages)
        assert params["language"] in support.codes, (
            f"{params['language']!r} is not among {entry['repo']}'s published languages"
        )
    body = (ROOT / "components/voice/app/backend/main.py").read_text(encoding="utf-8")
    section = body[body.index("class Txt2SpeechBody"):]
    section = section[:section.index("\n\n\n")]
    for name in params:
        assert f"{name}:" in section, f"{name!r} is not a field of Txt2SpeechBody"


def test_the_historical_pilot_entry_is_kept_as_evidence() -> None:
    """The shipped entry is added, not substituted: the 0.1.0 record stays readable."""
    record = _record()
    historical = [model["repo"] for model in record["models"]]
    assert "mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit" in historical
    assert "shipped_smoke" in record
    # And the historical entry is not what a run is planned from.
    planned = {entry["repo"] for entry in smoke_voice.selected_pilot_models(record)}
    assert "mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit" not in planned


def test_a_package_layout_entry_is_verified_where_the_app_installs_it(tmp_path) -> None:
    """The layout the shipped model is installed in is the layout the check understands.

    Before this, `validate_pilot_inputs` only knew the Hugging Face cache shape, so a
    record naming the app's own package library failed validation with "Invalid cache
    reference" before the smoke launched anything.
    """
    package_id = "example-voice-4bit"
    revision = "a" * 40
    body = b"weights"
    import hashlib as _hashlib

    entry = {
        "repo": "example/voice", "role": "tts", "layout": "package",
        "package_id": package_id, "revision": revision,
        "files": [{"path": "model/config.json", "bytes": len(body),
                   "sha256": _hashlib.sha256(body).hexdigest()}],
    }
    root = tmp_path / package_id / revision / "model"
    root.mkdir(parents=True)
    (root / "config.json").write_bytes(body)
    smoke_voice.validate_pilot_inputs(tmp_path, {"models": [entry]})
    (root / "config.json").write_bytes(b"weightz")
    with pytest.raises(ValueError, match="checksum"):
        smoke_voice.validate_pilot_inputs(tmp_path, {"models": [entry]})


def test_the_recorded_reference_is_the_tracked_clip_and_is_checksum_checked() -> None:
    """A reference that is not the recorded bytes would make the run a different run."""
    record = _record()
    assert smoke_voice.recorded_reference(record) is not None
    rewritten = json.loads(json.dumps(record))
    rewritten["shipped_smoke"]["models"][0]["reference"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="reference clip"):
        smoke_voice.recorded_reference(rewritten)


def test_readiness_comes_from_the_workers_own_ready_file(tmp_path) -> None:
    """The worker passes its sockets to uvicorn, so uvicorn logs no "Uvicorn running on ...".

    Readiness therefore has to be the ready file the worker writes - and a file recorded for a
    different pid (a previous launch, a stray process) must not count.
    """
    import subprocess

    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        ready = tmp_path / "ready.json"
        log = tmp_path / "worker.log"
        ready.write_text(json.dumps({"url": "http://127.0.0.1:1", "pid": process.pid + 1}))
        with pytest.raises(RuntimeError, match="did not become ready"):
            smoke_voice._await_ready(process, ready, log, timeout=0.4)
        ready.write_text(json.dumps({"url": "http://127.0.0.1:9", "pid": process.pid}))
        assert smoke_voice._await_ready(process, ready, log, timeout=5) == "http://127.0.0.1:9"
    finally:
        process.terminate()
        process.wait(timeout=10)


def test_a_model_that_needs_no_reference_keeps_the_plain_request_path(monkeypatch) -> None:
    """The plain route is byte-for-byte what it was; only a recorded clone uses the other."""
    calls = []

    def fake_request(url, token=None, body=None):
        calls.append((url, body))
        return 200, {"job": {"id": "job-1", "state": "done"}}

    audio = b"RIFF" + b"\0" * 100
    import hashlib as _hashlib
    import io

    response = io.BytesIO(audio)
    response.headers = {"X-Content-SHA256": _hashlib.sha256(audio).hexdigest()}
    monkeypatch.setattr(smoke_voice, "request", fake_request)
    monkeypatch.setattr(smoke_voice.urllib.request, "urlopen", lambda *a, **k: response)
    run = smoke_voice._run_tts(
        "http://127.0.0.1:1", "token", "example/tts", {"language": "en"}, Path("/tmp"))
    assert calls[0][0].endswith("/api/generate/txt2speech")
    assert calls[0][1]["language"] == "en"
    assert run["audio_bytes"] == len(audio)


def test_a_recorded_reference_run_posts_the_clip_through_the_workers_own_route(
        monkeypatch, tmp_path) -> None:
    """A cloning model refuses a plain request, so the recorded clip goes with the body."""
    import hashlib as _hashlib
    import io

    clip_bytes = b"RIFF....WAVE"
    clip = tmp_path / "reference.wav"
    clip.write_bytes(clip_bytes)
    seen = {}

    def fake_urlopen(request, timeout=None):
        seen["url"] = request.full_url
        seen["data"] = request.data
        seen["type"] = request.headers.get("Content-type") or request.headers.get("Content-Type")
        response = io.BytesIO(json.dumps({"job": {"id": "job-2"}}).encode())
        response.status = 200
        return response

    monkeypatch.setattr(smoke_voice.urllib.request, "urlopen", fake_urlopen)
    status, payload = smoke_voice._request_with_reference(
        "http://127.0.0.1:1", "token", "example/tts", "hello", {"language": "en"},
        {"path": clip, "sha256": _hashlib.sha256(clip_bytes).hexdigest(),
         "transcript": "the words in the clip"})
    assert status == 200 and payload["job"]["id"] == "job-2"
    assert seen["url"].endswith("/api/generate/txt2speech/reference")
    assert seen["type"].startswith("multipart/form-data; boundary=")
    assert b'name="request_json"' in seen["data"]
    assert b'name="source_sha256"' in seen["data"]
    assert b'name="audio"; filename="reference.wav"' in seen["data"]
    assert b'"language": "en"' in seen["data"]
    # The reference's own transcript travels with the request: Qwen3-TTS Base refuses a
    # reference whose words it cannot align, and the route reads them from the body.
    assert b'"ref_transcript": "the words in the clip"' in seen["data"]
    assert clip_bytes in seen["data"]
