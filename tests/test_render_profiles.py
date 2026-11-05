from __future__ import annotations

import copy
import hashlib
import json
import sys
import unittest
from dataclasses import FrozenInstanceError, fields
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from components.hub.backend import render_profiles
from components.hub.backend.render_hash_v1 import render_hash
from components.hub.backend.render_profiles import (
    DescriptorMatch,
    match_render_profile_dependencies,
    validate_render_compiler_descriptor,
    validate_render_profile_descriptor,
    validate_render_tool_descriptor,
)

HERE = Path(__file__).resolve().parent
PACKAGED_PROFILE_PATH = (
    HERE.parent / "packaging" / "render-authority" / "PROFILE.json")
FIXTURE_PATH = HERE / "fixtures" / "final-render-compiler-v1.json"
FIXTURE_BYTES = FIXTURE_PATH.read_bytes()
FIXTURE = json.loads(FIXTURE_BYTES)
FIXTURE_SHA256 = "923bff7b3955f69b0fe9b4e140f56eedb05c5c8fa2dd93e7d249ae8d67b848e7"

TOOL_RAW = b'{"format":"studio.render.tools.v1","platform":"macos-arm64","ffmpeg_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","ffmpeg_size_bytes":132141880,"ffprobe_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","ffprobe_size_bytes":131770248,"architecture_evidence_sha256":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","minimum_os_evidence_sha256":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd","linked_libraries_evidence_sha256":"eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee","build_configuration_evidence_sha256":"ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff","capability_inventory_evidence_sha256":"0000000000000000000000000000000000000000000000000000000000000000"}'
TOOL_SHA256 = "b5d57f87c774baf14d6f8baa170cce60cd08cd505cef0d2b47e6651a11822ded"

COMPILER_RAW = b'{"format":"studio.render.compiler.v1","implementation":"hub-final-render-v1","python_runtime_sha256":"6666666666666666666666666666666666666666666666666666666666666666","render_hash_sha256":"9841b99b2f05fa2ccf045c3f5c391895cad36f6991e1ac61711d8e504a3d45d7","render_contract_sha256":"bb1d0fe51c935a098e9c877312c364d0843cb72e9ae2b467f992dc5bc7692c44","final_render_compiler_sha256":"0677e899141f27f3772c53987d2849dfdbee2e8080e72ea7b2a00c2434e06fb9"}'
COMPILER_SHA256 = "5ba755667279763e9ffe92eb4a3ad2e3d02bebb68f7c71a120db053b380f7c99"

# Structural test bounds only. This candidate remains explicitly non-production.
PROFILE_RAW = b'{"format":"studio.render.profile.v1","implementation":"story-classic-no-font-v1","recipe_namespace":"studio.render.final.v1","compiler_artifact_sha256":"5ba755667279763e9ffe92eb4a3ad2e3d02bebb68f7c71a120db053b380f7c99","tools_artifact_sha256":"b5d57f87c774baf14d6f8baa170cce60cd08cd505cef0d2b47e6651a11822ded","combination_set":"retained-classic-no-font-candidate-v1","tier_policy":"story-final-chain-v1","output_policy":"story-final-output-v1","execution_bounds":{"canvas_pairs":[{"width":128,"height":96}],"working_fps":[5],"max_duration_sec":2.5,"max_operations":9,"max_assets":6,"max_asset_bytes":26,"max_total_input_bytes":133,"max_pixel_frames":122880,"scratch_reserve_bytes":1,"output_limit_bytes":1,"process_tree_rss_limit_bytes":1,"cumulative_wall_seconds":1},"hardware_qualification_record_sha256":null,"fidelity_record_sha256":null,"production_eligible":false}'
PROFILE_SHA256 = "6119a40a9707d12ca5e0a9ad345d0dbba68402e87f14770fd5b7495a3154de2d"


def raw(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def changed(payload, update):
    value = json.loads(payload)
    update(value)
    return raw(value)


def packet_raw(name="f0_base_audio", profile_digest=PROFILE_SHA256, update=None):
    packet = copy.deepcopy(FIXTURE[name]["packet"])
    packet["recipe"]["profile_digest"] = profile_digest
    if update is not None:
        update(packet)
    packet["recipe_digest"] = render_hash("recipe", packet["recipe"])
    packet["manifest_digest"] = render_hash("manifest", packet["manifest"])
    packet["packet_digest"] = render_hash("packet", {
        "recipe_digest": packet["recipe_digest"],
        "manifest_digest": packet["manifest_digest"],
    })
    return raw(packet)


class RenderProfileTests(unittest.TestCase):
    def assert_invalid(self, validator, payload):
        with self.assertRaisesRegex(ValueError, "Invalid render .* descriptor"):
            validator(payload)

    def validated(self, profile_raw=PROFILE_RAW, tool_raw=TOOL_RAW,
                  compiler_raw=COMPILER_RAW):
        return (
            validate_render_profile_descriptor(profile_raw),
            validate_render_tool_descriptor(tool_raw),
            validate_render_compiler_descriptor(compiler_raw),
        )

    def test_valid_descriptors_have_literal_raw_byte_identity(self):
        self.assertEqual(hashlib.sha256(FIXTURE_BYTES).hexdigest(), FIXTURE_SHA256)
        self.assertEqual(hashlib.sha256(TOOL_RAW).hexdigest(), TOOL_SHA256)
        self.assertEqual(hashlib.sha256(COMPILER_RAW).hexdigest(), COMPILER_SHA256)
        self.assertEqual(hashlib.sha256(PROFILE_RAW).hexdigest(), PROFILE_SHA256)
        profile, tools, compiler = self.validated()
        self.assertEqual(tools.digest, TOOL_SHA256)
        self.assertEqual(compiler.digest, COMPILER_SHA256)
        self.assertEqual(profile.digest, PROFILE_SHA256)
        self.assertEqual(profile.tools_artifact_sha256, TOOL_SHA256)
        self.assertEqual(profile.compiler_artifact_sha256, COMPILER_SHA256)
        self.assertEqual(profile.format, "studio.render.profile.v1")
        self.assertEqual(profile.implementation, "story-classic-no-font-v1")
        with self.assertRaises(FrozenInstanceError):
            tools.digest = "0" * 64

    def test_raw_decoder_refuses_nonbytes_bom_utf8_duplicates_depth_and_size(self):
        duplicate = TOOL_RAW.replace(b'{"format":', b'{"format":"studio.render.tools.v1","format":', 1)
        deep = b"[" * 34 + b"0" + b"]" * 34
        oversized = b" " * (1024 * 1024 + 1)
        for payload in (TOOL_RAW.decode(), b"\xef\xbb\xbf" + TOOL_RAW,
                        b'{"format":"\xff"}', duplicate, deep, oversized):
            self.assert_invalid(validate_render_tool_descriptor, payload)

    def test_tool_descriptor_refuses_shape_hash_and_noninteger_size(self):
        mutations = (
            lambda value: value.update(extra=None),
            lambda value: value.pop("platform"),
            lambda value: value.update(platform="macos-arm64\0"),
            lambda value: value.update(ffmpeg_sha256="A" * 64),
            lambda value: value.update(ffmpeg_sha256="a" * 63),
            lambda value: value.update(ffmpeg_size_bytes=True),
            lambda value: value.update(ffmpeg_size_bytes=1.0),
            lambda value: value.update(ffmpeg_size_bytes=0),
            lambda value: value.update(ffmpeg_size_bytes=9007199254740992),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.assert_invalid(validate_render_tool_descriptor,
                                    changed(TOOL_RAW, mutation))

    def test_compiler_descriptor_refuses_shape_literals_and_hashes(self):
        mutations = (
            lambda value: value.update(extra=None),
            lambda value: value.pop("python_runtime_sha256"),
            lambda value: value.update(format="studio.render.compiler.v2"),
            lambda value: value.update(implementation="other"),
            lambda value: value.update(render_contract_sha256="B" * 64),
            lambda value: value.update(final_render_compiler_sha256="0" * 63),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.assert_invalid(validate_render_compiler_descriptor,
                                    changed(COMPILER_RAW, mutation))

    def test_retained_v1_sources_match_the_unchanged_packaged_compiler(self):
        root = HERE.parent
        descriptor = json.loads((root / "packaging/render-authority/COMPILER.json").read_bytes())
        legacy = root / "components/hub/backend/render_legacy_v1"
        for name, key in (("render_contract.py", "render_contract_sha256"),
                          ("final_render_compiler.py", "final_render_compiler_sha256"),
                          ("render_hash_v1.py", "render_hash_sha256")):
            with self.subTest(name=name):
                self.assertEqual(hashlib.sha256((legacy / name).read_bytes()).hexdigest(),
                                 descriptor[key])

    def test_late_label_descriptors_keep_exact_versions_and_digest_identity(self):
        compiler_value = json.loads(COMPILER_RAW)
        compiler_value["implementation"] = "hub-final-render-late-label-v3"
        compiler_bytes = raw(compiler_value)
        compiler = validate_render_compiler_descriptor(compiler_bytes)
        self.assertEqual(compiler.digest, hashlib.sha256(compiler_bytes).hexdigest())
        profile_value = json.loads(PROFILE_RAW)
        profile_value.update(
            format="studio.render.profile.v3",
            implementation="story-classic-late-label-v3",
            combination_set="retained-classic-late-label-candidate-v3",
            compiler_artifact_sha256=compiler.digest,
        )
        profile_bytes = raw(profile_value)
        profile = validate_render_profile_descriptor(profile_bytes)
        self.assertEqual(profile.digest, hashlib.sha256(profile_bytes).hexdigest())
        self.assertEqual(profile.format, "studio.render.profile.v3")
        self.assertEqual(profile.implementation, "story-classic-late-label-v3")
        self.assertTrue(match_render_profile_dependencies(
            packet_raw(profile_digest=profile.digest), profile,
            validate_render_tool_descriptor(TOOL_RAW), compiler).descriptor_match)
        for key, invalid in (("format", "studio.render.profile.v1"),
                             ("implementation", "story-classic-no-font-v1"),
                             ("combination_set", "retained-classic-no-font-candidate-v1"),
                             ("production_eligible", True)):
            with self.subTest(key=key):
                self.assert_invalid(validate_render_profile_descriptor,
                                    raw(dict(profile_value, **{key: invalid})))

    def test_profile_refuses_unknown_missing_policy_and_production_true(self):
        mutations = (
            lambda value: value.update(extra=None),
            lambda value: value.pop("output_policy"),
            lambda value: value.update(format="studio.render.profile.v2"),
            lambda value: value.update(implementation="other"),
            lambda value: value.update(recipe_namespace="studio.render.other.v1"),
            lambda value: value.update(combination_set="invented"),
            lambda value: value.update(tier_policy="invented"),
            lambda value: value.update(output_policy="invented"),
            lambda value: value.update(production_eligible=True),
            lambda value: value.update(production_eligible=0),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.assert_invalid(validate_render_profile_descriptor,
                                    changed(PROFILE_RAW, mutation))

    def test_profile_refuses_malformed_nullable_hashes_and_accepts_explicit_nulls(self):
        for key in ("compiler_artifact_sha256", "tools_artifact_sha256",
                    "hardware_qualification_record_sha256", "fidelity_record_sha256"):
            for invalid in ("A" * 64, "0" * 63, False):
                with self.subTest(key=key, invalid=invalid):
                    self.assert_invalid(
                        validate_render_profile_descriptor,
                        changed(PROFILE_RAW, lambda value, key=key, invalid=invalid:
                                value.update({key: invalid})),
                    )
        nullable = changed(PROFILE_RAW, lambda value: value.update(
            compiler_artifact_sha256=None,
            tools_artifact_sha256=None,
            execution_bounds=None,
            hardware_qualification_record_sha256=None,
            fidelity_record_sha256=None,
        ))
        profile = validate_render_profile_descriptor(nullable)
        self.assertIsNone(profile.compiler_artifact_sha256)
        self.assertIsNone(profile.tools_artifact_sha256)

    def test_bounds_are_recursively_closed_and_use_actual_positive_numbers(self):
        bounds_keys = (
            "max_operations", "max_assets", "max_asset_bytes", "max_total_input_bytes",
            "max_pixel_frames", "scratch_reserve_bytes", "output_limit_bytes",
            "process_tree_rss_limit_bytes", "cumulative_wall_seconds",
        )
        mutations = [
            lambda value: value["execution_bounds"].update(extra=1),
            lambda value: value["execution_bounds"].pop("max_assets"),
            lambda value: value["execution_bounds"].update(canvas_pairs={}),
            lambda value: value["execution_bounds"]["canvas_pairs"][0].update(extra=1),
            lambda value: value["execution_bounds"]["canvas_pairs"][0].update(width=True),
            lambda value: value["execution_bounds"]["canvas_pairs"][0].update(width=1.0),
            lambda value: value["execution_bounds"]["canvas_pairs"][0].update(width=0),
            lambda value: value["execution_bounds"].update(working_fps=[5.0]),
            lambda value: value["execution_bounds"].update(working_fps=[False]),
            lambda value: value["execution_bounds"].update(max_duration_sec=True),
            lambda value: value["execution_bounds"].update(max_duration_sec=0),
            lambda value: value["execution_bounds"].update(max_duration_sec=float("nan")),
        ]
        for key in bounds_keys:
            mutations.extend((
                lambda value, key=key: value["execution_bounds"].update({key: True}),
                lambda value, key=key: value["execution_bounds"].update({key: 1.0}),
                lambda value, key=key: value["execution_bounds"].update({key: 0}),
                lambda value, key=key: value["execution_bounds"].update(
                    {key: 9007199254740992}),
            ))
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.assert_invalid(validate_render_profile_descriptor,
                                    changed(PROFILE_RAW, mutation))

    def test_match_checks_only_packet_profile_and_exact_dependency_digests(self):
        profile, tools, compiler = self.validated()
        for name in ("f0_base_audio", "f1_derived_no_font"):
            with self.subTest(name=name):
                result = match_render_profile_dependencies(
                    packet_raw(name), profile, tools, compiler,
                )
                self.assertEqual(result, DescriptorMatch(True, "matched", PROFILE_SHA256))

        wrong_profile = match_render_profile_dependencies(
            packet_raw(profile_digest="1" * 64), profile, tools, compiler,
        )
        self.assertEqual(wrong_profile.reason, "profile_digest")
        changed_tools = validate_render_tool_descriptor(changed(
            TOOL_RAW, lambda value: value.update(ffmpeg_sha256="1" * 64)
        ))
        changed_compiler = validate_render_compiler_descriptor(changed(
            COMPILER_RAW, lambda value: value.update(python_runtime_sha256="2" * 64)
        ))
        self.assertEqual(match_render_profile_dependencies(
            packet_raw(), profile, changed_tools, compiler).reason, "tool_artifact")
        self.assertEqual(match_render_profile_dependencies(
            packet_raw(), profile, tools, changed_compiler).reason, "compiler_artifact")

    def test_whitespace_changes_identity_and_packet_claim_must_follow(self):
        whitespace_raw = b" " + PROFILE_RAW
        self.assertEqual(hashlib.sha256(whitespace_raw).hexdigest(),
                         "97ee9e144ba263c3e0e84cbc9c9edbe9525e30566be5968ba39f21a8e855885a")
        whitespace_profile = validate_render_profile_descriptor(whitespace_raw)
        _, tools, compiler = self.validated()
        self.assertEqual(match_render_profile_dependencies(
            packet_raw(), whitespace_profile, tools, compiler).reason, "profile_digest")
        self.assertTrue(match_render_profile_dependencies(
            packet_raw(profile_digest=whitespace_profile.digest),
            whitespace_profile, tools, compiler).descriptor_match)

    def test_match_does_not_claim_encode_or_hardware_compatibility(self):
        profile, tools, compiler = self.validated()
        hardware_packet = packet_raw(update=lambda packet:
            packet["recipe"]["encode"].update(requested_effort="hardware"))
        result = match_render_profile_dependencies(hardware_packet, profile, tools, compiler)
        self.assertEqual(result, DescriptorMatch(True, "matched", PROFILE_SHA256))
        self.assertEqual([field.name for field in fields(result)],
                         ["descriptor_match", "reason", "profile_digest"])

        hardware_profile_raw = changed(PROFILE_RAW, lambda value: value.update(
            hardware_qualification_record_sha256="3" * 64,
        ))
        hardware_profile = validate_render_profile_descriptor(hardware_profile_raw)
        result = match_render_profile_dependencies(
            packet_raw(profile_digest=hardware_profile.digest),
            hardware_profile, tools, compiler,
        )
        self.assertTrue(result.descriptor_match)
        self.assertNotIn("qualified", result.reason)
        self.assertNotIn("compatible", result.reason)

    def test_match_reuses_packet_syntax_and_leaves_input_bytes_unchanged(self):
        profile, tools, compiler = self.validated()
        payload = packet_raw()
        before = bytes(payload)
        match_render_profile_dependencies(payload, profile, tools, compiler)
        self.assertEqual(payload, before)

        invalid = packet_raw(update=lambda packet:
            packet["recipe"].update(operations=[{"kind": "subtitles"}]))
        with self.assertRaisesRegex(ValueError, "Invalid render packet"):
            match_render_profile_dependencies(invalid, profile, tools, compiler)


class PackagedRenderProfileTests(unittest.TestCase):
    def test_v2_bounds_are_closed_and_reject_unknown_versions(self):
        original = PACKAGED_PROFILE_PATH.read_bytes()
        mutations = (
            lambda b: b.update(bounds_version=1),
            lambda b: b.update(bounds_version=3),
            lambda b: b.update(bounds_version=True),
            lambda b: b.update(bounds_version=2.0),
            lambda b: b.pop("bounds_version"),
            lambda b: b.update(canvas_pairs=[]),
            lambda b: b["canvas_range"].update(extra=1),
            lambda b: b["canvas_range"].pop("max_edge"),
            lambda b: b.update(canvas_range=[]),
            lambda b: b["canvas_range"].update(max_edge=3841),
            lambda b: b["canvas_range"].update(max_edge=True),
            lambda b: b["canvas_range"].update(max_pixels=0),
            lambda b: b["canvas_range"].update(max_pixels=8294400.0),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                value = json.loads(original)
                mutate(value["execution_bounds"])
                with self.assertRaisesRegex(ValueError, "Invalid render profile descriptor"):
                    validate_render_profile_descriptor(raw(value))

    def test_v1_reader_shape_explicitly_refuses_v2_bounds(self):
        bounds = json.loads(PACKAGED_PROFILE_PATH.read_bytes())["execution_bounds"]
        # Freeze the V1 reader's closed key set from the literal V1 descriptor above.
        with self.assertRaisesRegex(ValueError, "execution_bounds has missing or unknown fields"):
            render_profiles._exact(bounds, json.loads(PROFILE_RAW)["execution_bounds"],
                                   "profile", "execution_bounds")
        # V2 readers still accept frozen V1 descriptors under their original byte identity.
        self.assertEqual(validate_render_profile_descriptor(PROFILE_RAW).digest, PROFILE_SHA256)

    def test_candidate_envelope_covers_three_hours_of_4k60(self):
        raw = PACKAGED_PROFILE_PATH.read_bytes()
        validate_render_profile_descriptor(raw)
        value = json.loads(raw)
        bounds = value["execution_bounds"]

        self.assertFalse(value["production_eligible"])
        self.assertEqual(bounds["bounds_version"], 2)
        self.assertNotIn("canvas_pairs", bounds)
        self.assertEqual(bounds["canvas_range"], {"max_edge": 3840, "max_pixels": 8294400})
        self.assertEqual(bounds["working_fps"], [24, 25, 30, 60])
        self.assertEqual(bounds["max_duration_sec"], 10800)
        self.assertEqual(bounds["max_pixel_frames"], 3840 * 2160 * 60 * 10800)
        self.assertEqual(bounds["max_operations"], 8)
        self.assertEqual(bounds["max_assets"], 4)
        self.assertEqual(bounds["max_asset_bytes"], 8589934592)
        self.assertEqual(bounds["max_total_input_bytes"], 17179869184)
        # The experimental resource fences stay unchanged until a measured receipt.
        self.assertEqual(bounds["cumulative_wall_seconds"], 14400)
        self.assertEqual(bounds["process_tree_rss_limit_bytes"], 10737418240)
        self.assertEqual(bounds["scratch_reserve_bytes"], 17179869184)
        self.assertEqual(bounds["output_limit_bytes"], 8589934592)


if __name__ == "__main__":
    unittest.main()
