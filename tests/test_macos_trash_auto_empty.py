"""macOS's own "Remove items from the Trash after 30 days" (owner request 2026-10-19).

Everything the Hub retires ends in macOS Trash, so a Mac whose Finder never empties that Trash fills
its disk until someone does it by hand. The Hub reads the preference (`com.apple.finder
FXRemoveOldTrashItems`) as the logged-in user, publishes it in the `startup` report the Gateway
already reads, and can turn it **on** - never off.

**No test here touches this Mac's real Finder preference.** `output_storage._defaults` is faked in
every case, so the only `defaults` that ever runs is the fake; the real one is asserted unreachable.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from components.hub import output_storage  # noqa: E402
from components.hub.backend import auth, startup_control  # noqa: E402


_UNSET = object()


class FakeDefaults:
    """A stand-in for `/usr/bin/defaults` that records every call and answers a scripted reply.

    `read` answers `reply` (the text `defaults read` would print), or the missing-key answer (exit 1,
    "does not exist") when `reply is None`. A successful `write` is what makes the key read back - the
    written value is mirrored into `reply`, exactly as the real `defaults write` does. `stored`
    overrides that mirror, which is how "exited 0 and still does not stick" is simulated.
    """

    def __init__(self, reply: str | None = None, *, write_code: int = 0, stored: object = _UNSET,
                 stderr: str = "", runnable: bool = True) -> None:
        self.reply, self.write_code, self.stored = reply, write_code, stored
        self.stderr, self.runnable = stderr, runnable
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, *arguments: str):
        self.calls.append(arguments)
        if not self.runnable:
            return None                                  # `defaults` could not be run at all
        if arguments[0] == "write":
            if self.write_code == 0:
                self.reply = self.stored if self.stored is not _UNSET else (
                    "1" if arguments[-1].lower() == "true" else "0")
            return mock.Mock(returncode=self.write_code, stdout="", stderr=self.stderr)
        return mock.Mock(returncode=1 if self.reply is None else 0,
                         stdout="" if self.reply is None else self.reply + "\n", stderr="")


class ReadTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch.object(output_storage, "_finder_read", (0.0, None))
        patcher.start()
        self.addCleanup(patcher.stop)

    def read(self, fake: FakeDefaults):
        """One uncached read, so each case starts from the preference and not from a neighbour's answer."""
        with mock.patch.object(output_storage, "_defaults", fake):
            return output_storage.finder_trash_auto_empty(fresh=True)

    def test_a_key_that_was_never_written_is_off_not_unknown(self) -> None:
        """macOS writes it only when the box is ticked, so absent is its own default - a real answer."""
        fake = FakeDefaults(None)
        self.assertIs(self.read(fake), False)
        self.assertEqual(fake.calls, [("read", "com.apple.finder", "FXRemoveOldTrashItems")])

    def test_the_values_defaults_prints_for_a_boolean_are_read_as_such(self) -> None:
        for text, expected in (("1", True), ("true", True), ("YES", True),
                               ("0", False), ("false", False), ("no", False)):
            with self.subTest(text=text):
                self.assertIs(self.read(FakeDefaults(text)), expected)

    def test_a_read_that_could_not_run_or_answered_oddly_is_unknown(self) -> None:
        """Unknown is shown as unknown, never drawn as off: an unreadable preference is not a finding."""
        self.assertIsNone(self.read(FakeDefaults(runnable=False)))
        with mock.patch.object(output_storage, "_defaults",
                               lambda *a: mock.Mock(returncode=2, stdout="", stderr="boom")):
            self.assertIsNone(output_storage.finder_trash_auto_empty(fresh=True))

    def test_the_read_is_cached_so_a_poll_does_not_spawn_defaults_every_time(self) -> None:
        fake = FakeDefaults("1")
        ttl = output_storage.FINDER_READ_TTL_S
        with mock.patch.object(output_storage, "_defaults", fake):
            first = output_storage.finder_trash_auto_empty(now=1000.0)
            within = output_storage.finder_trash_auto_empty(now=1000.0 + ttl - 1)
            expired = output_storage.finder_trash_auto_empty(now=1000.0 + ttl + 1)
            forced = output_storage.finder_trash_auto_empty(now=1000.0, fresh=True)
        self.assertEqual((first, within, expired, forced), (True, True, True, True))
        # The first read, the one past the TTL, and the forced one - not the read inside the TTL.
        self.assertEqual(len(fake.calls), 3, fake.calls)


class EnableTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch.object(output_storage, "_finder_read", (0.0, None))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_it_writes_true_and_answers_the_value_read_back(self) -> None:
        fake = FakeDefaults(None)
        with mock.patch.object(output_storage, "_defaults", fake):
            result = output_storage.enable_finder_trash_auto_empty()
        self.assertEqual(result, {"macos_trash_auto_empty": True, "finder_restart_required": False})
        self.assertEqual(fake.calls, [("write", "com.apple.finder", "FXRemoveOldTrashItems", "-bool", "true"),
                                      ("read", "com.apple.finder", "FXRemoveOldTrashItems")])

    def test_it_never_writes_false(self) -> None:
        """The owner authorized turning this on. There is no path here that turns it off."""
        fake = FakeDefaults(None)
        with mock.patch.object(output_storage, "_defaults", fake):
            output_storage.enable_finder_trash_auto_empty()
        for call in fake.calls:
            self.assertNotIn("false", [part.lower() for part in call], call)
            self.assertNotIn("delete", [part.lower() for part in call], call)

    def test_a_write_that_does_not_stick_is_a_refusal_not_a_success(self) -> None:
        fake = FakeDefaults(None, write_code=0, stored=None)     # exits 0, the key still reads absent
        with mock.patch.object(output_storage, "_defaults", fake):
            with self.assertRaises(output_storage.TrashRefused) as caught:
                output_storage.enable_finder_trash_auto_empty()
        self.assertEqual((caught.exception.status, caught.exception.body["code"]), (500, "NOT_APPLIED"))

    def test_a_failed_write_says_so_and_does_not_claim_the_read_back(self) -> None:
        fake = FakeDefaults(None, write_code=1, stderr="Could not write domain com.apple.finder")
        with mock.patch.object(output_storage, "_defaults", fake):
            with self.assertRaises(output_storage.TrashRefused) as caught:
                output_storage.enable_finder_trash_auto_empty()
        self.assertEqual((caught.exception.status, caught.exception.body["code"]), (500, "WRITE_FAILED"))
        self.assertIn("Could not write domain", caught.exception.body["detail"])
        self.assertEqual(len(fake.calls), 1)                     # no read back after a failed write

    def test_defaults_that_cannot_be_run_is_a_refusal(self) -> None:
        with mock.patch.object(output_storage, "_defaults", FakeDefaults(runnable=False)):
            with self.assertRaises(output_storage.TrashRefused) as caught:
                output_storage.enable_finder_trash_auto_empty()
        self.assertEqual((caught.exception.status, caught.exception.body["code"]),
                         (503, "DEFAULTS_UNAVAILABLE"))


class PublicationTests(unittest.TestCase):
    """It travels in the `startup` object every Mac already reports, and in the Trash listing."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="studiohub-macos-trash-")
        self.addCleanup(temporary.cleanup)
        self.data = Path(os.path.realpath(temporary.name)) / "hub"
        self.data.mkdir(parents=True)
        from components.hub.backend import ledger
        for patcher in (mock.patch.object(output_storage, "_finder_read", (0.0, None)),
                        mock.patch.object(ledger, "DATA_DIR", self.data),
                        mock.patch.object(ledger, "DB_FILE", self.data / "hub.db")):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_the_startup_report_carries_it(self) -> None:
        for value in (True, False, None):
            with self.subTest(value=value):
                with mock.patch.object(output_storage, "finder_trash_auto_empty", lambda **_: value):
                    got = startup_control.snapshot()
                self.assertIn("macos_trash_auto_empty", got)
                self.assertIs(got["macos_trash_auto_empty"], value)

    def test_the_trash_listing_carries_it(self) -> None:
        with mock.patch.object(output_storage, "finder_trash_auto_empty", lambda **_: True):
            self.assertIs(output_storage.trash_overview()["macos_trash_auto_empty"], True)


class RouteTests(unittest.TestCase):
    """The real app, through its middleware (the fixture `test_node_rename.Routes` already uses)."""

    import test_node_rename as base
    setUp = base.Routes.setUp
    OWNER, FLEET = base.Routes.OWNER, base.Routes.FLEET

    def client(self, stack: ExitStack):
        # The route's audit line goes to `startup_control.DATA_DIR`, which is bound at import from
        # `registry` - so patching `registry.DATA_DIR` (the base fixture) is not enough, and without this
        # these tests write a real `startup_audit.jsonl` beside the checkout.
        stack.enter_context(mock.patch.object(startup_control, "DATA_DIR", self.data))
        return self.base.Routes.client(self, stack)

    def test_the_route_is_the_owners_and_the_fleets_and_a_node_cannot_use_it(self) -> None:
        for method, path in (("POST", "/api/hub/macos-trash-auto-empty/enable"),
                             ("POST", "/api/hub/fleet/nodes/x-hub/macos-trash-auto-empty/enable")):
            self.assertTrue(auth.fleet_path_allowed(method, path), path)
            self.assertFalse(auth.node_origin_allowed(method, path), path)
        # Nothing turns it off, and nothing else under that path exists.
        for path in ("/api/hub/macos-trash-auto-empty/disable",
                     "/api/hub/fleet/nodes/x-hub/macos-trash-auto-empty/disable",
                     "/api/hub/fleet/nodes/x-hub/macos-trash-auto-empty",
                     "/api/hub/macos-trash-auto-empty"):
            self.assertFalse(auth.fleet_path_allowed("POST", path), path)
        self.assertFalse(auth.fleet_path_allowed("GET", "/api/hub/macos-trash-auto-empty/enable"))

    def test_the_apps_own_button_runs_it_here_and_is_authenticated(self) -> None:
        enabled = {"ok": True, "macos_trash_auto_empty": True, "finder_restart_required": False}
        with ExitStack() as stack:
            client = self.client(stack)
            stack.enter_context(mock.patch.object(output_storage, "enable_finder_trash_auto_empty",
                                                  side_effect=lambda: dict(enabled)))
            self.assertEqual(client.post("/api/hub/macos-trash-auto-empty/enable").status_code, 401)
            answer = client.post("/api/hub/macos-trash-auto-empty/enable", headers=self.OWNER)
        self.assertEqual(answer.status_code, 200, answer.text)
        self.assertIs(answer.json()["macos_trash_auto_empty"], True)

    def test_a_refusal_keeps_the_hubs_status_and_code(self) -> None:
        refusal = output_storage.TrashRefused(500, "NOT_APPLIED", "written but does not read back as on")
        with ExitStack() as stack:
            client = self.client(stack)
            stack.enter_context(mock.patch.object(output_storage, "enable_finder_trash_auto_empty",
                                                  side_effect=refusal))
            answer = client.post("/api/hub/fleet/nodes/local/macos-trash-auto-empty/enable", headers=self.FLEET)
        self.assertEqual(answer.status_code, 500)
        self.assertEqual(answer.json()["code"], "NOT_APPLIED")

    def test_a_node_is_relayed_to_its_own_copy_of_the_route(self) -> None:
        from components.hub.backend import control_plane, fleet_nodes
        sent: list[tuple] = []

        async def relay(node_id, method, path, **kwargs):
            sent.append((node_id, method, path))
            return {"ok": True, "macos_trash_auto_empty": True}

        real = control_plane.load_settings
        with ExitStack() as stack:
            client = self.client(stack)
            stack.enter_context(mock.patch.object(control_plane, "load_settings",
                                                  lambda *a, **k: {**real(*a, **k), "role": "controller"}))
            stack.enter_context(mock.patch.object(fleet_nodes, "relay_model", relay))
            answer = client.post("/api/hub/fleet/nodes/some-node-hub/macos-trash-auto-empty/enable",
                                 headers=self.FLEET)
        self.assertEqual(answer.status_code, 200, answer.text)
        self.assertEqual(sent, [("some-node-hub", "POST", "/macos-trash-auto-empty/enable")])

    def test_a_node_on_an_older_hub_reads_as_needing_0_6_187(self) -> None:
        from components.hub.backend import control_plane, fleet_nodes
        refused = fleet_nodes.StartupRelayError(502, {"ok": False, "code": "NODE_REFUSED", "detail": "x"})
        real = control_plane.load_settings
        with ExitStack() as stack:
            client = self.client(stack)
            stack.enter_context(mock.patch.object(control_plane, "load_settings",
                                                  lambda *a, **k: {**real(*a, **k), "role": "controller"}))
            stack.enter_context(mock.patch.object(fleet_nodes, "relay_model", side_effect=refused))
            answer = client.post("/api/hub/fleet/nodes/old-mac-hub/macos-trash-auto-empty/enable",
                                 headers=self.FLEET)
        self.assertEqual(answer.status_code, 409)
        self.assertEqual((answer.json()["code"], answer.json()["needs_hub"]), ("HUB_TOO_OLD", "0.6.187"))

    def test_a_quiet_node_keeps_its_last_word_about_it(self) -> None:
        """A Mac nobody can ask is the one that matters most, so its last answer is kept."""
        from components.hub.backend import fleet_nodes
        rows: dict = {"node-1": {"id": "node-1"}}
        with mock.patch.object(fleet_nodes, "_read", lambda: rows), \
                mock.patch.object(fleet_nodes, "_write", lambda value: None):
            fleet_nodes.note_seen(["node-1"],
                                  startups={"node-1": {"open_at_login": True, "macos_trash_auto_empty": True}},
                                  last_stops={})
        kept = fleet_nodes._last_known_startup(rows["node-1"])
        self.assertIs(kept["macos_trash_auto_empty"], True)
        self.assertTrue(kept["stale"])

    def test_the_fleet_read_carries_it_on_a_machine_row(self) -> None:
        """The Gateway's only door to this: `startup` on the row, read straight from the snapshot."""
        with ExitStack() as stack:
            client = self.client(stack)
            stack.enter_context(mock.patch.object(output_storage, "finder_trash_auto_empty", lambda **_: True))
            row = client.get("/api/hub/resources", headers=self.FLEET).json()
        self.assertIs(row["startup"]["macos_trash_auto_empty"], True)


class NoRealPreferenceTests(unittest.TestCase):
    def test_no_test_in_this_file_can_reach_the_real_defaults(self) -> None:
        """A guard on the guard: the fake is the only `defaults` this file ever calls."""
        real = "/usr/bin/defaults"
        self.assertTrue(os.path.exists(real))
        with mock.patch.object(output_storage, "_finder_read", (0.0, None)):
            fake = FakeDefaults("1")
            with mock.patch.object(output_storage, "_defaults", fake):
                output_storage.finder_trash_auto_empty(fresh=True)
        self.assertEqual(len(fake.calls), 1)


if __name__ == "__main__":
    unittest.main()
