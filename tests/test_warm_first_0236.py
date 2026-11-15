#!/usr/bin/env python3
"""Warm-first scan order and load-telemetry pass-through (0.6.236).

Two bounded contracts, pinned with pure helper calls and fakes — no live Hub, no network:

* A finished job's `model_reused`/`load_s` reach the item and the ledger row only when the
  job states them: absent stays absent, false stays false, 0.0 stays 0.0, unknown stays NULL.
* Warm-first changes only scan order inside one sort group, only on fresh resident evidence,
  and never past the 30 s FIFO age-out. Empty/unknown/stale/busy evidence, the env off-switch
  and the one-batch/idle path all restore today's order — and fetch nothing.
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from components.hub.backend import broker, ledger  # noqa: E402


NOW = 1_000_000.0


def row(batch_id, model, *, modality="image", created_at=0.0, group=1):
    """One `(sort_group, batch)` row in the shape `_queued_batches()` produces."""
    return (group, {"id": batch_id, "model": model, "modality": modality,
                    "created_at": created_at})


def ready_studio(machine="m1", modality="image", models=(["model-a"],),
                 *, busy=False, due=None, state="ready"):
    return {"state": state, "machine": machine, "modality": modality,
            "policy": {"busy": busy, "loaded_models": list(models),
                       "next_release_at": due}}


def ids(rows):
    return [batch.get("id") for _group, batch in rows]


def fake_control(payload=None, error=None, delay=0.0):
    """A FleetMemoryControl stand-in: records inventory() calls, never touches a socket."""
    calls = []

    class _Fake:
        def __init__(self, monitor, client):
            pass

        async def inventory(self):
            calls.append(1)
            if delay:
                await asyncio.sleep(delay)
            if error is not None:
                raise error
            return payload

    return _Fake, calls


class WarmEvidenceTests(unittest.TestCase):
    """What counts as fresh resident evidence, and what never does."""

    def test_unknown_inventory_shape_yields_no_evidence(self):
        for payload in (None, [], "x", {}, {"studios": None}, {"studios": "all"},
                        {"studios": [None, 42, []]}):
            with self.subTest(payload=payload):
                self.assertEqual(
                    broker._warm_residents_from_inventory(payload, now=NOW), {})

    def test_not_ready_busy_stale_or_empty_rows_yield_no_evidence(self):
        cases = [
            ready_studio(state="down"),
            ready_studio(busy=True),
            ready_studio(due=NOW - 1),          # due for release: stale, not warm
            ready_studio(due="soon"),           # unknown stamp: never guessed
            ready_studio(models=()),            # nothing resident
            ready_studio(models=([""], [None])),  # unparseable entries only
        ]
        for studio in cases:
            with self.subTest(studio=studio):
                self.assertEqual(
                    broker._warm_residents_from_inventory(
                        {"studios": [studio]}, now=NOW), {})

    def test_ready_idle_rows_yield_residents_in_both_entry_shapes(self):
        inventory = {"studios": [
            ready_studio(models=(["model-a"], "model-b", ["model-c", "variant"])),
            ready_studio(machine="m2", modality="voice", models=(["voice-model"],),
                         due=NOW + 5),          # future release is still warm
        ]}
        self.assertEqual(
            broker._warm_residents_from_inventory(inventory, now=NOW),
            {("m1", "image"): {"model-a", "model-b", "model-c"},
             ("m2", "voice"): {"voice-model"}})


class WarmOrderTests(unittest.TestCase):
    """`_warm_first_order`: group first, then warm/aged rank, then the original FIFO keys."""

    def test_empty_or_unknown_evidence_keeps_fifo(self):
        rows = [row("a", "model-x", created_at=NOW - 3),
                row("b", "model-a", created_at=NOW - 1)]
        self.assertEqual(broker._warm_first_order(rows, {}, NOW), rows)
        stale = broker._warm_residents_from_inventory(
            {"studios": [ready_studio(busy=True, due=NOW - 1)]}, now=NOW)
        self.assertEqual(stale, {})
        self.assertEqual(broker._warm_first_order(rows, stale, NOW), rows)

    def test_warm_reorders_only_inside_its_own_sort_group(self):
        rows = [
            row("render-a", "model-x", modality="render", created_at=NOW - 9, group=0),
            row("render-b", "model-x", modality="render", created_at=NOW - 8, group=0),
            row("cold-c", "model-x", created_at=NOW - 5, group=1),
            row("warm-d", "model-a", created_at=NOW - 1, group=1),
        ]
        residents = broker._warm_residents_from_inventory(
            {"studios": [ready_studio()]}, now=NOW)
        ordered = broker._warm_first_order(rows, residents, NOW)
        self.assertEqual(ids(ordered),
                         ["render-a", "render-b", "warm-d", "cold-c"])
        groups = [group for group, _batch in ordered]
        self.assertEqual(groups, sorted(groups))  # never across render/other

    def test_cold_batch_older_than_30s_is_never_overtaken(self):
        rows = [row("old-cold", "model-x", created_at=NOW - 40),
                row("new-warm", "model-a", created_at=NOW - 10)]
        residents = broker._warm_residents_from_inventory(
            {"studios": [ready_studio()]}, now=NOW)
        self.assertEqual(ids(broker._warm_first_order(rows, residents, NOW)),
                         ["old-cold", "new-warm"])

    def test_a_warm_stream_cannot_starve_a_cold_batch_past_30s(self):
        residents = broker._warm_residents_from_inventory(
            {"studios": [ready_studio()]}, now=NOW)
        cold_created = NOW - 29
        rows = [row("cold", "model-x", created_at=cold_created),
                row("warm-1", "model-a", created_at=NOW - 2),
                row("warm-2", "model-a", created_at=NOW - 1)]
        # Inside the 30 s window the stream may pass; aging is what bounds the wait.
        demoted = broker._warm_first_order(rows, residents, NOW)
        self.assertEqual(ids(demoted), ["warm-1", "warm-2", "cold"])
        # One second past the window the cold batch is rank 0 and oldest: first again.
        aged = broker._warm_first_order(rows, residents, NOW + 2)
        self.assertEqual(ids(aged), ["cold", "warm-1", "warm-2"])

    def test_exact_model_mismatch_is_not_warm(self):
        residents = broker._warm_residents_from_inventory(
            {"studios": [ready_studio()]}, now=NOW)
        rows = [row("c", "model-a-2", created_at=NOW - 5),
                row("d", "Model-A", created_at=NOW - 1)]
        self.assertFalse(broker._batch_is_warm(rows[0][1], residents))
        self.assertFalse(broker._batch_is_warm(rows[1][1], residents))
        self.assertEqual(broker._warm_first_order(rows, residents, NOW), rows)


class WarmFetchTests(unittest.IsolatedAsyncioTestCase):
    """On-demand fetch: only with >= 2 batches, env on, bounded and cached."""

    def setUp(self):
        broker._WARM_EVIDENCE_CACHE.update(at=0.0, residents=None)
        broker._WARM_LAST_LOGGED_BATCH = None

    async def test_env_disable_restores_fifo_without_touching_anything(self):
        rows = [row("a", "model-x", created_at=time.time() - 5),
                row("b", "model-a", created_at=time.time() - 1)]
        control, calls = fake_control(
            payload={"studios": [ready_studio(due=None)]})
        with mock.patch.dict(os.environ, {"STUDIOHUB_WARM_FIRST": "0"}), \
                mock.patch.object(broker, "FleetMemoryControl", control), \
                mock.patch.object(broker, "_monitor",
                                  side_effect=AssertionError("must not fetch")):
            ordered = await broker._warm_first_queued(rows, object())
        self.assertEqual(ids(ordered), ["a", "b"])
        self.assertEqual(calls, [])

    async def test_unless_disabled_the_switch_defaults_to_on(self):
        env = {k: v for k, v in os.environ.items()
               if k != "STUDIOHUB_WARM_FIRST"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertTrue(broker._warm_first_enabled())
        with mock.patch.dict(os.environ, {"STUDIOHUB_WARM_FIRST": "0"}):
            self.assertFalse(broker._warm_first_enabled())

    async def test_one_queued_batch_never_fetches_evidence(self):
        rows = [row("only", "model-a", created_at=time.time() - 1)]
        with mock.patch.object(broker, "_monitor",
                               side_effect=AssertionError("must not fetch")):
            ordered = await broker._warm_first_queued(rows, object())
        self.assertEqual(ids(ordered), ["only"])

    async def test_one_model_and_modality_never_fetches_evidence(self):
        # Same model on the same modality cannot reorder, so the 1 s deadline is not spent.
        now = time.time()
        rows = [row("a", "model-a", created_at=now - 5),
                row("b", "model-a", created_at=now - 1)]
        with mock.patch.object(broker, "_monitor",
                               side_effect=AssertionError("must not fetch")):
            ordered = await broker._warm_first_queued(rows, object())
        self.assertEqual(ids(ordered), ["a", "b"])

    async def test_evidence_is_fetched_once_per_three_seconds(self):
        now = time.time()
        rows = [row("cold", "model-x", created_at=now - 5),
                row("warm", "model-a", created_at=now - 1)]
        control, calls = fake_control(payload={"studios": [ready_studio(due=None)]})
        with mock.patch.dict(os.environ, {"STUDIOHUB_WARM_FIRST": "1"}), \
                mock.patch.object(broker, "FleetMemoryControl", control), \
                mock.patch.object(broker, "_monitor", return_value=object()):
            first = await broker._warm_first_queued(rows, object())
            second = await broker._warm_first_queued(rows, object())
        self.assertEqual(calls, [1])
        self.assertEqual(ids(first), ["warm", "cold"])
        self.assertEqual(ids(second), ["warm", "cold"])

    async def test_timeout_and_error_yield_no_evidence_and_are_cached(self):
        slow, slow_calls = fake_control(delay=broker.WARM_EVIDENCE_DEADLINE_S + 0.3)
        with mock.patch.object(broker, "FleetMemoryControl", slow), \
                mock.patch.object(broker, "_monitor", return_value=object()):
            self.assertEqual(await broker._fetch_warm_residents(object()), {})
            self.assertEqual(await broker._fetch_warm_residents(object()), {})
        self.assertEqual(slow_calls, [1])  # the failure is cached for the same window

        broken, broken_calls = fake_control(error=RuntimeError("studio down"))
        broker._WARM_EVIDENCE_CACHE.update(at=0.0, residents=None)
        with mock.patch.object(broker, "FleetMemoryControl", broken), \
                mock.patch.object(broker, "_monitor", return_value=object()):
            self.assertEqual(await broker._fetch_warm_residents(object()), {})
            self.assertEqual(await broker._fetch_warm_residents(object()), {})
        self.assertEqual(broken_calls, [1])

    async def test_one_dispatch_line_names_the_warm_batch_and_machine(self):
        now = time.time()
        rows = [row("cold", "model-x", created_at=now - 5),
                row("warm", "model-a", created_at=now - 1)]
        control, _calls = fake_control(payload={"studios": [ready_studio(due=None)]})
        with mock.patch.dict(os.environ, {"STUDIOHUB_WARM_FIRST": "1"}), \
                mock.patch.object(broker, "FleetMemoryControl", control), \
                mock.patch.object(broker, "_monitor", return_value=object()):
            with self.assertLogs("studiohub.broker", level="INFO") as captured:
                first = await broker._warm_first_queued(rows, object())
                second = await broker._warm_first_queued(rows, object())
        self.assertEqual(ids(first), ["warm", "cold"])
        self.assertEqual(ids(second), ["warm", "cold"])
        lines = [line for line in captured.output
                 if "warm-first scan" in line]
        # One warm choice produces one line, even across repeated passes.
        self.assertEqual(len(lines), 1)
        self.assertIn("batch=warm", lines[0])
        self.assertIn("model=model-a", lines[0])
        self.assertIn("machine=m1", lines[0])

    async def test_unchanged_order_emits_nothing(self):
        now = time.time()
        rows = [row("a", "model-x", created_at=now - 5),
                row("b", "model-a-2", created_at=now - 1)]
        control, _calls = fake_control(payload={"studios": [ready_studio(due=None)]})
        with mock.patch.dict(os.environ, {"STUDIOHUB_WARM_FIRST": "1"}), \
                mock.patch.object(broker, "FleetMemoryControl", control), \
                mock.patch.object(broker, "_monitor", return_value=object()):
            with self.assertNoLogs("studiohub.broker", level="INFO"):
                ordered = await broker._warm_first_queued(rows, object())
        self.assertEqual(ids(ordered), ["a", "b"])


class LoadTelemetryPassThroughTests(unittest.TestCase):
    """The item mapping copies only what the job states — never derives or backfills."""

    def test_absent_stays_absent(self):
        item = {}
        broker._copy_load_telemetry(item, {})
        self.assertNotIn("model_reused", item)
        self.assertNotIn("load_s", item)

    def test_false_and_zero_are_copied_distinctly_from_absent(self):
        item = {}
        broker._copy_load_telemetry(item, {"model_reused": False, "load_s": 0.0})
        self.assertIs(item["model_reused"], False)
        self.assertEqual(item["load_s"], 0.0)

    def test_true_positive_and_explicit_null_are_copied(self):
        item = {}
        broker._copy_load_telemetry(
            item, {"model_reused": True, "load_s": 1.5})
        self.assertIs(item["model_reused"], True)
        self.assertEqual(item["load_s"], 1.5)
        null_item = {}
        broker._copy_load_telemetry(null_item, {"load_s": None})
        self.assertIn("load_s", null_item)
        self.assertIsNone(null_item["load_s"])


class LedgerTelemetryRoundTripTests(unittest.TestCase):
    """The additive columns keep NULL for unknown and preserve false / 0.0."""

    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="ledger-telemetry-0236-")
        self._old_db = ledger.DB_FILE
        ledger.DB_FILE = Path(self.temporary.name) / "hub.db"

    def tearDown(self):
        ledger.DB_FILE = self._old_db
        self.temporary.cleanup()

    def test_unknown_rows_read_back_null_and_false_zero_survive(self):
        unknown_id = ledger.record_asset(source="job", modality="image",
                                         artifact_path=None)
        unknown = ledger.get_asset(unknown_id)
        self.assertIsNone(unknown["model_reused"])
        self.assertIsNone(unknown["load_s"])

        false_id = ledger.record_asset(source="job", modality="image",
                                       model_reused=False, load_s=0.0)
        false_row = ledger.get_asset(false_id)
        self.assertFalse(false_row["model_reused"])  # 0, not NULL
        self.assertEqual(false_row["load_s"], 0.0)    # 0.0, not NULL

        true_id = ledger.record_asset(source="job", modality="image",
                                      model_reused=True, load_s=1.5)
        true_row = ledger.get_asset(true_id)
        self.assertTrue(true_row["model_reused"])
        self.assertEqual(true_row["load_s"], 1.5)

    def test_listing_path_carries_the_columns(self):
        ledger.record_asset(source="job", modality="image", batch_id="b-list",
                            model_reused=False, load_s=0.0)
        rows = ledger.query_assets(batch_id="b-list")
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["model_reused"])
        self.assertEqual(rows[0]["load_s"], 0.0)

    def test_old_rows_gain_nullable_columns_without_being_rewritten(self):
        # The real pre-0.6.236 assets shape (every column except the two new ones):
        # `_conn()` must ALTER the two in and leave the stored row untouched
        # (NULL unknown), never rebuild or rewrite it.
        conn = sqlite3.connect(ledger.DB_FILE)
        conn.execute(
            "CREATE TABLE assets ("
            " id TEXT PRIMARY KEY,"
            " created_at REAL NOT NULL,"
            " source TEXT NOT NULL,"
            " modality TEXT,"
            " studio TEXT,"
            " machine TEXT,"
            " model TEXT,"
            " seed INTEGER,"
            " prompt TEXT,"
            " params_json TEXT,"
            " artifact_path TEXT UNIQUE,"
            " artifact_url TEXT,"
            " batch_id TEXT,"
            " item_index INTEGER,"
            " recipe_id TEXT,"
            " duration_s REAL,"
            " runtime_s REAL)")
        conn.execute("INSERT INTO assets (id, created_at, source) VALUES"
                     " ('old-row', 1.0, 'scan')")
        conn.commit()
        conn.close()

        old = ledger.get_asset("old-row")
        self.assertEqual(old["source"], "scan")
        self.assertIsNone(old["model_reused"])
        self.assertIsNone(old["load_s"])

        new_id = ledger.record_asset(source="job", model_reused=True, load_s=2.0)
        new_row = ledger.get_asset(new_id)
        self.assertTrue(new_row["model_reused"])
        self.assertEqual(new_row["load_s"], 2.0)
        # The old row is still exactly as it was.
        self.assertIsNone(ledger.get_asset("old-row")["model_reused"])


class AttemptEvidenceTests(unittest.TestCase):
    """A retry must not inherit the previous attempt's load telemetry."""

    def test_load_telemetry_is_cleared_with_the_other_attempt_evidence(self):
        self.assertIn("model_reused", broker._ATTEMPT_EVIDENCE_FIELDS)
        self.assertIn("load_s", broker._ATTEMPT_EVIDENCE_FIELDS)
        item = {"model_reused": True, "load_s": 1.5, "runtime_s": 9.0,
                "state": "queued"}
        broker._clear_worker_attempt_evidence(item)
        self.assertNotIn("model_reused", item)
        self.assertNotIn("load_s", item)
        self.assertNotIn("runtime_s", item)


if __name__ == "__main__":
    unittest.main()
