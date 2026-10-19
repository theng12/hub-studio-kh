"""The unified Trash Folder in the app (owner request 2026-10-15).

Its per-root listing, the macOS Trash handoff (button and the timed one), start-up adoption of old
per-install Trash roots, and the owner-adjustable handoff setting. Temp directories only: no test
reads or writes the real ~/Library, /Applications or ~/StudioHubModels.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from components.hub import disk_governor, output_storage, standalone_config  # noqa: E402
from components.hub.backend import auth, ledger  # noqa: E402

DAY = 86400.0
GB = 1_000_000_000


def day_stamp(now: float, days_ago: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(now - days_ago * DAY))


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(os.path.realpath(temporary.name))
        self.data = self.root / "hub"
        patcher = mock.patch.object(ledger, "DATA_DIR", self.data)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.trash = self.data / "trash"

    def put(self, root: Path, item: str, size: int = 100) -> Path:
        path = root / item
        path.mkdir(parents=True)
        (path / "payload.bin").write_bytes(b"x" * size)
        return path


class ListingTests(Fixture):
    def test_overview_reports_the_unified_root_and_managed_fallbacks(self) -> None:
        now = time.time()
        self.put(self.trash, f"{day_stamp(now, 0)}/batch_1", 120)
        fallback = self.root / "external" / output_storage.FALLBACK_TRASH_NAME
        self.put(fallback, f"{day_stamp(now, 2)}/batch_2", 80)
        with mock.patch.object(output_storage, "_trash_bases", lambda: [self.trash, fallback]):
            got = output_storage.trash_overview(now)
        self.assertEqual(got["count"], 1)                       # items are the unified root's
        self.assertEqual(got["bytes"], sum(item["bytes"] for item in got["items"]))
        self.assertEqual([root["path"] for root in got["roots"]], [str(self.trash), str(fallback)])
        self.assertTrue(got["roots"][0]["primary"])
        self.assertFalse(got["roots"][1]["primary"])
        self.assertEqual([root["count"] for root in got["roots"]], [1, 1])
        self.assertGreater(got["roots"][1]["bytes"], 0)
        self.assertFalse(got["roots"][0]["cross_volume"])

    def test_overview_is_empty_when_nothing_was_retired(self) -> None:
        got = output_storage.trash_overview()
        self.assertEqual((got["count"], got["bytes"]), (0, 0))
        self.assertEqual(len(got["roots"]), 1)


class HandoffTests(Fixture):
    """The reusable helper behind both the button route and the cleanup-loop handoff."""

    def move_to_macos(self, paths: list[str]) -> list[str]:
        destination = self.root / "macOS Trash"
        destination.mkdir(exist_ok=True)
        for path in paths:
            os.rename(path, destination / Path(path).name)
        return ["ok"] * len(paths)

    def test_items_older_than_the_handoff_go_to_macos_trash(self) -> None:
        now = time.time()
        old = self.put(self.trash, f"{day_stamp(now, 9)}/old_1")
        young = self.put(self.trash, f"{day_stamp(now, 1)}/young_1")
        with mock.patch.object(output_storage, "_macos_trash", self.move_to_macos):
            result = output_storage.trash_handoff(7, now=now)
        self.assertTrue(result["ok"])
        self.assertEqual(result["moved_count"], 1)
        self.assertFalse(old.exists())
        self.assertTrue(young.exists())
        self.assertEqual(output_storage.trash_listing(now)["count"], 1)

    def test_a_failed_move_leaves_the_item_in_the_trash_folder(self) -> None:
        now = time.time()
        old = self.put(self.trash, f"{day_stamp(now, 30)}/stuck_1")
        with mock.patch.object(output_storage, "_macos_trash", lambda paths: ["refused"] * len(paths)):
            result = output_storage.trash_handoff(7, now=now)
        self.assertFalse(result["ok"])
        self.assertEqual(result["moved_count"], 0)
        self.assertEqual(len(result["failed"]), 1)
        self.assertTrue(old.exists())
        self.assertEqual(output_storage.trash_listing(now)["count"], 1)

    def test_the_boundary_is_the_dated_folder_age(self) -> None:
        now = time.time()
        edge = self.put(self.trash, f"{day_stamp(now, 7)}/edge_1")
        with mock.patch.object(output_storage, "_macos_trash", self.move_to_macos):
            self.assertEqual(output_storage.trash_handoff(8, now=now)["moved_count"], 0)
            self.assertTrue(edge.exists())
            self.assertEqual(output_storage.trash_handoff(7, now=now)["moved_count"], 1)

    def test_an_invalid_age_with_no_cap_moves_nothing(self) -> None:
        now = time.time()
        old = self.put(self.trash, f"{day_stamp(now, 90)}/old_1")
        with mock.patch.object(output_storage, "_macos_trash", self.move_to_macos):
            result = output_storage.trash_handoff(0, now=now)
        self.assertEqual(result["moved_count"], 0)
        self.assertTrue(old.exists())


class SizeCapTests(Fixture):
    """The Trash Folder's own size cap (owner rule 2026-10-19): the OLDEST items go to macOS Trash
    until the unified Trash Folder is under the cap. Nothing is ever permanently deleted."""

    def sizes(self, mapping: dict[str, int]):
        return mock.patch.object(disk_governor, "cached_physical",
                                 lambda path, _now: mapping[Path(path).name])

    def move_to_macos(self, paths: list[str]) -> list[str]:
        destination = self.root / "macOS Trash"
        destination.mkdir(exist_ok=True)
        for path in paths:
            os.rename(path, destination / Path(path).name)
        return ["ok"] * len(paths)

    def test_the_oldest_items_go_first_until_the_trash_folder_is_under_the_cap(self) -> None:
        now = time.time()
        oldest = self.put(self.trash, f"{day_stamp(now, 9)}/old_1")
        middle = self.put(self.trash, f"{day_stamp(now, 5)}/mid_1")
        newest = self.put(self.trash, f"{day_stamp(now, 1)}/new_1")
        with self.sizes({"old_1": 12 * GB, "mid_1": 12 * GB, "new_1": 12 * GB}), \
                mock.patch.object(output_storage, "_macos_trash", self.move_to_macos):
            result = output_storage.trash_handoff(365, 20 * GB, now=now)   # no item is a year old
        self.assertTrue(result["ok"])
        self.assertEqual(result["trash_bytes"], 36 * GB)
        self.assertEqual(result["moved_count"], 2)          # 36 GB -> 12 GB, under the 20 GB cap
        self.assertFalse(result["over_cap"])
        self.assertFalse(oldest.exists())
        self.assertFalse(middle.exists())
        self.assertTrue(newest.exists(), "the newest item must not go once the cap is met")

    def test_the_cap_counts_every_root_but_only_the_hubs_own_items_move(self) -> None:
        now = time.time()
        mine = self.put(self.trash, f"{day_stamp(now, 1)}/mine")
        fallback = self.root / "external" / output_storage.FALLBACK_TRASH_NAME
        self.put(fallback, f"{day_stamp(now, 1)}/theirs")
        with mock.patch.object(output_storage, "_trash_bases", lambda: [self.trash, fallback]), \
                self.sizes({"mine": 5 * GB, "theirs": 30 * GB}), \
                mock.patch.object(output_storage, "_macos_trash", self.move_to_macos):
            result = output_storage.trash_handoff(365, 20 * GB, now=now)
        self.assertEqual(result["trash_bytes"], 35 * GB)     # counted honestly, every root
        self.assertEqual(result["moved_count"], 1)           # the Hub's own item, young as it is
        self.assertFalse(mine.exists())
        self.assertTrue(result["over_cap"], "another root still holds it over the cap")

    def test_a_failed_move_leaves_the_item_in_the_trash_folder(self) -> None:
        now = time.time()
        item = self.put(self.trash, f"{day_stamp(now, 1)}/mine")
        with self.sizes({"mine": 30 * GB}), \
                mock.patch.object(output_storage, "_macos_trash", lambda paths: ["refused"] * len(paths)):
            result = output_storage.trash_handoff(365, 20 * GB, now=now)
        self.assertFalse(result["ok"])
        self.assertEqual(result["moved_count"], 0)
        self.assertEqual(len(result["failed"]), 1)
        self.assertTrue(item.exists())
        self.assertTrue(result["over_cap"])

    def test_neither_rule_eligible_moves_nothing(self) -> None:
        now = time.time()
        young = self.put(self.trash, f"{day_stamp(now, 1)}/young_1")
        with self.sizes({"young_1": 5 * GB}), \
                mock.patch.object(output_storage, "_macos_trash", self.move_to_macos):
            result = output_storage.trash_handoff(365, 20 * GB, now=now)
        self.assertEqual((result["moved_count"], result["over_cap"]), (0, False))
        self.assertTrue(young.exists())


class AdoptLegacyTests(Fixture):
    def sources(self) -> list[tuple[Path, str]]:
        return [(self.root / "Applications/_trash", "legacy-app"),
                (self.root / "StudioHubModels/_trash", "legacy-model"),
                (self.data / "_trash/failed-writes", "legacy-failed-write"),
                (self.root / "outputs/.studiohub-trash", "legacy-output-trash")]

    def build(self) -> list[tuple[Path, str]]:
        sources = self.sources()
        for index, (root, _label) in enumerate(sources):
            root.mkdir(parents=True, exist_ok=True)
            (root / f"retired-{index}").write_bytes(b"x" * (index + 1))
        return sources

    def test_adopts_every_legacy_root_with_a_manifest_and_is_idempotent(self) -> None:
        sources = self.build()
        report = output_storage.adopt_legacy(sources=sources)
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["moved_count"], 4)
        for root, _label in sources:
            self.assertEqual(list(root.iterdir()), [])
        manifest = json.loads(Path(report["manifest"]).read_text())
        recorded = {row["source"] for row in manifest["adopted"]}
        self.assertEqual(recorded, {str(root / f"retired-{index}")
                                    for index, (root, _label) in enumerate(sources)})
        self.assertTrue(all(row["adopted_at"] for row in manifest["adopted"]))
        adopted = [p for day in self.trash.iterdir() if day.is_dir() and day.name[:4].isdigit()
                   for p in day.iterdir()]
        self.assertEqual(len(adopted), 4)
        again = output_storage.adopt_legacy(sources=sources)
        self.assertEqual(again["moved_count"], 0)
        self.assertEqual(len([p for day in self.trash.iterdir() if day.is_dir() and day.name[:4].isdigit()
                              for p in day.iterdir()]), 4)

    def test_two_roots_with_the_same_name_both_survive(self) -> None:
        first, second = self.root / "one/_trash", self.root / "two/_trash"
        for root in (first, second):
            root.mkdir(parents=True)
            (root / "same.bin").write_bytes(b"x")
        report = output_storage.adopt_legacy(sources=[(first, "legacy-app"), (second, "legacy-model")])
        self.assertEqual(report["moved_count"], 2)
        names = [p.name for day in self.trash.iterdir() if day.is_dir() and day.name[:4].isdigit()
                 for leaf in day.iterdir() for p in leaf.iterdir()]
        self.assertEqual(names, ["same.bin", "same.bin"])

    def test_a_cross_volume_root_is_left_in_place_and_reported(self) -> None:
        sources = self.build()
        foreign = sources[3][0]
        with mock.patch.object(output_storage, "_same_volume", lambda path: Path(path) != foreign):
            report = output_storage.adopt_legacy(sources=sources)
        self.assertEqual(report["moved_count"], 3)
        self.assertEqual(report["cross_volume"], [str(foreign)])
        self.assertTrue((foreign / "retired-3").exists())

    def test_a_missing_root_is_reported_without_stopping_the_rest(self) -> None:
        present = self.root / "present/_trash"
        present.mkdir(parents=True)
        (present / "retired").write_bytes(b"x")
        missing = self.root / "absent/_trash"
        report = output_storage.adopt_legacy(sources=[(present, "legacy-app"), (missing, "legacy-model")])
        self.assertEqual(report["moved_count"], 1)
        self.assertEqual(report["missing"], [str(missing)])


class HandoffSettingTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(os.path.realpath(temporary.name))
        patcher = mock.patch.dict(os.environ, {"STUDIOHUB_DATA_DIR": str(self.root)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_default_is_seven_days_and_twenty_gb_and_the_owner_can_change_both(self) -> None:
        defaults = standalone_config.load_device_settings()["defaults"]
        self.assertEqual((defaults["retention_days"], defaults["trash_handoff_days"],
                          defaults["trash_handoff_gb"]), (7, 7, 20))
        saved = standalone_config.save_device_settings(
            max_bytes=20_000_000_000, retention_days=3, trash_handoff_days=14, trash_handoff_gb=50)
        self.assertEqual((saved["effective"]["trash_handoff_days"], saved["effective"]["trash_handoff_gb"]),
                         (14, 50))
        restored = standalone_config.restore_device_defaults()
        self.assertEqual((restored["effective"]["retention_days"], restored["effective"]["trash_handoff_days"],
                          restored["effective"]["trash_handoff_gb"]), (7, 7, 20))
        with self.assertRaises(ValueError):
            standalone_config.update_device_settings(trash_handoff_days=0)
        with self.assertRaises(ValueError):
            standalone_config.update_device_settings(trash_handoff_gb=0)
        with self.assertRaises(ValueError):
            standalone_config.update_device_settings(trash_handoff_gb=True)


class DefaultsMigrationTests(unittest.TestCase):
    """The 7-day rule reaches a Mac that never changed the setting, and never overrides one that did."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(os.path.realpath(temporary.name))
        patcher = mock.patch.dict(os.environ, {"STUDIOHUB_DATA_DIR": str(self.root)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, value: dict) -> None:
        (self.root / "device_settings.json").write_text(json.dumps(value))

    def test_an_untouched_3_day_default_of_version_2_becomes_7(self) -> None:
        self.write({"max_bytes": 20_000_000_000, "retention_days": 3, "minimum_free_bytes": 5_000_000_000,
                    "cleanup_enabled": True, "cleanup_interval_seconds": 300, "trash_handoff_days": 7,
                    "defaults_version": 2})
        effective = standalone_config.load_device_settings()["effective"]
        self.assertEqual((effective["retention_days"], effective["trash_handoff_days"],
                          effective["trash_handoff_gb"]), (7, 7, 20))

    def test_a_chosen_value_survives_a_file_with_or_without_a_version(self) -> None:
        for version in (None, 2, 3):
            with self.subTest(version=version):
                value = {"max_bytes": 60_000_000_000, "retention_days": 14, "trash_handoff_days": 30,
                         "trash_handoff_gb": 5, "minimum_free_bytes": 2_000_000_000}
                if version is not None:
                    value["defaults_version"] = version
                self.write(value)
                effective = standalone_config.load_device_settings()["effective"]
                self.assertEqual((effective["max_bytes"], effective["retention_days"],
                                  effective["trash_handoff_days"], effective["trash_handoff_gb"]),
                                 (60_000_000_000, 14, 30, 5))

    def test_a_file_already_at_the_current_version_is_not_migrated(self) -> None:
        self.write({"max_bytes": 20_000_000_000, "retention_days": 3, "defaults_version": 3})
        self.assertEqual(standalone_config.load_device_settings()["effective"]["retention_days"], 3)

    def test_the_pre_06117_defaults_still_migrate(self) -> None:
        self.write({"max_bytes": 50_000_000_000, "retention_days": 30, "minimum_free_bytes": 1_000_000_000})
        effective = standalone_config.load_device_settings()["effective"]
        self.assertEqual((effective["max_bytes"], effective["retention_days"],
                          effective["minimum_free_bytes"]), (20_000_000_000, 7, 5_000_000_000))

    def test_saving_a_value_stamps_the_version_so_it_is_never_migrated_again(self) -> None:
        standalone_config.save_device_settings(max_bytes=20_000_000_000, retention_days=3)
        self.assertEqual(json.loads((self.root / "device_settings.json").read_text())["defaults_version"],
                         standalone_config._DEFAULTS_VERSION)
        self.assertEqual(standalone_config.load_device_settings()["effective"]["retention_days"], 3)


class RouteTests(unittest.TestCase):
    """The app's two routes and their fleet allowlist, over a patched data directory."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(os.path.realpath(temporary.name))
        self.data = self.root / "Node State"
        self.data.mkdir()
        workers = self.root / "workers.json"
        workers.write_text(json.dumps([{
            "id": "image", "title": "Image Studio", "modality": "image",
            "host": "127.0.0.1", "port": 51101, "machine": "node-a",
            "local": True, "runtime_identity": {"id": "image-runtime"},
            "hub_token": "test-token", "managed_output_roots": [str(self.root / "out")],
        }]))
        os.chmod(workers, 0o600)
        (self.root / "out").mkdir()
        (self.data / ".hub_token").write_text("test-token\n")
        os.chmod(self.data / ".hub_token", 0o600)
        env = mock.patch.dict(os.environ, {
            "STUDIOHUB_DATA_DIR": str(self.data),
            "STUDIOHUB_WORKERS_FILE": str(workers),
            "STUDIOHUB_PACKAGED": "1",
        })
        env.start()
        self.addCleanup(env.stop)
        from contextlib import ExitStack
        from components.hub.backend import monitor as monitor_module, registry
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for module, name, value in (
            (registry, "DATA_DIR", self.data),
            (registry, "REGISTRY_FILE", self.data / "studios.json"),
            (registry, "LABELS_FILE", self.data / "machine_labels.json"),
            (registry, "FLAGS_FILE", self.data / "machine_flags.json"),
            (monitor_module, "DATA_DIR", self.data),
            (auth, "TOKEN_FILE", self.data / ".hub_token"),
            (auth, "PASSWORD_FILE", self.data / ".hub_password.json"),
            (auth, "SESSIONS_FILE", self.data / ".hub_sessions.json"),
            (ledger, "DATA_DIR", self.data),
            (ledger, "DB_FILE", self.data / "hub.db"),
            (ledger, "PINOKIO_HOME", self.data),
        ):
            self.stack.enter_context(mock.patch.object(module, name, value))
        registry._labels_cache = None
        registry._flags_cache = None

    def test_listing_and_move_routes_are_owner_routes_and_fleet_allowlisted(self) -> None:
        from fastapi.testclient import TestClient
        from components.hub.standalone import create_app
        owner = {"Authorization": "Bearer test-token"}
        marker = {"trash_folder": "/tmp/trash", "count": 2, "bytes": 9, "roots": []}
        calls = []

        def move(items):
            calls.append(items)
            return {"ok": True, "moved": ["2026-10-01/x"], "moved_count": 1, "failed": []}

        with TestClient(create_app(start_background=False)) as client:
            with mock.patch.object(output_storage, "trash_overview", return_value=marker), \
                    mock.patch.object(output_storage, "trash_to_macos", side_effect=move):
                self.assertEqual(client.get("/api/hub/trash").status_code, 401)
                listed = client.get("/api/hub/trash", headers=owner)
                moved = client.post("/api/hub/trash/move-to-macos-trash", json={}, headers=owner)
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(listed.json(), marker)
        self.assertEqual(moved.status_code, 200, moved.text)
        self.assertEqual(calls, ["all"])
        self.assertTrue(auth.fleet_path_allowed("GET", "/api/hub/trash"))
        self.assertTrue(auth.fleet_path_allowed("POST", "/api/hub/trash/move-to-macos-trash"))
        self.assertFalse(auth.fleet_path_allowed("POST", "/api/hub/trash/delete"))


class GovernorRetirementTests(Fixture):
    """The cleanup cycle's own retirements: the rotated log, and old model-download run folders."""

    def context(self, **overrides):
        from components.hub import disk_governor
        values = {"data_dir": self.data, "base_dir": self.root}
        values.update(overrides)
        return disk_governor.Context(**values)

    def test_the_rotated_service_log_is_retired_not_deleted(self) -> None:
        from components.hub import disk_governor
        rotated = self.root / "hub-worker.log.1"
        rotated.write_bytes(b"x" * 10)
        live = self.root / "hub-worker.log"
        live.write_bytes(b"y" * 10)
        with mock.patch.object(disk_governor.output_storage, "retire", return_value=True) as retire:
            disk_governor._logs(self.context(), disk_governor._Run(), False)
        retire.assert_called_once_with(rotated, "logs")
        self.assertTrue(live.exists())

    def test_a_download_run_folder_past_retention_is_retired_and_a_fresh_one_is_kept(self) -> None:
        from components.hub import disk_governor
        runs = self.data / "model-download-runs"
        old, fresh = runs / "old", runs / "fresh"
        for folder in (old, fresh):
            folder.mkdir(parents=True)
            (folder / "output.log").write_text("log\n")
        now = time.time()
        stamp = now - 4 * DAY
        os.utime(old, (stamp, stamp))
        os.utime(old / "output.log", (stamp, stamp))
        with mock.patch.object(disk_governor.output_storage, "retire", return_value=True) as retire:
            disk_governor._download_runs(self.context(settings={"retention_days": 3}), now,
                                         disk_governor._Run())
        retire.assert_called_once_with(old, "model-download-logs")
        self.assertTrue(fresh.exists())


class SubtitleInputRetirementTests(unittest.TestCase):
    """An expired job's stored recording goes to the Trash Folder instead of living for ever."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(os.path.realpath(temporary.name))
        from components.subtitle.app.backend import manager as subtitle_manager
        self.manager = subtitle_manager
        self.subject = subtitle_manager.SubtitleManager.__new__(subtitle_manager.SubtitleManager)
        self.subject.inputs = self.root / "inputs"
        self.subject.inputs.mkdir()
        self.subject.jobs = {}
        self.subject.lock = __import__("threading").RLock()

    def put(self, name: str, age_days: float) -> Path:
        folder = self.subject.inputs / name
        folder.mkdir()
        (folder / "original.wav").write_bytes(b"x" * 10)
        stamp = time.time() - age_days * DAY
        os.utime(folder, (stamp, stamp))
        return folder

    def test_an_expired_recording_is_retired_and_a_record_keeps_its_input(self) -> None:
        window = self.manager.INPUT_RETENTION_DAYS
        self.assertEqual(window, 7, "a subtitle input follows the shared 7-day output retention")
        expired = self.put("upload-expired", window + 1)
        kept_by_record = self.put("upload-record", window + 1)
        young = self.put("upload-young", window - 1)
        self.subject.jobs["j1"] = {"id": "j1", "state": "done",
                                   "input": {"original_path": str(kept_by_record / "original.wav")}}
        retired = []
        with mock.patch.object(self.manager, "retire_input", side_effect=lambda path: retired.append(path) or True):
            count = self.subject._retire_expired_inputs()
        self.assertEqual(count, 1)
        self.assertEqual(retired, [expired])
        self.assertTrue(kept_by_record.exists())
        self.assertTrue(young.exists())

    def test_the_sweep_runs_at_most_once_a_day(self) -> None:
        self.put("upload-expired", 100)
        retired = []
        with mock.patch.object(self.manager, "retire_input", side_effect=lambda path: retired.append(path) or True):
            first = self.subject._retire_expired_inputs_daily(now=time.time())
            second = self.subject._retire_expired_inputs_daily(now=time.time() + 60)
        self.assertEqual((first, second), (1, 0))
        self.assertEqual(len(retired), 1)


class TranscriptionClearTests(Fixture):
    """Clearing a transcription batch moves its files to the Trash Folder; a failed move clears nothing."""

    def setUp(self) -> None:
        super().setUp()
        from components.hub.backend import transcription_jobs
        self.jobs = transcription_jobs
        self.stack = mock.patch.multiple(
            transcription_jobs, ROOT=self.root / "transcription_jobs",
            batches={}, _item_tasks={}, _conn=lambda: _DummyConnection())
        self.stack.start()
        self.addCleanup(self.stack.stop)
        self.batch_dir = transcription_jobs.ROOT / "batch-1"
        self.batch_dir.mkdir(parents=True)
        (self.batch_dir / "input.wav").write_bytes(b"x" * 20)
        self.batch = {"id": "batch-1", "items": [{"index": 0, "state": "done"}]}
        transcription_jobs.batches["batch-1"] = self.batch

    def test_a_terminal_batch_is_moved_to_the_trash_folder_and_its_record_goes(self) -> None:
        with mock.patch.object(output_storage, "retire", return_value=True) as retire:
            reclaimed = self.jobs._remove_terminal_batch(self.batch)
        retire.assert_called_once_with(self.batch_dir, "transcription-batch")
        self.assertEqual(reclaimed, 20)
        self.assertNotIn("batch-1", self.jobs.batches)

    def test_a_failed_move_keeps_the_batch_and_its_files(self) -> None:
        with mock.patch.object(output_storage, "retire", return_value=False):
            self.assertIsNone(self.jobs._remove_terminal_batch(self.batch))
        self.assertIn("batch-1", self.jobs.batches)
        self.assertTrue(self.batch_dir.exists())


class FailedWriteRetirementTests(Fixture):
    """A policy write that fails retires its temporary file into the one Trash Folder."""

    def test_a_failed_settings_write_is_retired_into_the_unified_trash(self) -> None:
        from components.hub.backend import registry
        flags = self.root / "machine_flags.json"
        real_replace = Path.replace

        def refuse(source, destination, *args, **kwargs):
            if Path(destination) == flags:
                raise OSError("fixture: the policy file is not writable")
            return real_replace(source, destination, *args, **kwargs)

        with mock.patch.object(registry, "FLAGS_FILE", flags), \
                mock.patch.object(registry, "_flags_cache", None), \
                mock.patch.object(Path, "replace", refuse):
            with self.assertRaises(OSError):
                registry.set_priority_order("m", ["chat"])
        self.assertEqual(list(self.root.glob(".machine_flags.json-*")), [],
                         "the failed write was left beside the live file")
        retired = list(self.trash.rglob(".machine_flags.json-*"))
        self.assertEqual(len(retired), 1, "the failed write was not retired into the Trash Folder")
        self.assertIn("failed-write-", str(retired[0].parent.name))


class SharedVoiceRetirementTests(Fixture):
    """Removing a shared voice moves its folder to the Trash Folder; the tombstone is still written."""

    def test_a_removed_shared_voice_is_moved_not_deleted(self) -> None:
        from components.hub.backend import shared_voices
        voice_id = "0123456789ab"
        with mock.patch.object(shared_voices, "ROOT", self.root / "shared_voices"):
            directory = shared_voices.ROOT / voice_id
            directory.mkdir(parents=True)
            (directory / "metadata.json").write_text(
                json.dumps({"id": voice_id, "name": "Todd", "audio_sha256": "a" * 64}))
            (directory / "reference.wav").write_bytes(b"RIFF")
            shared_voices.prepare_delete(voice_id)
            self.assertFalse(directory.exists())
            self.assertTrue((shared_voices.ROOT / ".deleted" / f"{voice_id}.json").is_file(),
                            "the fleet tombstone must still be written")
        moved = list(self.trash.rglob(f"{voice_id}/reference.wav"))
        self.assertEqual(len(moved), 1, "the removed voice was deleted, not moved to the Trash Folder")


class _DummyConnection:
    def execute(self, *_args, **_kwargs):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


if __name__ == "__main__":
    unittest.main()
