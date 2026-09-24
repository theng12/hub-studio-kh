"""A job nothing can run must be refused, with the reason, not accepted and queued for ever.

The submission guard checked the packaged model gates (files, runtime, macOS, chip) and the storage
budget - and stopped there. Nothing asked whether a Studio was running. When the Voice worker was
not up, `_eligible_studios` found no studio to dispatch to, so the job sat in `queued` for ever,
while the app showed it as accepted work. A worker that dies is never deregistered either: it stays
in `monitor.registry`, so liveness is the monitor's own status for it.
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from components.hub import standalone  # noqa: E402

CATALOG = {
    "voice-mlx:qwen": {"engine": "voice-mlx", "model_id": "mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit"},
}
ENVELOPE = {"modality": "voice", "model": "mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit"}


class _Monitor:
    def __init__(self, workers, status):
        self.registry = workers
        self.status = status


def _worker(worker_id="voice-1", modality="voice", local=True):
    return {"id": worker_id, "modality": modality, "local": local, "machine": "local"}


class AdmissionRequiresAStudioTests(unittest.TestCase):
    def _readiness(self, monitor):
        with mock.patch.object(standalone, "_model_catalog", lambda: CATALOG), \
             mock.patch.object(standalone, "_model_state",
                               lambda manifest, catalog, mon, lane_contracts=None:
                               {"dispatch_state": "eligible"}):
            return standalone._submission_readiness(ENVELOPE, monitor)

    def test_no_studio_for_this_modality_is_refused_by_name(self):
        refusal = self._readiness(_Monitor([], {}))
        self.assertIsNotNone(refusal, "a job with no Studio to run it must not be accepted")
        self.assertEqual(refusal["code"], "STUDIO_NOT_RUNNING")
        self.assertIn("voice", refusal["detail"])

    def test_a_studio_that_is_not_answering_is_refused_by_name(self):
        monitor = _Monitor([_worker()], {"voice-1": {"status": "down"}})
        refusal = self._readiness(monitor)
        self.assertIsNotNone(refusal)
        self.assertEqual(refusal["code"], "STUDIO_NOT_RUNNING")
        self.assertIn("restart", refusal["detail"])

    def test_a_hub_that_has_not_checked_its_studio_yet_still_accepts(self):
        """`unknown` means the health check has not run, which must not refuse work at startup."""
        self.assertIsNone(self._readiness(_Monitor([_worker()], {"voice-1": {"status": "unknown"}})))
        self.assertIsNone(self._readiness(_Monitor([_worker()], {})))
        self.assertIsNone(self._readiness(_Monitor([_worker()], {"voice-1": {"status": "up"}})))

    def test_another_modality_running_does_not_count(self):
        monitor = _Monitor([_worker("image-1", "image")], {"image-1": {"status": "up"}})
        refusal = self._readiness(monitor)
        self.assertIsNotNone(refusal, "an image Studio cannot transcribe or speak")
        self.assertEqual(refusal["code"], "STUDIO_NOT_RUNNING")

    def test_a_remote_studio_counts(self):
        """A controller may have no local Studio of that modality and send the work to a node."""
        monitor = _Monitor([_worker("node-1", local=False)], {"node-1": {"status": "up"}})
        self.assertIsNone(self._readiness(monitor))


if __name__ == "__main__":
    unittest.main()
