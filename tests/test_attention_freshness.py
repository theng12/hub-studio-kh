"""This Mac's attention state: measured on an interval, answered on request, always with its time.

Owner, 2026-11-17: *"make sure the Hub's health is sending out signals every 30 minutes so we don't
have to keep checking."* Two properties are the whole point and both are pinned here: a verdict carries
`measured_at`, and a cleared condition clears the flag on the next measurement rather than ageing out.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from components.hub import attention  # noqa: E402
from components.hub.backend import ledger  # noqa: E402

RED = {"state": "red", "free_gb": 18.0, "total_gb": 460.0}
GREEN = {"state": "green", "free_gb": 200.0, "total_gb": 460.0}


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.data = Path(os.path.realpath(temp.name))
        for module, name, value in ((ledger, "DATA_DIR", self.data),):
            patcher = mock.patch.object(module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        attention.reset_cache()
        self.addCleanup(attention.reset_cache)


class Measurement(Fixture):
    def test_a_red_volume_is_attention_and_says_when_it_was_measured(self) -> None:
        got = attention.measure(disk=RED, activity_state="ready", now=1000.0)
        self.assertEqual(got["state"], "attention")
        self.assertEqual([r["code"] for r in got["reasons"]], ["OUTPUT_STORAGE_RED"])
        self.assertEqual(got["measured_at"], 1000.0)
        self.assertEqual(got["source"], "measured")
        self.assertEqual(got["storage"]["state"], "red")
        self.assertEqual(got["storage"]["measured_at"], 1000.0, "storage carries its own time")

    def test_a_cleared_condition_clears_the_flag_on_the_next_measurement(self) -> None:
        attention.refresh(disk=RED, activity_state="ready", now=1000.0)
        self.assertEqual(attention.cached()["state"], "attention")
        attention.refresh(disk=GREEN, activity_state="ready", now=2000.0)
        again = attention.cached()
        self.assertEqual(again["state"], "ok")
        self.assertEqual(again["reasons"], [])
        self.assertEqual(again["measured_at"], 2000.0, "the new time travels with the cleared verdict")

    def test_a_recent_failed_job_is_attention_and_a_healthy_one_is_not(self) -> None:
        failed = attention.measure(disk=GREEN, activity_state="needs_attention", now=10.0)
        self.assertEqual([r["code"] for r in failed["reasons"]], ["LAST_JOB_FAILED"])
        self.assertEqual(attention.measure(disk=GREEN, activity_state="ready")["state"], "ok")

    def test_an_unknown_disk_is_not_reported_as_healthy(self) -> None:
        got = attention.measure(disk={"state": "unknown"}, activity_state=None)
        self.assertEqual(got["state"], "unknown")
        self.assertEqual(got["reasons"], [])


class Interval(Fixture):
    def test_the_default_is_thirty_minutes(self) -> None:
        self.assertEqual(attention.settings()["interval_s"], 1800)
        self.assertEqual(attention.settings()["interval_s"], attention.DEFAULT_INTERVAL_S)

    def test_the_interval_is_a_setting_and_round_trips(self) -> None:
        attention.set_interval(600)
        self.assertEqual(attention.settings()["interval_s"], 600)
        path = self.data / attention.SETTINGS_FILE
        self.assertEqual(json.loads(path.read_text()), {"interval_s": 600})
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_a_nonsense_interval_is_refused(self) -> None:
        for value in (0, 59, 24 * 3600 + 1, "1800", 1.5, True):
            with self.assertRaises(Exception):
                attention.set_interval(value)
        self.assertEqual(attention.settings()["interval_s"], 1800, "a refusal leaves the setting alone")

    def test_a_corrupt_settings_file_falls_back_to_the_default(self) -> None:
        (self.data / attention.SETTINGS_FILE).write_text("{not json")
        self.assertEqual(attention.settings()["interval_s"], attention.DEFAULT_INTERVAL_S)


class Caching(Fixture):
    def test_an_answer_before_any_measurement_is_measured_not_invented(self) -> None:
        got = attention.cached()
        self.assertEqual(got["source"], "measured")
        self.assertIn("measured_at", got)

    def test_a_cached_answer_says_so_and_keeps_its_own_time(self) -> None:
        attention.refresh(disk=GREEN, activity_state="ready", now=500.0)
        got = attention.cached()
        self.assertEqual(got["source"], "cached")
        self.assertEqual(got["measured_at"], 500.0)


class FleetRow(Fixture):
    def test_the_row_carries_the_attention_block(self) -> None:
        from components.hub import standalone
        attention.refresh(disk=GREEN, activity_state="ready", now=42.0)
        row = standalone._trash_and_memory({})
        self.assertIn("attention", row)
        self.assertEqual(row["attention"]["state"], "ok")
        self.assertEqual(row["attention"]["measured_at"], 42.0)


if __name__ == "__main__":
    unittest.main()
