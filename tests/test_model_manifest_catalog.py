import json
import hashlib
import importlib.util
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "model-manifests"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
REVISION = re.compile(r"^[0-9a-f]{40}$")
# The capture specs are the release menu now, not a copy of it. This file used to restate the
# catalog as an exact-membership table (`EXPECTED`), which is why it stayed green while seven
# shipped packages had no capture spec at all: a test that re-implements the producer's list can
# only ever confirm itself. Every assertion below is an invariant over the real catalog, so the
# next model is covered without editing this file.
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
        from components.hub.backend import vocabulary
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
                # The modality travels in the same map. A second, inline engine->modality dict
                # in `_submission_readiness` drifted from this one, and an engine that was
                # registered everywhere but there made the Hub refuse *every job for every model
                # of that engine* with MODEL_MODALITY_MISMATCH - "model is not available for
                # this job modality" - which blames the operator's request instead of the
                # missing map. Registering an engine is now one edit, and this asserts both
                # halves of it against the engine each shipped manifest really declares.
                modality = standalone._modality_for_engine(engine)
                self.assertTrue(
                    modality,
                    f"{package_id}: engine {engine} maps to no modality, so every job for it "
                    f"would be refused as MODEL_MODALITY_MISMATCH",
                )
                self.assertIn(
                    modality, set(vocabulary.WORKER_FOR_MODALITY),
                    f"{package_id}: engine {engine} claims the unrouted modality {modality!r}",
                )

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
        with self.assertRaises(ValueError) as caught:
            standalone._modality_for_engine("not-a-real-engine")
        self.assertIn("not-a-real-engine", str(caught.exception))

    def test_every_shipped_manifest_is_reproducible_by_the_capture_tool(self):
        """The catalog directory and the capture specs must be the same set.

        `packaging/capture_model_manifests.py` is how the catalog is produced, so a manifest it
        has no spec for is a package a re-capture silently drops. Seven shipped packages were in
        exactly that state (`ace-step-1-5-turbo`, `chatterbox-{4,8}bit`, `fish-audio-s2-pro-*`,
        `s3tokenizerv2`, `stable-audio-3-medium`) while this file asserted their names from a
        hand-written table - the one thing a membership test cannot notice.

        `catalog_problems` is the same check the tool runs before it touches the network, so the
        test and the tool cannot disagree about what "reproducible" means.
        """
        self.assertEqual(CAPTURE.catalog_problems(CATALOG), [],
                         "the capture specs and the shipped catalog disagree (see the list above)")
        specs = {choice["package_id"]: choice for choice in CAPTURE.CHOICES}
        self.assertEqual(sorted(self.manifests), sorted(specs),
                         "every shipped manifest needs a CHOICES entry, and every CHOICES entry "
                         "must name a package the catalog ships")
        self.assertEqual(set(CAPTURE.DETAILS), set(specs),
                         "DETAILS must cover every CHOICES entry: a missing entry used to raise "
                         "a bare KeyError that named neither the package nor the file")

    def test_every_manifest_matches_the_spec_that_produces_it(self):
        """Every identity field the catalog publishes is pinned by its capture spec.

        Read through the spec, not restated here: adding a model means editing the spec, and this
        walks whatever the spec says against the manifest that ships.
        """
        for choice in CAPTURE.CHOICES:
            package_id = choice["package_id"]
            manifest = self.manifests[package_id]
            with self.subTest(package=package_id):
                self.assertEqual(manifest["model_id"], choice["model_id"])
                self.assertEqual(manifest["revision"], choice["revision"])
                self.assertEqual(manifest["engine"], choice["engine"])
                self.assertEqual(manifest.get("requires_packages") or [],
                                 list(choice.get("requires_packages") or []))
                self.assertRegex(manifest["revision"], REVISION)
                self.assertEqual(manifest["schema_version"], 1)
                self.assertEqual(manifest["minimum_macos"], "14.0")
                self.assertEqual(manifest["qualification"]["status"], "unqualified")
                details = CAPTURE.DETAILS[package_id]
                self.assertEqual(manifest["defaults"], details["defaults"])
                self.assertEqual(
                    [manifest["qualification"]["minimum_unified_memory_gb"],
                     manifest["qualification"]["recommended_unified_memory_gb"],
                     manifest["qualification"]["memory_evidence"]],
                    list(details["memory"]))
        declared = [choice["package_id"] for choice in CAPTURE.CHOICES if choice.get("default")]
        self.assertLessEqual(len(declared), 1,
                             "at most one package in the catalog may be the default")

    def test_a_missing_details_entry_names_the_package_and_the_file(self):
        """A bare KeyError named nothing; this one names both, before any network request."""
        choice = {"package_id": "not-a-real-package", "model_id": "owner/repo",
                  "revision": "a" * 40, "engine": "voice-mlx"}
        with self.assertRaises(ValueError) as caught:
            CAPTURE.capture(choice)
        message = str(caught.exception)
        self.assertIn("not-a-real-package", message)
        self.assertIn(CAPTURE.SPEC_FILE, message)

    def test_subtitle_and_default_blocks_pass_through_and_companions_stay_optional(self):
        """The two hand-declared blocks survive a capture; an absent companion list writes nothing.

        Another agent declares the subtitle contract and the default package in the shipped
        manifests. Neither is derivable from a publisher tree, so `--write` must carry them: from
        the spec when it declares one, and from the manifest already on disk when it does not.
        It must also still write `requires_packages` only for a package that has companions.
        """
        subtitle = {"family": "whisper", "languages": ["en", "km"],
                    "segment_timestamps": True, "word_timestamps": False, "long_form": True}
        choice = {"package_id": "whisper-small", "model_id": "owner/repo",
                  "revision": "a" * 40, "engine": "subtitle-mlx",
                  "requires_packages": ["whisper-turbo"], "subtitle": subtitle, "default": True}
        with mock.patch.object(CAPTURE, "snapshot",
                               return_value={"config.json": {"size": 1, "oid": "a" * 40}}), \
                mock.patch.object(CAPTURE, "captured_file",
                                  return_value={"path": "model/config.json", "size": 1,
                                                "sha256": "b" * 64, "urls": ["https://example"]}):
            manifest = CAPTURE.capture(choice)
        self.assertEqual(manifest["subtitle"], subtitle)
        self.assertIs(manifest["default"], True)
        self.assertEqual(manifest["requires_packages"], ["whisper-turbo"])
        choice.pop("requires_packages")
        choice.pop("subtitle")
        choice.pop("default")
        with mock.patch.object(CAPTURE, "snapshot",
                               return_value={"config.json": {"size": 1, "oid": "a" * 40}}), \
                mock.patch.object(CAPTURE, "captured_file",
                                  return_value={"path": "model/config.json", "size": 1,
                                                "sha256": "b" * 64, "urls": ["https://example"]}):
            bare = CAPTURE.capture(choice)
        self.assertNotIn("requires_packages", bare)
        self.assertNotIn("subtitle", bare)
        self.assertNotIn("default", bare)
        # A re-capture keeps a block the shipped manifest declares and the spec does not restate,
        # which is how a hand-added subtitle contract or default flag survives `--write`.
        shipped = dict(bare, subtitle=subtitle, default=True)
        kept = CAPTURE.preserve_declared_blocks(dict(bare), shipped)
        self.assertEqual(kept["subtitle"], subtitle)
        self.assertIs(kept["default"], True)
        # A block the spec does declare wins over the stale one on disk.
        self.assertIs(CAPTURE.preserve_declared_blocks(dict(bare, default=False),
                                                       shipped)["default"], False)

    def test_two_default_packages_are_refused(self):
        manifests = [{"package_id": "one", "default": True}, {"package_id": "two", "default": True}]
        problems = CAPTURE.duplicate_default_problems(manifests)
        self.assertEqual(len(problems), 1)
        self.assertIn("one", problems[0])
        self.assertIn("two", problems[0])
        self.assertEqual(CAPTURE.duplicate_default_problems(manifests[:1]), [])

    def test_a_write_never_replaces_a_manifest_it_does_not_reproduce(self):
        """A capture that selects a different file set leaves the shipped manifest alone.

        The write-time half of "the capture tool can reproduce the catalog": the offline field
        check cannot see which files a capture would select, so the selected set is compared with
        what ships and a package the capture does not rebuild is named instead of replaced.
        """
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            shipped = {"package_id": "example", "files": [
                {"path": "model/a", "sha256": "a" * 64},
                {"path": "model/b", "sha256": "b" * 64}]}
            (output / "example.json").write_text(json.dumps(shipped))
            same = {"package_id": "example", "files": list(shipped["files"])}
            fewer = {"package_id": "example", "files": shipped["files"][:1]}
            self.assertEqual(CAPTURE.unreproducible_problems([same], output), [])
            problems = CAPTURE.unreproducible_problems([fewer], output)
            self.assertEqual([package_id for package_id, _why in problems], ["example"])
            self.assertIn("model-manifests/example.json", problems[0][1])
            # A package with no manifest yet is a new capture, never a refusal.
            self.assertEqual(
                CAPTURE.unreproducible_problems([{"package_id": "new", "files": []}], output), [])

    def test_manifest_contents_match_the_captured_inventory(self):
        expected_digests = {
            # Updated only by a deliberate recapture of the exact pinned revisions.
            "ace-step-1-5-turbo": "ee5bc058cac0906e048b75cc47c1b157e7f11f55e7d013cb6ab2f39803f1c929",
            "flux2-klein-4b-mlx-4bit": "d49528a1b5da899519099f3312a7d1a60174c5f6c0858a6ac35daf16d1e0012d",
            # ST-16: captured 2026-09-24 from the pinned revision. 15 model-data files,
            # 5,907,438,792 bytes; the publisher's dotfiles, README and three example
            # images are excluded, so this total is not the 5,911,138,787-byte snapshot.
            "z-image-turbo-mflux-4bit": "df042f1a8eec72df794036e76cd3e48601408d8f11d06f035ddd7d610665ed2d",
            "qwen3-5-9b-4bit": "31a7cf2393d673bcb21a10ef51252455a34fd10b0bbf1c941861d2c2cdd87806",
            "omnivoice-bfloat16": "a1bdd369266965067586274bc4d2f0e157425c508fe7036e0fcf56bf845b320a",
            "qwen3-tts-12hz-0-6b-base-8bit": "23404870afb1a3ef651c496de9d0891f20c9f99488a92792bebcfd08ec54233e",
            "qwen3-tts-12hz-1-7b-base-4bit": "f514eb0f48dda5f3173c08ac954e644e11802aa5525c821cdaff752a9ad722d7",
            # Recaptured: the 4-bit VoxCPM tier's published floor now records the owner's
            # 8 GB observation instead of the earlier inventory guidance.
            "voxcpm2-4bit": "046da2b3f5e720c278eb15ccd6f5d5a70bd47a1bed434dd6e06350a0370ff64a",
            "voxcpm2-8bit": "a5063ff38195bf0bb3ff3d0be72704e71a41e15b2f016477d8bde9c0eceac147",
            "voxcpm2-bf16": "680d3a0b89a89853af24197f3a755e8789385c5ad38e5073830c5ec7604b228f",
            "whisper-base": "2d693235ded62b3450ef7517d503ef9fbaf0c383588fa3ac520d729dcb422aa3",
            "whisper-small": "17ecb436b5f484b6fd905decb8825f1f4c93fb52f187b5b54cd1c2901ed66bd0",
            # The three Whisper manifests now declare their own `subtitle` contract (and turbo the
            # catalog's single `"default": true`), which the Subtitle worker reads instead of
            # inventing multilingual/long-form capabilities. Their content changed, so their
            # digests did; the capture tool carries those blocks through `--write` verbatim.
            "whisper-turbo": "1ce9fb4d4e15c003c76b170ca5f7b3545f914d80a85ef05fe57800b9a5509672",
            # Adopted from the local Voice Studio cache rather than recaptured over the
            # network: the same pinned revisions, digests of the canonical manifests.
            # Recaptured 2026-09-24 with one companion spelling: `dependencies` was the first
            # field and `requires_packages` is the one every reader resolves, so the seven
            # manifests that still carried the legacy key were migrated and the key removed.
            "chatterbox-4bit": "2998a8cfb0a343ccecad1d66dd5d378c76f3fbe6c86363b45fd9ad04cd371109",
            "chatterbox-8bit": "6c787dfcd4b1b41220d2e4f251d909524bd92309b1dd1a1e476c4baf70fd421c",
            "fish-audio-s2-pro-8bit": "de3a8f7f44541830b3d38a205797aea326dcb681b042be96783c641717726d6f",
            "fish-audio-s2-pro-bf16": "9848ddd8f458ff1c604a85eb49f47e56f992ecf6158d3f28ea456b746fc70ca6",
            "s3tokenizerv2": "e3ada13f437013b48efbba8f2caefb7f02ec914654174a8229a3afedd579ea1f",
            # Stable Audio 3's MLX bundle, captured from the ungated optimized repo.
            "stable-audio-3-medium": "d847cf47d27aa11e398fbc4364b666e99f94a6b1fa22e1936b0314b1ccc2ac3e",
            # Khmer VITS families captured 2026-09-24 from the pinned revisions.
            "khmer-tts-mms": "706c460d7816acda84de7c6995936a8b2b8b5c4ddeff62ee8d91db6418568964",
            "khmer-tts-female-onnx": "2e9af294a76f38fd0e28078b084948c33b5f222ea6dd2fc5e23f2ad7ccf548bc",
            # Pocket TTS 4-bit and its two companions, captured 2026-09-24 (ST-13.A). The main
            # package is 3 files / 89,313,624 bytes; the tokenizer is one 59,339-byte
            # sentencepiece model and the preset voices are 8 embeddings / 4,334,272 bytes. All
            # three come from the publisher's own tree at the pinned revisions.
            "pocket-tts-4bit": "a94980fb8095234549109fd28c8c6571d9e574af9afa3fdc5a0fba57a085a43c",
            "pocket-tts-tokenizer": "1cbf8f87957be8a66ccc26410eb5fbc1dcb710fd8fa966ba17d176e2d63b592a",
            "pocket-tts-preset-voices": "de5fe27a84dee4e939165ac8e97a0fa545134a5141e3a442454cde0d5d897851",
        }
        # The digest map is the frozen-content half of the invariant: its keys are checked against
        # the manifests on disk (never against a hand-written list), so a package that appears
        # without a digest fails here instead of passing unnoticed. Change a digest only when the
        # manifest's own content changed - adding a capture spec does not change a manifest.
        self.assertEqual(set(expected_digests), set(self.manifests))
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
            "z-image-turbo-mflux-4bit": ("model/text_encoder/", "model/transformer/", "model/vae/", "model/tokenizer/"),
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
            # Pocket TTS: the whole executable model is one safetensors file plus its config;
            # the tokenizer and the preset prompts are separate packages on purpose.
            "pocket-tts-4bit": ("model/model.safetensors", "model/config.json"),
            "pocket-tts-tokenizer": ("model/tokenizer.model",),
            "pocket-tts-preset-voices": ("model/embeddings/alba.safetensors",
                                         "model/embeddings/marius.safetensors"),
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
        # Pocket TTS cannot generate without either companion: the tokenizer is loaded on every
        # call and the preset prompts are what a generation with no reference clip falls back to.
        self.assertEqual(self.manifests["pocket-tts-4bit"]["requires_packages"],
                         ["pocket-tts-tokenizer", "pocket-tts-preset-voices"])
        # Both companions describe the same publisher repository at the same revision as the files
        # they carry, and neither of them depends on anything itself.
        for companion in ("pocket-tts-tokenizer", "pocket-tts-preset-voices"):
            manifest = self.manifests[companion]
            self.assertNotIn("requires_packages", manifest)
            self.assertEqual(manifest["source"]["layout"]["model"]["repo"],
                             "kyutai/pocket-tts-without-voice-cloning")
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
