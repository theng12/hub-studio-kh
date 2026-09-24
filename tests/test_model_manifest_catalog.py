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
    "ace-step-1-5-turbo": ("ACE-Step/Ace-Step1.5", "19671f406d603126926c1b7e2adc169acbcade22", "music-mlx"),
    # The second Music engine: Stability's MLX Stable Audio 3, instrumental only.
    "stable-audio-3-medium": ("stabilityai/stable-audio-3-optimized", "da6edc54ddba10bfd79a077102ded687f80e882b", "music-mlx"),
    "flux2-klein-4b-mlx-4bit": ("AITRADER/FLUX2-klein-4B-mlx-4bit", "7fd24828501390b67a92c8b66d2fc5a707d0ba1a", "image-mflux"),
    # ST-14: the local Chat Studio's MLX text model (owner-selected, text-only accepted).
    "qwen3-5-9b-4bit": ("mlx-community/Qwen3.5-9B-4bit", "8b2b98c00a6b4d291155e4890773ca8f769aee53", "chat-mlx"),
    "qwen3-tts-12hz-0-6b-base-8bit": ("mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit", "50f45ef0047cde7e84c2ef04326acb8ada2436a7", "voice-mlx"),
    "qwen3-tts-12hz-1-7b-base-4bit": ("mlx-community/Qwen3-TTS-12Hz-1.7B-Base-4bit", "37e955a1deb861c088ae5f3a67043185f3d1a60c", "voice-mlx"),
    "voxcpm2-4bit": ("mlx-community/VoxCPM2-4bit", "dc9e5c187858da5f4a13dc4c247e297339216381", "voice-mlx"),
    "voxcpm2-8bit": ("mlx-community/VoxCPM2-8bit", "d52725898a0675703f7f9ddc5a4d1a3cdbb99032", "voice-mlx"),
    "voxcpm2-bf16": ("mlx-community/VoxCPM2-bf16", "a7fb80d9056983135a54ca3f71fcc5a637ccddf5", "voice-mlx"),
    "omnivoice-bfloat16": ("mlx-community/OmniVoice-bfloat16", "6119f7072580cc30c3cbdf496269216b03ad9742", "voice-mlx"),
    "whisper-turbo": ("mlx-community/whisper-large-v3-turbo", "a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb", "subtitle-mlx"),
    "whisper-small": ("mlx-community/whisper-small-mlx", "45f3915923c7a79a5a5b5a7d909d39aeb0e5630e", "subtitle-mlx"),
    "whisper-base": ("mlx-community/whisper-base-mlx", "1e3e249fb8d01c655324bd6841b1deadffd6d04c", "subtitle-mlx"),
    # ── Adopted from the Voice Studio cache (owner's instruction) ──
    # Revisions pinned from that cache's refs/main, every file digest computed from the bytes
    # on disk; the memory floors come from the Voice catalogue's declared minimums.
    "chatterbox-4bit": ("mlx-community/chatterbox-4bit", "f1d7b9696e1b6242e64eb8c4a823b6d1a50425a8", "voice-mlx"),
    "chatterbox-8bit": ("mlx-community/chatterbox-8bit", "9617d61b596a03d1bed766a28c341680e993a1b9", "voice-mlx"),
    "fish-audio-s2-pro-8bit": ("mlx-community/fish-audio-s2-pro-8bit", "c8d4481b3f7cbfe64d855c8b7cda7739502fc3ff", "voice-mlx"),
    "fish-audio-s2-pro-bf16": ("mlx-community/fish-audio-s2-pro-bf16", "eccd57bf5c1ebc13cb2f993df867f4e49931a36a", "voice-mlx"),
    "s3tokenizerv2": ("mlx-community/S3TokenizerV2", "e0c9886f0e1c35ae85b1f27277416fb19fc72bec", "voice-mlx"),
    # ── Khmer VITS families (ST-12, owner request 2026-09-24) ──
    # Two single-speaker Khmer engines packed under the `voice-vits` engine.
    "khmer-tts-mms": ("khmerttsopensource/khmer-tts", "c9380a9d1c817f2b21980d54f29f7a61b0d626f5", "voice-vits"),
    "khmer-tts-female-onnx": ("sengtha/khmer-tts-female-v2", "c6fde511a839c4f20cdce96e2a35735d493030d2", "voice-vits"),
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

    def test_every_catalog_engine_can_actually_be_served(self):
        """The catalog and the Hub's engine->worker map have to agree.

        They did not. `chat-mlx` was added to the engine allowlist in `model_packages` and to the
        shipped catalog, but not to the map in `standalone.py`, so `/api/hub/model-packages`
        raised `KeyError: 'chat-mlx'` and answered 500 — in 0.6.57, on every Mac, because the chat
        manifest ships in the catalog whether or not its model is installed. The app reads that
        route with a hard `try` inside its refresh, so the 500 did not degrade one list: it marked
        the whole Hub unreachable and left every model picker empty.

        Every existing test of that route used fixture manifests, which is exactly why none of
        them saw it. This one walks the real catalog, so a seventh engine cannot repeat it.
        """
        from components.hub import standalone
        from components import model_packages
        for package_id, manifest in sorted(self.manifests.items()):
            engine = manifest["engine"]
            with self.subTest(package=package_id, engine=engine):
                # The allowlist accepts it, so the worker map must serve it.
                try:
                    model_packages.validate_manifest(dict(manifest))
                except model_packages.PackageError as exc:
                    self.fail(f"{package_id}: the catalog ships a manifest the Hub rejects: {exc}")
                worker = standalone._worker_for_engine(engine)
                self.assertTrue(worker, f"{package_id}: engine {engine} maps to no worker")

    def test_an_unknown_engine_names_itself_rather_than_raising_a_bare_keyerror(self):
        """A KeyError from deep inside the route reads as "the Hub is down".

        The failure has to say which engine is unmapped and where to register it, because the
        person seeing it is looking at an empty model list with no other clue.
        """
        from components.hub import standalone
        with self.assertRaises(ValueError) as caught:
            standalone._worker_for_engine("not-a-real-engine")
        message = str(caught.exception)
        self.assertIn("not-a-real-engine", message)
        self.assertIn("model_packages.py", message)

    def test_catalog_is_the_exact_release_menu(self):
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
            "ace-step-1-5-turbo": "0e06526df7a27784c28f9e0e40dbc71a4392c9f0cfbbfb538bb9c0b43f16e0f6",
            "flux2-klein-4b-mlx-4bit": "d49528a1b5da899519099f3312a7d1a60174c5f6c0858a6ac35daf16d1e0012d",
            "qwen3-5-9b-4bit": "b6b8287be6856056ce9b6976b780cbc373a2127f88b57fc23022a29710c43922",
            "omnivoice-bfloat16": "a1bdd369266965067586274bc4d2f0e157425c508fe7036e0fcf56bf845b320a",
            "qwen3-tts-12hz-0-6b-base-8bit": "23404870afb1a3ef651c496de9d0891f20c9f99488a92792bebcfd08ec54233e",
            "qwen3-tts-12hz-1-7b-base-4bit": "f514eb0f48dda5f3173c08ac954e644e11802aa5525c821cdaff752a9ad722d7",
            # Recaptured: the 4-bit VoxCPM tier's published floor now records the owner's
            # 8 GB observation instead of the earlier inventory guidance.
            "voxcpm2-4bit": "046da2b3f5e720c278eb15ccd6f5d5a70bd47a1bed434dd6e06350a0370ff64a",
            "voxcpm2-8bit": "a5063ff38195bf0bb3ff3d0be72704e71a41e15b2f016477d8bde9c0eceac147",
            "voxcpm2-bf16": "680d3a0b89a89853af24197f3a755e8789385c5ad38e5073830c5ec7604b228f",
            "whisper-base": "1a504fa7c9f76ab223ead7120cd8311c741f269521a300e5e81b0d5f1dc8e2ef",
            "whisper-small": "44a6a3600f7874dfa7494ff8cf3741724046c39769e6af3affb77a6da70fb141",
            "whisper-turbo": "952efaf4c2d12b0fdde2b9122c89d01667e83fc1a1abf0b0d0daa597214fc1ef",
            # Adopted from the local Voice Studio cache rather than recaptured over the
            # network: the same pinned revisions, digests of the canonical manifests.
            "chatterbox-4bit": "0c068c53e7c0517c5a642947a4f451a6fc034544e2d6142ca5eb4586c336bd74",
            "chatterbox-8bit": "7f5b16346f56d87a1db05634583ed64bf18e3216563a939c7714ead6c225d693",
            "fish-audio-s2-pro-8bit": "290829227d765d29c7da863e31f745468e4c36fb081fa3cc83c5d10122ca115a",
            "fish-audio-s2-pro-bf16": "b7924d5dcfaeaceaf9a6a7ecc1843e22de72afec6ee0bce180081a9a276aded6",
            "s3tokenizerv2": "d351fbde041c2e321f0fc7d9b0e1399a8e7605a187a5bd3f9edf6c3f09e171f2",
            # Stable Audio 3's MLX bundle, captured from the ungated optimized repo.
            "stable-audio-3-medium": "82b64fa98763e4e4fcf1b8bbbe24c40dcd8ef1a03900b82423c1dbad0039516f",
            # Khmer VITS families captured 2026-09-24 from the pinned revisions.
            "khmer-tts-mms": "706c460d7816acda84de7c6995936a8b2b8b5c4ddeff62ee8d91db6418568964",
            "khmer-tts-female-onnx": "2e9af294a76f38fd0e28078b084948c33b5f222ea6dd2fc5e23f2ad7ccf548bc",
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
                if file.get("provider", "huggingface") == "github":
                    self.assertIn(file["revision"], source_revisions)
                    self.assertEqual(
                        file["urls"][0],
                        f"https://raw.githubusercontent.com/{file['repo']}/"
                        f"{file['revision']}/{file['source_path']}")
                else:
                    self.assertTrue(any(f"/resolve/{revision}/" in file["urls"][0]
                                        for revision in source_revisions))
                if file["path"].startswith("model/"):
                    self.assertNotIn("README", file["source_path"])
                    self.assertNotIn(".gitattributes", file["source_path"])

    def test_required_generation_assets_and_shared_whisper_processors_are_present(self):
        for package_id, prefix in {
            "flux2-klein-4b-mlx-4bit": ("model/text_encoder/", "model/transformer/", "model/vae/", "model/tokenizer/"),
            "qwen3-5-9b-4bit": ("model/model-00001-of-00002.safetensors", "model/config.json"),
            "qwen3-tts-12hz-0-6b-base-8bit": ("model/model.safetensors", "model/speech_tokenizer/model.safetensors", "model/vocab.json"),
            "voxcpm2-4bit": ("model/model.safetensors", "model/tokenizer.json"),
            "voxcpm2-8bit": ("model/model.safetensors", "model/tokenizer.json"),
            "voxcpm2-bf16": ("model/model.safetensors", "model/tokenizer.json"),
            "omnivoice-bfloat16": ("model/model.safetensors", "model/audio_tokenizer/model.safetensors", "model/tokenizer_config.json"),
            "whisper-turbo": ("model/weights.safetensors",),
            "whisper-small": ("model/weights.npz",),
            "whisper-base": ("model/weights.npz",),
            "khmer-tts-mms": ("model/model.safetensors", "model/config.json", "model/vocab.json"),
            "khmer-tts-female-onnx": ("model/khmer_tts_v3.onnx", "model/tts_meta.json"),
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
        for package_id in ("qwen3-tts-12hz-0-6b-base-8bit", "qwen3-tts-12hz-1-7b-base-4bit"):
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
