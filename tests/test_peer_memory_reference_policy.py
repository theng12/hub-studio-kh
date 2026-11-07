"""Focused regressions for producer reference policy on the peer catalog proxy.

The raw worker catalog used to lose the r5 inventory on its way to a controller. These tests pin
the annotation the proxy adds: one producer snapshot per local row, the same identity and host
total the aggregate catalog uses, and nothing at all for a remote worker or a non-catalog read.
No Hub app, network, filesystem model library or inference is used.
"""
from __future__ import annotations

import atexit
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_ROOT = tempfile.TemporaryDirectory(prefix="studiohub-peer-policy-")
atexit.register(_ROOT.cleanup)
os.environ.setdefault("STUDIOHUB_DATA_DIR", _ROOT.name)
_WORKERS = Path(_ROOT.name) / "workers.json"
_WORKERS.write_text(json.dumps([]))
os.environ.setdefault("STUDIOHUB_WORKERS_FILE", str(_WORKERS))

from components.hub import standalone  # noqa: E402
from components.hub.backend import memory_admission, monitor as monitor_module  # noqa: E402

MODEL = "AITRADER/FLUX2-klein-4B-mlx-4bit"
ROW = {"repo": MODEL, "min_total_memory_gb": 8.0}
TIER_16 = 16 * 1024 ** 3
WORKER = {"id": "image-1", "modality": "image", "machine": "local"}
REMOTE = {"id": "image-remote", "modality": "image", "machine": "node-b"}


class FakeMonitor:
    def __init__(self, models: list[dict], status: dict | None = None):
        self._models = models
        self.status = status or {}

    def cached_aggregate_catalog(self) -> dict:
        return {"models": self._models}


def catalog_bytes(*rows: dict) -> bytes:
    return json.dumps({"models": list(rows)}).encode()


class PeerCatalogAnnotationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Path(tempfile.mkdtemp(prefix="studiohub-peer-store-")) / \
            "memory_admission_overrides.json"
        self.addCleanup(lambda: self.settings.parent.rmdir() if self.settings.parent.exists()
                        else None)
        patches = [
            mock.patch.object(memory_admission, "SETTINGS_FILE", self.settings),
            mock.patch.object(monitor_module, "_local_node_id", lambda: "machine-a"),
            mock.patch.object(monitor_module, "_local_total_memory_bytes", lambda: TIER_16),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        memory_admission.reset_for_tests()
        self.addCleanup(memory_admission.reset_for_tests)

    def snapshot(self) -> dict:
        return monitor_module._memory_admission_inventory(
            MODEL, ROW, node_id="machine-a", total_memory_bytes=TIER_16)

    def annotate(self, monitor, worker, content, content_type="application/json"):
        return standalone._annotate_worker_catalog(monitor, worker, content, content_type)

    def test_a_local_row_carries_the_same_snapshot_as_the_aggregate_read(self) -> None:
        aggregate = {"repo": MODEL, "hub_studio": WORKER["id"],
                     "hub_memory_admission": self.snapshot()}
        answer = self.annotate(FakeMonitor([aggregate]), WORKER,
                               catalog_bytes({"repo": MODEL, "min_total_memory_gb": 8}))
        row = json.loads(answer)["models"][0]
        self.assertEqual(row["hub_memory_admission"], aggregate["hub_memory_admission"])
        self.assertEqual(row["hub_memory_admission"]["node_id"], "machine-a")
        self.assertEqual(row["hub_memory_admission"]["revision"],
                         self.snapshot()["revision"])

    def test_an_aggregate_miss_uses_the_same_identity_and_host_total(self) -> None:
        expected = monitor_module._memory_admission_inventory(
            MODEL, memory_admission.publish_floor(dict(ROW), 16.0),
            node_id="machine-a", total_memory_bytes=TIER_16)
        monitor = FakeMonitor([], {"image-1": {"health": {"memory": {"total_gb": 16.0}}}})
        row = json.loads(self.annotate(
            monitor, WORKER, catalog_bytes({"repo": MODEL, "min_total_memory_gb": 8}))
        )["models"][0]
        self.assertEqual(row["hub_memory_admission"], expected)

    def test_a_remote_worker_row_is_never_annotated_with_this_macs_policy(self) -> None:
        content = catalog_bytes({"repo": MODEL, "min_total_memory_gb": 8})
        self.assertEqual(self.annotate(FakeMonitor([]), REMOTE, content), content)
        row = json.loads(content)["models"][0]
        self.assertNotIn("hub_memory_admission", row)

    def test_non_catalog_and_non_json_reads_are_untouched(self) -> None:
        content = catalog_bytes({"repo": MODEL})
        self.assertEqual(self.annotate(FakeMonitor([]), WORKER, content, "text/plain"), content)
        self.assertEqual(self.annotate(FakeMonitor([]), WORKER, b"not json"), b"not json")
        self.assertEqual(self.annotate(FakeMonitor([]), WORKER, b'{"models": "x"}'),
                         b'{"models": "x"}')


if __name__ == "__main__":
    unittest.main()
