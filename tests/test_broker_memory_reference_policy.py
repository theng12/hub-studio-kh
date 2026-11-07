"""The broker's reference-policy admission and dispatch (frozen r5 / Gateway ACK 2026-11-07).

A reference item is admitted against the **selected producer node's** own
``hub_memory_admission`` snapshot: the actual count selects that node's exact-count row, or its
generic row as the resolver's fallback. The engine's intrinsic cap may only lower the Hub ceiling.
A snapshot that is present but invalid, unknown or refusing is loud; it never becomes an allow and
never falls back to the lane contract or to this controller's own memory. A row without the
annotation is the older producer and keeps the existing lane-contract path exactly.

The dispatch field is the ACK's exact internal shape and is carried as JSON in the image
multipart form. A caller-supplied value is never authority.
"""
from __future__ import annotations

import asyncio
import base64
import json
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from components.hub.backend import broker

MODEL = "AITRADER/FLUX2-klein-4B-mlx-4bit"
REVISION = "a" * 64
LOCAL_ID = "local-machine"
_ABSENT = object()
# The core's frozen r5 scopes: a mode token covers the model-wide groups plus its tier and rows.
MODEL_COVERS = ["node_id", "model", "mode", "ram_effective", "ram_sources",
                "override_scope", "model_wide_fallback", "published_ram"]
MODE_COVERS = MODEL_COVERS + ["physical_tier", "reference_rows"]


def reference(payload: bytes = b"x") -> dict:
    return {"b64": base64.b64encode(payload).decode(), "mime": "image/png"}


def row(count=None, *, allowed=True, maximum=4, swap=0.0, intrinsic=4, **extra) -> dict:
    return {
        "reference_count": count,
        "effective": {"allowed": allowed, "max_reference_images": maximum,
                      "swap_allowance_gb": swap},
        "sources": {"allowed": "published", "max_reference_images": "published",
                    "swap_allowance_gb": "published"},
        "unknown_reasons": {"allowed": None, "max_reference_images": None,
                            "swap_allowance_gb": None},
        "published": None,
        "override": None,
        "max_intrinsic": intrinsic,
        "clamped_reason": None,
        "overridden": False,
        "stale_tier_ignored": False,
        **extra,
    }


def snapshot(*, node_id: str = "node-a", model: str = MODEL, mode: str = "edit",
             rows: list | None = None, available: bool = True, reason: str | None = None,
             tier: str = "8", tier_gb: int | None = 8, intrinsic: int | None = 4,
             revision: str = REVISION) -> dict:
    view = {
        "available": available,
        "physical_tier": tier,
        "physical_tier_gb": tier_gb,
        "total_memory_bytes": 8 * 1024 ** 3,
        "mode": mode,
        "intrinsic_max": intrinsic,
        "intrinsic_unknown_reason": None,
        "rows": rows if rows is not None else [row(None)],
        "revision": revision,
        "revision_covers": list(MODE_COVERS),
    }
    if reason is not None:
        view["reason"] = reason
    return {
        "revision": "b" * 64,
        "revision_covers": list(MODEL_COVERS),
        "node_id": node_id,
        "model": model,
        "ram": {
            "model_wide": {"revision": "b" * 64,
                           "revision_covers": list(MODEL_COVERS)},
            "modes": ({mode: {"model": model, "mode": mode, "revision": revision,
                              "revision_covers": list(MODE_COVERS)}}
                      if mode else {}),
        },
        "reference": {mode: view} if mode else {},
    }


def image_row(*, snap=_ABSENT, capabilities=("txt2img", "edit"), references=True,
              repo: str = MODEL) -> dict:
    entry = {
        "repo": repo,
        "modality": "image",
        "capabilities": list(capabilities),
        "min_unified_memory_gb": 16.0,
        "min_total_memory_gb": 16.0,
        "min_free_memory_gb": 2.8,
    }
    if references:
        entry["lane_contract"] = {
            "schema": "studiohub.lane-contract.v1",
            "modality": "image",
            "operation": "image.text_to_image",
            "references": {
                "field": "images", "min": 1, "max": 4, "max_dimension": 1024,
                "media_types": ["image/png", "image/jpeg", "image/webp"],
                "counts_against_prompt": False,
                "min_total_memory_gb": 24.0, "min_free_memory_gb": 14.5,
            },
        }
    if snap is not _ABSENT:
        entry["hub_memory_admission"] = snap
    return entry


def studio(machine: str = "node-a") -> dict:
    return {"id": f"image@{machine}", "machine": machine, "modality": "image",
            "host": "10.0.0.9", "port": 51201, "local": False}


class RecordingClient:
    """Records the real POST and then stops the dispatch, as a transport error would."""

    def __init__(self, stop: bool = False):
        self.posts: list[tuple[str, dict]] = []
        self.stop = stop

    async def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        if self.stop:
            raise httpx.HTTPError("recorded before transport")
        return mock.Mock(status_code=200)


def dispatch(entry, refs, *, studio_row=None, policy_out=None,
             modes=("img2img", "edit")):
    client = RecordingClient()
    studio_row = studio_row or studio()
    batch = {"id": "b", "modality": "image", "model": MODEL}
    with mock.patch.object(broker, "studio_request",
                           lambda row, path: (f"http://{row['machine']}{path}", {"h": "1"})):
        return asyncio.run(broker._reference_dispatch(
            client, studio_row, batch, entry, refs, None, modes, policy_out=policy_out))


class ReferenceDispatchPolicyTests(unittest.TestCase):
    def test_an_observed_absent_annotation_keeps_the_lane_contract_path(self):
        class Monitor:
            def cached_aggregate_catalog(self):
                return {"models": [{"hub_studio": "image@node-a", "repo": MODEL}]}

        policy: dict = {}
        with mock.patch.object(broker, "_monitor", lambda: Monitor()):
            mode, url, _headers, _files = dispatch(
                image_row(), [reference()], policy_out=policy)
        self.assertEqual(mode, "edit")
        self.assertTrue(url.endswith("/api/generate/edit"))
        self.assertEqual(policy, {}, "no producer snapshot means no internal policy field")

    def test_a_raw_entry_without_annotation_uses_the_aggregate_annotation(self):
        class Monitor:
            def cached_aggregate_catalog(self):
                return {"models": [{
                    "hub_studio": "image@node-a", "repo": MODEL,
                    "hub_memory_admission": snapshot(rows=[row(None, maximum=2)]),
                }]}

        policy: dict = {}
        with mock.patch.object(broker, "_monitor", lambda: Monitor()):
            dispatch(image_row(), [reference()], policy_out=policy)
        self.assertEqual(policy["reference_policy"]["effective"]["max_reference_images"], 2)

    def test_an_unreadable_aggregate_snapshot_refuses_instead_of_allowing_legacy(self):
        class Monitor:
            def cached_aggregate_catalog(self):
                raise RuntimeError("cache unavailable")

        with mock.patch.object(broker, "_monitor", lambda: Monitor()):
            with self.assertRaisesRegex(ValueError,
                                        "memory-admission snapshot is unreadable"):
                dispatch(image_row(), [reference(b"a"), reference(b"b"), reference(b"c")],
                         policy_out={})

    def test_the_exact_count_row_is_selected_over_the_generic_row(self):
        snap = snapshot(rows=[row(None, maximum=4), row(2, maximum=2)])
        policy: dict = {}
        dispatch(image_row(snap=snap), [reference(b"a"), reference(b"b")], policy_out=policy)
        field = policy["reference_policy"]
        self.assertEqual(set(field), {"model", "mode", "node_id", "physical_tier",
                                      "reference_count", "revision", "effective"})
        self.assertEqual(field["reference_count"], 2)
        self.assertEqual(field["effective"], {"allowed": True, "max_reference_images": 2,
                                              "swap_allowance_gb": 0.0})
        self.assertEqual(field["node_id"], "node-a")
        self.assertEqual(field["model"], MODEL)
        self.assertEqual(field["mode"], "edit")
        self.assertEqual(field["physical_tier"], "8")
        self.assertEqual(field["revision"], REVISION)

    def test_the_generic_row_is_the_resolver_fallback_and_reports_a_null_count(self):
        snap = snapshot(rows=[row(None, maximum=3)])
        policy: dict = {}
        dispatch(image_row(snap=snap), [reference(b"a"), reference(b"b")], policy_out=policy)
        field = policy["reference_policy"]
        self.assertIsNone(field["reference_count"])
        self.assertEqual(field["effective"]["max_reference_images"], 3)

    def test_a_present_null_snapshot_is_refused_not_treated_as_absent(self):
        with self.assertRaisesRegex(ValueError, "invalid memory-admission snapshot"):
            dispatch(image_row(snap=None), [reference()], policy_out={})

    def test_an_unknown_allowed_value_is_refused_never_coerced(self):
        snap = snapshot(rows=[row(None, allowed=None, maximum=4)])
        with self.assertRaisesRegex(ValueError, "does not state whether references are allowed"):
            dispatch(image_row(snap=snap), [reference()], policy_out={})

    def test_an_unknown_cap_is_refused_never_treated_as_unconstrained(self):
        snap = snapshot(rows=[row(None, maximum=None)])
        with self.assertRaisesRegex(ValueError, "does not state a reference-image cap"):
            dispatch(image_row(snap=snap), [reference()], policy_out={})

    def test_an_exact_row_is_selected_even_when_it_refuses_and_the_generic_allows(self):
        snap = snapshot(rows=[row(None, maximum=4), row(2, allowed=False, maximum=0)])
        with self.assertRaisesRegex(ValueError, "refuses reference images"):
            dispatch(image_row(snap=snap), [reference(b"a"), reference(b"b")], policy_out={})

    def test_allowed_false_is_a_loud_refusal(self):
        snap = snapshot(rows=[row(None, allowed=False, maximum=0)])
        with self.assertRaisesRegex(ValueError, "refuses reference images"):
            dispatch(image_row(snap=snap), [reference()], policy_out={})

    def test_a_zero_cap_survives_as_zero_and_refuses_the_count(self):
        snap = snapshot(rows=[row(None, allowed=True, maximum=0)])
        with self.assertRaisesRegex(ValueError, "at most 0"):
            dispatch(image_row(snap=snap), [reference()], policy_out={})

    def test_a_null_swap_allowance_is_preserved_in_the_dispatch_field(self):
        snap = snapshot(rows=[row(None, maximum=2, swap=None)])
        policy: dict = {}
        dispatch(image_row(snap=snap), [reference()], policy_out=policy)
        self.assertIsNone(policy["reference_policy"]["effective"]["swap_allowance_gb"])
        self.assertIn("swap_allowance_gb", policy["reference_policy"]["effective"])

    def test_a_snapshot_for_another_node_is_refused(self):
        snap = snapshot(node_id="node-b")
        with self.assertRaisesRegex(ValueError, "not the selected destination 'node-a'"):
            dispatch(image_row(snap=snap), [reference()], policy_out={})

    def test_a_snapshot_without_the_selected_mode_is_refused(self):
        snap = snapshot(mode="txt2img")
        with self.assertRaisesRegex(ValueError, "no reference policy for mode 'edit'"):
            dispatch(image_row(snap=snap), [reference()], policy_out={})

    def test_a_snapshot_for_another_model_is_refused(self):
        snap = snapshot(model="other/model")
        with self.assertRaisesRegex(ValueError, "names model 'other/model'"):
            dispatch(image_row(snap=snap), [reference()], policy_out={})

    def test_a_snapshot_naming_a_row_alias_instead_of_the_selected_model_is_refused(self):
        entry = image_row(snap=snapshot(model="some/package-id"))
        entry["model_id"] = "some/package-id"
        with self.assertRaisesRegex(ValueError, "names model 'some/package-id'"):
            dispatch(entry, [reference()], policy_out={})

    def test_a_snapshot_missing_its_mode_coverage_is_refused(self):
        snap = snapshot()
        del snap["reference"]["edit"]["revision_covers"]
        with self.assertRaisesRegex(ValueError, "does not cover the exact mode scope"):
            dispatch(image_row(snap=snap), [reference()], policy_out={})

    def test_an_empty_mode_coverage_is_refused(self):
        snap = snapshot()
        snap["reference"]["edit"]["revision_covers"] = []
        with self.assertRaisesRegex(ValueError, "does not cover the exact mode scope"):
            dispatch(image_row(snap=snap), [reference()], policy_out={})

    def test_model_wide_coverage_is_not_accepted_as_mode_coverage(self):
        snap = snapshot()
        snap["reference"]["edit"]["revision_covers"] = list(MODEL_COVERS)
        with self.assertRaisesRegex(ValueError, "does not cover the exact mode scope"):
            dispatch(image_row(snap=snap), [reference()], policy_out={})

    def test_a_borrowed_revision_without_its_same_mode_ram_snapshot_is_refused(self):
        snap = snapshot()
        snap["ram"]["modes"].pop("edit")
        with self.assertRaisesRegex(
                ValueError, "do not match the producer's own 'edit' RAM snapshot"):
            dispatch(image_row(snap=snap), [reference()], policy_out={})

    def test_a_reference_revision_that_differs_from_its_ram_snapshot_is_refused(self):
        snap = snapshot()
        snap["ram"]["modes"]["edit"]["revision"] = "c" * 64
        with self.assertRaisesRegex(
                ValueError, "do not match the producer's own 'edit' RAM snapshot"):
            dispatch(image_row(snap=snap), [reference()], policy_out={})

    def test_a_tier_that_disagrees_with_its_band_is_refused(self):
        snap = snapshot(tier="8", tier_gb=16)
        with self.assertRaisesRegex(ValueError, "disagrees with its band"):
            dispatch(image_row(snap=snap), [reference()], policy_out={})

    def test_the_intrinsic_cap_only_lowers_the_ceiling(self):
        snap = snapshot(rows=[row(None, maximum=4, intrinsic=2)], intrinsic=2)
        policy: dict = {}
        dispatch(image_row(snap=snap), [reference(b"a"), reference(b"b")], policy_out=policy)
        self.assertEqual(policy["reference_policy"]["effective"]["max_reference_images"], 2)
        with self.assertRaisesRegex(ValueError, "at most 2"):
            dispatch(image_row(snap=snap),
                     [reference(b"a"), reference(b"b"), reference(b"c")], policy_out={})

    def test_the_intrinsic_cap_never_raises_a_lower_hub_ceiling(self):
        snap = snapshot(rows=[row(None, maximum=1, intrinsic=4)], intrinsic=4)
        policy: dict = {}
        dispatch(image_row(snap=snap), [reference()], policy_out=policy)
        self.assertEqual(policy["reference_policy"]["effective"]["max_reference_images"], 1)

    def test_an_unavailable_policy_is_refused_with_the_producer_reason(self):
        snap = snapshot(available=False, reason="off on this tier")
        with self.assertRaisesRegex(ValueError, "unavailable: off on this tier"):
            dispatch(image_row(snap=snap), [reference()], policy_out={})

    def test_the_selected_nodes_snapshot_wins_over_this_controllers_rows(self):
        class Monitor:
            def cached_aggregate_catalog(self):
                return {"models": [{
                    "hub_studio": "image@node-a", "repo": MODEL,
                    "hub_memory_admission": snapshot(node_id=LOCAL_ID, rows=[row(None, maximum=99)]),
                }]}

        snap = snapshot(node_id="node-a", rows=[row(None, maximum=2)])
        policy: dict = {}
        with mock.patch.object(broker, "_monitor", lambda: Monitor()):
            dispatch(image_row(snap=snap), [reference()], policy_out=policy)
        self.assertEqual(policy["reference_policy"]["node_id"], "node-a")
        self.assertEqual(policy["reference_policy"]["effective"]["max_reference_images"], 2)


class SubmitDoorPolicyTests(unittest.TestCase):
    class Monitor:
        def __init__(self, entries):
            self.entries = entries

        def cached_catalog_entries(self, model, modality=None):
            return self.entries

        def cached_aggregate_catalog(self):
            return {"models": []}

    def door(self, entries, counts=None):
        with mock.patch.object(broker, "_monitor", lambda: self.Monitor(entries)):
            return broker._image_reference_door_refusal(MODEL, counts)

    def test_a_count_above_the_producers_cap_is_refused_at_the_door(self):
        entries = [{"studio": studio(),
                    "entry": image_row(snap=snapshot(rows=[row(None, maximum=2)]))}]
        error = self.door(entries, [3])
        self.assertIn("3 reference image(s)", error)
        self.assertIn("at most 2", error)

    def test_an_annotation_absent_row_leaves_the_older_path_unchanged(self):
        self.assertIsNone(self.door([{"studio": studio(), "entry": image_row()}], [3]))
        self.assertIsNone(self.door([{"studio": studio(), "entry": image_row()}]))

    def test_an_unreadable_aggregate_snapshot_is_loud_at_the_door(self):
        entries = [{"studio": studio(), "entry": image_row()}]

        class Monitor:
            def cached_catalog_entries(self, model, modality=None):
                return entries

            def cached_aggregate_catalog(self):
                raise RuntimeError("cache unavailable")

        with mock.patch.object(broker, "_monitor", lambda: Monitor()):
            error = broker._image_reference_door_refusal(MODEL, [3])
        self.assertIn("3 reference image(s)", error)
        self.assertIn("unreadable", error)

    def test_an_unknown_policy_is_loud_at_the_door(self):
        entries = [{"studio": studio(),
                    "entry": image_row(snap=snapshot(rows=[row(None, allowed=None)]))}]
        error = self.door(entries, [1])
        self.assertIn("does not state whether references are allowed", error)

    def test_one_producer_that_states_the_count_is_enough(self):
        entries = [
            {"studio": studio("node-a"),
             "entry": image_row(snap=snapshot(node_id="node-a", rows=[row(None, maximum=2)]))},
            {"studio": studio("node-b"),
             "entry": image_row(snap=snapshot(node_id="node-b", rows=[row(None, maximum=4)]))},
        ]
        self.assertIsNone(self.door(entries, [3]))

    def test_the_old_call_without_counts_keeps_its_answer(self):
        entries = [{"studio": studio(),
                    "entry": image_row(snap=snapshot(rows=[row(None, maximum=0)]))}]
        self.assertIsNone(self.door(entries))


class MultipartAndDispatchCallSiteTests(unittest.TestCase):
    def test_multipart_carries_the_internal_policy_as_json(self):
        policy = snapshot(rows=[row(None, maximum=2)])
        field = {"model": MODEL, "mode": "edit", "node_id": "node-a",
                 "physical_tier": "8", "reference_count": None, "revision": REVISION,
                 "effective": {"allowed": True, "max_reference_images": 2,
                               "swap_allowance_gb": 0.0}}
        fields = broker._multipart_fields({"repo": MODEL, "reference_policy": field})
        self.assertEqual(json.loads(fields["reference_policy"]), field)
        self.assertNotIn("'", fields["reference_policy"][:2], "never a Python repr")

    def _run_item(self, entry, params, monkeypatch_floor=None):
        item = {
            "index": 0, "prompt": "proof", "params": params, "seed": -1,
            "state": "running", "tries": 1, "studio": "image@node-a",
            "studio_job_id": None, "retry_at": None, "error": None,
            "client_request_id": None,
        }
        batch = {"id": "b", "modality": "image", "model": MODEL, "shared_params": {},
                 "cancelled": False, "routing": "pool", "created_at": time.time(),
                 "items": [item]}

        async def catalog_entry(_studio, _model):
            return entry

        class Monitor:
            registry: list = []

            def cached_aggregate_catalog(self):
                return {"models": []}

        client = RecordingClient(stop=True)
        patches = [
            mock.patch.object(broker, "_catalog_entry", catalog_entry),
            mock.patch.object(broker, "_monitor", lambda: Monitor()),
            mock.patch.object(broker, "studio_request",
                              lambda row, path: (f"http://{row['machine']}{path}", {"h": "1"})),
        ]
        if monkeypatch_floor is not None:
            patches.append(mock.patch.object(
                broker.memory_admission, "worker_free_floor_gb", monkeypatch_floor))
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        asyncio.run(broker._run_item(client, batch, item, studio()))
        return client, item

    def test_the_real_dispatch_posts_the_snapshot_policy_and_drops_a_caller_value(self):
        calls: list[tuple] = []

        def floor(model, mode=None):
            calls.append((model, mode))
            return None

        snap = snapshot(rows=[row(None, maximum=2)])
        entry = image_row(snap=snap)
        attacker = {"model": MODEL, "mode": "edit", "node_id": "attacker",
                    "physical_tier": "8", "reference_count": None,
                    "revision": "c" * 64,
                    "effective": {"allowed": True, "max_reference_images": 99,
                                  "swap_allowance_gb": 9.0}}
        client, item = self._run_item(
            entry, {"reference_images": [reference()], "reference_policy": attacker}, floor)
        self.assertEqual(len(client.posts), 1)
        url, kwargs = client.posts[0]
        self.assertTrue(url.endswith("/api/generate/edit"), url)
        policy = json.loads(kwargs["data"]["reference_policy"])
        self.assertEqual(policy["node_id"], "node-a")
        self.assertEqual(policy["model"], MODEL)
        self.assertEqual(policy["effective"]["max_reference_images"], 2)
        self.assertNotEqual(policy["node_id"], "attacker")
        self.assertEqual(calls, [(MODEL, "edit")], "the free floor is forwarded mode-aware")
        self.assertEqual(item["state"], "error")

    def test_without_a_snapshot_a_caller_policy_never_reaches_the_worker(self):
        attacker = {"node_id": "attacker", "effective": {"allowed": True,
                                                          "max_reference_images": 99}}
        client, _item = self._run_item(
            image_row(), {"reference_images": [reference()],
                          "reference_policy": attacker})
        self.assertEqual(len(client.posts), 1)
        _url, kwargs = client.posts[0]
        self.assertNotIn("reference_policy", kwargs["data"])


if __name__ == "__main__":
    unittest.main()
