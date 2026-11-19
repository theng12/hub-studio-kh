"""The render relay must not cut off a node's own 600 s encode probe at 60 s."""
import asyncio
import unittest
from unittest.mock import patch

import httpx

from components.hub.backend import fleet_nodes

NODE = "node-a"
JOB = "11111111-2222-3333-4444-555555555555"
SLOW_ANSWER_S = 75.0


class Clock:
    def __init__(self):
        self.now = 0.0

    def advance(self, seconds):
        self.now += seconds


class FakeResponse:
    status_code = 200
    headers = {"content-length": "2"}

    async def aiter_bytes(self, chunk_size):
        yield b"ok"


class FakeStream:
    def __init__(self, clock, delay, timeout):
        self.clock, self.delay, self.timeout = clock, delay, timeout

    async def __aenter__(self):
        # A fake transport clock: charge virtual seconds, then enforce the caller's read ceiling.
        self.clock.advance(self.delay)
        if self.delay > self.timeout.read:
            raise httpx.ReadTimeout("the node answered after the relay's ceiling")
        return FakeResponse()

    async def __aexit__(self, *_exc):
        return False


class FakeClient:
    def __init__(self, delay):
        self.clock = Clock()
        self.delay = delay
        self.timeouts = []

    def stream(self, method, url, **kwargs):
        self.timeouts.append(kwargs["timeout"])
        return FakeStream(self.clock, self.delay, kwargs["timeout"])

    async def aclose(self):
        pass


def relay(client, operation, identity=""):
    with patch.object(fleet_nodes, "_read", return_value={
            NODE: {"address": "http://node.invalid"}}):
        return asyncio.run(fleet_nodes.relay_final_render(
            NODE, operation, identity=identity, client=client))


class RenderRelayCeilingTests(unittest.TestCase):
    def test_encode_probe_survives_a_75_second_answer(self):
        client = FakeClient(SLOW_ANSWER_S)
        answer = relay(client, "encode_probe_post")
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer.body, b"ok")
        self.assertEqual(client.clock.now, SLOW_ANSWER_S)
        self.assertEqual(client.timeouts[0].read, 660.0)
        self.assertEqual(client.timeouts[0].connect, 4.0)

    def test_status_read_is_still_cancelled_at_60_seconds(self):
        client = FakeClient(SLOW_ANSWER_S)
        with self.assertRaises(fleet_nodes.StartupRelayError) as caught:
            relay(client, "status", JOB)
        self.assertEqual(client.timeouts[0].read, 60.0)
        self.assertEqual(client.clock.now, SLOW_ANSWER_S)
        self.assertEqual(caught.exception.status, 502)
        self.assertEqual(caught.exception.body["code"], "NODE_UNREACHABLE")

    def test_unlisted_operation_keeps_the_old_bound(self):
        client = FakeClient(SLOW_ANSWER_S)
        with self.assertRaises(fleet_nodes.StartupRelayError):
            relay(client, "cancel", JOB)
        self.assertEqual(client.timeouts[0].read, 60.0)


if __name__ == "__main__":
    unittest.main()
