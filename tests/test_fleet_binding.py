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
from voice_worker import ready_host, run_server


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

    def test_the_client_address_follows_the_bind(self) -> None:
        # A wildcard bind still answers on loopback, so the app keeps using it.
        self.assertEqual(ready_host("0.0.0.0"), "127.0.0.1")
        self.assertEqual(ready_host("127.0.0.1"), "127.0.0.1")
        # A scoped bind is only reachable at the address it bound.
        self.assertEqual(ready_host("100.101.102.103"), "100.101.102.103")


if __name__ == "__main__":
    unittest.main()
