"""A Mac's Trash Folder, managed from the Gateway (owner decision 4g, 2026-10-16, Studio Hub 0.6.182).

Listed with totals, moved to macOS Trash, or permanently deleted - only `<trash>/<date>/<name>` entries,
never through a link, and a permanent delete only on `confirm` with the count and bytes re-measured.
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from components.hub import output_storage  # noqa: E402
from components.hub.backend import auth, ledger  # noqa: E402

TODAY = time.strftime("%Y-%m-%d", time.gmtime())


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(os.path.realpath(temp.name))
        patcher = mock.patch.object(ledger, "DATA_DIR", self.root / "hub")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.trash = self.root / "hub" / "trash"
        self.outside = self.root / "outside"
        self.outside.mkdir()
        (self.outside / "precious.txt").write_text("not the Trash Folder's")

    def put(self, item: str, size: int = 5000) -> Path:
        """`<date>/<name>` as a folder holding one file of `size` bytes."""
        path = self.trash / item
        path.mkdir(parents=True)
        (path / "payload.bin").write_bytes(b"x" * size)
        return path

    def listing(self) -> dict:
        return output_storage.trash_listing()

    def delete(self, items="all", **overrides):
        current = output_storage.trash_listing() if items == "all" else None
        chosen = current["items"] if current else [i for i in self.listing()["items"] if i["id"] in items]
        body = {"items": items, "confirm": True, "expected_count": len(chosen),
                "expected_bytes": sum(i["bytes"] for i in chosen), **overrides}
        return output_storage.trash_delete(**body)


class Listing(Fixture):
    def test_totals_by_kind_and_age_and_disk_free(self) -> None:
        self.put(f"{TODAY}/previous-app-0123abcd")
        self.put(f"{TODAY}/batch_42", size=9000)
        self.put("2026-01-01/stale-updates-deadbeef")
        (self.trash / "not-a-date").mkdir()                        # not ours: never listed
        (self.trash / "not-a-date" / "x").write_text("x")
        got = self.listing()
        self.assertEqual(got["count"], 3)
        self.assertEqual(got["bytes"], sum(i["bytes"] for i in got["items"]))
        self.assertGreater(got["bytes"], 0)
        self.assertEqual({k: v["count"] for k, v in got["by_kind"].items()},
                         {"previous-app": 1, "output": 1, "stale-updates": 1})
        self.assertEqual(got["by_age"]["today"]["count"], 2)
        self.assertEqual(got["by_age"]["over 30 days"]["count"], 1)
        self.assertGreater(got["disk"]["free_bytes"], 0)

    def test_a_linked_date_folder_is_never_listed(self) -> None:
        (self.trash).mkdir(parents=True)
        os.symlink(self.outside, self.trash / TODAY)
        self.assertEqual(self.listing()["count"], 0)

    def test_an_empty_or_missing_trash_folder_lists_nothing(self) -> None:
        self.assertEqual((self.listing()["count"], self.listing()["bytes"]), (0, 0))


class PathSafety(Fixture):
    def test_ids_outside_the_two_levels_are_refused_and_nothing_is_deleted(self) -> None:
        kept = self.put(f"{TODAY}/keep-0123abcd")
        for bad in (["../outside"], [f"{TODAY}/.."], [f"{TODAY}/."], [str(self.outside)], [f"{TODAY}"],
                    [f"{TODAY}/keep-0123abcd/payload.bin"], ["2026-13/x"], [""], [7], [], "everything"):
            with self.assertRaises(output_storage.TrashRefused) as caught:
                output_storage.trash_delete(items=bad, confirm=True, expected_count=0, expected_bytes=0)
            self.assertEqual(caught.exception.status, 400, bad)
        self.assertTrue((kept / "payload.bin").exists())
        self.assertTrue((self.outside / "precious.txt").exists())

    def test_a_linked_date_folder_is_refused_even_by_id(self) -> None:
        self.trash.mkdir(parents=True)
        os.symlink(self.outside, self.trash / TODAY)
        with self.assertRaises(output_storage.TrashRefused) as caught:
            output_storage.trash_delete(items=[f"{TODAY}/precious.txt"], confirm=True,
                                        expected_count=1, expected_bytes=0)
        self.assertEqual(caught.exception.status, 400)
        self.assertTrue((self.outside / "precious.txt").exists())

    def test_links_inside_are_removed_never_followed(self) -> None:
        day = self.trash / TODAY
        day.mkdir(parents=True)
        os.symlink(self.outside, day / "link-to-outside")               # an entry that is a link
        folder = self.put(f"{TODAY}/folder-0123abcd")
        os.symlink(self.outside, folder / "inner-link")                 # a link inside a deleted folder
        result = self.delete()
        self.assertEqual(result["deleted_count"], 2)
        self.assertFalse(os.path.lexists(day / "link-to-outside"))
        self.assertFalse(folder.exists())
        self.assertEqual((self.outside / "precious.txt").read_text(), "not the Trash Folder's")

    def test_the_users_macos_trash_and_a_roots_own_trash_folder_are_never_reached(self) -> None:
        self.put(f"{TODAY}/a-0123abcd")
        fallback = self.root / "managed" / output_storage.FALLBACK_TRASH_NAME / TODAY / "b"
        fallback.mkdir(parents=True)
        self.assertEqual([i["id"] for i in self.listing()["items"]], [f"{TODAY}/a-0123abcd"])
        self.delete()
        self.assertTrue(fallback.exists())


class Confirmation(Fixture):
    def test_a_count_or_bytes_mismatch_is_409_and_nothing_is_deleted(self) -> None:
        a = self.put(f"{TODAY}/a-0123abcd")
        b = self.put(f"{TODAY}/b-0123abcd")
        listed = self.listing()
        for count, size in ((listed["count"] - 1, listed["bytes"]), (listed["count"], listed["bytes"] + 1)):
            with self.assertRaises(output_storage.TrashRefused) as caught:
                output_storage.trash_delete(items="all", confirm=True, expected_count=count, expected_bytes=size)
            self.assertEqual((caught.exception.status, caught.exception.body["code"]), (409, "TRASH_CHANGED"))
            self.assertEqual((caught.exception.body["count"], caught.exception.body["bytes"]),
                             (listed["count"], listed["bytes"]))
        self.assertTrue(a.exists() and b.exists())

    def test_something_added_after_the_listing_refuses_the_delete(self) -> None:
        self.put(f"{TODAY}/a-0123abcd")
        listed = self.listing()
        late = self.put(f"{TODAY}/late-0123abcd")
        with self.assertRaises(output_storage.TrashRefused):
            output_storage.trash_delete(items="all", confirm=True, expected_count=listed["count"],
                                        expected_bytes=listed["bytes"])
        self.assertTrue(late.exists())

    def test_confirm_and_expected_values_are_required(self) -> None:
        kept = self.put(f"{TODAY}/a-0123abcd")
        listed = self.listing()
        for body in ({"confirm": False}, {"confirm": "true"}, {"expected_count": None},
                     {"expected_bytes": "1"}, {"expected_count": True}, {"expected_bytes": -1}):
            args = {"items": "all", "confirm": True, "expected_count": listed["count"],
                    "expected_bytes": listed["bytes"], **body}
            with self.assertRaises(output_storage.TrashRefused) as caught:
                output_storage.trash_delete(**args)
            self.assertEqual(caught.exception.status, 400, body)
        self.assertTrue(kept.exists())

    def test_a_selected_item_only(self) -> None:
        a = self.put(f"{TODAY}/a-0123abcd")
        b = self.put(f"{TODAY}/b-0123abcd")
        result = self.delete([f"{TODAY}/a-0123abcd"])
        self.assertEqual((result["deleted"], a.exists(), b.exists()), ([f"{TODAY}/a-0123abcd"], False, True))
        with self.assertRaises(output_storage.TrashRefused) as caught:
            self.delete([f"{TODAY}/a-0123abcd"], expected_count=1, expected_bytes=0)
        self.assertEqual(caught.exception.status, 409, "an id that is gone is a changed Trash Folder")


class PartialFailure(Fixture):
    def test_one_failing_item_is_reported_the_rest_go_and_leftovers_are_swept_next_time(self) -> None:
        self.put(f"{TODAY}/a-0123abcd")
        self.put(f"{TODAY}/b-0123abcd")
        self.put(f"{TODAY}/c-0123abcd")
        real = output_storage.shutil.rmtree

        def rmtree(path, *args, **kwargs):
            if str(path) == "1":                                       # entries are staged as 0, 1, 2
                raise PermissionError(13, "Operation not permitted", str(path))
            return real(path, *args, **kwargs)

        with mock.patch.object(output_storage.shutil, "rmtree", rmtree):
            result = self.delete()
        self.assertFalse(result["ok"])
        self.assertEqual(sorted(result["deleted"]), [f"{TODAY}/a-0123abcd", f"{TODAY}/c-0123abcd"])
        self.assertEqual([f["id"] for f in result["failed"]], [f"{TODAY}/b-0123abcd"])
        self.assertEqual(self.listing()["count"], 0, "what could not be removed is no longer listed")
        self.assertEqual(len(list(self.trash.glob(".deleting-*/*"))), 1, "its leftover waits in the staging folder")
        self.put(f"{TODAY}/d-0123abcd")
        self.delete()                                                  # the next delete sweeps it
        self.assertEqual(list(self.trash.glob(".deleting-*")), [])

    def test_item_that_cannot_be_renamed_stays_listed(self) -> None:
        self.put(f"{TODAY}/a-0123abcd")
        self.put(f"{TODAY}/b-0123abcd")
        real = os.rename

        def rename(src, dst, *args, **kwargs):
            if src == "b-0123abcd":
                raise PermissionError(13, "Operation not permitted", src)
            return real(src, dst, *args, **kwargs)

        with mock.patch.object(output_storage.os, "rename", rename):
            result = self.delete()
        self.assertEqual(result["deleted"], [f"{TODAY}/a-0123abcd"])
        self.assertEqual([f["id"] for f in result["failed"]], [f"{TODAY}/b-0123abcd"])
        self.assertEqual([i["id"] for i in self.listing()["items"]], [f"{TODAY}/b-0123abcd"])


class DeleteDoesNotBlockTheHub(Fixture):
    def _holds_lock_free(self, patch_target, patch_name, action) -> None:
        started, release, got = threading.Event(), threading.Event(), threading.Event()
        real = getattr(patch_target, patch_name)

        def slow(*args, **kwargs):
            started.set()
            release.wait(10)
            return real(*args, **kwargs)

        def acquire():
            with output_storage._LOCK:
                got.set()

        with mock.patch.object(patch_target, patch_name, slow):
            worker = threading.Thread(target=action)
            worker.start()
            self.assertTrue(started.wait(5), "the action reached its slow step")
            threading.Thread(target=acquire).start()
            self.assertTrue(got.wait(2), "the lock is free while the slow step runs")
            release.set()
            worker.join(10)

    def test_a_slow_delete_does_not_hold_the_lock(self) -> None:
        self.put(f"{TODAY}/a-0123abcd")
        self._holds_lock_free(output_storage.shutil, "rmtree", self.delete)
        self.assertEqual(self.listing()["count"], 0)

    def test_a_slow_move_to_macos_trash_does_not_hold_the_lock(self) -> None:
        self.put(f"{TODAY}/a-0123abcd")
        self._holds_lock_free(output_storage, "_macos_trash", lambda: output_storage.trash_to_macos("all"))

    def test_a_date_folder_swapped_for_a_link_is_not_followed(self) -> None:
        self.put(f"{TODAY}/a-0123abcd")
        day, moved = self.trash / TODAY, self.root / "elsewhere"
        listed = self.listing()
        real, armed = output_storage._real_day, []

        def swap(path, real_root):
            ok = real(path, real_root)
            armed.append(path == day)
            if ok and path == day and sum(armed) == 2:                 # swap right after the delete's own check
                os.rename(day, moved)
                os.symlink(self.outside, day)
            return ok

        with mock.patch.object(output_storage, "_real_day", swap):
            result = output_storage.trash_delete(items="all", confirm=True, expected_count=listed["count"],
                                                 expected_bytes=listed["bytes"])
        self.assertFalse(result["ok"])
        self.assertTrue((self.outside / "precious.txt").exists())
        self.assertTrue((moved / "a-0123abcd" / "payload.bin").exists())


class MoveToMacosTrash(Fixture):
    def test_moves_are_read_back_from_the_disk(self) -> None:
        self.put(f"{TODAY}/a-0123abcd")
        self.put(f"{TODAY}/b-0123abcd")
        macos = self.root / "macos-trash"
        macos.mkdir()

        def fake(paths):
            out = []
            for path in paths:
                if path.endswith("a-0123abcd"):
                    os.rename(path, macos / Path(path).name)
                    out.append("ok")
                else:
                    out.append("You don't have permission.")
            return out

        with mock.patch.object(output_storage, "_macos_trash", fake), \
                mock.patch.object(output_storage.shutil, "rmtree", side_effect=AssertionError("deleted")):
            result = output_storage.trash_to_macos("all")
        self.assertEqual(result["moved"], [f"{TODAY}/a-0123abcd"])
        self.assertEqual(result["failed"], [{"id": f"{TODAY}/b-0123abcd", "error": "You don't have permission."}])
        self.assertTrue((macos / "a-0123abcd" / "payload.bin").exists())
        self.assertEqual([i["id"] for i in self.listing()["items"]], [f"{TODAY}/b-0123abcd"])

    def test_items_are_required(self) -> None:
        with self.assertRaises(output_storage.TrashRefused):
            output_storage.trash_to_macos(None)


class Routes(unittest.TestCase):
    import test_node_rename as base
    setUp = base.Routes.setUp
    OWNER, FLEET = base.Routes.OWNER, base.Routes.FLEET

    def client(self, stack: ExitStack):
        stack.enter_context(mock.patch.object(ledger, "DATA_DIR", self.data))
        return self.base.Routes.client(self, stack)

    def test_the_fleet_token_lists_this_macs_trash_and_a_node_cannot(self) -> None:
        with ExitStack() as stack:
            client = self.client(stack)
            (self.data / "trash" / TODAY / "a-0123abcd").mkdir(parents=True)
            (self.data / "trash" / TODAY / "a-0123abcd" / "f").write_bytes(b"y" * 4096)
            self.assertEqual(client.get("/api/hub/fleet/nodes/local/trash").status_code, 401)
            got = client.get("/api/hub/fleet/nodes/local/trash", headers=self.FLEET).json()
            self.assertEqual((got["ok"], got["count"]), (True, 1))
            refused = client.post("/api/hub/fleet/nodes/local/trash/delete", headers=self.FLEET,
                                  json={"items": "all", "confirm": True, "expected_count": 1, "expected_bytes": 1})
            self.assertEqual((refused.status_code, refused.json()["code"]), (409, "TRASH_CHANGED"))
            done = client.post("/api/hub/fleet/nodes/local/trash/delete", headers=self.FLEET, json={
                "items": "all", "confirm": True, "expected_count": 1, "expected_bytes": got["bytes"]}).json()
            self.assertEqual(done["deleted_count"], 1)
        for method, path in (("GET", "/api/hub/fleet/nodes/x-hub/trash"),
                             ("POST", "/api/hub/fleet/nodes/x-hub/trash/delete"),
                             ("POST", "/api/hub/fleet/nodes/x-hub/trash/move-to-macos-trash")):
            self.assertTrue(auth.fleet_path_allowed(method, path))
            self.assertFalse(auth.node_origin_allowed(method, path))
        self.assertFalse(auth.fleet_path_allowed("POST", "/api/hub/fleet/nodes/x-hub/trash/anything"))

    def test_a_node_on_an_older_hub_reads_as_needing_0_6_182(self) -> None:
        from components.hub.backend import control_plane, fleet_nodes
        refused = fleet_nodes.StartupRelayError(502, {"ok": False, "code": "NODE_REFUSED", "detail": "x"})
        with ExitStack() as stack:
            client = self.client(stack)
            real = control_plane.load_settings
            stack.enter_context(mock.patch.object(control_plane, "load_settings", lambda *a, **k: {
                **real(*a, **k), "role": "controller"}))
            stack.enter_context(mock.patch.object(fleet_nodes, "relay_model", side_effect=refused))
            answer = client.get("/api/hub/fleet/nodes/old-mac-hub/trash", headers=self.FLEET)
        self.assertEqual(answer.status_code, 409)
        self.assertEqual((answer.json()["code"], answer.json()["needs_hub"]), ("HUB_TOO_OLD", "0.6.182"))


if __name__ == "__main__":
    unittest.main()
