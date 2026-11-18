"""The delta update: a manifest of the app, a clone plus the changed files, and every way it refuses.

The property under test is one sentence: **the staged app is byte-for-byte what the release's
manifest says, the running app was never touched, and anything that cannot be proved is refused**
(so the caller falls back to the full disk image). The fixtures use the installed app's real shape:
read-only 0444/0555 files and directories, symlinks, executables, a `runtimes/<name>` split.
"""
from __future__ import annotations

import gzip
import hashlib
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from components.hub.backend import file_manifest as fm
from components.hub.backend import release_delta as rd

R = "Contents/Resources/"


def force_remove(path: Path) -> None:
    if path.exists() or path.is_symlink():
        def fix(function, target, _exc):
            os.chmod(os.path.dirname(target), 0o755)
            function(target)
        shutil.rmtree(path, onerror=fix)


def make_app(root: Path, files: dict[str, tuple[bytes, bool]],
             links: dict[str, str] | None = None, *, readonly: bool = True,
             manifest: bool = False) -> Path:
    """An ``.app`` shaped like an installed one: 0444/0555 files, 0555 directories."""
    app = root / "Hub Studio KH.app"
    for rel, (data, executable) in files.items():
        target = app / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        os.chmod(target, (0o555 if executable else 0o444) if readonly else (0o755 if executable else 0o644))
    for rel, destination in (links or {}).items():
        (app / rel).parent.mkdir(parents=True, exist_ok=True)
        os.symlink(destination, app / rel)
    if manifest:
        # The installed app's own shape: the manifest of the release it was built from, inside it.
        fm.write(app, destination=app / fm.DIR_REL)
    if readonly:
        for directory in sorted((p for p in app.rglob("*") if p.is_dir() and not p.is_symlink()),
                                key=lambda p: len(p.parts), reverse=True):
            os.chmod(directory, 0o555)
        os.chmod(app, 0o555)
    return app


def by_sha(app: Path) -> dict[str, Path]:
    return {digest: app / rel for rel, (kind, _x, _s, digest) in fm.scan(app).items() if kind == "f"}


def fetcher_from(app: Path, calls: list | None = None):
    """A blob source backed by the *new* release's tree, as a controller's store would be."""
    table = by_sha(app)

    def get(sha: str, destination: Path, size: int) -> None:
        if calls is not None:
            calls.append(sha)
        shutil.copyfile(table[sha], destination)
    return get


OLD_FILES = {
    R + "release-manifest.json": (b'{"version":"0.6.1"}', False),
    "Contents/Info.plist": (b"<plist>build-old</plist>", False),
    "Contents/MacOS/Hub Studio KH": (b"\xcf\xfa\xed\xfe native shell old", True),
    R + "components/hub/backend/broker.py": (b"# broker v1\n", False),
    R + "components/hub/backend/removed.py": (b"# going away\n", False),
    R + "components/hub/backend/moved_from.py": (b"# moves without changing\n" * 50, False),
    R + "components/hub/backend/mode.py": (b"# becomes executable\n", False),
    R + "runtimes/voice/bin/python3": (b"\x7fELF python", True),
    R + "runtimes/voice/lib/big.so": (os.urandom(4096), False),
    R + "runtimes/image/lib/tcl9/x.tcl": (b"proc x {} {}\n", False),
}
OLD_LINKS = {R + "runtimes/voice/bin/python": "python3"}

NEW_FILES = {
    R + "release-manifest.json": (b'{"version":"0.6.2"}', False),
    "Contents/Info.plist": (b"<plist>build-new</plist>", False),
    "Contents/MacOS/Hub Studio KH": (b"\xcf\xfa\xed\xfe native shell old", True),   # unchanged
    R + "components/hub/backend/broker.py": (b"# broker v2 - changed\n", False),
    R + "components/hub/backend/added.py": (b"# brand new file\n", False),
    R + "components/hub/backend/moved_to.py": (b"# moves without changing\n" * 50, False),
    R + "components/hub/backend/mode.py": (b"# becomes executable\n", True),
    R + "components/hub/backend/deeper/newdir/x.py": (b"# new directory\n", False),
    R + "runtimes/voice/bin/python3": (b"\x7fELF python", True),
    R + "runtimes/voice/lib/big.so": (OLD_FILES[R + "runtimes/voice/lib/big.so"][0], False),
    R + "runtimes/image/lib/tcl9/x.tcl": (b"proc x {} {}\n", False),
}
NEW_LINKS = {R + "runtimes/voice/bin/python": "python3"}


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="studiohub-delta-"))
        self.addCleanup(force_remove, self.tmp)
        # The installed app carries the manifest of the release it was built from.
        self.old = make_app(self.tmp / "old" / "Applications", OLD_FILES, OLD_LINKS, manifest=True)
        self.new = make_app(self.tmp / "new", NEW_FILES, NEW_LINKS, readonly=False)
        self.new_entries = fm.scan(self.new)
        self.new_index, _ = fm.build(self.new_entries)

    def _writable(self, app: Path) -> Path:
        for directory in [app, *(p for p in app.rglob("*") if p.is_dir() and not p.is_symlink())]:
            os.chmod(directory, 0o755)
        return app

    def tree_state(self, app: Path) -> dict:
        state = {}
        for path in sorted(app.rglob("*")):
            info = path.lstat()
            state[path.relative_to(app).as_posix()] = (
                stat.S_IMODE(info.st_mode), info.st_ino,
                None if path.is_dir() and not path.is_symlink() else
                (os.readlink(path) if path.is_symlink() else path.read_bytes()))
        return state

    def stage(self, **overrides):
        args = dict(installed=self.old, new=self.new_entries,
                    files_sha256=self.new_index["files_sha256"],
                    get_blob=fetcher_from(self.new))
        args.update(overrides)
        return rd.stage(args.pop("installed"), args.pop("new"), args.pop("files_sha256"),
                        args.pop("get_blob"), **args)

    def assert_refused_cleanly(self, code: str, **overrides) -> None:
        # The running app must be exactly as it was, and no staging directory may survive.
        before = self.tree_state(self.old)
        with self.assertRaises(rd.DeltaRefused) as caught:
            self.stage(**overrides)
        self.assertEqual(caught.exception.code, code, caught.exception.message)
        self.assertEqual(self.tree_state(self.old), before, "the running app was modified")
        self.assertEqual([p.name for p in self.old.parent.iterdir()], ["Hub Studio KH.app"],
                         "a refused delta left a staging directory behind")


class ManifestTests(Fixture):
    def test_manifest_is_deterministic_across_copies_and_modes(self) -> None:
        # Same content, different modes and mtimes, different location: identical manifest bytes.
        copy = make_app(self.tmp / "copy", NEW_FILES, NEW_LINKS, readonly=True)
        for path in copy.rglob("*"):
            if path.is_file():
                os.utime(path, (1, 1))
        one, tsv_one = fm.build(fm.scan(self.new))
        two, tsv_two = fm.build(fm.scan(copy))
        self.assertEqual(fm.index_bytes(one), fm.index_bytes(two))
        self.assertEqual(tsv_one, tsv_two)
        self.assertEqual({n: fm.gzip_bytes(d) for n, d in tsv_one.items()},
                         {n: fm.gzip_bytes(d) for n, d in tsv_two.items()})

    def test_only_the_executable_bit_is_recorded_and_it_is_recorded(self) -> None:
        readonly = fm.scan(make_app(self.tmp / "ro", NEW_FILES, NEW_LINKS, readonly=True))
        writable = fm.scan(make_app(self.tmp / "rw", NEW_FILES, NEW_LINKS, readonly=False))
        self.assertEqual(readonly, writable, "0444/0555 vs 0644/0755 must not read as a change")
        flipped = dict(NEW_FILES)
        flipped[R + "components/hub/backend/broker.py"] = (b"# broker v2 - changed\n", True)
        other = fm.scan(make_app(self.tmp / "flip", flipped, NEW_LINKS, readonly=False))
        self.assertNotEqual(other, writable, "flipping the executable bit must be a change")

    def test_ds_store_and_the_manifest_directory_are_not_listed(self) -> None:
        app = make_app(self.tmp / "ds", NEW_FILES, NEW_LINKS, readonly=False)
        for name in (".DS_Store", "._broker.py", ".localized", "Icon\r"):
            (app / R / "components" / name).write_bytes(b"junk")
        metadata_dir = app / R / "components/.Spotlight-V100"
        metadata_dir.mkdir()
        (metadata_dir / "index.db").write_bytes(b"metadata")
        fm.write(app)
        entries = fm.scan(app)
        self.assertEqual(entries, fm.scan(self.new))
        self.assertFalse([p for p in entries if ".DS_Store" in p or "file-manifest" in p])

    def test_symlinks_are_recorded_and_never_followed(self) -> None:
        app = make_app(self.tmp / "ln", {R + "real/a.txt": (b"a", False)},
                       {R + "alias": "real"}, readonly=False)
        entries = fm.scan(app)
        self.assertEqual(entries[R + "alias"], ("l", False, 0, "real"))
        self.assertNotIn(R + "alias/a.txt", entries)

    def test_groups_split_core_from_each_runtime(self) -> None:
        index, tsv = fm.build(self.new_entries)
        self.assertEqual(sorted(index["groups"]), ["core", "runtime-image", "runtime-voice"])
        self.assertIn(R + "runtimes/voice/bin/python3", fm.parse(tsv["runtime-voice"]))
        self.assertNotIn("runtimes", "".join(fm.parse(tsv["core"])))
        self.assertEqual(index["files_count"], len(self.new_entries))

    def test_an_unchanged_group_keeps_its_digest_across_releases(self) -> None:
        old_index, _ = fm.build(fm.scan(self.old))
        new_index, _ = fm.build(self.new_entries)
        self.assertEqual(old_index["groups"]["runtime-image"]["sha256"],
                         new_index["groups"]["runtime-image"]["sha256"])
        self.assertNotEqual(old_index["groups"]["core"]["sha256"], new_index["groups"]["core"]["sha256"])
        self.assertNotEqual(old_index["files_sha256"], new_index["files_sha256"])

    def test_the_stored_manifest_round_trips_and_a_tampered_one_is_refused(self) -> None:
        index, groups = fm.load_dir(self.old / fm.DIR_REL)
        self.assertEqual(fm.flatten(groups), fm.scan(self.old))
        # A tampered files_sha256 (what a descriptor signs) no longer matches its groups.
        forged = dict(index, files_sha256="0" * 64)
        with self.assertRaises(fm.ManifestError):
            fm.check_index(forged)
        # A group whose bytes changed no longer matches the sha256 the index names.
        tsv = fm.build(fm.scan(self.old))[1]["core"]
        with self.assertRaises(fm.ManifestError):
            fm.check_group(index, "core", tsv.replace(b"broker", b"brokeR", 1))

    def test_parse_refuses_what_could_escape_or_mislead(self) -> None:
        good = "f\t0\t1\t" + "a" * 64 + "\t"
        for label, body in {
            "parent dir": good + "../evil\n",
            "absolute": good + "/etc/passwd\n",
            "dot segment": good + "a/./b\n",
            "unsorted": good + "b\n" + good + "a\n",
            "duplicate": good + "a\n" + good + "a\n",
            "short sha": "f\t0\t1\tabc\tp\n",
            "bad type": "x\t0\t1\t" + "a" * 64 + "\tp\n",
            "bad exec": "f\t2\t1\t" + "a" * 64 + "\tp\n",
            "five fields": "f\t0\t1\t" + "a" * 64 + "\n",
        }.items():
            with self.subTest(label):
                with self.assertRaises(fm.ManifestError):
                    fm.parse(body.encode())

    def test_gunzip_refuses_a_bomb(self) -> None:
        with self.assertRaises(fm.ManifestError):
            fm.gunzip_limited(gzip.compress(b"0" * 10_000), limit=1000)


class StageTests(Fixture):
    def test_the_staged_app_is_the_manifest_and_the_running_app_is_untouched(self) -> None:
        before = self.tree_state(self.old)
        calls: list[str] = []
        staged, summary = self.stage(get_blob=fetcher_from(self.new, calls))
        self.assertEqual(self.tree_state(self.old), before, "the running app must not change")
        # Tree == manifest, checked independently of the code under test.
        self.assertEqual(fm.scan(staged), self.new_entries)
        self.assertEqual(staged.parent.parent, self.old.parent)
        self.assertEqual(staged.name, self.old.name)
        self.assertEqual(summary["files_removed"], 2)   # removed.py and the moved file's old name
        # Only what changed was fetched; a moved file and an unchanged one cost nothing.
        fetched = {self.new_entries[p][3] for p in (R + "components/hub/backend/broker.py",
                                                      R + "components/hub/backend/added.py",
                                                      R + "components/hub/backend/deeper/newdir/x.py",
                                                      "Contents/Info.plist", R + "release-manifest.json")}
        self.assertEqual(set(calls), fetched)
        self.assertEqual(sorted(p.name for p in staged.parent.iterdir()), [staged.name],
                         "the staging directory holds only the app once it is done")
        # The next update needs no manifest inside the app: it measures the installed bytes.
        # The staged app must carry none - a manifest written post-seal would break the signature.
        self.assertFalse((staged / fm.DIR_REL).exists())

    def test_the_executable_bit_change_and_the_removed_and_new_directories(self) -> None:
        staged, _ = self.stage()
        self.assertTrue(os.access(staged / R / "components/hub/backend/mode.py", os.X_OK))
        self.assertFalse((staged / R / "components/hub/backend/removed.py").exists())
        self.assertTrue((staged / R / "components/hub/backend/deeper/newdir/x.py").is_file())
        self.assertTrue((staged / R / "components/hub/backend/moved_to.py").is_file())
        self.assertFalse((staged / R / "components/hub/backend/moved_from.py").exists())

    def test_directory_modes_are_put_back(self) -> None:
        staged, _ = self.stage()
        backend = staged / R / "components/hub/backend"
        self.assertEqual(stat.S_IMODE(backend.stat().st_mode), 0o555,
                         "a directory made writable to stage must not be left writable")

    def test_a_changed_file_is_replaced_not_rewritten_in_place(self) -> None:
        inode = (self.old / R / "components/hub/backend/broker.py").stat().st_ino
        staged, _ = self.stage()
        self.assertEqual((self.old / R / "components/hub/backend/broker.py").stat().st_ino, inode)
        self.assertEqual((self.old / R / "components/hub/backend/broker.py").read_bytes(), b"# broker v1\n")
        self.assertNotEqual((staged / R / "components/hub/backend/broker.py").stat().st_ino, inode)

    def test_a_stray_file_in_the_installed_app_is_not_carried_into_the_release(self) -> None:
        self._writable(self.old)
        (self.old / R / "components/hub/backend/stray.pyc").write_bytes(b"junk")
        rd._installed_cache.clear()
        staged, _ = self.stage()
        self.assertFalse((staged / R / "components/hub/backend/stray.pyc").exists())
        self.assertEqual(fm.scan(staged), self.new_entries)

    # ── refusals ───────────────────────────────────────────────────────────

    def test_refuses_when_a_blob_is_missing(self) -> None:
        def missing(sha, destination, size):
            raise rd.DeltaRefused("missing_blob", "the controller does not have it")
        self.assert_refused_cleanly("missing_blob", get_blob=missing)

    def test_refuses_a_blob_that_does_not_hash_to_its_name(self) -> None:
        def wrong(sha, destination, size):
            destination.write_bytes(b"x" * size)
        self.assert_refused_cleanly("corrupt", get_blob=wrong)

    def test_refuses_a_truncated_blob(self) -> None:
        good = fetcher_from(self.new)

        def truncated(sha, destination, size):
            good(sha, destination, size)
            destination.write_bytes(destination.read_bytes()[:-1])
        self.assert_refused_cleanly("corrupt", get_blob=truncated)

    def test_refuses_a_path_that_climbs_out_of_the_bundle(self) -> None:
        entries = dict(self.new_entries, **{"../evil": ("f", False, 1, "a" * 64)})
        self.assert_refused_cleanly("unsafe", new=entries)

    def test_refuses_an_absolute_path(self) -> None:
        entries = dict(self.new_entries, **{"/tmp/evil": ("f", False, 1, "a" * 64)})
        self.assert_refused_cleanly("unsafe", new=entries)

    def test_refuses_a_symlink_that_leaves_the_bundle(self) -> None:
        for target in ("../../../../etc", "/etc"):
            with self.subTest(target=target):
                entries = dict(self.new_entries, **{R + "escape": ("l", False, 0, target)})
                self.assert_refused_cleanly("unsafe", new=entries)

    def test_refuses_to_write_through_a_symlink_directory(self) -> None:
        # `alias` is a symlink in the installed app; a manifest that then lists alias/x would
        # write outside the bundle if the parent were followed.
        outside = self.tmp / "outside"
        outside.mkdir()
        self._writable(self.old)
        os.symlink(outside, self.old / R / "alias")
        rd._installed_cache.clear()
        # The plan removes installed entries the release does not list (an unlisted link would
        # simply be unlinked, touching nothing outside), so the guard under test is the one
        # that refuses to write through a parent the release itself keeps as a symlink.
        entries = dict(self.new_entries, **{
            R + "alias": ("l", False, 0, str(outside)),
            R + "alias/x": ("f", False, 1, hashlib.sha256(b"x").hexdigest())})
        table = {hashlib.sha256(b"x").hexdigest(): b"x"}

        good = fetcher_from(self.new)

        def get(sha, destination, size):
            if sha in table:
                destination.write_bytes(table[sha])
            else:
                good(sha, destination, size)
        with self.assertRaises(rd.DeltaRefused) as caught:
            self.stage(new=entries, get_blob=get)
        self.assertEqual(caught.exception.code, "unsafe")
        self.assertEqual(list(outside.iterdir()), [], "nothing may be written outside the bundle")

    def test_refuses_a_tampered_files_sha256(self) -> None:
        self.assert_refused_cleanly("corrupt", files_sha256="f" * 64)

    def test_verify_refuses_an_extra_a_missing_and_a_mis_sized_file(self) -> None:
        staged, _ = self.stage()
        rd.verify_tree(staged, self.new_entries, set())
        self._writable(staged)
        extra = staged / R / "extra.txt"
        extra.write_bytes(b"e")
        with self.assertRaises(rd.DeltaRefused):
            rd.verify_tree(staged, self.new_entries, set())
        extra.unlink()
        (staged / R / "components/hub/backend/added.py").write_bytes(b"short")
        with self.assertRaises(rd.DeltaRefused):
            rd.verify_tree(staged, self.new_entries, set())
        (staged / R / "components/hub/backend/added.py").unlink()
        with self.assertRaises(rd.DeltaRefused):
            rd.verify_tree(staged, self.new_entries, set())

    def test_verify_rehashes_a_changed_file_of_the_right_size(self) -> None:
        staged, _ = self.stage()
        self._writable(staged)
        path = staged / R / "components/hub/backend/added.py"
        data = path.read_bytes()
        path.write_bytes(b"#" * len(data))
        rd.verify_tree(staged, self.new_entries, set())      # same size: stat alone cannot see it
        with self.assertRaises(rd.DeltaRefused):
            rd.verify_tree(staged, self.new_entries, {R + "components/hub/backend/added.py"})

    def test_a_kill_part_way_through_leaves_the_running_app_untouched(self) -> None:
        good = fetcher_from(self.new)
        seen = []

        def killed(sha, destination, size):
            if seen:
                raise KeyboardInterrupt("killed mid-apply")
            seen.append(sha)
            good(sha, destination, size)
        before = self.tree_state(self.old)
        with self.assertRaises(KeyboardInterrupt):
            self.stage(get_blob=killed)
        self.assertEqual(self.tree_state(self.old), before)
        self.assertEqual([p.name for p in self.old.parent.iterdir()], ["Hub Studio KH.app"])

    def test_a_kill_during_the_write_phase_also_leaves_it_untouched(self) -> None:
        before = self.tree_state(self.old)
        with mock.patch.object(rd, "_place", side_effect=KeyboardInterrupt("killed while writing")):
            with self.assertRaises(KeyboardInterrupt):
                self.stage()
        self.assertEqual(self.tree_state(self.old), before)
        self.assertEqual([p.name for p in self.old.parent.iterdir()], ["Hub Studio KH.app"])

    def test_refuses_a_delta_that_is_most_of_a_full_download(self) -> None:
        self.assert_refused_cleanly("over_threshold", artifact_bytes=10)

    # ── not APFS ───────────────────────────────────────────────────────────

    def test_without_apfs_clones_it_copies_when_there_is_room(self) -> None:
        real = subprocess.run

        def no_clone(command, **kwargs):
            if "-c" in command:
                raise subprocess.CalledProcessError(1, command)
            return real(command, **kwargs)
        with mock.patch.object(rd.subprocess, "run", side_effect=no_clone):
            staged, summary = self.stage()
        self.assertEqual(summary["method"], "copy")
        self.assertEqual(fm.scan(staged), self.new_entries)

    def test_without_apfs_and_without_room_it_refuses_instead_of_filling_the_disk(self) -> None:
        real = subprocess.run

        def no_clone(command, **kwargs):
            if "-c" in command:
                raise subprocess.CalledProcessError(1, command)
            return real(command, **kwargs)
        usage = shutil._ntuple_diskusage(10**12, 10**12 - 1000, 1000)
        with mock.patch.object(rd.subprocess, "run", side_effect=no_clone), \
                mock.patch.object(rd.shutil, "disk_usage", return_value=usage):
            self.assert_refused_cleanly("no_space")


class InstalledTests(Fixture):
    def test_the_installed_app_is_measured_from_its_bytes_not_its_embedded_manifest(self) -> None:
        rd._installed_cache.clear()
        # An embedded manifest never stands in for the bytes, however well-formed it is: the
        # measurement runs, and here - where the fixture's records are accurate - agrees with it.
        with mock.patch.object(fm, "scan", wraps=fm.scan) as measured:
            entries = rd.installed_entries(self.old)
        measured.assert_called_once_with(self.old)
        self.assertEqual(entries, fm.scan(self.old))
        self.assertEqual(entries, fm.flatten(fm.load_dir(self.old / fm.DIR_REL)[1]))
        bare = make_app(self.tmp / "bare", OLD_FILES, OLD_LINKS, readonly=False)
        rd._installed_cache.clear()
        self.assertEqual(rd.installed_entries(bare), fm.scan(bare))

    def test_an_app_is_found_from_the_release_manifest_the_hub_was_started_with(self) -> None:
        manifest = self.old / R / "release-manifest.json"
        with mock.patch.dict(os.environ, {"STUDIOHUB_RELEASE_MANIFEST": str(manifest)}):
            self.assertEqual(rd.installed_app_root(), self.old)
        with mock.patch.dict(os.environ, {"STUDIOHUB_RELEASE_MANIFEST": "/repo/release-manifest.json"}):
            self.assertIsNone(rd.installed_app_root())


class RealReleasePairTests(unittest.TestCase):
    """The measured claim: releases 0.6.112 and 0.6.113 differ by six files. Needs both disk images
    (set STUDIOHUB_DELTA_DMGS to a directory holding them); skipped, cleanly, where they are absent."""

    def test_112_to_113_is_six_files(self) -> None:
        roots = [Path(p) for p in (os.environ.get("STUDIOHUB_DELTA_DMGS"),
                                   PROJECT_ROOT / "dist", PROJECT_ROOT / "_trash/dist") if p]
        found = {}
        for root in roots:
            for version in ("0.6.112", "0.6.113"):
                found.setdefault(version, next(iter(sorted(root.glob(f"StudioHub-{version}-*.dmg"))), None)
                                 if root.is_dir() else None)
        if not all(found.get(v) for v in ("0.6.112", "0.6.113")):
            self.skipTest("the 0.6.112 and 0.6.113 disk images are not available here")
        mounts = []
        scratch = Path(tempfile.mkdtemp(prefix="studiohub-real-pair-"))
        self.addCleanup(shutil.rmtree, scratch, True)
        try:
            entries = []
            for version in ("0.6.112", "0.6.113"):
                mount = scratch / version
                mount.mkdir()
                subprocess.run(["hdiutil", "attach", "-readonly", "-nobrowse", "-noverify",
                                "-mountpoint", str(mount), str(found[version])],
                               check=True, capture_output=True)
                mounts.append(mount)
                app = next(mount.glob("*.app"))
                entries.append(fm.scan(app))
        finally:
            for mount in mounts:
                subprocess.run(["hdiutil", "detach", str(mount)], capture_output=True)
        old, new = entries
        plan = rd.Plan(old, new)
        changed = sorted(set(plan.write) | set(plan.chmod) | set(plan.remove))
        self.assertEqual(len(changed), 6, changed)
        self.assertLess(sum(new[p][2] for p in plan.write), 2 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
