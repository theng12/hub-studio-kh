"""The app's peer paths must be modalities the Hub actually routes.

The Hub's fleet surface is `/studio/<modality>/<path>`, where the first segment selects the local
worker. The app called `/studio/subtitle/...` — the worker's *name*, not its *modality* — and the
Hub answered `404 {"detail":"unknown studio"}`, so the Transcribe button could never work.

Nothing caught it. The Swift contract fixture had been written against the app's own spelling, so
the app and its fixture agreed with each other and disagreed with the Hub; the gate passed while
the feature was dead. These tests read the routing table out of the Hub source and compare it with
every studio path the app builds, so a rename on either side fails here instead of in the
operator's hands.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HUB = ROOT / "components" / "hub" / "standalone.py"
NATIVE = ROOT / "native"
FIXTURE = ROOT / "tests" / "native" / "api_main.swift"


def hub_modalities() -> set[str]:
    """The modalities the Hub's peer surface routes, read from its own source."""
    text = HUB.read_text()
    block = re.search(r"WORKER_FOR_MODALITY\s*=\s*\{(.*?)\}", text, re.S)
    assert block, "WORKER_FOR_MODALITY not found in the Hub"
    return set(re.findall(r'"([a-z_]+)"\s*:', block.group(1)))


def studio_paths(text: str) -> set[str]:
    """Every first segment after /studio/ in a native source file, however it is written."""
    found = set()
    # get(["studio", "<modality>", ...]) and makeRequest(["studio", "<modality>", ...])
    for match in re.finditer(r'"studio"\s*,\s*"([A-Za-z_]+)"', text):
        found.add(match.group(1))
    return found


class PeerPathContractTests(unittest.TestCase):
    def test_the_routing_table_is_what_the_app_expects(self):
        self.assertEqual(hub_modalities(), {"image", "voice", "transcription"})

    def test_every_studio_path_in_the_app_names_a_routed_modality(self):
        routed = hub_modalities()
        checked = 0
        for source in sorted(NATIVE.glob("*.swift")):
            used = studio_paths(source.read_text())
            checked += len(used)
            for modality in used:
                self.assertIn(
                    modality, routed,
                    f"{source.name} asks the Hub for /studio/{modality}/... but the Hub routes "
                    f"{sorted(routed)}; it will answer 404 'unknown studio'")
        # If the app's paths are ever spelled in a way this test cannot see, it must say so
        # rather than pass quietly - a gate that matches nothing is not a gate.
        self.assertGreater(checked, 0, "no studio paths were found to check at all")

    def test_the_swift_fixture_refuses_a_studio_the_hub_does_not_route(self):
        """The fixture must be able to fail, or it is not a gate.

        It is the only place the app's requests are checked against a Hub-shaped server, so it has
        to refuse an unrouted modality the way the Hub does.
        """
        text = FIXTURE.read_text()
        self.assertIn("unknown studio", text)
        guard = re.search(r'hubRoutedModalities[^=]*=\s*\[(.*?)\]', text, re.S)
        self.assertIsNotNone(guard, "the fixture no longer refuses unknown studios")
        self.assertEqual(set(re.findall(r'"([a-z_]+)"', guard.group(1))), hub_modalities())

    def test_subtitle_is_never_used_as_a_peer_modality_again(self):
        """The exact regression: the worker's name is not the modality."""
        for source in list(NATIVE.glob("*.swift")):
            self.assertNotIn(
                '"studio", "subtitle"', source.read_text(),
                f"{source.name} uses the worker name where the Hub expects the modality")
        self.assertNotIn('"/studio/subtitle/', FIXTURE.read_text())


if __name__ == "__main__":
    unittest.main()
