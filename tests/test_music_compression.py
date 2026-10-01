"""Music ``output_format``: compress at source, and the YuE2 FLAC delivery bug.

Pinned here, per the 2026-10-01 compression plan:

* absent ``output_format`` is today's file, byte for byte, with today's media type;
* ``m4a`` delivers AAC-LC with an ``ftyp`` header, ``bytes``/``sha256`` of *that* file, the decoded
  facts of the lossless master, and the master removed once the encode is verified;
* the encode runs **after** the inference lock is released;
* an encode that cannot run delivers the master and records why (a paid job is never failed);
* a YuE2 job is FLAC end to end: served, acknowledged and surviving a restart. The worker recorded
  ``audio/wav`` and looked for ``{id}.wav``, so a finished song answered 409 and was marked
  ``OUTPUT_UNAVAILABLE`` after a restart.
"""
from __future__ import annotations

import hashlib
import importlib.util
import math
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from components import audio_encode  # noqa: E402
from components.music.app.backend import catalog, generation  # noqa: E402

FFMPEG = audio_encode.find_tool("ffmpeg")
SECONDS = 2.0
RATE = 44100


def _sine_wav(path: Path, seconds: float = SECONDS) -> None:
    frames = int(seconds * RATE)
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(2)
        stream.setsampwidth(2)
        stream.setframerate(RATE)
        stream.writeframes(b"".join(
            struct.pack("<hh", int(8000 * math.sin(i / 20)), int(8000 * math.sin(i / 31)))
            for i in range(frames)))


def _facts(path: Path) -> dict:
    data = Path(path).read_bytes()
    with wave.open(str(path), "rb") as stream:
        frames, rate, channels = stream.getnframes(), stream.getframerate(), stream.getnchannels()
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "audio_duration_s": frames / rate, "sample_rate_hz": rate, "channels": channels,
            "frames": frames, "engine_seconds": 0.01, "media_type": "audio/wav"}


def _fake_generate(*, output, **unused):
    _sine_wav(Path(output))
    return _facts(Path(output))


class _VerifiedCache:
    def __init__(self, *unused, library=None, **also_unused):
        self.library = Path(library)
        self.root = self.library / "installed" / "model"
        self.root.mkdir(parents=True, exist_ok=True)

    def status(self, **unused):
        return {"state": "cached", "checkpoints_root": str(self.root)}

    def companion_root(self, package_id):
        return self.root


class _Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.models = self.root / "models"
        self.models.mkdir()

    def manager(self):
        with mock.patch.object(generation.cache_module, "MusicCache", _VerifiedCache):
            manager = generation.GenerationManager(
                self.data, library_dir=str(self.models), runtime_sha256="f" * 64)
        self.addCleanup(manager.close)
        return manager

    @staticmethod
    def wait_for(manager, job_id, state="done"):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            job = manager.get(job_id)
            if job["state"] == state:
                return job
            time.sleep(0.01)
        raise AssertionError(manager.get(job_id))


class RequestShapeTests(unittest.TestCase):
    def test_absent_field_is_not_echoed_so_old_callers_see_identical_params(self):
        for repo in (catalog.STABLE_AUDIO_REPO, catalog.YUE2_REPO):
            raw = {"repo": repo, "prompt": "piano", "lyrics": "[Verse]\nla\n"}
            self.assertNotIn("output_format", generation.validate_request(raw))

    def test_supplied_field_is_echoed_for_the_recovery_comparison(self):
        for repo in (catalog.STABLE_AUDIO_REPO, catalog.YUE2_REPO):
            raw = {"repo": repo, "prompt": "piano", "lyrics": "[Verse]\nla\n",
                   "output_format": "m4a"}
            self.assertEqual(generation.validate_request(raw)["output_format"], "m4a")

    def test_an_unknown_format_is_refused_by_name(self):
        with self.assertRaisesRegex(ValueError, "output_format must be"):
            generation.validate_request({"prompt": "piano", "output_format": "mp3"})

    def test_each_lane_publishes_what_it_honours_and_it_is_never_defaultable(self):
        for model in catalog.CATALOG:
            contract = generation.lane_contract(model)
            parameter = next(p for p in contract["parameters"] if p["name"] == "output_format")
            native = "flac" if model.runtime == catalog.RUNTIME_YUE2 else "wav"
            self.assertEqual(parameter["enum"], [native, "m4a"])
            self.assertTrue(parameter["request_only"])
            self.assertNotIn("default", parameter)
            self.assertEqual(contract["artifact"]["extension"], native)
            self.assertEqual(contract["artifact"]["media_type"], f"audio/{native}")


class Sa3DeliveryTests(_Base):
    def run_job(self, **extra):
        manager = self.manager()
        with mock.patch.object(generation.stable_audio, "generate", _fake_generate):
            job = manager.submit({"prompt": "warm drone", "duration": 2, **extra})
            return manager, self.wait_for(manager, job["id"])

    def test_default_delivery_is_the_wav_exactly_as_before(self):
        manager, done = self.run_job()
        path = manager.outputs / f"{done['id']}.wav"
        self.assertEqual(done["output_path"], str(path))
        self.assertEqual(done["media_type"], "audio/wav")
        self.assertEqual(done["format"], "wav")
        self.assertEqual(done["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertNotIn("delivery", done)
        self.assertFalse((manager.outputs / f"{done['id']}.m4a").exists())
        self.assertTrue(manager.ack(done["id"])["ok"])

    @unittest.skipUnless(FFMPEG, "no ffmpeg available")
    def test_m4a_delivers_aac_with_the_delivered_files_own_receipt(self):
        manager, done = self.run_job(output_format="m4a")
        path = manager.outputs / f"{done['id']}.m4a"
        data = path.read_bytes()
        self.assertEqual(data[4:8], b"ftyp")
        self.assertEqual(done["output_path"], str(path))
        self.assertEqual(done["media_type"], "audio/mp4")
        self.assertEqual(done["format"], "m4a")
        self.assertEqual((done["bytes"], done["sha256"]),
                         (len(data), hashlib.sha256(data).hexdigest()))
        # Decoded facts describe the audio and come from the lossless master.
        self.assertEqual(done["sample_rate_hz"], RATE)
        self.assertEqual(done["channels"], 2)
        self.assertEqual(done["frames"], int(SECONDS * RATE))
        self.assertAlmostEqual(audio_encode.probe_duration(path), SECONDS, delta=0.1)
        # The worker's own lossless master is gone once the encode is verified.
        self.assertFalse((manager.outputs / f"{done['id']}.wav").exists())
        self.assertEqual(done["delivery"]["applied"], "m4a")
        self.assertGreater(done["delivery"]["master_bytes"], done["bytes"])
        self.assertNotIn("delivery_fallback_reason", done)
        sidecar = __import__("json").loads((manager.outputs / f"{done['id']}.json").read_text())
        self.assertEqual(sidecar["artifact"]["sha256"], done["sha256"])
        self.assertEqual(sidecar["delivery"]["media_type"], "audio/mp4")
        # ack and a worker restart accept the compressed file.
        self.assertTrue(manager.ack(done["id"])["ok"])
        restarted = self.manager().get(done["id"])
        self.assertEqual(restarted["state"], "done")
        self.assertEqual(restarted["output_path"], str(path))

    @unittest.skipUnless(FFMPEG, "no ffmpeg available")
    def test_the_route_serves_the_m4a_with_its_type_and_digest(self):
        if importlib.util.find_spec("fastapi") is None:
            self.skipTest("FastAPI is not installed")
        from fastapi.testclient import TestClient
        from components.music.app.backend.main import create_app

        manager, done = self.run_job(output_format="m4a")
        with TestClient(create_app(manager, "secret")) as client:
            response = client.get(f"/api/generate/jobs/{done['id']}/audio",
                                  headers={"X-Studio-Token": "secret"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], "audio/mp4")
        self.assertEqual(response.headers["x-content-sha256"], done["sha256"])
        self.assertEqual(hashlib.sha256(response.content).hexdigest(), done["sha256"])

    def test_missing_encoder_delivers_the_wav_and_records_why(self):
        with mock.patch.object(audio_encode, "find_tool", lambda name="ffmpeg": None):
            manager, done = self.run_job(output_format="m4a")
        self.assertEqual(done["state"], "done")
        self.assertEqual(done["media_type"], "audio/wav")
        self.assertTrue((manager.outputs / f"{done['id']}.wav").is_file())
        self.assertIn("ffmpeg", done["delivery_fallback_reason"])
        self.assertEqual(done["delivery"]["applied"], "wav")
        self.assertTrue(manager.ack(done["id"])["ok"])
        self.assertEqual(list(manager.outputs.glob(".*.partial")), [])

    def test_an_encoder_that_fails_midway_delivers_the_wav(self):
        def broken(master, destination, **unused):
            Path(destination).write_bytes(b"partial")
            raise audio_encode.EncodeError("AAC encode failed: boom")

        with mock.patch.object(audio_encode, "encode_m4a", broken):
            manager, done = self.run_job(output_format="m4a")
        self.assertEqual(done["state"], "done")
        self.assertEqual(done["format"], "wav")
        self.assertIn("boom", done["delivery_fallback_reason"])

    def test_the_encode_runs_after_the_inference_lock_is_released(self):
        seen = {}
        manager = self.manager()

        def spy(master, destination, **unused):
            seen["locked"] = manager.inference_lock.locked()
            # A second job can take the lock now.
            got = manager.inference_lock.acquire(blocking=False)
            seen["second_job_can_start"] = got
            if got:
                manager.inference_lock.release()
            raise audio_encode.EncodeError("spy: not encoding")

        with mock.patch.object(generation.stable_audio, "generate", _fake_generate), \
                mock.patch.object(audio_encode, "encode_m4a", spy):
            job = manager.submit({"prompt": "warm drone", "duration": 2, "output_format": "m4a"})
            self.wait_for(manager, job["id"])
        self.assertFalse(seen["locked"])
        self.assertTrue(seen["second_job_can_start"])


@unittest.skipUnless(FFMPEG, "no ffmpeg available")
class Yue2DeliveryTests(_Base):
    """A YuE2 job writes FLAC; the worker must serve, ack and restore that file."""

    def bridge(self):
        """A stub interpreter speaking the bridge's JSON contract, writing a real FLAC."""
        stub = self.root / "stub-python"
        flac = self.root / "song.flac"
        wav = self.root / "song.wav"
        _sine_wav(wav, 3.0)
        subprocess.run([str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y", "-i", str(wav),
                        str(flac)], check=True)
        stub.write_text(
            "#!/bin/sh\n"
            'spec="$4"\n'
            'out=$(sed -n \'s/.*"output": "\\([^"]*\\)".*/\\1/p\' "$spec")\n'
            f'cp "{flac}" "$out"\n'
            'printf \'{"status": "ok", "seconds": 3.0, "sample_rate": 44100, "channels": 2}\\n\'\n')
        stub.chmod(0o755)
        return stub

    def song(self, **extra):
        manager = self.manager()
        with mock.patch.object(generation.cache_module, "MusicCache", _VerifiedCache), \
                mock.patch.object(generation, "yue2_runtime_python", lambda: self.bridge()):
            job = manager.submit({"repo": catalog.YUE2_REPO, "prompt": "warm ballad",
                                  "lyrics": "[Verse]\nmorning light\n", "duration_seconds": 36,
                                  **extra})
            return manager, self.wait_for(manager, job["id"])

    def test_a_flac_song_is_recorded_as_flac_served_acked_and_survives_restart(self):
        manager, done = self.song()
        path = manager.outputs / f"{done['id']}.flac"
        self.assertTrue(path.is_file())
        self.assertEqual(done["output_path"], str(path))
        self.assertEqual(done["media_type"], "audio/flac")
        self.assertEqual(done["format"], "flac")
        self.assertTrue(manager.ack(done["id"])["ok"], "ack looked for a .wav that never existed")
        restarted = self.manager().get(done["id"])
        self.assertEqual(restarted["state"], "done", restarted.get("error"))
        self.assertEqual(restarted["output_path"], str(path))
        self.assertIsNone(restarted.get("error_code"))

    def test_the_audio_route_serves_the_flac_instead_of_409(self):
        if importlib.util.find_spec("fastapi") is None:
            self.skipTest("FastAPI is not installed")
        from fastapi.testclient import TestClient
        from components.music.app.backend.main import create_app

        manager, done = self.song()
        with TestClient(create_app(manager, "secret")) as client:
            response = client.get(f"/api/generate/jobs/{done['id']}/audio",
                                  headers={"X-Studio-Token": "secret"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], "audio/flac")
        self.assertEqual(response.content[:4], b"fLaC")
        self.assertEqual(hashlib.sha256(response.content).hexdigest(), done["sha256"])

    def test_a_yue2_job_can_ask_for_m4a_too(self):
        manager, done = self.song(output_format="m4a")
        self.assertEqual(done["media_type"], "audio/mp4")
        self.assertTrue((manager.outputs / f"{done['id']}.m4a").is_file())
        self.assertFalse((manager.outputs / f"{done['id']}.flac").exists())
        self.assertTrue(manager.ack(done["id"])["ok"])
        self.assertEqual(self.manager().get(done["id"])["state"], "done")

    def test_an_older_record_that_called_its_flac_a_wav_is_still_served_as_flac(self):
        """Records written before this fix said ``audio/wav`` for a ``.flac`` path."""
        manager, done = self.song()
        record = manager.jobs[done["id"]]
        record["media_type"] = "audio/wav"
        self.assertEqual(manager.final_path(record).suffix, ".flac")


if __name__ == "__main__":
    unittest.main()
