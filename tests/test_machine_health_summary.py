"""A Mac's health on the Fleet page must summarise its Studios, not pick one.

`fleet_overview` iterated the registry, skipped every machine it had already seen, and reported the
first studio's status as the machine's. A Mac running Voice and Image with Image down therefore read
"up" if Voice happened to be first - and read "down" while Voice was fine if it did not. The page
answers a question about the Mac.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from components.hub import standalone  # noqa: E402


class MachineHealthTests(unittest.TestCase):
    def test_every_studio_up_reads_up(self):
        self.assertEqual(standalone.machine_health([("Voice", "up"), ("Image", "up")]), ("up", None))

    def test_one_studio_down_reads_degraded_and_names_it(self):
        health, detail = standalone.machine_health([("Voice", "up"), ("Subtitle", "down")])
        self.assertEqual(health, "degraded")
        self.assertEqual(detail, "Subtitle is not answering")

    def test_every_studio_down_reads_down_and_names_them(self):
        health, detail = standalone.machine_health([("Voice", "down"), ("Image", "down")])
        self.assertEqual(health, "down")
        self.assertIn("Image", detail)
        self.assertIn("Voice", detail)
        self.assertIn("are not answering", detail)

    def test_unknown_statuses_alone_read_unknown_not_down(self):
        """A Hub that has not run its first health check must not report a Mac as sick."""
        self.assertEqual(standalone.machine_health([("Voice", "unknown")]), ("unknown", None))
        self.assertEqual(standalone.machine_health([]), ("unknown", None))

    def test_a_studio_still_starting_does_not_make_the_mac_look_down(self):
        health, detail = standalone.machine_health([("Voice", "up"), ("Image", "unknown")])
        self.assertEqual(health, "up")
        self.assertIsNone(detail)

    def test_the_route_uses_it(self):
        source = (Path(__file__).resolve().parents[1] / "components/hub/standalone.py").read_text()
        self.assertIn("health, detail = machine_health([", source)
        self.assertNotIn('"health": state.get("status", "unknown")', source,
                         "the first studio's status must not be the machine's health")


if __name__ == "__main__":
    unittest.main()
