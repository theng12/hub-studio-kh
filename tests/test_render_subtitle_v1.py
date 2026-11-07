from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from components.hub.backend.final_render_compiler import (
    UnsupportedFinalRenderCombination,
    _filter_path,
    compile_final_render_packet,
    subtitle_input_requirements,
)
from components.hub.backend.render_contract import validate_render_packet_syntax
from components.hub.backend.render_hash_v1 import render_hash
from components.hub.backend.render_profiles import (
    validate_render_compiler_descriptor,
    validate_render_profile_descriptor,
)

HERE = Path(__file__).resolve().parent
FIXTURE = json.loads((HERE / "fixtures" / "final-render-compiler-v1.json").read_bytes())
BASE = FIXTURE["f0_base_audio"]["packet"]
OUTPUT = "/schema/output/final.mp4"
LIMITS = {
    "max_subtitle_fonts": 8,
    "max_subtitle_font_bytes": 33554432,
    "max_subtitle_ass_bytes": 4194304,
    "max_asset_bytes": 8589934592,
    "max_total_input_bytes": 17179869184,
}
ASS = """[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Anton,30.67,&H00ffffff,&H00ffffff,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,3.33,0,5,27,27,27,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.40,0:00:01.82,Default,,0,0,0,,{\\pos(640,446)}EVERY FRAME
Dialogue: 0,0:00:01.82,0:00:05.80,Default,,0,0,0,,MATCHES HERE.
"""


def raw(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def ttf(family="Anton"):
    name = family.encode("utf-16-be")
    name_table = (
        b"\x00\x00" + (1).to_bytes(2, "big") + (18).to_bytes(2, "big")
        + (3).to_bytes(2, "big") + (1).to_bytes(2, "big")
        + (0).to_bytes(2, "big") + (1).to_bytes(2, "big")
        + len(name).to_bytes(2, "big") + (0).to_bytes(2, "big") + name
    )
    return (
        b"\x00\x01\x00\x00" + (1).to_bytes(2, "big")
        + (0).to_bytes(2, "big") + (0).to_bytes(2, "big") + (0).to_bytes(2, "big")
        + b"name" + bytes(4) + (28).to_bytes(4, "big")
        + len(name_table).to_bytes(4, "big") + name_table
    )


def font_record(logical_id, family, data):
    return {
        "asset": {"type": "asset_ref", "logical_id": logical_id},
        "family": family,
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def subtitle_operation(fonts=("Anton",)):
    return {
        "kind": "subtitle_stage",
        "contract": "studio.render.subtitle.v1",
        "ass": {"type": "asset_ref", "logical_id": "subtitle_ass"},
        "fonts": [font_record(f"subtitle_font_{index:04d}", family,
                              ttf(family) if index else ttf(family))
                  for index, family in enumerate(fonts)],
    }


def build_packet(ass=ASS, fonts=(("Anton", None),), operations=None, duration=6.0):
    ass = ass.encode("utf-8") if type(ass) is str else ass
    packet = copy.deepcopy(BASE)
    recipe = packet["recipe"]
    recipe["canvas"] = {"width": 1280, "height": 720, "working_fps": 5}
    recipe["target_duration_sec"] = duration
    if operations is None:
        operations = [{
            "kind": "subtitle_stage",
            "contract": "studio.render.subtitle.v1",
            "ass": {"type": "asset_ref", "logical_id": "subtitle_ass"},
            "fonts": [font_record(f"subtitle_font_{index:04d}", family,
                                  data if data is not None else ttf(family))
                      for index, (family, data) in enumerate(fonts)],
        }]
    recipe["operations"] = operations
    packet["manifest"] = [
        {"logical_id": "audio", "safe_name": "audio.wav", "role": "premixed_audio",
         "sha256": hashlib.sha256(b"a").hexdigest(), "size_bytes": 1},
        {"logical_id": "base", "safe_name": "base.mp4", "role": "base_video",
         "sha256": hashlib.sha256(b"b").hexdigest(), "size_bytes": 1},
        {"logical_id": "subtitle_ass", "safe_name": "subtitle_ass.ass",
         "role": "subtitle_ass", "sha256": hashlib.sha256(ass).hexdigest(),
         "size_bytes": len(ass)},
    ]
    for index, (family, data) in enumerate(fonts):
        data = data if data is not None else ttf(family)
        packet["manifest"].append({
            "logical_id": f"subtitle_font_{index:04d}",
            "safe_name": f"subtitle_font_{index:04d}.ttf",
            "role": "subtitle_font",
            "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
        })
    packet["manifest"].sort(key=lambda entry: entry["logical_id"])
    seal(packet)
    return packet


def seal(packet):
    packet["recipe_digest"] = render_hash("recipe", packet["recipe"])
    packet["manifest_digest"] = render_hash("manifest", packet["manifest"])
    packet["packet_digest"] = render_hash("packet", {
        "recipe_digest": packet["recipe_digest"],
        "manifest_digest": packet["manifest_digest"],
    })
    return packet


def stage_files(directory, packet, ass=ASS, fonts=None):
    ass = ass.encode("utf-8") if type(ass) is str else ass
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    fonts_dir = root / "fonts"
    fonts_dir.mkdir()
    ass_path = root / "subtitle.ass"
    ass_path.write_bytes(ass)
    assets = dict(FIXTURE["f0_base_audio"]["local_assets"])
    assets["subtitle_ass"] = str(ass_path)
    for index, (family, data) in enumerate(fonts or ()):
        logical_id = f"subtitle_font_{index:04d}"
        path = fonts_dir / (logical_id + ".ttf")
        path.write_bytes(data or ttf(family))
        assets[logical_id] = str(path)
    return assets, str(fonts_dir)


def compile_subtitle(packet, assets, fonts_dir, **options):
    values = {"subtitles_allowed": True, "subtitle_fonts_dir": fonts_dir,
              "subtitle_limits": LIMITS}
    values.update(options)
    return compile_final_render_packet(raw(packet), assets, OUTPUT, **values)


class SubtitlePacketContractTests(unittest.TestCase):
    def assert_invalid(self, packet, reason=None):
        with self.assertRaisesRegex(ValueError, reason or "Invalid render packet"):
            validate_render_packet_syntax(raw(seal(packet)))

    def test_story_shape_roles_and_manifest_are_accepted(self):
        packet = build_packet()
        operation = packet["recipe"]["operations"][0]
        self.assertEqual(list(operation),
                         ["kind", "contract", "ass", "fonts"])
        self.assertEqual(operation["ass"],
                         {"type": "asset_ref", "logical_id": "subtitle_ass"})
        self.assertEqual(list(operation["fonts"][0]),
                         ["asset", "family", "sha256"])
        validated = validate_render_packet_syntax(raw(packet))
        requirements = subtitle_input_requirements(raw(packet))
        self.assertEqual(requirements.ass_logical_id, "subtitle_ass")
        self.assertEqual(requirements.font_logical_ids, ("subtitle_font_0000",))
        self.assertEqual(requirements.font_families, ("Anton",))
        self.assertEqual(validated["manifest"][2]["role"], "subtitle_ass")

    def test_font_id_order_extension_role_and_singleton_are_closed(self):
        cases = []

        packet = build_packet()
        packet["recipe"]["operations"][0]["fonts"][0]["asset"]["logical_id"] = "subtitle_font_0001"
        cases.append(packet)

        packet = build_packet()
        packet["recipe"]["operations"].append(copy.deepcopy(packet["recipe"]["operations"][0]))
        cases.append(packet)

        packet = build_packet()
        packet["recipe"]["operations"][0]["contract"] = "studio.render.subtitle.v2"
        cases.append(packet)

        packet = build_packet()
        packet["recipe"]["operations"][0]["ass"]["logical_id"] = "ass"
        cases.append(packet)

        packet = build_packet()
        packet["recipe"]["operations"][0]["fonts"][0]["family"] = "../Anton"
        cases.append(packet)

        packet = build_packet()
        packet["recipe"]["operations"][0]["extra"] = None
        cases.append(packet)

        packet = build_packet()
        packet["recipe"]["operations"][0].pop("fonts")
        cases.append(packet)

        packet = build_packet()
        packet["manifest"][3]["safe_name"] = "subtitle_font_0000.ass"
        cases.append(packet)

        packet = build_packet()
        packet["manifest"][3]["role"] = "logo"
        cases.append(packet)

        packet = build_packet()
        packet["recipe"]["operations"][0]["fonts"][0]["sha256"] = "0" * 64
        cases.append(packet)

        for packet in cases:
            with self.subTest(operation=packet["recipe"]["operations"]):
                self.assert_invalid(packet)

    def test_stage_order_and_missing_manifest_bijection_are_closed(self):
        packet = build_packet()
        packet["recipe"]["operations"] = [
            {"kind": "logo", "asset": {"type": "asset_ref", "logical_id": "logo"},
             "opacity": 1, "scale": 0.1, "anchor": "center", "margin_px": 10,
             "gate": "always"},
            *packet["recipe"]["operations"],
        ]
        packet["manifest"].append({
            "logical_id": "logo", "safe_name": "logo.png", "role": "logo",
            "sha256": hashlib.sha256(b"logo").hexdigest(), "size_bytes": 4,
        })
        packet["manifest"].sort(key=lambda entry: entry["logical_id"])
        self.assert_invalid(packet, "stage order")

        packet = build_packet()
        packet["manifest"] = [entry for entry in packet["manifest"]
                              if entry["logical_id"] != "subtitle_font_0000"]
        self.assert_invalid(packet, "bijection")


class SubtitleCompilerTests(unittest.TestCase):
    def test_libass_filter_with_private_paths_and_no_dropping_tier(self):
        packet = build_packet()
        with tempfile.TemporaryDirectory() as directory:
            assets, fonts_dir = stage_files(directory, packet, fonts=[("Anton", ttf())])
            plan = compile_subtitle(packet, assets, fonts_dir)
        graph = plan.tiers[0].argv[plan.tiers[0].argv.index("-filter_complex") + 1]
        self.assertIn("ass=filename=" + _filter_path(assets["subtitle_ass"]), graph)
        self.assertIn("fontsdir=" + _filter_path(fonts_dir), graph)
        self.assertIn("subtitles", plan.requested_burn_ins)
        self.assertFalse(any(tier.drops_burned_in_features for tier in plan.tiers))
        self.assertEqual([logical_id for logical_id, _ in plan.input_uses], ["base", "audio"])

    def test_subtitle_scope_preflight_and_bounds_are_enforced(self):
        packet = build_packet()
        with tempfile.TemporaryDirectory() as directory:
            assets, fonts_dir = stage_files(directory, packet, fonts=[("Anton", ttf())])
            with self.assertRaises(UnsupportedFinalRenderCombination) as raised:
                compile_final_render_packet(raw(packet), assets, OUTPUT)
            self.assertEqual(raised.exception.reason, "subtitle_profile_not_allowed")
            preflight = compile_final_render_packet(
                raw(packet), assets, OUTPUT, subtitles_allowed=True)
            self.assertTrue(preflight.tiers)
            with self.assertRaisesRegex(ValueError, "bounds"):
                compile_subtitle(packet, assets, fonts_dir, subtitle_limits=None)
            for key, value in (("max_subtitle_fonts", 0),
                               ("max_subtitle_font_bytes", True),
                               ("max_subtitle_ass_bytes", "4")):
                with self.subTest(key=key):
                    limits = dict(LIMITS, **{key: value})
                    with self.assertRaisesRegex(ValueError, "bounds"):
                        compile_subtitle(packet, assets, fonts_dir, subtitle_limits=limits)
            limits = dict(LIMITS, max_subtitle_ass_bytes=len(ASS) - 1)
            with self.assertRaisesRegex(ValueError, "exceeds its bound"):
                compile_subtitle(packet, assets, fonts_dir, subtitle_limits=limits)
            limits = dict(LIMITS, max_subtitle_font_bytes=len(ttf()) - 1)
            with self.assertRaisesRegex(ValueError, "exceeds its bound"):
                compile_subtitle(packet, assets, fonts_dir, subtitle_limits=limits)

    def test_actual_bytes_hashes_paths_and_font_family_are_verified(self):
        packet = build_packet()
        with tempfile.TemporaryDirectory() as directory:
            assets, fonts_dir = stage_files(directory, packet, fonts=[("Anton", ttf())])
            changed = dict(assets)
            changed["subtitle_ass"] = str(Path(directory) / "other.ass")
            Path(changed["subtitle_ass"]).write_bytes(
                ASS.replace("MATCHES", "MATCHED").encode("utf-8"))
            with self.assertRaisesRegex(ValueError, "sha256"):
                compile_subtitle(packet, changed, fonts_dir)
            missing = dict(assets)
            missing["subtitle_ass"] = str(Path(directory) / "missing.ass")
            with self.assertRaisesRegex(ValueError, "unreadable"):
                compile_subtitle(packet, missing, fonts_dir)
            outside = dict(assets)
            outside["subtitle_font_0000"] = str(Path(directory) / "font.ttf")
            Path(outside["subtitle_font_0000"]).write_bytes(ttf())
            with self.assertRaisesRegex(ValueError, "outside"):
                compile_subtitle(packet, outside, fonts_dir)
            wrong_ass = ASS.replace("Default,Anton", "Default,Serif")
            wrong_family = build_packet(ass=wrong_ass, fonts=(("Serif", ttf("Anton")),))
            wrong_assets, wrong_dir = stage_files(
                directory + "/2", wrong_family, ass=wrong_ass,
                fonts=[("Serif", ttf("Anton"))])
            with self.assertRaisesRegex(ValueError, "does not match"):
                compile_subtitle(wrong_family, wrong_assets, wrong_dir)

    def test_ass_external_references_and_closure_are_rejected(self):
        cases = (
            ASS.replace("WrapStyle: 2", "Video File: outside.mp4"),
            ASS.replace("[Events]", "[Embedded Fonts]"),
            ASS.replace("PlayResX: 1280", "PlayResX: 1920"),
            ASS.replace("Style: Default,Anton", "Style: Default,Other"),
            ASS.replace("0:00:05.80", "0:00:07.00"),
            ASS.split("[Events]")[0] + "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n",
        )
        for index, ass in enumerate(cases):
            with self.subTest(index=index):
                packet = build_packet(ass=ass)
                with tempfile.TemporaryDirectory() as directory:
                    assets, fonts_dir = stage_files(directory, packet, ass=ass,
                                                    fonts=[("Anton", ttf())])
                    with self.assertRaises(ValueError):
                        compile_subtitle(packet, assets, fonts_dir)

    def test_font_count_bound_and_combined_late_label_plan(self):
        packet = build_packet(fonts=(("Anton", ttf()), ("Second", ttf("Second"))))
        with tempfile.TemporaryDirectory() as directory:
            assets, fonts_dir = stage_files(
                directory, packet, fonts=[("Anton", ttf()), ("Second", ttf("Second"))])
            with self.assertRaisesRegex(ValueError, "font count"):
                compile_subtitle(packet, assets, fonts_dir,
                                 subtitle_limits=dict(LIMITS, max_subtitle_fonts=1))
            packet["recipe"]["operations"].append({
                "kind": "late_label_stage", "contract": "studio.render.late-label.v3",
                "cue_id": "cue1", "enable": {"start_sec": 0.4, "end_sec": 1.5},
                "placement": {"x_px": 10, "y_px": 10},
                "motion": {"kind": "none", "entrance_sec": 0.3},
                "label": {"asset": {"type": "asset_ref", "logical_id": "cue_0001_label"}},
                "reveal": None, "line": None, "line_reveal": None,
            })
            packet["manifest"].append({
                "logical_id": "cue_0001_label", "safe_name": "cue_0001_label.png",
                "role": "cue_label", "sha256": hashlib.sha256(b"png").hexdigest(),
                "size_bytes": 3,
            })
            packet["manifest"].sort(key=lambda entry: entry["logical_id"])
            seal(packet)
            assets["cue_0001_label"] = "/dev/fd/10"
            plan = compile_subtitle(packet, assets, fonts_dir, late_labels_allowed=True)
            graph = plan.tiers[0].argv[plan.tiers[0].argv.index("-filter_complex") + 1]
            self.assertLess(graph.index("ass=filename="), graph.index("overlay="))
            self.assertIn("subtitles", plan.requested_burn_ins)

    def test_filter_path_escaping(self):
        self.assertEqual(_filter_path("/tmp/a,b:c'[d];e\\f"),
                         "/tmp/a\\,b\\:c\\'\\[d\\]\\;e\\\\f")


class SubtitleProfileDescriptorTests(unittest.TestCase):
    def test_frozen_v3_sources_keep_the_immutable_late_label_pins(self):
        from components.hub.backend.render_late_label_v3 import final_render_compiler
        self.assertTrue(callable(final_render_compiler.compile_final_render_packet))
        root = HERE.parent
        descriptor = json.loads(
            (root / "packaging/render-authority-late-label/COMPILER.json").read_bytes())
        for key, name in (
            ("final_render_compiler_sha256", "final_render_compiler.py"),
            ("render_contract_sha256", "render_contract.py"),
            ("render_hash_sha256", "render_hash_v1.py"),
        ):
            frozen = root / "components/hub/backend/render_late_label_v3" / name
            self.assertEqual(descriptor[key], hashlib.sha256(frozen.read_bytes()).hexdigest())

    def test_frozen_v4_sources_keep_the_immutable_subtitle_pins(self):
        from components.hub.backend.render_subtitle_v4 import final_render_compiler
        self.assertTrue(callable(final_render_compiler.compile_final_render_packet))
        root = HERE.parent
        descriptor = json.loads(
            (root / "packaging/render-authority-subtitle/COMPILER.json").read_bytes())
        for key, name in (
            ("final_render_compiler_sha256", "final_render_compiler.py"),
            ("render_contract_sha256", "render_contract.py"),
            ("render_hash_sha256", "render_hash_v1.py"),
        ):
            frozen = root / "components/hub/backend/render_subtitle_v4" / name
            self.assertEqual(descriptor[key], hashlib.sha256(frozen.read_bytes()).hexdigest())

    COMPILER = {
        "format": "studio.render.compiler.v1",
        "implementation": "hub-final-render-subtitle-v1",
        "python_runtime_sha256": "6" * 64, "render_hash_sha256": "7" * 64,
        "render_contract_sha256": "8" * 64, "final_render_compiler_sha256": "9" * 64,
    }
    BOUNDS = {
        "bounds_version": 2, "canvas_range": {"max_edge": 3840, "max_pixels": 8294400},
        "working_fps": [24, 25, 30, 60], "max_duration_sec": 10800,
        "max_operations": 32, "max_assets": 32, "max_asset_bytes": 8589934592,
        "max_total_input_bytes": 17179869184, "max_pixel_frames": 5374771200000,
        "scratch_reserve_bytes": 17179869184, "output_limit_bytes": 8589934592,
        "process_tree_rss_limit_bytes": 10737418240, "cumulative_wall_seconds": 14400,
        **LIMITS,
    }
    PROFILE = {
        "format": "studio.render.profile.v4",
        "implementation": "story-classic-subtitle-v1",
        "recipe_namespace": "studio.render.final.v1",
        "compiler_artifact_sha256": "5" * 64, "tools_artifact_sha256": "4" * 64,
        "combination_set": "retained-classic-subtitle-candidate-v1",
        "tier_policy": "story-final-chain-v1", "output_policy": "story-final-output-v1",
        "execution_bounds": BOUNDS,
        "hardware_qualification_record_sha256": None,
        "fidelity_record_sha256": None, "production_eligible": False,
    }

    def test_v4_profile_and_subtitle_compiler_are_exact_nonproduction(self):
        compiler = validate_render_compiler_descriptor(raw(self.COMPILER))
        profile = validate_render_profile_descriptor(raw(self.PROFILE))
        self.assertEqual(compiler.digest, hashlib.sha256(raw(self.COMPILER)).hexdigest())
        self.assertEqual(profile.digest, hashlib.sha256(raw(self.PROFILE)).hexdigest())
        self.assertEqual(profile.format, "studio.render.profile.v4")
        self.assertEqual(profile.implementation, "story-classic-subtitle-v1")

    def test_v4_requires_the_three_subtitle_bounds_and_production_false(self):
        for key in LIMITS:
            value = copy.deepcopy(self.PROFILE)
            value["execution_bounds"].pop(key)
            with self.subTest(key=key):
                with self.assertRaisesRegex(ValueError, "missing or unknown"):
                    validate_render_profile_descriptor(raw(value))
        for value in (dict(self.PROFILE, production_eligible=True),
                      dict(self.PROFILE, implementation="other"),
                      dict(self.PROFILE, combination_set="other"),
                      dict(self.PROFILE, execution_bounds=None)):
            with self.assertRaisesRegex(ValueError, "Invalid render profile"):
                validate_render_profile_descriptor(raw(value))


if __name__ == "__main__":
    unittest.main()
