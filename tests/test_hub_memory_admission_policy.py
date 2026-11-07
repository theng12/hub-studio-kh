"""Focused regressions for the D1 memory-admission core (proposal.r5, frozen).

Covers the mode/count policy, the physical-tier authority, strict false/0/null/unknown
handling, the SHA256 effective-state revision, and the one-lock CAS that keeps a fleet
compare/write from interleaving with the legacy local writes. No route, broker, monitor or
Image-worker code is exercised or changed here.
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from components.hub.backend import memory_admission

MODEL = "AITRADER/FLUX2-klein-4B-mlx-4bit"
ROW = {"repo": MODEL, "min_total_memory_gb": 8.0}
TIER_8 = 8 * 1024 ** 3
TIER_16 = 16 * 1024 ** 3


class AdmissionPolicyCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = Path(self.temp.name) / "memory_admission_overrides.json"
        patcher = mock.patch.object(memory_admission, "SETTINGS_FILE", self.settings)
        patcher.start()
        self.addCleanup(patcher.stop)
        memory_admission.reset_for_tests()
        self.addCleanup(memory_admission.reset_for_tests)

    def rows(self, policy: dict) -> dict:
        return {row["reference_count"]: row for row in policy["rows"]}


class LegacyAndModeScopeTests(AdmissionPolicyCase):
    def test_legacy_write_keeps_its_exact_record_shape_and_scope(self) -> None:
        policy = memory_admission.set_override(
            MODEL, min_total_memory_gb=12, min_free_memory_gb=3, catalog_entry=ROW)
        self.assertEqual(policy["override_scope"], "model")
        self.assertEqual(policy["effective_min_total_memory_gb"], 12.0)
        self.assertEqual(policy["effective_min_free_memory_gb"], 3.0)
        record = json.loads(self.settings.read_text())[MODEL.lower()]
        self.assertEqual(set(record),
                         {"model", "min_total_memory_gb", "min_free_memory_gb", "updated_at"})
        self.assertNotIn("mode", record)
        self.assertNotIn("seq", record)

        cleared = memory_admission.reset_override(MODEL, ROW)
        self.assertIsNone(cleared["override_scope"])
        self.assertFalse(self.settings.exists(), "the legacy reset must leave no record behind")

    def test_mode_record_wins_then_falls_back_to_the_model_wide_record(self) -> None:
        memory_admission.set_override(
            MODEL, min_total_memory_gb=12, min_free_memory_gb=3, catalog_entry=ROW)
        scoped = memory_admission.set_override(
            MODEL, min_total_memory_gb=10, min_free_memory_gb=2.5, catalog_entry=ROW,
            mode="txt2img")
        self.assertEqual(scoped["override_scope"], "mode")
        self.assertEqual(scoped["effective_min_total_memory_gb"], 10.0)
        self.assertEqual(scoped["effective_min_free_memory_gb"], 2.5)
        self.assertEqual(memory_admission.worker_free_floor_gb(MODEL, "txt2img"), 2.5)

        # A mode reset clears only the mode record; the surviving legacy record is labelled.
        fallback = memory_admission.reset_override(MODEL, ROW, mode="txt2img")
        self.assertEqual(fallback["override_scope"], "model")
        self.assertEqual(fallback["effective_min_total_memory_gb"], 12.0)
        self.assertEqual(memory_admission.worker_free_floor_gb(MODEL, "txt2img"), 3.0)
        # The model-wide read never borrows a mode record.
        self.assertEqual(memory_admission.worker_free_floor_gb(MODEL), 3.0)

    def test_a_model_wide_reset_does_not_clear_a_mode_record(self) -> None:
        memory_admission.set_override(
            MODEL, min_total_memory_gb=12, min_free_memory_gb=3, catalog_entry=ROW)
        memory_admission.set_override(
            MODEL, min_total_memory_gb=10, min_free_memory_gb=2.5, catalog_entry=ROW,
            mode="txt2img")
        memory_admission.reset_override(MODEL, ROW)
        policy = memory_admission.describe(MODEL, ROW, mode="txt2img")
        self.assertEqual(policy["override_scope"], "mode")
        self.assertEqual(policy["effective_min_free_memory_gb"], 2.5)


class ReferencePolicyTests(AdmissionPolicyCase):
    def test_generic_and_exact_rows_are_distinct_and_a_reset_clears_only_one(self) -> None:
        memory_admission.set_reference_override(
            MODEL, "edit", None,
            {"allowed": True, "max_reference_images": 2, "swap_allowance_gb": 1.5},
            total_memory_bytes=TIER_16)
        memory_admission.set_reference_override(
            MODEL, "edit", 2, {"allowed": True, "max_reference_images": 5},
            total_memory_bytes=TIER_16)
        memory_admission.set_reference_override(
            MODEL, "edit", 3, {"allowed": False},
            total_memory_bytes=TIER_16)

        rows = self.rows(memory_admission.reference_policy(MODEL, "edit", TIER_16))
        self.assertEqual(sorted(rows, key=lambda count: -1 if count is None else count),
                         [None, 1, 2, 3, 4])
        self.assertEqual(rows[None]["effective"]["max_reference_images"], 2)
        self.assertEqual(rows[None]["sources"]["max_reference_images"], "override")
        self.assertEqual(rows[1]["effective"]["max_reference_images"], 3)
        self.assertEqual(rows[1]["sources"]["max_reference_images"], "published",
                         "a generic override must never answer for an exact published row")
        self.assertEqual(rows[2]["effective"]["max_reference_images"], 5)
        self.assertEqual(rows[2]["sources"]["max_reference_images"], "override")
        self.assertFalse(rows[3]["effective"]["allowed"])
        self.assertEqual(rows[3]["sources"]["allowed"], "override")

        memory_admission.set_reference_override(
            MODEL, "edit", 2, None, total_memory_bytes=TIER_16)
        rows = self.rows(memory_admission.reference_policy(MODEL, "edit", TIER_16))
        self.assertIsNone(rows[2]["override"])
        self.assertEqual(rows[2]["effective"]["max_reference_images"], 3)
        self.assertEqual(rows[2]["sources"]["max_reference_images"], "published")
        self.assertFalse(rows[3]["effective"]["allowed"], "sibling row 3 was reset")
        self.assertEqual(rows[None]["effective"]["max_reference_images"], 2,
                         "the generic row was reset by an exact-count reset")

    def test_resolution_order_is_exact_override_exact_published_generic_then_default(self) -> None:
        memory_admission.set_reference_override(
            MODEL, "edit", None, {"allowed": False}, total_memory_bytes=TIER_16)
        # Count 9 carries an override (so it is a row) but has no published exact row.
        memory_admission.set_reference_override(
            MODEL, "edit", 9, {"swap_allowance_gb": 2}, total_memory_bytes=TIER_16)
        rows = self.rows(memory_admission.reference_policy(MODEL, "edit", TIER_16))
        self.assertTrue(rows[1]["effective"]["allowed"],
                        "the exact published row outranks a generic override")
        self.assertEqual(rows[1]["sources"]["allowed"], "published")
        # Count 9 has no exact published row: the generic override answers, per field.
        self.assertFalse(rows[9]["effective"]["allowed"])
        self.assertEqual(rows[9]["sources"]["allowed"], "override")
        self.assertEqual(rows[9]["effective"]["max_reference_images"], 3)
        self.assertEqual(rows[9]["sources"]["max_reference_images"], "published")
        self.assertEqual(rows[9]["effective"]["swap_allowance_gb"], 2.0)
        self.assertEqual(rows[9]["sources"]["swap_allowance_gb"], "override")

        memory_admission.set_reference_override(
            MODEL, "edit", 1, {"allowed": False, "max_reference_images": 0},
            total_memory_bytes=TIER_16)
        row = self.rows(memory_admission.reference_policy(MODEL, "edit", TIER_16))[1]
        self.assertIs(row["effective"]["allowed"], False)
        self.assertEqual(row["effective"]["max_reference_images"], 0)
        self.assertEqual(row["sources"]["allowed"], "override")
        self.assertEqual(row["sources"]["max_reference_images"], "override")

    def test_model_default_is_the_last_rung_and_preserves_false_zero(self) -> None:
        policy = memory_admission.reference_policy(
            "no/such-model", "edit", TIER_16,
            model_default={"allowed": False, "max_reference_images": 0,
                           "swap_allowance_gb": 0})
        row = policy["rows"][0]
        self.assertIs(row["effective"]["allowed"], False)
        self.assertEqual(row["effective"]["max_reference_images"], 0)
        self.assertEqual(row["effective"]["swap_allowance_gb"], 0.0)
        self.assertEqual(row["sources"]["allowed"], "published")
        self.assertIsNone(row["unknown_reasons"]["allowed"])

        partial = memory_admission.reference_policy(
            "no/such-model", "edit", TIER_16, model_default={"allowed": True})
        row = partial["rows"][0]
        self.assertIs(row["effective"]["allowed"], True)
        self.assertEqual(row["sources"]["allowed"], "published")
        self.assertIsNone(row["effective"]["max_reference_images"])
        self.assertIsNone(row["effective"]["swap_allowance_gb"])
        self.assertTrue(row["unknown_reasons"]["max_reference_images"],
                        "a field the model default did not supply stays unknown")
        self.assertIsNone(row["published"])

    def test_unknown_stays_null_with_a_reason_not_false_or_zero(self) -> None:
        policy = memory_admission.reference_policy("no/such-model", "edit", TIER_16)
        self.assertTrue(policy["available"])
        row = policy["rows"][0]
        self.assertIsNone(row["effective"]["allowed"])
        self.assertIsNone(row["effective"]["max_reference_images"])
        self.assertIsNone(row["effective"]["swap_allowance_gb"])
        self.assertEqual(row["sources"], {"allowed": "absent",
                                          "max_reference_images": "absent",
                                          "swap_allowance_gb": "absent"})
        for field in ("allowed", "max_reference_images", "swap_allowance_gb"):
            self.assertTrue(row["unknown_reasons"][field])
        self.assertIsNone(row["max_intrinsic"])
        self.assertTrue(row["intrinsic_unknown_reason"])

    def test_published_false_and_zero_survive_verbatim(self) -> None:
        row = memory_admission.reference_policy(MODEL, "edit", TIER_8)["rows"][0]
        self.assertIs(row["effective"]["allowed"], False)
        self.assertEqual(row["effective"]["max_reference_images"], 0)
        self.assertEqual(row["effective"]["swap_allowance_gb"], 0.0)
        self.assertEqual(row["sources"]["allowed"], "published")

    def test_intrinsic_cap_clamps_only_when_the_table_states_one(self) -> None:
        qwen = "mflux-community/qwen-image-2-1-turbo-mflux-q4"
        memory_admission.set_reference_override(
            qwen, "edit", None, {"allowed": True, "max_reference_images": 15},
            total_memory_bytes=TIER_16)
        row = memory_admission.reference_policy(qwen, "edit", TIER_16)["rows"][0]
        self.assertEqual(row["max_intrinsic"], 10)
        self.assertEqual(row["effective"]["max_reference_images"], 10)
        self.assertTrue(row["clamped_reason"])

        memory_admission.set_reference_override(
            MODEL, "edit", None, {"allowed": True, "max_reference_images": 99},
            total_memory_bytes=TIER_16)
        row = memory_admission.reference_policy(MODEL, "edit", TIER_16)["rows"][0]
        self.assertIsNone(row["max_intrinsic"])
        self.assertTrue(row["intrinsic_unknown_reason"])
        self.assertEqual(row["effective"]["max_reference_images"], 99,
                         "an unknown intrinsic cap must not be guessed into a clamp")
        self.assertIsNone(row["clamped_reason"])

    def test_reference_only_write_leaves_ram_untouched_and_vice_versa(self) -> None:
        memory_admission.set_override(
            MODEL, min_total_memory_gb=12, min_free_memory_gb=3, catalog_entry=ROW)
        before = memory_admission.describe(MODEL, ROW)
        memory_admission.set_reference_override(
            MODEL, "edit", None, {"allowed": True}, total_memory_bytes=TIER_16)
        after = memory_admission.describe(MODEL, ROW)
        for field in ("effective_min_total_memory_gb", "effective_min_free_memory_gb",
                      "override_scope"):
            self.assertEqual(before[field], after[field])
        memory_admission.set_override(
            MODEL, min_total_memory_gb=12, min_free_memory_gb=4, catalog_entry=ROW)
        rows = self.rows(memory_admission.reference_policy(MODEL, "edit", TIER_16))
        self.assertIs(rows[None]["effective"]["allowed"], True)
        memory_admission.reset_override(MODEL, ROW)
        rows = self.rows(memory_admission.reference_policy(MODEL, "edit", TIER_16))
        self.assertIs(rows[None]["effective"]["allowed"], True,
                      "a RAM reset must not clear a reference row")

    def test_partial_writes_retain_absent_fields_and_null_clears_only_one(self) -> None:
        memory_admission.set_reference_override(
            MODEL, "edit", 2,
            {"allowed": True, "max_reference_images": 5, "swap_allowance_gb": 1.5},
            total_memory_bytes=TIER_16)
        memory_admission.set_reference_override(
            MODEL, "edit", 2, {"allowed": True}, total_memory_bytes=TIER_16)
        row = self.rows(memory_admission.reference_policy(MODEL, "edit", TIER_16))[2]
        self.assertEqual(row["override"]["max_reference_images"], 5,
                         "an absent field must retain the stored value")
        self.assertEqual(row["override"]["swap_allowance_gb"], 1.5)

        memory_admission.set_reference_override(
            MODEL, "edit", 2, {"allowed": None}, total_memory_bytes=TIER_16)
        row = self.rows(memory_admission.reference_policy(MODEL, "edit", TIER_16))[2]
        self.assertNotIn("allowed", row["override"],
                         "an explicit null must clear only that field")
        self.assertEqual(row["override"]["max_reference_images"], 5)
        self.assertEqual(row["override"]["swap_allowance_gb"], 1.5)
        self.assertEqual(row["sources"]["allowed"], "published",
                         "the cleared field falls to the next rung")

        memory_admission.set_reference_override(
            MODEL, "edit", 2,
            {"max_reference_images": None, "swap_allowance_gb": None},
            total_memory_bytes=TIER_16)
        row = self.rows(memory_admission.reference_policy(MODEL, "edit", TIER_16))[2]
        self.assertIsNone(row["override"], "a cell with no fields left is the row's clear")
        self.assertEqual(row["effective"]["max_reference_images"], 3)
        self.assertEqual(row["sources"]["max_reference_images"], "published")

        memory_admission.set_reference_override(
            MODEL, "edit", 3, {"allowed": False}, total_memory_bytes=TIER_16)
        memory_admission.set_reference_override(
            MODEL, "edit", 3, None, total_memory_bytes=TIER_16)
        row = self.rows(memory_admission.reference_policy(MODEL, "edit", TIER_16))[3]
        self.assertIsNone(row["override"], "a top-level null clears the selected row only")

    def test_reference_write_validation_is_strict(self) -> None:
        for count in (0, -1, True, "2", 2.0):
            with self.subTest(count=count):
                with self.assertRaises(ValueError):
                    memory_admission.set_reference_override(
                        MODEL, "edit", count, {"allowed": True},
                        total_memory_bytes=TIER_16)
        with self.assertRaises(ValueError):
            memory_admission.set_reference_override(
                MODEL, "edit", None, {}, total_memory_bytes=TIER_16)
        with self.assertRaises(ValueError):
            memory_admission.set_reference_override(
                MODEL, "edit", None, {"invented": 1}, total_memory_bytes=TIER_16)
        with self.assertRaises(ValueError):
            memory_admission.set_reference_override(
                MODEL, "edit", None, {"max_reference_images": -1},
                total_memory_bytes=TIER_16)
        with self.assertRaises(ValueError):
            memory_admission.set_reference_override(
                MODEL, "edit", None, {"allowed": True}, total_memory_bytes=None)
        self.assertFalse(self.settings.exists())


class PhysicalTierTests(AdmissionPolicyCase):
    def test_missing_table_reads_unavailable_and_refuses_writes(self) -> None:
        missing = Path(self.temp.name) / "missing-reference-limits.json"
        with mock.patch.object(memory_admission, "REFERENCE_LIMITS_PATH", missing):
            memory_admission._reference_limit_table.cache_clear()
            try:
                self.assertIsNone(memory_admission.physical_tier(TIER_16))
                policy = memory_admission.reference_policy(MODEL, "edit", TIER_16)
                self.assertIs(policy["available"], False)
                self.assertTrue(policy["reason"])
                state = memory_admission.effective_state(
                    MODEL, ROW, node_id="local", mode="edit", total_memory_bytes=TIER_16)
                self.assertIs(state["reference_policy"]["available"], False)
                self.assertRegex(state["revision"], r"^[0-9a-f]{64}$")
                with self.assertRaises(memory_admission.TierUnknown):
                    memory_admission.set_reference_override(
                        MODEL, "edit", 2, {"allowed": True}, total_memory_bytes=TIER_16)
                self.assertFalse(self.settings.exists(),
                                 "a TIER_UNKNOWN write must leave nothing behind")
            finally:
                memory_admission._reference_limit_table.cache_clear()

    def test_band_matches_the_image_resolver(self) -> None:
        from components.image.app.backend import generation
        samples = (6 * 1024 ** 3, TIER_8, TIER_16, 24 * 1024 ** 3, 32 * 1024 ** 3, 34.36e9)
        for value in samples:
            with self.subTest(bytes=value):
                self.assertEqual(memory_admission.physical_tier(value),
                                 generation.reference_tier(value))
        self.assertEqual(memory_admission.physical_tier(6 * 1024 ** 3), "<8")
        self.assertEqual(memory_admission.physical_tier(34.36e9), "32")

    def test_an_unknown_host_total_is_not_invented_into_a_tier(self) -> None:
        for value in (None, 0, -1, True, "16", float("nan"), float("inf")):
            with self.subTest(total=value):
                self.assertIsNone(memory_admission.physical_tier(value))
        policy = memory_admission.reference_policy(MODEL, "edit", None)
        self.assertIs(policy["available"], False)
        self.assertIn("total memory is unknown", policy["reason"])
        state = memory_admission.effective_state(
            MODEL, ROW, node_id="local", mode="edit", total_memory_bytes=None)
        self.assertIs(state["reference_policy"]["available"], False)
        self.assertRegex(state["revision"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            state["revision"],
            memory_admission.effective_state(
                MODEL, ROW, node_id="local", mode="edit",
                total_memory_bytes=None)["revision"],
            "the unavailable mode state must have a stable token")
        # Model-wide RAM does not need a physical tier and stays available.
        memory_admission.set_override(
            MODEL, min_total_memory_gb=12, min_free_memory_gb=3, catalog_entry=ROW)
        model_wide = memory_admission.effective_state(
            MODEL, ROW, node_id="local", total_memory_bytes=None)
        self.assertEqual(model_wide["effective_min_total_memory_gb"], 12.0)
        self.assertEqual(model_wide["effective_min_free_memory_gb"], 3.0)
        self.assertRegex(model_wide["revision"], r"^[0-9a-f]{64}$")

    def test_an_unknown_total_refuses_a_reference_write_without_a_file(self) -> None:
        for value in (None, 0, -1, True, "16", float("nan"), float("inf")):
            with self.subTest(total=value):
                with self.assertRaises(ValueError):
                    memory_admission.set_reference_override(
                        MODEL, "edit", 2, {"allowed": True},
                        total_memory_bytes=value)
        self.assertFalse(self.settings.exists(),
                         "an unknown-tier write must leave nothing behind")

    def test_a_cell_written_on_another_tier_is_ignored_as_absent(self) -> None:
        memory_admission.set_reference_override(
            MODEL, "edit", None, {"allowed": True, "max_reference_images": 1},
            total_memory_bytes=TIER_8)
        row = memory_admission.reference_policy(MODEL, "edit", TIER_16)["rows"][0]
        self.assertTrue(row["stale_tier_ignored"])
        self.assertIsNone(row["override"])
        self.assertEqual(row["effective"]["max_reference_images"], 3)
        self.assertEqual(row["sources"]["max_reference_images"], "published")
        row = memory_admission.reference_policy(MODEL, "edit", TIER_8)["rows"][0]
        self.assertFalse(row["stale_tier_ignored"])
        self.assertEqual(row["effective"]["max_reference_images"], 1)

    def test_old_tier_values_stay_stored_and_restorable_across_a_new_tier_write(self) -> None:
        memory_admission.set_reference_override(
            MODEL, "edit", None,
            {"allowed": True, "max_reference_images": 2}, total_memory_bytes=TIER_8)
        memory_admission.set_reference_override(
            MODEL, "edit", 2, {"allowed": True, "max_reference_images": 4},
            total_memory_bytes=TIER_8)
        memory_admission.set_reference_override(
            MODEL, "edit", 3, {"allowed": False}, total_memory_bytes=TIER_8)
        memory_admission.set_reference_override(
            MODEL, "edit", 4, {"allowed": True, "max_reference_images": 1},
            total_memory_bytes=TIER_16)
        stored = json.loads(self.settings.read_text())[MODEL.lower() + "::reference"]
        self.assertEqual(sorted(stored["cells"]), ["16", "8"],
                         "a new-tier write must retain the old tier's settings")

        old = self.rows(memory_admission.reference_policy(MODEL, "edit", TIER_8))
        self.assertEqual(old[None]["effective"]["max_reference_images"], 2)
        self.assertEqual(old[2]["effective"]["max_reference_images"], 4)
        self.assertFalse(old[3]["effective"]["allowed"])
        self.assertFalse(old[None]["stale_tier_ignored"])

        new = self.rows(memory_admission.reference_policy(MODEL, "edit", TIER_16))
        self.assertEqual(new[4]["effective"]["max_reference_images"], 1)
        self.assertIsNone(new[2]["override"], "an old-tier exact cell applied on a new tier")
        self.assertTrue(new[None]["stale_tier_ignored"])

        # A selected reset at the new tier clears only that tier's selected row.
        memory_admission.set_reference_override(
            MODEL, "edit", 4, None, total_memory_bytes=TIER_16)
        stored = json.loads(self.settings.read_text())[MODEL.lower() + "::reference"]
        self.assertEqual(sorted(stored["cells"]), ["8"],
                         "a new-tier reset must not remove the old tier's block")
        old = self.rows(memory_admission.reference_policy(MODEL, "edit", TIER_8))
        self.assertEqual(old[None]["effective"]["max_reference_images"], 2)
        self.assertEqual(old[2]["effective"]["max_reference_images"], 4)
        self.assertFalse(old[3]["effective"]["allowed"])

        # A reset of one sibling leaves the other old-tier settings untouched.
        memory_admission.set_reference_override(
            MODEL, "edit", 2, None, total_memory_bytes=TIER_8)
        old = self.rows(memory_admission.reference_policy(MODEL, "edit", TIER_8))
        self.assertEqual(old[None]["effective"]["max_reference_images"], 2)
        self.assertFalse(old[3]["effective"]["allowed"])

    def test_an_older_single_tier_record_still_loads(self) -> None:
        self.settings.write_text(json.dumps({
            MODEL.lower() + "::reference": {
                "model": MODEL,
                "tier_gb": "8",
                "cells": {"edit": {"generic": {"allowed": True,
                                               "max_reference_images": 2,
                                               "updated_at": 1.0},
                                  "counts": {"2": {"allowed": False,
                                                   "updated_at": 1.0}}}},
                "updated_at": 1.0,
            }
        }))
        memory_admission.reset_for_tests()
        rows = self.rows(memory_admission.reference_policy(MODEL, "edit", TIER_8))
        self.assertEqual(rows[None]["effective"]["max_reference_images"], 2)
        self.assertIs(rows[2]["effective"]["allowed"], False)
        self.assertTrue(
            self.rows(memory_admission.reference_policy(MODEL, "edit", TIER_16))[None]
            ["stale_tier_ignored"])

    def test_a_write_on_a_new_tier_never_reapplies_the_stale_cells(self) -> None:
        memory_admission.set_reference_override(
            MODEL, "edit", 2, {"allowed": True, "max_reference_images": 1},
            total_memory_bytes=TIER_8)
        memory_admission.set_reference_override(
            MODEL, "edit", 3, {"allowed": True, "max_reference_images": 4},
            total_memory_bytes=TIER_16)
        rows = self.rows(memory_admission.reference_policy(MODEL, "edit", TIER_16))
        self.assertIsNone(rows[2]["override"], "a tier-8 cell applied on tier 16")
        self.assertEqual(rows[3]["effective"]["max_reference_images"], 4)


class RevisionAndCasTests(AdmissionPolicyCase):
    def test_revision_is_64_hex_deterministic_and_reverts_to_an_earlier_state(self) -> None:
        base = memory_admission.effective_state(MODEL, ROW, node_id="local")
        self.assertRegex(base["revision"], r"^[0-9a-f]{64}$")
        self.assertNotIn("reference_policy", base)
        self.assertEqual(base["revision_covers"],
                         memory_admission._REVISION_COVERS_MODEL)
        self.assertEqual(
            base["revision"],
            memory_admission.effective_state(MODEL, ROW, node_id="local")["revision"])

        memory_admission.set_override(
            MODEL, min_total_memory_gb=12, min_free_memory_gb=3, catalog_entry=ROW)
        written = memory_admission.effective_state(MODEL, ROW, node_id="local")
        self.assertNotEqual(base["revision"], written["revision"])
        memory_admission.reset_override(MODEL, ROW)
        reverted = memory_admission.effective_state(MODEL, ROW, node_id="local")
        self.assertEqual(base["revision"], reverted["revision"],
                         "a reset that restores the earlier state must regain its token")
        self.assertNotIn('"seq"', self.settings.read_text() if self.settings.exists() else "")

    def test_mode_token_covers_the_mode_scope_only(self) -> None:
        model_wide = memory_admission.effective_state(MODEL, ROW, node_id="local")
        mode = memory_admission.effective_state(
            MODEL, ROW, node_id="local", mode="edit", total_memory_bytes=TIER_16)
        self.assertIn("reference_policy", mode)
        self.assertNotEqual(model_wide["revision"], mode["revision"])
        self.assertEqual(mode["revision_covers"], memory_admission._REVISION_COVERS_MODE)
        self.assertIn("physical_tier", mode["revision_covers"])
        self.assertIn("reference_rows", mode["revision_covers"])

    def test_reference_write_time_is_not_bound_and_reset_regains_the_earlier_token(self) -> None:
        base = memory_admission.effective_state(
            MODEL, ROW, node_id="local", mode="edit", total_memory_bytes=TIER_16)
        self.assertIn("model_wide_fallback", base["revision_covers"])
        memory_admission.set_reference_override(
            MODEL, "edit", 2, {"allowed": True, "max_reference_images": 3},
            total_memory_bytes=TIER_16)
        first = memory_admission.effective_state(
            MODEL, ROW, node_id="local", mode="edit", total_memory_bytes=TIER_16)
        self.assertNotEqual(base["revision"], first["revision"])
        time.sleep(0.01)
        memory_admission.set_reference_override(
            MODEL, "edit", 2, {"allowed": True, "max_reference_images": 3},
            total_memory_bytes=TIER_16)
        second = memory_admission.effective_state(
            MODEL, ROW, node_id="local", mode="edit", total_memory_bytes=TIER_16)
        self.assertEqual(first["revision"], second["revision"],
                         "a write timestamp must not be part of the effective state")
        memory_admission.set_reference_override(
            MODEL, "edit", 2, None, total_memory_bytes=TIER_16)
        cleared = memory_admission.effective_state(
            MODEL, ROW, node_id="local", mode="edit", total_memory_bytes=TIER_16)
        self.assertEqual(base["revision"], cleared["revision"],
                         "a reset restoring the earlier state must regain its token")

    def test_a_published_table_change_flips_the_mode_token(self) -> None:
        state = memory_admission.effective_state(
            MODEL, ROW, node_id="local", mode="edit", total_memory_bytes=TIER_16)
        table = json.loads(json.dumps(memory_admission._reference_limit_table()))
        for row in table["rows"]:
            if (row.get("model_id") == MODEL and row.get("mode") == "edit"
                    and row.get("reference_count") is None):
                row["tiers"]["16"]["max_reference_images"] = 9
        with mock.patch.object(memory_admission, "_reference_limit_table", lambda: table):
            changed = memory_admission.effective_state(
                MODEL, ROW, node_id="local", mode="edit", total_memory_bytes=TIER_16)
        self.assertNotEqual(state["revision"], changed["revision"],
                            "a published-table change must flip the state token")

    def test_stale_expected_revision_is_a_conflict_with_the_fresh_state(self) -> None:
        current = memory_admission.effective_state(MODEL, ROW, node_id="local")
        with self.assertRaises(memory_admission.RevisionConflict) as caught:
            memory_admission.compare_and_write(
                MODEL, ROW, node_id="local", expected_revision="0" * 64,
                change=lambda: None)
        self.assertEqual(caught.exception.current["revision"], current["revision"])
        with self.assertRaises(ValueError):
            memory_admission.compare_and_write(
                MODEL, ROW, node_id="local", expected_revision="not-a-token",
                change=lambda: None)

    def test_concurrent_writers_cannot_interleave(self) -> None:
        current = memory_admission.effective_state(MODEL, ROW, node_id="local")
        barrier = threading.Barrier(2)
        outcomes: list[tuple[str, dict]] = []

        def writer(free: float) -> None:
            barrier.wait()
            try:
                result = memory_admission.compare_and_write(
                    MODEL, ROW, node_id="local",
                    expected_revision=current["revision"],
                    change=lambda: memory_admission.set_override(
                        MODEL, min_total_memory_gb=12, min_free_memory_gb=free,
                        catalog_entry=ROW))
                outcomes.append(("ok", result))
            except memory_admission.RevisionConflict as conflict:
                outcomes.append(("conflict", conflict.current))

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(writer, (2.0, 5.0)))

        self.assertEqual(sorted(kind for kind, _ in outcomes), ["conflict", "ok"])
        winner = next(state for kind, state in outcomes if kind == "ok")
        final = memory_admission.effective_state(MODEL, ROW, node_id="local")
        self.assertEqual(final["revision"], winner["revision"])
        self.assertEqual(final["effective_min_free_memory_gb"],
                         winner["effective_min_free_memory_gb"])
        loser = next(state for kind, state in outcomes if kind == "conflict")
        self.assertEqual(loser["revision"], final["revision"],
                         "the loser must see the winner's fresh state, not the stale one")


if __name__ == "__main__":
    unittest.main()
