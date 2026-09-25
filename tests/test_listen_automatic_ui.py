"""The Connections page's "follow this Mac's networks" setting, and the manual choice that ends it.

The owner's ask: *"i keep forgetting to turn on tailscale. i want it already the default on."*
`ShellConfiguration.listenHostAutomatic` is the preference (absent means automatic), and
`ShellConfiguration.automaticListenHost(keeping:)` is the recomputation — this Mac's current private
and Tailscale addresses, added to whatever was already saved, so automatic can never remove a way in
that worked. The Kit behaviours are pinned in `tests/native/main.swift`, which compiles the real
configuration type.

What this module pins is the *page*: a setting that changes by itself has to be visible and
switchable, and — the defect that would be invisible until the next launch — a manual address
choice has to end automatic mode. Ticking Tailscale off and having the next launch put it back is
an edit that silently undoes itself, which is worse than either default.
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIEWS = (ROOT / "native" / "StudioViews.swift").read_text()


def body(of: str, marker: str, stop: str = "\n    }") -> str:
    text = "\n".join(line.split("//", 1)[0] for line in of.splitlines())
    start = text.index(marker)
    end = text.find(stop, start + len(marker))
    return text[start:end if end >= 0 else len(text)]


class ListenAutomaticUITests(unittest.TestCase):
    def test_the_setting_is_visible_and_switchable(self) -> None:
        connections = body(VIEWS, "private var fleetSection: some View")
        self.assertIn('Toggle("Follow this Mac\'s networks automatically"', connections,
                      "the page must show the setting that changes the listen list by itself")
        self.assertIn("studio.configuration.listenHostAutomatic", connections,
                      "the checkbox must read the preference, not a copy of it")
        self.assertIn("setListenAutomatic(", connections,
                      "the checkbox must write the preference through the one setter")

    def test_the_page_says_what_automatic_means(self) -> None:
        """A setting that changes by itself must say so, in one line, where it is set."""
        connections = body(VIEWS, "private var fleetSection: some View")
        self.assertIn(
            "Loopback and every private or Tailscale address this Mac has, recomputed each time "
            "it starts. Never a public address.",
            connections, "the automatic list must be explained where the operator can read it")
        self.assertIn("listenHostAutomatic {", connections,
                      "the sentence must appear only while automatic is on")

    def test_a_manual_choice_ends_automatic_mode(self) -> None:
        """The defect this exists for: an untick that comes back on the next launch."""
        connections = body(VIEWS, "private var fleetSection: some View")
        self.assertNotIn("studio.setListenHosts(", connections,
                         "an address tick must not write the list without ending automatic mode")
        self.assertEqual(connections.count("pinListenHosts("), 2,
                         "both the per-address ticks and the wildcard must pin the choice")
        pin = body(VIEWS, "private func pinListenHosts(")
        self.assertIn("listenHostAutomatic = false", pin,
                      "a manual choice must turn automatic off")
        self.assertIn("setListenHosts(hosts)", pin,
                      "the ticked addresses must be saved with the choice")
        self.assertIn("saveConfiguration()", body(VIEWS, "private func setListenAutomatic("),
                      "the setting must persist, or the next launch cannot honour it")

    def test_turning_automatic_on_recomputes_without_removing_an_address(self) -> None:
        setter = body(VIEWS, "private func setListenAutomatic(")
        self.assertIn("ShellConfiguration.automaticListenHost(", setter,
                      "automatic must be the producer's own recomputation, not a list here")
        self.assertIn("if automatic {", setter,
                      "turning it off must pin exactly what is on screen")
        self.assertNotIn("LocalAddresses.current()", setter,
                         "the address list is ShellConfiguration's answer, not this page's")

    def test_the_resolved_list_and_its_warning_still_render(self) -> None:
        """The existing facts stay: what this Mac answers on, and who can reach it."""
        connections = body(VIEWS, "private var fleetSection: some View")
        self.assertIn('LabeledContent("Listening on", value: listenTitle(studio.configuration.listenHost))',
                      connections, "the resolved list must still be shown as a fact")
        self.assertIn("listenWarning", connections, "the who-can-reach-it sentence must stay")
        self.assertIn("LocalAddresses.current()", connections,
                      "the addresses this Mac has right now must still be listed")
        # An address that is no longer present is named and skipped for the launch, never used to
        # poison the service set.
        title = body(VIEWS, "private func listenTitle(")
        self.assertIn("This Mac only", title)
        self.assertIn("Every network this Mac is on", title)


if __name__ == "__main__":
    unittest.main()
