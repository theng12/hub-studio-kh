"""The scheduler must not sweep the output roots on every pass.

`output_storage.admission` answers "may this job run given the storage policy" - and it answers by
calling `enforce`, which walks and hashes the managed output roots. `_submission_readiness` calls it
for every queued item, on every dispatch pass, several times a second while work is waiting, on the
event loop. Enforcement still happens on the background timer (which is what deletes files) and on
every operator action; the scheduler's repeated question is answered from a short-lived snapshot.
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from components.hub import output_storage  # noqa: E402


class AdmissionCacheTests(unittest.TestCase):
    def setUp(self):
        output_storage._ADMISSION_SNAPSHOT.clear()
        self.calls = []

    def _enforce(self, **kwargs):
        self.calls.append(kwargs)
        return {"used_bytes": 10, "free_bytes": 10_000_000, "reclaimed_bytes": 0}

    def test_repeated_questions_are_answered_from_one_sweep(self):
        with mock.patch.object(output_storage, "enforce", self._enforce):
            for _ in range(5):
                self.assertIsNone(output_storage.admission(
                    machine="local", max_bytes=1000, retention_days=30, enabled=True))
        self.assertEqual(len(self.calls), 1, "a dispatch pass must not re-sweep per item")

    def test_a_different_policy_is_asked_about_separately(self):
        with mock.patch.object(output_storage, "enforce", self._enforce):
            output_storage.admission(machine="local", max_bytes=1000, retention_days=30, enabled=True)
            output_storage.admission(machine="local", max_bytes=2000, retention_days=30, enabled=True)
            output_storage.admission(machine="node-2", max_bytes=1000, retention_days=30, enabled=True)
        self.assertEqual(len(self.calls), 3, "each policy is its own question")

    def test_the_refusal_still_uses_the_real_numbers(self):
        def full(**kwargs):
            self.calls.append(kwargs)
            return {"used_bytes": 5000, "free_bytes": 10, "reclaimed_bytes": 0}

        with mock.patch.object(output_storage, "enforce", full):
            first = output_storage.admission(machine="local", max_bytes=1000, retention_days=30,
                                             enabled=True)
            second = output_storage.admission(machine="local", max_bytes=1000, retention_days=30,
                                              enabled=True)
        for refusal in (first, second):
            self.assertIsNotNone(refusal)
            self.assertEqual(refusal["code"], "OUTPUT_STORAGE_BLOCKED")
            self.assertEqual(refusal["storage"]["used_bytes"], 5000)
        self.assertEqual(len(self.calls), 1)

    def test_a_caller_cannot_corrupt_the_cached_answer(self):
        with mock.patch.object(output_storage, "enforce", self._enforce):
            refusal = output_storage.admission(machine="local", max_bytes=5, retention_days=30,
                                               enabled=True)
            self.assertIsNotNone(refusal)
            refusal["storage"]["used_bytes"] = 0          # a caller annotating its copy
            again = output_storage.admission(machine="local", max_bytes=5, retention_days=30,
                                             enabled=True)
        self.assertEqual(again["storage"]["used_bytes"], 10, "the cache must hand out copies")

    def test_the_snapshot_expires(self):
        with mock.patch.object(output_storage, "enforce", self._enforce):
            output_storage.admission(machine="local", max_bytes=1000, retention_days=30, enabled=True)
            with mock.patch.object(output_storage, "_ADMISSION_TTL_SECONDS", -1.0):
                output_storage.admission(machine="local", max_bytes=1000, retention_days=30,
                                         enabled=True)
        self.assertEqual(len(self.calls), 2, "enforcement must not be remembered for ever")


if __name__ == "__main__":
    unittest.main()
