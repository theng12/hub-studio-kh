"""The identifiers on this Hub, in plain view and editable where editing is honest.

The owner's requirement (2026-10-25): *"what are the identifiers on Studio Hub for each machine?
Make sure they're prominent and easily edited, so I don't repeat my current mistake, because they're
hard to change. With this move I want a clean slate."*

Three identifiers, and they are not the same kind of thing:

* `machine_name` — a label for people. Editable any time, changes nothing.
* `controller_id` — **the key a controller routes by**. A join registers it as this Mac's roster
  `node_id`, and it used to be derived once from `<hostname>-hub`, frozen on first write, and shown
  only as a hover tooltip. It is now printed, copyable, and editable **while this Mac is not in a
  group** — the only window in which changing it does not break somebody else's roster.
* `site_id` / `site_name` — which site this Mac belongs to, for the gateway's own grouping.

The Hub refuses an identity change from inside a group and names the way out, refuses an id that is
already a Mac on this controller's roster, and refuses a malformed id before writing anything. Each
of those is pinned here, along with the native wiring that puts the three on the screen.
"""
from __future__ import annotations

import contextlib
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from components.hub.backend import control_plane, fleet_nodes, fleet_pull, peers  # noqa: E402

NODE = "office-imac-blue-m1-8-256-hub"
ADDRESS = "http://100.64.0.1:47873"
STUDIOS = [{"id": "image", "modality": "image"}]

API = (PROJECT_ROOT / "native" / "StudioAPI.swift").read_text()
VIEWS = (PROJECT_ROOT / "native" / "StudioViews.swift").read_text()


class MachineIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="studiohub-machine-identity-")
        self.addCleanup(self.temporary.cleanup)
        self.data = Path(self.temporary.name)
        for module in (fleet_nodes, fleet_pull, peers):
            patcher = mock.patch.object(module, "DATA_DIR", self.data)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.settings_file = self.data / "controller_settings.json"
        settings = mock.patch.object(control_plane, "SETTINGS_FILE", self.settings_file)
        settings.start()
        self.addCleanup(settings.stop)
        # The writer lock's own file lives beside the real data directory; a test must not create
        # one there, and none of these tests is about the lock.
        lock = mock.patch.object(control_plane, "settings_writer_lock",
                                 lambda **kwargs: contextlib.nullcontext())
        lock.start()
        self.addCleanup(lock.stop)
        previous = control_plane._settings_cache
        control_plane._settings_cache = None
        self.addCleanup(setattr, control_plane, "_settings_cache", previous)
        # The default this Mac starts from, so the test does not depend on the runner's hostname.
        control_plane.save_settings({"controller_id": "mac-hub", "site_id": "local-site",
                                     "site_name": "Local site"})

    def saved(self) -> dict:
        return json.loads(self.settings_file.read_text())

    # -- editing is allowed on a clean slate ---------------------------------
    def test_a_standalone_mac_can_be_renamed_and_the_change_is_persisted_and_cached(self) -> None:
        control_plane.save_settings({"controller_id": "studio-mini-01", "site_id": "office-site",
                                     "site_name": "Office"})

        self.assertEqual("studio-mini-01", self.saved()["controller_id"])
        self.assertEqual("office-site", self.saved()["site_id"])
        self.assertEqual("Office", self.saved()["site_name"])
        # The process cache, not only the file: a running Hub that read the file back would answer
        # the old id on `/api/hub/controller` and `/api/hub/resources` until the next restart.
        cached = control_plane.load_settings()
        self.assertEqual("studio-mini-01", cached["controller_id"])
        self.assertEqual("office-site", cached["site_id"])

    def test_the_change_is_saved_on_its_own_without_losing_the_rest_of_the_identity(self) -> None:
        """The app sends all three; a caller that sends one must not blank the others."""
        control_plane.save_settings({"controller_id": "studio-mini-01",
                                     "site_id": "local-site", "site_name": "Local site"})
        settings = control_plane.load_settings()
        self.assertEqual("studio-mini-01", settings["controller_id"])
        self.assertEqual("local-site", settings["site_id"])
        self.assertEqual("Local site", settings["site_name"])

    def test_an_identity_change_after_leaving_is_allowed(self) -> None:
        """The whole flow: join, leave, rename, join something else — proven end to end elsewhere."""
        control_plane.save_settings({"fleet_credential_origin": "adopted",
                                     "parent_controller_url": ADDRESS, "controller_id": "mac-hub"})
        with self.assertRaises(ValueError):
            control_plane.save_settings({"controller_id": "studio-mini-01"})

        # What `POST /api/hub/fleet/leave` writes.
        control_plane.save_settings({"fleet_credential_origin": "minted", "role": "standalone",
                                     "parent_controller_url": None})
        control_plane.save_settings({"controller_id": "studio-mini-01", "site_id": "shops-site",
                                     "site_name": "Shops"})
        self.assertEqual("studio-mini-01", self.saved()["controller_id"])

    # -- and refused where it would break a live group -----------------------
    def test_an_identity_change_is_refused_while_this_mac_is_in_a_group(self) -> None:
        control_plane.save_settings({"fleet_credential_origin": "adopted",
                                     "parent_controller_url": ADDRESS, "controller_id": "mac-hub"})

        for field, value, label in (("controller_id", "studio-mini-01", "Machine ID"),
                                    ("site_id", "other-site", "Site ID"),
                                    ("site_name", "Elsewhere", "Site name")):
            with self.subTest(field=field):
                with self.assertRaises(ValueError) as refused:
                    control_plane.save_settings({field: value})
                message = str(refused.exception)
                self.assertIn("in a group", message)
                self.assertIn(label, message, "the refusal did not name the field in words")
                self.assertIn("Leave the fleet", message)
        # Nothing was written by any of the refusals.
        self.assertEqual("mac-hub", self.saved()["controller_id"])
        self.assertEqual("local-site", self.saved()["site_id"])

    def test_the_join_and_promotion_paths_still_save_while_adopted(self) -> None:
        """The rule is about *changing* the identity, not about saving anything at all.

        `POST /api/hub/fleet/adopt` and a promotion both write settings on a Mac whose credential
        origin is `adopted`. A guard that refused every save in that state would break joining — so
        a save that carries the identity unchanged has to go through.
        """
        control_plane.save_settings({"fleet_credential_origin": "adopted",
                                     "parent_controller_url": ADDRESS, "controller_id": "mac-hub"})
        control_plane.save_settings({"fleet_credential_origin": "adopted", "role": "agent",
                                     "parent_controller_url": ADDRESS, "controller_id": "mac-hub"})
        self.assertEqual("agent", self.saved()["role"])
        self.assertEqual(ADDRESS, self.saved()["parent_controller_url"])
        # And a promotion, which mints this Mac's own group credential.
        peers.set_fleet_token("the-group-token-value-123456")
        fleet_nodes.set_node_credential("borrowed")
        control_plane.save_settings({"role": "controller", "controller_id": "mac-hub"})
        self.assertEqual("minted", self.saved()["fleet_credential_origin"])

    def test_a_malformed_id_is_refused_and_nothing_is_written(self) -> None:
        before = self.saved()
        # The Hub's own vocabulary: `[a-z0-9][a-z0-9._-]*`. A space, a name that starts with a
        # separator, an id longer than the hundred characters the ledger and the roster accept, and
        # nothing at all. (A trailing dash is inside that vocabulary and is accepted - pinned here
        # so the day it changes is a deliberate one.)
        for value in ("Studio Mini", "-leading", "a" * 101, "", ".dot-first"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    control_plane.save_settings({"controller_id": value})
        self.assertEqual(before, self.saved())
        self.assertEqual("mac-hub", control_plane.load_settings()["controller_id"])
        self.assertEqual(before, self.saved(), "a refused save rewrote the file")
        control_plane.save_settings({"controller_id": "trailing-"})
        self.assertEqual("trailing-", self.saved()["controller_id"])

    def test_what_was_typed_is_normalized_before_it_is_validated(self) -> None:
        """Case is folded, so a typed `Studio-Mini-01` is accepted as the id it reads as.

        `save_settings` has always lowercased the saved value (`str(...).strip().lower()`), and the
        app re-reads the Hub's answer after a save, so the field shows the id that was actually
        stored rather than the capitals that were typed. Pinned because the field is now editable:
        "typed it with a capital and it silently did nothing" would be the alternative reading.
        """
        control_plane.save_settings({"controller_id": "Studio-Mini-01"})
        self.assertEqual("studio-mini-01", self.saved()["controller_id"])
        self.assertEqual("studio-mini-01", control_plane.load_settings()["controller_id"])
        # An underscore is inside the id vocabulary, so it is kept rather than rewritten.
        control_plane.save_settings({"controller_id": "Studio_Mini"})
        self.assertEqual("studio_mini", self.saved()["controller_id"])

    def test_a_duplicate_of_a_mac_on_this_controllers_roster_is_refused(self) -> None:
        fleet_nodes.join({"node_id": NODE, "name": "Office iMac", "address": ADDRESS,
                          "studios": STUDIOS})
        before = self.saved()

        with self.assertRaises(ValueError) as refused:
            control_plane.save_settings({"controller_id": NODE})

        message = str(refused.exception)
        self.assertIn(NODE, message)
        self.assertIn("Office iMac", message, "the refusal did not say which Mac already holds it")
        self.assertIn("one routed machine", message)
        self.assertEqual(before, self.saved(), "a refused duplicate was written anyway")

    def test_an_id_that_matches_no_mac_in_the_roster_is_allowed(self) -> None:
        fleet_nodes.join({"node_id": NODE, "name": "Office iMac", "address": ADDRESS,
                          "studios": STUDIOS})
        control_plane.save_settings({"controller_id": "studio-mini-01"})
        self.assertEqual("studio-mini-01", self.saved()["controller_id"])

    def test_an_unchanged_id_is_never_checked_against_the_roster(self) -> None:
        """The check is about *routing to a second machine*, not about the id merely existing here.

        A Mac whose id is unchanged must be able to save anything else - a promotion, a role, a
        site — even if its own roster happens to hold a row with that id. A guard that looked the
        id up unconditionally would refuse that Mac every future save, for ever.
        """
        # A node that joined this controller under the same id this Mac already has.
        fleet_nodes.join({"node_id": "mac-hub", "name": "A Mac with my id", "address": ADDRESS,
                          "studios": STUDIOS})

        control_plane.save_settings({"controller_id": "mac-hub", "role": "controller"})

        self.assertEqual("mac-hub", self.saved()["controller_id"])
        self.assertEqual("controller", self.saved()["role"])


class NativeIdentityWiringTests(unittest.TestCase):
    """The three identifiers on the screen, and the two actions beside them.

    The private views cannot be reached from the Swift fixture, so — as the peer-path, Chat and
    metrics wiring suites already do — these pin the source. `native/build.sh` is what proves it
    compiles; this proves the pieces are wired to each other.
    """

    def test_the_machine_id_is_printed_rather_than_kept_in_a_tooltip(self) -> None:
        self.assertNotIn('ValueRow("Node identity"', VIEWS,
                         "the id is back to being a tooltip on a row that shows the name")
        self.assertNotIn('help("Internal id:', VIEWS)
        block = re.search(r"private var machineIdentifiers: some View \{(.*?)\n    \}", VIEWS, re.S)
        self.assertIsNotNone(block, "the identity block is gone")
        body = block.group(1)
        for label in ("Machine ID", "Site"):
            self.assertIn(f'identifierLine("{label}"', body, f"{label} is not printed")
        self.assertEqual(2, body.count("copyLabel:"),
                         "an identifier has no Copy button, or a third one appeared unaccounted for")

    def test_the_editor_appears_only_while_this_mac_is_not_in_a_group(self) -> None:
        block = re.search(r"private var machineIdentifiers: some View \{(.*?)\n    \}", VIEWS, re.S)
        body = block.group(1)
        self.assertIn('fleetCredentialOrigin == "adopted"', body,
                      "the read-only gate no longer reads the credential origin")
        read_only, else_branch = body.split("} else {", 1)
        self.assertIn("machineIdentityEditor", else_branch, "the fields are not the standalone case")
        self.assertIn('Button("Leave this group…")', read_only,
                      "the read-only case offers no way out")
        self.assertIn("confirmingLeave = true", read_only)
        self.assertIn("Read-only while this Mac is in a group", read_only,
                      "the reason it cannot be edited is not stated")

    def test_the_fields_are_validated_by_the_hub_and_the_saved_value_is_re_read(self) -> None:
        self.assertIn("func setMachineIdentity(", API)
        self.assertIn("MachineIdentityRequest(controllerId:", API)
        editor = re.search(r"private var machineIdentityEditor: some View \{(.*?)\n    \}", VIEWS, re.S)
        self.assertIsNotNone(editor)
        self.assertIn("workspace.setMachineIdentity(", editor.group(1))
        # A hint that is a suggestion, not a derivation: nothing generates an id from the hostname.
        self.assertIn("studio-mini-01", editor.group(1))

    def test_reset_the_fleet_is_reachable_and_confirmed(self) -> None:
        self.assertIn("func resetFleet() async", API)
        self.assertIn('["api", "hub", "fleet", "reset"]', API)
        self.assertIn('Button("Reset the fleet…") { confirmingFleetReset = true }', VIEWS)
        self.assertIn('Button("Reset the Fleet", role: .destructive)', VIEWS)
        self.assertIn("Task { await workspace.resetFleet() }", VIEWS)
        self.assertIn('"Reset this controller\'s fleet?"', VIEWS)

    def test_a_node_row_shows_its_id_beside_its_name(self) -> None:
        identity = re.search(r"private func nodeIdentity\(_ row: NativeFleetRow\) -> some View \{(.*?)\n    \}",
                             VIEWS, re.S)
        self.assertIsNotNone(identity, "nodeIdentity is gone")
        self.assertIn('Text("ID \\(nodeId)")', identity.group(1),
                      "a node row shows only the label, so two similarly named Macs cannot be told apart")


if __name__ == "__main__":
    unittest.main()
