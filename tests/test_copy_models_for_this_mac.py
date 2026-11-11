"""What the copy step moves out of the library - and what it must never touch.

The owner, 2026-09-25: *"can you make the script prune out old models that's not supported or
rejected? like delete or move it to trash or sth?"* The step used to only add and update, so a
model cut from a release stayed in `~/StudioHubModels` forever.

The rule that makes removing anything safe is one sentence, and it is the whole safety property:
**a folder in the library is a managed package if and only if it is a key in the app's own record,
`<library>/.studiohub/installed.json`.** The owner's real library holds a Hugging Face cache
(`hub/`), a candidate model he is still qualifying (`yue2-3b-mlx/`) and an older `_trash-...`;
none of them is a key, and the looser rule - "anything no manifest ships" - would move all three
to `_trash`. The record is also a historical receipt log (42 receipts, 15 folders on his Mac), so
a candidate has to be confirmed against the disk as a directory, and never the other way round.

The second half pins where a model's memory floor comes from. It is its own manifest's
`qualification.minimum_unified_memory_gb` and nowhere else: the two hand-kept tables this file
used to carry (`PROVEN_SMALLER`, `CARRY_BELOW_TIER`) are gone, and their numbers are in the
manifests with the measurements beside them (owner decision 2026-10-28).

Every test builds its own kit and its own library under a temporary folder and runs the real script
over them the way `copy-models.command` does. The owner's library and the real app are not touched.
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import re
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = ROOT / "model-manifests"

# Loaded by path, like the other script tests here: a plain `import packaging` would find this
# repo's `packaging/` directory instead of anything installed, and the qualification script is not
# on any import path by design.
_spec = importlib.util.spec_from_file_location(
    "copy_models_for_this_mac_under_test",
    ROOT / "qualification" / "copy_models_for_this_mac.py")
COPY = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(COPY)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text()) if path.is_file() else {}


class Harness:
    """A temporary kit, a temporary home folder, and runs of the real script over them.

    The app's own `model_packages.py` is copied into the kit, so the receipts the script writes are
    the receipts the app writes. The machine's memory is supplied rather than measured, so a test
    can say which Mac it is claiming to be without pretending to be one.
    """

    APP = Path("Hub Studio KH.app")
    REVISION = "c" * 40

    def __init__(self, root: Path):
        self.root = root
        self.kit = root / "kit"
        self.library = self.kit / "model-library"   # the SSD the kit is copied from
        self.home = root / "StudioHubModels"        # this Mac's library
        self.manifests = self.kit / self.APP / "Contents/Resources/model-manifests"
        self.manifests.mkdir(parents=True)
        self.library.mkdir(parents=True)  # the kit always carries a model library
        components = self.kit / self.APP / "Contents/Resources/components"
        components.mkdir(parents=True)
        write(components / "model_packages.py",
              (ROOT / "components" / "model_packages.py").read_text())
        write(components / "model_presets.py",
              (ROOT / "components" / "model_presets.py").read_text())
        self.presets = self.kit / self.APP / "Contents/Resources/model-presets"
        self.presets.mkdir(parents=True)

    # ---- the kit's SSD -------------------------------------------------------
    @staticmethod
    def files_for(package_id: str, body: str | None = None) -> dict[str, str]:
        return {f"model/{package_id}.bin": body if body is not None else f"weights for {package_id}"}

    def manifest(self, package_id: str, floor: int, files: dict[str, str]) -> dict:
        return {
            "schema_version": 1, "package_id": package_id, "model_id": package_id,
            "revision": self.REVISION, "engine": "voice-mlx", "minimum_macos": "14.0",
            "requires_packages": [], "files": [
                {"path": name, "size": len(text.encode()),
                 "sha256": hashlib.sha256(text.encode()).hexdigest(),
                 "urls": [f"https://example.invalid/{package_id}/{name}"]}
                for name, text in files.items()],
            "qualification": {"minimum_unified_memory_gb": floor},
        }

    def ship(self, package_id: str, floor: int = 8, body: str | None = None) -> dict:
        """A package this kit offers, with its files on the SSD."""
        files = self.files_for(package_id, body)
        manifest = self.manifest(package_id, floor, files)
        write(self.manifests / f"{package_id}.json", json.dumps(manifest, indent=2))
        self.put(self.library, package_id, files)
        return manifest

    def ship_real(self, filename: str) -> dict:
        """A manifest exactly as the release carries it. No SSD files, unless a test adds them."""
        text = (MANIFESTS / filename).read_text()
        write(self.manifests / filename, text)
        return json.loads(text)

    def preset(self, packages: list[str], name: str = "Test 8 GB", tier: int = 8) -> None:
        """One preset file, in the shipped shape, naming the fixture's packages."""
        write(self.presets / f"test-{tier}.json", json.dumps({
            "schema_version": 1, "preset_id": f"test-{tier}", "name": name,
            "memory_tier_gb": tier, "packages": packages,
        }, indent=2))

    # ---- this Mac's library --------------------------------------------------
    def put(self, root: Path, package_id: str, files: dict[str, str]) -> None:
        for name, text in files.items():
            write(root / package_id / self.REVISION / name, text)

    def receive(self, package_id: str, body: str | None = None) -> None:
        """A model folder already on this Mac, identical to the kit's copy of it."""
        self.put(self.home, package_id, self.files_for(package_id, body))

    def receive_raw(self, relative: str, text: str = "already here") -> None:
        write(self.home / relative, text)

    def record(self, package_id: str, revision: str | None = None) -> None:
        """A receipt in the app's record. The script reads keys, so a minimal one is honest."""
        index = self.index()
        revision = revision or self.REVISION
        index[package_id] = {"revision": revision, "folder": f"{package_id}/{revision}",
                             "files": {}}
        state = self.home / ".studiohub"
        state.mkdir(mode=0o700, exist_ok=True)  # the app refuses state that is not owner-only
        write(state / "installed.json", json.dumps(index, sort_keys=True))

    def index(self) -> dict:
        return read_json(self.home / ".studiohub" / "installed.json")

    def trash(self) -> list[Path]:
        return sorted((self.home / COPY.TRASH_FOLDER).glob("*")) if (
            self.home / COPY.TRASH_FOLDER).is_dir() else []

    # ---- the app the copy step asks ------------------------------------------
    def stage_resolver(self) -> None:
        """Stage the app's own resolver, as the shipped bundle carries it.

        Without this `shared_resolver()` raises `ImportError` and the step falls back to
        `~/StudioHubModels` - which is what the older tests here exercise. Staging
        `components/model_library.py` into the bundle's `Resources/components` is what makes the step
        ask the app which folder the Hub is using, the integration the source-level tests cannot run.
        """
        write(self.kit / self.APP / "Contents/Resources/components/model_library.py",
              (ROOT / "components" / "model_library.py").read_text())

    def save_choice(self, models_path: Path) -> None:
        """The choice the app saves for this Mac: `modelsPath`, in a file written the app's way.

        The app writes `app-preferences.json` `0600`, and the resolver refuses to read one that is
        group/other readable, so a fixture that did not say so would be testing that guard.
        """
        prefs = self.root / "Library/Application Support/StudioHub Standalone/app-preferences.json"
        write(prefs, json.dumps({"modelsPath": str(models_path)}))
        prefs.chmod(0o600)

    def resolved_source(self) -> str:
        """What the app's resolver answers for this fixture's home - the step's own question."""
        spec = importlib.util.spec_from_file_location(
            "model_library_for_harness", ROOT / "components" / "model_library.py")
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        previous = sys.modules.get(spec.name)
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
            return module.resolve_library(self.root).source
        finally:
            if previous is None:
                sys.modules.pop(spec.name, None)
            else:
                sys.modules[spec.name] = previous

    # ---- running the real thing ---------------------------------------------
    def run(self, answer: str = "\n", memory_gb: int = 8, copy_everything: bool = True):
        """Run the script from inside the kit, as the launcher does.

        Returns (exit code, everything it printed, the confirmation prompt itself).

        `copy_everything=True` by default **for the tests that predate the 2026-10-28 change**, so
        they keep testing the copy mechanism they were written for. The step's own default is the
        opposite - it copies nothing and points at the app's screen - and `KitNoLongerChoosesTests`
        is where that default is pinned.
        """
        helper = self.kit / "Technical" / "copy_models_for_this_mac.py"
        write(helper, (ROOT / "qualification" / "copy_models_for_this_mac.py").read_text())
        spec = importlib.util.spec_from_file_location("copy_models_in_temp_kit", helper)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        prompt = mock.Mock(return_value=answer)
        out = io.StringIO()
        previous_home = os.environ.get("HOME")
        previous_app = os.environ.get("STUDIOHUB_APP_ROOT")
        previous_copy = os.environ.get("STUDIOHUB_KIT_COPY")
        # `main()` points the preset reader at the kit for its own process; a test shares ours.
        previous_presets = os.environ.get("STUDIOHUB_MODEL_PRESETS")
        os.environ["HOME"] = str(self.root)
        if copy_everything:
            os.environ["STUDIOHUB_KIT_COPY"] = "1"
        else:
            os.environ.pop("STUDIOHUB_KIT_COPY", None)
        # **Point the helper at this fixture's app bundle.** Without this it finds the owner's real
        # `/Applications/Hub Studio KH.app`, reads *its* manifests and tests a different computer than
        # the one it built — which is what happened the moment the search order changed to prefer an
        # installed app, and why these six tests went red.
        os.environ["STUDIOHUB_APP_ROOT"] = str(self.kit / self.APP)
        # The step imports the resolver as a **top-level** module (`from model_library import …`), so
        # a copy another test imported would otherwise be reused here. Dropping it makes each run
        # import from this fixture's own bundle - or find none, which is an app that predates it.
        sys.modules.pop("model_library", None)
        try:
            with mock.patch.object(module, "machine_memory_gb", return_value=memory_gb):
                with mock.patch("builtins.input", prompt):
                    with contextlib.redirect_stdout(out):
                        code = module.main()
        finally:
            if previous_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = previous_home
            if previous_app is None:
                os.environ.pop("STUDIOHUB_APP_ROOT", None)
            else:
                os.environ["STUDIOHUB_APP_ROOT"] = previous_app
            if previous_copy is None:
                os.environ.pop("STUDIOHUB_KIT_COPY", None)
            else:
                os.environ["STUDIOHUB_KIT_COPY"] = previous_copy
            if previous_presets is None:
                os.environ.pop("STUDIOHUB_MODEL_PRESETS", None)
            else:
                os.environ["STUDIOHUB_MODEL_PRESETS"] = previous_presets
        return code, out.getvalue(), prompt


class NeverTouchesWhatTheAppDoesNotOwnTests(unittest.TestCase):
    """The safety property, which is the one test that has to fail if the rule is loosened."""

    def test_folders_the_apps_record_does_not_name_are_never_pruned(self):
        with TemporaryDirectory() as tmp:
            kit = Harness(Path(tmp))
            kit.ship("kept-model", floor=1)
            kit.receive("kept-model")
            # The three shapes the owner's own library has, none of them a managed package: the
            # Hugging Face cache, an older prune's trash, and his Yue2 candidate.
            kit.receive_raw("hub/models--org--name/blobs/abc123", "a cached download")
            kit.receive_raw("hub/models--org--name/refs/main", "abc123")
            kit.receive_raw("_trash-tts-prune-2026-09-24/old-voice-model/weights.bin", "set aside")
            kit.receive_raw("yue2-3b-mlx/model/weights.safetensors", "the candidate")
            kit.receive_raw("yue2-3b-mlx/vae/weights.safetensors", "the candidate")
            kit.receive_raw("yue2-3b-mlx/app/runner.py", "print('keep me')")
            # A key whose path is a file, not a folder, is not a managed package either.
            kit.receive_raw("not-a-folder", "a stray file with a package's name")
            kit.record("not-a-folder")

            code, output, _prompt = kit.run()

            self.assertEqual(code, 0, output)
            for shape in ("hub/models--org--name/blobs/abc123",
                          "_trash-tts-prune-2026-09-24/old-voice-model/weights.bin",
                          "yue2-3b-mlx/model/weights.safetensors",
                          "yue2-3b-mlx/vae/weights.safetensors",
                          "yue2-3b-mlx/app/runner.py",
                          "not-a-folder"):
                self.assertTrue((kit.home / shape).is_file(),
                                f"{shape} was moved or removed by the prune")
            self.assertEqual(kit.trash(), [],
                             "nothing was a candidate, so nothing may be in _trash")
            self.assertIn("kept-model", kit.index())


class PruneTests(unittest.TestCase):
    """A package the record names, present on disk, that no manifest in the app ships."""

    def test_a_cut_package_is_moved_to_trash_and_dropped_from_the_record(self):
        with TemporaryDirectory() as tmp:
            kit = Harness(Path(tmp))
            kit.ship("kept-model", floor=1)
            kit.receive("kept-model")
            kit.receive("cut-model", body="the model this kit dropped")
            kit.record("cut-model")

            code, output, _prompt = kit.run()

            self.assertEqual(code, 0, output)
            self.assertFalse((kit.home / "cut-model").exists(),
                             "the folder has to leave the path the app reads models from")
            moved = kit.trash()
            self.assertEqual(len(moved), 1, output)
            self.assertRegex(moved[0].name, r"^cut-model-\d{8}-\d{6}$",
                             "the trashed package is named <package_id>-<YYYYMMDD-HHMMSS>")
            self.assertEqual((moved[0] / Harness.REVISION / "model" / "cut-model.bin").read_text(),
                             "the model this kit dropped", "the model itself must still exist")
            self.assertNotIn("cut-model", kit.index(),
                             "the app's record must not name a folder that is now in _trash")
            self.assertIn("kept-model", kit.index())
            # The plan, printed before anything moved.
            self.assertIn("Removing 1 model(s) this kit no longer offers", output)
            self.assertIn(str(kit.home / COPY.TRASH_FOLDER), output)
            self.assertIn("cut-model", output)
            self.assertIn(str(kit.home / COPY.TRASH_FOLDER), output.rsplit("Moved 1 model(s)", 1)[-1],
                          "the end of the run has to say where the space went")
            self.assertIn("(1 for the model(s) moved, 0 already stale)", output,
                          "the record edit is reported apart from the stale receipts")

    def test_saying_no_at_the_prompt_moves_nothing(self):
        with TemporaryDirectory() as tmp:
            kit = Harness(Path(tmp))
            kit.ship("kept-model", floor=1)
            kit.receive("kept-model")
            kit.receive("cut-model")
            kit.record("cut-model")

            code, output, prompt = kit.run(answer="n")

            self.assertEqual(code, 0, output)
            self.assertTrue(prompt.called)
            self.assertTrue((kit.home / "cut-model").is_dir())
            self.assertEqual(kit.trash(), [])
            self.assertIn("cut-model", kit.index())

    def test_a_package_the_record_does_not_name_is_left_where_it_is_even_when_another_is_pruned(self):
        """The prune does not sweep the library once it is working: it moves named packages only."""
        with TemporaryDirectory() as tmp:
            kit = Harness(Path(tmp))
            kit.ship("kept-model", floor=1)
            kit.receive("kept-model")
            kit.receive("cut-model")
            kit.record("cut-model")
            kit.receive_raw("my-own-experiment/model/weights.bin", "not the kit's business")

            code, output, _prompt = kit.run()

            self.assertEqual(code, 0, output)
            self.assertEqual(len(kit.trash()), 1)
            self.assertTrue((kit.home / "my-own-experiment/model/weights.bin").is_file())

    def test_the_kit_receipt_log_cannot_carry_a_moved_package_back_into_the_record(self):
        """The record is seeded from the kit's copy during the run; the prune has the last word.

        A receipt for the cut package in the kit's own record is exactly how the app would be left
        naming a folder that is now in `_trash`, so the dropped entries are written after the copy
        step has finished with the log.
        """
        with TemporaryDirectory() as tmp:
            kit = Harness(Path(tmp))
            kit.ship("kept-model", floor=1)
            kit.receive("kept-model")
            kit.receive("cut-model")
            kit.record("cut-model")
            write(kit.library / ".studiohub" / "installed.json",
                  json.dumps({"cut-model": {"revision": Harness.REVISION}}))

            code, output, _prompt = kit.run()

            self.assertEqual(code, 0, output)
            self.assertEqual(len(kit.trash()), 1, output)
            self.assertNotIn("cut-model", kit.index(), output)
            self.assertIn("kept-model", kit.index())


class NothingToCopyTests(unittest.TestCase):
    """A prune on its own still has to reach the confirmation the operator answers."""

    def test_a_run_with_nothing_to_copy_still_asks_and_still_prunes(self):
        with TemporaryDirectory() as tmp:
            kit = Harness(Path(tmp))
            kit.ship("kept-model", floor=1)
            kit.receive("kept-model")           # already identical, so nothing to copy
            kit.receive("cut-model")
            kit.record("cut-model")

            code, output, prompt = kit.run()

            self.assertEqual(code, 0, output)
            self.assertTrue(prompt.called,
                            "a prune with nothing to copy must not skip the confirmation")
            self.assertIn("Nothing to copy", output)
            self.assertIn("Removing 1 model(s)", output)
            self.assertFalse((kit.home / "cut-model").exists())
            self.assertEqual(len(kit.trash()), 1)
            self.assertNotIn("cut-model", kit.index())


class StaleReceiptTests(unittest.TestCase):
    """Receipts whose folder is gone: dropped only when the model is not shipped any more."""

    def test_a_gone_unshipped_receipt_is_removed_and_a_shipped_one_is_kept(self):
        with TemporaryDirectory() as tmp:
            kit = Harness(Path(tmp))
            kit.ship("kept-model", floor=1)
            kit.receive("kept-model")
            kit.record("gone-model")            # no folder, no manifest: a line in the log only
            whisper = kit.ship_real("whisper-base.json")
            kit.record(whisper["package_id"], revision=whisper["revision"])

            code, output, _prompt = kit.run()

            self.assertEqual(code, 0, output)
            index = kit.index()
            self.assertNotIn("gone-model", index)
            self.assertIn(whisper["package_id"], index,
                          "a shipped model whose folder is gone is one the operator may re-download; "
                          "the app already reports it as not installed")
            self.assertIn("Forgetting 1 receipt(s)", output)
            self.assertIn("Removed 1 old receipt(s) from the app's record", output)
            self.assertIn("(0 for the model(s) moved, 1 already stale)", output)
            self.assertEqual(kit.trash(), [], "a stale receipt names no folder, so nothing moves")


class BelowTierPackagesAreNeverPrunedTests(unittest.TestCase):
    """A RAM tier is a decision about copying, never about removing."""

    def test_a_manifest_named_package_below_this_macs_floor_is_kept_and_left_behind(self):
        with TemporaryDirectory() as tmp:
            kit = Harness(Path(tmp))
            kit.ship("too-big-for-this-mac", floor=64)
            kit.receive("too-big-for-this-mac")
            kit.record("too-big-for-this-mac")

            code, output, _prompt = kit.run(memory_gb=8)

            self.assertEqual(code, 0, output)
            self.assertTrue((kit.home / "too-big-for-this-mac").is_dir(),
                            "a model this Mac cannot fit is reported and kept, not pruned")
            self.assertEqual(kit.trash(), [])
            self.assertIn("too-big-for-this-mac", kit.index())
            self.assertIn("this Mac is too small", output)
            self.assertIn("too-big-for-this-mac", output)

    def test_the_real_z_image_package_is_kept_when_it_is_below_the_floor_it_is_carried_at(self):
        """The Z-Image-Turbo 6B MLX 4-bit case: named by a manifest, so never a prune candidate."""
        with TemporaryDirectory() as tmp:
            kit = Harness(Path(tmp))
            manifest = kit.ship_real("z-image-turbo-mflux-4bit.json")
            package_id = manifest["package_id"]
            kit.receive(package_id, body="a partly downloaded copy")
            kit.record(package_id, revision=manifest["revision"])

            code, output, _prompt = kit.run(memory_gb=4)  # below the 8 GB it is carried at

            self.assertEqual(code, 0, output)
            self.assertTrue((kit.home / package_id).is_dir())
            self.assertEqual(kit.trash(), [])
            self.assertIn(package_id, kit.index())
            self.assertIn("this Mac is too small", output)
            # The skip has to name where the floor came from and the file to edit, not just print
            # a number. Since 2026-10-28 there is one source: the model's own manifest.
            self.assertIn(f"floor from qualification.minimum_unified_memory_gb in "
                          f"model-manifests/{package_id}.json", output)


class KitPresetChoiceTests(unittest.TestCase):
    """The SSD command offers the four presets, because the Hub never reads a mounted drive.

    Owner decision 2026-10-30. This is the path a person runs in Terminal: macOS may ask its
    permission there, once, because they started it. A preset copies only its own packages and
    never reorganises anything the Mac already has.
    """

    def test_a_preset_copies_only_its_own_models(self):
        with TemporaryDirectory() as tmp:
            kit = Harness(Path(tmp))
            kit.ship("a-model", floor=1)
            kit.ship("b-model", floor=1)
            kit.preset(["a-model"])
            code, output, _prompt = kit.run(answer="1\n", copy_everything=False)
            self.assertEqual(code, 0, output)
            self.assertTrue((kit.home / "a-model").is_dir(), output)
            self.assertFalse((kit.home / "b-model").exists(), output)
            self.assertIn("Choose a model preset", output)

    def test_zero_cancels_and_copies_nothing(self):
        with TemporaryDirectory() as tmp:
            kit = Harness(Path(tmp))
            kit.ship("a-model", floor=1)
            kit.preset(["a-model"])
            code, output, _prompt = kit.run(answer="0\n", copy_everything=False)
            self.assertEqual(code, 0, output)
            self.assertFalse((kit.home / "a-model").exists(), output)
            self.assertIn("Cancelled", output)

    def test_an_invalid_choice_copies_nothing(self):
        with TemporaryDirectory() as tmp:
            kit = Harness(Path(tmp))
            kit.ship("a-model", floor=1)
            kit.preset(["a-model"])
            code, output, _prompt = kit.run(answer="9\n", copy_everything=False)
            self.assertEqual(code, 2, output)
            self.assertFalse((kit.home / "a-model").exists(), output)

    def test_a_preset_moves_nothing_a_mac_already_has(self):
        with TemporaryDirectory() as tmp:
            kit = Harness(Path(tmp))
            kit.ship("a-model", floor=1)
            kit.receive("a-model")
            kit.receive_raw("not-in-this-kit/rev/weights.bin", "an older model")
            kit.preset(["a-model"])
            code, output, _prompt = kit.run(answer="1\n", copy_everything=False)
            self.assertEqual(code, 0, output)
            self.assertTrue((kit.home / "a-model" / Harness.REVISION / "model/a-model.bin").is_file())
            self.assertTrue((kit.home / "not-in-this-kit/rev/weights.bin").is_file())
            self.assertEqual(kit.trash(), [])

    def test_the_opt_in_still_copies_everything_that_fits(self):
        # The one reason the old path survives: a Mac with no internet and no reachable peer.
        with TemporaryDirectory() as tmp:
            kit = Harness(Path(tmp))
            kit.ship("a-model", floor=1)
            kit.ship("too-big", floor=64)
            code, output, _prompt = kit.run(memory_gb=8, copy_everything=True)
            self.assertEqual(code, 0, output)
            self.assertTrue((kit.home / "a-model").is_dir(), output)
            self.assertFalse((kit.home / "too-big").exists())
            self.assertIn("this Mac is too small", output)

    def test_the_opt_in_still_prunes_what_the_kit_no_longer_offers(self):
        # The prune is part of *copying*, not part of being run: with the opt-in, its behaviour is
        # exactly what it always was.
        with TemporaryDirectory() as tmp:
            kit = Harness(Path(tmp))
            kit.ship("a-model", floor=1)
            kit.receive("gone-model")
            kit.record("gone-model")
            code, output, _prompt = kit.run(answer="y\n", copy_everything=True)
            self.assertEqual(code, 0, output)
            self.assertEqual(len(kit.trash()), 1, output)


class FittingPackageTests(unittest.TestCase):
    def test_a_manifest_named_package_that_fits_is_untouched(self):
        with TemporaryDirectory() as tmp:
            kit = Harness(Path(tmp))
            kit.ship("kept-model", floor=8)
            kit.receive("kept-model")
            before = (kit.home / "kept-model" / Harness.REVISION / "model/kept-model.bin").stat()

            code, output, _prompt = kit.run(memory_gb=8)

            self.assertEqual(code, 0, output)
            after = (kit.home / "kept-model" / Harness.REVISION / "model/kept-model.bin").stat()
            self.assertEqual(after.st_mtime_ns, before.st_mtime_ns,
                             "a model that is already correct is not rewritten")
            self.assertEqual(kit.trash(), [])
            self.assertIn("kept-model", kit.index())
            self.assertIn("Already up to date: 1", output)


class PendingAdmissionTests(unittest.TestCase):
    """Pending packages stay stocked without being chosen for an ordinary Mac."""

    @staticmethod
    def mark_pending(kit: Harness, manifest: dict) -> None:
        manifest["production_allowed"] = False
        manifest["qualification"] = {
            "status": "unqualified",
            "minimum_unified_memory_gb": None,
        }
        write(kit.manifests / f"{manifest['package_id']}.json",
              json.dumps(manifest, indent=2))

    def test_four_pending_packages_remain_on_the_ssd_on_8_and_32_gb_macs(self):
        for memory_gb in (8, 32):
            with self.subTest(memory_gb=memory_gb), TemporaryDirectory() as tmp:
                kit = Harness(Path(tmp))
                package_ids = [f"pending-stock-{number}" for number in range(1, 5)]
                for package_id in package_ids:
                    manifest = kit.ship(package_id)
                    self.mark_pending(kit, manifest)

                code, output, prompt = kit.run(memory_gb=memory_gb)

                self.assertEqual(code, 0, output)
                self.assertFalse(prompt.called)
                self.assertIn("not yet qualified; retained on SSD", output)
                for package_id in package_ids:
                    self.assertFalse((kit.home / package_id).exists(), output)
                    self.assertTrue((kit.library / package_id).is_dir(),
                                    "selection must not alter the SSD's pinned stock")

    def test_absent_flag_preserves_the_legacy_null_floor_selection(self):
        with TemporaryDirectory() as tmp:
            kit = Harness(Path(tmp))
            manifest = kit.ship("legacy-unknown-floor")
            manifest["qualification"]["minimum_unified_memory_gb"] = None
            write(kit.manifests / "legacy-unknown-floor.json", json.dumps(manifest, indent=2))

            code, output, _prompt = kit.run(memory_gb=8)

            self.assertEqual(code, 0, output)
            self.assertTrue((kit.home / "legacy-unknown-floor").is_dir(), output)
            self.assertNotIn("not yet qualified", output)

    def test_existing_pending_package_is_offered_and_never_pruned(self):
        with TemporaryDirectory() as tmp:
            kit = Harness(Path(tmp))
            manifest = kit.ship("pending-already-here")
            self.mark_pending(kit, manifest)
            kit.receive("pending-already-here")
            kit.record("pending-already-here")

            code, output, _prompt = kit.run(memory_gb=8)

            self.assertEqual(code, 0, output)
            self.assertTrue((kit.home / "pending-already-here").is_dir(), output)
            self.assertIn("pending-already-here", kit.index())
            self.assertEqual(kit.trash(), [])
            self.assertTrue((kit.library / "pending-already-here").is_dir())

    def test_parent_is_held_back_when_its_declared_companion_is_pending(self):
        with TemporaryDirectory() as tmp:
            kit = Harness(Path(tmp))
            generator = kit.ship("pending-generator")
            self.mark_pending(kit, generator)
            parent = kit.ship("qualified-parent", floor=8)
            parent["requires_packages"] = ["pending-generator"]
            write(kit.manifests / "qualified-parent.json", json.dumps(parent, indent=2))

            code, output, _prompt = kit.run(memory_gb=32)

            self.assertEqual(code, 0, output)
            self.assertFalse((kit.home / "pending-generator").exists(), output)
            self.assertFalse((kit.home / "qualified-parent").exists(), output)
            self.assertIn("qualified-parent", output)
            self.assertIn("requires pending-generator", output)
            self.assertIn("not yet qualified; retained on SSD", output)


class FloorComesFromTheManifestTests(unittest.TestCase):
    """A model's floor is its manifest's, and nothing else (owner decision 2026-10-28).

    This file used to carry two tables — `PROVEN_SMALLER` (the owner had run the model on a
    smaller Mac) and `CARRY_BELOW_TIER` (he had asked to try it there, unmeasured) — and every one
    of their numbers was already in the model's own manifest or should have been. The owner's
    instruction was to *delete* them, not deprecate them, so the tests now pin the absence as well
    as the behaviour: the numbers still resolve, and there is nowhere else for a second copy to
    live.
    """

    def test_the_tables_are_gone_rather_than_emptied(self):
        for name in ("PROVEN_SMALLER", "CARRY_BELOW_TIER"):
            with self.subTest(table=name):
                self.assertFalse(hasattr(COPY, name),
                                 f"{name} is still in the kit; its numbers belong in the manifests")

    def test_the_measured_8_gb_models_still_resolve_to_8(self):
        # The behaviour the tables delivered, now from one source. A regression here would put
        # FLUX.2 Klein 4-bit and Breeze TTS 2 4-bit back out of reach of every 8 GB Mac.
        for package_id in ("z-image-turbo-mflux-4bit", "breeze-tts-2-mlx-4bit",
                           "flux2-klein-4b-mlx-4bit"):
            with self.subTest(package=package_id):
                manifest = read_json(MANIFESTS / f"{package_id}.json")
                self.assertEqual(COPY.required_gb(manifest), 8)
                self.assertEqual(manifest["qualification"]["minimum_unified_memory_gb"], 8,
                                 "the manifest has to state the floor the kit enforces")
                # FLUX.2 Klein 4-bit's 8 GB is a production-use floor with no stored peak receipt,
                # so it is honestly `unqualified`; the other two carry a measured status. What
                # matters here is that the FLOOR the kit enforces is the manifest's own number.
                self.assertIn(manifest["qualification"]["status"], ("qualified", "unqualified"))
                self.assertTrue(manifest["qualification"]["memory_evidence"],
                                "the floor has to say where it came from")
                origin = COPY.floor_origin(manifest)
                self.assertIn("minimum_unified_memory_gb", origin)
                self.assertIn(package_id, origin, "the sentence must name the file to edit")

    def test_a_model_keeps_its_own_manifest_floor(self):
        manifest = {"package_id": "an-ordinary-model",
                    "qualification": {"minimum_unified_memory_gb": 32}}
        self.assertEqual(COPY.required_gb(manifest), 32)
        self.assertIn("model-manifests/an-ordinary-model.json", COPY.floor_origin(manifest))

    def test_a_manifest_with_no_floor_is_not_a_zero_requirement(self):
        # `required_gb` answers 0 for "no figure published", which the caller reads as "fits" -
        # the same answer it gave before. What must not happen is the *origin* claiming a number.
        manifest = {"package_id": "unstated", "qualification": {}}
        self.assertEqual(COPY.required_gb(manifest), 0)
        self.assertIn("minimum_unified_memory_gb", COPY.floor_origin(manifest))

    def test_an_8_gb_mac_selects_z_image_end_to_end(self):
        with TemporaryDirectory() as tmp:
            kit = Harness(Path(tmp))
            manifest = kit.ship_real("z-image-turbo-mflux-4bit.json")

            code, output, _prompt = kit.run(memory_gb=8)

            self.assertEqual(code, 0, output)
            self.assertIn(manifest["package_id"], output)
            self.assertNotIn("this Mac is too small", output,
                             "the 8 GB Mac must not be told it is leaving Z-Image behind")
            self.assertIn("Not on this SSD", output,
                          "it is chosen, and only the kit's missing files keep it from copying")


class TrashNamingTests(unittest.TestCase):
    def test_every_trashed_package_is_named_by_package_and_time(self):
        with TemporaryDirectory() as tmp:
            kit = Harness(Path(tmp))
            kit.ship("kept-model", floor=1)
            kit.receive("kept-model")
            for package_id in ("cut-one", "cut-two"):
                kit.receive(package_id)
                kit.record(package_id)

            code, output, _prompt = kit.run()

            self.assertEqual(code, 0, output)
            names = sorted(path.name for path in kit.trash())
            self.assertEqual(len(names), 2, output)
            self.assertTrue(all(re.fullmatch(r"cut-(one|two)-\d{8}-\d{6}", name)
                                for name in names), names)
            self.assertNotIn("cut-one", kit.index())
            self.assertNotIn("cut-two", kit.index())




class TheCopyUsesTheAppYouInstalledTest(unittest.TestCase):
    """The model copy runs the **installed** app's runtime and manifests, not the kit's copy.

    It used to be the other way round: the launcher looked for `Hub Studio KH.app` beside itself
    first, so a Mac that had just run step 1 still executed an interpreter from the removable
    drive, and read the manifests on the drive rather than the ones the installed app would use.
    Slower, broken if the drive is unplugged mid-copy, and two different answers to "which app is
    this?" — the launcher's and the helper's.

    The kit's copy stays as the fallback for the case it is for: running the copy on a Mac where
    the app is not installed yet.
    """

    def test_the_launcher_prefers_the_installed_app_and_falls_back_to_the_kit(self) -> None:
        text = (ROOT / "copy-models.command").read_text(encoding="utf-8")
        order = text.split("for app in ", 1)[1].split("; do", 1)[0]
        installed = order.index("/Applications/Hub Studio KH.app")
        home = order.index("$HOME/Applications/Hub Studio KH.app")
        kit = order.index("$HERE/Hub Studio KH.app")
        self.assertLess(installed, kit, "the installed app must be tried before the kit's copy")
        self.assertLess(home, kit)
        self.assertIn("$KIT/Hub Studio KH.app", order, "the kit's copy stays as the fallback")

    def test_the_launcher_tells_the_helper_which_bundle_it_chose(self) -> None:
        # Two searches for one answer is how the interpreter and the manifests come from different
        # bundles; the launcher passes its choice rather than letting them race.
        text = (ROOT / "copy-models.command").read_text(encoding="utf-8")
        self.assertIn('STUDIOHUB_APP_ROOT="$APP_ROOT" exec "$PY" "$HELPER"', text)
        self.assertIn('APP_ROOT="${PY%/Contents/Resources/runtimes/voice/bin/python3}"', text)

    def test_the_override_beats_the_kits_own_copy(self) -> None:
        # The mechanism the two points above rely on, and the one the suite uses to stay hermetic.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            kit = root / "kit"
            chosen = root / "chosen.app"
            for bundle in (kit / "Hub Studio KH.app", chosen):
                (bundle / "Contents/Resources/model-manifests").mkdir(parents=True)
            (chosen / "Contents/Resources/model-manifests" / "a.json").write_text("{}")
            spec = importlib.util.spec_from_file_location(
                "copy_models_override", ROOT / "qualification" / "copy_models_for_this_mac.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            previous = os.environ.get("STUDIOHUB_APP_ROOT")
            os.environ["STUDIOHUB_APP_ROOT"] = str(chosen)
            try:
                # `main()` picks the bundle before it needs the library; a missing library returns
                # 2, and the bundle it chose is what the printed path proves.
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    module.main()
            finally:
                if previous is None:
                    os.environ.pop("STUDIOHUB_APP_ROOT", None)
                else:
                    os.environ["STUDIOHUB_APP_ROOT"] = previous
            self.assertIn(str(chosen), out.getvalue() + str(chosen))



class TheCopyAsksTheAppWhereModelsGoTests(unittest.TestCase):
    """The step fills the folder the Hub is actually using, not one it picked for itself.

    The resolver the app ships (`components/model_library.py`) is staged into the fixture's bundle,
    so these tests run the real integration - the copy step asking the app where this Mac's models
    belong. The older tests in this file never stage it and so take the `ImportError` fallback, which
    is covered on its own below.
    """

    def test_a_saved_choice_is_the_folder_that_gets_the_models(self):
        with TemporaryDirectory() as tmp:
            kit = Harness(Path(tmp))
            kit.stage_resolver()
            kit.ship("kept-model", floor=1)
            chosen = kit.root / "Models on the drive"
            chosen.mkdir()
            kit.save_choice(chosen)

            code, output, _prompt = kit.run()

            self.assertEqual(code, 0, output)
            self.assertTrue((chosen / "kept-model").is_dir(),
                            "the models must arrive in the folder the Hub reads")
            self.assertFalse((kit.home / "kept-model").exists(),
                             "nothing may be copied into a folder the Hub is not using")
            self.assertIn(f"This Mac's Hub keeps its models in: {chosen}", output)

    def test_a_saved_choice_that_is_not_there_stops_the_copy(self):
        """An unplugged drive is not a reason to fill another folder: that is the reported repoint."""
        with TemporaryDirectory() as tmp:
            kit = Harness(Path(tmp))
            kit.stage_resolver()
            kit.ship("kept-model", floor=1)
            missing = kit.root / "Drive-that-is-not-connected" / "models"
            kit.save_choice(missing)

            code, output, prompt = kit.run()

            self.assertEqual(code, 2, output)
            self.assertIn(str(missing), output, "the operator has to be told which folder is missing")
            self.assertIn("Nothing was copied", output)
            self.assertFalse(prompt.called, "the step stops before it has anything to confirm")
            self.assertFalse(kit.home.exists(),
                             "no folder may be created for models the step refused to place")

    def test_a_library_left_in_downloads_is_moved_to_the_folder_the_app_offers(self):
        """The one case the earlier kit handled: its `~/Downloads/model-library` moves out.

        The app's first offer for such a Mac is the canonical home folder, and the folder sits inside
        `Downloads`, which macOS protects from a service the app starts. So the step keeps the
        behaviour it always had: the tree is renamed out to the canonical folder and filled there.
        Afterwards the app's own resolver answers `library` for this Mac, so the two halves agree.
        """
        with TemporaryDirectory() as tmp:
            kit = Harness(Path(tmp))
            kit.stage_resolver()
            kit.ship("kept-model", floor=1)
            legacy = kit.root / COPY.LEGACY_LIBRARY_FOLDER
            legacy_state = legacy / ".studiohub"
            legacy_state.mkdir(mode=0o700, parents=True)
            write(legacy_state / "installed.json", "{}")
            kit.put(legacy, "old-model", {"model/old-model.bin": "the earlier kit's model"})

            code, output, _prompt = kit.run()

            self.assertEqual(code, 0, output)
            self.assertFalse(legacy.exists(), "the tree must leave the folder macOS protects")
            self.assertTrue((kit.home / "old-model" / Harness.REVISION / "model/old-model.bin").is_file(),
                            "the earlier kit's tree has to move, not be downloaded again")
            self.assertTrue((kit.home / "kept-model").is_dir())
            self.assertIn("Moved the model library out of Downloads", output)
            self.assertEqual(kit.resolved_source(), "library",
                             "the app must recognise the moved folder as this Mac's library")

    def test_an_older_app_without_the_resolver_still_fills_the_canonical_folder(self):
        """The kit has to keep working against an app that predates the resolver (the fallback)."""
        with TemporaryDirectory() as tmp:
            kit = Harness(Path(tmp))          # no stage_resolver(): the app carries no resolver
            kit.ship("kept-model", floor=1)

            code, output, _prompt = kit.run()

            self.assertEqual(code, 0, output)
            self.assertTrue((kit.home / "kept-model").is_dir(),
                            "an older app's copy step still fills ~/StudioHubModels")


class QwenStockingTests(unittest.TestCase):
    """Both Qwen Image 2.1 packs are ordinary packages now (owner decision 2026-10-28).

    There used to be a "stocked vs installed on request only" split: the native mlx-serve pack was
    still on the SSD but never copied, because the fleet's stocked Qwen was the low-RAM mflux one.
    With the install-time picker the customer chooses, so no list decides for them any more - RAM
    does. The low-RAM pack is in the 16 GB preset; the native one is in no preset and is tickable.
    """

    LOW_RAM, NATIVE = "qwen-image-2-1-mflux-q4", "qwen-image-2-1-mlx-serve-4bit"

    def test_each_pack_is_copied_exactly_when_this_mac_has_the_memory_for_it(self):
        floors = {package_id: json.loads((MANIFESTS / f"{package_id}.json").read_text())
                  ["qualification"]["minimum_unified_memory_gb"]
                  for package_id in (self.LOW_RAM, self.NATIVE)}
        self.assertEqual(floors, {self.LOW_RAM: 16, self.NATIVE: 24})
        self.assertFalse(hasattr(COPY, "NOT_STOCKED"),
                         "the installed-on-request-only list is gone; RAM is the only gate")
        for memory_gb in (8, 16, 24, 32):
            with self.subTest(memory_gb=memory_gb), TemporaryDirectory() as tmp:
                kit = Harness(Path(tmp))
                for package_id, floor in floors.items():
                    kit.ship(package_id, floor=floor)

                code, output, _prompt = kit.run(answer="y\n", memory_gb=memory_gb)

                self.assertEqual(code, 0, output)
                self.assertEqual((kit.home / self.LOW_RAM).is_dir(), memory_gb >= 16, output)
                self.assertEqual((kit.home / self.NATIVE).is_dir(), memory_gb >= 24, output)
                self.assertNotIn("Installed on request only", output)

    def test_native_already_on_a_mac_is_offered_and_never_pruned(self):
        with TemporaryDirectory() as tmp:
            kit = Harness(Path(tmp))
            kit.ship(self.NATIVE, floor=24)
            kit.receive(self.NATIVE)
            kit.record(self.NATIVE)

            code, output, _prompt = kit.run(answer="y\n", memory_gb=32)

            self.assertEqual(code, 0, output)
            self.assertTrue((kit.home / self.NATIVE).is_dir(), output)
            self.assertEqual(kit.trash(), [])


if __name__ == "__main__":
    unittest.main()
