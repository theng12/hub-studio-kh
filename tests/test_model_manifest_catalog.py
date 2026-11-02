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
        exactly that state (`chatterbox-{4,8}bit`, `fish-audio-s2-pro-*`,
        `s3tokenizerv2`, `stable-audio-3-medium`, and `ace-step-1-5-turbo`, since retired) while this file asserted their names from a
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
                self.assertEqual(manifest["minimum_macos"],
                                 choice.get("minimum_macos", "14.0"))
                details = CAPTURE.DETAILS[package_id]
                # A tier is `qualified` only where the capture spec records a measurement, and
                # `unqualified` everywhere else. This asserted the literal string "unqualified" for
                # every manifest, which is the shape that keeps a measured tier unpublished: it
                # pinned the blanket answer rather than the rule, so recording a real measurement
                # failed the suite instead of being the thing the suite wanted.
                declared = details.get("qualification") or {}
                self.assertEqual(manifest["qualification"]["status"],
                                 declared.get("status", "unqualified"))
                if declared.get("status") == "qualified":
                    # And a claim of qualification has to say what qualified it. A status is an
                    # evidence claim, so the evidence has to be in the same block.
                    self.assertIn("MEASURED", manifest["qualification"]["memory_evidence"],
                                  "a qualified tier must name the measurement that qualified it")
                    self.assertTrue(declared.get("reason"),
                                    "and a qualified tier must carry its own reason")
                else:
                    self.assertIn("unqualified", manifest["qualification"]["status"])
                self.assertEqual(manifest["defaults"], details["defaults"])
                self.assertEqual(
                    [manifest["qualification"]["minimum_unified_memory_gb"],
                     manifest["qualification"]["recommended_unified_memory_gb"],
                     manifest["qualification"].get("memory_evidence")],
                    list(details["memory"]))
        self.assertEqual(CAPTURE.duplicate_default_problems(list(CAPTURE.CHOICES)), [],
                         "at most one package per engine may be the default")

    def test_a_missing_details_entry_names_the_package_and_the_file(self):
        """A bare KeyError named nothing; this one names both, before any network request."""
        choice = {"package_id": "not-a-real-package", "model_id": "owner/repo",
                  "revision": "a" * 40, "engine": "voice-mlx"}
        with self.assertRaises(ValueError) as caught:
            CAPTURE.capture(choice)
        message = str(caught.exception)
        self.assertIn("not-a-real-package", message)
        self.assertIn(CAPTURE.SPEC_FILE, message)

    def test_declared_blocks_pass_through_and_companions_stay_optional(self):
        """Human-declared policy survives capture; an absent companion list writes nothing.

        Another agent declares the subtitle contract and the default package in the shipped
        manifests. Neither is derivable from a publisher tree, so `--write` must carry them: from
        the spec when it declares one, and from the manifest already on disk when it does not.
        It must also still write `requires_packages` only for a package that has companions.
        """
        subtitle = {"family": "whisper", "languages": ["en", "km"],
                    "segment_timestamps": True, "word_timestamps": False, "long_form": True}
        choice = {"package_id": "whisper-small", "model_id": "owner/repo",
                  "revision": "a" * 40, "engine": "subtitle-mlx",
                  "requires_packages": ["whisper-turbo"], "subtitle": subtitle, "default": True,
                  "production_allowed": False}
        with mock.patch.object(CAPTURE, "snapshot",
                               return_value={"config.json": {"size": 1, "oid": "a" * 40}}), \
                mock.patch.object(CAPTURE, "captured_file",
                                  return_value={"path": "model/config.json", "size": 1,
                                                "sha256": "b" * 64, "urls": ["https://example"]}):
            manifest = CAPTURE.capture(choice)
        self.assertEqual(manifest["subtitle"], subtitle)
        self.assertIs(manifest["default"], True)
        self.assertIs(manifest["production_allowed"], False)
        self.assertEqual(manifest["requires_packages"], ["whisper-turbo"])
        choice.pop("requires_packages")
        choice.pop("subtitle")
        choice.pop("default")
        choice.pop("production_allowed")
        with mock.patch.object(CAPTURE, "snapshot",
                               return_value={"config.json": {"size": 1, "oid": "a" * 40}}), \
                mock.patch.object(CAPTURE, "captured_file",
                                  return_value={"path": "model/config.json", "size": 1,
                                                "sha256": "b" * 64, "urls": ["https://example"]}):
            bare = CAPTURE.capture(choice)
        self.assertNotIn("requires_packages", bare)
        self.assertNotIn("subtitle", bare)
        self.assertNotIn("default", bare)
        self.assertNotIn("production_allowed", bare)
        # A re-capture keeps a block the shipped manifest declares and the spec does not restate,
        # which is how a hand-added subtitle contract or default flag survives `--write`.
        shipped = dict(bare, subtitle=subtitle, default=True, production_allowed=False)
        kept = CAPTURE.preserve_declared_blocks(dict(bare), shipped)
        self.assertEqual(kept["subtitle"], subtitle)
        self.assertIs(kept["default"], True)
        self.assertIs(kept["production_allowed"], False)
        # A block the spec does declare wins over the stale one on disk.
        self.assertIs(CAPTURE.preserve_declared_blocks(dict(bare, default=False),
                                                       shipped)["default"], False)

    def test_production_allowed_in_capture_spec_requires_a_boolean(self):
        choice = dict(next(item for item in CAPTURE.CHOICES
                           if item["package_id"] == "qwen3-5-9b-4bit"))
        choice["production_allowed"] = "false"
        problems = CAPTURE.manifest_problems(choice, self.manifests[choice["package_id"]])
        self.assertTrue(any("production_allowed" in problem and "boolean" in problem
                            for problem in problems), problems)

    def test_nemotron_capture_reproduces_runtime_manifest_offline(self):
        manifest = self.manifests["nemotron-lightning-4bit"]
        choice = next(item for item in CAPTURE.CHOICES
                      if item["package_id"] == "nemotron-lightning-4bit")
        publisher_tree = {
            entry["source_path"]: {
                "size": entry["size"], "lfs": {"oid": entry["sha256"]},
            }
            for entry in manifest["files"]
        }
        # README remains in the full stock/SSD receipt, but the runtime capture's established
        # file policy selects only the ten inference files in this manifest.
        publisher_tree["README.md"] = {
            "size": 1440,
            "lfs": {"oid": "31d67cc7ae807a7984eee77d35cd7fd507289cc2ea420447e72f12f3e20d2e89"},
        }
        with mock.patch.object(CAPTURE, "snapshot", return_value=publisher_tree), \
                mock.patch.object(CAPTURE, "request",
                                  side_effect=AssertionError("capture fetched a file body")):
            captured = CAPTURE.capture(choice)
        self.assertEqual(captured, manifest)

    def test_qwen36_capture_reproduces_verified_runtime_manifest_offline(self):
        manifest = self.manifests["qwen36-35b-4bit"]
        choice = next(item for item in CAPTURE.CHOICES
                      if item["package_id"] == "qwen36-35b-4bit")
        publisher_tree = {
            entry["source_path"]: {
                "size": entry["size"], "lfs": {"oid": entry["sha256"]},
            }
            for entry in manifest["files"]
        }
        # The independently verified stock receipt has all sixteen canonical files. Runtime
        # capture keeps the fifteen inference files and applies the established README policy.
        publisher_tree["README.md"] = {
            "size": 724,
            "lfs": {"oid": "6f43151b34a95ac409d70a5ed81c45611004ea669b46431d30f6183d5d0ed253"},
        }
        with mock.patch.object(CAPTURE, "snapshot", return_value=publisher_tree), \
                mock.patch.object(CAPTURE, "request",
                                  side_effect=AssertionError("capture fetched a file body")):
            captured = CAPTURE.capture(choice)

        self.assertEqual(captured, manifest)
        paths = {entry["source_path"] for entry in manifest["files"]}
        self.assertEqual(len(paths), 15)
        self.assertEqual(sum(entry["size"] for entry in manifest["files"]), 20_429_166_969)
        self.assertNotIn("README.md", paths)
        self.assertIn("generation_config.json", paths)
        self.assertEqual(
            next(entry["sha256"] for entry in manifest["files"]
                 if entry["source_path"] == "config.json"),
            "a822a9e48b0aafbe3144ec37d4fb067e178ed96615ce6e4420b3149893cc5767",
        )
        self.assertEqual(manifest["revision"],
                         "38740b847e4cb78f352aba30aa41c76e08e6eb46")
        self.assertEqual(manifest["minimum_macos"], "14.0")
        self.assertEqual(manifest["quantization"], "MLX 4-bit")
        self.assertIn("per-tensor 8-bit gate overrides", manifest["quantization_source"])
        self.assertIs(manifest["production_allowed"], True)
        self.assertNotIn("default", manifest)
        qualification = manifest["qualification"]
        self.assertEqual(qualification["status"], "unqualified")
        # Installable and dispatchable from 2026-10-27 (owner: "We no longer block or lock any
        # model."), so the RAM floor is the only gate and it must be published rather than left
        # null - null reads as "copies everywhere", which neither of these weights can honour.
        self.assertEqual(qualification["minimum_unified_memory_gb"], 24)
        self.assertEqual(qualification["recommended_unified_memory_gb"], 32)
        self.assertIn("PROVISIONAL, NOT MEASURED", qualification["memory_evidence"])
        self.assertIsNone(qualification["tested_usable_context_tokens"])

    def test_gemma3_12b_capture_reproduces_runtime_manifest_offline(self):
        manifest = self.manifests["gemma-3-12b-it-4bit"]
        choice = next(item for item in CAPTURE.CHOICES
                      if item["package_id"] == "gemma-3-12b-it-4bit")
        publisher_tree = {
            entry["source_path"]: {
                "size": entry["size"], "lfs": {"oid": entry["sha256"]},
            }
            for entry in manifest["files"]
        }
        publisher_tree["README.md"] = {
            "size": 945,
            "lfs": {"oid": None},
        }
        publisher_tree[".gitattributes"] = {
            "size": 1570,
            "lfs": {"oid": None},
        }
        with mock.patch.object(CAPTURE, "snapshot", return_value=publisher_tree), \
                mock.patch.object(CAPTURE, "request",
                                  side_effect=AssertionError("capture fetched a file body")):
            captured = CAPTURE.capture(choice)
        self.assertEqual(captured, manifest)
        paths = {entry["source_path"] for entry in manifest["files"]}
        self.assertEqual(len(paths), 13)
        self.assertEqual(sum(entry["size"] for entry in manifest["files"]), 8_068_018_787)
        self.assertNotIn("README.md", paths)
        self.assertNotIn(".gitattributes", paths)
        self.assertEqual(manifest["revision"],
                         "86cc6a8dedbc456dd0e4af01a9d09f396f77e558")
        self.assertEqual(manifest["engine"], "chat-mlx")
        self.assertEqual(manifest["minimum_macos"], "14.0")
        self.assertEqual(manifest["quantization"], "MLX 4-bit")
        self.assertIs(manifest["production_allowed"], True)
        self.assertNotIn("default", manifest)
        qualification = manifest["qualification"]
        self.assertEqual(qualification["status"], "unqualified")
        self.assertEqual(qualification["minimum_unified_memory_gb"], 16)
        self.assertEqual(qualification["recommended_unified_memory_gb"], 24)
        self.assertIn("PROVISIONAL, NOT MEASURED", qualification["memory_evidence"])
        self.assertIsNone(qualification["tested_usable_context_tokens"])

    def test_qualified_gemma4_capture_reproduces_verified_runtime_manifest_offline(self):
        manifest = self.manifests["gemma4-26b-a4b-qat-4bit"]
        choice = next(item for item in CAPTURE.CHOICES
                      if item["package_id"] == "gemma4-26b-a4b-qat-4bit")
        publisher_tree = {
            entry["source_path"]: {
                "size": entry["size"], "lfs": {"oid": entry["sha256"]},
            }
            for entry in manifest["files"]
        }
        publisher_tree["README.md"] = {
            "size": 363,
            "lfs": {"oid": "9995a0f5587351fb94c01c37df97179aeaba7652e78824805cb7093bfdad5d67"},
        }
        with mock.patch.object(CAPTURE, "snapshot", return_value=publisher_tree), \
                mock.patch.object(CAPTURE, "request",
                                  side_effect=AssertionError("capture fetched a file body")):
            captured = CAPTURE.capture(choice)

        self.assertEqual(captured, manifest)
        paths = {entry["source_path"] for entry in manifest["files"]}
        self.assertEqual(len(paths), 10)
        self.assertEqual(sum(entry["size"] for entry in manifest["files"]), 15_641_239_295)
        self.assertNotIn("README.md", paths)
        self.assertIn("generation_config.json", paths)
        self.assertEqual(
            next(entry["sha256"] for entry in manifest["files"]
                 if entry["source_path"] == "config.json"),
            "29910322dd085f45c8f95c6c0f1611b20f722d6f6c8394321b34817e98a972fa",
        )
        self.assertEqual(manifest["revision"],
                         "0e3cbab38ce568cf6e23543010d08d03b731910c")
        self.assertEqual(manifest["minimum_macos"], "14.0")
        self.assertEqual(manifest["quantization"], "QAT MLX 4-bit")
        self.assertIn("per-tensor precision overrides", manifest["quantization_source"])
        self.assertIs(manifest["production_allowed"], True)
        self.assertNotIn("default", manifest)
        qualification = manifest["qualification"]
        self.assertEqual(qualification["status"], "qualified")
        self.assertEqual(qualification["minimum_unified_memory_gb"], 32)
        self.assertEqual(qualification["recommended_unified_memory_gb"], 32)
        self.assertEqual(qualification["context_window_tokens"], 49_152)
        self.assertEqual(qualification["tested_usable_context_tokens"], 51_035)
        self.assertNotIn("min_free_memory_gb", qualification)
        self.assertEqual(manifest["max_output_tokens"], 8_192)
        self.assertIn("MEASURED", qualification["memory_evidence"])
        self.assertIn("physical 32 GiB M2 Max", qualification["memory_evidence"])
        self.assertIn("3x1,200-word", qualification["reason"])
        self.assertIn("985, 800 and 805", qualification["reason"])
        self.assertIn("owner accepted", qualification["reason"])
        self.assertIn("49,152", qualification["reason"])
        self.assertIn("combined input plus reserved output", qualification["reason"])
        self.assertIn("curated hardware budget", qualification["reason"])
        self.assertIn("51,035", qualification["reason"])
        self.assertIn("14/16 sections", qualification["reason"])
        self.assertIn("262,144", qualification["reason"])
        self.assertIn("model-declared", qualification["reason"])

    def test_all_three_moe_candidates_are_admitted_and_each_publishes_a_ram_floor(self):
        """No MoE candidate is withheld by name; being too big is the RAM floor's job.

        Until 2026-10-27 `production_allowed: false` carried two facts at once - "not measured"
        and "not offered" - so a Mac could not install one even where it fit. The owner removed
        the name lock ("They are models we can run so we should have them installable. Able to
        run on the machine or not is based off its RAM. We no longer block or lock any model."),
        which makes a published floor mandatory: a null floor reads as "fits every Mac" to the
        kit copy and to dispatch.
        """
        gemma = self.manifests["gemma4-26b-a4b-qat-4bit"]
        self.assertIs(gemma["production_allowed"], True)
        self.assertEqual(gemma["qualification"]["status"], "qualified")
        for package_id in ("nemotron-lightning-4bit", "qwen36-35b-4bit"):
            with self.subTest(package=package_id):
                manifest = self.manifests[package_id]
                self.assertIs(manifest["production_allowed"], True)
                self.assertEqual(manifest["qualification"]["status"], "unqualified")
                self.assertGreaterEqual(
                    manifest["qualification"]["minimum_unified_memory_gb"], 24)
                self.assertIsNone(
                    manifest["qualification"]["tested_usable_context_tokens"])
        gemma3 = self.manifests["gemma-3-12b-it-4bit"]
        self.assertIs(gemma3["production_allowed"], True)
        self.assertEqual(gemma3["qualification"]["status"], "unqualified")
        self.assertEqual(gemma3["qualification"]["minimum_unified_memory_gb"], 16)
        self.assertEqual(gemma3["qualification"]["recommended_unified_memory_gb"], 24)
        self.assertIn("PROVISIONAL, NOT MEASURED", gemma3["qualification"]["memory_evidence"])
        self.assertIsNone(gemma3["qualification"]["tested_usable_context_tokens"])
        self.assertIs(self.manifests["qwen3-5-9b-4bit"]["default"], True)
        self.assertNotIn("default", gemma)
        self.assertNotIn("default", gemma3)

    def test_two_default_packages_are_refused(self):
        manifests = [{"package_id": "one", "default": True}, {"package_id": "two", "default": True}]
        problems = CAPTURE.duplicate_default_problems(manifests)
        self.assertEqual(len(problems), 1)
        self.assertIn("one", problems[0])
        self.assertIn("two", problems[0])
        self.assertEqual(CAPTURE.duplicate_default_problems(manifests[:1]), [])

    def test_each_engine_may_declare_its_own_default(self):
        manifests = [
            {"package_id": "chat", "engine": "chat-mlx", "default": True},
            {"package_id": "subtitle", "engine": "subtitle-mlx", "default": True},
        ]
        self.assertEqual(CAPTURE.duplicate_default_problems(manifests), [])

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
            # Updated only by a deliberate recapture of the exact pinned revisions. **All values
            # were refreshed on 2026-09-26**: the capture tool gained `&limit=100` on its tree
            # URL (Hub paginates at 50, so a large repo could silently lose weights), and that
            # one field changed every manifest's canonical bytes.
            #
            # The three Video packages were captured in the same pass.
            # Digest moved 2026-10-28: the floor moved 16 -> 8 GB so the fleet's measured tier
            # lives in the manifest rather than in the kit's and the Hub's own copies of it. The
            # pinned revision and the file inventory are unchanged.
            "flux2-klein-4b-mlx-4bit": "179f8b79f5b29506ea30e12c13b86f062d2e63525ed7231daba4b71e2cbab2d9",
            # ST-16: captured 2026-09-24 from the pinned revision. 15 model-data files,
            # 5,907,438,792 bytes; the publisher's dotfiles, README and three example
            # images are excluded, so this total is not the 5,911,138,787-byte snapshot.
            "z-image-turbo-mflux-4bit": "07164342f7eeb72ad97466b8a1b2c3af1429b3f170271e238804eb4cdd5f4738",
            # Recaptured 2026-09-25: the qualification is now a MEASUREMENT rather than
            # catalog arithmetic. Three 16 GB Macs (M1, M2, M4) each completed a 256-token
            # completion at 12.40-18.60 tok/s with peak MLX 5.23 GB, so `status` moves from
            # `unqualified` to `qualified` and `memory_evidence` records the runs and their
            # source. Nothing about the pinned revision or the file inventory changed.
            # Baseline remains the single default Chat package; the default declaration is part
            # of both its producer entry and its frozen manifest identity.
            "qwen3-5-9b-4bit": "b309fa1bd8a71bf3287139a6e5fed723c5651bf1582a02f3d219928b2bf3dbe2",
            # Nemotron 3.5 Lightning 30B-A3B MLX 4-bit: ten runtime files / 17,792,581,692
            # bytes. The independently verified full stock package has an eleventh file,
            # README.md; runtime capture excludes it by the catalog's established policy.
            # Digest moved 2026-10-27: `production_allowed` became true and a PROVISIONAL 24/32 GB
            # weight-size floor was published, so the name lock is gone and RAM is the only gate.
            # No publisher file, revision or byte count changed.
            "nemotron-lightning-4bit": "a29b23f3a6a76e1d09966583ea20811204ae5f82301f0a03311688ac9ab7ee5e",
            # Qwen 3.6 35B-A3B MLX 4-bit: 15 runtime files / 20,429,166,969 bytes.
            # The independently verified full 16-file SSD stock also carries README.md;
            # runtime capture excludes only that 724-byte publisher file.
            # Digest moved on 2026-10-27 for the same reason as Nemotron: admitted by name, RAM
            # floor published at a PROVISIONAL 24/32 GB (the package is 19.03 GiB of weights).
            "qwen36-35b-4bit": "348569723abbba433f4fd5089081f1da0e0a802a9f3113c8ee6d2c3fe5d5a4f1",
            # Gemma 4 26B-A4B QAT MLX 4-bit: 10 runtime files / 15,641,239,295 bytes.
            # The independently verified 11-file SSD stock remains whole; runtime capture
            # excludes only its 363-byte README.md. Only the 32 GiB qualification metadata
            # changed here; the pinned revision and every publisher file hash stayed fixed.
            "gemma4-26b-a4b-qat-4bit": "db49cdedea6effed5dc53baaa57e93f1c78fb2e3d6583494e13994be26df1947",
            # Gemma 3 12B Instruct MLX 4-bit: 13 runtime files / 8,068,018,787 bytes.
            # Admitted 2026-10-27 as chat-mlx package with a PROVISIONAL 16/24 GB weight-size floor
            # (7.51 GiB of weights cannot fit 8 GB). The pinned revision and verified file hashes
            # are identical to the former text-encoder capture.
            "gemma-3-12b-it-4bit": "0740468a03143c9e80aec876875e3ab26ade0ec0b27953def5872bef7ca1081c",
            # Qwen Image 2.1 Turbo MLX 4-bit: 19 files / 9,963,443,598 bytes, captured
            # 2026-10-12 from the pinned public mflux-community checkpoint. Owner-adopted for
            # normal 16 GB+ production on 2026-10-13; Turbo measurements remain unpublished.
            "qwen-image-2-1-turbo-mflux-q4": "6e6398ea10c9893db2a5d4554bde5614c6c781d79f146173e01848702bfe4e69",
            "omnivoice-bfloat16": "f2e761bd958ca3d84c6e1b8470ce7720a4ce8ded34fb57733b870f8c07d70bb4",
            "qwen3-tts-12hz-0-6b-base-8bit": "4d9e8048e6484e0225c5fb1b5644b8ee57cb969e6a6c10feb05865b330a033eb",
            "qwen3-tts-12hz-1-7b-base-4bit": "4e7c1d4f4dce6d3a65c4787052ee4772468fa23adc53ff5fefc139392a8aacbe",
            # Recaptured: the 4-bit VoxCPM tier's published floor now records the owner's
            # 8 GB observation instead of the earlier inventory guidance.
            "voxcpm2-4bit": "d62a7dde09e4bdd8a8021a2bd6cdadc461cbb0650f138036aa6f91874cafbee7",
            "voxcpm2-8bit": "9082500194cfb29141b707457922df334561c726cce796feaf76019da1bed3d5",
            "voxcpm2-bf16": "b57ee4c4eb8924fb090572430eb47b15af0793f7675145ed118c7cb9ce51e229",
            "whisper-base": "381382765495a7e2962ff67be6cd5c6a4c9bfc45d9588c94af31e96c039a5c16",
            "whisper-small": "7c854e5c5e2ef5f61c1d50885f4052ae3e57345968ef99d3b70c69e0902938e6",
            # The three Whisper manifests now declare their own `subtitle` contract (and turbo the
            # catalog's single `"default": true`), which the Subtitle worker reads instead of
            # inventing multilingual/long-form capabilities. Their content changed, so their
            # digests did; the capture tool carries those blocks through `--write` verbatim.
            "whisper-turbo": "482d5b40438bdc7712d8955ada1bbc66adb0f4d28cab0845eade5311248484dd",
            # Adopted from the local Voice Studio cache rather than recaptured over the
            # network: the same pinned revisions, digests of the canonical manifests.
            # Recaptured 2026-09-24 with one companion spelling: `dependencies` was the first
            # field and `requires_packages` is the one every reader resolves, so the seven
            # manifests that still carried the legacy key were migrated and the key removed.
            "chatterbox-4bit": "6831bbddeaea3645d88a35878cddb8ab9d26749f7917704a1f117f0946b68409",
            "chatterbox-8bit": "1f015b5253fab1ee7c77de1fa7741748552c30b1658d08a8cf6986262d49a0f8",
            "fish-audio-s2-pro-8bit": "f14e76210a087317bd917adae5dd3c4fc5cdc2652b35acdcf19490c1a2df406f",
            "fish-audio-s2-pro-bf16": "9db7d62dc5cb26e170c46108afcd31658eb09cc764868058c436a74fd50a3837",
            # Fish S2 Pro 4-bit, captured 2026-10-02 at majentik revision dda28aa6: 10 files /
            # 4,450,615,835 bytes. The publisher's `codec.pth` is NOT listed (mlx-audio 0.5.7 cannot load
            # it and silently decodes noise); `codec.safetensors` comes from the 8-bit package's pinned
            # revision instead (bit-identical tensors), recorded as a second source. LICENSE.md + NOTICE kept.
            "fish-audio-s2-pro-4bit": "685152d38105e1503c9f867d50d19348a6fb237a876b62aeb2775b6a24ebc679",
            "s3tokenizerv2": "523d8ed23e680e453987d5b0c080db21140e5df867c9990ed34b1c4fc0006ea6",
            # Stable Audio 3's MLX bundle, captured from the ungated optimized repo.
            "stable-audio-3-medium": "e40bdda5250897a7d23cf4d34084eaf26a2188441bfbb546f1dea7e738f982dc",
            # Khmer VITS families captured 2026-09-24 from the pinned revisions.
            "khmer-tts-mms": "91dd3ffd9f224aaace05add8518c69151b015936d905eb113db1ccbe2713a537",
            "khmer-tts-female-onnx": "763ca6d13c3f9d9cedc9ea28348a7045da2e7102e6a1747021b214f8dfa92c3a",
            # Pocket TTS 4-bit and its two companions, captured 2026-09-24 (ST-13.A). The main
            # package is 3 files / 89,313,624 bytes; the tokenizer is one 59,339-byte
            # sentencepiece model and the preset voices are 8 embeddings / 4,334,272 bytes. All
            # three come from the publisher's own tree at the pinned revisions.
            "pocket-tts-4bit": "64244e1c45d964e903983097cb61a59c0a7a4ae326380b7268e059303cd4d373",
            # Breeze TTS 2 4-bit, captured 2026-09-29 at revision 3a06d26b: 12 files /
            # 3,042,440,328 bytes. Twelve rather than the publisher's 16 because the capture
            # excludes `.gitattributes`, the publisher's `README.md` and a demo video — none of
            # which is inference data. `LICENSE` and `NOTICE` are kept: a pack's licence travels
            # with it.
            # Digest moved 2026-10-28 for the same reason as FLUX.2 Klein 4-bit above: the floor
            # moved 16 -> 8 GB and the measurement moved into `memory_evidence`. The pinned
            # revision and the file inventory are unchanged.
            "breeze-tts-2-mlx-4bit": "32b1bfec5e8bd05fdc489b7ace2c84937bacf268afc1e7b332bea2af1b153e1a",
            # Qwen3-TTS 0.6B Base 4-bit, added 2026-10-02 (owner: keep both 0.6B builds) at revision
            # 0d6bb6fe: 12 files, hashed from the local snapshot (its speech_tokenizer files are
            # byte-identical to the 8-bit package's).
            "qwen3-tts-12hz-0-6b-base-4bit": "2febc1a5a1184a8e3c5335dca16e62b7db0aa8c863c5deb3d27fe7a8cec212cc",
            "pocket-tts-tokenizer": "24fc05ee633df4219c1bec5480742b1ff5c7d067eb42af4ceba0aa8e547692cd",
            "pocket-tts-preset-voices": "b5c804a1ab6346c1f975690c61ac563c6ff3ad862e44012f1bb52097c571a2c7",
            # ── Video: the LTX pack and its text encoder (Lance retired 0.6.120) ──
            # Captured 2026-09-26. LTX's manifest is the first in this catalog whose status is
            # `qualified` on the strength of a full generation driven through a *worker*, not a
            # standalone runner, and its `memory_evidence` names both runs.
            # Refreshed 2026-09-27 (0.6.83) with the true-16:9 measurement: a third run at
            # 1024x576, 49 frames, 351.6 s, 9.36 GB peak MLX Metal, recorded in the manifest's
            # `memory_evidence` and `reason`.
            # ── Music: YuE2, the lane's second engine, and its decoder companion ──
            # Captured 2026-09-27 from `vanch007/mlx-Yue2-3B` (9 files / 9,920,168,755 bytes) and
            # `m-a-p/YuE2-Vae` (7 files / 530,542,479 bytes). Both carry a MEASURED tier from three
            # runs on this 32 GB Mac: 10.79 GB / 11.81 GB / 11.93 GB peak by sampled `footprint`.
            # The generator's capture deliberately includes `ar-bf16.safetensors`, the 4.33 GB file
            # the 8-bit AR was quantized from, because the port's `verify_conversion` refuses a tree
            # without it. 0.6.168: the generator also carries the publisher's `LICENSE` (20,309 bytes;
            # 10 files / 9,920,189,064 bytes), which the port's load check requires.
            "yue2-3b-mlx": "ff5e47647ca8167179a6502e084cb29c6cb74da80c8d03ad8bf8d2e8e1cc2957",
            "yue2-vae": "9baea3673de6c5ad455888d1f0aa8afc717a60fe9cf69f18caa716617055257f",
            # Phonon-2 (owner decision 2026-10-05), captured at selcukkubur/Phonon-2-mlx 71936ba3: 5 files /
            # 189,498,804 bytes - the packed model.safetensors, Parakeet's config.json, ATTRIBUTION.md and
            # both licence texts (byte-identical to FermionResearch/Phonon-2's SHA256SUMS entries).
            "phonon-2-mlx": "d2e7ee4e41a9d9824fad54eeae6451d49482f2622b3259c077af074db238686e",
            # Qwen Image 2.1 4-bit ships as two distinct immutable packages. The native
            # mlx-serve package declares its conditional macOS 26.2 floor and is qualified on
            # 24 GB (physical M4 run, 2026-10-08); the low-RAM mflux package keeps macOS 14 and
            # is qualified on 16 GB by the owner's physical 16 GiB M4 run of 2026-10-08.
            "qwen-image-2-1-mlx-serve-4bit": "6a0e552d93718bee1f7ef7546dad19ea3798e7506d2b32d099a9af430ff4d97d",
            # 2026-10-26: the licence record was corrected from Apache-2.0 (the mlx-community
            # conversion's licence) to qwen-research (the weights'), which changes this digest and
            # nothing else. The licence is not part of `manifest_digest`, so installed receipts
            # stay valid.
            "qwen-image-2-1-mflux-q4": "475a994ada0bab01a6c13f569ddfc91b573c8ff086cae6f8a7f9f4b96f6eda35",
            # BGE small en v1.5 MLX bf16 (owner decision 2026-10-11), captured at
            # mlx-community/bge-small-en-v1.5-bf16 0e415031: 10 files / 67,702,966 bytes.
            "bge-small-en-v1-5-bf16": "726de16948b3602205ff7c34fac9ccfe096df263a8b0e59949de4b858d3554c9",
            # Vision (owner decisions 2026-10-11), captured 2026-10-11: Depth Anything V2 Small
            # (Apache-2.0, 3 files / 99,175,385 bytes) and Florence-2 base ft / large ft (MIT,
            # florence-community transformers-native safetensors, 11 files / 468,554,144 and
            # 1,546,333,353 bytes).
            "depth-anything-v2-small": "855881450f41103025797c1229db0e4f2497ee2065335c9028116a0637fc14ba",
            "florence-2-base-ft": "053b462afd0d40dfea570c6e769aa9e46425fa290f88cbce6dbea9edef99a23b",
            "florence-2-large-ft": "5e30ad737154f25afb01d524d9f2ba6a4aeb7779cbd9ee1f2b640850fcc0cd56",
        }
        # The digest map is the frozen-content half of the invariant: its keys are checked against
        # the manifests on disk (never against a hand-written list), so a package that appears
        # without a digest fails here instead of passing unnoticed. Change a digest only when the
        # manifest's own content changed - adding a capture spec does not change a manifest.
        self.assertEqual(set(expected_digests), set(self.manifests))
        for package_id, manifest in self.manifests.items():
            payload = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
            self.assertEqual(hashlib.sha256(payload).hexdigest(), expected_digests[package_id])

    def test_phonon2_is_pinned_attributed_unqualified_and_optional(self):
        """Phonon-2 (owner decision 2026-10-05): every byte pinned, CC-BY attribution shipped."""
        manifest = self.manifests["phonon-2-mlx"]
        revision = "71936ba3bf6479d299b97fdfe2d53e36d9375fe5"
        self.assertEqual((manifest["model_id"], manifest["revision"], manifest["engine"]),
                         ("selcukkubur/Phonon-2-mlx", revision, "subtitle-mlx"))
        self.assertEqual({entry["path"]: (entry["size"], entry["sha256"]) for entry in manifest["files"]}, {
            "model/model.safetensors": (189223871, "56d9ee40a251ad04991f6346c7be82f6655dd12dbfa439b94cfd7c829eb0b7be"),
            "model/config.json": (244093, "f320f1292511f34ec47f513755fe20fd01dbfc09a925d42730e66059a6e1ef4c"),
            "model/ATTRIBUTION.md": (825, "b2ad6ae5199689e432dd85654d6de8084fa5eba8bd4d97d7ad85e1e24509e9ee"),
            # Byte-identical to FermionResearch/Phonon-2's own SHA256SUMS.txt entries.
            "model/LICENSE-WEIGHTS-CC-BY-4.0.txt": (18657, "9ba9550ad48438d0836ddab3da480b3b69ffa0aac7b7878b5a0039e7ab429411"),
            "model/LICENSE-CODE-Apache-2.0.txt": (11358, "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"),
        })
        for entry in manifest["files"]:
            self.assertEqual(entry["urls"], [f"https://huggingface.co/selcukkubur/Phonon-2-mlx/resolve/{revision}/"
                                             f"{entry['source_path']}"])
        self.assertEqual(manifest["license"]["weights"], "CC-BY-4.0")
        for owner in ("FermionResearch", "NVIDIA"):
            self.assertIn(owner, manifest["license"]["attribution"])
        self.assertTrue(set(manifest["license"]["files"]) <= {e["path"] for e in manifest["files"]})
        qualification = manifest["qualification"]
        self.assertEqual(qualification["status"], "unqualified")
        self.assertEqual(qualification["minimum_unified_memory_gb"], 8)
        self.assertLess(qualification["min_free_memory_gb"], 2.0, "a small free floor, below the Hub default")
        self.assertTrue(qualification["min_free_memory_note"])
        self.assertNotIn("default", manifest, "Whisper stays the default subtitle model")
        self.assertEqual(manifest["subtitle"], {"family": "phonon2", "languages": ["en"], "segment_timestamps": True,
                                                "word_timestamps": True, "long_form": True})

    def test_fish_4bit_ships_the_8bit_codec_instead_of_the_publishers_codec_pth(self):
        """The 4-bit publisher's `codec.pth` makes mlx-audio 0.5.7 load the LLM as the codec (noise).

        The package must carry the 8-bit package's exact `codec.safetensors` bytes, from its pinned
        revision, recorded as a second source; and it must never list `codec.pth`.
        """
        four, eight = self.manifests["fish-audio-s2-pro-4bit"], self.manifests["fish-audio-s2-pro-8bit"]
        files = {entry["path"]: entry for entry in four["files"]}
        self.assertFalse(any(path.endswith("codec.pth") for path in files))
        codec = files["model/codec.safetensors"]
        eight_codec = next(e for e in eight["files"] if e["path"] == "model/codec.safetensors")
        self.assertEqual((codec["sha256"], codec["size"]), (eight_codec["sha256"], eight_codec["size"]))
        self.assertEqual(codec["urls"], eight_codec["urls"])
        self.assertIn(f"/resolve/{eight['revision']}/", codec["urls"][0])
        for entry in four["files"]:
            if entry is not codec:
                self.assertIn(f"/majentik/fishaudio-s2-pro-MLX-4bit/resolve/{four['revision']}/", entry["urls"][0])
        self.assertIn({"destination": "model/codec.safetensors", "repo": eight["model_id"],
                       "revision": eight["revision"]},
                      [{k: c[k] for k in ("destination", "repo", "revision")}
                       for c in four["source"]["layout"].get("companions", [])])
        self.assertIn(eight["revision"], {s["revision"] for s in four["source"]["sources"]})
        # The licence travels with the pack: the Fish Audio Research License requires its NOTICE.
        self.assertTrue({"model/LICENSE.md", "model/NOTICE"} <= set(files))

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
            qualification = manifest["qualification"]
            if manifest.get("production_allowed", True):
                self.assertGreaterEqual(qualification["minimum_unified_memory_gb"], 8)
                self.assertGreaterEqual(qualification["recommended_unified_memory_gb"],
                                        qualification["minimum_unified_memory_gb"])
            else:
                self.assertIs(manifest["production_allowed"], False)
                self.assertEqual(qualification["status"], "unqualified")
                self.assertIsNone(qualification["minimum_unified_memory_gb"])
                self.assertIsNone(qualification["recommended_unified_memory_gb"])
                self.assertIsNone(qualification["tested_usable_context_tokens"])


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
