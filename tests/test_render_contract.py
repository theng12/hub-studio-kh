from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from components.hub.backend.render_contract import validate_render_packet_syntax
from components.hub.backend.render_hash_v1 import render_hash

RECIPE = {
    "namespace": "studio.render.final.v1",
    "profile_digest": "1" * 64,
    "base_video": {"type": "asset_ref", "logical_id": "base"},
    "premixed_audio": {"type": "asset_ref", "logical_id": "audio"},
    "canvas": {"width": 1920, "height": 1080, "working_fps": 24},
    "target_duration_sec": 3,
    "effects_gate": {"start_sec": None, "end_sec": None},
    "operations": [],
    "encode": {
        "container": "mp4", "requested_codec": "h264", "requested_effort": "medium",
        "crf": 23, "quality_cpu_preset": "medium", "bitrate_kbps": None,
    },
}
MANIFEST = [
    {"logical_id": "audio", "safe_name": "audio.wav", "role": "premixed_audio",
     "sha256": "dc1518e49ba49e89bb9ebb58b0adc3185b5add1aa01ef094e05b207bf609e6dc", "size_bytes": 15},
    {"logical_id": "base", "safe_name": "base.mp4", "role": "base_video",
     "sha256": "be848bac42a9fb1f1bb2474f00ec7c1e19d12f58e319ff51110be59ad6c6ae6e", "size_bytes": 14},
]
EXPECTED = (
    "c5918595dc2edcc684db5e78c6766f758477e48719699d944669d968c456f9aa",
    "e55bbe75b574fa01ca1ae57be89aa249adb83bf5ce771116a81293fff4b48873",
    "068cae9bbe8f5cdbe22fcc24e057768332295d21ed4aed92e85b1da6b6ef7ec1",
)
FULL_PACKET_JSON = b'''{"recipe":{"namespace":"studio.render.final.v1","profile_digest":"2222222222222222222222222222222222222222222222222222222222222222","base_video":{"type":"asset_ref","logical_id":"base"},"premixed_audio":{"type":"asset_ref","logical_id":"audio"},"canvas":{"width":1920,"height":1080,"working_fps":5},"target_duration_sec":2.4,"effects_gate":{"start_sec":0.1,"end_sec":2.3},"operations":[{"kind":"overlay_layer","asset":{"type":"asset_ref","logical_id":"overlay_a"},"input_mode":{"kind":"loop_stream"},"opacity":0.75,"sizing":{"mode":"contain"},"placement":{"mode":"anchor","value":"center"},"time_range":{"start_sec":0.1,"end_sec":1.7},"chroma":{"color":"#00FF00","similarity":0.15,"blend":0.1}},{"kind":"overlay_layer","asset":{"type":"asset_ref","logical_id":"overlay_b"},"input_mode":{"kind":"loop_still","decoder":"png"},"opacity":0.6,"sizing":{"mode":"width_fraction","value":0.25},"placement":{"mode":"anchor","value":"lower-right"},"time_range":{"start_sec":0.2,"end_sec":1.6},"chroma":null},{"kind":"classic_color_grade","preset":"cinematic","brightness":0,"contrast":1,"saturation":1,"warmth":0,"tint":0},{"kind":"classic_vignette","strength":0.4},{"kind":"classic_film_grain","strength":7},{"kind":"aged_archive_film","strength":20},{"kind":"classic_letterbox","ratio":"1.85:1","mode":"fit","color":"black"},{"kind":"presentation_frame","inset_pct":4,"border_width":12,"corner_radius":20,"border_color":"#F2C14E","background_color":"#111C34","fit":"cover","background":{"kind":"asset","asset":{"type":"asset_ref","logical_id":"frame"},"input_mode":{"kind":"loop_still","decoder":"png"}}},{"kind":"logo","asset":{"type":"asset_ref","logical_id":"logo"},"opacity":0.8,"scale":0.2,"anchor":"top-left","margin_px":4,"gate":"effects_gate"}],"encode":{"container":"mp4","requested_codec":"h264","requested_effort":"medium","crf":23,"quality_cpu_preset":"ultrafast","bitrate_kbps":2500}},"manifest":[{"logical_id":"audio","safe_name":"audio.wav","role":"premixed_audio","sha256":"431fb6b4afe3745dd2b0c96fd383e252ae459c24de7f4a722f6f9754a1c10dc7","size_bytes":15},{"logical_id":"base","safe_name":"base.mp4","role":"base_video","sha256":"e1e674f0896e086c39e8535489ed8026f8bf2d8f60ddf8110c12dc2a54d9c924","size_bytes":14},{"logical_id":"frame","safe_name":"frame.png","role":"frame_background","sha256":"1ef4770443b0cb538224829844848e68ec38d0d4baf192da5c8af15b6ece81cc","size_bytes":15},{"logical_id":"logo","safe_name":"logo.png","role":"logo","sha256":"bc7af3a8de3d5ea42d8baa6a7b5a31f4c25431defe86ca752078791fcb750ef3","size_bytes":14},{"logical_id":"overlay_a","safe_name":"overlay_a.webm","role":"overlay","sha256":"da2523c2c5ed5e61e29d6bbff6bf1d8c128972b58a0ffe9c331d15c4b5668e5a","size_bytes":19},{"logical_id":"overlay_b","safe_name":"overlay_b.png","role":"overlay","sha256":"0bd38b60001015fb8e0fa8068a88b6ed779db8ea8a36d4903ef482307011236d","size_bytes":19}],"recipe_digest":"093a5fb343f21f10263c07cfc6254035af542bf5bf7424e107607349403844ad","manifest_digest":"1e642fd556e98b01858d2b2c4aba033c886075745fdf5d067463adae3d18f71d","packet_digest":"179693c8e260ae1c27dbc0a674da3925567eb17283456f2d797d858d7a1d4872"}'''

FRACTIONAL_FRAME_VECTORS = (
    {
        "name": "fractional-authored-values",
        "canvas": {"width": 1920, "height": 1080},
        "border_width": 12.25,
        "corner_radius": 20.5,
        "recipe_digest": "1c368a23bd7581204b25bf0bf4a54c6026d37b3297712f5a86399cd51c0e9ec5",
        "manifest_digest": "1e642fd556e98b01858d2b2c4aba033c886075745fdf5d067463adae3d18f71d",
        "packet_digest": "f0cad603d1cc811ca4d7be05a6c8f8076aa6a6880ec7ad76c1af2f2c9bbc9254",
    },
    {
        "name": "fractional-before-height-scale",
        "canvas": {"width": 960, "height": 540},
        "border_width": 2.51,
        "corner_radius": 20.5,
        "recipe_digest": "8f131e5c080762da9ce3be526420a9922c6bd635cc2edc942371d604c9b17385",
        "manifest_digest": "1e642fd556e98b01858d2b2c4aba033c886075745fdf5d067463adae3d18f71d",
        "packet_digest": "d2463199de9119765cb679e1a7e81c26b3f04e57fa5cc9a65a06fa2880cfe5a7",
    },
)


def packet():
    return {
        "recipe": copy.deepcopy(RECIPE), "manifest": copy.deepcopy(MANIFEST),
        "recipe_digest": EXPECTED[0], "manifest_digest": EXPECTED[1], "packet_digest": EXPECTED[2],
    }


def raw(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def rehash(value):
    value["recipe_digest"] = render_hash("recipe", value["recipe"])
    value["manifest_digest"] = render_hash("manifest", value["manifest"])
    value["packet_digest"] = render_hash("packet", {
        "recipe_digest": value["recipe_digest"], "manifest_digest": value["manifest_digest"],
    })


class RenderContractTests(unittest.TestCase):
    def assert_rejected(self, value):
        with self.assertRaisesRegex(ValueError, "Invalid render packet"):
            validate_render_packet_syntax(raw(value))

    def test_accepts_fractional_frame_values_without_rounding(self):
        for vector in FRACTIONAL_FRAME_VECTORS:
            value = json.loads(FULL_PACKET_JSON)
            value["recipe"]["canvas"].update(vector["canvas"])
            frame = next(operation for operation in value["recipe"]["operations"]
                         if operation["kind"] == "presentation_frame")
            frame["border_width"] = vector["border_width"]
            frame["corner_radius"] = vector["corner_radius"]
            value["recipe_digest"] = vector["recipe_digest"]
            value["manifest_digest"] = vector["manifest_digest"]
            value["packet_digest"] = vector["packet_digest"]
            self.assertEqual(render_hash("recipe", value["recipe"]), vector["recipe_digest"])
            self.assertEqual(render_hash("manifest", value["manifest"]), vector["manifest_digest"])
            self.assertEqual(render_hash("packet", {
                "recipe_digest": vector["recipe_digest"],
                "manifest_digest": vector["manifest_digest"],
            }), vector["packet_digest"])
            validated = validate_render_packet_syntax(raw(value))
            validated_frame = next(operation for operation in validated["recipe"]["operations"]
                                   if operation["kind"] == "presentation_frame")
            self.assertEqual(validated_frame["border_width"], vector["border_width"])
            self.assertEqual(validated_frame["corner_radius"], vector["corner_radius"])
            self.assertIsInstance(validated_frame["border_width"], float)
            self.assertIsInstance(validated_frame["corner_radius"], float)

        for key, invalid in (("border_width", 160.01), ("corner_radius", 300.01),
                             ("border_width", True)):
            value = json.loads(FULL_PACKET_JSON)
            frame = next(operation for operation in value["recipe"]["operations"]
                         if operation["kind"] == "presentation_frame")
            frame[key] = invalid
            rehash(value)
            self.assert_rejected(value)
        value = json.loads(FULL_PACKET_JSON)
        next(operation for operation in value["recipe"]["operations"]
             if operation["kind"] == "presentation_frame")["border_width"] = float("nan")
        self.assert_rejected(value)

        for kind, invalid in (("classic_film_grain", 7.5),
                              ("aged_archive_film", 20.5),
                              ("logo", 4.5)):
            value = json.loads(FULL_PACKET_JSON)
            operation = next(item for item in value["recipe"]["operations"] if item["kind"] == kind)
            operation["margin_px" if kind == "logo" else "strength"] = invalid
            rehash(value)
            self.assert_rejected(value)


    def test_accepts_paired_literal_body_and_frozen_digests(self):
        value = packet()
        self.assertEqual(render_hash("recipe", value["recipe"]), EXPECTED[0])
        self.assertEqual(render_hash("manifest", value["manifest"]), EXPECTED[1])
        self.assertEqual(render_hash("packet", {
            "recipe_digest": EXPECTED[0], "manifest_digest": EXPECTED[1],
        }), EXPECTED[2])
        self.assertEqual(validate_render_packet_syntax(raw(value)), value)
        full = json.loads(FULL_PACKET_JSON)
        self.assertEqual(validate_render_packet_syntax(FULL_PACKET_JSON), full)
        self.assertEqual(render_hash("recipe", full["recipe"]), full["recipe_digest"])
        self.assertEqual(render_hash("manifest", full["manifest"]), full["manifest_digest"])
        self.assertEqual(render_hash("packet", {
            "recipe_digest": full["recipe_digest"],
            "manifest_digest": full["manifest_digest"],
        }), full["packet_digest"])
        self.assertEqual(list(dict.fromkeys(
            operation["kind"] for operation in full["recipe"]["operations"]
        )), ["overlay_layer", "classic_color_grade", "classic_vignette",
             "classic_film_grain", "aged_archive_film", "classic_letterbox",
             "presentation_frame", "logo"])

    def test_rejects_raw_duplicate_bom_utf8_and_nonbytes(self):
        duplicate = raw(packet()).replace(
            b'{"namespace":',
            b'{"namespace":"studio.render.final.v1","namespace":',
            1,
        )
        for payload in (duplicate, b"\xef\xbb\xbf" + raw(packet()), b'{"x":"\xff"}'):
            with self.assertRaisesRegex(ValueError, "Invalid render packet"):
                validate_render_packet_syntax(payload)
        with self.assertRaisesRegex(ValueError, "Invalid render packet"):
            validate_render_packet_syntax(raw(packet()).decode())

    def test_rejects_shape_numeric_literal_reference_and_hash_drift(self):
        changes = (
            (lambda p: p["recipe"].update(extra=None), True),
            (lambda p: p["recipe"]["canvas"].update(width=True), True),
            (lambda p: p["recipe"]["canvas"].update(width=3), True),
            (lambda p: p["recipe"]["effects_gate"].update(start_sec=0), True),
            (lambda p: p["recipe"]["encode"].update(bitrate_kbps=499), True),
            (lambda p: p["manifest"].reverse(), True),
            (lambda p: p["manifest"][0].update(role="overlay"), True),
            (lambda p: p["manifest"][0].update(safe_name="../audio.wav"), True),
            (lambda p: p["manifest"][0].update(safe_name="audio\0.wav"), True),
            (lambda p: p["recipe"]["base_video"].update(logical_id="missing"), True),
            (lambda p: p.update(recipe_digest="0" * 64), False),
            (lambda p: p["recipe"].update(target_duration_sec=4), False),
        )
        for change, update in changes:
            value = packet()
            change(value)
            if update:
                rehash(value)
            self.assert_rejected(value)

    def test_rejects_unknown_operation_filter_and_unused_manifest(self):
        for operation in (
            {"kind": "subtitles"},
            {"kind": "overlay_layer", "filter_argv": ["movie=x"]},
        ):
            value = packet()
            value["recipe"]["operations"] = [operation]
            rehash(value)
            self.assert_rejected(value)
        value = packet()
        value["manifest"].append({
            "logical_id": "unused", "safe_name": "unused.png", "role": "overlay",
            "sha256": "3" * 64, "size_bytes": 1,
        })
        rehash(value)
        self.assert_rejected(value)


if __name__ == "__main__":
    unittest.main()
