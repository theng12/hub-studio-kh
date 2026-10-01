"""A remote Mac must never fall out of dispatch eligibility between two peer sweeps.

`dispatch_ready` trusts a peer snapshot for PEER_TTL_S. The monitor fires `peers.refresh` every
poll (5 s), and refresh used to re-read a snapshot only once it was PEER_TTL_S old, so the age
cycled 0..~15 s and the Mac was "not ready" for the last ~3 s of every cycle (about a fifth of
the time): a freed node waited, and the scarcest-first pool shrank and grew back.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from components.hub.backend import monitor, peers  # noqa: E402


class _Response:
    status_code = 200

    def json(self):
        return {"host": {"total_gb": 16.0, "available_gb": 8.0}, "studios": {}}


class _Client:
    reads = 0

    async def get(self, url, **kwargs):
        type(self).reads += 1
        return _Response()


class PeerRefreshCadenceTests(unittest.TestCase):
    STUDIO = {"id": "node-1-image", "machine": "node-1", "modality": "image",
              "host": "10.0.0.5", "hub_port": 47873, "hub_token": "t"}

    def setUp(self):
        self.saved = dict(peers._cache)
        peers._cache.clear()
        self.addCleanup(lambda: (peers._cache.clear(), peers._cache.update(self.saved)))

    def test_a_remote_mac_is_ready_at_every_instant_between_monitor_polls(self) -> None:
        clock = [1000.0]
        _Client.reads = 0
        not_ready = []
        with mock.patch.object(peers.time, "time", lambda: clock[0]):
            for _tick in range(40):                     # ~3.4 minutes of 5.1 s monitor polls
                asyncio.run(peers.refresh([self.STUDIO], _Client()))
                for _step in range(51):                 # sample every 0.1 s until the next poll
                    if not peers.dispatch_ready(self.STUDIO):
                        not_ready.append(round(clock[0], 1))
                    clock[0] += 0.1
        self.assertEqual([], not_ready[:3],
                         f"a remote Mac was not dispatch-ready {len(not_ready)} times between "
                         "peer sweeps")
        self.assertLess(_Client.reads, 40, "the sweep must not read on every poll")

    def test_the_refresh_threshold_leaves_room_before_the_ttl(self) -> None:
        self.assertGreaterEqual(peers.PEER_TTL_S - peers.PEER_REFRESH_S,
                                monitor.POLL_INTERVAL_S + 1.0)


if __name__ == "__main__":
    unittest.main()
