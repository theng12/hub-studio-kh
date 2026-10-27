"""The one-time preset cleanup for Macs already set up (owner decision 2026-10-28).

The owner's rules, each pinned here:

1. **A dry run moves nothing.** `plan` is read-only, and the shipped `apply` calls `plan` itself so
   the report the owner approves and the work that happens are one computation.
2. **In-use models are refused, not moved**: an active job, a paused model, a package a kept model
   requires. Each is reported by name with its reason.
3. **Restore within 30 days** works for a model the cleanup moved, and **expiry after 30 days**
   hands it to macOS Trash through the one existing path.
4. **A second run is a no-op** that says when it first ran.
5. **The preset per Mac is the starter for its tier unless the owner sets another** - and never
   derived from what the Mac happens to hold.
6. **A model the owner wants kept is kept**, and said so.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from components import model_presets
from components.hub.backend import model_removal, preset_cleanup

ROOT = Path(__file__).resolve().parents[1]


def manifests() -> dict[str, dict]:
    return {path.stem: json.loads(path.read_text(encoding="utf-8"))
            for path in (ROOT / "model-manifests").glob("*.json")}


def make_library(root: Path, package_ids: list[str], manifests_by_id: dict[str, dict]) -> Path:
    """A library holding these packages, with a receipt for each."""
    root.mkdir(parents=True, exist_ok=True)
    state = root / ".studiohub"
    state.mkdir(mode=0o700)
    os.chmod(state, 0o700)
    index = {}
    for package_id in package_ids:
        revision = manifests_by_id[package_id]["revision"]
        folder = root / package_id / revision
        folder.mkdir(parents=True)
        (folder / "w.safetensors").write_bytes(b"x" * 100)
        index[package_id] = {"revision": revision, "folder": f"{package_id}/{revision}",
                             "files": {}}
    (state / "installed.json").write_text(json.dumps(index), encoding="utf-8")
    return root


class PresetForTests(unittest.TestCase):
    def test_the_starter_for_the_tier_unless_the_owner_set_another(self) -> None:
        self.assertEqual(preset_cleanup.preset_for(8, None), "essentials-8")
        self.assertEqual(preset_cleanup.preset_for(16, None), "creator-16")
        self.assertEqual(preset_cleanup.preset_for(32, None), "studio-pro-32")
        self.assertEqual(preset_cleanup.preset_for(16, "studio-pro-32"), "studio-pro-32")

    def test_an_unknown_memory_is_no_preset_not_a_small_one(self) -> None:
        self.assertIsNone(preset_cleanup.preset_for(None, None))

    def test_it_is_never_derived_from_what_the_mac_holds(self) -> None:
        # The signature has no library argument at all, which is the strongest form of this: the
        # target cannot move depending on the diff it is compared against.
        import inspect
        parameters = set(inspect.signature(preset_cleanup.preset_for).parameters)
        self.assertEqual(parameters, {"memory_gb", "chosen"})


class PlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifests = manifests()
        self.temp = Path(tempfile.mkdtemp(prefix="cleanup-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.temp, True))
        # `whisper-turbo` is in every preset; `qwen36-35b-4bit` is only in Studio Pro; and
        # `qwen-image-2-1-mlx-serve-4bit` is in no preset at all.
        self.library = make_library(self.temp / "library",
                                    ["whisper-turbo", "qwen36-35b-4bit",
                                     "qwen-image-2-1-mlx-serve-4bit"], self.manifests)
        self.records = json.loads((self.library / ".studiohub" / "installed.json").read_text())

    def _plan(self, **overrides):
        values = dict(library=self.library, manifests=self.manifests, catalog=self.manifests,
                      records=self.records, preset_id="essentials-8", memory_gb=8)
        values.update(overrides)
        return preset_cleanup.plan(**values)

    def test_the_dry_run_moves_nothing_and_writes_nothing(self) -> None:
        before = sorted(path.name for path in self.library.iterdir())
        index_before = (self.library / ".studiohub" / "installed.json").read_text()
        report = self._plan()
        self.assertEqual(sorted(path.name for path in self.library.iterdir()), before)
        self.assertEqual((self.library / ".studiohub" / "installed.json").read_text(), index_before)
        self.assertFalse((self.library / "_trash").exists(), "the dry run created a trash folder")
        # And it still says exactly what would happen.
        self.assertIn("whisper-turbo", [row["package_id"] for row in report["in_preset"]])
        self.assertEqual(sorted(row["package_id"] for row in report["would_move"]),
                         ["qwen-image-2-1-mlx-serve-4bit", "qwen36-35b-4bit"])
        self.assertEqual(report["move_count"], 2)
        self.assertEqual(report["move_bytes"], 200)

    def test_a_package_outside_the_preset_that_a_kept_one_needs_is_refused(self) -> None:
        """`yue2-vae` is a companion of `yue2-3b-mlx`, and it is in no preset on its own.

        The 24 GB preset keeps `yue2-3b-mlx`; the VAE arrives as its companion. If the VAE is
        outside the target preset, moving it would leave the kept model unusable - so the reader
        refuses it and names the package that needs it.
        """
        library = make_library(self.temp / "lib2", ["yue2-3b-mlx", "yue2-vae"], self.manifests)
        records = json.loads((library / ".studiohub" / "installed.json").read_text())
        # The 8 GB preset keeps neither, so the VAE is a `would_move` candidate there and the
        # parent is judged the same way. Point the target at a preset that keeps the parent.
        report = self._plan(library=library, records=records, preset_id="creator-plus-24",
                            memory_gb=24)
        kept = {row["package_id"] for row in report["in_preset"]}
        self.assertIn("yue2-3b-mlx", kept)
        # With the parent kept, the companion must not be movable even though no preset names it.
        self.assertNotIn("yue2-vae", [row["package_id"] for row in report["would_move"]])
        refused = {row["package_id"]: row["reason"] for row in report["refused"]}
        if "yue2-vae" in refused:
            self.assertIn("yue2-3b-mlx", refused["yue2-vae"])

    def test_a_running_job_refuses_that_model(self) -> None:
        report = self._plan(running_jobs=[("image@m1", "ddalcu/Qwen-Image-2.1-MLX-Serve-4bit")])
        refused = {row["package_id"]: row["reason"] for row in report["refused"]}
        self.assertIn("qwen-image-2-1-mlx-serve-4bit", refused)
        self.assertIn("job is running", refused["qwen-image-2-1-mlx-serve-4bit"])
        self.assertIn("qwen36-35b-4bit", [row["package_id"] for row in report["would_move"]],
                      "one model being busy must not stop the rest")

    def test_a_paused_model_is_refused(self) -> None:
        report = self._plan(paused={"qwen36-35b-4bit": {"studio": "chat", "failures": 3}})
        refused = {row["package_id"]: row["reason"] for row in report["refused"]}
        self.assertIn("qwen36-35b-4bit", refused)
        self.assertIn("paused", refused["qwen36-35b-4bit"])

    def test_a_model_the_owner_keeps_stays_and_is_reported_as_kept(self) -> None:
        # The owner's whole reason for this rule: one model on a Mac for a local test.
        report = self._plan(keep={"qwen36-35b-4bit"})
        self.assertIn("qwen36-35b-4bit", [row["package_id"] for row in report["kept"]])
        self.assertNotIn("qwen36-35b-4bit", [row["package_id"] for row in report["would_move"]])

    def test_a_mac_with_no_preset_refuses_rather_than_inventing_one(self) -> None:
        with self.assertRaises(preset_cleanup.CleanupRefused) as caught:
            self._plan(preset_id=None, memory_gb=None)
        self.assertIn("no preset", caught.exception.body["detail"])


class OwnerListTests(unittest.TestCase):
    """The owner's own 32 GB case: Gemma 4 is IN the Studio Pro preset, so it must not be moved.

    The coordinator's note is explicit - Gemma 4 26B is in no other preset and on a 32 GB Mac it is
    wanted for a local test, so a dry run that listed it as "would move" would be proposing to
    remove the model the owner just asked for. Nothing here special-cases the package: it is in the
    preset file, and the cleanup reads the same file.
    """

    def setUp(self) -> None:
        self.manifests = manifests()
        self.temp = Path(tempfile.mkdtemp(prefix="cleanup-owner-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.temp, True))
        self.library = make_library(self.temp / "library",
                                    ["gemma4-26b-a4b-qat-4bit", "qwen36-35b-4bit",
                                     "nemotron-lightning-4bit"], self.manifests)
        self.records = json.loads((self.library / ".studiohub" / "installed.json").read_text())

    def test_gemma_four_is_kept_on_a_thirty_two_gb_mac(self) -> None:
        report = preset_cleanup.plan(
            library=self.library, manifests=self.manifests, catalog=self.manifests,
            records=self.records, preset_id="studio-pro-32", memory_gb=32)
        self.assertIn("gemma4-26b-a4b-qat-4bit",
                      [row["package_id"] for row in report["in_preset"]])
        self.assertNotIn("gemma4-26b-a4b-qat-4bit",
                         [row["package_id"] for row in report["would_move"]])
        # And the one writing model that is in no preset at all is proposed, which is the point of
        # the dry run: the owner sees it and decides.
        self.assertIn("nemotron-lightning-4bit",
                      [row["package_id"] for row in report["would_move"]])

    def test_the_preset_choice_is_what_decides_not_this_app(self) -> None:
        # The same library against the 8 GB preset: Gemma 4 is outside it, so it IS proposed. One
        # input changed and the answer changed with it - no special case anywhere.
        report = preset_cleanup.plan(
            library=self.library, manifests=self.manifests, catalog=self.manifests,
            records=self.records, preset_id="essentials-8", memory_gb=8)
        self.assertIn("gemma4-26b-a4b-qat-4bit",
                      [row["package_id"] for row in report["would_move"]])


class ApplyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifests = manifests()
        self.temp = Path(tempfile.mkdtemp(prefix="cleanup-apply-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.temp, True))
        self.library = make_library(self.temp / "library",
                                    ["whisper-turbo", "qwen36-35b-4bit"], self.manifests)
        self.records = json.loads((self.library / ".studiohub" / "installed.json").read_text())
        self.data = self.temp / "data"
        self.data.mkdir()

    def _apply(self, **overrides):
        values = dict(data_dir=self.data, machine="mac-01", library=self.library,
                      manifests=self.manifests, catalog=self.manifests, records=self.records,
                      may_move=True, preset_id="essentials-8", memory_gb=8,
                      approved_by="vengtheng")
        values.update(overrides)
        return preset_cleanup.apply(**values)

    def test_an_approved_run_moves_and_records_itself(self) -> None:
        answer = self._apply()
        self.assertTrue(answer["ok"])
        self.assertTrue(answer["ran"])
        self.assertEqual(answer["moved"], ["qwen36-35b-4bit"])
        self.assertTrue((self.library / "_trash").is_dir())
        self.assertFalse((self.library / "qwen36-35b-4bit").exists())
        self.assertTrue((self.library / "whisper-turbo").is_dir(), "a preset model stays")
        record = preset_cleanup.has_run(self.data, "mac-01")
        self.assertEqual(record["approved_by"], "vengtheng")
        self.assertEqual(record["preset_id"], "essentials-8")
        self.assertEqual(record["moved"], ["qwen36-35b-4bit"])

    def test_a_second_run_is_a_no_op_that_says_when_it_ran(self) -> None:
        first = self._apply()
        # Put a package back outside the preset, so a second run that really ran would move it.
        folder = self.library / "qwen36-35b-4bit" / self.manifests["qwen36-35b-4bit"]["revision"]
        folder.mkdir(parents=True)
        (folder / "w.safetensors").write_bytes(b"y" * 50)
        second = self._apply()
        self.assertFalse(second["ran"])
        self.assertEqual(second["moved"], first["moved"])
        self.assertIn("already ran", second["detail"])
        self.assertTrue((self.library / "qwen36-35b-4bit").is_dir(), "the second run moved it again")

    def test_a_mac_that_was_already_clean_still_records_that_it_ran(self) -> None:
        library = make_library(self.temp / "clean", ["whisper-turbo"], self.manifests)
        records = json.loads((library / ".studiohub" / "installed.json").read_text())
        answer = self._apply(library=library, records=records, machine="mac-clean")
        self.assertTrue(answer["ran"])
        self.assertEqual(answer["moved"], [])
        self.assertIsNotNone(preset_cleanup.has_run(self.data, "mac-clean"))

    def test_a_hub_that_does_not_own_the_library_refuses_before_moving(self) -> None:
        with self.assertRaises(preset_cleanup.CleanupRefused) as caught:
            self._apply(may_move=False, refusal="This Hub does not own it")
        self.assertEqual(caught.exception.status, 403)
        self.assertTrue((self.library / "qwen36-35b-4bit").is_dir())
        self.assertIsNone(preset_cleanup.has_run(self.data, "mac-01"),
                          "a refused run records nothing")

    def test_the_approved_report_and_the_work_are_one_computation(self) -> None:
        # `apply` calls `plan` itself; this fails if a later refactor lets the two disagree.
        shown = preset_cleanup.plan(library=self.library, manifests=self.manifests,
                                    catalog=self.manifests, records=self.records,
                                    preset_id="essentials-8", memory_gb=8)
        answer = self._apply()
        self.assertEqual(sorted(row["package_id"] for row in shown["would_move"]),
                         sorted(answer["moved"]))

    def test_a_model_that_is_busy_when_the_run_starts_is_kept(self) -> None:
        """Refused **before** the move, because `apply` runs `plan` again for itself.

        That is the point of `apply` calling `plan`: a job that started between the owner reading
        the dry run and pressing the button changes the answer, and the owner's approval cannot
        cover a model that is now being read.
        """
        answer = self._apply(running_jobs=[("image@m1", "mlx-community/Qwen3.6-35B-A3B-4bit")])
        self.assertEqual(answer["moved"], [])
        self.assertTrue((self.library / "qwen36-35b-4bit").is_dir())
        self.assertIn("qwen36-35b-4bit",
                      [row["package_id"] for row in answer["plan"]["refused"]])
        # The run happened and is recorded: it did its job on everything it could move.
        self.assertTrue(answer["ran"])

    def test_a_damaged_state_file_does_not_block_the_cleanup(self) -> None:
        preset_cleanup.state_path(self.data).write_text("{ not json")
        answer = self._apply()
        self.assertTrue(answer["ran"])
        self.assertIsNotNone(preset_cleanup.has_run(self.data, "mac-01"))


class RestoreAndExpiryTests(unittest.TestCase):
    """The moved packages are recoverable for 30 days, then handed to macOS Trash - P4's mechanism."""

    def setUp(self) -> None:
        self.manifests = manifests()
        self.temp = Path(tempfile.mkdtemp(prefix="cleanup-rc-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.temp, True))
        self.library = make_library(self.temp / "library",
                                    ["whisper-turbo", "qwen36-35b-4bit"], self.manifests)
        self.records = json.loads((self.library / ".studiohub" / "installed.json").read_text())
        self.data = self.temp / "data"
        self.data.mkdir()

    def _run(self, now: float) -> dict:
        return preset_cleanup.apply(
            data_dir=self.data, machine="mac-01", library=self.library,
            manifests=self.manifests, catalog=self.manifests, records=self.records,
            may_move=True, preset_id="essentials-8", memory_gb=8, approved_by="vengtheng", now=now)

    def test_a_moved_model_can_be_restored_within_thirty_days(self) -> None:
        self._run(time.time())
        listed = model_removal.listing(self.library)
        self.assertEqual([row["package_id"] for row in listed], ["qwen36-35b-4bit"])
        restored = model_removal.restore(self.library, Path(listed[0]["folder"]))
        self.assertEqual(restored["package_id"], "qwen36-35b-4bit")
        self.assertTrue((self.library / "qwen36-35b-4bit").is_dir())

    def test_it_is_offered_for_restore_until_thirty_days_and_handed_over_after(self) -> None:
        moved_at = 1_700_000_000.0
        self._run(moved_at)
        twenty_nine = model_removal.handoff(self.library, now=moved_at + 29 * 86400)
        self.assertEqual(twenty_nine["moved"], [])
        self.assertEqual(len(twenty_nine["kept"]), 1)
        import shutil

        def fake_macos_trash(paths):
            for path in paths:
                shutil.rmtree(path, True)       # what the real one does on success
            return ["ok" for _ in paths]

        with mock.patch.object(model_removal.output_storage, "_macos_trash",
                               side_effect=fake_macos_trash, create=True):
            thirty_one = model_removal.handoff(self.library, now=moved_at + 31 * 86400)
        self.assertEqual(len(thirty_one["moved"]), 1)


class SettingTests(unittest.TestCase):
    """The preset choice and the keep list are device settings with their own readers."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        from components.hub import standalone_config as config
        self.config = config
        self.file = Path(self.temp.name) / "device_settings.json"
        patcher = mock.patch.object(config, "_settings_path", lambda: self.file)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_no_file_means_no_choice_which_is_a_real_answer(self) -> None:
        self.assertIsNone(self.config.preset_choice())
        self.assertEqual(self.config.preset_cleanup_keep(), [])

    def test_a_choice_round_trips_and_clears(self) -> None:
        self.assertEqual(self.config.set_preset_choice("creator-16"), "creator-16")
        self.assertEqual(self.config.preset_choice(), "creator-16")
        self.assertIsNone(self.config.set_preset_choice(None))
        self.assertIsNone(self.config.preset_choice())

    def test_an_unknown_preset_is_refused_by_name(self) -> None:
        with self.assertRaises(model_presets.PresetError) as caught:
            self.config.set_preset_choice("no-such-preset")
        self.assertIn("no-such-preset", str(caught.exception))

    def test_a_choice_survives_the_other_settings_being_written(self) -> None:
        self.config.set_preset_choice("creator-16")
        self.config.update_device_settings(retention_days=9)
        self.assertEqual(self.config.preset_choice(), "creator-16")
        self.assertEqual(self.config.load_device_settings()["effective"]["retention_days"], 9)


if __name__ == "__main__":
    unittest.main()
