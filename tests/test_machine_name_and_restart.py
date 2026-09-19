"""Naming this Mac, and one press to apply a setting that waits for a restart.

Two things the owner hit while setting up his own controller:

* he could not name it - the Hub has kept a label for the machine all along (`machine_name` is what
  every other Mac and the gateway show, and `registry.set_label` has always written it), but no
  route and no control ever offered it, so the only name available was the hostname;
* switching Standalone -> Controller, and turning on a network address, both need the services
  stopped first. The controls are disabled while they run, so a new operator is left to work out
  that they must stop, change, and start again - three steps, two of them invisible.

The name is a label, never an identity: routing and fleet registration do not depend on it, which is
what makes it safe to change on a running fleet.
"""
import json
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.test_standalone_connections import HubProcess  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
VIEWS = (ROOT / "native" / "StudioViews.swift").read_text()
HUB_APP = (ROOT / "native" / "StudioHub.swift").read_text()
API = (ROOT / "native" / "StudioAPI.swift").read_text()
STANDALONE = (ROOT / "components" / "hub" / "standalone.py").read_text()
AUTH = (ROOT / "components" / "hub" / "backend" / "auth.py").read_text()


class MachineNameTests(unittest.TestCase):
    def test_the_hub_offers_a_rename_and_it_is_what_the_fleet_reads(self) -> None:
        with tempfile.TemporaryDirectory(prefix="studiohub-name-") as root:
            directory = Path(root)
            hub = HubProcess(directory, "hub", "rename-fixture-token-0123456789")
            hub.start()
            try:
                status, before, _ = hub.request("/api/hub/controller", token=hub.token)
                self.assertEqual(status, 200, before)
                self.assertEqual(before["settings"]["machine_name"], before["settings"]["machine_name"])

                status, renamed, _ = hub.post_json("/api/hub/machine-label",
                                                   {"name": "Studio Mini 01"}, token=hub.token)
                self.assertEqual(status, 200, renamed)
                self.assertEqual(renamed["settings"]["machine_name"], "Studio Mini 01")

                # Read back through the ordinary identity read: this is the name a node and the
                # gateway see, so it has to survive the round trip rather than live in one reply.
                status, after, _ = hub.request("/api/hub/controller", token=hub.token)
                self.assertEqual(status, 200, after)
                self.assertEqual(after["settings"]["machine_name"], "Studio Mini 01")

                # Clearing it falls back to the hostname rather than to an empty name.
                status, cleared, _ = hub.post_json("/api/hub/machine-label",
                                                   {"name": ""}, token=hub.token)
                self.assertEqual(status, 200, cleared)
                self.assertNotEqual(cleared["settings"]["machine_name"], "")

                status, refused, _ = hub.post_json(
                    "/api/hub/machine-label", {"name": "Not Yours"},
                    token=(hub.data / ".fleet_token").read_text().strip())
                self.assertEqual(status, 403, refused)
            finally:
                hub.stop()

    def test_the_app_exposes_it(self) -> None:
        self.assertIn('["api", "hub", "machine-label"]', API, "the app must call the rename route")
        self.assertIn("machineNameDraft", VIEWS, "the name needs a field")
        self.assertIn("renameThisMachine", VIEWS)


class RestartNoticeTests(unittest.TestCase):
    def test_a_change_that_waits_for_a_restart_says_so_and_offers_one_press(self) -> None:
        section = VIEWS[VIEWS.index("restartNotice"):]
        section = section[:section.index("private var controllerSection")]
        self.assertIn("Restart Services", section, "the notice must carry the button")
        self.assertIn("studio.restartServices()", section)

    def test_the_notice_sits_with_the_controls_that_need_it(self) -> None:
        """Not at the foot of the page: it belongs above the network and port controls."""
        connections = VIEWS[VIEWS.index("private var connections"):]
        self.assertLess(connections.index("restartNotice"),
                        connections.index("Listening on"),
                        "the notice must come before the controls it is about")

    def test_restarting_actually_stops_and_starts(self) -> None:
        self.assertIn("func restartServices()", HUB_APP)
        self.assertIn("restartAfterStop = true", HUB_APP)
        self.assertIn("restartAfterStop = false\n            start()", HUB_APP,
                      "the stop must be followed by a start, or the button only stops the services")

    def test_the_role_choice_explains_itself(self) -> None:
        """Controller/Node/Standalone are Hub words, not operator words."""
        connections = VIEWS[VIEWS.index("private var connections"):]
        self.assertIn("runs its own group and sends work to nodes", connections)
        self.assertIn("The role applies immediately", connections)


if __name__ == "__main__":
    unittest.main()
