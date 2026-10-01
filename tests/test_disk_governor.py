"""The disk governor: tiers, the order of reclamation, and what it must never touch.

Everything runs in temp folders against a fake disk (`disk_governor.disk_usage` is replaced by a
function that reads how many bytes the temp tree holds), and a fake `private_size` stands in for
APFS's "blocks this file does not share". No real data and no real volume is touched.

The disk here is 1000 bytes: with `STOP_BYTES` patched to 50, "stop" is < 50 free, "red" < 60 (6%) and
"amber" < 120 (12%).
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from components.hub import disk_governor as dg  # noqa: E402
from components.hub import output_storage  # noqa: E402
from components.hub.backend import ledger  # noqa: E402

DAY = 86400
_REAL_SYSTEM_WARNINGS = dg.system_settings_warnings
CLONE = "previous-app-"          # fake APFS: anything under a previous-app folder shares all its blocks


def day_name(offset: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() - offset * DAY))


class Fixture(unittest.TestCase):
    TOTAL = 1000

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(os.path.realpath(self.temp.name))
        self.base = self.root / "Support"
        self.hub = self.base / "hub"
        self.library = self.root / "models"
        self.out = self.root / "out"
        self.apps = self.root / "Applications"
        for folder in (self.hub, self.library / ".studiohub", self.out, self.apps / "Studio Hub.app" / "Contents"):
            folder.mkdir(parents=True)
        (self.library / ".studiohub" / "installed.json").write_text("{}")
        self.other = 0                                   # bytes used by "everything else on the disk"
        self.stack = mock.patch.multiple(
            dg, disk_usage=self.fake_usage, STOP_BYTES=50,
            _fs_private_size=self.fake_private)
        self.stack.start()
        self.addCleanup(self.stack.stop)
        for module, name, value in ((ledger, "DATA_DIR", self.hub), (ledger, "DB_FILE", self.hub / "hub.db")):
            patcher = mock.patch.object(module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        dg._SIZE_CACHE.clear()
        dg._STATE.update(last_sweep=None, warnings=[], measured={}, measured_at=0.0, history_at=0.0, system_warnings=[])
        patcher = mock.patch.object(dg, "system_settings_warnings", return_value=[])   # never ask the real Mac
        patcher.start()
        self.addCleanup(patcher.stop)
        output_storage._ADMISSION_SNAPSHOT.clear()

    # the fake disk -----------------------------------------------------------
    def used(self) -> int:
        total = 0
        for base, _dirs, names in os.walk(self.root):
            for name in names:
                path = os.path.join(base, name)
                if not os.path.islink(path) and name != "hub.db":
                    total += os.lstat(path).st_size
        return total

    def fake_usage(self, _path):
        used = self.used() + self.other
        return SimpleNamespace(total=self.TOTAL, used=used, free=self.TOTAL - used)

    @staticmethod
    def fake_private(path: str):
        return 0 if CLONE in path else os.lstat(path).st_size

    def set_free(self, free: int) -> None:
        """Make the disk have `free` bytes free right now, whatever the temp tree holds."""
        self.other = 0
        self.other = self.TOTAL - self.used() - free

    # builders ----------------------------------------------------------------
    def put(self, path: Path, size: int, age: float = 0.0) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)
        stamp = time.time() - age
        os.utime(path, (stamp, stamp))
        return path

    @staticmethod
    def age_tree(path: Path, age: float) -> Path:
        """Back-date a folder and everything in it (a download's activity is its newest file or folder)."""
        stamp = time.time() - age
        for base, dirs, names in os.walk(path):
            for name in dirs + names:
                os.utime(os.path.join(base, name), (stamp, stamp), follow_symlinks=False)
        os.utime(path, (stamp, stamp))
        return path

    def trash(self, offset: int, name: str, size: int, inner: str = "f", age: float = 0.0) -> Path:
        entry = self.hub / "trash" / day_name(offset) / name
        self.put(entry / inner, size, age)
        return entry

    def ctx(self, **overrides) -> dg.Context:
        values = dict(data_dir=self.hub, base_dir=self.base, library=self.library, manifests=[],
                      app_root=self.apps / "Studio Hub.app", machines=["m"], output_roots=[self.out],
                      settings={"retention_days": 3, "cleanup_enabled": True})
        values.update(overrides)
        return dg.Context(**values)

    def add_output(self, name: str, size: int, *, age_days: float, acked: bool, batch: str | None = None) -> Path:
        path = self.out / name
        data = b"o" * size
        path.write_bytes(data)
        batch = batch or name
        self.assertTrue(output_storage.register_finalized_output(
            machine="m", studio="image", job_id=name, managed_output_roots=[str(self.out)],
            output_path=str(path), generated_at=time.time() - age_days * DAY, batch_id=batch, item_index=0,
            bytes=size, sha256=hashlib.sha256(data).hexdigest()))
        if acked:
            output_storage.mark_receipt_acked(batch_id=batch, item_index=0)
        return path


class TierTests(unittest.TestCase):
    def test_tiers_follow_the_owners_thresholds(self):
        total = 256 * dg.GB
        cases = [(40 * dg.GB, "green"), (0.12 * total + 1, "green"), (0.12 * total - 1, "amber"),
                 (0.06 * total + 1, "amber"), (0.06 * total - 1, "red"), (5 * dg.GB + 1, "red"),
                 (5 * dg.GB - 1, "stop"), (0, "stop")]
        for free, expected in cases:
            self.assertEqual(dg.tier(int(free), total), expected, free / dg.GB)

    def test_trash_cap_is_the_smaller_of_15_gb_and_6_percent_of_the_disk(self):
        self.assertEqual(dg.trash_cap_bytes(256 * dg.GB), 15 * dg.GB)
        self.assertEqual(dg.trash_cap_bytes(100 * dg.GB), 6 * dg.GB)
        self.assertEqual(dg.trash_cap_bytes(2000 * dg.GB), 15 * dg.GB)


class TrashTests(Fixture):
    def test_the_cap_counts_physical_bytes_so_a_clone_is_not_a_reason_to_delete(self):
        old = self.trash(5, "batch-old", 40)
        self.trash(4, f"{CLONE}aaaa", 400)                          # 400 logical bytes, all shared with the app
        self.trash(1, "batch-new", 30)
        self.set_free(500)                                          # green
        # logical total 470 > cap 60, physical total 70 > 60: only the oldest real file has to go
        result = dg.sweep(self.ctx())
        self.assertFalse(old.exists(), "the oldest real entry should go first")
        self.assertTrue((self.hub / "trash" / day_name(4) / f"{CLONE}aaaa").exists(),
                        "a clone with no private bytes was deleted for a size it does not cost")
        self.assertTrue((self.hub / "trash" / day_name(1) / "batch-new").exists())
        self.assertEqual([a["step"] for a in result["actions"]], ["trash"])

    def test_trash_bytes_reports_physical_not_logical_size(self):
        self.trash(1, f"{CLONE}bbbb", 400)
        self.trash(1, "batch", 25)
        output_storage._TRASH_SIZE = (0.0, 0)
        dg._SIZE_CACHE.clear()
        with mock.patch.object(ledger, "DATA_DIR", self.hub):
            self.assertEqual(output_storage.trash_bytes(), 25)

    def test_oldest_first_down_to_the_cap(self):
        a = self.trash(6, "a", 30)
        b = self.trash(5, "b", 30)
        c = self.trash(4, "c", 30)                                  # 90 > 60: only the oldest goes
        self.set_free(500)
        dg.sweep(self.ctx())
        self.assertFalse(a.exists())
        self.assertTrue(b.exists() and c.exists())

    def test_exactly_one_previous_app_is_kept_and_release_downloads_go_at_once(self):
        self.set_free(500)
        for offset, name in ((6, "previous-app-1"), (4, "previous-app-2"), (2, "previous-app-3")):
            self.trash(offset, name, 5)
        download = self.trash(0, "release-download-9", 5)           # today's: no waiting for 15 days
        result = dg.sweep(self.ctx())
        left = sorted(p.name for p in (self.hub / "trash").rglob("previous-app-*"))
        self.assertEqual(left, ["previous-app-3"])
        self.assertFalse(download.exists())
        steps = {a["step"]: a["count"] for a in result["actions"]}
        self.assertEqual(steps["previous apps"], 2)
        self.assertEqual(steps["release downloads"], 1)

    def test_a_trash_day_older_than_15_days_is_purged(self):
        self.set_free(500)
        old = self.trash(16, "x", 1)
        keep = self.trash(14, "y", 1)
        dg.sweep(self.ctx())
        self.assertFalse(old.exists())
        self.assertTrue(keep.exists())


class AppLeftoverTests(Fixture):
    def test_orphan_old_and_staged_copies_go_after_an_hour_and_only_while_the_app_is_whole(self):
        self.set_free(500)
        stale_old = self.put(self.apps / ".studiohub-old-1111" / "Studio Hub.app" / "f", 9, age=2 * 3600).parent.parent
        stale_staged = self.put(self.apps / ".studiohub-staged-2222" / "Studio Hub.app" / "f", 9, age=2 * 3600).parent.parent
        os.utime(stale_old, (time.time() - 7200,) * 2)
        os.utime(stale_staged, (time.time() - 7200,) * 2)
        fresh = self.put(self.apps / ".studiohub-staged-3333" / "f", 9)            # an update in progress
        dg.sweep(self.ctx())
        self.assertFalse(stale_old.exists() or stale_staged.exists())
        self.assertTrue(fresh.exists(), "an update that is running right now was deleted")

    def test_an_old_copy_is_kept_when_the_installed_app_is_not_there(self):
        self.set_free(500)
        parent = self.root / "Broken"
        old = self.put(parent / ".studiohub-old-4444" / "f", 9, age=2 * 3600).parent
        os.utime(old, (time.time() - 7200,) * 2)
        dg.sweep(self.ctx(app_root=parent / "Studio Hub.app"))       # no Contents/: .studiohub-old may be the only app
        self.assertTrue(old.exists())

    def test_updates_keep_only_the_newest_and_that_goes_too_when_red_and_idle(self):
        older = self.put(self.base / "Updates" / "k1" / "a.dmg", 5)
        newest = self.put(self.base / "Updates" / "k2" / "a.dmg", 5)
        self.age_tree(older.parent, 5 * DAY)
        self.age_tree(newest.parent, 3 * 3600)
        self.set_free(500)
        dg.sweep(self.ctx())
        self.assertFalse(older.exists())
        self.assertTrue(newest.exists(), "a download that may be resumed was deleted while the disk was fine")
        self.set_free(45)                                            # stop tier
        dg.sweep(self.ctx())
        self.assertFalse(newest.exists())


class TempFileTests(Fixture):
    def test_partial_files_nobody_is_writing_are_removed_and_finals_are_not(self):
        self.set_free(500)
        stale = self.put(self.out / ".job1.abcd.partial.wav", 7, age=7 * 3600)
        running = self.put(self.out / ".job2.abcd.partial.wav", 7, age=60)
        final = self.put(self.out / "job3.wav", 7, age=7 * 3600)
        upload = self.put(self.base / "subtitle" / "inputs" / ".upload.part", 7, age=7 * 3600)
        result = dg.sweep(self.ctx())
        self.assertFalse(stale.exists() or upload.exists())
        self.assertTrue(running.exists(), "a partial file a job is writing now was deleted")
        self.assertTrue(final.exists(), "a finished output was treated as a partial file")
        self.assertEqual(result["actions"][0]["step"], "temp files")


class OrderTests(Fixture):
    def test_amber_stops_as_soon_as_free_space_is_back_to_green(self):
        log1 = self.put(self.base / "hub-worker.log.1", 30)
        live = self.put(self.base / "hub-worker.log", 30)
        trash = self.trash(2, "t", 20)
        self.set_free(100)                                          # amber (< 120)
        result = dg.sweep(self.ctx())
        self.assertFalse(log1.exists(), "step 2 (logs) did not run first")
        self.assertTrue(live.exists() and live.stat().st_size == 30, "amber emptied the live log")
        self.assertTrue(trash.exists(), "the sweep went on after the disk was green again")
        self.assertEqual(result["tier_before"], "amber")
        self.assertEqual(result["tier"], "green")
        self.assertEqual([a["step"] for a in result["actions"]], ["logs"])

    def test_the_whole_order_and_what_is_never_touched(self):
        self.put(self.base / "hub-worker.log.1", 10)
        self.put(self.base / "hub-worker.log", 10)
        self.age_tree(self.put(self.base / "Updates" / "k1" / "a.dmg", 10).parent, 2 * 3600)
        self.trash(3, "t-old", 20)
        self.trash(1, "t-new", 20)
        installed = {"pkg-a": {"revision": "a" * 40, "folder": f"pkg-a/{'a' * 40}"}}
        self.put(self.library / "pkg-a" / ("a" * 40) / "model" / "w.bin", 100)       # an installed model
        (self.library / ".studiohub" / "installed.json").write_text(__import__("json").dumps(installed))
        self.put(self.library / "_trash" / "x" / "f", 50)                            # the owner's trash
        orphan_view = self.put(self.base / "snapshot-views" / f"gone-{'b' * 16}" / "w", 10).parent
        live_view = self.put(self.base / "snapshot-views" / f"pkg-a-{'a' * 16}" / "w", 5).parent
        staging = self.age_tree(self.put(self.library / ".studiohub" / "staging" / "x-1" / "p.part", 10).parent, 2 * 3600)
        mirror = self.put(self.library / "hub" / "models--org--gone" / "blobs" / "b", 10).parent.parent
        manifests = [{"package_id": "gone", "model_id": "org/gone", "revision": "b" * 40}]
        acked = self.add_output("acked.png", 10, age_days=1, acked=True)             # younger than 3 days
        unacked = self.add_output("unacked.png", 10, age_days=1, acked=False)
        leased = self.add_output("leased.png", 10, age_days=1, acked=True, batch="leased-batch")
        release = output_storage.acquire_reader(batch_id="leased-batch", item_index=0)
        self.addCleanup(release)
        # Everything removable is 110 bytes. Start at 5 free (stop tier); after the last step free is 115,
        # still amber (< 120), so the disk never turns green early and no step is skipped.
        removable = 10 + 10 + 10 + 20 + 20 + 10 + 10 + 10 + 10
        self.set_free(5)
        self.assertLess(5 + removable, 120, "fixture error: the disk would go green before the last step")
        result = dg.sweep(self.ctx(manifests=manifests))

        order = [a["step"] for a in result["actions"]]
        self.assertEqual(order, ["logs", "stale updates", "trash", "orphan snapshot views",
                                 "partial model downloads", "orphan model mirror", "acked outputs"])
        self.assertTrue(live_view.exists(), "a snapshot view of an installed model was removed")
        self.assertFalse(orphan_view.exists() or staging.exists() or mirror.exists() or acked.exists())
        self.assertTrue((self.library / "pkg-a" / ("a" * 40) / "model" / "w.bin").exists(), "an installed model was touched")
        self.assertTrue((self.library / "_trash" / "x" / "f").exists(), "a _trash folder was touched")
        self.assertTrue(unacked.exists(), "an unacknowledged output was reclaimed")
        self.assertTrue(leased.exists(), "a leased output was reclaimed")
        self.assertEqual(result["tier"], "amber")
        self.assertTrue(any("could not bring" in w for w in dg.snapshot(self.hub)["warnings"]))

    def test_acked_outputs_younger_than_the_retention_window_only_at_red(self):
        young = self.add_output("young.png", 40, age_days=1, acked=True)
        old = self.add_output("old.png", 40, age_days=5, acked=True)
        self.set_free(100)                                         # amber
        dg.sweep(self.ctx(), now=time.time())
        self.assertFalse(old.exists(), "an acked output past retention is the amber step")
        self.assertTrue(young.exists(), "amber took an output younger than the retention window")
        self.set_free(30)                                           # stop tier
        dg.sweep(self.ctx())
        self.assertFalse(young.exists(), "red did not go younger than the retention window")

    def test_amber_leaves_acked_outputs_alone_when_the_operator_turned_cleanup_off(self):
        old = self.add_output("old.png", 40, age_days=5, acked=True)
        self.set_free(100)
        dg.sweep(self.ctx(settings={"retention_days": 3, "cleanup_enabled": False}))
        self.assertTrue(old.exists())


class OrphanTests(Fixture):
    def test_orphans_are_judged_only_with_a_readable_library(self):
        view = self.put(self.base / "snapshot-views" / f"gone-{'c' * 16}" / "w", 10).parent
        (self.library / ".studiohub" / "installed.json").write_text("not json")
        self.set_free(100)
        dg.sweep(self.ctx())
        self.assertTrue(view.exists(), "an unreadable receipt file made every view an orphan")
        dg.sweep(self.ctx(library=self.root / "disconnected-ssd"))
        self.assertTrue(view.exists(), "a missing library was read as 'no model is installed'")

    def test_a_running_download_keeps_its_staging_and_a_hard_linked_mirror_is_not_an_orphan(self):
        staging = self.age_tree(self.put(self.library / ".studiohub" / "staging" / "pkg-1" / "p.part", 10).parent, 3 * DAY)
        linked_blob = self.put(self.library / "pkg-z" / "w.bin", 10)
        mirror = self.library / "hub" / "models--org--shared" / "snapshots" / "r"
        mirror.mkdir(parents=True)
        os.link(linked_blob, mirror / "w.bin")
        unknown = self.put(self.library / "hub" / "models--someone--else" / "blobs" / "b", 10).parent.parent
        manifests = [{"package_id": "shared", "model_id": "org/shared", "revision": "d" * 40}]
        self.set_free(100)
        dg.sweep(self.ctx(manifests=manifests, running_staging=lambda: {str(staging)}))
        self.assertTrue(staging.exists(), "a download in progress lost its staging")
        self.assertTrue(mirror.parent.parent.exists(), "a mirror hard-linked to the library was deleted")
        self.assertTrue(unknown.exists(), "a repo the catalog does not know was deleted")

    def test_startup_sweeps_orphans_even_on_a_green_disk(self):
        view = self.put(self.base / "snapshot-views" / f"gone-{'e' * 16}" / "w", 10).parent
        self.set_free(500)
        dg.sweep(self.ctx())                                       # an ordinary pass leaves derived data alone
        self.assertTrue(view.exists())
        result = dg.sweep(self.ctx(), startup=True)
        self.assertFalse(view.exists())
        self.assertIn("orphan snapshot views", [a["step"] for a in result["actions"]])


class HistoryTests(Fixture):
    def test_old_finished_batches_are_compacted_and_live_ones_stay(self):
        now = time.time()
        with ledger._conn() as conn:
            for batch_id, age, finished in (("old-done", 100, 1), ("new-done", 10, 1), ("old-live", 100, 0)):
                conn.execute("INSERT INTO batches (id, created_at, finished, payload) VALUES (?,?,?,?)",
                             (batch_id, now - age * DAY, finished, "{}" + " " * 4000))
            conn.execute("INSERT INTO batch_requests VALUES ('r1','f','old-done',1,?)", (now - 100 * DAY,))
        result = ledger.compact_history(now=now, days=90)
        self.assertEqual(result["batches"], 1)
        with ledger._conn() as conn:
            ids = {row[0] for row in conn.execute("SELECT id FROM batches")}
            requests = conn.execute("SELECT COUNT(*) FROM batch_requests").fetchone()[0]
        self.assertEqual(ids, {"new-done", "old-live"})
        self.assertEqual(requests, 0)

    def test_the_file_is_vacuumed_when_a_quarter_of_it_is_free_pages(self):
        now = time.time()
        with ledger._conn() as conn:
            for index in range(60):
                conn.execute("INSERT INTO batches (id, created_at, finished, payload) VALUES (?,?,1,?)",
                             (f"b{index}", now - 200 * DAY, "x" * 8000))
        before = (self.hub / "hub.db").stat().st_size
        result = ledger.compact_history(now=now, days=90)
        self.assertTrue(result["vacuumed"])
        self.assertLess((self.hub / "hub.db").stat().st_size, before)

    def test_the_governor_compacts_once_a_day_and_at_startup(self):
        self.set_free(500)
        calls = []
        with mock.patch.object(ledger, "compact_history",
                               side_effect=lambda **kw: calls.append(kw) or {"batches": 0, "output_groups": 0, "vacuumed": False}):
            dg.sweep(self.ctx(), startup=True)
            dg.sweep(self.ctx())
            dg.sweep(self.ctx(), now=time.time() + dg.HISTORY_EVERY_S + 1)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["days"], 90)


class SnapshotTests(Fixture):
    def test_the_disk_object_names_state_trash_consumers_swap_and_last_sweep(self):
        self.put(self.base / "hub-worker.log", 15)
        self.trash(1, "t", 25)
        self.add_output("o.png", 35, age_days=0, acked=False)
        self.set_free(100)                                          # amber
        with mock.patch.object(dg, "_swap_used_gb", return_value=3.5):
            dg.sweep(self.ctx(), startup=True)
            disk = dg.snapshot(self.hub)
        self.assertEqual(disk["state"], "green")                    # the sweep gave the Trash Folder back
        for key in ("state", "free_gb", "total_gb", "trash_gb", "consumers", "top", "swap_used_gb",
                    "last_sweep", "warnings"):
            self.assertIn(key, disk)
        self.assertEqual(disk["swap_used_gb"], 3.5)
        self.assertEqual(set(disk["consumers"]), {"outputs_gb", "trash_gb", "logs_gb", "apps_gb", "models_gb", "other_gb"})
        self.assertEqual({item["name"] for item in disk["top"]}, {"outputs", "trash", "logs", "apps", "models", "other"})
        self.assertEqual([item["gb"] for item in disk["top"]], sorted((i["gb"] for i in disk["top"]), reverse=True))
        self.assertEqual(set(disk["last_sweep"]), {"at", "reclaimed_gb", "actions"})

    def test_state_and_warning_follow_the_tier(self):
        for free, state in ((500, "green"), (100, "amber"), (55, "red"), (30, "stop")):
            self.set_free(free)
            disk = dg.snapshot(self.hub)
            self.assertEqual(disk["state"], state, free)
            self.assertEqual(bool(disk["warnings"]), state != "green", free)

    def test_trash_gb_is_the_physical_size(self):
        self.trash(1, f"{CLONE}z", 500)
        self.trash(1, "real", 20)
        self.set_free(500)
        dg.sweep(self.ctx())
        self.assertEqual(dg._STATE["measured"]["trash"], 20)

    def test_host_stats_carries_the_disk_object(self):
        from components.hub.backend import resources
        with mock.patch.object(resources, "DATA_DIR", self.hub):
            stats = resources.host_stats()
        self.assertIn("state", stats["disk"])
        self.assertEqual(stats["disk"]["total_gb"], round(self.TOTAL / dg.GB, 2))


class SpoolTierTests(Fixture):
    def asks(self):
        calls = []
        return calls, self.ctx(spool_limit=lambda: 80, spool_enforce=lambda target: calls.append(target) or
                               {"reclaimed_bytes": 5, "cleared": 1})

    def test_amber_halves_the_spool_limit_and_red_empties_what_is_finished(self):
        self.set_free(100)                                          # amber
        calls, ctx = self.asks()
        result = dg.sweep(ctx)
        self.assertEqual(calls, [40])
        self.assertIn("transcription spool", [a["step"] for a in result["actions"]])
        self.set_free(30)                                           # stop tier
        calls.clear()
        dg.sweep(ctx)
        self.assertEqual(calls, [0])

    def test_a_green_disk_does_not_touch_the_spool(self):
        self.set_free(500)
        calls, ctx = self.asks()
        dg.sweep(ctx)
        self.assertEqual(calls, [])


class SystemSettingsWarningTests(Fixture):
    def reads(self, answers: dict):
        return mock.patch.object(dg, "_read", side_effect=lambda command: answers.get(command[-1] if command[1] == "read" else command[1]))

    def test_auto_update_download_and_time_machine_are_named_in_the_warnings(self):
        answers = {"AutomaticDownload": "1\n", "destinationinfo": "====\nName : Backup\n",
                   "listlocalsnapshots": "Snapshots for disk /:\ncom.apple.TimeMachine.2026-10-01-000000.local\n"}
        with self.reads(answers):
            warnings = _REAL_SYSTEM_WARNINGS()
        self.assertEqual(len(warnings), 2)
        self.assertIn("download updates automatically", warnings[0])
        self.assertIn("Time Machine", warnings[1])
        self.assertIn("1 local snapshot ", warnings[1])

    def test_a_node_set_up_properly_has_no_system_warnings(self):
        answers = {"AutomaticDownload": "0\n", "destinationinfo": "tmutil: No destinations configured.\n",
                   "listlocalsnapshots": "Snapshots for disk /:\n"}
        with self.reads(answers):
            self.assertEqual(_REAL_SYSTEM_WARNINGS(), [])

    def test_a_setting_that_cannot_be_read_is_not_a_warning(self):
        with mock.patch.object(dg, "_read", return_value=None):
            self.assertEqual(_REAL_SYSTEM_WARNINGS(), [])

    def test_the_warnings_reach_the_disk_object_for_the_gateway(self):
        self.set_free(500)
        with mock.patch.object(dg, "system_settings_warnings", return_value=["Time Machine is on for this Mac"]):
            dg.sweep(self.ctx(), startup=True)
        self.assertIn("Time Machine is on for this Mac", dg.snapshot(self.hub)["warnings"])


class GatewayReaderContractTests(Fixture):
    """The Gateway's `fleet.machine_disk` reads these names and these state words; a rename here
    would make every Mac look like it said nothing."""

    def test_the_keys_and_state_words_the_gateway_reads(self):
        self.set_free(100)
        dg.sweep(self.ctx(), startup=True)
        disk = dg.snapshot(self.hub)
        for key in ("state", "free_gb", "total_gb", "trash_gb", "swap_used_gb", "top", "last_sweep", "warnings"):
            self.assertIn(key, disk)
        self.assertIn(disk["state"], ("green", "amber", "red", "stop"))
        self.assertNotIn("black", {dg.tier(f, 1000) for f in (0, 30, 55, 100, 500)})
        for item in disk["top"]:
            self.assertEqual(set(item), {"name", "gb"})
        self.assertTrue(all(isinstance(w, str) for w in disk["warnings"]))


class BackgroundLoopTests(Fixture):
    def test_the_governor_runs_in_the_cleanup_thread_not_only_at_admission(self):
        """The 5-minute thread used to call `enforce` alone, which never reads free space."""
        ran = threading.Event()
        calls = []

        def after(first):
            calls.append(first)
            if len(calls) >= 2:
                ran.set()

        asked = []

        def settings():
            asked.append(1)                                         # quick first pass, then park the daemon thread
            return {"effective": {"max_bytes": 10**9, "retention_days": 3, "cleanup_enabled": True,
                                  "cleanup_interval_seconds": 0.01 if len(asked) == 1 else 3600}}
        with mock.patch.object(output_storage, "_STARTED", False):
            output_storage.start_background(lambda: {"m"}, settings, after_sweep=after)
            self.assertTrue(ran.wait(5), "the governor was not called by the background loop")
        self.assertEqual(calls[0], True)                           # the start-up pass is first
        self.assertEqual(calls[1], False)


class UnackedTests(Fixture):
    def test_an_unacknowledged_output_moves_to_the_trash_folder_after_7_days(self):
        acked = self.add_output("acked.png", 5, age_days=10, acked=True)
        late = self.add_output("late.png", 5, age_days=8, acked=False)
        recent = self.add_output("recent.png", 5, age_days=6, acked=False)
        leased = self.add_output("leased.png", 5, age_days=9, acked=False, batch="lb")
        release = output_storage.acquire_reader(batch_id="lb", item_index=0)
        self.addCleanup(release)
        result = output_storage.enforce(machine="m", max_bytes=10**9, retention_days=3)
        self.assertFalse(acked.exists())
        self.assertFalse(late.exists(), "an output nobody collected for 8 days stayed in live output")
        self.assertTrue(recent.exists(), "an unacknowledged output was moved before 7 days")
        self.assertTrue(leased.exists(), "a leased output was moved")
        self.assertEqual(len(list((self.hub / "trash").rglob("*-late.png"))), 1, "moved, not deleted")
        self.assertEqual(result["deleted"], 2)

    def test_preview_counts_an_old_unacked_output_as_removable(self):
        self.add_output("late.png", 5, age_days=8, acked=False)
        view = output_storage.preview(machine="m", max_bytes=10**9, retention_days=3)
        self.assertEqual(view["would_delete_bytes"], 5)
        self.assertEqual(view["protected_bytes"], 0)


if __name__ == "__main__":
    unittest.main()
