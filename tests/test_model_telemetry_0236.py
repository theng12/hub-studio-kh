#!/usr/bin/env python3
"""Voice load/reuse telemetry (0.6.236).

Pins the worker half of the contract the Hub now carries: `model_reused` is true only
for the exact `(repo, "tts-mlx")` resident key, `load_s` times only the model getter and
is 0.0 on a reuse, and an unmeasured job serializes neither field. No model is loaded
and no engine runs here.
"""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from components.voice.app.backend.generation import (  # noqa: E402
    GenerationJob,
    GenerationManager,
)


class _FakeManager:
    def __init__(self, loaded, *, delay=0.0, error=None):
        self.loaded = list(loaded)
        self.delay = delay
        self.error = error
        self.calls = 0

    def loaded_model_keys(self):
        return list(self.loaded)

    def _mlx_audio_get_model(self, repo):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return object()


def job():
    return GenerationJob(job_id="j-0236", mode="txt2speech", params={})


class VoiceLoadTelemetryTests(unittest.TestCase):
    def test_exact_resident_key_is_a_reuse_with_zero_load(self):
        manager = _FakeManager([("org/model", "tts-mlx")], delay=0.02)
        j = job()
        GenerationManager._mlx_audio_load_with_telemetry(manager, j, "org/model")
        self.assertIs(j.model_reused, True)
        self.assertEqual(j.load_s, 0.0)
        self.assertEqual(manager.calls, 1)

    def test_cold_load_measures_the_getter_only(self):
        manager = _FakeManager([], delay=0.02)
        j = job()
        GenerationManager._mlx_audio_load_with_telemetry(manager, j, "org/model")
        self.assertIs(j.model_reused, False)
        self.assertGreater(j.load_s, 0.0)
        self.assertLess(j.load_s, 1.0)

    def test_a_failed_load_leaves_telemetry_unknown(self):
        manager = _FakeManager([], error=RuntimeError("load failed"))
        j = job()
        with self.assertRaises(RuntimeError):
            GenerationManager._mlx_audio_load_with_telemetry(manager, j, "org/model")
        self.assertIsNone(j.model_reused)
        self.assertIsNone(j.load_s)
        self.assertNotIn("model_reused", j.serialize())
        self.assertNotIn("load_s", j.serialize())

    def test_a_different_runtime_key_is_not_a_reuse(self):
        manager = _FakeManager([("org/model", "other-runtime")], delay=0.01)
        j = job()
        GenerationManager._mlx_audio_load_with_telemetry(manager, j, "org/model")
        self.assertIs(j.model_reused, False)
        self.assertGreater(j.load_s, 0.0)

    def test_a_different_repo_is_not_a_reuse(self):
        manager = _FakeManager([("org/other", "tts-mlx")], delay=0.01)
        j = job()
        GenerationManager._mlx_audio_load_with_telemetry(manager, j, "org/model")
        self.assertIs(j.model_reused, False)

    def test_unmeasured_job_serializes_neither_field(self):
        result = job().serialize()
        self.assertNotIn("model_reused", result)
        self.assertNotIn("load_s", result)

    def test_measured_job_serializes_both_fields(self):
        j = job()
        j.model_reused = False
        j.load_s = 1.25
        result = j.serialize()
        self.assertIs(result["model_reused"], False)
        self.assertEqual(result["load_s"], 1.25)


if __name__ == "__main__":
    unittest.main()
