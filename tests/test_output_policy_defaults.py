"""Voice and transcription output follow the same policy as everything else: 3 days, 20 GB or 8% of the disk.

Their own defaults were 30 days / 80 GB (a third of a 256 GB Mac), looser than the Hub's 3 d / 20 GB.
A settings file that still holds the old defaults was never chosen and reads as the new ones; a value
the operator chose is kept.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from components.hub.backend import job_storage, transcription_jobs  # noqa: E402

GiB = 1024 ** 3


class SpoolDefaultTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for module, name, value in ((job_storage, "DATA_DIR", self.root), (job_storage, "SETTINGS_FILE", self.root / "js.json"),
                                    (transcription_jobs, "DATA_DIR", self.root),
                                    (transcription_jobs, "SETTINGS_FILE", self.root / "tj.json")):
            patcher = mock.patch.object(module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def disk(self, total_gib: int):
        return mock.patch.object(job_storage.shutil, "disk_usage",
                                 lambda _p: SimpleNamespace(total=total_gib * GiB, used=0, free=0))

    def test_defaults_are_3_days_and_20_gb_or_8_percent_of_a_small_disk(self):
        with self.disk(2000):
            value = job_storage._read()
        self.assertEqual((value["retention_days"], value["max_bytes"]), (3, 20 * 1000 ** 3))
        with self.disk(100):
            self.assertEqual(job_storage._read()["max_bytes"], int(100 * GiB * 0.08))
        self.assertEqual(transcription_jobs.settings()["retention_days"], 3)

    def test_the_old_defaults_migrate_and_a_chosen_policy_is_kept(self):
        (self.root / "js.json").write_text(json.dumps(
            {"enabled": True, "max_bytes": 80 * GiB, "retention_days": 30, "policy_version": 2}))
        (self.root / "tj.json").write_text(json.dumps({"retention_days": 30, "policy_version": 2}))
        with self.disk(2000):
            self.assertEqual(job_storage._read()["retention_days"], 3)
            self.assertEqual(job_storage._read()["max_bytes"], 20 * 1000 ** 3)
        self.assertEqual(transcription_jobs.settings()["retention_days"], 3)
        self.assertEqual(json.loads((self.root / "js.json").read_text())["policy_version"], job_storage.POLICY_VERSION)
        with self.disk(2000):
            job_storage.save(True, 80, 30)                       # chosen by the operator: kept
            kept = job_storage._read()
        self.assertEqual((kept["retention_days"], kept["max_bytes"]), (30, 80 * GiB))
        self.assertEqual(transcription_jobs.settings()["retention_days"], 30)


if __name__ == "__main__":
    unittest.main()
