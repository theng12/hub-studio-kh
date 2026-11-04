"""The Hub broker's image reference door: N references become N `images` parts (2026-10-26).

Reference-to-image rides inside `item["params"]["reference_images"]`, never beside `params` (the
2026-09-26 text-to-video regression). For an image edit the broker resolves every reference, in
order, and posts one multipart `images` part per reference to `/api/generate/edit`; the count is
checked against the window the worker published in its lane contract, and a worker that advertises
no `edit` is refused before anything is posted. Video keeps its single `file` part.

The anti-silent-drop proof is the worker's own `reference_count`, carried onto the item and
published in `terminal_result`, so the Gateway can fail an item that attached a different number
of references instead of accepting a text-to-image render as success.
"""
from __future__ import annotations

import asyncio
import base64
from pathlib import Path
import sys
import unittest
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from components.hub.backend import broker, memory_admission, workload_policy

MODEL = "AITRADER/FLUX2-klein-4B-mlx-4bit"


def image_row(*, capabilities=("txt2img", "edit"), references=True, **extra) -> dict:
    """A worker catalog row shaped like the Image worker's own `edit` advertisement."""
    row = {
        "repo": MODEL,
        "modality": "image",
        "capabilities": list(capabilities),
        # The row's own two floors, as the Image worker publishes them.
        "min_unified_memory_gb": 16.0,
        "min_total_memory_gb": 16.0,
        "min_free_memory_gb": 2.8,
    }
    if references:
        row["lane_contract"] = {
            "schema": "studiohub.lane-contract.v1",
            "modality": "image",
            "operation": "image.text_to_image",
            "references": {
                "field": "images", "min": 1, "max": 2, "max_dimension": 1024,
                "media_types": ["image/png", "image/jpeg", "image/webp"],
                "counts_against_prompt": False,
                "min_total_memory_gb": 24.0, "min_free_memory_gb": 14.5,
            },
        }
    row.update(extra)
    return row


def reference(payload: bytes, mime: str = "image/png") -> dict:
    return {"b64": base64.b64encode(payload).decode(), "mime": mime}


class Stub:
    """The only client method these cases reach: nothing here fetches a remote reference."""

    def __init__(self):
        self.posts = []

    async def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return mock.Mock(status_code=200)


def dispatch(row, refs, *, modality="image", ref_mode=None, modes=("img2img", "edit")):
    client = Stub()
    studio = {"id": "image@local", "machine": "local", "modality": modality}
    batch = {"id": "b", "modality": modality, "model": MODEL}
    with mock.patch.object(broker, "studio_request",
                           lambda _studio, path: ("http://worker" + path, {"h": "1"})):
        return asyncio.run(broker._reference_dispatch(
            client, studio, batch, row, refs, ref_mode, modes))


class ImageReferenceDispatchTests(unittest.TestCase):
    def test_two_references_become_two_images_parts_in_order(self):
        mode, url, headers, files = dispatch(
            image_row(), [reference(b"first"), reference(b"second", "image/jpeg")])
        self.assertEqual(mode, "edit")
        self.assertTrue(url.endswith("/api/generate/edit"), url)
        self.assertEqual(headers, {"h": "1"})
        self.assertEqual([name for name, _part in files], ["images", "images"])
        self.assertEqual([part[0] for _name, part in files],
                         ["reference0.png", "reference1.jpg"])
        self.assertEqual([part[1] for _name, part in files], [b"first", b"second"])
        self.assertEqual([part[2] for _name, part in files], ["image/png", "image/jpeg"])

    def test_more_references_than_the_published_max_are_refused_with_the_number(self):
        with self.assertRaisesRegex(ValueError, r"3 reference image\(s\) sent; this model takes "
                                                r"1 to 2"):
            dispatch(image_row(), [reference(b"a"), reference(b"b"), reference(b"c")])

    def test_an_edit_worker_that_publishes_no_count_window_is_refused(self):
        with self.assertRaisesRegex(ValueError, "publishes no reference count window"):
            dispatch(image_row(references=False), [reference(b"a")])

    def test_a_worker_without_the_capability_is_refused_before_any_post(self):
        client = Stub()
        with mock.patch.object(broker, "studio_request") as request:
            with self.assertRaisesRegex(ValueError, "does not support reference images"):
                asyncio.run(broker._reference_dispatch(
                    client, {"id": "image@local", "machine": "local", "modality": "image"},
                    {"id": "b", "modality": "image", "model": MODEL},
                    image_row(capabilities=("txt2img",)), [reference(b"a")], None,
                    ("img2img", "edit")))
        request.assert_not_called()
        self.assertEqual(client.posts, [])

    def test_img2img_keeps_its_single_image_part(self):
        mode, url, _headers, files = dispatch(
            image_row(capabilities=("txt2img", "img2img"), references=False),
            [reference(b"a")])
        self.assertEqual(mode, "img2img")
        self.assertTrue(url.endswith("/api/generate/img2img"), url)
        self.assertEqual([name for name, _part in files], ["image"])

    def test_video_keeps_its_single_file_part(self):
        mode, url, _headers, files = dispatch(
            image_row(capabilities=("img2vid", "img2video"), references=False),
            [reference(b"a")], modality="video", modes=("img2video",))
        self.assertEqual(mode, "img2video")
        self.assertTrue(url.endswith("/api/generate/video2video"), url)
        self.assertEqual([name for name, _part in files], ["file"])


class ItemLaneTests(unittest.TestCase):
    def test_an_item_with_references_is_an_edit_item(self):
        self.assertEqual(broker._item_mode({"params": {"reference_images": [{"b64": "x"}]}}),
                         "edit")
        self.assertEqual(broker._item_mode({"params": {}}), "txt2img")
        batch = {"items": [{"params": {}}, {"params": {"reference_images": [{}]}}]}
        self.assertEqual(broker._batch_mode(batch), "edit")
        self.assertEqual(broker._batch_mode({"items": [{"params": {}}]}), "txt2img")

    def test_the_worker_multipart_fields_carry_the_requested_geometry(self):
        """A 1:1 reference request must not silently become the worker's 16:9 default."""
        fields = broker._multipart_fields({"repo": MODEL, "prompt": "x",
                                           "aspect_ratio": "1:1", "resolution": "1K",
                                           "steps": 4, "seed": 7})
        self.assertEqual(fields["aspect_ratio"], "1:1")
        self.assertEqual(fields["resolution"], "1K")
        self.assertEqual(fields["steps"], "4")

    def test_the_worker_count_reaches_the_gateway_result_and_only_there(self):
        """The Gateway compares what it sent with what was attached, so the Hub must publish it."""
        item = {"state": "done", "index": 0, "bytes": 10, "sha256": "a" * 64,
                "media_type": "image/png"}
        broker._record_worker_identity(item, {"id": "image@local"}, {"reference_count": 2})
        self.assertEqual(item["reference_count"], 2)
        self.assertEqual(broker.terminal_result({"id": "b"}, item)["reference_count"], 2)
        text_item = {"state": "done", "index": 1, "bytes": 10, "sha256": "a" * 64,
                     "media_type": "image/png"}
        self.assertNotIn("reference_count", broker.terminal_result({"id": "b"}, text_item))


class EditMemoryAdmissionTests(unittest.TestCase):
    """An edit item's floors are the reference lane's own, not the model's text-to-image floors."""

    def test_the_reference_lane_sets_the_edit_floors_and_leaves_txt2img_alone(self):
        row = image_row()
        text = memory_admission.describe(MODEL, row)
        edit = memory_admission.describe(MODEL, row, mode="edit")
        # FLUX.2 Klein 4-bit's tier is 8 GB, stated by its own manifest since 2026-10-28 (it used
        # to arrive from the Hub's `_FLEET_DEFAULTS` copy of the same measurement); this row's
        # reference lane (the default configuration, as a large Mac would publish it) asks for 24.
        self.assertEqual((text["effective_min_total_memory_gb"], text["min_total_source"]),
                         (8.0, "manifest"))
        self.assertEqual(text["effective_min_free_memory_gb"], 2.8)
        self.assertEqual((edit["effective_min_total_memory_gb"], edit["min_total_source"]),
                         (24.0, "reference_lane"))
        self.assertEqual(edit["effective_min_free_memory_gb"], 14.5)
        self.assertEqual(edit["min_free_source"], "reference_lane")
        self.assertEqual(broker._admission_requirements(MODEL, row, mode="edit"),
                         {"min_total": 24.0, "min_free": 14.5})
        self.assertEqual(broker._admission_requirements(MODEL, row),
                         {"min_total": 8.0, "min_free": 2.8})

    def test_a_small_macs_row_qualifies_it_for_the_bounded_configuration(self):
        """The floor a Mac publishes is its *own* configuration's, so an 8 GB Mac can serve it."""
        small = image_row()
        small["lane_contract"]["references"].update(
            {"mode": "bounded-streamed", "min_total_memory_gb": 8.0,
             "min_free_memory_gb": 5.0})
        edit = broker._admission_requirements(MODEL, small, mode="edit")
        self.assertEqual(edit, {"min_total": 8.0, "min_free": 5.0})
        eight = {"total_gb": 8.59, "available_gb": 6.0}
        self.assertEqual(broker._memory_gate(edit, eight), ("run", None))

    def test_memory_admission_refuses_a_mac_below_the_edit_floor_and_runs_txt2img(self):
        row = image_row()
        edit = broker._admission_requirements(MODEL, row, mode="edit")
        text = broker._admission_requirements(MODEL, row)
        sixteen = {"total_gb": 16.0, "available_gb": 12.0}
        decision, note = broker._memory_gate(edit, sixteen)
        self.assertEqual(decision, "skip")
        self.assertIn("24", note)
        self.assertEqual(broker._memory_gate(text, sixteen), ("run", None))
        # A Mac that clears the total floor but is short right now waits rather than skips.
        ample_but_busy = {"total_gb": 32.0, "available_gb": 9.0}
        decision, note = broker._memory_gate(edit, ample_but_busy)
        self.assertEqual(decision, "wait")
        self.assertIn("14.5", note)

    def test_the_edit_pool_is_the_macs_that_advertise_it_and_clear_the_floor(self):
        studios = [{"id": f"image@{name}", "modality": "image", "machine": name, "local": False}
                   for name in ("small", "large", "quiet")]
        rows = {
            # Both advertise edit (a worker only does that when its own host clears the floor),
            # but the Hub still applies the measured requirement to the catalog it holds.
            "small": image_row(),
            "large": image_row(),
            "quiet": image_row(capabilities=("txt2img",)),
        }
        asked = {}

        class Monitor:
            registry = studios
            status = {studio["id"]: {"status": "up"} for studio in studios}

            def cached_catalog_entries(self, model, *, modality=None):
                asked["modality"] = modality
                return [{"studio": studio, "entry": {**rows[studio["machine"]],
                                                     "cached": {"state": "cached"}}}
                        for studio in studios]

        memory = {"small": 16.0, "large": 32.0, "quiet": 32.0}
        with mock.patch.object(broker, "_monitor", lambda: Monitor()), \
                mock.patch.object(broker.peers, "dispatch_ready", lambda studio: True), \
                mock.patch.object(broker, "machine_enabled", lambda machine: True), \
                mock.patch.object(broker, "studio_enabled", lambda machine, studio: True), \
                mock.patch.object(broker, "_known_memory_gb",
                                  lambda studio: memory[studio["machine"]]):
            edit_pool = broker.pool_machines("image", MODEL, mode="edit")
            text_pool = broker.pool_machines("image", MODEL)
        self.assertEqual(asked.get("modality"), "image")
        self.assertEqual(edit_pool, {"large"})
        self.assertEqual(text_pool, {"small", "large", "quiet"})

    def test_a_reference_floor_that_is_not_a_number_is_a_loud_row_fault(self):
        row = image_row()
        row["lane_contract"]["references"]["min_total_memory_gb"] = "about 24"
        with self.assertRaisesRegex(ValueError, "lane_contract.references.min_total_memory_gb"):
            workload_policy.required_reference_total_memory_gb(MODEL, row)


class DoorTests(unittest.TestCase):
    """The public image door refuses unknown modes and unserviceable references before queueing.

    Owner rule: an incoming mode the door does not know fails loudly with the two accepted
    modes named in the owner's words, and reference images are refused by name (text-only
    model) or with the producer's own unavailability reason (supported model, host cannot
    serve it) — never silently ignored, never mislabelled. Video/music/voice are untouched.
    """

    class _Monitor:
        def __init__(self, entries):
            self._entries = entries

        def cached_catalog_entries(self, model, *, modality=None):
            return [{"entry": entry} for entry in self._entries
                    if entry.get("repo") == model]

    @staticmethod
    def _envelope(**params):
        return {"modality": "image", "model": MODEL,
                "items": [{"prompt": "a quiet garden", "params": dict(params)}]}

    def _submit(self, envelope, entries=()):
        with mock.patch.object(broker, "_monitor",
                               lambda: DoorTests._Monitor(list(entries))):
            return broker._submit_batch_locked(envelope)

    def test_malformed_image_items_are_refused_before_mode_validation(self):
        for items in ([None], [123], ["text"], "nope", {"prompt": "text"}, []):
            with self.subTest(items=items):
                result = self._submit({**self._envelope(mode="edit"), "items": items})
                self.assertIn("error", result)
                self.assertNotIn("batch_id", result)

    def test_missing_runtime_preserves_producer_reason(self):
        result = self._submit(self._envelope(reference_images=[reference(b"image")]), entries=[{
            "repo": MODEL, "label": "Unregistered image package",
            "image_modes": [{"id": "txt2img", "available": False}],
            "readiness": {"state": "no_runtime_model", "reason": "No compatible image runtime is installed"},
        }])
        self.assertIn("No compatible image runtime is installed", result.get("error", ""))
        self.assertNotIn("does not support", result.get("error", ""))

    def test_malformed_reference_list_cannot_fall_back_to_text(self):
        for references in ({"url": "https://example.invalid/image.png"}, "image.png", 1):
            with self.subTest(references=references):
                result = self._submit(self._envelope(reference_images=references))
                self.assertIn("reference_images must be a list", result.get("error", ""))
                self.assertNotIn("batch_id", result)

    def test_an_unknown_image_mode_is_refused_by_name_before_queueing(self):
        result = self._submit(self._envelope(mode="banana"))
        error = result.get("error", "")
        self.assertIn("unsupported image mode 'banana'", error)
        self.assertIn("Text to image (txt2img)", error)
        self.assertIn("Reference to image (edit)", error)
        self.assertNotIn("batch_id", result)

    def test_ref_mode_and_a_top_level_mode_are_checked_too(self):
        for envelope in (self._envelope(ref_mode="invented"),
                         {**self._envelope(), "mode": "edit-it"}):
            with self.subTest(envelope=envelope):
                result = self._submit(envelope)
                self.assertIn("accepted modes", result.get("error", ""))
                self.assertNotIn("batch_id", result)

    def test_every_declared_mode_key_must_agree(self):
        """One canonical resolved mode per item: `ref_mode`, `params.mode` and the
        envelope's `mode` cannot disagree, and none of them escapes the references check."""
        ref_mode_only = self._submit(self._envelope(ref_mode="edit"))
        self.assertIn("Reference to image needs reference images",
                      ref_mode_only.get("error", ""))
        conflict = self._submit({**self._envelope(mode="txt2img"), "mode": "txt2img"})
        # envelope txt2img + item ref_mode edit: the declared keys contradict each other.
        contradictory = self._submit({**self._envelope(ref_mode="edit"), "mode": "txt2img"})
        self.assertIn("disagree", contradictory.get("error", ""))
        self.assertNotIn("batch_id", contradictory)
        # Same mode declared at both levels with references present: consistent, passes.
        agreed = self._submit({**self._envelope(mode="edit",
                                                reference_images=[reference(b"x")]),
                               "mode": "edit"})
        self.assertNotIn("error", agreed)

    def test_an_explicit_mode_must_agree_with_the_references(self):
        no_refs = self._submit(self._envelope(mode="edit"))
        self.assertIn("Reference to image needs reference images",
                      no_refs.get("error", ""))
        conflict = self._submit(self._envelope(
            mode="txt2img", reference_images=[reference(b"x")]))
        self.assertIn("Text to image cannot carry reference images",
                      conflict.get("error", ""))

    def test_a_text_only_model_is_refused_with_the_owner_words(self):
        zimage = {"repo": MODEL, "modality": "image",
                  "label": "Z-Image-Turbo 6B MLX 4-bit",
                  "image_modes": [{"id": "txt2img", "label": "Text to image",
                                   "available": True, "reason": None}]}
        result = self._submit(self._envelope(reference_images=[reference(b"x")]), [zimage])
        error = result.get("error", "")
        self.assertIn("Z-Image-Turbo 6B MLX 4-bit does not support Reference to image", error)
        self.assertIn("Text to image only", error)
        self.assertNotIn("batch_id", result)

    def test_a_supported_model_that_is_unavailable_says_why_not(self):
        unavailable = {
            "repo": MODEL, "modality": "image", "label": "FLUX.2 Klein 4B MLX 4-bit",
            "image_modes": [
                {"id": "txt2img", "label": "Text to image", "available": True, "reason": None},
                {"id": "edit", "label": "Reference to image", "available": False,
                 "reason": "reference rendering needs about 24 GB of unified memory",
                 "references": {"min": 1, "max": 2}},
            ]}
        result = self._submit(self._envelope(reference_images=[reference(b"x")]),
                              [unavailable])
        error = result.get("error", "")
        self.assertIn("Reference to image is unavailable", error)
        self.assertIn("24 GB", error)
        self.assertNotIn("does not support", error)

    def test_references_pass_the_door_when_a_row_serves_them_or_none_exists(self):
        available = {
            "repo": MODEL, "modality": "image", "label": "FLUX.2 Klein 4B MLX 4-bit",
            "image_modes": [
                {"id": "txt2img", "label": "Text to image", "available": True, "reason": None},
                {"id": "edit", "label": "Reference to image", "available": True,
                 "reason": None, "references": {"min": 1, "max": 2}},
            ]}
        with mock.patch.object(broker, "_monitor",
                               lambda: DoorTests._Monitor([available])):
            self.assertIsNone(broker._image_reference_door_refusal(MODEL))
        with mock.patch.object(broker, "_monitor", lambda: DoorTests._Monitor([])):
            self.assertIsNone(broker._image_reference_door_refusal(MODEL))

    def test_legacy_rows_without_image_modes_are_read_from_the_lane_contract(self):
        # Intrinsic support stated by `lane_contract.references`, no live `edit` capability:
        # unavailable with the producer's reason — never "does not support".
        legacy = image_row(capabilities=("txt2img",))
        legacy["reference_lane"] = {"available": False, "mode": "bounded-staged",
                                    "reason": "this Mac reports 16.0 GB"}
        with mock.patch.object(broker, "_monitor", lambda: DoorTests._Monitor([legacy])):
            error = broker._image_reference_door_refusal(MODEL)
        self.assertIn("Reference to image is unavailable", error)
        self.assertIn("16.0 GB", error)

    def test_video_mode_keys_are_out_of_scope(self):
        with mock.patch.object(broker, "_image_mode_door_refusal") as door, \
                mock.patch.object(broker.cloud_guard, "refusal", return_value="blocked"):
            broker._submit_batch_locked({
                "modality": "video", "model": "org/model",
                "items": [{"prompt": "x", "params": {"mode": "banana"}}]})
        door.assert_not_called()


if __name__ == "__main__":
    unittest.main()
