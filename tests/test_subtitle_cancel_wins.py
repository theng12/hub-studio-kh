"""A cancel that lands while the artifacts are being written must still win.

The Subtitle worker checked the cancel flag right after decoding and then published the artifacts and
committed `state='done'`. A cancel arriving in that window - after the check, before the commit - was
silently overwritten by "done": the app showed a finished job the operator had cancelled, and the
Hub's lifecycle treated it as completed work with artifacts. The window is small and exactly the one
a person hits by pressing Cancel when a job looks finished.
"""
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from components.subtitle.app.backend.manager import SubtitleManager  # noqa: E402


class _Engine:
    """Stands in for the pinned decoder: returns a result without touching a model."""

    def __init__(self, on_transcribe=None):
        self.on_transcribe = on_transcribe

    def transcribe(self, path, *, package_id, language, word_timestamps, cancelled, progress):
        if self.on_transcribe:
            self.on_transcribe()
        return {"text": "hello", "segments": [{"start": 0.0, "end": 1.0, "text": "hello"}],
                "duration": 1.0, "language": "en"}


VOICE_APP = Path(__file__).resolve().parents[1] / "components" / "voice" / "app"


def _manager(root: Path, engine) -> SubtitleManager:
    # The worker delegates publishing and duration probing to the Voice app's own modules, so it
    # needs the real directory rather than a placeholder.
    manager = SubtitleManager(data=root / "data", library=root / "library", manifests=[],
                              runtime_sha256="0" * 64, voice_app_dir=VOICE_APP)
    manager.engine = engine
    return manager


def _queued(job_id: str, input_path: Path) -> dict:
    now = time.time()
    return {"job_id": job_id, "state": "queued", "progress": 0.0, "created_at": now,
            "started_at": now, "finished_at": None, "error": None, "error_code": None,
            "params": {"model": "whisper-turbo", "language": None, "word_timestamps": False,
                       "formats": ["srt"]},
            "input_path": str(input_path)}


class CancelWinsTests(unittest.TestCase):
    def test_a_cancel_during_publishing_ends_as_cancelled(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            audio = root / "recording.wav"
            audio.write_bytes(b"RIFF____WAVE")
            manager = _manager(root, _Engine())
            job_id = "stt-1"
            manager.jobs[job_id] = _queued(job_id, audio)
            event = threading.Event()
            manager.cancel_events[job_id] = event
            artifact = root / "data" / "artifacts" / "stt-1.srt"
            artifact.parent.mkdir(parents=True, exist_ok=True)

            def publish(job_id, result, formats, staged):
                # The operator pressed Cancel in exactly this window.
                event.set()
                artifact.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n")
                return {"srt": {"path": str(artifact), "sha256": "a" * 64, "bytes": 12}}

            manager._publish = publish
            manager._run(job_id, str(audio))

            job = manager.jobs[job_id]
            self.assertEqual(job["state"], "cancelled",
                             "a cancel that landed while writing must not be overwritten by done")
            self.assertFalse(job.get("artifacts"), "and no artifacts may be advertised")
            self.assertIsNone(job.get("output_path"))
            self.assertFalse(artifact.exists(), "the artifact written in that window is removed")

    def test_a_job_without_a_cancel_still_finishes(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            audio = root / "recording.wav"
            audio.write_bytes(b"RIFF____WAVE")
            manager = _manager(root, _Engine())
            job_id = "stt-2"
            manager.jobs[job_id] = _queued(job_id, audio)
            manager.cancel_events[job_id] = threading.Event()
            artifact = root / "data" / "artifacts" / "stt-2.srt"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n")
            manager._publish = lambda *a, **k: {
                "srt": {"path": str(artifact), "sha256": "a" * 64, "bytes": 12}}
            manager._run(job_id, str(audio))
            self.assertEqual(manager.jobs[job_id]["state"], "done")
            self.assertTrue(artifact.exists(), "nothing is removed when nobody cancelled")

    def test_a_cancel_before_decoding_is_still_a_cancel(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            audio = root / "recording.wav"
            audio.write_bytes(b"RIFF____WAVE")
            manager = _manager(root, _Engine())
            job_id = "stt-3"
            manager.jobs[job_id] = _queued(job_id, audio)
            event = threading.Event()
            event.set()
            manager.cancel_events[job_id] = event
            manager._run(job_id, str(audio))
            self.assertEqual(manager.jobs[job_id]["state"], "cancelled")


if __name__ == "__main__":
    unittest.main()
