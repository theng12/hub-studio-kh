"""The bind is a fact this Hub publishes, not something a caller infers from a timeout.

GatewayKH asked for it in as many words: *"Say the bind over /api/hub/settings (read + write) —
then the gateway can report 'bound to loopback' as a fact and fix any Mac that's already
reachable."* The owner's version is the reason it matters: a Mac that came up loopback-only is
unreachable to the whole fleet, and the only symptom today is work that never arrives.

It is published from **one** helper and read by two routes. Two routes with two answers is the
defect this file exists to prevent, so the agreement is asserted rather than assumed.
"""
from __future__ import annotations

import os
from pathlib import Path
import unittest
from unittest import mock

from components.hub import standalone


ROOT = Path(__file__).resolve().parents[1]
# The owner's own Mac, read from its `app-preferences.json`: loopback + LAN + tailnet.
OWNER = "127.0.0.1,192.168.18.248,100.126.154.54"


class BindFactsTests(unittest.TestCase):
    def facts(self, host: str, port: str = "47873") -> dict:
        with mock.patch.dict(os.environ, {"STUDIOHUB_BOUND_HOST": host,
                                          "STUDIOHUB_BOUND_PORT": port}, clear=False):
            return standalone._bind_facts()

    def test_loopback_only_says_so_in_words_a_caller_can_act_on(self) -> None:
        facts = self.facts("127.0.0.1")
        self.assertTrue(facts["known"])
        self.assertTrue(facts["loopback_only"], facts)
        self.assertFalse(facts["reachable_off_this_mac"], facts)
        self.assertEqual(facts["tailnet"], [])
        self.assertEqual(facts["port"], 47873)

    def test_a_mixed_mac_is_classified_the_way_the_shell_classifies_it(self) -> None:
        facts = self.facts(OWNER)
        self.assertEqual(facts["classes"],
                         {"127.0.0.1": "loopback", "192.168.18.248": "lan",
                          "100.126.154.54": "tailnet"})
        self.assertFalse(facts["loopback_only"])
        self.assertTrue(facts["reachable_off_this_mac"])
        self.assertEqual(facts["tailnet"], ["100.126.154.54"])
        self.assertFalse(facts["wildcard"])

    def test_the_wildcard_is_named_rather_than_left_to_look_like_an_address(self) -> None:
        facts = self.facts("0.0.0.0")
        self.assertTrue(facts["wildcard"], facts)
        self.assertEqual(facts["classes"]["0.0.0.0"], "other",
                         "the wildcard is not a private address; it is the absence of a scope")

    def test_a_hub_started_without_the_launcher_says_it_does_not_know(self) -> None:
        """Absent is a different answer from loopback, and must not be rendered as one."""
        with mock.patch.dict(os.environ, {}, clear=True):
            facts = standalone._bind_facts()
        self.assertFalse(facts["known"])
        self.assertNotIn("loopback_only", facts)
        self.assertIn("launcher", facts["reason"])

    def test_a_hostname_is_never_guessed_at(self) -> None:
        self.assertEqual(self.facts("mac-studio.local")["classes"]["mac-studio.local"], "other")


class BothRoutesAgreeTests(unittest.TestCase):
    """One definition, two readers — asserted against the routes themselves.

    Read from the source rather than by standing a Hub up: the property being pinned is *where
    the answer comes from*, and a live call would only prove the two happened to match on the
    machine that ran it. `BindFactsTests` above establishes that the one definition is right.
    """

    SOURCE = (ROOT / "components" / "hub" / "standalone.py").read_text()

    def _route_body(self, marker: str) -> str:
        body = self.SOURCE[self.SOURCE.index(marker):]
        return body[:body.index("\n    @app.")]

    def test_health_and_settings_both_read_the_one_definition(self) -> None:
        for marker in ("def health() -> dict[str, Any]:",
                       'def settings() -> dict[str, Any]:'):
            with self.subTest(route=marker):
                self.assertIn("_bind_facts()", self._route_body(marker),
                              f"{marker} must publish the bind from the shared helper; a second "
                              f"derivation is a second answer")

    def test_there_is_exactly_one_definition(self) -> None:
        self.assertEqual(self.SOURCE.count("def _bind_facts()"), 1)
        # It is read by the two routes above and nowhere else reimplemented: any other
        # classification of an address in this file would be a second set of rules.
        self.assertEqual(self.SOURCE.count("is_loopback"), 1)
        self.assertEqual(self.SOURCE.count("64 <= second <= 127"), 1)


if __name__ == "__main__":
    unittest.main()
