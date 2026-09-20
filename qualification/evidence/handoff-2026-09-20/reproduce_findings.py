"""Offline regression probes for the handoff backlog; failures are expected at f6362e0.

Run from the repository root using .build/voice-runtime/bin/python3 -B <this file> -v.
Uses only temporary fixture state, no real app, model inference, or fleet enrollment.
"""
from pathlib import Path
import asyncio
import copy
from contextlib import ExitStack
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


class HandoffRegressions(unittest.TestCase):
    def test_fleet_permission_denies_unclassified_worker_routes(self):
        from components.hub.backend.auth import fleet_path_allowed
        checks = {method + " " + path: fleet_path_allowed(method, path)
                  for method, path in (
                      ("GET", "/studio/voice/api/settings"),
                      ("GET", "/studio/voice/api/not-yet-classified"),
                      ("PATCH", "/studio/voice/api/not-yet-classified"))}
        print("FLEET MATCHER:", json.dumps(checks))
        self.assertFalse(any(checks.values()), "Unclassified worker routes are granted by default")

    def _voice_probe(self, cancel_after_acceptance=False):
        from components.hub.backend import broker
        item = {"index": 0, "prompt": "Fixture", "params": {}, "seed": None,
                "state": "running", "studio": "voice-fixture", "studio_job_id": None}
        batch = {"id": "fixture", "modality": "voice", "model": "fixture/model",
                 "shared_params": {}, "cancelled": False, "genstudio_execution": None,
                 "items": [item]}
        studio = {"id": "voice-fixture", "machine": "fixture-machine"}
        saved = []
        at_first_poll = []
        class StopBeforePoll(BaseException):
            pass
        class Client:
            async def post(self, *args, **kwargs):
                batch["cancelled"] = cancel_after_acceptance
                return SimpleNamespace(status_code=200,
                    json=lambda: {"job": {"id": "accepted-voice-job", "state": "queued"}})
            async def delete(self, *args, **kwargs):
                return SimpleNamespace(status_code=500)
        async def stop_before_poll(*args):
            at_first_poll.extend(copy.deepcopy(saved))
            raise StopBeforePoll()
        async def noop(*args):
            return None
        with ExitStack() as stack:
            stack.enter_context(patch.object(broker.ledger, "save_batch",
                side_effect=lambda value: saved.append(copy.deepcopy(value))))
            stack.enter_context(patch.object(broker.ledger, "record_activity_ownership"))
            stack.enter_context(patch.object(broker, "studio_request", return_value=("fixture", {})))
            stack.enter_context(patch.object(broker, "_monitor", return_value=SimpleNamespace(registry=[studio])))
            stack.enter_context(patch.object(broker, "_expire_genstudio_batch", return_value=False))
            stack.enter_context(patch.object(broker, "_post_item_webhook", side_effect=noop))
            stack.enter_context(patch.object(broker, "_maybe_finish", side_effect=noop))
            stack.enter_context(patch.object(broker.asyncio, "sleep", side_effect=stop_before_poll))
            try:
                asyncio.run(broker._run_item(Client(), batch, item, studio))
            except StopBeforePoll:
                pass
        return item, at_first_poll

    def test_voice_acceptance_id_is_persisted_before_polling(self):
        item, writes = self._voice_probe()
        persisted_ids = [row["items"][0].get("studio_job_id") for row in writes]
        print("VOICE ACCEPTANCE:", json.dumps({"live_id": item["studio_job_id"],
                                               "persisted_ids_before_poll": persisted_ids}))
        self.assertIn("accepted-voice-job", persisted_ids,
                      "Accepted Voice identity is not saved in its batch before first poll")

    def test_failed_voice_cancel_does_not_claim_terminal_cancellation(self):
        item, _ = self._voice_probe(cancel_after_acceptance=True)
        print("VOICE CANCEL:", json.dumps({"state": item["state"],
                                          "cancel_error": item.get("cancel_error")}))
        self.assertNotEqual(item["state"], "cancelled",
                            "Worker refused cancellation but Hub claims terminal cancelled")

    def test_launch_verifier_rejects_failed_health_probe(self):
        with tempfile.TemporaryDirectory(prefix="hub-handoff-verifier-") as raw:
            folder = Path(raw)
            app = folder / "Fixture.app"
            resources = app / "Contents/Resources"
            binary = app / "Contents/MacOS"
            binary.mkdir(parents=True)
            python = resources / "runtimes/voice/bin/python3"
            python.parent.mkdir(parents=True)
            python.symlink_to(sys.executable)
            (resources / "release-manifest.json").write_text(json.dumps({
                "version": "fixture", "implementation_commit": "fixture",
                "development_build": True,
            }))
            service = binary / "StudioHubService"
            service.write_text("#!/bin/sh\n"
                'while [ "$#" -gt 0 ]; do\n'
                '  if [ "$1" = "--ready-file" ]; then shift; ready="$1"; fi\n'
                '  shift\ndone\n'
                # Invalid URL makes the health probe fail without making a network call.
                'printf \'%s\\n\' \'{"url":"invalid://fixture","token":"fixture"}\' > "$ready"\n'
                'exec /bin/sleep 30\n')
            service.chmod(0o755)
            library = folder / "models"
            library.mkdir()
            env = dict(os.environ, APP=str(app), LIB=str(library), PORT="0", TMPDIR=raw)
            result = subprocess.run(["sh", str(ROOT / "qualification/verify-launch.sh")],
                                    env=env, text=True, capture_output=True, timeout=40)
            print("VERIFIER OUTPUT:\n" + result.stdout + result.stderr)
            self.assertNotEqual(result.returncode, 0,
                                "Verifier reported success after its health probe failed")

    def test_subtitle_final_persistence_failure_has_truthful_artifact_disposition(self):
        from components.subtitle.app.backend import manager as module
        with tempfile.TemporaryDirectory(prefix="hub-handoff-subtitle-") as raw:
            folder = Path(raw)
            manager = module.SubtitleManager.__new__(module.SubtitleManager)
            manager.data = folder
            manager.artifacts = folder / "artifacts"
            manager.artifacts.mkdir()
            manager.state_file = folder / "jobs.json"
            manager.lock = threading.RLock()
            event = threading.Event()
            manager.cancel_events = {"fixture": event}
            job = {"id": "fixture", "state": "queued", "created_at": time.time(),
                   "params": {"model": "fixture", "language": None,
                              "word_timestamps": False, "formats": ["srt"]}}
            manager.jobs = {"fixture": job}
            source = folder / "fixture.wav"
            source.write_bytes(b"fixture input; engine is mocked")
            result = {"text": "hello", "text_file": "hello", "language": "en",
                      "duration": 1, "segments": [{"start": 0, "end": 1, "text": "hello"}],
                      "srt": "1\n00:00:00,000 --> 00:00:01,000\nhello\n", "vtt": "WEBVTT\n"}
            class Engine:
                def transcribe(self, *args, **kwargs):
                    return result
            manager.engine = Engine()
            atomic_json = module._atomic_json
            def fail_final_write(path, value):
                if value["fixture"]["state"] == "done":
                    raise OSError("fixture: disk full at completion receipt")
                atomic_json(path, value)
            with patch.object(module, "_atomic_json", side_effect=fail_final_write):
                manager._run("fixture", str(source))
            remaining = sorted(path.name for path in manager.artifacts.iterdir())
            print("SUBTITLE DISPOSITION:", json.dumps({"error_code": job["error_code"],
                "error": job["error"], "remaining_artifacts": remaining}))
            self.assertTrue(not remaining or job["error_code"] == "JOB_PERSISTENCE_UNCERTAIN",
                            "Published artifact remains while receipt says it was discarded")


if __name__ == "__main__":
    # Registry imports create DATA_DIR immediately, before individual mocks run.
    # Select fixture state before any Hub module is imported.
    with tempfile.TemporaryDirectory(prefix="hub-handoff-imports-") as state:
        with patch.dict(os.environ, {"STUDIOHUB_DATA_DIR": state}):
            unittest.main()
