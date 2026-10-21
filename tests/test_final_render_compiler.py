from __future__ import annotations

import ast
import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from components.hub.backend.final_render_compiler import (
    UnsupportedFinalRenderCombination,
    _build_logo_filters,
    _compile_validated_packet,
    _js_fixed,
    compile_final_render_packet,
)
from components.hub.backend.render_contract import validate_render_packet_syntax

HERE = Path(__file__).resolve().parent
FIXTURE_PATH = HERE / "fixtures" / "final-render-compiler-v1.json"
FIXTURE_BYTES = FIXTURE_PATH.read_bytes()
FIXTURE = json.loads(FIXTURE_BYTES)
FIXTURE_SHA256 = "923bff7b3955f69b0fe9b4e140f56eedb05c5c8fa2dd93e7d249ae8d67b848e7"
OUTPUT = FIXTURE["paths"]["output"]


def raw(packet):
    return json.dumps(packet, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def assets_for(vector):
    if "local_assets" in vector:
        return dict(vector["local_assets"])
    available = FIXTURE["paths"]["local_assets"]
    return {entry["logical_id"]: available[entry["logical_id"]]
            for entry in vector["packet"]["manifest"]}


def compile_vector(vector):
    return compile_final_render_packet(
        raw(vector["packet"]), assets_for(vector), vector.get("output_path", OUTPUT)
    )


def graph(plan):
    argv = plan.tiers[0].argv
    index = argv.index("-filter_complex")
    return argv[index + 1]


def input_view(plan):
    return [{"logicalId": logical_id, "index": index}
            for logical_id, index in plan.input_uses]


def tier_view(tier):
    result = {"label": tier.label}
    if tier.note is not None:
        result["note"] = tier.note
    if tier.degraded is not None:
        result["degraded"] = tier.degraded
    if tier.drops_burned_in_features:
        result["drops_burned_in_features"] = True
    result["argv"] = list(tier.argv)
    return result


def integral_floats(value):
    if type(value) is int:
        return float(value)
    if type(value) is list:
        return [integral_floats(item) for item in value]
    if type(value) is dict:
        return {key: integral_floats(item) for key, item in value.items()}
    return value


def sealed_packet(recipe, manifest, digests):
    return {
        "recipe": recipe,
        "manifest": manifest,
        "recipe_digest": digests[0],
        "manifest_digest": digests[1],
        "packet_digest": digests[2],
    }


class FinalRenderCompilerTests(unittest.TestCase):
    def test_fixture_is_the_exact_schema_only_story_vector(self):
        self.assertEqual(hashlib.sha256(FIXTURE_BYTES).hexdigest(), FIXTURE_SHA256)
        self.assertIs(FIXTURE["schema_test_only"], True)
        self.assertEqual(FIXTURE["qualification"], "none")
        self.assertEqual(FIXTURE["source"]["story_base"],
                         "5283a0ccc50e9f52a0205a1278e549c584331465")
        self.assertIn("not a historical no-font render", FIXTURE["source"]["derivation"])

    def test_f0_and_f1_match_literal_rmp_inputs_burnins_graph_and_argv(self):
        expected_hashes = {
            "f0_base_audio": (
                "e86ab313ca78a7d0819e0179f712ab0ca7a2ac29731164cd5d87378d3fb4aa1b",
                "ca219ad30f3250f02c50fea8a76833b4c753ac0a898fea11dbc104c29869b3c1",
                "9c0cf536db4ba2c82d284dc713d2fd8f9e6a9b09483ce63ac7f87929bf1c303f",
            ),
            "f1_derived_no_font": (
                "75a3bbe96384ab9c07ecbb8246e836081c500ed641261cfe34754a20859bf823",
                "24ac419b6cfea3cb23f8a5dbc54b1bbce550c8ef028f555dd4502543f16e722c",
                "b71d4587c84e86eb95676b33ac6970265613076da430920dc26f5ee303146e25",
            ),
        }
        for name in ("f0_base_audio", "f1_derived_no_font"):
            vector = FIXTURE[name]
            packet = vector["packet"]
            self.assertEqual((packet["recipe_digest"], packet["manifest_digest"],
                              packet["packet_digest"]), expected_hashes[name])
            plan = compile_vector(vector)
            self.assertEqual(input_view(plan), vector["expected_input_uses"])
            self.assertEqual(list(plan.requested_burn_ins),
                             vector["expected_requested_burn_ins"])
            self.assertEqual(graph(plan), vector["expected_graph"])
            self.assertEqual(list(plan.tiers[0].argv), vector["expected_full_argv"])
            self.assertEqual(list(plan.tiers[3].argv), vector["expected_minimal_argv"])

    def test_repeated_input_modes_and_zero_start_range_match_literals(self):
        repeated = FIXTURE["overlay_input_and_formula_vector"]
        plan = compile_vector(repeated)
        self.assertEqual(input_view(plan), repeated["expected_input_uses"])
        self.assertEqual(graph(plan), repeated["expected_graph"])
        self.assertEqual(list(plan.tiers[0].argv), repeated["expected_full_argv"])
        zero = FIXTURE["zero_start_null_end_overlay_vector"]
        zero_plan = compile_vector(zero)
        self.assertEqual(input_view(zero_plan), zero["expected_input_uses"])
        self.assertEqual(graph(zero_plan), zero["expected_graph"])
        self.assertNotIn(":enable=", graph(zero_plan))

    def test_aliased_repeated_overlays_compile_by_occurrence(self):
        vector = FIXTURE["aliased_repeated_overlay_vector"]
        distinct = compile_vector(vector)
        self.assertEqual(input_view(distinct), vector["expected_input_uses"])
        self.assertEqual(graph(distinct), vector["expected_graph"])
        self.assertEqual(list(distinct.tiers[0].argv), vector["expected_full_argv"])
        validated = validate_render_packet_syntax(raw(vector["packet"]))
        validated["recipe"]["operations"][1] = validated["recipe"]["operations"][0]
        aliased = _compile_validated_packet(validated, assets_for(vector),
                                             vector["output_path"])
        self.assertIs(validated["recipe"]["operations"][0],
                      validated["recipe"]["operations"][1])
        self.assertEqual(aliased, distinct)

    def test_all_four_gate_forms_match_literal_precision(self):
        for vector in FIXTURE["gate_vectors"]:
            with self.subTest(vector["name"]):
                self.assertEqual(graph(compile_vector(vector)), vector["expected_graph"])

    def test_fractional_frame_values_round_only_after_height_scale(self):
        for vector in FIXTURE["fractional_frame_vectors"]:
            with self.subTest(vector["name"]):
                self.assertEqual(graph(compile_vector(vector)), vector["expected_graph"])
        self.assertIn("overlay=23:23", FIXTURE["fractional_frame_vectors"][0]["expected_graph"])
        self.assertIn("lt(X,10)", FIXTURE["fractional_frame_vectors"][0]["expected_graph"])
        self.assertIn("overlay=55:55", FIXTURE["fractional_frame_vectors"][1]["expected_graph"])
        self.assertIn("lt(X,21)", FIXTURE["fractional_frame_vectors"][1]["expected_graph"])

    def test_logo_gates_private_zero_refusal_and_legacy_local_zero_formula(self):
        for vector in FIXTURE["logo_vectors"]:
            with self.subTest(vector["name"]):
                self.assertEqual(graph(compile_vector(vector)), vector["expected_graph"])
        zero = next(item for item in FIXTURE["unsupported_vectors"]
                    if item["name"] == "zero-logo-margin")
        with self.assertRaises(UnsupportedFinalRenderCombination) as raised:
            compile_vector(zero)
        self.assertEqual(raised.exception.reason, "logo_margin_default")
        vector = FIXTURE["local_legacy_logo_zero"]
        counter = iter(("[v1]",))
        filters, output = _build_logo_filters(
            vector["input"], lambda: next(counter), vector["logo"],
            vector["input_index"], vector["width"], vector["height"], "",
        )
        self.assertEqual({"filters": list(filters), "output": output}, vector["expected"])

    def test_letterbox_noop_skip_and_logo_gate_precedence_refuse_whole_plan(self):
        for vector in FIXTURE["unsupported_vectors"]:
            with self.subTest(vector["name"]):
                with self.assertRaises(UnsupportedFinalRenderCombination) as raised:
                    compile_vector(vector)
                self.assertEqual(raised.exception.reason, vector["reason"])

    def test_all_literal_tiers_labels_argv_conditions_and_fresh_values_match(self):
        matching = "encoder libx265 failed with return code -22"
        for vector in FIXTURE["tier_vectors"]:
            with self.subTest(vector["name"]):
                first = compile_vector(vector)
                second = compile_vector(vector)
                self.assertEqual([tier_view(t) for t in first.tiers],
                                 vector["expected_tiers"])
                effective_h265 = (vector["packet"]["recipe"]["encode"]["container"] != "webm"
                                  and vector["packet"]["recipe"]["encode"]["requested_codec"] == "h265")
                hardware = vector["packet"]["recipe"]["encode"]["requested_effort"] == "hardware"
                self.assertEqual([tier.condition for tier in first.tiers], [
                    "always", "always" if hardware else "never",
                    "libx265_einval" if effective_h265 else "never", "always",
                ])
                expected = vector["expected_predicates"]
                self.assertEqual(first.tiers[1].should_run(), expected["hardware_fallback"])
                self.assertEqual(first.tiers[2].should_run(matching), expected["h265_matching_error"])
                self.assertEqual(first.tiers[2].should_run("return code -22"),
                                 expected["h265_missing_libx265"])
                self.assertEqual(first.tiers[2].should_run("libx265 failed"),
                                 expected["h265_missing_return_code"])
                self.assertEqual(first.tiers[2].should_run(), expected["h265_empty_error"])
                self.assertEqual(first, second)
                self.assertIsNot(first.tiers, second.tiers)
                for left, right in zip(first.tiers, second.tiers):
                    self.assertIsNot(left.argv, right.argv)
        hardware = next(v for v in FIXTURE["tier_vectors"] if v["name"] == "hardware-quality-slow")
        webm = next(v for v in FIXTURE["tier_vectors"] if v["name"] == "webm-stays-vp9")
        self.assertIn("h264_videotoolbox", hardware["expected_tiers"][3]["argv"])
        self.assertIn("libvpx-vp9", webm["expected_tiers"][3]["argv"])

    def test_float_string_and_color_emission_matches_story_literals(self):
        f1 = compile_vector(FIXTURE["f1_derived_no_font"])
        self.assertEqual(graph(f1), FIXTURE["f1_derived_no_font"]["expected_graph"])
        self.assertIn("brightness=-0.030:contrast=1.200:saturation=0.850",
                      graph(f1))
        self.assertIn("color=#F2C14E", graph(f1))
        solid_frame = graph(compile_vector(FIXTURE["fractional_frame_vectors"][0]))
        self.assertIn("color=c=#111C34", solid_frame)
        repeated = graph(compile_vector(FIXTURE["overlay_input_and_formula_vector"]))
        self.assertIn("aa=0", repeated)
        self.assertIn("gte(t,0.123)", repeated)
        for vector in FIXTURE["fractional_frame_vectors"]:
            self.assertEqual(graph(compile_vector(vector)), vector["expected_graph"])

    def test_integral_float_spellings_keep_identity_and_literal_plan(self):
        for name in ("f1_derived_no_font",):
            vector = FIXTURE[name]
            packet = integral_floats(vector["packet"])
            self.assertEqual(packet["recipe_digest"], vector["packet"]["recipe_digest"])
            self.assertEqual(packet["manifest_digest"], vector["packet"]["manifest_digest"])
            self.assertEqual(packet["packet_digest"], vector["packet"]["packet_digest"])
            plan = compile_final_render_packet(raw(packet), assets_for(vector), vector["output_path"])
            self.assertEqual(input_view(plan), vector["expected_input_uses"])
            self.assertEqual(list(plan.requested_burn_ins), vector["expected_requested_burn_ins"])
            self.assertEqual(graph(plan), vector["expected_graph"])
            self.assertEqual(list(plan.tiers[0].argv), vector["expected_full_argv"])
            self.assertEqual(list(plan.tiers[3].argv), vector["expected_minimal_argv"])

        for vector in FIXTURE["tier_vectors"]:
            with self.subTest(vector["name"]):
                packet = integral_floats(vector["packet"])
                plan = compile_final_render_packet(raw(packet), assets_for(vector), OUTPUT)
                self.assertEqual([tier_view(tier) for tier in plan.tiers],
                                 vector["expected_tiers"])

    def test_signed_zero_grade_uses_literal_javascript_fixed_spelling(self):
        base = copy.deepcopy(FIXTURE["f0_base_audio"]["packet"])
        recipe = base["recipe"]
        recipe["operations"] = [{
            "kind": "classic_color_grade", "preset": "none",
            "brightness": -0.0, "contrast": -0.0, "saturation": -0.0,
            "warmth": 0, "tint": 0,
        }]
        packet = sealed_packet(recipe, base["manifest"], (
            "d2bbdcd46e488dd23a06c8c8e733b05df7ce14aa892d22ce206a996a340d85d3",
            "ca219ad30f3250f02c50fea8a76833b4c753ac0a898fea11dbc104c29869b3c1",
            "d6b993f6a5db7e79319147277745639f35eae0fb6cba95f36702e690fbe3c378",
        ))
        expected_graph = ("[0:v]setpts=PTS-STARTPTS[v0];\n"
                          "[v0]eq=contrast=0.000:saturation=0.000[v1];\n"
                          "[v1]null[vout]")
        expected_argv = [
            "-y", "-i", "/schema/assets/base.mp4", "-i", "/schema/assets/audio.wav",
            "-filter_complex", expected_graph, "-map", "[vout]", "-map", "1:a",
            "-c:v", "libx264", "-crf", "23", "-preset", "ultrafast",
            "-c:a", "aac", "-b:a", "384k", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", "-t", "2", "/schema/output/final.mp4",
        ]
        plan = compile_final_render_packet(
            raw(packet), FIXTURE["f0_base_audio"]["local_assets"], OUTPUT,
        )
        self.assertEqual(graph(plan), expected_graph)
        self.assertEqual(list(plan.tiers[0].argv), expected_argv)
        self.assertEqual(plan.tiers[0].label, "H.264 · preset ultrafast")
        self.assertEqual(_js_fixed(-0.0, 3), "0.000")
        self.assertEqual(_js_fixed(-0.0004, 3), "-0.000")

    def test_frame_rounding_lower_neighbor_and_tie_match_literal_plans(self):
        base = FIXTURE["f0_base_audio"]["packet"]
        cases = (
            (
                "lower-neighbor", 0.49999999999999994,
                (
                    "8f0abab4f5509ea3ae8ca8f8459b93859a1eae7530ce3dcc85a939af7222061d",
                    "ca219ad30f3250f02c50fea8a76833b4c753ac0a898fea11dbc104c29869b3c1",
                    "5f1abaa4bbfa31310b9e7b8a3fec454470eb2e6f5f1e8483e6153f7acd226636",
                ),
                ("[0:v]setpts=PTS-STARTPTS[v0];\n"
                 "[v0]scale=128:1080:force_original_aspect_ratio=increase,crop=128:1080,format=rgba[mainframe2media];\n"
                 "color=c=#111C34:s=128x1080:r=5[mainframe2bg];\n"
                 "[mainframe2bg]drawbox=x=0:y=0:w=128:h=1080:color=#F2C14E:t=fill[mainframe2border];\n"
                 "[mainframe2border][mainframe2media]overlay=0:0:shortest=1,format=yuv420p[v1];\n"
                 "[v1]null[vout]"),
            ),
            (
                "tie", 0.5,
                (
                    "6cad994c1dcb6b204931f45156edc8ad7a5ceb3a68b90f284bd5e9654cce0d27",
                    "ca219ad30f3250f02c50fea8a76833b4c753ac0a898fea11dbc104c29869b3c1",
                    "44a368154eba85d4f28ebd6bdd270c87936bc4418ceb84e4a59b8b15c4de8e3b",
                ),
                ("[0:v]setpts=PTS-STARTPTS[v0];\n"
                 "[v0]scale=128:1080:force_original_aspect_ratio=increase,crop=128:1080,format=rgba[mainframe2rgb];\n"
                 "color=c=white:s=128x1080:r=5,trim=end_frame=1,format=gray,geq=lum='if(lt(X,1)*lt(Y,1),if(lte(pow(X-1,2)+pow(Y-1,2),1),255,0),if(gte(X,W-1)*lt(Y,1),if(lte(pow(X-(W-1),2)+pow(Y-1,2),1),255,0),if(lt(X,1)*gte(Y,H-1),if(lte(pow(X-1,2)+pow(Y-(H-1),2),1),255,0),if(gte(X,W-1)*gte(Y,H-1),if(lte(pow(X-(W-1),2)+pow(Y-(H-1),2),1),255,0),255))))',loop=loop=-1:size=1[mainframe2mask];\n"
                 "[mainframe2rgb][mainframe2mask]alphamerge=shortest=1[mainframe2media];\n"
                 "color=c=#111C34:s=128x1080:r=5[mainframe2bg];\n"
                 "[mainframe2bg]drawbox=x=0:y=0:w=128:h=1080:color=#F2C14E:t=fill[mainframe2border];\n"
                 "[mainframe2border][mainframe2media]overlay=0:0:shortest=1,format=yuv420p[v1];\n"
                 "[v1]null[vout]"),
            ),
        )
        for name, radius, digests, expected_graph in cases:
            with self.subTest(name):
                recipe = copy.deepcopy(base["recipe"])
                recipe["canvas"] = {"width": 128, "height": 1080, "working_fps": 5}
                recipe["operations"] = [{
                    "kind": "presentation_frame", "inset_pct": 0,
                    "border_width": 0, "corner_radius": radius,
                    "border_color": "#F2C14E", "background_color": "#111C34",
                    "fit": "cover", "background": {"kind": "solid"},
                }]
                packet = sealed_packet(recipe, copy.deepcopy(base["manifest"]), digests)
                expected_argv = [
                    "-y", "-i", "/schema/assets/base.mp4", "-i", "/schema/assets/audio.wav",
                    "-filter_complex", expected_graph, "-map", "[vout]", "-map", "1:a",
                    "-c:v", "libx264", "-crf", "23", "-preset", "ultrafast",
                    "-c:a", "aac", "-b:a", "384k", "-pix_fmt", "yuv420p",
                    "-movflags", "+faststart", "-t", "2", "/schema/output/final.mp4",
                ]
                plan = compile_final_render_packet(
                    raw(packet), FIXTURE["f0_base_audio"]["local_assets"], OUTPUT,
                )
                self.assertEqual(graph(plan), expected_graph)
                self.assertEqual(list(plan.tiers[0].argv), expected_argv)
                self.assertEqual(plan.tiers[0].label, "H.264 · preset ultrafast")
                if name == "lower-neighbor":
                    self.assertNotIn("geq=", graph(plan))
                    self.assertNotIn("alphamerge", graph(plan))

    def test_closed_raw_and_local_binding_boundaries_refuse_before_emission(self):
        vector = FIXTURE["f0_base_audio"]
        packet_bytes = raw(vector["packet"])
        good = assets_for(vector)
        cases = (
            {"base": good["base"]},
            {**good, "extra": "/schema/assets/extra.png"},
            {**good, "audio": ""},
            {**good, "audio": "/schema/assets/audio\0.wav"},
        )
        for assets in cases:
            with self.assertRaises(ValueError):
                compile_final_render_packet(packet_bytes, assets, OUTPUT)
        for output in ("", "/schema/output/final\0.mp4"):
            with self.assertRaises(ValueError):
                compile_final_render_packet(packet_bytes, good, output)
        with self.assertRaises(ValueError):
            compile_final_render_packet(packet_bytes.decode(), good, OUTPUT)
        assets = dict(good)
        before = dict(assets)
        compile_final_render_packet(packet_bytes, assets, OUTPUT)
        self.assertEqual(assets, before)
        self.assertEqual(packet_bytes, raw(vector["packet"]))

    def test_module_import_closure_has_no_service_or_execution_edge(self):
        source_path = HERE.parent / "components" / "hub" / "backend" / "final_render_compiler.py"
        source = source_path.read_text()
        tree = ast.parse(source)
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.add(("." * node.level) + (node.module or ""))
        self.assertLessEqual(imports, {
            "__future__", "collections.abc", "dataclasses", "decimal", "math",
            "re", "typing", ".render_contract",
        })
        for forbidden in ("subprocess", "os.", "pathlib", "socket", "requests",
                          "urllib", "render_assets", "broker", "auth", "route"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
