from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ID = "qwen-image-2-1-turbo-mflux-q4"
MODEL_ID = "mflux-community/qwen-image-2-1-turbo-mflux-q4"
REVISION = "bda8951ec6b8cc288a961432fe15c22433521a1d"


def load_capture():
    path = ROOT / "packaging" / "capture_model_manifests.py"
    spec = importlib.util.spec_from_file_location("capture_qwen_turbo_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CAPTURE = load_capture()


class QwenTurboPackageTests(unittest.TestCase):
    def test_capture_spec_pins_turbo_as_unqualified_16gb_target(self):
        choices = {choice["package_id"]: choice for choice in CAPTURE.CHOICES}
        choice = choices[PACKAGE_ID]
        details = CAPTURE.DETAILS[PACKAGE_ID]

        self.assertEqual(choice["model_id"], MODEL_ID)
        self.assertEqual(choice["revision"], REVISION)
        self.assertEqual(choice["engine"], "image-mflux")
        self.assertFalse(choice["production_allowed"])
        self.assertIn("MLX 4-bit", choice["display_name"])
        self.assertEqual(choice["quantization"], "MLX 4-bit")
        self.assertEqual(choice["license"]["weights"], "qwen-research")
        self.assertEqual(details["defaults"]["width"], 1024)
        self.assertEqual(details["defaults"]["height"], 1024)
        self.assertEqual(details["defaults"]["steps"], 8)
        self.assertEqual(details["defaults"]["guidance"], 1.0)
        self.assertEqual(details["defaults"]["seed"], -1)
        self.assertEqual(
            details["defaults"]["parameters_source"],
            ".planning/2026-09-10-standalone-app-feasibility/qwen-image-turbo-20261012.md",
        )
        self.assertEqual(
            details["defaults"]["schedule_source"],
            "model/model_index.json sample_sigmas",
        )
        self.assertEqual(details["memory"][:2], (None, None))
        self.assertIn("target", details["memory"][2].lower())
        self.assertIn("not measured", details["memory"][2].lower())
        self.assertEqual(details["qualification"]["status"], "unqualified")
        self.assertEqual(details["qualification"]["target_unified_memory_gb"], 16)
        self.assertNotIn("min_free_memory_gb", details["qualification"])
        self.assertFalse(any("seconds_per_step" in key for key in details["qualification"]))

    def test_manifest_captures_pinned_inference_files_with_immutable_digests(self):
        manifest = json.loads(
            (ROOT / "model-manifests" / f"{PACKAGE_ID}.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["package_id"], PACKAGE_ID)
        self.assertEqual(manifest["model_id"], MODEL_ID)
        self.assertEqual(manifest["revision"], REVISION)
        self.assertEqual(manifest["engine"], "image-mflux")
        self.assertEqual(manifest["production_allowed"], False)
        self.assertEqual(manifest["license"]["weights"], "qwen-research")
        self.assertEqual(manifest["qualification"]["status"], "unqualified")
        self.assertEqual(manifest["qualification"]["target_unified_memory_gb"], 16)
        self.assertNotIn("min_free_memory_gb", manifest["qualification"])
        self.assertNotIn("seconds_per_step", manifest["qualification"])

        files = {entry["path"]: entry for entry in manifest["files"]}
        for path in (
            "model/model_index.json",
            "model/manifest.json",
            "model/processor/chat_template.jinja",
            "model/processor/preprocessor_config.json",
            "model/processor/tokenizer.json",
            "model/processor/tokenizer_config.json",
            "model/text_encoder/config.json",
            "model/text_encoder/model.safetensors.index.json",
            "model/text_encoder/0.safetensors",
            "model/text_encoder/1.safetensors",
            "model/text_encoder/2.safetensors",
            "model/transformer/config.json",
            "model/transformer/model.safetensors.index.json",
            "model/transformer/0.safetensors",
            "model/transformer/1.safetensors",
            "model/vae/config.json",
            "model/vae/model.safetensors.index.json",
            "model/vae/0.safetensors",
        ):
            self.assertIn(path, files)
        self.assertFalse(any(path.endswith("README.md") for path in files))
        for entry in files.values():
            self.assertGreater(entry["size"], 0, entry["path"])
            self.assertRegex(entry["sha256"], r"^[0-9a-f]{64}$", entry["path"])
            self.assertTrue(entry["urls"], entry["path"])
            for url in entry["urls"]:
                self.assertEqual(
                    url,
                    f"https://huggingface.co/{MODEL_ID}/resolve/{REVISION}/"
                    f"{entry['source_path']}",
                )
        self.assertEqual(manifest["defaults"], CAPTURE.DETAILS[PACKAGE_ID]["defaults"])
        self.assertEqual(CAPTURE.catalog_problems(ROOT / "model-manifests"), [])

    def test_manifest_is_byte_stable_and_names_required_checkpoint_files(self):
        path = ROOT / "model-manifests" / f"{PACKAGE_ID}.json"
        body = path.read_bytes()
        manifest = json.loads(body)
        self.assertEqual(body, (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode())
        files = {entry["path"] for entry in manifest["files"]}
        self.assertTrue({
            "model/model_index.json",
            "model/processor/tokenizer.json",
            "model/processor/tokenizer_config.json",
            "model/text_encoder/config.json",
            "model/text_encoder/model.safetensors.index.json",
            "model/transformer/config.json",
            "model/transformer/model.safetensors.index.json",
            "model/vae/config.json",
            "model/vae/model.safetensors.index.json",
        }.issubset(files))
        for entry in manifest["files"]:
            self.assertEqual(len(entry["sha256"]), 64)
            int(entry["sha256"], 16)
            self.assertGreater(entry["size"], 0)
            self.assertEqual(len(entry["urls"]), 1)


if __name__ == "__main__":
    unittest.main()
