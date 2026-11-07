"""The frozen r5 memory-admission inventory on the existing aggregate catalog.

`monitor._assemble_aggregate_catalog` already annotates each worker row with the scalar
`hub_free_memory_gb`/`hub_free_memory_source`. Proposal r5 adds one `hub_memory_admission`
object per applicable row, resolved by the same `memory_admission.effective_state` the GET
route answers with:

* the top-level token is the model-wide `(node_id, model)` scope only;
* each `ram.modes[mode]` view and the matching `reference[mode]` view carry the exact
  `(node_id, model, mode)` token and `revision_covers` GET would return for that mode;
* only this Hub's own rows are annotated. A remote row is the remote producer's answer and
  keeps whatever annotation it published; this controller's settings and RAM are never
  applied to it.

No route, broker or core policy code is exercised or changed here.
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from components.hub.backend import memory_admission, monitor as monitor_module
from components.hub.backend.monitor import StudioMonitor

MODEL = "AITRADER/FLUX2-klein-4B-mlx-4bit"
UNKNOWN = "no/such-model"
REMOTE_ONLY = "org/remote-only-model"
TIER_16 = 16 * 1024 ** 3
LOCAL_NODE = "local-node"

REMOTE_ANNOTATION = {
    "revision": "a" * 64,
    "revision_covers": ["node_id", "model"],
    "node_id": "node-b",
    "model": REMOTE_ONLY,
    "ram": {"model_wide": {"effective_min_free_memory_gb": 1.0}, "modes": {}},
    "reference": {},
}


class _InventoryMonitor(StudioMonitor):
    """A monitor with the registry, status and cached catalogs the assembler reads."""

    def __init__(self, registry: list[dict], status: dict, catalogs: dict[str, dict]):
        self.registry = registry
        self.status = status
        self._catalog_cache = {
            sid: (time.time(), catalog) for sid, catalog in catalogs.items()
        }
        self._transcribe_cache: dict = {}
        self._catalog_meta: dict = {}
        self.catalog_state_path = Path(tempfile.mkdtemp()) / "catalog_observations.json"

    def catalog_observation(self, studio_id, *, transcription=False):
        return {"observed_at": 0.0, "age_seconds": 0.0, "stale": False, "last_error": None}


class InventoryCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        settings = Path(self.temp.name) / "memory_admission_overrides.json"
        patcher = mock.patch.object(memory_admission, "SETTINGS_FILE", settings)
        patcher.start()
        self.addCleanup(patcher.stop)
        memory_admission.reset_for_tests()
        self.addCleanup(memory_admission.reset_for_tests)

        node_patch = mock.patch.object(monitor_module, "_local_node_id", return_value=LOCAL_NODE)
        node_patch.start()
        self.addCleanup(node_patch.stop)
        bytes_patch = mock.patch.object(
            monitor_module, "_local_total_memory_bytes", return_value=TIER_16)
        bytes_patch.start()
        self.addCleanup(bytes_patch.stop)

        self.local_studio = {"id": "image", "modality": "image", "machine": "local"}
        self.remote_studio = {"id": "image@node-b", "modality": "image", "machine": "node-b"}
        self.status = {
            "image": {"status": "down", "health": {"memory": {"total_gb": 17.18}}},
            "image@node-b": {"status": "down", "health": {"memory": {"total_gb": 34.36}}},
        }
        self.local_catalog = {"models": [
            {"repo": MODEL, "min_total_memory_gb": 8.0, "min_free_memory_gb": 2.8},
            {"repo": UNKNOWN, "min_total_memory_gb": 8.0},
        ]}
        self.remote_catalog = {"models": [
            {"repo": MODEL, "min_total_memory_gb": 8.0},
            {"repo": REMOTE_ONLY, "min_total_memory_gb": 8.0,
             "hub_memory_admission": REMOTE_ANNOTATION},
        ]}
        self.monitor = _InventoryMonitor(
            [self.local_studio, self.remote_studio],
            self.status,
            {"image": self.local_catalog, "image@node-b": self.remote_catalog},
        )

    def assemble(self) -> dict:
        return self.monitor._assemble_aggregate_catalog(
            [self.local_catalog, self.remote_catalog], [])

    @staticmethod
    def row(assembled: dict, machine: str, repo: str) -> dict:
        return next(
            item for item in assembled["models"]
            if item["hub_machine"] == machine and item.get("repo") == repo
        )

    def inventory(self, assembled: dict, machine: str = "local",
                  repo: str = MODEL) -> dict:
        return self.row(assembled, machine, repo)["hub_memory_admission"]

    def assert_token(self, value: str) -> None:
        self.assertRegex(value, r"^[0-9a-f]{64}$")


class PublicationIdentityTests(InventoryCase):
    def test_only_the_local_node_gets_an_inventory_and_remote_bytes_are_preserved(self) -> None:
        assembled = self.assemble()

        local = self.inventory(assembled)
        self.assertEqual(local["node_id"], LOCAL_NODE)
        self.assertEqual(local["model"], MODEL)
        self.assert_token(local["revision"])
        self.assertEqual(local["revision_covers"],
                         memory_admission._REVISION_COVERS_MODEL)

        remote = self.row(assembled, "node-b", MODEL)
        self.assertNotIn(
            "hub_memory_admission", remote,
            "this controller must not fabricate an effective policy for another node",
        )
        preserved = self.row(assembled, "node-b", REMOTE_ONLY)["hub_memory_admission"]
        self.assertEqual(preserved, REMOTE_ANNOTATION,
                         "a remote producer's own annotation must travel unchanged")

    def test_a_local_override_is_never_applied_to_another_nodes_rows(self) -> None:
        memory_admission.set_override(
            MODEL, min_total_memory_gb=12, min_free_memory_gb=4, catalog_entry={"repo": MODEL})
        assembled = self.assemble()

        local = self.inventory(assembled)
        self.assertEqual(local["ram"]["model_wide"]["effective_min_total_memory_gb"], 12.0)
        self.assertEqual(local["ram"]["model_wide"]["effective_min_free_memory_gb"], 4.0)
        self.assertEqual(local["ram"]["model_wide"]["override_scope"], "model")

        self.assertNotIn("hub_memory_admission", self.row(assembled, "node-b", MODEL))
        self.assertEqual(self.row(assembled, "node-b", REMOTE_ONLY)["hub_memory_admission"],
                         REMOTE_ANNOTATION)

    def test_the_candidate_path_publishes_the_same_inventory(self) -> None:
        # `candidate_models` reads exactly this cache-only aggregate (monitor.py:1265).
        cached = self.monitor.cached_aggregate_catalog()
        direct = self.assemble()
        self.assertEqual(self.inventory(cached)["revision"], self.inventory(direct)["revision"])
        self.assertIn("hub_memory_admission", self.row(cached, "local", MODEL))


class TokenScopeTests(InventoryCase):
    def test_each_mode_view_is_bit_for_bit_the_exact_mode_get(self) -> None:
        assembled = self.assemble()
        row = self.row(assembled, "local", MODEL)
        inventory = row["hub_memory_admission"]

        wide = memory_admission.effective_state(MODEL, row, node_id=LOCAL_NODE)
        self.assertEqual(inventory["revision"], wide["revision"])
        self.assertEqual(inventory["revision_covers"], wide["revision_covers"])
        self.assertEqual(inventory["ram"]["model_wide"], wide)

        for mode in memory_admission.MODES:
            with self.subTest(mode=mode):
                expected = memory_admission.effective_state(
                    MODEL, row, node_id=LOCAL_NODE, mode=mode,
                    total_memory_bytes=TIER_16)
                self.assertEqual(inventory["ram"]["modes"][mode], expected)
                reference = inventory["reference"][mode]
                self.assertEqual(reference["revision"], expected["revision"])
                self.assertEqual(reference["revision_covers"], expected["revision_covers"])
                self.assertEqual(expected["revision_covers"],
                                 memory_admission._REVISION_COVERS_MODE)
                self.assertEqual(reference["physical_tier"],
                                 expected["reference_policy"]["physical_tier"])
                self.assertEqual(reference["total_memory_bytes"], TIER_16)
                self.assertEqual(reference["rows"],
                                 expected["reference_policy"]["rows"])

        self.assertNotEqual(
            inventory["revision"],
            inventory["ram"]["modes"]["txt2img"]["revision"],
            "the top-level token is the model-wide scope, never a mode token",
        )
        self.assertNotEqual(
            inventory["ram"]["modes"]["txt2img"]["revision"],
            inventory["ram"]["modes"]["edit"]["revision"],
            "a token must never be copied across modes",
        )

    def test_an_unknown_model_keeps_null_values_with_reasons(self) -> None:
        assembled = self.assemble()
        reference = self.inventory(assembled, repo=UNKNOWN)["reference"]["edit"]
        generic = next(row for row in reference["rows"] if row["reference_count"] is None)
        self.assertIsNone(generic["effective"]["allowed"])
        self.assertIsNone(generic["effective"]["max_reference_images"])
        self.assertIsNone(generic["effective"]["swap_allowance_gb"])
        self.assertEqual(generic["sources"], {"allowed": "absent",
                                              "max_reference_images": "absent",
                                              "swap_allowance_gb": "absent"})
        for field in ("allowed", "max_reference_images", "swap_allowance_gb"):
            self.assertTrue(generic["unknown_reasons"][field])


class MutationVisibilityTests(InventoryCase):
    def test_a_write_is_visible_on_the_next_read_on_both_paths(self) -> None:
        before_cached = self.inventory(self.monitor.cached_aggregate_catalog())
        before_live = self.inventory(asyncio.run(self.monitor.aggregate_catalog()))
        self.assertEqual(before_cached["revision"], before_live["revision"])

        memory_admission.set_override(
            MODEL, min_total_memory_gb=12, min_free_memory_gb=5, catalog_entry={"repo": MODEL})

        after_cached = self.inventory(self.monitor.cached_aggregate_catalog())
        after_live = self.inventory(asyncio.run(self.monitor.aggregate_catalog()))
        self.assertNotEqual(after_cached["revision"], before_cached["revision"],
                            "the annotation must be resolved fresh, never cached")
        self.assertEqual(after_cached["revision"], after_live["revision"])
        self.assertEqual(after_cached["ram"]["model_wide"]["effective_min_free_memory_gb"], 5.0)
        self.assertEqual(after_cached["ram"]["model_wide"]["min_free_source"],
                         "operator_override")

        # A reference write flips only the mode scopes that bind it.
        edit_before = after_cached["ram"]["modes"]["edit"]["revision"]
        txt_before = after_cached["ram"]["modes"]["txt2img"]["revision"]
        memory_admission.set_reference_override(
            MODEL, "edit", 1, {"allowed": False}, total_memory_bytes=TIER_16)
        after_write = self.inventory(self.monitor.cached_aggregate_catalog())
        self.assertNotEqual(after_write["ram"]["modes"]["edit"]["revision"], edit_before)
        self.assertEqual(after_write["ram"]["modes"]["txt2img"]["revision"], txt_before,
                         "a reference write must not move the other mode's token")


class FalseZeroNullTests(InventoryCase):
    def test_false_zero_and_null_survive_verbatim(self) -> None:
        memory_admission.set_reference_override(
            MODEL, "edit", 1,
            {"allowed": False, "max_reference_images": 0, "swap_allowance_gb": 0},
            total_memory_bytes=TIER_16)
        reference = self.inventory(self.assemble())["reference"]["edit"]
        rows = {row["reference_count"]: row for row in reference["rows"]}
        row = rows[1]
        self.assertIs(row["effective"]["allowed"], False)
        self.assertEqual(row["effective"]["max_reference_images"], 0)
        self.assertEqual(row["effective"]["swap_allowance_gb"], 0.0)
        self.assertEqual(row["sources"]["allowed"], "override")
        self.assertTrue(row["overridden"])

        # A count no rule states stays null with its reason, never a guessed zero or false.
        unknown = self.inventory(self.assemble(), repo=UNKNOWN)["reference"]["edit"]
        generic = next(row for row in unknown["rows"] if row["reference_count"] is None)
        self.assertIsNone(generic["effective"]["allowed"])
        self.assertTrue(generic["unknown_reasons"]["allowed"])
        self.assertFalse(generic["overridden"])


if __name__ == "__main__":
    unittest.main()
