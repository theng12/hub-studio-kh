"""The identity this Hub publishes beside raw "local" machine ids.

Every site's fleet row and terminal evidence carries the same sentinel
(`machine`/`machine_id` = "local"), so a consumer could not tell one site's local
from another's, and "This Mac" was the same placeholder on every site. The
additive context — `machine_label`, `controller_id`, `controller_label`,
`site_id`, `site_name` — names the configured machine and the persisted site
without moving the sentinel or the shipped defaults, and a remote executor keeps
its own label: this controller's local name never describes a job another Mac ran.
"""
from __future__ import annotations

import contextlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from components.hub.backend import broker, control_plane, registry  # noqa: E402

CONTEXT_KEYS = ("machine_label", "controller_id", "controller_label", "site_id", "site_name")


class ExecutionIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="hub-execution-identity-")
        self.addCleanup(self.temporary.cleanup)
        data = Path(self.temporary.name)
        for target, attribute, value in (
            (control_plane, "SETTINGS_FILE", data / "controller_settings.json"),
            (control_plane, "DATABASE_URL_FILE", data / ".controller_database_url"),
            (registry, "LABELS_FILE", data / "machine_labels.json"),
        ):
            patcher = mock.patch.object(target, attribute, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        # The writer lock's own file lives beside the real data directory; none of these
        # tests is about the lock.
        lock = mock.patch.object(control_plane, "settings_writer_lock",
                                 lambda **kwargs: contextlib.nullcontext())
        lock.start()
        self.addCleanup(lock.stop)
        for module, attribute in ((control_plane, "_settings_cache"), (registry, "_labels_cache")):
            previous = getattr(module, attribute)
            setattr(module, attribute, None)
            self.addCleanup(setattr, module, attribute, previous)
        self.labels = data / "machine_labels.json"

    def identify(self) -> None:
        control_plane.save_settings({"controller_id": "site1500-hub", "site_id": "site1500",
                                     "site_name": "Site 1500"})

    def test_the_context_is_the_persisted_settings_not_a_guess(self) -> None:
        self.identify()
        identity = control_plane.execution_identity()
        self.assertEqual("site1500-hub", identity["controller_id"])
        self.assertEqual("site1500", identity["site_id"])
        self.assertEqual("Site 1500", identity["site_name"])
        self.assertEqual(control_plane.public_settings()["machine_name"], identity["machine_label"])
        self.assertEqual(identity["machine_label"], identity["controller_label"])
        self.assertTrue(identity["machine_label"], "a blank label would read as no data")

    def test_the_shipped_defaults_stay_the_compatibility_values(self) -> None:
        identity = control_plane.execution_identity()
        self.assertEqual("local-site", identity["site_id"], "the raw default must not migrate")
        self.assertEqual("Local site", identity["site_name"])
        self.assertTrue(identity["controller_id"])
        self.assertTrue(identity["machine_label"])

    def test_local_evidence_keeps_the_raw_sentinel_and_qualifies_it(self) -> None:
        self.identify()
        result = broker.terminal_result({"id": "b"}, {"state": "done", "index": 0, "machine": "local"})
        self.assertIsNotNone(result)
        self.assertEqual("local", result["machine_id"], "the raw routing sentinel must not move")
        identity = control_plane.execution_identity()
        for key in CONTEXT_KEYS:
            self.assertEqual(identity[key], result[key], key)

    def test_a_remote_executor_is_named_by_its_own_label(self) -> None:
        self.identify()
        self.labels.write_text(json.dumps({"node-mac": "Studio 3"}), encoding="utf-8")
        registry._labels_cache = None
        result = broker.terminal_result({"id": "b"}, {"state": "done", "index": 0, "machine": "node-mac"})
        self.assertEqual("node-mac", result["machine_id"], "the raw id must not move")
        self.assertEqual("Studio 3", result["machine_label"],
                         "a remote executor must not carry the controller's local name")
        self.assertNotEqual(result["controller_label"], result["machine_label"])
        self.assertEqual("site1500-hub", result["controller_id"])
        self.assertEqual("site1500", result["site_id"])

    def test_an_unknown_remote_executor_falls_back_to_its_id(self) -> None:
        result = broker.terminal_result({"id": "b"}, {"state": "done", "index": 0, "machine": "ghost-mac"})
        self.assertEqual("ghost-mac", result["machine_id"])
        self.assertEqual("ghost-mac", result["machine_label"],
                         "an unnamed Mac publishes its id, never this controller's name")

    def test_a_job_that_is_not_done_publishes_nothing(self) -> None:
        self.assertIsNone(broker.terminal_result({"id": "b"}, {"state": "running",
                                                              "machine": "local"}))


if __name__ == "__main__":
    unittest.main()
