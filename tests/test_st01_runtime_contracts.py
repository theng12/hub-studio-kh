"""Static integrity checks for ST-01's runtime input contract.

These tests intentionally prove only reproducible runtime inputs and import
surfaces.  A complete runtime install is still not model readiness or
permission to generate; Hub admission and model-package verification own that
later claim.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "research" / "st01-runtime-contracts.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ST01RuntimeContractTests(unittest.TestCase):
    def test_contract_records_clean_exact_read_only_source_inputs(self):
        payload = json.loads(CONTRACT.read_text(encoding="utf-8"))

        self.assertEqual(payload["schema"], "studiohub.st01-runtime-contracts.v1")
        records = {record["component"]: record for record in payload["source_inputs"]}
        self.assertEqual(
            set(records),
            {"hub", "image", "voice", "voice-adopted-component"},
        )
        references = [record for record in records.values() if record.get("read_only_reference")]
        self.assertEqual({record["component"] for record in references}, {"hub", "image", "voice"})
        for record in references:
            self.assertEqual(record["working_tree_status"], "clean")
            self.assertRegex(record["resolved_commit"], r"^[0-9a-f]{40}$")
            self.assertRegex(record["official_remote"], r"^https://github\.com/.+\.git$")
            self.assertRegex(record["official_check"], r"^[^\n]+$")

    def test_voice_runtime_subject_matches_adopted_component(self):
        payload = json.loads(CONTRACT.read_text(encoding="utf-8"))
        adoption = json.loads(
            (ROOT / "research" / "voice-adoption.json").read_text(encoding="utf-8")
        )
        source_inventory = json.loads(
            (ROOT / "research" / "source-checkouts.json").read_text(encoding="utf-8")
        )
        component_version = (ROOT / "components" / "voice" / "VERSION").read_text(
            encoding="utf-8"
        ).strip()
        adopted = next(
            record
            for record in payload["source_inputs"]
            if record["component"] == "voice-adopted-component"
        )
        voice = next(runtime for runtime in payload["runtimes"] if runtime["engine"] == "voice-mlx")

        self.assertEqual(adopted["resolved_commit"], adoption["commit"])
        self.assertEqual(adopted["version"], adoption["version"])
        self.assertEqual(component_version, adoption["version"])
        inventory_adopted = source_inventory["current_provenance"]["adopted_components"][0]
        self.assertEqual(inventory_adopted["commit"], adoption["commit"])
        self.assertEqual(inventory_adopted["version"], component_version)
        self.assertEqual(voice["contract_subject"]["adoption_commit"], adoption["commit"])
        self.assertEqual(voice["contract_subject"]["version"], component_version)

    def test_each_engine_records_a_locked_arm64_macos14_import_surface(self):
        payload = json.loads(CONTRACT.read_text(encoding="utf-8"))
        runtimes = {runtime["engine"]: runtime for runtime in payload["runtimes"]}

        for runtime in runtimes.values():
            lock_path = ROOT / runtime["lock_path"]
            self.assertTrue(lock_path.is_file(), lock_path)
            self.assertEqual(runtime["runtime_lock_sha256"], sha256(lock_path))
            self.assertEqual(runtime["platform"]["architecture"], "arm64")
            self.assertEqual(runtime["platform"]["minimum_macos"], "14.0")
            self.assertEqual(runtime["readiness"], "runtime-inputs-only")
            self.assertGreater(len(runtime["required_imports"]), 0)
            self.assertTrue(runtime["native_binary_floor_audit"])

    def test_every_shipped_engine_is_either_covered_here_or_declared_outside_it(self):
        """The engine vocabulary, not today's three entries.

        This assertion used to be `set(runtimes) == {"voice-mlx", "subtitle-mlx", "image-mflux"}`,
        which is the exact shape `AGENTS.md` warns about: three engines that already ship
        (`voice-vits`, `music-mlx`, `chat-mlx`) were outside it, so the test asserted the record's
        own contents rather than "every engine the catalog can route has a runtime story". Adding
        an engine therefore required hand-editing this line, and forgetting to left no trace.

        Now the record has to *partition* the Hub's engine table: each engine is covered by a
        locked import surface here, or named in `engines_outside_this_contract` with the record
        that does cover it. A new engine fails this test until someone decides which it is.
        """
        payload = json.loads(CONTRACT.read_text(encoding="utf-8"))
        covered = {runtime["engine"] for runtime in payload["runtimes"]}
        outside = {engine: record
                   for engine, record in payload.get("engines_outside_this_contract", {}).items()}
        self.assertEqual(
            covered & set(outside), set(),
            "an engine cannot be both covered by this contract and declared outside it")
        from components.hub.standalone import _ENGINE_ROUTE  # the Hub's own engine table

        self.assertEqual(
            covered | set(outside), set(_ENGINE_ROUTE),
            "an engine the catalog can route has no runtime story: give it an entry in "
            "research/st01-runtime-contracts.json's `runtimes`, or name it in "
            "`engines_outside_this_contract` with the record that covers it")
        for engine, record in outside.items():
            with self.subTest(engine=engine):
                self.assertTrue(record.get("reason"),
                                f"{engine} is declared outside this contract with no reason")
                self.assertTrue(
                    (ROOT / record.get("record", "")).is_file(),
                    f"{engine} points at {record.get('record')!r}, which is not a file")

    def test_image_contract_keeps_mflux_separate_and_pins_macos14_mlx_wheels(self):
        payload = json.loads(CONTRACT.read_text(encoding="utf-8"))
        image = next(runtime for runtime in payload["runtimes"] if runtime["engine"] == "image-mflux")
        lock = (ROOT / image["lock_path"]).read_text(encoding="utf-8")

        # The contract this protects is the macOS 14 floor and the arm64 wheel, not a particular
        # version. These three assertions used to hardcode mflux==0.17.5 and mlx-0.31.2, so the
        # engine bump for Z-Image failed them while nothing was actually wrong. The expected
        # versions now come from the contract record, which stays the single source of truth, and
        # the floor is still asserted explicitly.
        mflux_match = re.search(r"mflux (\d+\.\d+\.\d+)", image["provenance"]["mflux"])
        mlx_match = re.search(r"(\d+\.\d+\.\d+) macosx_14_0_arm64", image["provenance"]["mlx"])
        metal_match = re.search(r"(\d+\.\d+\.\d+) macosx_14_0_arm64", image["provenance"]["mlx_metal"])
        self.assertIsNotNone(mflux_match, "the contract must name the mflux version it pins")
        self.assertIsNotNone(mlx_match, "the contract must name a macOS 14 arm64 mlx wheel")
        self.assertIsNotNone(metal_match, "the contract must name a macOS 14 arm64 mlx-metal wheel")
        mflux_version, mlx_version = mflux_match.group(1), mlx_match.group(1)
        self.assertIn(f"mflux=={mflux_version}", lock)
        self.assertIn(f"mlx-{mlx_version}-cp312-cp312-macosx_14_0_arm64.whl#sha256=", lock)
        self.assertIn(f"mlx_metal-{mlx_version}-py3-none-macosx_14_0_arm64.whl#sha256=", lock)
        self.assertEqual(
            metal_match.group(1), mlx_version,
            "mlx and mlx-metal must ship the same version",
        )
        self.assertIn("mflux", image["required_imports"])
        self.assertIn("mlx.core", image["required_imports"])
        self.assertNotEqual(image["lock_path"], next(
            runtime["lock_path"] for runtime in payload["runtimes"] if runtime["engine"] == "voice-mlx"
        ))

    def test_contract_distinguishes_full_image_audit_and_qwen3_tts_base_loader(self):
        payload = json.loads(CONTRACT.read_text(encoding="utf-8"))
        image = next(runtime for runtime in payload["runtimes"] if runtime["engine"] == "image-mflux")
        voice = next(runtime for runtime in payload["runtimes"] if runtime["engine"] == "voice-mlx")

        audit = image["build_side_native_audit"]
        self.assertTrue((ROOT / audit["report_path"]).is_file())
        self.assertEqual(audit["binaries_checked"], 182)
        self.assertEqual(audit["minimum_macos_above_target"], 0)
        self.assertEqual(audit["missing_arm64"], 0)
        self.assertEqual(audit["status"], "not-packageable-without-relocation")
        self.assertEqual(image["distribution_inventory"]["count"], 69)
        self.assertTrue((ROOT / image["distribution_inventory"]["path"]).is_file())
        self.assertTrue((ROOT / image["distribution_inventory"]["licenses_path"]).is_dir())
        qwen_base = voice["build_side_import_proof"]["imports"]["qwen_base"]
        self.assertEqual(qwen_base["status"], "imported")
        self.assertEqual(qwen_base["model_family"], "Qwen3-TTS Base")
        self.assertEqual(qwen_base["model_type"], "qwen3_tts")
        self.assertEqual(
            qwen_base["module"],
            "mlx_audio.tts.models.qwen3_tts.qwen3_tts",
        )
        self.assertEqual(
            voice["build_side_import_proof"]["imports"]["voxcpm2"]["status"],
            "imported",
        )
        self.assertEqual(len(payload["official_workflows"]), 3)


if __name__ == "__main__":
    unittest.main()
