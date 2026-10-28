"""Real model removal: a move into the library's `_trash`, refused while anything is reading it.

**Owner decision 2026-10-28.** The Models page's Remove answered "Hub model removal is unavailable
until it can protect models with active readers" - so the only way to get 9 GB back was Finder,
which deletes without a receipt and without knowing whether a job is mid-generation. What is
pinned here:

1. **It moves, it never deletes.** The package folder lands in `<library>/_trash/<pkg>-<stamp>/`
   with a receipt that says what it was, when and why. Emptying that folder is the owner's call.
2. **It refuses while anything is using it**: a running job on the model, a paused model, or
   another installed package that requires it - and each refusal names which.
3. **It refills nothing** when the Hub does not own the library.
4. **The receipt goes with the folder**, so the app reads "not installed" instead of "installed
   under a different manifest - press Verify".
5. **Restore works within 30 days**, and after 30 days it is handed to macOS Trash - the same
   mechanism the Hub's own Trash Folder uses, never a second deleter.
6. **Every removal is separate**: two inside one second do not merge into one folder.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from components.hub.backend import model_removal

MANIFEST = {"package_id": "a-model", "model_id": "org/a-model-4bit", "revision": "a" * 40,
            "engine": "image-mflux", "files": [{"path": "model/w.safetensors", "size": 10}]}


def library_with(package_id: str = "a-model", *, receipt: bool = True, size: int = 10) -> Path:
    root = Path(tempfile.mkdtemp(prefix="library-"))
    folder = root / package_id / MANIFEST["revision"]
    folder.mkdir(parents=True)
    (folder / "w.safetensors").write_bytes(b"x" * size)
    if receipt:
        state = root / ".studiohub"
        state.mkdir(mode=0o700)
        os.chmod(state, 0o700)
        (state / "installed.json").write_text(json.dumps(
            {package_id: {"revision": MANIFEST["revision"],
                          "folder": f"{package_id}/{MANIFEST['revision']}", "files": {}}}),
            encoding="utf-8")
    return root


def remove(root: Path, **overrides):
    values = dict(library=root, manifest=MANIFEST, catalog={"a-model": MANIFEST},
                  records={"a-model": {"folder": f"a-model/{MANIFEST['revision']}"}},
                  may_move=True, running_jobs=[], paused=None)
    values.update(overrides)
    return model_removal.remove(**values)


class MoveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.library = library_with()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.library, True))

    def test_it_moves_the_folder_into_trash_and_writes_a_receipt(self) -> None:
        answer = remove(self.library)
        self.assertTrue(answer["ok"])
        moved = Path(answer["moved_to"])
        self.assertTrue(moved.is_dir())
        self.assertEqual(moved.parent, self.library / "_trash")
        self.assertFalse((self.library / "a-model").exists(), "the package is no longer in the library")
        self.assertTrue((moved / MANIFEST["revision"] / "w.safetensors").is_file(),
                        "every byte came along")
        record = json.loads((moved / ".studiohub-removed.json").read_text())
        self.assertEqual(record["package_id"], "a-model")
        self.assertEqual(record["model_id"], "org/a-model-4bit")
        self.assertEqual(record["bytes"], 10)
        self.assertIn("restore", record)
        self.assertIn("removed_at_iso", record)

    def test_it_is_a_move_and_not_a_copy(self) -> None:
        # Nothing is left behind, and the same inode arrived: a copy plus a delete would leave the
        # bytes on disk if the delete failed, and the point of this control is that it cannot.
        original = (self.library / "a-model" / MANIFEST["revision"] / "w.safetensors").stat()
        answer = remove(self.library)
        arrived = (Path(answer["moved_to"]) / MANIFEST["revision"] / "w.safetensors").stat()
        self.assertEqual((original.st_ino, original.st_dev), (arrived.st_ino, arrived.st_dev))

    def test_the_receipt_is_dropped_so_the_app_reads_not_installed(self) -> None:
        remove(self.library)
        index = json.loads((self.library / ".studiohub" / "installed.json").read_text())
        self.assertNotIn("a-model", index)

    def test_two_removals_inside_one_second_do_not_merge(self) -> None:
        first = remove(self.library, now=1_700_000_000.0)
        # Put a second copy of the same package back and remove it at the same instant.
        folder = self.library / "a-model" / MANIFEST["revision"]
        folder.mkdir(parents=True)
        (folder / "w.safetensors").write_bytes(b"y" * 4)
        second = remove(self.library, now=1_700_000_000.0)
        self.assertNotEqual(first["moved_to"], second["moved_to"])
        self.assertTrue(Path(first["moved_to"]).is_dir())
        self.assertTrue(Path(second["moved_to"]).is_dir())

    def test_a_package_that_is_not_here_is_refused_not_invented(self) -> None:
        with self.assertRaises(model_removal.RemoveRefused) as caught:
            remove(self.library, manifest={**MANIFEST, "package_id": "never-installed"})
        self.assertEqual(caught.exception.status, 404)
        self.assertIn("nothing to remove", caught.exception.body["detail"])

    def test_a_manifest_that_names_a_path_is_never_joined(self) -> None:
        # A package id is a folder name. One that walks out of the library must not resolve, however
        # it got into a manifest.
        for bad in ("..", "../other", "/etc", "a/b"):
            with self.subTest(package_id=bad):
                self.assertIsNone(model_removal.package_folder(
                    self.library, {"package_id": bad}, None))


class ReservedFolderTests(unittest.TestCase):
    """A damaged receipt must not turn "remove this package" into "remove the whole trash".

    `package_folder` reduces a receipt's `<package>/<revision>` to its top segment. A receipt whose
    folder is `_trash/x` - damaged, hand-edited, or written by a build that meant something else -
    reduced to `_trash`, and the move would have taken every other removed package with it.
    """

    def setUp(self) -> None:
        self.library = library_with()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.library, True))
        (self.library / "_trash" / "someone-elses-20260101-000000").mkdir(parents=True)

    def test_a_receipt_naming_the_trash_does_not_resolve_to_it(self) -> None:
        for relative in ("_trash/x", "_trash", ".studiohub", ".studiohub/installed.json"):
            with self.subTest(relative=relative):
                self.assertIsNone(model_removal.package_folder(
                    self.library, {"package_id": "never-a-package"}, {"folder": relative}))

    def test_a_reserved_package_id_does_not_resolve(self) -> None:
        for package_id in ("_trash", ".studiohub"):
            with self.subTest(package_id=package_id):
                self.assertIsNone(model_removal.package_folder(
                    self.library, {"package_id": package_id}, None))

    def test_removing_a_damaged_receipt_leaves_the_rest_of_the_trash_alone(self) -> None:
        with self.assertRaises(model_removal.RemoveRefused):
            remove(self.library, manifest={"package_id": "never-a-package", "model_id": "x",
                                           "revision": "a" * 40, "engine": "image-mflux"},
                   records={"never-a-package": {"folder": "_trash/someone-elses"}})
        self.assertTrue((self.library / "_trash" / "someone-elses-20260101-000000").is_dir())


class RefusalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.library = library_with()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.library, True))

    def _assert_refused(self, **overrides) -> str:
        with self.assertRaises(model_removal.RemoveRefused) as caught:
            remove(self.library, **overrides)
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(caught.exception.code, "IN_USE")
        self.assertTrue((self.library / "a-model").is_dir(), "a refused removal moved nothing")
        return caught.exception.body["detail"]

    def test_a_running_job_on_the_model_refuses_it(self) -> None:
        detail = self._assert_refused(running_jobs=[("image@m1", "org/a-model-4bit")])
        self.assertIn("a image@m1 job is running", detail.replace("a image", "a image"))

    def test_a_running_job_on_another_model_does_not(self) -> None:
        # The refusal is about this package's bytes, not about the Mac being busy.
        self.assertTrue(remove(self.library, running_jobs=[("image@m1", "org/some-other-model")])["ok"])

    def test_a_paused_model_still_holding_weights_refuses_it(self) -> None:
        detail = self._assert_refused(paused={"studio": "image", "failures": 3})
        self.assertIn("image", detail)
        self.assertIn("paused", detail)

    def test_an_installed_package_that_requires_it_refuses_it(self) -> None:
        # A companion (a tokenizer, a VAE) is not the operator's to remove out from under its parent.
        companion = {"package_id": "a-model", "model_id": "org/a-model-4bit", "revision": "a" * 40,
                     "engine": "voice-mlx", "files": []}
        parent = {"package_id": "parent", "model_id": "org/parent", "revision": "b" * 40,
                  "engine": "voice-mlx", "files": [], "requires_packages": ["a-model"]}
        detail = self._assert_refused(catalog={"a-model": companion, "parent": parent},
                                      records={"a-model": {}, "parent": {}})
        self.assertIn("parent is installed and requires it", detail)

    def test_a_requiring_package_that_is_not_installed_does_not_refuse(self) -> None:
        parent = {"package_id": "parent", "model_id": "org/parent", "revision": "b" * 40,
                  "engine": "voice-mlx", "files": [], "requires_packages": ["a-model"]}
        answer = remove(self.library, catalog={"a-model": MANIFEST, "parent": parent},
                        records={"a-model": {}})
        self.assertTrue(answer["ok"])

    def test_a_hub_that_does_not_own_the_library_refuses(self) -> None:
        with self.assertRaises(model_removal.RemoveRefused) as caught:
            remove(self.library, may_move=False, refusal="This Hub does not own it")
        self.assertEqual(caught.exception.status, 403)
        self.assertIn("does not own it", caught.exception.body["detail"])
        self.assertTrue((self.library / "a-model").is_dir())


class RestoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.library = library_with()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.library, True))

    def test_a_removed_package_can_be_put_back(self) -> None:
        answer = remove(self.library)
        moved_name = Path(answer["moved_to"]).name
        restored = model_removal.restore(self.library, self.library / "_trash" / moved_name)
        self.assertEqual(restored["package_id"], "a-model")
        self.assertTrue((self.library / "a-model" / MANIFEST["revision"] / "w.safetensors").is_file())
        self.assertFalse((self.library / "_trash" / moved_name).exists())
        # The "this was removed" receipt does not travel back into the library with it.
        self.assertFalse((self.library / "a-model" / ".studiohub-removed.json").exists())

    def test_restore_refuses_to_overwrite_an_installed_copy(self) -> None:
        answer = remove(self.library)
        folder = self.library / "a-model" / MANIFEST["revision"]
        folder.mkdir(parents=True)
        (folder / "w.safetensors").write_bytes(b"new")
        with self.assertRaises(model_removal.RemoveRefused) as caught:
            model_removal.restore(self.library, Path(answer["moved_to"]))
        self.assertEqual(caught.exception.code, "ALREADY_INSTALLED")
        self.assertTrue((folder / "w.safetensors").is_file())

    def test_a_folder_moved_by_a_kit_prune_can_still_be_restored(self) -> None:
        # The kit's own prune writes no `.studiohub-removed.json`; the name carries the package.
        trash = self.library / "_trash"
        folder = trash / "b-model-20261028-101010"
        (folder / ("c" * 40)).mkdir(parents=True)
        restored = model_removal.restore(self.library, folder)
        self.assertEqual(restored["package_id"], "b-model")
        self.assertTrue((self.library / "b-model").is_dir())

    def test_a_path_outside_the_librarys_trash_is_refused(self) -> None:
        elsewhere = Path(tempfile.mkdtemp(prefix="elsewhere-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(elsewhere, True))
        (elsewhere / "x").mkdir()
        with self.assertRaises(model_removal.RemoveRefused) as caught:
            model_removal.restore(self.library, elsewhere / "x")
        # Nothing was moved: the answer is a refusal either way, and the folder is untouched.
        self.assertIn(caught.exception.code, ("BAD_FOLDER", "NOT_REMOVED"))
        self.assertTrue((elsewhere / "x").is_dir())

    def test_a_link_inside_the_trash_is_refused(self) -> None:
        # A link would be "a folder inside _trash" to a naive check while restoring whatever it
        # points at - the one thing this path must never do.
        outside = Path(tempfile.mkdtemp(prefix="outside-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(outside, True))
        (outside / "y").mkdir()
        (self.library / "_trash").mkdir(parents=True, exist_ok=True)
        link = self.library / "_trash" / "b-model-20261028-101010"
        link.symlink_to(outside / "y")
        with self.assertRaises(model_removal.RemoveRefused):
            model_removal.restore(self.library, link)
        self.assertTrue((outside / "y").is_dir(), "the link's target was moved")


class ExpiryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.library = library_with()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.library, True))

    def test_nothing_younger_than_thirty_days_is_handed_over(self) -> None:
        remove(self.library)
        answer = model_removal.handoff(self.library, now=time.time() + 29 * 86400)
        self.assertEqual(answer["moved"], [])
        self.assertEqual(len(answer["kept"]), 1)
        self.assertTrue((self.library / "_trash").is_dir())

    def test_after_thirty_days_it_goes_to_macos_trash_through_the_existing_path(self) -> None:
        remove(self.library)
        trashed: list[str] = []

        def fake_macos_trash(paths):
            trashed.extend(paths)
            for path in paths:                # what the real one does on success
                __import__("shutil").rmtree(path, True)
            return ["ok" for _ in paths]

        with mock.patch.object(model_removal.output_storage, "_macos_trash",
                               side_effect=fake_macos_trash, create=True):
            answer = model_removal.handoff(self.library, now=time.time() + 31 * 86400)
        self.assertEqual(len(answer["moved"]), 1)
        self.assertEqual(len(trashed), 1)
        self.assertIn("_trash", trashed[0])

    def test_a_failed_handoff_keeps_the_folder_and_reports_it(self) -> None:
        answer_removed = remove(self.library)
        with mock.patch("components.hub.output_storage._macos_trash",
                        return_value=["Operation not permitted"]):
            answer = model_removal.handoff(self.library, now=time.time() + 31 * 86400)
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["moved"], [])
        self.assertEqual(len(answer["failed"]), 1)
        self.assertTrue(Path(answer_removed["moved_to"]).is_dir(), "a failed handoff deleted nothing")

    def test_zero_days_hands_nothing_over(self) -> None:
        remove(self.library)
        answer = model_removal.handoff(self.library, days=0)
        self.assertEqual((answer["moved"], answer["cutoff_seconds"]), ([], None))


class ListingTests(unittest.TestCase):
    def test_the_listing_reports_what_is_recoverable_and_its_age(self) -> None:
        library = library_with()
        self.addCleanup(lambda: __import__("shutil").rmtree(library, True))
        remove(library, now=1_700_000_000.0)
        rows = model_removal.listing(library)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["package_id"], "a-model")
        self.assertEqual(rows[0]["bytes"], 10)
        self.assertTrue(rows[0]["receipt"])
        self.assertEqual(rows[0]["removed_at"], 1_700_000_000.0)

    def test_a_kit_prune_folder_is_listed_without_a_receipt(self) -> None:
        library = library_with(receipt=False)
        self.addCleanup(lambda: __import__("shutil").rmtree(library, True))
        folder = library / "_trash" / "b-model-20261028-101010"
        folder.mkdir(parents=True)
        (folder / "w").write_bytes(b"z")
        rows = model_removal.listing(library)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["package_id"], "b-model")
        self.assertFalse(rows[0]["receipt"])
        self.assertEqual(rows[0]["bytes"], 1)

    def test_an_absent_trash_is_an_empty_list_not_an_error(self) -> None:
        library = library_with(receipt=False)
        self.addCleanup(lambda: __import__("shutil").rmtree(library, True))
        self.assertEqual(model_removal.listing(library), [])


if __name__ == "__main__":
    unittest.main()
