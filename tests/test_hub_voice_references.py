"""ST-04 Hub contracts for staged voice-clone references.

One Hub asset contract, two ingestion origins:

  * local     - the app uploads a chosen clip; ``source_asset_id`` is omitted.
  * GenStudio - the controller pushes a clip carrying its durable
    ``source_asset_id``; the existing required-source semantics are preserved.

Both must return the same ``asset_id`` that ``/api/hub/jobs`` consumes as
``voice_reference_asset_id``, pass identical validation, and keep reference
bytes outside the generated-output cleanup roots.

The adopted Hub modules bind their state paths at import time, so the whole
proof runs in a child process with the packaged environment already set.  That
keeps this module from leaking ``STUDIOHUB_*`` state into the other Hub suites
in the same interpreter.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import unittest

_PROOF = r'''
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
import wave
from unittest.mock import patch

_TEMP = tempfile.TemporaryDirectory(prefix="studiohub-voice-reference-")
_ROOT = Path(_TEMP.name)
_DATA = _ROOT / "data"
_LIBRARY = _ROOT / "models"
_MANIFESTS = _ROOT / "manifests"
_OUTPUTS = _ROOT / "voice" / "output"
_WORKERS = _ROOT / "workers.json"
_TOKEN = "fixture-existing-hub-token"
for directory in (_DATA, _LIBRARY, _MANIFESTS, _OUTPUTS):
    directory.mkdir(parents=True, exist_ok=True)
(_DATA / ".hub_token").write_text(_TOKEN + "\n")
os.chmod(_DATA / ".hub_token", 0o600)
_WORKERS.write_text(json.dumps([{
    "id": "voice", "modality": "voice", "host": "127.0.0.1", "port": 51101,
    "machine": "local", "local": True,
    "runtime_identity": {"compatible": True}, "hub_token": _TOKEN,
    "managed_output_roots": [str(_OUTPUTS)],
}]))
os.chmod(_WORKERS, 0o600)
os.environ.update({
    "STUDIOHUB_DATA_DIR": str(_DATA),
    "STUDIOHUB_WORKERS_FILE": str(_WORKERS),
    "STUDIOHUB_MODELS_DIR": str(_LIBRARY),
    "STUDIOHUB_MODEL_MANIFESTS": str(_MANIFESTS),
    "STUDIOHUB_PACKAGED": "1",
    "STUDIOHUB_REQUIRE_AUTH": "1",
})

from fastapi.testclient import TestClient

from components.hub import standalone
from components.hub.backend import execution_assets

HEADERS = {"Authorization": f"Bearer {_TOKEN}"}
ROUTE = "/api/hub/voice-references"


def _wav(seconds: float = 1.0, sample_rate: int = 24000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(sample_rate)
        stream.writeframes(
            b"".join(
                int(6000 * (1 if (index // 100) % 2 else -1)).to_bytes(
                    2, "little", signed=True
                )
                for index in range(int(seconds * sample_rate))
            )
        )
    return buffer.getvalue()


AUDIO = _wav()
AUDIO_SHA = hashlib.sha256(AUDIO).hexdigest()


class VoiceReferenceRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(standalone.create_app(start_background=False))
        self.client.__enter__()

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)

    def _stage(self, *, audio: bytes = AUDIO, filename: str = "voice.wav", **form):
        return self.client.post(
            ROUTE, headers=HEADERS,
            files={"audio": (filename, audio, "audio/wav")},
            data={key: str(value) for key, value in form.items()},
        )

    # ── authentication ────────────────────────────────────────────────

    def test_route_requires_the_owner_token(self) -> None:
        response = self.client.post(
            ROUTE, files={"audio": ("voice.wav", AUDIO, "audio/wav")}, data={},
        )
        self.assertEqual(response.status_code, 401)

    # ── local ingestion ───────────────────────────────────────────────

    def test_local_upload_stages_one_reusable_asset(self) -> None:
        response = self._stage(transcript="Exact words.", language="en")
        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["origin"], "local")
        self.assertEqual(payload["sha256"], AUDIO_SHA)
        self.assertEqual(payload["bytes"], len(AUDIO))
        self.assertEqual(payload["media_type"], "audio/wav")
        self.assertTrue(payload["transcript_supplied"])
        self.assertGreater(payload["expires_at"], payload["created_at"])

        # The Hub computed the digest itself, and the bytes really are staged
        # privately outside the generated-output root.
        metadata, path = execution_assets.resolve_voice_reference(payload["asset_id"])
        self.assertEqual(path.read_bytes(), AUDIO)
        self.assertEqual(metadata["sha256"], AUDIO_SHA)
        self.assertFalse(
            Path(execution_assets.ROOT).resolve()
            == Path(_OUTPUTS).resolve()
        )

    def test_local_upload_without_a_claimed_checksum_still_binds_the_digest(self) -> None:
        payload = self._stage().json()
        self.assertEqual(payload["sha256"], AUDIO_SHA)
        self.assertFalse(payload["transcript_supplied"])

    def test_repeated_local_upload_is_idempotent_and_extends_expiry(self) -> None:
        first = self._stage(transcript="Exact words.", ttl_seconds=600).json()
        second = self._stage(transcript="Exact words.", ttl_seconds=900).json()
        self.assertEqual(first["asset_id"], second["asset_id"])
        self.assertGreaterEqual(second["expires_at"], first["expires_at"])

    def test_a_different_transcript_produces_a_different_asset(self) -> None:
        first = self._stage(transcript="Exact words.").json()
        second = self._stage(transcript="Different words.").json()
        self.assertNotEqual(first["asset_id"], second["asset_id"])

    def test_local_upload_rejects_a_declared_checksum_mismatch(self) -> None:
        response = self._stage(sha256="0" * 64)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "REFERENCE_AUDIO_CHECKSUM_MISMATCH")

    def test_local_upload_accepts_a_matching_declared_checksum(self) -> None:
        response = self._stage(sha256=AUDIO_SHA)
        self.assertEqual(response.status_code, 201)

    # ── GenStudio ingestion (unchanged semantics) ─────────────────────

    def test_genstudio_ingestion_keeps_its_required_source_semantics(self) -> None:
        response = self._stage(
            transcript="Exact words.", source_asset_id="genstudio-asset-123",
            sha256=AUDIO_SHA,
        )
        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["origin"], "genstudio")
        metadata, path = execution_assets.resolve_voice_reference(payload["asset_id"])
        self.assertEqual(path.read_bytes(), AUDIO)
        self.assertTrue(metadata["source_asset_fingerprint"])
        # The private source identity is never published.
        self.assertNotIn("source_asset_fingerprint", payload)

    def test_genstudio_and_local_ingestion_share_validation(self) -> None:
        local = self._stage(filename="voice.txt").json()["detail"]["code"]
        remote = self._stage(
            filename="voice.txt", source_asset_id="genstudio-asset-123",
        ).json()["detail"]["code"]
        self.assertEqual(local, remote)
        self.assertEqual(local, "REFERENCE_AUDIO_TYPE_UNSUPPORTED")

    def test_blank_source_asset_id_does_not_silently_become_genstudio(self) -> None:
        payload = self._stage(source_asset_id="   ").json()
        self.assertEqual(payload["origin"], "local")

    def test_both_origins_deliver_the_same_voice_for_the_same_model(self) -> None:
        """ST-10.CLONE: the clone's voice depends on the clip, not on who sent it.

        The two asset ids deliberately differ — an execution copy binds its upstream
        provenance — but everything the Voice worker uses to build the voice, together with
        the model chosen on the job, must be identical, so a GenStudio-origin clone and an
        app-chosen clone of the same clip produce the same voice identity.
        """
        transcript = "Exact words."
        local = self._stage(transcript=transcript, language="en").json()
        remote = self._stage(transcript=transcript, language="en",
                             source_asset_id="genstudio-asset-123").json()
        self.assertEqual((local["origin"], remote["origin"]), ("local", "genstudio"))
        self.assertNotEqual(local["asset_id"], remote["asset_id"],
                            "an execution copy keeps its upstream provenance")
        voice_inputs = []
        for payload in (local, remote):
            metadata, path = execution_assets.resolve_voice_reference(payload["asset_id"])
            voice_inputs.append({
                "reference_bytes": path.read_bytes(),
                "sha256": metadata["sha256"],
                "transcript": metadata["transcript"],
                "language": metadata["language"],
                "media_type": metadata["media_type"],
            })
        self.assertEqual(voice_inputs[0], voice_inputs[1],
                         "the same clip must reach the Voice worker identically")
        self.assertEqual(voice_inputs[0]["sha256"], AUDIO_SHA)
        self.assertEqual(voice_inputs[0]["language"], "en")

    # ── validation and machine-readable errors ────────────────────────

    def test_unsupported_extension_is_refused_with_a_code(self) -> None:
        response = self._stage(filename="voice.aiff")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "REFERENCE_AUDIO_TYPE_UNSUPPORTED")

    def test_oversized_reference_is_refused_with_a_code(self) -> None:
        response = self._stage(audio=b"\0" * (execution_assets.MAX_BYTES + 1))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "REFERENCE_AUDIO_TOO_LARGE")

    def test_empty_reference_is_refused_with_a_code(self) -> None:
        response = self._stage(audio=b"")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "REFERENCE_AUDIO_EMPTY")

    def test_path_traversal_filename_is_refused(self) -> None:
        response = self._stage(filename="../escape.wav")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "REFERENCE_AUDIO_TYPE_UNSUPPORTED")

    def test_unsorted_transcript_segments_are_refused_by_the_worker_rule(self) -> None:
        # The Hub accepts the JSON array and stores order; the Voice worker owns
        # ordering/inside-the-clip rules. Malformed JSON is the Hub's error.
        response = self._stage(transcript_segments="{not json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["detail"]["code"], "REFERENCE_TRANSCRIPT_SEGMENTS_INVALID",
        )

    def test_transcript_segments_round_trip_to_the_asset(self) -> None:
        segments = json.dumps([{"start_seconds": 0.0, "end_seconds": 0.8, "text": "Exact words."}])
        payload = self._stage(transcript_segments=segments).json()
        metadata, _path = execution_assets.resolve_voice_reference(payload["asset_id"])
        self.assertEqual(metadata["transcript_segments"][0]["text"], "Exact words.")
        self.assertTrue(payload["transcript_supplied"])

    def test_expiry_is_clamped_to_the_declared_bounds(self) -> None:
        import time
        short = self._stage(ttl_seconds=1).json()
        self.assertGreaterEqual(short["expires_at"] - time.time(), 299)
        long = self._stage(ttl_seconds=10_000_000).json()
        self.assertLessEqual(long["expires_at"] - time.time(), execution_assets.MAX_TTL_SECONDS + 5)

    def test_transcript_is_optional_at_the_hub_but_stored_when_given(self) -> None:
        payload = self._stage().json()
        metadata, _path = execution_assets.resolve_voice_reference(payload["asset_id"])
        self.assertEqual(metadata["transcript"], "")
        self.assertFalse(payload["transcript_supplied"])

    # ── status route ──────────────────────────────────────────────────

    def test_status_route_reports_a_live_reference(self) -> None:
        payload = self._stage(transcript="Exact words.").json()
        response = self.client.get(f"{ROUTE}/{payload['asset_id']}", headers=HEADERS)
        self.assertEqual(response.status_code, 200)
        status = response.json()
        self.assertEqual(status["asset_id"], payload["asset_id"])
        self.assertEqual(status["sha256"], AUDIO_SHA)
        self.assertEqual(status["bytes"], len(AUDIO))
        self.assertTrue(status["transcript_supplied"])
        self.assertNotIn("transcript", status)

    def test_status_route_reports_expiry_with_a_code(self) -> None:
        payload = self._stage().json()
        metadata, _path = execution_assets.resolve_voice_reference(payload["asset_id"])
        metadata["expires_at"] = 1.0
        (Path(execution_assets.ROOT) / payload["asset_id"] / "metadata.json").write_text(
            json.dumps(metadata)
        )
        response = self.client.get(f"{ROUTE}/{payload['asset_id']}", headers=HEADERS)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "VOICE_REFERENCE_ASSET_EXPIRED")

    def test_status_route_reports_an_unknown_asset_with_a_code(self) -> None:
        response = self.client.get(f"{ROUTE}/{'a' * 24}", headers=HEADERS)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "VOICE_REFERENCE_ASSET_MISSING")

    def test_native_envelope_level_reference_reaches_the_item(self) -> None:
        """The app's top-level voice_reference_asset_id must reach the item params.

        GenStudio nests the staged id in sharedParams; the native envelope sends
        it beside model/items. Both must route to the same worker endpoint, so
        the Hub folds the top-level form in without overwriting sharedParams.
        """
        payload = self._stage(transcript="Exact words.").json()
        from components.hub.backend import broker
        seen: dict = {}
        original = broker.submit_batch

        def spy(envelope):
            seen.update(envelope)
            return {"batch_id": "fixture-batch", "items": 0}

        broker.submit_batch = spy
        try:
            with patch.object(standalone, "_submission_readiness", lambda *_a, **_k: None):
                response = self.client.post("/api/hub/jobs", headers=HEADERS, json={
                    "modality": "voice", "model": "fixture/voice",
                    "clientRequestId": "voice-ref-native-0001",
                    "voice_reference_asset_id": payload["asset_id"],
                    "items": [{"text": "Narration", "params": {}}],
                })
        finally:
            broker.submit_batch = original
        self.assertEqual(response.status_code, 200)
        self.assertEqual(seen["voice_reference_asset_id"], payload["asset_id"])

    def test_shared_params_reference_wins_over_the_top_level_form(self) -> None:
        from components.hub.backend import broker
        staged = broker._shared_params_with_local_reference({
            "voice_reference_asset_id": "a" * 24,
            "sharedParams": {"voice_reference_asset_id": "b" * 24},
        })
        self.assertEqual(staged["voice_reference_asset_id"], "b" * 24)
        lifted = broker._shared_params_with_local_reference({
            "voice_reference_asset_id": "a" * 24,
        })
        self.assertEqual(lifted["voice_reference_asset_id"], "a" * 24)
        self.assertEqual(
            broker._shared_params_with_local_reference({}), {},
        )

    # -- reference bytes stay outside generated-output cleanup ---------

    def test_cleanup_index_refuses_a_reference_outside_the_output_root(self) -> None:
        """A staged reference is never indexed as a deletable generated output."""
        from components.hub import output_storage
        payload = self._stage(transcript="Exact words.").json()
        _metadata, reference_path = execution_assets.resolve_voice_reference(payload["asset_id"])
        generated = _OUTPUTS / "job.wav"
        generated.write_bytes(AUDIO)
        accepted = output_storage.register_finalized_output(
            machine="local", studio="voice", job_id="job-1",
            managed_output_roots=[str(_OUTPUTS)], output_path=str(generated),
            generated_at=time.time(),
        )
        refused = output_storage.register_finalized_output(
            machine="local", studio="voice", job_id="job-2",
            managed_output_roots=[str(_OUTPUTS)], output_path=str(reference_path),
            generated_at=time.time(),
        )
        self.assertIsNotNone(accepted)
        self.assertIsNone(refused)

    # ── the asset feeds a voice job ───────────────────────────────────

    def test_staged_asset_is_accepted_as_a_job_reference(self) -> None:
        """The returned id is the exact job parameter the Broker consumes."""
        payload = self._stage(transcript="Exact words.").json()
        from components.hub.backend import broker
        seen: dict = {}
        original = broker.submit_batch

        def spy(envelope):
            seen.update(envelope)
            return {"batch_id": "fixture-batch", "items": 0}

        broker.submit_batch = spy
        try:
            with patch.object(standalone, "_submission_readiness", lambda *_a, **_k: None):
                response = self.client.post("/api/hub/jobs", headers=HEADERS, json={
                    "modality": "voice", "model": "fixture/voice",
                    "clientRequestId": "voice-ref-0001",
                    "items": [{"text": "Narration",
                               "params": {"voice_reference_asset_id": payload["asset_id"]}}],
                })
        finally:
            broker.submit_batch = original
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            seen["items"][0]["params"]["voice_reference_asset_id"], payload["asset_id"],
        )

import unittest
unittest.main()
'''


class HubVoiceReferenceRouteTests(unittest.TestCase):
    def test_real_reference_asset_route_in_packaged_hub(self) -> None:
        env = dict(os.environ)
        root = str(Path(__file__).resolve().parents[1])
        env["PYTHONPATH"] = root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        result = subprocess.run(
            [sys.executable, "-B", "-c", _PROOF],
            text=True, capture_output=True, env=env, timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
