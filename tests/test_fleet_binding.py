"""Where the Hub listens, and what it tells its own app to connect to.

Binding beyond loopback is what turns a Mac into a fleet node, and it is the same act that
makes the Hub reachable by something other than this Mac. It is therefore never inferred:
the default stays loopback, and an address that is not a single IPv4 address is refused
rather than guessed at.
"""
from __future__ import annotations

import inspect
from pathlib import Path
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for extra in (PROJECT_ROOT, PROJECT_ROOT / "packaging"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from hub_worker import _bind_host
from voice_worker import LOOPBACK, bind_hosts, run_server


class BindHostTests(unittest.TestCase):
    def test_the_default_is_loopback(self) -> None:
        signature = inspect.signature(run_server)
        self.assertEqual(signature.parameters["host"].default, "127.0.0.1")
        self.assertEqual(_bind_host(""), "127.0.0.1")

    def test_loopback_wildcard_and_a_single_address_are_accepted(self) -> None:
        self.assertEqual(_bind_host("127.0.0.1"), "127.0.0.1")
        self.assertEqual(_bind_host("0.0.0.0"), "0.0.0.0")
        # A Tailscale address is the intended way to scope a Hub to one private network.
        self.assertEqual(_bind_host("100.101.102.103"), "100.101.102.103")
        self.assertEqual(_bind_host("192.168.1.20"), "192.168.1.20")
        self.assertEqual(_bind_host("192.168.1.20,100.101.102.103"),
                         "192.168.1.20,100.101.102.103")
        self.assertEqual(_bind_host("192.168.1.20,0.0.0.0"), "0.0.0.0")

    def test_anything_that_is_not_an_address_is_refused(self) -> None:
        for value in ("localhost", "http://100.1.1.1", "100.1.1.1:8080", "100.1.1.0/24",
                      "::1", "fe80::1", "0.0.0.0 ", "not an address"):
            with self.subTest(value=value):
                # A trailing space is stripped, so that one is accepted; everything else is not.
                if value == "0.0.0.0 ":
                    self.assertEqual(_bind_host(value), "0.0.0.0")
                    continue
                with self.assertRaises(ValueError):
                    _bind_host(value)

    def test_a_mac_can_serve_its_lan_and_its_tailscale_address_at_once(self) -> None:
        hosts = bind_hosts("192.168.1.20,100.101.102.103")
        # Loopback is never dropped: the owner app talks to its own Hub over it, so a Mac
        # that lost it would be unable to reach its own services.
        self.assertEqual(hosts, ["127.0.0.1", "192.168.1.20", "100.101.102.103"])

    def test_loopback_is_always_bound_and_never_duplicated(self) -> None:
        self.assertEqual(bind_hosts(""), ["127.0.0.1"])
        self.assertEqual(bind_hosts("127.0.0.1"), ["127.0.0.1"])
        self.assertEqual(bind_hosts("127.0.0.1,127.0.0.1"), ["127.0.0.1"])
        self.assertEqual(bind_hosts(" 192.168.1.20 , 192.168.1.20 "), ["127.0.0.1", "192.168.1.20"])

    def test_a_wildcard_request_supersedes_the_rest(self) -> None:
        # 0.0.0.0 already answers on every address, so listing others beside it is noise.
        self.assertEqual(bind_hosts("0.0.0.0"), ["0.0.0.0"])
        self.assertEqual(bind_hosts("192.168.1.20,0.0.0.0"), ["0.0.0.0"])

    def test_the_app_always_reaches_its_own_hub_on_loopback(self) -> None:
        self.assertEqual(LOOPBACK, "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
