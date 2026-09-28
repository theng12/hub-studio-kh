"""The one-click model copy has to be safe to run twice, including after the SSD is refreshed.

The owner's workflow is: plug the SSD in, double-click one file, and expect new models to appear
and changed models to be corrected without re-copying the 29 GB that did not change. These tests
pin the three decisions that makes possible - what counts as "changed", what happens to a model
whose re-copy is interrupted, and what happens to the revision folder it replaces.
"""
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "copy_models_for_this_mac", ROOT / "qualification" / "copy_models_for_this_mac.py")
COPY = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(COPY)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


class SignatureTests(unittest.TestCase):
    def test_identical_trees_are_current(self):
        with TemporaryDirectory() as tmp:
            source, target = Path(tmp) / "s", Path(tmp) / "t"
            for root in (source, target):
                write(root / "abc" / "model.safetensors", "weights")
                write(root / "abc" / "config.json", "{}")
            self.assertFalse(COPY.needs_copy(source, target))

    def test_a_missing_model_is_copied(self):
        with TemporaryDirectory() as tmp:
            source = Path(tmp) / "s"
            write(source / "abc" / "model.safetensors", "weights")
            self.assertTrue(COPY.needs_copy(source, Path(tmp) / "t"))

    def test_a_new_file_in_a_new_revision_is_a_change(self):
        with TemporaryDirectory() as tmp:
            source, target = Path(tmp) / "s", Path(tmp) / "t"
            write(source / "aaa" / "model.safetensors", "weights")
            write(source / "bbb" / "model.safetensors", "weights")
            write(target / "aaa" / "model.safetensors", "weights")
            self.assertTrue(COPY.needs_copy(source, target))

    def test_a_changed_weight_file_is_a_change(self):
        """A corrected or re-exported weight is the update this script exists to notice."""
        with TemporaryDirectory() as tmp:
            source, target = Path(tmp) / "s", Path(tmp) / "t"
            write(source / "abc" / "model.safetensors", "the corrected weights, exported again")
            write(target / "abc" / "model.safetensors", "the old weights")
            self.assertTrue(COPY.needs_copy(source, target))

    def test_a_truncated_copy_on_this_mac_is_a_change(self):
        with TemporaryDirectory() as tmp:
            source, target = Path(tmp) / "s", Path(tmp) / "t"
            write(source / "abc" / "model.safetensors", "complete weights")
            write(target / "abc" / "model.safetensors", "complete")
            self.assertTrue(COPY.needs_copy(source, target))

    def test_a_same_size_edit_is_left_to_the_apps_own_verifier(self):
        """The limit of a size-based comparison, pinned so it is a known cost and not a surprise.

        A file rewritten in place with exactly the same byte count is not noticed here. It does
        not need to be: the app hashes every byte of a package the first time it uses it, and the
        update path that matters - a corrected export, a new revision - always changes a size.
        """
        with TemporaryDirectory() as tmp:
            source, target = Path(tmp) / "s", Path(tmp) / "t"
            write(source / "abc" / "config.json", '{"a": 1}')
            write(target / "abc" / "config.json", '{"a": 2}')
            self.assertFalse(COPY.needs_copy(source, target))

    def test_an_unfinished_download_is_not_part_of_the_model(self):
        """The app's own partial download must not make a complete model look out of date."""
        with TemporaryDirectory() as tmp:
            source, target = Path(tmp) / "s", Path(tmp) / "t"
            write(source / "abc" / "model.safetensors", "weights")
            source.joinpath("abc", "model.safetensors.incomplete").write_bytes(b"")
            write(target / "abc" / "model.safetensors", "weights")
            target.joinpath("abc", "model.safetensors.incomplete").write_bytes(b"")
            self.assertFalse(COPY.needs_copy(source, target))

    def test_a_symlink_shape_survives_the_comparison(self):
        if not hasattr(os, "symlink"):
            self.skipTest("no symlinks here")
        with TemporaryDirectory() as tmp:
            source, target = Path(tmp) / "s", Path(tmp) / "t"
            for root in (source, target):
                write(root / "blobs" / "abc", "weights")
                (root / "snapshots").mkdir(parents=True, exist_ok=True)
                # A Hugging Face cache links relatively, which is exactly why the shape can be
                # compared between two different folders.
                os.symlink("../blobs/abc", root / "snapshots" / "model")
            self.assertFalse(COPY.needs_copy(source, target))


class CopyTests(unittest.TestCase):
    def test_replace_removes_files_the_new_model_no_longer_has(self):
        """Updating is a replacement, not a merge: a renamed file must not linger twice."""
        with TemporaryDirectory() as tmp:
            source, target = Path(tmp) / "s", Path(tmp) / "t"
            write(source / "abc" / "new.safetensors", "weights")
            write(target / "abc" / "old.safetensors", "weights")
            COPY.copy_folder(source, target)
            self.assertFalse((target / "abc" / "old.safetensors").exists())
            self.assertEqual((target / "abc" / "new.safetensors").read_text(), "weights")

    def test_an_interrupted_copy_leaves_the_previous_folder_in_place(self):
        """The failure that matters is a half-written model. The old one must survive it."""
        with TemporaryDirectory() as tmp:
            source, target = Path(tmp) / "s", Path(tmp) / "t"
            write(source / "abc" / "model.safetensors", "new weights")
            write(target / "abc" / "model.safetensors", "old weights")
            real_run = COPY.subprocess.run

            def failing(command, *args, **kwargs):
                if command[:2] == ["/bin/cp", "-Rp"]:
                    # Simulate the copy dying partway: the staging folder exists but is empty,
                    # so it is not the model, and the previous folder must still be intact.
                    Path(command[3]).mkdir(parents=True, exist_ok=True)
                    return type("Result", (), {"returncode": 1})()
                return real_run(command, *args, **kwargs)

            with mock.patch.object(COPY.subprocess, "run", failing):
                with self.assertRaises(OSError):
                    COPY.copy_folder(source, target)

            self.assertEqual((target / "abc" / "model.safetensors").read_text(), "old weights")
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ["s", "t"])

    def test_no_staging_or_backup_folders_are_left_behind(self):
        with TemporaryDirectory() as tmp:
            source, target = Path(tmp) / "s", Path(tmp) / "t"
            write(source / "abc" / "model.safetensors", "weights")
            COPY.copy_folder(source, target)
            COPY.copy_folder(source, target)
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ["s", "t"])

class PruneTests(unittest.TestCase):
    REVISION = "a" * 40
    OLDER = "b" * 40

    def test_the_previous_revision_is_removed_and_reported(self):
        """Keeping the old revision next to the new one would double what every Mac carries."""
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "package"
            write(target / self.REVISION / "model.safetensors", "new")
            write(target / self.OLDER / "model.safetensors", "0123456789")
            freed = COPY.prune_old_revisions(target, self.REVISION)
            self.assertEqual(freed, 10)
            self.assertEqual(sorted(p.name for p in target.iterdir()), [self.REVISION])

    def test_anything_that_is_not_a_revision_folder_is_left_alone(self):
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "package"
            write(target / self.REVISION / "model.safetensors", "new")
            write(target / "README.md", "keep me")
            write(target / "not-a-revision" / "file", "keep me too")
            COPY.prune_old_revisions(target, self.REVISION)
            self.assertTrue((target / "README.md").exists())
            self.assertTrue((target / "not-a-revision" / "file").exists())

    def test_there_is_nothing_to_prune_on_a_first_copy(self):
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "package"
            write(target / self.REVISION / "model.safetensors", "new")
            self.assertEqual(COPY.prune_old_revisions(target, self.REVISION), 0)


class EndToEndTests(unittest.TestCase):
    """The whole script, over a small synthetic library shaped like the real kit.

    This is the test that would have caught the failure this release is about: the app decides a
    model is installed by comparing its manifest against `.studiohub/installed.json`, so a script
    that copies new weight files but not the record leaves an updated model reading as missing.
    """

    REVISION = "c" * 40
    # The unversioned name every kit ships. It used to be `Studio Hub 0.4.0.app`, and once the
    # launcher stopped looking for a pinned name the fixture's app was no longer found — at which
    # point the helper fell through to the **owner's real `/Applications/Studio Hub.app`** and read
    # its (older) `model_packages`. A test that reaches out of its sandbox is a worse bug than the
    # one it was hiding; the fixture now uses the name the drives actually carry.
    APP = Path("Studio Hub.app")

    def _manifest(self, package_id: str, revision: str, files: dict) -> dict:
        entries = []
        for name, text in files.items():
            entries.append({"path": name, "size": len(text.encode()),
                            "sha256": hashlib.sha256(text.encode()).hexdigest(),
                            "urls": [f"https://example.invalid/{package_id}/{name}"]})
        return {"schema_version": 1, "package_id": package_id, "model_id": package_id,
                "revision": revision, "engine": "voice-mlx", "minimum_macos": "14.0",
                "requires_packages": [], "files": entries,
                "qualification": {"minimum_unified_memory_gb": 1}}

    def _build(self, root: Path, packages: dict):
        """Lay out a kit, a home folder and the app's own verifier, as on the SSD."""
        kit = root / "kit"
        library = kit / "model-library"
        manifests = kit / self.APP / "Contents/Resources/model-manifests"
        components = kit / self.APP / "Contents/Resources/components"
        manifests.mkdir(parents=True)
        components.mkdir(parents=True)
        (kit / self.APP / "Contents/Resources/runtimes/voice/bin").mkdir(parents=True)
        (kit / self.APP / "Contents/Resources/runtimes/voice/bin/python3").write_text("")
        (components / "model_packages.py").write_text(
            (ROOT / "components" / "model_packages.py").read_text())
        for package_id, (revision, files) in packages.items():
            (manifests / f"{package_id}.json").write_text(
                json.dumps(self._manifest(package_id, revision, files), indent=2))
            for name, text in files.items():
                write(library / package_id / revision / name, text)
        return kit, library, manifests

    def _run(self, root: Path, kit: Path, answer: str = "\n"):
        import io
        import contextlib
        # The script finds the kit from its own location, so run the copy that sits in the kit.
        helper = kit / "Technical" / "copy_models_for_this_mac.py"
        helper.parent.mkdir(parents=True, exist_ok=True)
        helper.write_text((ROOT / "qualification" / "copy_models_for_this_mac.py").read_text())
        spec = importlib.util.spec_from_file_location("copy_models_in_kit", helper)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        argv = sys.argv
        home = os.environ.get("HOME")
        app_root = os.environ.get("STUDIOHUB_APP_ROOT")
        os.environ["HOME"] = str(root)          # the script writes to ~/StudioHubModels
        # **Point the helper at this fixture's app bundle.** It prefers an *installed*
        # `/Applications/Studio Hub.app` now, and without this it read the owner's real thirty
        # manifests against this fixture's library — every model came back "not on this SSD". The
        # sibling suite (`test_copy_models_for_this_mac.py`) had the same hole and the same fix.
        os.environ["STUDIOHUB_APP_ROOT"] = str(kit / self.APP)
        sys.argv = ["copy_models_for_this_mac.py"]
        out = io.StringIO()
        try:
            with mock.patch("builtins.input", return_value=answer):
                with contextlib.redirect_stdout(out):
                    code = module.main()
        finally:
            sys.argv = argv
            os.environ["HOME"] = home
            if app_root is None:
                os.environ.pop("STUDIOHUB_APP_ROOT", None)
            else:
                os.environ["STUDIOHUB_APP_ROOT"] = app_root
        return code, out.getvalue()

    def _receipt(self, destination: Path, package_id: str):
        return json.loads((destination / ".studiohub" / "installed.json").read_text())[package_id]

    def test_new_models_are_added_and_changed_models_are_corrected(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            kit, library, _manifests = self._build(root, {
                "old-model": (self.REVISION, {"weights.bin": "the old weights"}),
                "current-model": ("d" * 40, {"weights.bin": "already correct"}),
                "brand-new-model": ("e" * 40, {"weights.bin": "never copied before"}),
            })
            destination = root / "StudioHubModels"
            # This Mac already has two of the three: one identical, one stale.
            write(destination / "old-model" / self.REVISION / "weights.bin", "the old weights")
            write(destination / "current-model" / ("d" * 40) / "weights.bin", "already correct")
            # The stale one is stale because the SSD has newer contents for the same revision.
            write(library / "old-model" / self.REVISION / "weights.bin", "the corrected weights!")

            code, output = self._run(root, kit)
            self.assertEqual(code, 0, output)
            self.assertIn("Copied 1 new, updated 1, 1 already up to date", output)
            self.assertIn("brand-new-model", output)
            self.assertEqual(
                (destination / "brand-new-model" / ("e" * 40) / "weights.bin").read_text(),
                "never copied before")
            self.assertEqual(
                (destination / "old-model" / self.REVISION / "weights.bin").read_text(),
                "the corrected weights!")

    def test_updated_models_are_recorded_so_the_app_sees_them_as_installed(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            kit, library, manifests = self._build(root, {
                "changed-model": (self.REVISION, {"weights.bin": "the corrected weights"}),
            })
            destination = root / "StudioHubModels"
            write(destination / "changed-model" / self.REVISION / "weights.bin", "the old weights")

            code, output = self._run(root, kit)
            self.assertEqual(code, 0, output)

            # The app's own preflight must call the updated copy ready, using the receipt this
            # script wrote and the manifest the app ships.
            import sys as _sys
            _sys.path.insert(0, str(ROOT / "components"))
            import model_packages
            manifest = json.loads((manifests / "changed-model.json").read_text())
            state = model_packages.preflight(manifest, destination)
            self.assertEqual(state["state"], "files_verified", state)
            self.assertEqual(self._receipt(destination, "changed-model")["revision"], self.REVISION)

    def test_running_it_twice_copies_nothing_the_second_time(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            kit, _library, _manifests = self._build(
                root, {"a-model": (self.REVISION, {"weights.bin": "weights"})})
            first, output = self._run(root, kit)
            self.assertEqual(first, 0, output)
            self.assertIn("Copied 1 new", output)

            second, output = self._run(root, kit)
            self.assertEqual(second, 0, output)
            self.assertIn("Nothing to copy", output)
            self.assertIn("Already up to date: 1", output)

    def test_a_copy_that_stopped_early_is_repaired_by_running_it_again(self):
        """The repair path the owner asked for: a model folder that is *missing files*, not changed.

        This is what an interrupted copy leaves behind - the folder is there, some of its files are
        there, and the app reads the model as "not installed" with nothing on screen saying why.
        Running the copy step again has to notice and fill it in, without re-copying the models that
        are already correct.
        """
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            kit, _library, _manifests = self._build(root, {
                "half-model": (self.REVISION, {"weights.bin": "weights",
                                               "config.json": "{}",
                                               "tokenizer/vocab.txt": "a b c"}),
                "whole-model": ("c" * 40, {"weights.bin": "weights"}),
            })
            destination = root / "StudioHubModels"
            first, output = self._run(root, kit)
            self.assertEqual(first, 0, output)
            self.assertIn("Copied 2 new", output)

            # An interrupted copy: a whole file gone, and another one truncated.
            (destination / "half-model" / self.REVISION / "config.json").unlink()
            (destination / "half-model" / self.REVISION / "weights.bin").write_text("wei")
            before = (destination / "whole-model" / ("c" * 40) / "weights.bin").stat().st_mtime_ns

            second, output = self._run(root, kit)
            self.assertEqual(second, 0, output)
            self.assertEqual(
                (destination / "half-model" / self.REVISION / "config.json").read_text(), "{}")
            self.assertEqual(
                (destination / "half-model" / self.REVISION / "weights.bin").read_text(), "weights")
            self.assertEqual(
                (destination / "half-model" / self.REVISION / "tokenizer/vocab.txt").read_text(),
                "a b c")
            # The model that was already right was not touched: re-running stays cheap.
            self.assertEqual(
                (destination / "whole-model" / ("c" * 40) / "weights.bin").stat().st_mtime_ns, before)
            self.assertEqual(self._receipt(destination, "half-model")["revision"], self.REVISION)

    def test_a_model_too_big_for_this_mac_is_never_copied(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            kit, library, manifests = self._build(root, {
                "small-model": (self.REVISION, {"weights.bin": "weights"}),
                "one-terabyte-model": ("f" * 40, {"weights.bin": "weights"}),
            })
            big = json.loads((manifests / "one-terabyte-model.json").read_text())
            big["qualification"]["minimum_unified_memory_gb"] = 1024
            (manifests / "one-terabyte-model.json").write_text(json.dumps(big, indent=2))

            code, output = self._run(root, kit)
            self.assertEqual(code, 0, output)
            self.assertIn("this Mac is too small", output)
            self.assertFalse((root / "StudioHubModels/one-terabyte-model").exists())


    def test_a_missing_record_is_repaired_without_recopying_the_model(self):
        """Running the copy step is the repair path for "not installed" - and must stay cheap.

        The model's files are already right, so the only thing missing is the app's record. That
        must be written from what is on this Mac, not by copying 28 GB again.
        """
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            kit, _library, _manifests = self._build(
                root, {"a-model": (self.REVISION, {"weights.bin": "weights"})})
            destination = root / "StudioHubModels"
            code, output = self._run(root, kit)
            self.assertEqual(code, 0, output)
            self.assertIn("ready", output)

            # Lose the record, as a botched manual copy would.
            state = destination / ".studiohub/installed.json"
            state.unlink()
            before = (destination / "a-model" / self.REVISION / "weights.bin").stat().st_mtime_ns

            code, output = self._run(root, kit)
            self.assertEqual(code, 0, output)
            self.assertIn("Nothing to copy", output)
            self.assertIn("record(s) repaired", output)
            self.assertEqual(self._receipt(destination, "a-model")["revision"], self.REVISION)
            self.assertEqual(
                (destination / "a-model" / self.REVISION / "weights.bin").stat().st_mtime_ns,
                before)

    def test_a_library_the_earlier_kit_left_in_downloads_is_moved_not_copied(self):
        """The old kit filled ~/Downloads/model-library, which macOS protects.

        A library inside that folder stops the app's services from starting at all, so the copy
        step has to get it out of the way. Moving it is a rename on the same disk: the 34 GB must
        not be copied a second time, and the old folder must not be left behind to be chosen by
        mistake.
        """
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            kit, _library, _manifests = self._build(
                root, {"a-model": (self.REVISION, {"weights.bin": "weights"})})
            legacy = root / "Downloads" / "model-library"
            write(legacy / "a-model" / self.REVISION / "weights.bin", "weights")
            before = (legacy / "a-model" / self.REVISION / "weights.bin").stat().st_ino

            code, output = self._run(root, kit)
            self.assertEqual(code, 0, output)
            self.assertIn("Moved the model library out of Downloads", output)
            moved = root / "StudioHubModels" / "a-model" / self.REVISION / "weights.bin"
            self.assertTrue(moved.is_file())
            self.assertEqual(moved.stat().st_ino, before, "the file must be moved, not copied")
            self.assertFalse(legacy.exists(), "the protected folder must not be left behind")

    def test_a_second_library_in_downloads_is_reported_and_left_alone(self):
        """A Mac that already has both keeps using the home-folder library.

        Nothing is merged or deleted behind the operator's back; the leftover copy is named so it
        can be removed once they are satisfied, which is the only reason it is mentioned.
        """
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            kit, _library, _manifests = self._build(
                root, {"a-model": (self.REVISION, {"weights.bin": "weights"})})
            write(root / "StudioHubModels" / "a-model" / self.REVISION / "weights.bin", "weights")
            write(root / "Downloads/model-library" / "a-model" / self.REVISION / "weights.bin",
                  "weights")

            code, output = self._run(root, kit)
            self.assertEqual(code, 0, output)
            self.assertIn("A second library is still at", output)
            self.assertIn("Downloads/model-library", output)
            self.assertTrue((root / "Downloads/model-library" / "a-model" / self.REVISION).is_dir(),
                            "the leftover library is not deleted for the operator")


class LauncherTests(unittest.TestCase):
    """The launcher is the only part the owner touches, and it failed on the real drive.

    It was written assuming it sat inside the kit's Technical folder, so the copy placed in the
    kit *root* looked one directory too high for both the app and its helper, and printed
    "I cannot find Studio Hub" on the SSD the owner was holding. These tests hold the launcher
    to working from either place, and to naming the files the owner actually sees.
    """

    LAUNCHERS = ("copy-models.command", "install_app.command")

    def test_each_launcher_is_a_valid_shell_script(self):
        for name in self.LAUNCHERS:
            result = subprocess.run(["/bin/sh", "-n", str(ROOT / name)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, f"{name}: {result.stderr}")

    def test_the_copy_launcher_finds_its_helper_in_either_location(self):
        text = (ROOT / "copy-models.command").read_text()
        self.assertIn("$HERE/Technical/copy_models_for_this_mac.py", text)
        self.assertIn("$HERE/copy_models_for_this_mac.py", text)

    def test_the_copy_launcher_finds_the_app_in_the_root_or_next_to_it(self):
        text = (ROOT / "copy-models.command").read_text()
        for candidate in ("$HERE/Studio Hub.app", "$KIT/Studio Hub.app",
                          "/Applications/Studio Hub.app"):
            self.assertIn(candidate, text)
        # The bundle's name carries no version, on purpose (`packaging/stage_voice.py` dropped
        # it so a new release replaces the old install instead of sitting beside it). A launcher
        # that looks for a version-pinned name works exactly until the next release.
        self.assertNotRegex(
            text, r"Studio Hub \d+\.\d+",
            "copy-models.command looks for a version-pinned app name; the bundle has none")

    def test_the_install_launcher_finds_the_app_from_the_root_too(self):
        text = (ROOT / "install_app.command").read_text()
        self.assertIn("$HERE/Studio Hub.app", text)
        self.assertIn("$KIT/Studio Hub.app", text)
        self.assertNotRegex(
            text, r"Studio Hub \d+\.\d+",
            "install_app.command looks for a version-pinned app name; the bundle has none")

    def test_messages_name_the_files_as_they_are_named_on_the_drive(self):
        """The operator sees files with a number and spaces, so the help must say those names."""
        for name in self.LAUNCHERS:
            text = (ROOT / name).read_text()
            self.assertNotIn("install_app.command", text.replace("$HERE", ""))
            self.assertNotIn("'copy-models.command'", text)

    def test_the_launchers_ship_in_the_kit_next_to_the_app(self):
        kit = Path("/Volumes/KH-STUDIO-ATLAS/Studio Hub App")
        if not kit.is_dir():
            self.skipTest("the SSD is not mounted on this machine")
        for relative in ("1 - Install the app.command", "2 - Copy the models.command",
                         "Technical/copy-models.command", "Technical/install_app.command",
                         "Technical/copy_models_for_this_mac.py"):
            self.assertTrue((kit / relative).is_file(), f"missing on the drive: {relative}")


class KitManifestTests(unittest.TestCase):
    """The kit manifest is the record of what was handed to a machine.

    It described distribution as "copy the app and the model library folder to the target Mac",
    which stopped being true the moment the two launchers existed; and it recorded only the app's
    build commit, which does not change when only the distribution files do.
    """

    def _kit(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "release_kit", ROOT / "packaging" / "release_kit.py")
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module

    def test_operator_steps_describe_the_launchers_not_a_manual_copy(self):
        text = (ROOT / "packaging" / "release_kit.py").read_text()
        self.assertIn("1 - Install the app.command", text)
        self.assertIn("2 - Copy the models.command", text)
        self.assertNotIn('"Copy the app and the model library folder to the target Mac."', text)

    def test_the_kit_records_the_commit_that_built_it(self):
        kit = self._kit()
        commit = kit.git_commit()
        if commit is None:
            self.skipTest("not a git checkout")
        self.assertRegex(commit, r"^[0-9a-f]{40}$")

    def test_a_missing_git_does_not_break_the_manifest(self):
        """The manifest describes an artifact, so it must build without git metadata."""
        kit = self._kit()
        with mock.patch.object(kit.subprocess, "run", side_effect=FileNotFoundError()):
            self.assertIsNone(kit.git_commit())


class VoiceCopyTests(unittest.TestCase):
    """Voices travel with the models, and a voice already on the Mac is never overwritten.

    The operator's own recordings live only on that machine. A model-copy step that rewrote a clip
    or a transcript because the kit's copy differed would destroy work nothing else can restore, so
    the rule is add-and-leave-alone, and the tests hold it there.
    """

    REVISION = "9" * 40

    def _voice(self, root: Path, voice_id: str, name: str, transcript: str = "the words") -> Path:
        folder = root / voice_id
        write(folder / "metadata.json", json.dumps({"id": voice_id, "name": name}))
        write(folder / "reference.wav", f"audio for {name}")
        if transcript:
            write(folder / "transcript.txt", transcript)
        return folder

    def test_a_missing_voice_is_added(self):
        with TemporaryDirectory() as tmp:
            source, target = Path(tmp) / "kit/voices", Path(tmp) / "library"
            self._voice(source, "0616aec33b41", "Christian")
            added, present, names = COPY.copy_voices(source, target)
            self.assertEqual((added, present), (1, 0))
            self.assertEqual(names, ["Christian"])
            self.assertTrue((target / "0616aec33b41/transcript.txt").is_file())

    def test_a_voice_already_on_this_mac_is_left_exactly_as_it_is(self):
        """A locally corrected transcript must survive the kit being copied again."""
        with TemporaryDirectory() as tmp:
            source, target = Path(tmp) / "kit/voices", Path(tmp) / "library"
            self._voice(source, "0616aec33b41", "Christian", transcript="what the kit says")
            self._voice(target, "0616aec33b41", "Christian", transcript="what I corrected here")
            added, present, names = COPY.copy_voices(source, target)
            self.assertEqual((added, present, names), (0, 1, []))
            self.assertEqual((target / "0616aec33b41/transcript.txt").read_text(),
                             "what I corrected here")

    def test_running_it_twice_adds_nothing_the_second_time(self):
        with TemporaryDirectory() as tmp:
            source, target = Path(tmp) / "kit/voices", Path(tmp) / "library"
            self._voice(source, "0616aec33b41", "Christian")
            self._voice(source, "074743daa991", "Vincent")
            self.assertEqual(COPY.copy_voices(source, target)[0], 2)
            added, present, _ = COPY.copy_voices(source, target)
            self.assertEqual((added, present), (0, 2))

    def test_a_folder_without_metadata_is_not_a_voice(self):
        with TemporaryDirectory() as tmp:
            source, target = Path(tmp) / "kit/voices", Path(tmp) / "library"
            self._voice(source, "0616aec33b41", "Christian")
            write(source / "README.md", "notes")
            write(source / "half-copied/reference.wav", "audio with no metadata")
            added, _present, _names = COPY.copy_voices(source, target)
            self.assertEqual(added, 1)
            self.assertFalse((target / "half-copied").exists())

    def test_the_kit_can_carry_voices_the_app_can_read(self):
        """The real kit folder, loaded the way the Voice worker loads it."""
        kit_voices = ROOT / "voices"
        if not kit_voices.is_dir():
            self.skipTest("this tree carries no voices")
        found = COPY.kit_voices(kit_voices)
        self.assertTrue(found, "the kit's voices folder holds no readable voices")
        for folder, name in found:
            self.assertTrue(name.strip(), f"{folder.name} has no usable name")
            self.assertTrue((folder / "metadata.json").is_file())
            reference = [p for p in folder.iterdir() if p.stem == "reference"]
            self.assertTrue(reference, f"{name} has no reference clip")
            self.assertTrue((folder / "transcript.txt").is_file(),
                            f"{name} has no transcript, so it cannot clone")

    def test_timings_are_added_to_a_voice_that_is_already_here(self):
        """A measured file the app needs must not be skipped along with the operator's own clip.

        The rule "never overwrite an existing voice" protects the recording and its transcript. The
        timings are neither: they are measured, the app only uses them when they match the transcript
        exactly, and without them a clip longer than the model's window cannot be prepared at all.
        Measured on a real node on 2026-09-18: 0 of 10 voices had timings while the kit carried 7,
        because the timings were added to the kit after the voices were already on the Mac.
        """
        with TemporaryDirectory() as tmp:
            source, target = Path(tmp) / "kit/voices", Path(tmp) / "library"
            self._voice(source, "0616aec33b41", "Christian", "the words")
            self._voice(target, "0616aec33b41", "Christian", "the words I corrected")
            (source / "0616aec33b41" / COPY.TIMED_TRANSCRIPT).write_text(
                json.dumps({"transcript": "the words", "segments": [
                    {"start_seconds": 0.0, "end_seconds": 1.0, "text": "the words"}]}))

            added, present, names = COPY.copy_voices(source, target)

            self.assertEqual((added, present), (0, 1), "the voice itself is not re-copied")
            self.assertTrue((target / "0616aec33b41" / COPY.TIMED_TRANSCRIPT).is_file(),
                            "the timings must reach a voice that was already present")
            self.assertEqual((target / "0616aec33b41/transcript.txt").read_text(),
                             "the words I corrected", "the operator's transcript is untouched")
            self.assertIn("timings", " ".join(names), "and it is reported")

    def test_timings_are_not_overwritten_if_this_mac_already_has_them(self):
        with TemporaryDirectory() as tmp:
            source, target = Path(tmp) / "kit/voices", Path(tmp) / "library"
            self._voice(source, "0616aec33b41", "Christian")
            self._voice(target, "0616aec33b41", "Christian")
            (source / "0616aec33b41" / COPY.TIMED_TRANSCRIPT).write_text('{"segments": []}')
            (target / "0616aec33b41" / COPY.TIMED_TRANSCRIPT).write_text('{"segments": [{"mine": 1}]}')

            COPY.copy_voices(source, target)

            self.assertIn("mine", (target / "0616aec33b41" / COPY.TIMED_TRANSCRIPT).read_text(),
                          "timings already measured here are left alone")


if __name__ == "__main__":
    unittest.main()
