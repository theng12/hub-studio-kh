from __future__ import annotations

import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from components.hub.backend.final_render_compiler import (
    UnsupportedFinalRenderCombination,
    compile_final_render_packet,
    late_label_input_requirements,
    late_label_sequence_path,
    validate_late_label_dimensions,
    validate_png_bytes,
)
from components.hub.backend.render_contract import validate_render_packet_syntax
from components.hub.backend.render_hash_v1 import render_hash

HERE = Path(__file__).resolve().parent
SCHEMA_BYTES = (HERE / "fixtures" / "late-label-v1.schema.json").read_bytes()
FIXTURE_BYTES = (HERE / "fixtures" / "late-label-v1.fixtures.json").read_bytes()
SCHEMA_SHA256 = "3af1706c30276ef0df9e0c861cd71b424b869210e7067221b5ad8d28ee60d13c"
FIXTURE_SHA256 = "978bd5d357a343cc9e6fb2adc64b381c150e639f793bb88b263927ca8c192173"
SET_SHA256 = "6a7ca2079e190529a9f3aa5ee34a08317d68878b7816c48a0712191bf18e0c58"
VECTORS = json.loads(FIXTURE_BYTES)["vectors"]
OUTPUT = "/private/late-label-workspace/final.mp4"

BASE_RECIPE = {
    "namespace": "studio.render.final.v1",
    "profile_digest": "1" * 64,
    "base_video": {"type": "asset_ref", "logical_id": "base"},
    "premixed_audio": {"type": "asset_ref", "logical_id": "audio"},
    "canvas": {"width": 1280, "height": 720, "working_fps": 25},
    "target_duration_sec": 6,
    "effects_gate": {"start_sec": None, "end_sec": None},
    "operations": [],
    "encode": {
        "container": "mp4", "requested_codec": "h264", "requested_effort": "medium",
        "crf": 23, "quality_cpu_preset": "medium", "bitrate_kbps": None,
    },
}
FRAME = {
    "kind": "presentation_frame", "inset_pct": 0, "border_width": 0,
    "corner_radius": 0, "border_color": "#FFFFFF", "background_color": "#000000",
    "fit": "cover", "background": {"kind": "solid"},
}
LOGO = {
    "kind": "logo", "asset": {"type": "asset_ref", "logical_id": "logo"},
    "opacity": 1, "scale": 0.2, "anchor": "top-left", "margin_px": 4,
    "gate": "always",
}


def raw(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def vector(name):
    return next(item for item in VECTORS if item["id"] == name)


def _refs(value):
    if isinstance(value, dict):
        if value.get("type") == "asset_ref":
            yield value["logical_id"]
        for item in value.values():
            yield from _refs(item)
    elif isinstance(value, list):
        for item in value:
            yield from _refs(item)


def packet_for(operations, *, cue_role="cue_label", canvas=None, duration=None):
    recipe = copy.deepcopy(BASE_RECIPE)
    recipe["operations"] = copy.deepcopy(operations)
    if canvas is not None:
        recipe["canvas"] = dict(canvas)
    if duration is not None:
        recipe["target_duration_sec"] = duration
    roles = {"base": "base_video", "audio": "premixed_audio"}
    names = {"base": "base.mp4", "audio": "audio.wav"}
    for operation in recipe["operations"]:
        for logical_id in _refs(operation):
            if operation["kind"] == "late_label_stage":
                roles[logical_id] = cue_role
                names[logical_id] = logical_id + ".png"
            elif operation["kind"] == "presentation_frame":
                roles[logical_id] = "frame_background"
                names[logical_id] = logical_id + ".png"
            elif operation["kind"] == "logo":
                roles[logical_id] = "logo"
                names[logical_id] = logical_id + ".png"
    manifest = [{
        "logical_id": logical_id,
        "safe_name": names[logical_id],
        "role": roles[logical_id],
        "sha256": hashlib.sha256(logical_id.encode()).hexdigest(),
        "size_bytes": 1,
    } for logical_id in sorted(roles)]
    value = {
        "recipe": recipe, "manifest": manifest,
        "recipe_digest": "", "manifest_digest": "", "packet_digest": "",
    }
    value["recipe_digest"] = render_hash("recipe", recipe)
    value["manifest_digest"] = render_hash("manifest", manifest)
    value["packet_digest"] = render_hash("packet", {
        "recipe_digest": value["recipe_digest"],
        "manifest_digest": value["manifest_digest"],
    })
    return value


def vector_packet(name, *, cue_role=None):
    item = vector(name)
    return packet_for(
        [item["operation"]],
        cue_role=cue_role or item["context"].get("asset_role", "cue_label"),
        canvas=item["context"]["canvas"],
        duration=item["context"]["target_duration_sec"],
    )


def renumber_late_operation(operation, index):
    """Move a frozen index-0 operation to its constructed recipe ordinal."""
    value = copy.deepcopy(operation)
    old_prefix = "cue_0000_"
    new_prefix = f"cue_{index:04d}_"

    def rename(node):
        if isinstance(node, dict):
            if node.get("type") == "asset_ref" and isinstance(
                    node.get("logical_id"), str):
                logical_id = node["logical_id"]
                if logical_id.startswith(old_prefix):
                    node["logical_id"] = new_prefix + logical_id[len(old_prefix):]
            for item in node.values():
                rename(item)
        elif isinstance(node, list):
            for item in node:
                rename(item)

    rename(value)
    return value


def assets_for(packet):
    return {entry["logical_id"]: "/schema/" + entry["safe_name"]
            for entry in packet["manifest"]}


def png_chunk(kind, payload):
    crc = zlib.crc32(payload, zlib.crc32(kind)) & 0xFFFFFFFF
    return (len(payload).to_bytes(4, "big") + kind + payload
            + crc.to_bytes(4, "big"))


def minimal_png(width=240, height=80, facts=(8, 6, 0, 0, 0), extra=b"",
                idat_payload=None, without_idat=False):
    ihdr = width.to_bytes(4, "big") + height.to_bytes(4, "big") + bytes(facts)
    if idat_payload is None:
        row = b"\0" + b"\0" * (width * 4)
        idat_payload = zlib.compress(row * height)
    chunks = [png_chunk(b"IHDR", ihdr), extra]
    if not without_idat:
        chunks.append(png_chunk(b"IDAT", idat_payload))
    chunks.append(png_chunk(b"IEND", b""))
    return b"\x89PNG\r\n\x1a\n" + b"".join(chunks)


class LateLabelContractTests(unittest.TestCase):
    def assert_rejected(self, value):
        with self.assertRaisesRegex(ValueError, "Invalid render packet"):
            validate_render_packet_syntax(raw(value))

    def test_frozen_schema_fixture_and_set_hashes_match(self):
        self.assertEqual(hashlib.sha256(SCHEMA_BYTES).hexdigest(), SCHEMA_SHA256)
        self.assertEqual(hashlib.sha256(FIXTURE_BYTES).hexdigest(), FIXTURE_SHA256)
        combined = hashlib.sha256(SCHEMA_BYTES + b"\0" + FIXTURE_BYTES).hexdigest()
        self.assertEqual(combined, SET_SHA256)

    def test_all_fifteen_frozen_operation_vectors_match(self):
        self.assertEqual(len(VECTORS), 15)
        for item in VECTORS:
            with self.subTest(item["id"]):
                value = vector_packet(item["id"])
                if item["valid"]:
                    validate_render_packet_syntax(raw(value))
                else:
                    self.assert_rejected(value)

    def test_stage_order_repeatable_cues_and_singleton_logo(self):
        late = vector("positive-none")["operation"]
        value = packet_for([copy.deepcopy(late), copy.deepcopy(FRAME)])
        self.assert_rejected(value)
        staged = packet_for([
            copy.deepcopy(FRAME),
            renumber_late_operation(late, 1),
            copy.deepcopy(LOGO),
        ])
        validate_render_packet_syntax(raw(staged))

        second = copy.deepcopy(late)
        second["cue_id"] = "second"
        second["label"]["asset"]["logical_id"] = "cue_0001_label"
        repeated = packet_for([copy.deepcopy(late), second])
        validate_render_packet_syntax(raw(repeated))

        twice_logo = packet_for([copy.deepcopy(late), copy.deepcopy(LOGO),
                                 copy.deepcopy(LOGO)])
        self.assert_rejected(twice_logo)

        duplicate = packet_for([copy.deepcopy(late), copy.deepcopy(late)])
        duplicate["recipe"]["operations"][1]["label"]["asset"]["logical_id"] = \
            "cue_0001_label"
        self.assert_rejected(duplicate)

    def test_ll_f07_uses_normative_ordinal_names_not_truncated_cue_text(self):
        late = copy.deepcopy(vector("positive-none")["operation"])
        late["cue_id"] = "a" * 64
        validate_render_packet_syntax(raw(packet_for([late])))
        late["label"]["asset"]["logical_id"] = (
            "cue_0000_" + "a" * 47 + "deadbeef")
        self.assert_rejected(packet_for([late]))


class LateLabelCompilerAndPngTests(unittest.TestCase):
    def test_preparation_lists_private_png_ids_and_ordered_sequences(self):
        requirements = late_label_input_requirements(
            raw(vector_packet("positive-wipe")))
        self.assertIsNotNone(requirements)
        self.assertEqual([item.key for item in requirements.sequences],
                         ["cue_0000_r"])
        self.assertEqual(len(requirements.sequences[0].logical_ids), 9)
        self.assertEqual(requirements.sequences[0].working_fps, 25)
        self.assertEqual(requirements.sequences[0].start_sec, 2.125)
        self.assertEqual(requirements.png_logical_ids,
                         tuple(f"cue_0000_r{i:04d}" for i in range(9)))
        self.assertIsNone(late_label_input_requirements(raw(packet_for([]))))

    def test_default_refuses_late_stage_and_explicit_allowed_compiles(self):
        packet = vector_packet("positive-none")
        packet_bytes = raw(packet)
        with self.assertRaises(UnsupportedFinalRenderCombination) as raised:
            compile_final_render_packet(packet_bytes, assets_for(packet), OUTPUT)
        self.assertEqual(raised.exception.reason, "late_label_profile_not_allowed")
        with self.assertRaises(UnsupportedFinalRenderCombination):
            compile_final_render_packet(
                packet_bytes, assets_for(packet), OUTPUT, late_labels_allowed=False)
        for truthy in (1, "yes"):
            with self.subTest(truthy=truthy):
                with self.assertRaises(UnsupportedFinalRenderCombination) as raised:
                    compile_final_render_packet(
                        packet_bytes, assets_for(packet), OUTPUT,
                        late_labels_allowed=truthy)
                self.assertEqual(raised.exception.reason,
                                 "late_label_profile_not_allowed")
        plan = compile_final_render_packet(
            packet_bytes, assets_for(packet), OUTPUT, late_labels_allowed=True)
        self.assertEqual(plan.generated_inputs, ())
        self.assertIn("cue_0000_label", {item[0] for item in plan.input_uses})

    def test_no_op_legacy_packet_is_unchanged_by_default(self):
        packet = packet_for([])
        first = compile_final_render_packet(raw(packet), assets_for(packet), OUTPUT)
        second = compile_final_render_packet(
            raw(packet), assets_for(packet), OUTPUT, late_labels_allowed=False)
        self.assertEqual(first, second)
        self.assertEqual(first.generated_inputs, ())
        argv = list(first.tiers[0].argv)
        self.assertEqual(argv.count("-i"), 2)

    def test_finite_reveal_plan_is_receiver_owned_and_stage_ordered(self):
        packet = vector_packet("positive-wipe")
        plan = compile_final_render_packet(
            raw(packet), assets_for(packet), OUTPUT, late_labels_allowed=True)
        expected = late_label_sequence_path(OUTPUT, "cue_0000_r")
        self.assertEqual(expected,
                         OUTPUT + ".late-label/cue_0000_r/%04d.png")
        self.assertEqual(plan.generated_inputs, (("cue_0000_r", 2),))
        argv = list(plan.tiers[0].argv)
        pattern_index = argv.index(expected)
        self.assertEqual(argv[pattern_index - 7:pattern_index], [
            "-framerate", "25", "-f", "image2", "-start_number", "0", "-i",
        ])
        graph = argv[argv.index("-filter_complex") + 1]
        self.assertIn("setpts=PTS+2.125/TB", graph)
        self.assertIn("between(t,2.125,4.875)", graph)
        self.assertIn("eof_action=repeat", graph)
        self.assertNotIn("shortest=1", graph)
        self.assertNotIn("%04d", raw(packet).decode())

    def test_late_overlay_emits_enable_and_scales_source_for_scale_motions(self):
        for name, scaled in (("positive-wipe", False),
                             ("positive-pop", True),
                             ("positive-pulse", True)):
            with self.subTest(name=name):
                packet = vector_packet(name)
                plan = compile_final_render_packet(
                    raw(packet), assets_for(packet), OUTPUT, late_labels_allowed=True)
                argv = list(plan.tiers[0].argv)
                graph = argv[argv.index("-filter_complex") + 1]
                overlay = next(statement for statement in graph.split(";\n")
                               if "overlay=" in statement)
                self.assertIn(":enable='between(t,2.125,4.875)'", overlay)
                self.assertNotIn(",enable=", overlay)
                self.assertNotIn(":scale=", overlay)
                self.assertEqual("scale=w='iw*(" in graph, scaled)
                if scaled:
                    self.assertIn(":eval=frame", overlay)

    def test_reveal_then_later_static_cue_keeps_distinct_input_indices(self):
        first = vector("positive-wipe")["operation"]
        second = copy.deepcopy(vector("positive-none")["operation"])
        second["cue_id"] = "second"
        second["label"]["asset"]["logical_id"] = "cue_0001_label"
        packet = packet_for([first, second])
        plan = compile_final_render_packet(
            raw(packet), assets_for(packet), OUTPUT, late_labels_allowed=True)
        pattern = late_label_sequence_path(OUTPUT, "cue_0000_r")
        self.assertEqual(plan.generated_inputs, (("cue_0000_r", 2),))
        self.assertEqual(dict(plan.input_uses)["cue_0001_label"], 3)
        argv = list(plan.tiers[0].argv)
        input_paths = [argv[index + 1] for index, value in enumerate(argv)
                       if value == "-i"]
        self.assertEqual(len(input_paths), 4)
        self.assertEqual(input_paths[2], pattern)
        self.assertEqual(input_paths[3], "/schema/cue_0001_label.png")

    def test_frame_late_and_logo_filter_order_is_frame_then_cue_then_logo(self):
        packet = packet_for([
            copy.deepcopy(FRAME),
            renumber_late_operation(vector("positive-none")["operation"], 1),
            copy.deepcopy(LOGO),
        ])
        plan = compile_final_render_packet(
            raw(packet), assets_for(packet), OUTPUT, late_labels_allowed=True)
        argv = list(plan.tiers[0].argv)
        graph = argv[argv.index("-filter_complex") + 1]
        frame_at = graph.index("mainframe")
        cue_at = graph.index("between(t,2.125,4.875)")
        logo_at = graph.index("colorchannelmixer")
        self.assertLess(frame_at, cue_at)
        self.assertLess(cue_at, logo_at)

    def test_png_bytes_validate_facts_hash_size_and_refuse_animation_palette(self):
        data = minimal_png()
        digest = hashlib.sha256(data).hexdigest()
        self.assertEqual(validate_png_bytes(
            data, expected_sha256=digest, expected_size_bytes=len(data),
            expected_width=240, expected_height=80), (240, 80))
        with self.assertRaisesRegex(ValueError, "sha256"):
            validate_png_bytes(data, expected_sha256="0" * 64)
        with self.assertRaisesRegex(ValueError, "size"):
            validate_png_bytes(data, expected_size_bytes=len(data) + 1)
        with self.assertRaisesRegex(ValueError, "facts"):
            validate_png_bytes(minimal_png(facts=(8, 6, 1, 0, 0)))
        animated = png_chunk(b"acTL", b"\0" * 8)
        with self.assertRaisesRegex(ValueError, "animation"):
            validate_png_bytes(minimal_png(extra=animated))

    def test_png_requires_real_closed_idat_crc_and_decodable_scanlines(self):
        with self.assertRaisesRegex(ValueError, "IDAT"):
            validate_png_bytes(minimal_png(without_idat=True))
        corrupt = bytearray(minimal_png())
        corrupt[-1] ^= 1
        with self.assertRaisesRegex(ValueError, "CRC"):
            validate_png_bytes(bytes(corrupt))
        with self.assertRaisesRegex(ValueError, "compression"):
            validate_png_bytes(minimal_png(idat_payload=b"not-a-zlib-stream"))
        with self.assertRaisesRegex(ValueError, "unknown critical"):
            validate_png_bytes(minimal_png(extra=png_chunk(b"ABCD", b"")))

    def test_png_dimensions_bind_line_canvas_reveal_equality_and_placement(self):
        wipe = vector_packet("positive-wipe")
        ids = late_label_input_requirements(raw(wipe)).png_logical_ids
        validate_late_label_dimensions(
            raw(wipe), {logical_id: (240, 80) for logical_id in ids})
        mixed = {logical_id: (240, 80) for logical_id in ids}
        mixed["cue_0000_r0004"] = (239, 80)
        with self.assertRaisesRegex(
                ValueError, "incompatible late_label_stage\\[0\\]\\.reveal"):
            validate_late_label_dimensions(raw(wipe), mixed)

        line = vector_packet("positive-line-draw")
        line_ids = late_label_input_requirements(raw(line)).png_logical_ids
        line_dimensions = {}
        for logical_id in line_ids:
            line_dimensions[logical_id] = (
                (1280, 720) if logical_id.startswith("cue_0000_lr")
                else (240, 80))
        validate_late_label_dimensions(raw(line), line_dimensions)

        moved = copy.deepcopy(wipe)
        moved["recipe"]["operations"][0]["placement"]["x_px"] = 1041
        moved["recipe_digest"] = render_hash("recipe", moved["recipe"])
        moved["packet_digest"] = render_hash("packet", {
            "recipe_digest": moved["recipe_digest"],
            "manifest_digest": moved["manifest_digest"],
        })
        with self.assertRaisesRegex(ValueError, "placement"):
            validate_late_label_dimensions(
                raw(moved), {logical_id: (240, 80) for logical_id in ids})


if __name__ == "__main__":
    unittest.main()
