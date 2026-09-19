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
    # The preset-speaker and voice-design tiers. The kit shipped only the cloning tiers, so the
    # Audio page could only ever offer cloning; these are what make the other two tabs possible.
    "qwen3-tts-12hz-0-6b-customvoice-8bit": ("mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit", "049ef77fe8816b536193c0c25f9a214d17921282", "voice-mlx"),
    "qwen3-tts-12hz-1-7b-customvoice-8bit": ("mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit", "41d3337e8b7f2843a75841595fc14e4b9a7a4b96", "voice-mlx"),
    "qwen3-tts-12hz-1-7b-voicedesign-8bit": ("mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-8bit", "f90d617701d9f7f4ca499291e0b57f2b3c2fd2ee", "voice-mlx"),
    # ── Adopted from the Voice Studio cache (owner's instruction) ──
    # Revisions pinned from that cache's refs/main, every file digest computed from the bytes
    # on disk; the memory floors come from the Voice catalogue's declared minimums.
    "audio8-tts-preview-0-6b-bf16": ("mlx-community/Audio8-TTS-Preview-0.6b-bf16", "e59b26ec0ad753ea5441a6439424e305ccc93800", "voice-mlx"),
    "chatterbox-4bit": ("mlx-community/chatterbox-4bit", "f1d7b9696e1b6242e64eb8c4a823b6d1a50425a8", "voice-mlx"),
    "chatterbox-8bit": ("mlx-community/chatterbox-8bit", "9617d61b596a03d1bed766a28c341680e993a1b9", "voice-mlx"),
    "chatterbox-turbo-4bit": ("mlx-community/chatterbox-turbo-4bit", "c63817725071d7b5269c7b558772d6e8cbf59cec", "voice-mlx"),
    "echo-tts-base": ("mlx-community/echo-tts-base", "882ed93d9d5ec1e9c084b22902f4871049fae995", "voice-mlx"),
    "f5-tts": ("SWivid/F5-TTS", "84e5a410d9cead4de2f847e7c9369a6440bdfaca", "voice-mlx"),
    "fish-audio-s2-pro-8bit": ("mlx-community/fish-audio-s2-pro-8bit", "c8d4481b3f7cbfe64d855c8b7cda7739502fc3ff", "voice-mlx"),
    "fish-audio-s2-pro-bf16": ("mlx-community/fish-audio-s2-pro-bf16", "eccd57bf5c1ebc13cb2f993df867f4e49931a36a", "voice-mlx"),
    "fish-s1-dac-min": ("jordand/fish-s1-dac-min", "18b770e5b62b1a58ebd7423401a937cc063d8729", "voice-mlx"),
    "kokoro-82m-bf16": ("mlx-community/Kokoro-82M-bf16", "a71e4d38b236d968966a2002c4c895dbd12b1c3c", "voice-mlx"),
    "longcat-audiodit-1b-4bit": ("mlx-community/LongCat-AudioDiT-1B-4bit", "37d0247191d1f464cebd102de63e1e04907612e2", "voice-mlx"),
    "moss-audio-tokenizer-nano": ("OpenMOSS-Team/MOSS-Audio-Tokenizer-Nano", "6aa02b01e445cc585582cf0ba480bc3ea6c8dd68", "voice-mlx"),
    "moss-tts-nano-100m": ("mlx-community/MOSS-TTS-Nano-100M", "229a9c51bb0ffff6fd0dbe53b5bf0c441e438a79", "voice-mlx"),
    "orpheus-3b-0-1-ft-4bit": ("mlx-community/orpheus-3b-0.1-ft-4bit", "b30aed4ecfa03c0177a5419b51919d14a105f183", "voice-mlx"),
    "s3tokenizerv2": ("mlx-community/S3TokenizerV2", "e0c9886f0e1c35ae85b1f27277416fb19fc72bec", "voice-mlx"),
    "snac-24khz": ("mlx-community/snac_24khz", "556af1cd3b1c5f2d294f6aa9bb886245d7b716ac", "voice-mlx"),
    "umt5-base": ("google/umt5-base", "0de9394d54f8975e71838d309de1cb496c894ab9", "voice-mlx"),
    "vibevoice-realtime-0-5b-4bit": ("mlx-community/VibeVoice-Realtime-0.5B-4bit", "550877a15358642c1e062aa38ce1fe0623beaa1b", "voice-mlx"),
    "voxtral-4b-tts-2603-mlx-4bit": ("mlx-community/Voxtral-4B-TTS-2603-mlx-4bit", "f98fc91b9cb5adc7dab56102c690458276c14c6a", "voice-mlx"),
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
            "qwen3-tts-12hz-0-6b-customvoice-8bit": "cdefed4cdccf52a52c1858c39ab87d77811a8508e23c45d796b80b6469518a8c",
            "qwen3-tts-12hz-1-7b-customvoice-8bit": "2690316310dfbc75249645bd7bcd021ebbb3ef7b114b0c9aa5ec2e4f24ecd18f",
            "qwen3-tts-12hz-1-7b-voicedesign-8bit": "d4d7eddb9b9a3e17957389c1d685be1104ec933c2df5ee552eda784af223f990",
            # Adopted from the local Voice Studio cache rather than recaptured over the
            # network: the same pinned revisions, digests of the canonical manifests.
            "audio8-tts-preview-0-6b-bf16": "5fc895875568a1a4d3d51749b52b0e9fd1bc5d70d5ef9ad5425dc47b98ed29fd",
            "chatterbox-4bit": "0c068c53e7c0517c5a642947a4f451a6fc034544e2d6142ca5eb4586c336bd74",
            "chatterbox-8bit": "7f5b16346f56d87a1db05634583ed64bf18e3216563a939c7714ead6c225d693",
            "chatterbox-turbo-4bit": "9826f5bfd9f26b66950c0750d31f7732b943953c2680d23a088a8d03cc8d03d1",
            "echo-tts-base": "1ab15f4a3dbb6a314c7025a11c4f5f325ca486f7b67c15c953faf2b81e505aad",
            "f5-tts": "f811a09772356b2e88ce5484003bfe52112f079facd0710f1de2e62366a39b41",
            "fish-audio-s2-pro-8bit": "290829227d765d29c7da863e31f745468e4c36fb081fa3cc83c5d10122ca115a",
            "fish-audio-s2-pro-bf16": "b7924d5dcfaeaceaf9a6a7ecc1843e22de72afec6ee0bce180081a9a276aded6",
            "fish-s1-dac-min": "ed7381574537d6b80b19c92d84aeb7a03692f068d73c5c2bbbfcf05e0ed00fd7",
            "kokoro-82m-bf16": "8d38864668cd2683f8203423b20b3aae36a3e0550abdb58a2d9b47d87e9f33e2",
            "longcat-audiodit-1b-4bit": "cc2ab7f27020fdb5991e9a707206055576bd36df451d58ef13cf828edc977d90",
            "moss-audio-tokenizer-nano": "62623a6ab2c64c87459cbe5e57f42294a59c9b6b523c81a942ec1e8666050cbe",
            "moss-tts-nano-100m": "6ddefec42fb778c022a843323d6b262166c6b393afedfe0165abf4f3351da57f",
            "orpheus-3b-0-1-ft-4bit": "ce7d2ef9f9019ec3ebea55e3e61b344fb5a5688413a54f0bd4c7c13fd03092c6",
            "s3tokenizerv2": "d351fbde041c2e321f0fc7d9b0e1399a8e7605a187a5bd3f9edf6c3f09e171f2",
            "snac-24khz": "3de0f7d84631694abed5d30ab0f785d2d0df4fc2fd4076a93da56c61d6e7b260",
            "umt5-base": "8036296138d919d330b63f5c1d7803c4f4da7baab9ce10ec41dd699bb692cf1e",
            "vibevoice-realtime-0-5b-4bit": "b0b65edbe472c76ea9c39251cf26505589d75308b21616340faf845152c6d3d9",
            "voxtral-4b-tts-2603-mlx-4bit": "2216037cd62d29e5723f7b104419c3cccbbb3e771d7056fd9b6cc129f04f3a53",
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
            "qwen3-tts-12hz-0-6b-customvoice-8bit": ("model/model.safetensors", "model/speech_tokenizer/model.safetensors", "model/vocab.json"),
            "qwen3-tts-12hz-1-7b-customvoice-8bit": ("model/model.safetensors", "model/speech_tokenizer/model.safetensors", "model/vocab.json"),
            "qwen3-tts-12hz-1-7b-voicedesign-8bit": ("model/model.safetensors", "model/speech_tokenizer/model.safetensors", "model/vocab.json"),
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
