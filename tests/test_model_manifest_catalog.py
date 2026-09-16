import json
import hashlib
import importlib.util
import re
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "model-manifests"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
REVISION = re.compile(r"^[0-9a-f]{40}$")

EXPECTED = {
    "flux2-klein-4b-mlx-4bit": ("AITRADER/FLUX2-klein-4B-mlx-4bit", "7fd24828501390b67a92c8b66d2fc5a707d0ba1a", "image-mflux"),
    "qwen3-tts-12hz-0-6b-base-8bit": ("mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit", "50f45ef0047cde7e84c2ef04326acb8ada2436a7", "voice-mlx"),
    "qwen3-tts-12hz-0-6b-base-4bit": ("mlx-community/Qwen3-TTS-12Hz-0.6B-Base-4bit", "0d6bb6fe33f92d47a507e23b9148940e8366ab5b", "voice-mlx"),
    "qwen3-tts-12hz-1-7b-base-8bit": ("mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit", "e7dd0585652209fa0d7783659aad4e8a324de11c", "voice-mlx"),
    "qwen3-tts-12hz-1-7b-base-4bit": ("mlx-community/Qwen3-TTS-12Hz-1.7B-Base-4bit", "37e955a1deb861c088ae5f3a67043185f3d1a60c", "voice-mlx"),
    "voxcpm2-4bit": ("mlx-community/VoxCPM2-4bit", "dc9e5c187858da5f4a13dc4c247e297339216381", "voice-mlx"),
    "voxcpm2-8bit": ("mlx-community/VoxCPM2-8bit", "d52725898a0675703f7f9ddc5a4d1a3cdbb99032", "voice-mlx"),
    "voxcpm2-bf16": ("mlx-community/VoxCPM2-bf16", "a7fb80d9056983135a54ca3f71fcc5a637ccddf5", "voice-mlx"),
    "omnivoice-bfloat16": ("mlx-community/OmniVoice-bfloat16", "6119f7072580cc30c3cbdf496269216b03ad9742", "voice-mlx"),
    "whisper-turbo": ("mlx-community/whisper-large-v3-turbo", "a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb", "subtitle-mlx"),
    "whisper-small": ("mlx-community/whisper-small-mlx", "45f3915923c7a79a5a5b5a7d909d39aeb0e5630e", "subtitle-mlx"),
    "whisper-base": ("mlx-community/whisper-base-mlx", "1e3e249fb8d01c655324bd6841b1deadffd6d04c", "subtitle-mlx"),
}
PROCESSOR_FILES = {
    "added_tokens.json", "merges.txt", "normalizer.json", "special_tokens_map.json",
    "tokenizer.json", "tokenizer_config.json", "vocab.json",
}
_capture_spec = importlib.util.spec_from_file_location(
    "capture_model_manifests", ROOT / "packaging" / "capture_model_manifests.py")
CAPTURE = importlib.util.module_from_spec(_capture_spec)
assert _capture_spec.loader is not None
_capture_spec.loader.exec_module(CAPTURE)


class ModelManifestCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifests = {path.stem: json.loads(path.read_text()) for path in CATALOG.glob("*.json")}

    def test_catalog_is_the_exact_twelve_model_release_menu(self):
        self.assertEqual(set(self.manifests), set(EXPECTED))
        for package_id, (model_id, revision, engine) in EXPECTED.items():
            manifest = self.manifests[package_id]
            self.assertEqual(manifest["schema_version"], 1)
            self.assertEqual((manifest["model_id"], manifest["revision"], manifest["engine"]),
                             (model_id, revision, engine))
            self.assertEqual(manifest["minimum_macos"], "14.0")
            self.assertEqual(manifest["qualification"]["status"], "unqualified")
            self.assertRegex(manifest["revision"], REVISION)

    def test_manifest_contents_match_the_captured_inventory(self):
        expected_digests = {
            # Updated only by a deliberate recapture of the exact pinned revisions.
            "flux2-klein-4b-mlx-4bit": "d49528a1b5da899519099f3312a7d1a60174c5f6c0858a6ac35daf16d1e0012d",
            "omnivoice-bfloat16": "a1bdd369266965067586274bc4d2f0e157425c508fe7036e0fcf56bf845b320a",
            "qwen3-tts-12hz-0-6b-base-4bit": "30cd91171bb5c1e63e5d7fb66aa4de16400fe77f2a0ebf6480e706ce4285a45b",
            "qwen3-tts-12hz-0-6b-base-8bit": "23404870afb1a3ef651c496de9d0891f20c9f99488a92792bebcfd08ec54233e",
            "qwen3-tts-12hz-1-7b-base-4bit": "f514eb0f48dda5f3173c08ac954e644e11802aa5525c821cdaff752a9ad722d7",
            "qwen3-tts-12hz-1-7b-base-8bit": "e0307971122e2b2a526bdef5308129f9122130c27f8cfbe7429ccd3ea5f1a5ba",
            # Recaptured: the 4-bit VoxCPM tier's published floor now records the owner's
            # 8 GB observation instead of the earlier inventory guidance.
            "voxcpm2-4bit": "046da2b3f5e720c278eb15ccd6f5d5a70bd47a1bed434dd6e06350a0370ff64a",
            "voxcpm2-8bit": "a5063ff38195bf0bb3ff3d0be72704e71a41e15b2f016477d8bde9c0eceac147",
            "voxcpm2-bf16": "680d3a0b89a89853af24197f3a755e8789385c5ad38e5073830c5ec7604b228f",
            "whisper-base": "1a504fa7c9f76ab223ead7120cd8311c741f269521a300e5e81b0d5f1dc8e2ef",
            "whisper-small": "44a6a3600f7874dfa7494ff8cf3741724046c39769e6af3affb77a6da70fb141",
            "whisper-turbo": "952efaf4c2d12b0fdde2b9122c89d01667e83fc1a1abf0b0d0daa597214fc1ef",
        }
        self.assertEqual(set(expected_digests), set(EXPECTED))
        for package_id, manifest in self.manifests.items():
            payload = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
            self.assertEqual(hashlib.sha256(payload).hexdigest(), expected_digests[package_id])

    def test_every_imported_file_has_an_immutable_hash_and_source_mapping(self):
        for manifest in self.manifests.values():
            paths = [file["path"] for file in manifest["files"]]
            self.assertEqual(paths, sorted(set(paths)))
            self.assertTrue(paths)
            for file in manifest["files"]:
                self.assertFalse(file["path"].startswith("/"))
                self.assertNotIn("\\", file["path"])
                self.assertNotIn("..", Path(file["path"]).parts)
                self.assertTrue(file["path"].startswith(("model/", "companions/processor/")))
                if file["path"].startswith("model/"):
                    self.assertTrue(file["source_path"])
                else:
                    self.assertNotIn("source_path", file)
                self.assertGreater(file["size"], 0)
                self.assertRegex(file["sha256"], SHA256)
                self.assertEqual(len(file["urls"]), 1)
                source_revisions = {item["revision"] for item in manifest["source"]["sources"]}
                self.assertTrue(any(f"/resolve/{revision}/" in file["urls"][0]
                                    for revision in source_revisions))
                if file["path"].startswith("model/"):
                    self.assertNotIn("README", file["source_path"])
                    self.assertNotIn(".gitattributes", file["source_path"])

    def test_required_generation_assets_and_shared_whisper_processors_are_present(self):
        for package_id, prefix in {
            "flux2-klein-4b-mlx-4bit": ("model/text_encoder/", "model/transformer/", "model/vae/", "model/tokenizer/"),
            "qwen3-tts-12hz-0-6b-base-8bit": ("model/model.safetensors", "model/speech_tokenizer/model.safetensors", "model/vocab.json"),
            "qwen3-tts-12hz-1-7b-base-8bit": ("model/model.safetensors", "model/speech_tokenizer/model.safetensors", "model/vocab.json"),
            "voxcpm2-4bit": ("model/model.safetensors", "model/tokenizer.json"),
            "voxcpm2-8bit": ("model/model.safetensors", "model/tokenizer.json"),
            "voxcpm2-bf16": ("model/model.safetensors", "model/tokenizer.json"),
            "omnivoice-bfloat16": ("model/model.safetensors", "model/audio_tokenizer/model.safetensors", "model/tokenizer_config.json"),
            "whisper-turbo": ("model/weights.safetensors",),
            "whisper-small": ("model/weights.npz",),
            "whisper-base": ("model/weights.npz",),
        }.items():
            paths = {file["path"] for file in self.manifests[package_id]["files"]}
            for required in prefix:
                self.assertTrue(any(path.startswith(required) for path in paths), (package_id, required))

        for package_id in ("whisper-turbo", "whisper-small", "whisper-base"):
            files = self.manifests[package_id]["files"]
            companions = {file["path"].removeprefix("companions/processor/")
                          for file in files if file["path"].startswith("companions/processor/")}
            self.assertEqual(companions, PROCESSOR_FILES)
            self.assertFalse(any(file["path"].endswith("model.safetensors") and
                                 file["path"].startswith("companions/") for file in files))

    def test_dependencies_and_layout_are_explicit(self):
        for package_id in ("qwen3-tts-12hz-0-6b-base-8bit", "qwen3-tts-12hz-1-7b-base-8bit"):
            self.assertEqual(self.manifests[package_id]["requires_packages"], ["whisper-turbo"])
        for manifest in self.manifests.values():
            source = manifest["source"]
            self.assertEqual(source["provider"], "huggingface")
            self.assertEqual(source["layout"]["model"]["destination"], "model")
            self.assertEqual(source["layout"]["model"]["revision"], manifest["revision"])
            self.assertTrue(source["sources"])
            self.assertTrue(manifest["defaults"])
            self.assertGreaterEqual(manifest["qualification"]["minimum_unified_memory_gb"], 8)
            self.assertGreaterEqual(manifest["qualification"]["recommended_unified_memory_gb"],
                                    manifest["qualification"]["minimum_unified_memory_gb"])


class CaptureBoundaryTests(unittest.TestCase):
    class _Response:
        def __init__(self, body):
            self.body = body
            self.read_limit = None

        def __enter__(self):
            return self

        def __exit__(self, *unused):
            return False

        def read(self, limit):
            self.read_limit = limit
            return self.body

    def test_metadata_body_is_read_with_a_bounded_limit_and_rejects_excess(self):
        response = self._Response(b"x" * (CAPTURE.MAX_METADATA_BYTES + 1))
        with mock.patch.object(CAPTURE, "urlopen", return_value=response):
            with self.assertRaisesRegex(ValueError, "response exceeds"):
                CAPTURE.request_json("https://metadata.example")
        self.assertEqual(response.read_limit, CAPTURE.MAX_METADATA_BYTES + 1)

    def test_small_file_body_is_bounded_and_git_blob_oid_is_verified(self):
        body = b"small asset"
        oid = hashlib.sha1(b"blob 11\0" + body).hexdigest()
        response = self._Response(body)
        with mock.patch.object(CAPTURE, "urlopen", return_value=response):
            self.assertEqual(CAPTURE.file_digest("owner/repo", "a" * 40, "config.json",
                                                  {"size": len(body), "oid": oid}),
                             hashlib.sha256(body).hexdigest())
        self.assertEqual(response.read_limit, CAPTURE.MAX_SMALL_FILE_BYTES + 1)

    def test_lfs_metadata_digest_never_requests_a_weight_payload(self):
        with mock.patch.object(CAPTURE, "request", side_effect=AssertionError("unexpected fetch")):
            self.assertEqual(CAPTURE.file_digest("owner/repo", "a" * 40, "weights.safetensors", {
                "size": 5_000_000_000, "lfs": {"oid": "b" * 64},
            }), "b" * 64)

    def test_snapshot_rejects_a_revision_mismatch(self):
        with mock.patch.object(CAPTURE, "request_json", return_value={"sha": "b" * 40}):
            with self.assertRaisesRegex(ValueError, "expected revision"):
                CAPTURE.snapshot("owner/repo", "a" * 40)

    def test_capture_rejects_missing_processor_assets_without_fetching_models(self):
        primary = {"config.json": {"size": 1, "oid": "a" * 40}}
        processor = {"tokenizer.json": {"size": 1, "oid": "b" * 40}}
        choice = {"package_id": "whisper-turbo", "model_id": "owner/repo", "revision": "a" * 40,
                  "engine": "subtitle-mlx", "processor": ("owner/processor", "b" * 40)}
        with mock.patch.object(CAPTURE, "snapshot", side_effect=(primary, processor)), \
                mock.patch.object(CAPTURE, "captured_file", return_value={}):
            with self.assertRaisesRegex(ValueError, "missing processor files"):
                CAPTURE.capture(choice)


if __name__ == "__main__":
    unittest.main()
