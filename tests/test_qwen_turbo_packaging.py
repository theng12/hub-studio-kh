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


def load_copy_models():
    path = ROOT / "qualification" / "copy_models_for_this_mac.py"
    spec = importlib.util.spec_from_file_location("copy_qwen_turbo_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


COPY_MODELS = load_copy_models()


class QwenTurboPackageTests(unittest.TestCase):
    def test_capture_spec_pins_owner_adopted_unmeasured_turbo_at_16gb(self):
        choices = {choice["package_id"]: choice for choice in CAPTURE.CHOICES}
        choice = choices[PACKAGE_ID]
        details = CAPTURE.DETAILS[PACKAGE_ID]

        self.assertEqual(choice["model_id"], MODEL_ID)
        self.assertEqual(choice["revision"], REVISION)
        self.assertEqual(choice["engine"], "image-mflux")
        self.assertTrue(choice["production_allowed"])
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
        self.assertEqual(details["memory"][:2], (16, 16))
        self.assertIn("owner", details["memory"][2].lower())
        self.assertIn("unmeasured", details["memory"][2].lower())
        self.assertEqual(details["qualification"]["status"], "unqualified")
        self.assertEqual(details["qualification"]["adoption_basis"],
                         "owner decision 2026-10-13")
        self.assertEqual(details["qualification"]["turbo_measurements"], "unmeasured")
        self.assertEqual(details["qualification"]["min_free_memory_gb"], 8.0)
        self.assertIn("regular Qwen Image 2.1 Low-RAM",
                      details["qualification"]["min_free_memory_note"])
        self.assertFalse(any("seconds_per_step" in key for key in details["qualification"]))

    def test_manifest_captures_pinned_inference_files_with_immutable_digests(self):
        manifest = json.loads(
            (ROOT / "model-manifests" / f"{PACKAGE_ID}.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["package_id"], PACKAGE_ID)
        self.assertEqual(manifest["model_id"], MODEL_ID)
        self.assertEqual(manifest["revision"], REVISION)
        self.assertEqual(manifest["engine"], "image-mflux")
        self.assertEqual(manifest["production_allowed"], True)
        self.assertEqual(manifest["license"]["weights"], "qwen-research")
        self.assertEqual(manifest["qualification"]["status"], "unqualified")
        self.assertEqual(manifest["qualification"]["minimum_unified_memory_gb"], 16)
        self.assertEqual(manifest["qualification"]["recommended_unified_memory_gb"], 16)
        self.assertEqual(manifest["qualification"]["min_free_memory_gb"], 8.0)
        self.assertIn("Owner adopted", manifest["qualification"]["reason"])
        self.assertIn("remain unmeasured", manifest["qualification"]["reason"])
        self.assertNotIn("seconds_per_step", manifest["qualification"])

        self.assertEqual(COPY_MODELS.required_gb(manifest), 16)
        self.assertNotIn(PACKAGE_ID, COPY_MODELS.NOT_STOCKED)

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
