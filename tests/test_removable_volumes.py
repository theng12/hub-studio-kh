"""The 0.6.210 freeze and the 0.6.211 prompt: a Mac with no kit drive reads no volume at all.

**The incident, part one (0.6.206-0.6.209).** The Hub walked ``/Volumes`` inside
``GET /api/hub/model-presets``, and the app read that route on every connect and poll. An
ad-hoc signed build loses its macOS removable-volume permission on every update, so the
`open()` waited on an unanswered permission prompt and held a threadpool slot; enough polls
filled the pool and ``/api/health`` and ``/api/hub/resources`` stopped answering. Clicking
**Allow** revived the Mac.

**The incident, part two (the owner, 2026-10-30).** 0.6.210 moved the walk into a bounded child,
but the child still opened **every** mounted volume at every Hub start, so every Mac that had any
drive attached was asked again - "access files on a removable volume", and "access files on a
network volume" for a share. Most Macs never have a kit attached, so those prompts bought
nothing. The child now learns which mounts exist from the kernel mount table and opens only a
drive named like a kit (``KH-STUDIO-*``).

What is pinned here, in the order the fix is shaped:

1. The probe runs in a **child process with a hard timeout**, at most **once per process**, and a
   blocked child yields the honest ``needs_permission`` state instead of a hung slot.
2. **No volume that is not named like a kit is opened** - not a plain drive, not a network share
   even when it *is* named like a kit, and not ``/Volumes`` itself. Proved with an audit hook
   that records every path the child opens, stats or scans.
3. A kit-named drive is read and its library found, in every layout the drives have used.
4. A kit-named drive macOS refuses is ``needs_permission`` (a typed state, never a zero), and
   "no kit found" says which answer it is - `looked_inside` shows what was actually read.
5. ``status()``/``kits()`` never probe and never block; ``snapshot()`` takes the kits as an
   argument, so no request can reintroduce a walk.
6. Every route keeps answering while the child is blocked, and ``host_stats()`` carries the state.
7. The periodic removed-model handoff reads the library only when a removal actually happened.
8. The Gateway's per-node preset route does its work off the event loop.

The blocked child is a stand-in for the unanswered prompt. `tccutil` is deliberately not used:
that would change this Mac's privacy settings, which the owner forbade. What a **real** TCC
prompt does can only be observed on a Mac that has no permission for the drive; the audit hook
here proves the Hub does not touch a volume it has no business touching, which is the half that
can be proved without changing a privacy setting.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from components.hub.backend import model_install_plan
from components.hub.backend import removable_volumes

PREFIX = "studiohub-volume-probe:"

#: Records every path the child opens, stats or scans, so "we never read that volume" is a
#: measurement rather than a reading of the source. The log file is opened before the hook is
#: installed, so the log itself is not among the recorded paths.
AUDIT_PREFIX = r"""
import os as _os, sys as _sys
_audit_log = open(_os.environ["PROBE_AUDIT_LOG"], "a", buffering=1)
def _audit_hook(event, args):
    if event in ("os.scandir", "os.listdir", "open", "os.stat") and args:
        _audit_log.write(event + "\t" + str(args[0]) + "\n")
_sys.addaudithook(_audit_hook)
"""


def _blocking_child() -> list[str]:
    return [sys.executable, "-c", "import time; time.sleep(30)"]


def _answering_child(marker: Path) -> list[str]:
    source = (
        "import json, sys\n"
        f"open(sys.argv[1], 'a').write('x')\n"
        f"print({PREFIX!r} + json.dumps({{'ok': True, 'kits': ['/Volumes/KIT/model-library']}}))\n"
    )
    return [sys.executable, "-c", source, str(marker)]


def _mount_line(point: Path | str, kind: str = "apfs") -> str:
    """One line in the shape `/sbin/mount` prints."""
    if kind in removable_volumes.NETWORK_FILESYSTEMS:
        return f"NAS:/share on {point} ({kind}, nodev, nosuid, mounted by the owner)"
    return f"/dev/disk9s1 on {point} ({kind}, local, nodev, nosuid, journaled, noowners)"


def _mount_table(folder: Path, lines: list[str]) -> Path:
    path = folder / "mount-table.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _child(root: Path | str, table: Path) -> list[str]:
    return removable_volumes._child_command(root, mounts_from=table)


def _audited(child: list[str]) -> list[str]:
    """The same child with the audit hook in front of it."""
    command = list(child)
    assert command[2] == "-c", command
    command[3] = AUDIT_PREFIX + "\n" + command[3]
    return command


def _kit(root: Path, name: str) -> Path:
    """A drive the 2026-10-28 kit layout: <drive>/Video Generation Kit/Studio Hub KH/model-library."""
    drive = root / name
    (drive / "Video Generation Kit" / "Studio Hub KH" / "model-library").mkdir(parents=True)
    return drive


class ProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        removable_volumes._reset_for_testing()
        removable_volumes._set_probe_command_for_testing(None)

    def tearDown(self) -> None:
        removable_volumes._reset_for_testing()
        removable_volumes._set_probe_command_for_testing(None)

    def test_status_before_the_probe_is_not_checked_and_does_not_block(self) -> None:
        # "I have not looked" must not read as "there is nothing there" - and asking must not
        # start a walk either.
        with mock.patch.object(subprocess, "run", side_effect=AssertionError("probed from status")):
            state = removable_volumes.status()
        self.assertEqual(state["state"], "not_checked")
        self.assertEqual(state["kits"], [])

    def test_a_blocked_child_times_out_into_needs_permission(self) -> None:
        started = time.monotonic()
        state = removable_volumes.probe(timeout=0.3, command=_blocking_child())
        elapsed = time.monotonic() - started
        self.assertEqual(state["state"], "needs_permission")
        self.assertIn("permission", (state["reason"] or "").lower())
        self.assertEqual(state["path"], "/Volumes")
        self.assertLess(elapsed, 2.0, "the parent must give up on its own, not wait for the child")

    def test_a_child_that_answers_ready_is_cached_and_probed_only_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "count"
            removable_volumes._set_probe_command_for_testing(_answering_child(marker))
            removable_volumes.start()
            removable_volumes.start()
            deadline = time.monotonic() + 5
            while removable_volumes.status()["state"] == "checking" and time.monotonic() < deadline:
                time.sleep(0.01)
            state = removable_volumes.status()
            runs = marker.read_text()
        self.assertEqual(state["state"], "ready")
        self.assertEqual(removable_volumes.kits(), [Path("/Volumes/KIT/model-library")])
        self.assertEqual(runs, "x", "the child ran more than once in one process")

    def test_a_refused_read_is_needs_permission_not_an_empty_kit_list(self) -> None:
        source = ("import json\n"
                  f"print({PREFIX!r} + json.dumps({{'ok': False, 'reason': 'Operation not permitted'}}))")
        state = removable_volumes.probe(command=[sys.executable, "-c", source])
        self.assertEqual(state["state"], "needs_permission")
        self.assertIn("Operation not permitted", state["reason"])
        self.assertEqual(state["kits"], [])


class WhichVolumesAreReadTests(unittest.TestCase):
    """The 0.6.211 rule: the mount's name decides, and nothing else is opened."""

    def setUp(self) -> None:
        removable_volumes._reset_for_testing()
        removable_volumes._set_probe_command_for_testing(None)

    def tearDown(self) -> None:
        removable_volumes._reset_for_testing()
        removable_volumes._set_probe_command_for_testing(None)

    def _probed(self, root: Path, lines: list[str], tmp: Path):
        """Run the real child against a temporary volume root and a written mount table."""
        table = _mount_table(tmp, lines)
        log = tmp / "audit.log"
        with mock.patch.dict(os.environ, {"PROBE_AUDIT_LOG": str(log)}):
            state = removable_volumes.probe(timeout=10,
                                            command=_audited(_child(root, table)))
        return state, [line.split("\t", 1) for line in log.read_text().splitlines() if "\t" in line]

    def test_no_drive_is_opened_when_none_is_named_like_a_kit(self) -> None:
        """The owner's case: this Mac has drives, none is a kit, and nothing is read.

        Each non-matching mount **contains a model-library**, so a content walk would have found
        one - the old probe did exactly that, and that read is what macOS asked about.
        """
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            root = folder / "Volumes"
            plain = root / "Backup"
            share = root / "StudioShare"
            (plain / "model-library").mkdir(parents=True)
            (share / "model-library").mkdir(parents=True)
            state, events = self._probed(root, [
                _mount_line("/", "apfs"),
                _mount_line(plain, "apfs"),
                _mount_line(share, "smbfs"),
            ], folder)
        self.assertEqual(state["state"], "ready", state)
        self.assertEqual(state["kits"], [])
        self.assertEqual(state["looked_inside"], [],
                         "a drive not named like a kit was looked inside")
        self.assertIn("No drive named KH-STUDIO-*", state["reason"])
        self.assertTrue(events, "the audit hook recorded nothing, so this proof would be vacuous")
        touched = [path for _, path in events]
        for path in touched:
            self.assertFalse(str(path).startswith(str(plain)),
                             f"the plain drive was read: {path}")
            self.assertFalse(str(path).startswith(str(share)),
                             f"the network share was read: {path}")
        self.assertNotIn(str(root), touched, "the child still listed the volume root itself")

    def test_a_network_volume_named_like_a_kit_is_still_never_read(self) -> None:
        """A share that happens to carry the kit's name is the *other* permission, not a source."""
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            root = folder / "Volumes"
            share = root / "KH-STUDIO-NAS"
            (share / "model-library").mkdir(parents=True)
            state, events = self._probed(root, [
                _mount_line("/", "apfs"), _mount_line(share, "smbfs")], folder)
        self.assertEqual(state["state"], "ready", state)
        self.assertEqual(state["kits"], [])
        self.assertEqual(state["looked_inside"], [])
        self.assertIn("network", (state["reason"] or "").lower())
        self.assertTrue(state["not_read"], "the network mount was not recorded as skipped")
        for _, path in events:
            self.assertFalse(str(path).startswith(str(share)),
                             f"a network volume was read: {path}")

    def test_the_kits_three_level_library_is_found_in_a_kit_named_drive(self) -> None:
        """The 2026-10-28 drives keep the library at <drive>/Video Generation Kit/Studio Hub KH/.

        The old two-level walk never found these, so the screen could not offer the SSD as a
        source on the very drives the kit ships on. Both old layouts stay supported.
        """
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            root = folder / "Volumes"
            deep = _kit(root, "KH-STUDIO-ATLAS") / "Video Generation Kit" / "Studio Hub KH" \
                / "model-library"
            flat = root / "KH-STUDIO-OLD" / "model-library"
            two = root / "KH-STUDIO-TWO" / "Studio Hub App" / "model-library"
            flat.mkdir(parents=True)
            two.mkdir(parents=True)
            state, events = self._probed(root, [
                _mount_line("/", "apfs"),
                _mount_line(root / "KH-STUDIO-ATLAS"),
                _mount_line(root / "KH-STUDIO-OLD"),
                _mount_line(root / "KH-STUDIO-TWO"),
            ], folder)
        self.assertEqual(state["state"], "ready", state)
        self.assertEqual(sorted(state["kits"]), sorted([str(deep), str(flat), str(two)]))
        self.assertEqual(len(state["looked_inside"]), 3)
        # The tripwire is live: a drive that *is* read shows up in the audit log, so the
        # "nothing was read" assertions in the cases above are not passing on an empty log.
        self.assertIn(str(root / "KH-STUDIO-ATLAS"),
                      [path for _, path in events if _ == "os.scandir"],
                      "the audit hook did not record the kit drive it did read")

    def test_a_kit_drive_without_a_library_says_that_and_not_no_kit_mounted(self) -> None:
        """A kit-named drive that was read and holds nothing is a different fact from "none here"."""
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            root = folder / "Volumes"
            (root / "KH-STUDIO-EMPTY" / "Video Generation Kit").mkdir(parents=True)
            state, _ = self._probed(root, [
                _mount_line("/", "apfs"), _mount_line(root / "KH-STUDIO-EMPTY")], folder)
        self.assertEqual(state["state"], "ready", state)
        self.assertEqual(state["kits"], [])
        self.assertEqual(state["looked_inside"], [str(root / "KH-STUDIO-EMPTY")])
        self.assertIn("KH-STUDIO-EMPTY", state["reason"])
        self.assertIn("no model-library folder", state["reason"])

    @unittest.skipIf(os.geteuid() == 0, "root reads a mode-000 folder, so nothing is refused")
    def test_a_kit_drive_that_refuses_the_read_is_needs_permission(self) -> None:
        """Matching the name but not readable: `needs_permission`, not "no kit is mounted"."""
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            root = folder / "Volumes"
            drive = _kit(root, "KH-STUDIO-LOCKED")
            os.chmod(drive, 0o000)
            try:
                state, _ = self._probed(root, [
                    _mount_line("/", "apfs"), _mount_line(drive)], folder)
            finally:
                os.chmod(drive, 0o755)
        self.assertEqual(state["state"], "needs_permission", state)
        self.assertEqual(state["kits"], [])
        self.assertEqual(state["looked_inside"], [str(drive)])
        self.assertTrue(state["unreadable"], "a refused read left no trace in the answer")
        self.assertIn("KH-STUDIO-LOCKED", state["reason"])
        self.assertIn("Click Allow", state["reason"])

    @unittest.skipIf(os.geteuid() == 0, "root reads a mode-000 folder, so nothing is refused")
    def test_one_refused_drive_does_not_hide_the_kit_that_was_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            root = folder / "Volumes"
            good = _kit(root, "KH-STUDIO-GOOD")
            locked = _kit(root, "KH-STUDIO-LOCKED")
            os.chmod(locked, 0o000)
            try:
                state, _ = self._probed(root, [
                    _mount_line("/", "apfs"), _mount_line(good), _mount_line(locked)], folder)
            finally:
                os.chmod(locked, 0o755)
        self.assertEqual(state["state"], "ready", state)
        self.assertEqual(len(state["kits"]), 1)
        self.assertTrue(state["kits"][0].startswith(str(good)))
        self.assertIn("KH-STUDIO-LOCKED", state["reason"])
        self.assertIn("could not be read", state["reason"])

    @unittest.skipIf(os.geteuid() == 0, "root reads a mode-000 folder, so nothing is refused")
    def test_macoss_own_hidden_folders_on_a_kit_drive_are_never_entered(self) -> None:
        """Measured on this Mac: `.Spotlight-V100` and friends answer EPERM.

        They are macOS's metadata folders on every volume, they cannot hold a kit library, and
        the old `glob("*")` never matched them either. Entering them would record a healthy kit
        drive as one the Hub "could not read" - the live proof on 2026-10-30 caught exactly that.
        """
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            root = folder / "Volumes"
            drive = _kit(root, "KH-STUDIO-ATLAS")
            hidden = drive / ".Spotlight-V100"
            (hidden / "model-library").mkdir(parents=True)
            os.chmod(hidden, 0o000)
            try:
                state, events = self._probed(root, [
                    _mount_line("/", "apfs"), _mount_line(drive)], folder)
            finally:
                os.chmod(hidden, 0o755)
        self.assertEqual(state["state"], "ready", state)
        self.assertEqual(state["unreadable"], [], "a macOS metadata folder was read")
        self.assertEqual(state["reason"], None)
        self.assertEqual(len(state["kits"]), 1)
        for _, path in events:
            self.assertFalse(str(path).startswith(str(hidden)),
                             f"a hidden macOS folder was read: {path}")

    def test_the_child_does_not_list_the_volume_root_even_on_this_mac(self) -> None:
        """The production root, with a mount table that names nothing: no `/Volumes` access.

        This is the half of the macOS question that can be answered here. The prompt itself needs
        a Mac that has no permission for a drive, which cannot be produced without changing a
        privacy setting.
        """
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            table = _mount_table(folder, [_mount_line("/", "apfs")])
            log = folder / "audit.log"
            with mock.patch.dict(os.environ, {"PROBE_AUDIT_LOG": str(log)}):
                state = removable_volumes.probe(timeout=10, command=_audited(
                    removable_volumes._child_command(removable_volumes.VOLUME_ROOT,
                                                     mounts_from=table)))
            events = [line.split("\t", 1) for line in log.read_text().splitlines() if "\t" in line]
        self.assertEqual(state["state"], "ready", state)
        self.assertEqual(state["looked_inside"], [])
        touched = [str(path) for _, path in events]
        self.assertEqual([path for path in touched if path.startswith("/Volumes")], [],
                         f"the child touched /Volumes: {touched}")

    def test_the_cached_answer_carries_what_was_read(self) -> None:
        """The API's honest scope: `looked_inside` and `unreadable`, not only the reason words."""
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            root = folder / "Volumes"
            _kit(root, "KH-STUDIO-NOVA")
            table = _mount_table(folder, [
                _mount_line("/", "apfs"), _mount_line(root / "KH-STUDIO-NOVA")])
            removable_volumes._set_probe_command_for_testing(_child(root, table))
            removable_volumes.start()
            deadline = time.monotonic() + 10
            while removable_volumes.status()["state"] == "checking" and time.monotonic() < deadline:
                time.sleep(0.01)
            state = removable_volumes.status()
        self.assertEqual(state["state"], "ready", state)
        self.assertEqual(state["looked_inside"], [str(root / "KH-STUDIO-NOVA")])
        self.assertEqual(state["unreadable"], [])
        self.assertEqual(len(state["kits"]), 1)

    def test_the_mount_table_is_read_without_opening_a_volume(self) -> None:
        """`/sbin/mount` is the kernel's list; it is what makes the name filter possible at all."""
        done = subprocess.run(["/sbin/mount"], capture_output=True, text=True, timeout=10)
        self.assertEqual(done.returncode, 0, done.stderr)
        points = [line.split(" on ", 1)[1].split(" (", 1)[0] for line in done.stdout.splitlines()
                  if " on " in line and " (" in line]
        self.assertIn("/", points, "the mount table does not name the root filesystem")


class NoWalkOnARequestTests(unittest.TestCase):
    def setUp(self) -> None:
        removable_volumes._reset_for_testing()
        removable_volumes._set_probe_command_for_testing(None)

    def tearDown(self) -> None:
        removable_volumes._reset_for_testing()
        removable_volumes._set_probe_command_for_testing(None)

    def test_a_timed_out_probe_does_not_stop_the_routes_and_host_stats_says_so(self) -> None:
        """The gate's headline case: a removable volume is mounted and an access blocks.

        A child that never returns stands in for the unanswered macOS prompt. `/api/health`,
        `/api/hub/resources` and `/api/hub/model-presets` must all answer while it is blocked, and
        once it has timed out the host stats must say `needs_permission` - distinct from busy and
        from unreachable.
        """
        from fastapi.testclient import TestClient
        from components.hub import standalone
        from components.hub.backend import auth, resources

        removable_volumes._set_probe_command_for_testing(_blocking_child())
        old_timeout = removable_volumes.PROBE_TIMEOUT_SECONDS
        removable_volumes.PROBE_TIMEOUT_SECONDS = 1.0
        try:
            removable_volumes.start()
            with tempfile.TemporaryDirectory() as tmp:
                models = Path(tmp) / "models"
                models.mkdir()
                os.environ["STUDIOHUB_MODELS_DIR"] = str(models)
                app = standalone.create_app(start_background=False)
                token = auth.load_token()
                headers = {"X-Hub-Token": token}
                with TestClient(app) as client:
                    # While the child is still blocked, health must already answer.
                    started = time.monotonic()
                    health = client.get("/api/health")
                    self.assertEqual(health.status_code, 200, health.text)
                    self.assertLess(time.monotonic() - started, 1.0,
                                    "health waited on the blocked volume check")
                    self.assertIn("removable_volumes", health.json())

                    # Let the one probe time out, then read the honest state and every route.
                    deadline = time.monotonic() + 5
                    while (removable_volumes.status()["state"] == "checking"
                           and time.monotonic() < deadline):
                        time.sleep(0.02)
                    state = removable_volumes.status()
                    self.assertEqual(state["state"], "needs_permission", state)
                    self.assertEqual(resources.host_stats()["removable_volumes"]["state"],
                                     "needs_permission")

                    started = time.monotonic()
                    host = client.get("/api/hub/resources", headers=headers)
                    self.assertEqual(host.status_code, 200, host.text)
                    self.assertEqual(host.json()["host"]["removable_volumes"]["state"],
                                     "needs_permission")
                    presets = client.get("/api/hub/model-presets", headers=headers)
                    self.assertEqual(presets.status_code, 200, presets.text)
                    self.assertEqual(presets.json()["kits_state"]["state"], "needs_permission")
                    self.assertLess(time.monotonic() - started, 10.0,
                                    "a route blocked on the volume check")
        finally:
            removable_volumes.PROBE_TIMEOUT_SECONDS = old_timeout

    def test_no_route_calls_the_probe(self) -> None:
        from fastapi.testclient import TestClient
        from components.hub import standalone
        from components.hub.backend import auth

        with tempfile.TemporaryDirectory() as tmp:
            models = Path(tmp) / "models"
            models.mkdir()
            os.environ["STUDIOHUB_MODELS_DIR"] = str(models)
            app = standalone.create_app(start_background=False)
            token = auth.load_token()
            headers = {"X-Hub-Token": token}
            with mock.patch.object(removable_volumes, "probe",
                                   side_effect=AssertionError("a route walked /Volumes")):
                with TestClient(app) as client:
                    for path in ("/api/health", "/api/hub/resources", "/api/hub/model-presets"):
                        answer = client.get(path, headers=headers)
                        self.assertEqual(answer.status_code, 200, f"{path}: {answer.text}")

    def test_snapshot_takes_kits_and_never_derives_them(self) -> None:
        manifests = {path.stem: json.loads(path.read_text(encoding="utf-8"))
                     for path in (Path(__file__).resolve().parents[1] / "model-manifests").glob("*.json")}
        with self.assertRaises(TypeError):
            model_install_plan.snapshot(          # type: ignore[call-arg]
                manifests=manifests, receipts={}, library=Path("/tmp"),
                memory_gb=16, stop_bytes=5_000_000_000, modality_of=lambda engine: "image")
        with mock.patch.object(model_install_plan, "disk",
                               return_value={"path": "/tmp", "free_bytes": 10 ** 12,
                                             "total_bytes": 2 * 10 ** 12, "measured": True}):
            screen = model_install_plan.snapshot(
                kits=[Path("/Volumes/KIT/model-library")], kits_state={"state": "ready"},
                manifests=manifests, receipts={}, library=Path("/tmp"),
                memory_gb=16, stop_bytes=5_000_000_000, modality_of=lambda engine: "image")
        self.assertEqual(screen["kits"], ["/Volumes/KIT/model-library"])


class IdleCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        from components.hub.backend import model_removal
        model_removal._REMOVAL_HAPPENED = False
        self.removal = model_removal

    def tearDown(self) -> None:
        self.removal._REMOVAL_HAPPENED = False

    def test_an_idle_hub_reads_no_library(self) -> None:
        from components.hub import output_storage
        with mock.patch.object(self.removal, "_pending_path",
                               return_value=Path("/nonexistent/model-removal-pending.json")), \
                mock.patch.object(self.removal, "listing",
                                  side_effect=AssertionError("the idle cleanup read the library")), \
                mock.patch.object(self.removal, "handoff",
                                  side_effect=AssertionError("the idle cleanup ran a handoff")):
            self.assertFalse(self.removal.removal_happened())
            output_storage.removed_model_pass()

    def test_a_removal_makes_the_next_pass_read_the_library(self) -> None:
        from components.hub import output_storage
        self.removal._REMOVAL_HAPPENED = True
        with mock.patch.object(self.removal, "handoff") as handoff:
            output_storage.removed_model_pass()
        handoff.assert_called_once()


class FleetRouteOffTheEventLoopTests(unittest.TestCase):
    def test_a_slow_preset_screen_does_not_hold_the_loop(self) -> None:
        """The Gateway route used to run `model_presets_screen()` on the event loop.

        A slow read there stalls every other coroutine - the same shape as the freeze. The route
        must hand the work to a threadpool, so a concurrent task keeps running.
        """
        from components.hub import standalone

        with tempfile.TemporaryDirectory() as tmp:
            models = Path(tmp) / "models"
            models.mkdir()
            os.environ["STUDIOHUB_MODELS_DIR"] = str(models)
            app = standalone.create_app(start_background=False)
            endpoint = next(route.endpoint for route in app.routes
                            if getattr(route, "path", None) == "/api/hub/fleet/nodes/{node_id}/model-presets")

            def slow_snapshot(**_kwargs):
                time.sleep(0.4)
                return {"presets": [], "groups": [], "models": [], "problems": []}

            ticks: list[float] = []

            async def scenario() -> None:
                started = time.monotonic()

                async def ticker() -> None:
                    await asyncio.sleep(0.1)
                    ticks.append(time.monotonic() - started)

                with mock.patch.object(model_install_plan, "snapshot", side_effect=slow_snapshot):
                    await asyncio.gather(endpoint("local"), ticker())

            asyncio.run(scenario())
        self.assertTrue(ticks, "the concurrent task never ran")
        self.assertLess(ticks[0], 0.35,
                        "the preset route held the event loop instead of using a threadpool")


if __name__ == "__main__":
    unittest.main()
