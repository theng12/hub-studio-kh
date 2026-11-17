"""Empty all trash (owner, 2026-11-16, Studio Hub 0.6.243).

The Hub Trash Folder and the whole macOS Trash on one Mac, removed for good from its own window or -
unless its "Allow remote empty" switch is off - from the Gateway with the fleet credential. Re-measured
before anything is deleted (409 TRASH_CHANGED), one audit line per run, a named failure when macOS
keeps the Trash private (Full Disk Access), and never a prompt. Temp directories and a temp HOME only:
no test reads or writes the real ~/.Trash or the owner's data.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from components.hub import output_storage, standalone, workstation  # noqa: E402
from components.hub.backend import auth, broker, ledger, resources, startup_control  # noqa: E402

TODAY = time.strftime("%Y-%m-%d", time.gmtime())


def _no_prompt(*_args, **_kwargs):
    raise AssertionError("emptying the trash ran a child process (osascript/Finder can show a prompt)")


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(os.path.realpath(temp.name))
        self.data = self.root / "hub"
        self.home = self.root / "home"
        self.macos = self.home / ".Trash"
        self.macos.mkdir(parents=True)
        self.outside = self.root / "outside"
        self.outside.mkdir()
        (self.outside / "precious.txt").write_text("not the trash's")
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for module, name, value in ((ledger, "DATA_DIR", self.data), (ledger, "DB_FILE", self.data / "hub.db"),
                                    (output_storage, "QUIET_S", 0.0), (output_storage, "_PREVIEW", (0.0, None)),
                                    (workstation, "load", lambda: {**workstation.DEFAULTS})):
            self.stack.enter_context(mock.patch.object(module, name, value))
        self.stack.enter_context(mock.patch.dict(os.environ, {"HOME": str(self.home)}))
        self.addCleanup(self._unlock)

    def _unlock(self) -> None:
        os.chmod(self.macos, 0o700)

    def history(self, days_ago: float = 1, machine: str = "local", studio: str = "image") -> None:
        """Real generation history in this Mac's own ledger: a job this Hub owned on its own worker."""
        self.data.mkdir(parents=True, exist_ok=True)
        when = time.time() - days_ago * 86400
        with ledger.activity_transaction() as conn:
            ledger.record_activity_event(
                machine=machine, studio=studio, job_id=f"j{time.time_ns()}", state="done",
                model="m", source="job", hub_owned=machine == "local",
                observed_at=when, activity_received_at=when, conn=conn)

    def hub_item(self, name: str, size: int = 5000) -> Path:
        path = self.data / "trash" / TODAY / name
        path.mkdir(parents=True)
        (path / "payload.bin").write_bytes(b"x" * size)
        return path

    def macos_item(self, name: str, size: int = 7000) -> Path:
        path = self.macos / name
        path.write_bytes(b"y" * size)
        return path

    def preview(self) -> dict:
        return output_storage.empty_preview()


class EmptyAll(Fixture):
    def test_it_empties_both_trashes_and_says_what_each_freed(self) -> None:
        self.hub_item("batch_1")
        self.macos_item("old.mov")
        (self.macos / "folder").mkdir()
        (self.macos / "folder" / "inside.bin").write_bytes(b"z" * 3000)
        os.symlink(self.outside, self.macos / "a-link")             # removed as a link, never followed
        plan = self.preview()
        self.assertEqual(plan["count"], 4)
        self.assertEqual(plan["frees_bytes"], plan["bytes"])
        with mock.patch.object(subprocess, "Popen", _no_prompt):
            done = output_storage.empty_all(expected_count=plan["count"], expected_bytes=plan["bytes"])
        self.assertTrue(done["ok"], done)
        self.assertEqual(done["deleted_count"], 4)
        self.assertEqual(done["freed_bytes"], plan["bytes"])
        self.assertEqual((done["left_count"], done["left_bytes"]), (0, 0))
        self.assertEqual([row["location"] for row in done["locations"]], ["hub_trash", "macos_trash"])
        self.assertTrue(all(row["state"] == "emptied" for row in done["locations"]))
        self.assertEqual(os.listdir(self.macos), [])
        self.assertEqual(list((self.data / "trash" / TODAY).iterdir()), [])
        self.assertEqual((self.outside / "precious.txt").read_text(), "not the trash's")

    def test_a_changed_trash_is_409_and_nothing_is_deleted(self) -> None:
        self.hub_item("batch_1")
        kept = self.macos_item("old.mov")
        plan = self.preview()
        self.macos_item("arrived-after.mov")
        with self.assertRaises(output_storage.TrashRefused) as refused:
            output_storage.empty_all(expected_count=plan["count"], expected_bytes=plan["bytes"])
        self.assertEqual((refused.exception.status, refused.exception.body["code"]), (409, "TRASH_CHANGED"))
        self.assertTrue(kept.exists())
        self.assertEqual(len(list((self.data / "trash" / TODAY).iterdir())), 1)

    def test_an_item_still_being_written_stays_and_is_named(self) -> None:
        self.macos_item("old.mov")
        os.utime(self.macos / "old.mov", (time.time() - 3600, time.time() - 3600))
        fresh = self.macos_item("copying.mov")
        with mock.patch.object(output_storage, "QUIET_S", 10_000.0):
            done = output_storage.empty_all()
        macos = done["locations"][1]
        # Both were kept, so nothing was emptied: "skipped" is the honest word over honest figures
        # (critic, 2026-11-17; "emptied" with deleted_count 0 was the defect).
        self.assertEqual((macos["state"], macos["left_count"]), ("skipped", 2))  # ctime is recent for both
        self.assertIn("may still be being written", macos["detail"])
        self.assertTrue(fresh.exists())

    def test_a_trash_that_was_entirely_kept_is_skipped_with_a_reason(self) -> None:
        self.macos_item("copying.mov")
        with mock.patch.object(output_storage, "QUIET_S", 10_000.0):
            done = output_storage.empty_all()
        self.assertTrue(done["ok"])
        self.assertEqual((done["state"], done["deleted_count"], done["left_count"]), ("skipped", 0, 1))
        self.assertEqual(done["detail"], "Left 1 item changed in the last minute: they may still be being written.")
        self.assertTrue((self.macos / "copying.mov").exists())

    def test_without_full_disk_access_it_says_so_and_never_prompts(self) -> None:
        self.hub_item("batch_1")
        self.macos_item("old.mov")
        os.chmod(self.macos, 0)                                     # what macOS answers without FDA: EPERM
        with mock.patch.object(subprocess, "Popen", _no_prompt):
            plan = self.preview()
            done = output_storage.empty_all()
        self.assertEqual(plan["blocked"], [output_storage.FDA_NEEDED])
        self.assertFalse(done["ok"])
        self.assertEqual(done["code"], output_storage.FDA_NEEDED)
        self.assertIn("Full Disk Access", done["detail"])
        self.assertEqual(done["locations"][0]["state"], "emptied")   # the Hub's own folder still went
        self.assertEqual(done["locations"][1]["state"], "failed")
        self.assertIsNone(done["left_bytes"], "an unreadable Trash is not reported, never 0")
        sizes = output_storage.trash_sizes(max_age=0)
        self.assertEqual(sizes["macos_trash"], {"count": None, "bytes": None, "code": output_storage.FDA_NEEDED})

    def test_a_refused_delete_is_named_with_what_is_left(self) -> None:
        self.macos_item("locked.mov")
        with mock.patch.object(output_storage, "_purge_entry", side_effect=PermissionError(1, "Operation not permitted")):
            done = output_storage.empty_all()
        macos = done["locations"][1]
        self.assertEqual((macos["state"], macos["code"], macos["left_count"]), ("failed", "DELETE_REFUSED", 1))
        self.assertIn("Operation not permitted", macos["detail"])

    def test_expected_figures_go_together(self) -> None:
        with self.assertRaises(output_storage.TrashRefused) as refused:
            output_storage.empty_all(expected_count=1)
        self.assertEqual(refused.exception.body["code"], "EXPECTED_REQUIRED")

class RemoteEmptyModes(Fixture):
    """Off is the shipped first state on every Mac (owner, 2026-11-16: *"it's default off. but i will
    have it turned on when it's released"*). `automatic` and its rule are what the owner uses after
    turning it on; nothing derived may be the first state. A Mac someone is using, or one in the
    middle of a job, is skipped even when it is on."""

    def test_a_freshly_installed_mac_is_off_and_says_so_plainly(self) -> None:
        decision = output_storage.remote_empty_decision()
        self.assertFalse(decision["effective"])
        self.assertEqual(decision["source"], "off until you enable it")
        self.assertEqual(decision["reason"], output_storage.REMOTE_OFF_DETAIL)
        self.assertEqual(output_storage.remote_empty_refusal(),
                         ("REMOTE_EMPTY_OFF", output_storage.REMOTE_OFF_DETAIL))

    def test_an_explicit_on_wins_and_reads_as_the_owners_choice(self) -> None:
        self.assertEqual(output_storage.set_remote_empty("on")["source"], "you")
        self.assertTrue(output_storage.remote_empty_allowed())
        self.assertIsNone(output_storage.remote_empty_refusal())
        self.assertEqual(stat.S_IMODE(os.stat(self.data / output_storage.TRASH_SETTINGS_FILE).st_mode), 0o600)
        off = output_storage.set_remote_empty("off")
        self.assertEqual((off["effective"], off["source"]), (False, "you"))

    def test_automatic_is_on_only_where_this_mac_generated_recently(self) -> None:
        output_storage.set_remote_empty("automatic")
        self.assertEqual(output_storage.remote_empty_decision()["reason"], "no generation work in 7 days")
        self.history(days_ago=2)
        decision = output_storage.remote_empty_decision()
        self.assertEqual((decision["effective"], decision["source"]), (True, "automatic"))

    def test_another_macs_work_and_chat_do_not_count(self) -> None:
        output_storage.set_remote_empty("automatic")
        self.history(days_ago=1, machine="blue-1")
        self.history(days_ago=1, studio="chat")
        self.assertFalse(output_storage.remote_empty_decision()["effective"])

    def test_the_window_and_the_rule_switch_are_settings(self) -> None:
        output_storage.set_remote_empty("automatic")
        self.history(days_ago=10)
        self.assertFalse(output_storage.remote_empty_decision()["effective"])
        self.assertTrue(output_storage.set_remote_empty(window_days=14)["effective"])
        self.assertEqual(output_storage.remote_empty_decision()["rule"]["window_days"], 14)
        self.assertIn("automatic rule is off", output_storage.set_remote_empty(rule_enabled=False)["reason"])

    def test_a_mac_someone_is_using_is_refused_even_when_on(self) -> None:
        output_storage.set_remote_empty("on")
        with mock.patch.object(workstation, "in_use", lambda: True):
            self.assertEqual(output_storage.remote_empty_refusal(),
                             ("REMOTE_EMPTY_OFF", "workstation in use: someone is using this Mac"))
        with mock.patch.object(workstation, "in_use", lambda: False):
            self.assertIsNone(output_storage.remote_empty_refusal())

    def test_a_working_mac_is_skipped_even_when_on(self) -> None:
        output_storage.set_remote_empty("on")
        with mock.patch.object(broker, "in_flight", lambda: 2):
            self.assertEqual(output_storage.remote_empty_refusal(),
                             ("MACHINE_WORKING", "working: this Mac is in the middle of a job; "
                                                 "the next run takes it"))

    def test_a_bad_value_is_refused(self) -> None:
        for mode, enabled, days in (("sometimes", None, None), (None, "yes", None),
                                    ("automatic", None, 0), ("automatic", None, "7")):
            with self.assertRaises(output_storage.TrashRefused):
                output_storage.set_remote_empty(mode, enabled=enabled, window_days=days)

    def test_the_first_cut_setting_file_counts_as_an_explicit_choice(self) -> None:
        self.data.mkdir(parents=True, exist_ok=True)
        (self.data / output_storage.TRASH_SETTINGS_FILE).write_text(json.dumps({"allow_remote_empty": True}))
        decision = output_storage.remote_empty_decision()
        self.assertEqual((decision["effective"], decision["source"]), (True, "you"))


class OverviewShape(Fixture):
    def test_the_trash_overview_carries_the_switch_at_the_level_the_app_reads(self) -> None:
        # `NativeTrashFolder.remoteEmpty` and `.allowRemoteEmpty` are top-level keys of
        # `GET /api/hub/trash`; publishing them only inside `empty` left the Settings switch undrawn
        # (caught by the gate's window proof, not by these tests, so this pins it).
        self.hub_item("batch_1")
        got = output_storage.trash_overview()
        self.assertIn("remote_empty", got)
        self.assertEqual(got["remote_empty"], got["empty"]["remote_empty"])
        self.assertIs(got["allow_remote_empty"], got["remote_empty"]["effective"])
        # An untouched Mac says this, not a rule's verdict - the normal state on ~50 Macs.
        self.assertEqual(got["remote_empty"]["source"], "off until you enable it")


class FleetFields(unittest.TestCase):
    def test_an_older_hub_reads_not_reported_never_zero(self) -> None:
        row = standalone._trash_and_memory({})
        # Every *measure* is "not reported" (None), never 0: an older Mac that cannot say must not
        # read as an empty, healthy one. Named one by one rather than as a set of values, because the
        # row also carries a block (`attention_report`) and a block is not a measurement.
        self.assertEqual({key: value for key, value in row.items() if key != "attention_report"},
                         {key: None for key in standalone._TRASH_AND_MEMORY_KEYS})
        self.assertIn("measured_at", row["attention_report"], "the block carries its own time")

    def test_memory_detail_names_wired_compressed_swap_and_the_top_three(self) -> None:
        import psutil
        detail = resources.memory_detail(psutil.virtual_memory())
        self.assertEqual(set(detail), {"wired_gb", "compressed_gb", "swap_used_gb", "top_processes"})
        self.assertLessEqual(len(detail["top_processes"]), 3)
        self.assertTrue(all(set(row) == {"name", "gb"} for row in detail["top_processes"]))


class Routes(unittest.TestCase):
    import test_node_rename as base
    setUp = base.Routes.setUp
    OWNER, FLEET = base.Routes.OWNER, base.Routes.FLEET

    def client(self, stack: ExitStack):
        self.home = Path(self.tmp.name) / "home"
        (self.home / ".Trash").mkdir(parents=True)
        self.audits: list[tuple[str, dict]] = []
        stack.enter_context(mock.patch.object(ledger, "DATA_DIR", self.data))
        stack.enter_context(mock.patch.object(output_storage, "QUIET_S", 0.0))
        stack.enter_context(mock.patch.object(output_storage, "_PREVIEW", (0.0, None)))
        stack.enter_context(mock.patch.dict(os.environ, {"HOME": str(self.home)}))
        stack.enter_context(mock.patch.object(startup_control, "audit",
                                              side_effect=lambda event, fields: self.audits.append((event, fields))))
        return self.base.Routes.client(self, stack)

    def put(self) -> None:
        (self.home / ".Trash" / "old.mov").write_bytes(b"y" * 4096)

    def runs(self) -> list[dict]:
        return [fields for event, fields in self.audits if event == "trash_empty"]

    def test_the_fleet_route_can_empty_a_leaf_node_cannot(self) -> None:
        for path in ("/api/hub/fleet/empty-trash", "/api/hub/fleet/nodes/x-hub/empty-trash"):
            self.assertTrue(auth.fleet_path_allowed("POST", path))
            self.assertFalse(auth.node_path_allowed("POST", path))
            self.assertFalse(auth.node_origin_allowed("POST", path))
        for path in ("/api/hub/trash/empty", "/api/hub/trash/allow-remote-empty"):
            self.assertFalse(auth.fleet_path_allowed("POST", path), "the window's own routes are not fleet routes")
        with ExitStack() as stack:
            client = self.client(stack)
            self.put()
            # Uncredentialed first: the owner call below leaves a session cookie that would authenticate it.
            self.assertEqual(client.post("/api/hub/fleet/nodes/local/empty-trash", json={}).status_code, 401)
            client.post("/api/hub/trash/allow-remote-empty", json={"mode": "on"}, headers=self.OWNER)
            answer = client.post("/api/hub/fleet/nodes/local/empty-trash", json={}, headers=self.FLEET)
        self.assertEqual(answer.status_code, 200, answer.text)
        row = answer.json()["machines"][0]
        self.assertEqual((row["state"], row["deleted_count"], row["left_count"]), ("emptied", 1, 0))
        self.assertGreater(row["freed_bytes"], 0)
        self.assertFalse((self.home / ".Trash" / "old.mov").exists())
        self.assertEqual(len(self.runs()), 1, "one audit line per run")
        self.assertEqual(self.runs()[0]["emptied"], 1)
        self.assertEqual(row["remote_empty"]["source"], "you")
        self.assertTrue(row["remote_empty"]["effective"])

    def test_the_switch_off_refuses_the_fleet_and_still_lets_the_window_empty(self) -> None:
        with ExitStack() as stack:
            client = self.client(stack)
            self.put()
            off = client.post("/api/hub/trash/allow-remote-empty", json={"enabled": False}, headers=self.OWNER)
            refused = client.post("/api/hub/fleet/nodes/local/empty-trash", json={}, headers=self.FLEET)
            everyone = client.post("/api/hub/fleet/empty-trash", json={}, headers=self.FLEET)
            still_there = (self.home / ".Trash" / "old.mov").exists()
            listed = client.get("/api/hub/trash", headers=self.OWNER).json()
            window = client.post("/api/hub/trash/empty", headers=self.OWNER, json={
                "expected_count": listed["empty"]["count"], "expected_bytes": listed["empty"]["bytes"]})
        self.assertEqual(off.json()["allow_remote_empty"], False)
        self.assertEqual((refused.status_code, refused.json()["code"]), (409, "REMOTE_EMPTY_OFF"))
        self.assertEqual(refused.json()["machines"][0]["state"], "skipped")
        self.assertIn("Allow remote empty is off", refused.json()["detail"])
        self.assertEqual(refused.json()["machines"][0]["reason"], output_storage.REMOTE_OFF_DETAIL)
        self.assertEqual(everyone.json()["machines"][0]["state"], "skipped")
        self.assertTrue(still_there, "a refused run deletes nothing")
        self.assertIs(listed["allow_remote_empty"], False)
        self.assertEqual(window.status_code, 200, window.text)
        self.assertEqual(window.json()["machines"][0]["state"], "emptied")
        self.assertEqual(len(self.runs()), 3)

    def test_the_window_route_needs_the_figures_it_showed_and_409s_on_change(self) -> None:
        with ExitStack() as stack:
            client = self.client(stack)
            self.put()
            missing = client.post("/api/hub/trash/empty", json={}, headers=self.OWNER)
            changed = client.post("/api/hub/trash/empty", headers=self.OWNER,
                                  json={"expected_count": 1, "expected_bytes": 1})
        self.assertEqual((missing.status_code, missing.json()["code"]), (400, "EXPECTED_REQUIRED"))
        self.assertEqual((changed.status_code, changed.json()["code"]), (409, "TRASH_CHANGED"))
        self.assertTrue((self.home / ".Trash" / "old.mov").exists())

    def test_a_window_run_that_deleted_nothing_is_skipped_not_emptied(self) -> None:
        # The Gateway keeps this row's word verbatim, so "emptied" with 0 deleted and 1 left told the
        # Fleet page something that did not happen (critic, 2026-11-17).
        with ExitStack() as stack:
            client = self.client(stack)
            self.put()
            stack.enter_context(mock.patch.object(output_storage, "QUIET_S", 10_000.0))
            plan = output_storage.empty_preview()
            answer = client.post("/api/hub/trash/empty", headers=self.OWNER,
                                 json={"expected_count": plan["count"], "expected_bytes": plan["bytes"]})
        row = answer.json()["machines"][0]
        self.assertEqual((answer.status_code, row["state"], row["deleted_count"], row["left_count"]),
                         (200, "skipped", 0, 1))
        self.assertIn("Left 1 item changed in the last minute", row["reason"])

    def test_a_second_press_while_emptying_is_skipped_not_failed(self) -> None:
        # A Mac already emptying is doing what this run asked: a skip, never an error on the Fleet page.
        with ExitStack() as stack:
            client = self.client(stack)
            client.post("/api/hub/trash/allow-remote-empty", json={"mode": "on"}, headers=self.OWNER)
            busy = output_storage.TrashRefused(409, "EMPTY_RUNNING",
                                               "This Mac is already emptying its trash; look again when it ends.")
            stack.enter_context(mock.patch.object(output_storage, "empty_all", side_effect=busy))
            answer = client.post("/api/hub/fleet/nodes/local/empty-trash", json={}, headers=self.FLEET)
        row = answer.json()["machines"][0]
        self.assertEqual((answer.status_code, row["state"], row["code"]), (409, "skipped", "EMPTY_RUNNING"))
        self.assertIn("already emptying", row["reason"])

    def test_the_first_state_is_off_and_the_row_says_so_plainly(self) -> None:
        with ExitStack() as stack:
            client = self.client(stack)
            self.put()
            answer = client.post("/api/hub/fleet/nodes/local/empty-trash", json={}, headers=self.FLEET)
        row = answer.json()["machines"][0]
        self.assertEqual((answer.status_code, row["state"], row["code"]), (409, "skipped", "REMOTE_EMPTY_OFF"))
        self.assertEqual(row["reason"], output_storage.REMOTE_OFF_DETAIL)
        self.assertIn("only be emptied from its own Hub window", row["reason"])
        self.assertEqual(row["remote_empty"]["source"], "off until you enable it")
        self.assertFalse(row["remote_empty"]["effective"])
        self.assertTrue((self.home / ".Trash" / "old.mov").exists(), "a skipped Mac deletes nothing")

    def test_a_working_mac_is_skipped_even_with_the_switch_on(self) -> None:
        with ExitStack() as stack:
            client = self.client(stack)
            self.put()
            client.post("/api/hub/trash/allow-remote-empty", json={"mode": "on"}, headers=self.OWNER)
            stack.enter_context(mock.patch.object(broker, "in_flight", lambda: 1))
            answer = client.post("/api/hub/fleet/empty-trash", json={}, headers=self.FLEET)
        row = answer.json()["machines"][0]
        self.assertEqual((row["state"], row["code"]), ("skipped", "MACHINE_WORKING"))
        self.assertTrue(row["reason"].startswith("working: "))
        self.assertTrue((self.home / ".Trash" / "old.mov").exists())
        self.assertEqual(answer.json()["ok"], True, "a skipped Mac is not a failed run")

    def test_a_controller_answers_one_row_per_mac_each_terminal(self) -> None:
        from components.hub.backend import control_plane, fleet_nodes
        nodes = {"mini-1": {"name": "Mini 1"}, "mini-2": {"name": "Mini 2"}, "mini-3": {"name": "Mini 3"}}

        async def relay(node_id, method, path, **_kwargs):
            self.assertEqual((method, path), ("POST", "/empty-trash"))
            if node_id == "mini-1":
                return {"ok": True, "machines": [{"machine": "mini-1", "label": "Mini 1", "state": "emptied",
                                                  "freed_bytes": 10, "deleted_count": 1, "left_count": 0,
                                                  "left_bytes": 0}]}
            if node_id == "mini-2":
                raise fleet_nodes.StartupRelayError(502, {"ok": False, "code": "NODE_UNREACHABLE", "detail": "off"})
            raise fleet_nodes.StartupRelayError(502, {"ok": False, "code": "NODE_REFUSED", "detail": "old"})

        with ExitStack() as stack:
            client = self.client(stack)
            real = control_plane.load_settings
            stack.enter_context(mock.patch.object(control_plane, "load_settings", lambda *a, **k: {
                **real(*a, **k), "role": "controller"}))
            stack.enter_context(mock.patch.object(fleet_nodes, "_read", return_value=nodes))
            stack.enter_context(mock.patch.object(fleet_nodes, "relay_model", side_effect=relay))
            answer = client.post("/api/hub/fleet/empty-trash", json={}, headers=self.FLEET).json()
        states = {row["machine"]: (row["state"], row.get("code")) for row in answer["machines"]}
        self.assertEqual(states["mini-1"], ("emptied", None))
        self.assertEqual(states["mini-2"], ("skipped", "NODE_UNREACHABLE"))
        self.assertEqual(states["mini-3"], ("skipped", "HUB_TOO_OLD"))
        self.assertEqual(len(answer["machines"]), 4)                 # this Mac first, then each node
        self.assertTrue(answer["ok"])
        self.assertEqual(len(self.runs()), 1)
        self.assertEqual((self.runs()[0]["scope"], self.runs()[0]["machines"]), ("all", 4))


if __name__ == "__main__":
    unittest.main()
