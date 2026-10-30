"""The install screen's projection: what copying a set costs, from where, and what "complete" means.

**Owner decision 2026-10-28.** What is pinned here, each because it is a failure this project has
already paid for rather than a preference:

1. **The free space is the model library's volume**, not the data directory's. Every kit setup
   keeps the models on an external drive, and `/api/hub/resources` answers about the data dir -
   so the screen had a number about a different disk.
2. **A preset model the SSD never shipped still installs.** The 2026-10-28 write carried manifests
   for `florence-2-large-ft` and `phonon-2-mlx` with no weights on any drive. That model falls
   back to another Mac's LAN share or the pinned internet URLs, says which, and stays in the
   totals - it is never quietly dropped, and the preset is never called complete without it.
3. **Complete means installed and verified.** Built from the receipts, never from sizes and never
   from `progress = 100`.
4. **The refusal is the existing floor.** Copying stops at `disk_governor.STOP_BYTES`, the same
   number that stops new work, and the sentence names both figures.
5. **Nothing is remembered.** Every call recomputes; a cached projection is a number the screen
   shows after it stopped being true.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from components import model_presets
from components.hub.backend import model_install_plan as plan

ROOT = Path(__file__).resolve().parents[1]


def manifests() -> dict[str, dict]:
    return {path.stem: json.loads(path.read_text(encoding="utf-8"))
            for path in (ROOT / "model-manifests").glob("*.json")}


def modality_of(engine: str) -> str:
    table = {"image-mflux": "image", "image-mlx-serve": "image", "voice-mlx": "voice",
             "voice-vits": "voice", "subtitle-mlx": "transcription", "music-mlx": "music",
             "music-yue2": "music", "chat-mlx": "chat", "embed-mlx": "embeddings",
             "vision-mlx": "vision", "video-ltx": "video"}
    if engine not in table:
        raise ValueError(f"unregistered engine {engine!r}")
    return table[engine]


def receipts(*verified: str) -> dict[str, dict]:
    return {package: {"package_id": package, "state": "files_verified"} for package in verified}


class ProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = {
            "small": {"package_id": "small", "bytes": 1_000_000_000, "installed": False, "reason": None},
            "large": {"package_id": "large", "bytes": 9_000_000_000, "installed": False, "reason": None},
            "here": {"package_id": "here", "bytes": 5_000_000_000, "installed": True, "reason": None},
        }

    def test_only_what_is_missing_is_counted(self) -> None:
        # A second run of the same preset costs what is left. Counting the whole set again is the
        # number that makes an operator think a Mac cannot hold a set it already holds.
        out = plan.projection(packages=["small", "large", "here"], rows=self.rows,
                              disk_state={"free_bytes": 400_000_000_000}, stop_bytes=5_000_000_000)
        self.assertEqual(out["to_copy_bytes"], 10_000_000_000)
        self.assertEqual(out["free_after_bytes"], 390_000_000_000)
        self.assertIsNone(out["refused"])

    def test_a_copy_that_would_pass_the_floor_is_refused_and_names_both_numbers(self) -> None:
        out = plan.projection(packages=["large"], rows=self.rows,
                              disk_state={"free_bytes": 10_000_000_000, "path": "/Volumes/KIT"},
                              stop_bytes=5_000_000_000)
        self.assertEqual(out["free_after_bytes"], 1_000_000_000)
        self.assertIn("9.0 GB", out["refused"])
        self.assertIn("1.0 GB", out["refused"])
        self.assertIn("5.0 GB", out["refused"])
        self.assertIn("/Volumes/KIT", out["refused"])

    def test_an_unmeasured_disk_is_not_a_refusal_and_not_a_zero(self) -> None:
        # "I could not read the disk" and "the answer is no space" must not produce one value.
        out = plan.projection(packages=["large"], rows=self.rows,
                              disk_state={"free_bytes": None, "path": "/nope", "measured": False},
                              stop_bytes=5_000_000_000)
        self.assertIsNone(out["refused"])
        self.assertIsNone(out["free_after_bytes"])
        self.assertEqual(out["to_copy_bytes"], 9_000_000_000)

    def test_a_package_with_no_row_is_named_rather_than_dropped(self) -> None:
        out = plan.projection(packages=["small", "ghost"], rows=self.rows,
                              disk_state={"free_bytes": 10 ** 12}, stop_bytes=5_000_000_000)
        self.assertEqual(out["unknown_packages"], ["ghost"])


class DiskTests(unittest.TestCase):
    def test_the_free_figure_belongs_to_the_library_volume(self) -> None:
        # The whole point of the fix: the path measured is the library's, not the data dir's.
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "elsewhere").mkdir()          # the drive exists; the library does not yet
            library = Path(tmp) / "elsewhere" / "StudioHubModels"
            seen = {}
            usage = type("U", (), {"free": 123, "total": 456, "used": 333})()

            def fake_usage(path):
                seen["path"] = str(path)
                return usage

            with mock.patch.object(plan.shutil, "disk_usage", side_effect=fake_usage):
                state = plan.disk(library)
        # The library does not exist yet, so its nearest existing parent is measured - never the
        # data directory, which is a different volume on every kit setup.
        self.assertEqual(seen["path"], str(Path(tmp) / "elsewhere"),
                         "measured the nearest existing parent of the library, not the data dir")
        self.assertTrue(state["measured"])
        self.assertEqual((state["free_bytes"], state["total_bytes"]), (123, 456))
        self.assertEqual(state["path"], str(library))

    def test_a_real_library_reports_a_real_figure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = plan.disk(Path(tmp))
        self.assertTrue(state["measured"])
        self.assertGreater(state["free_bytes"], 0)


class SourceOrderTests(unittest.TestCase):
    def test_sources_are_lan_then_internet(self):
        manifest = json.loads((ROOT / "model-manifests" / "whisper-turbo.json").read_text())
        self.assertEqual(plan._sources("whisper-turbo", manifest, {"whisper-turbo": "mac-02"}),
                         [plan.SOURCE_LAN, plan.SOURCE_INTERNET])
        self.assertEqual(plan._sources("whisper-turbo", manifest, None), [plan.SOURCE_INTERNET])


class PresetScreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifests = manifests()

    def _snapshot(self, *, verified=(), library=None, free=10 ** 12):
        state = {"path": str(library or "/tmp/lib"), "free_bytes": free, "total_bytes": free * 2,
                 "measured": True}
        with mock.patch.object(plan, "disk", return_value=state):
            return plan.snapshot(
                                 manifests=self.manifests,
                                 receipts=receipts(*verified), library=Path(str(library or "/tmp/lib")),
                                 memory_gb=32, stop_bytes=5_000_000_000, modality_of=modality_of)

    def test_four_presets_with_their_own_completion_counts(self) -> None:
        screen = self._snapshot()
        self.assertEqual([p["preset_id"] for p in screen["presets"]],
                         ["essentials-8", "creator-16", "creator-plus-24", "studio-pro-32"])
        for preset in screen["presets"]:
            self.assertEqual(preset["installed_count"], 0)
            self.assertFalse(preset["complete"])
            self.assertEqual(preset["package_count"], len(preset["packages"]))
            # `own_packages` is the preset file's list; `packages` is what the screen can offer, so it
            # is that list minus anything that is not a manifest and plus nothing (companions are not
            # choices). Nothing on the shipped four is absent, so they are equal here - which is
            # itself the invariant worth pinning.
            self.assertEqual(sorted(preset["packages"]), sorted(preset["own_packages"]))

    def test_a_preset_is_complete_only_when_every_package_is_verified(self) -> None:
        every = model_presets.install_set(model_presets.by_id("essentials-8"), self.manifests)
        screen = self._snapshot(verified=tuple(every))
        eight = next(p for p in screen["presets"] if p["preset_id"] == "essentials-8")
        self.assertTrue(eight["complete"])
        self.assertEqual(eight["installed_count"], eight["package_count"])
        self.assertEqual(eight["projection"]["to_copy_bytes"], 0)

    def test_one_missing_package_leaves_the_preset_incomplete(self) -> None:
        # The "verified completion" rule: 9 of 10 is not "almost done", and the missing model is
        # named rather than inferred from a byte count.
        every = model_presets.install_set(model_presets.by_id("essentials-8"), self.manifests)
        screen = self._snapshot(verified=tuple(every[:-1]))
        eight = next(p for p in screen["presets"] if p["preset_id"] == "essentials-8")
        self.assertFalse(eight["complete"])
        self.assertEqual(eight["installed_count"], eight["package_count"] - 1)
        self.assertGreater(eight["projection"]["to_copy_bytes"], 0)

    def test_a_preset_model_missing_from_the_ssd_falls_back_and_stays_in_the_totals(self) -> None:
        """The 2026-10-28 finding, end to end.

        `florence-2-large-ft` is in the 16 GB preset and its weights were on none of the four
        drives. It must still be in the 16 GB preset's package list and its size total, it must
        offer the internet (its manifest pins URLs), and it must keep the preset incomplete until
        it has really arrived.
        """
        screen = self._snapshot()
        sixteen = next(p for p in screen["presets"] if p["preset_id"] == "creator-16")
        self.assertIn("florence-2-large-ft", sixteen["packages"])
        row = next(r for r in screen["models"] if r["package_id"] == "florence-2-large-ft")
        self.assertEqual(row["sources"], [plan.SOURCE_INTERNET],
                         "with no kit weights and no peer share, the pinned URLs are the source")
        self.assertFalse(row["installed"])
        self.assertGreater(row["bytes"], 0)
        self.assertEqual(sixteen["projection"]["unknown_packages"], [], "it is named nowhere as missing")

    def test_phonon_is_the_same_case(self) -> None:
        screen = self._snapshot()
        row = next(r for r in screen["models"] if r["package_id"] == "phonon-2-mlx")
        self.assertEqual(row["sources"], [plan.SOURCE_INTERNET])
        self.assertFalse(row["installed"])

    def test_the_screen_says_which_folder_the_free_figure_belongs_to(self) -> None:
        screen = self._snapshot(library="/Volumes/KH-STUDIO-ATLAS/model-target")
        self.assertEqual(screen["library"], "/Volumes/KH-STUDIO-ATLAS/model-target")
        self.assertEqual(screen["disk"]["path"], "/Volumes/KH-STUDIO-ATLAS/model-target")

    def test_every_model_is_grouped_by_its_job_and_nothing_is_hidden(self) -> None:
        screen = self._snapshot()
        jobs = {group["job"] for group in screen["groups"]}
        self.assertEqual(jobs, {"image", "voice", "writing", "vision", "music",
                                "transcription", "embeddings"})
        listed = {row["package_id"] for row in screen["models"]}
        # Every non-companion manifest appears, whatever its RAM floor and whatever preset lacks it.
        chosen = {p for preset in model_presets.load() for p in preset["packages"]}
        companions = plan.dependency_ids(self.manifests, model_presets.load())
        expected = {pid for pid, m in self.manifests.items()
                    if pid not in companions and not m.get("_manifest_error")}
        self.assertEqual(listed, expected)

    def test_a_model_too_big_for_this_mac_is_shown_with_its_reason(self) -> None:
        with mock.patch.object(plan, "disk", return_value={"path": "/tmp", "free_bytes": 10 ** 12,
                                                           "total_bytes": 2 * 10 ** 12,
                                                           "measured": True}):
            screen = plan.snapshot(manifests=self.manifests, receipts={}, library=Path("/tmp"),
                                   memory_gb=8, stop_bytes=5_000_000_000, modality_of=modality_of)
        big = next(r for r in screen["models"] if r["package_id"] == "qwen36-35b-4bit")
        self.assertIn(big["floor_gb"], (24,))
        self.assertIsNotNone(big["reason"], "a model too big must say so, not vanish")
        self.assertIn("8 GB", big["reason"])
        # And it is still listed: hidden is the one thing the owner's rule forbids.
        self.assertIn("qwen36-35b-4bit", {row["package_id"] for row in screen["models"]})

    def test_a_companion_is_not_offered_as_something_to_choose(self) -> None:
        screen = self._snapshot()
        ids = {row["package_id"] for row in screen["models"]}
        for companion in ("yue2-vae", "s3tokenizerv2", "pocket-tts-tokenizer"):
            with self.subTest(companion=companion):
                self.assertNotIn(companion, ids)

    def test_a_companion_that_a_preset_names_is_still_offered(self) -> None:
        # `whisper-turbo` is a transcription model the 8 GB preset lists AND a companion of the
        # Qwen voice packages. Reading the companion relationship alone would hide a model the
        # owner chose by name - which is the "blocked by name" failure in a different costume.
        screen = self._snapshot()
        ids = {row["package_id"] for row in screen["models"]}
        self.assertIn("whisper-turbo", ids)
        self.assertIn("whisper-turbo", {p for preset in model_presets.load()
                                        for p in preset["packages"]})
        # It also still arrives as a companion: the install set of the 16 GB preset has it twice
        # over, once chosen and once required, and lists it once.
        sixteen = model_presets.install_set(model_presets.by_id("creator-16"), self.manifests)
        self.assertEqual(sixteen.count("whisper-turbo"), 1)

    def test_a_refused_projection_reaches_the_preset(self) -> None:
        screen = self._snapshot(free=6_000_000_000)      # 1 GB of headroom against a 5 GB floor
        self.assertTrue(all(p["projection"]["refused"] for p in screen["presets"]))

    def test_the_snapshot_is_computed_fresh_every_call(self) -> None:
        before = self._snapshot()
        after = self._snapshot(verified=tuple(model_presets.install_set(
            model_presets.by_id("essentials-8"), self.manifests)))
        self.assertFalse(next(p for p in before["presets"] if p["preset_id"] == "essentials-8")["complete"])
        self.assertTrue(next(p for p in after["presets"] if p["preset_id"] == "essentials-8")["complete"])

    def test_every_preset_names_a_model_that_exists(self) -> None:
        # A typo in a preset file has to be visible on the screen, not discovered at install time.
        self.assertEqual(self._snapshot()["problems"], [])


class RouteTests(unittest.TestCase):
    """The real route answers, and one package it cannot describe does not blank the screen."""

    def test_the_route_publishes_the_screen(self) -> None:
        from fastapi.testclient import TestClient
        from components.hub import standalone
        from components.hub.backend import auth
        with tempfile.TemporaryDirectory() as tmp:
            models = Path(tmp) / "models"
            models.mkdir()
            os.environ["STUDIOHUB_MODELS_DIR"] = str(models)
            app = standalone.create_app(start_background=False)
            token = auth.load_token()
            with TestClient(app) as client:
                answer = client.get("/api/hub/model-presets", headers={"X-Hub-Token": token})
            self.assertEqual(answer.status_code, 200, answer.text)
            body = answer.json()
            self.assertEqual(len(body["presets"]), 4)
            self.assertTrue(body["groups"])
            self.assertIn("disk", body)
            self.assertEqual(body["problems"], [])


if __name__ == "__main__":
    unittest.main()
