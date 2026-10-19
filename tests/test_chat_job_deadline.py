"""The Chat job deadline is computed from the job, and a quiet job is stopped.

Three defects are pinned here, and each of them is a number that used to be one flat value.

**One: the deadline fits the job.** The lane's old limit was a flat 1,800 s, derived from 30
scenes at ~403 tokens each on an M1. The longest job the fleet plans is a 100-prompt scene pack,
and 100 x 300 tokens at the same measured 12.0 tok/s is 2,500 s of generation before load, the
prompt or the safety margin — so the old wall cut the longest planned job roughly in half. That
is the case `test_a_hundred_prompt_pack_is_not_cut_at_the_old_flat_limit` asserts.

**Two: the expectation comes from the caller, not from `max_tokens`.** `max_tokens` is a ceiling;
using it as the expectation turns every job into a three-hour one, which is the failure the
owner's decision names. A hint, or the fleet's own p90 answer, is the expectation instead.

**Three: a job that has gone quiet is stopped, and one that is still writing is not.** The stall
is the stronger evidence the owner asked for — token-level silence rather than the studio-level
silence `stall.py` deliberately refuses to act on.
"""

from __future__ import annotations

import asyncio
import time
import unittest
from unittest import mock

from components.hub.backend import chat_deadline


def studio(machine: str = "local") -> dict:
    return {"id": "chat", "machine": machine, "modality": "chat"}


def deadline(monitor, *, chip: str = "Apple M1", **kwargs) -> chat_deadline.ChatDeadline:
    """One job's deadline with the Mac's chip pinned, so the arithmetic is the thing under test.

    The chip lookup is a seam of its own (`chat_deadline._host`, which reads the Hub's cached
    peer/host view); pinning it here keeps a run on any Mac asserting the same numbers.
    """
    chat_deadline._chip_cache.clear()
    with mock.patch.object(chat_deadline, "_host", lambda _studio: {"chip": chip}):
        return chat_deadline.chat_deadline(monitor, studio(), **kwargs)


class FakeMonitor:
    """The Hub's polled view, in the shape `progress_signature` reads it."""

    def __init__(self, *, support: str | None = "available") -> None:
        self.status: dict = {"chat": {}}
        if support is not None:
            self.status["chat"]["activity_support"] = support

    def activity(self, *, job_id: str = "job-1", written: int = 0) -> None:
        self.status["chat"]["activity_support"] = "available"
        self.status["chat"]["activity"] = {
            "schema": "kh-studio.activity.v1", "studio": "chat", "observed_at": time.time(),
            "active": {"id": job_id, "state": "running", "progress_stage": "generating",
                       "generation_tokens": written},
            "latest": None,
        }


def body(prompt_chars: int) -> bytes:
    return ('{"model": "qwen3-5-9b-4bit", "messages": [{"role": "user", "content": "'
            + ("x" * prompt_chars) + '"}]}').encode()


class DeadlineFitsTheJobTests(unittest.TestCase):
    FLOOR = 1800.0

    def test_a_hundred_prompt_pack_is_not_cut_at_the_old_flat_limit(self) -> None:
        """The longest planned job, on the slowest measured Mac, with no measurement to hand.

        100 scene prompts x 300 tokens = 30,000 expected output tokens; the pack's own prompt is
        stated at 12,000 tokens. At the M1's stated 12.0 tok/s that is 2,500 s of decode alone.
        """
        computed = deadline(
            FakeMonitor(), request_body=body(12_000 * 4), hint=30_000, floor_s=self.FLOOR)
        self.assertGreater(computed.seconds, self.FLOOR,
                           "a 100-prompt pack must be given longer than the flat lane limit")
        self.assertLessEqual(computed.seconds, chat_deadline.CEILING_S)
        # The old code answered `standalone.deadline_floor_seconds("chat")` — 1,800 — for this job.
        self.assertGreater(computed.seconds, 3000.0)
        self.assertIn("30,000 expected output tokens", computed.note)
        self.assertIn("68 minutes", computed.note)

    def test_a_median_sized_answer_still_gets_the_lane_floor(self) -> None:
        """The floor is what keeps this change from cutting anything that fits today: the fleet's
        median answer (3,415 tokens) needs ~285 s of decode on an M1, far under 30 minutes."""
        computed = deadline(FakeMonitor(), request_body=body(4_000), hint=3_415,
                            floor_s=self.FLOOR)
        self.assertEqual(computed.seconds, self.FLOOR)
        self.assertIn("floor", computed.note)

    def test_the_callers_hint_beats_the_p90_default(self) -> None:
        small = deadline(FakeMonitor(), request_body=body(400), hint=200, floor_s=self.FLOOR)
        large = deadline(FakeMonitor(), request_body=body(400), hint=40_000, floor_s=self.FLOOR)
        self.assertGreater(large.seconds, small.seconds)

    def test_max_tokens_is_never_the_expectation(self) -> None:
        """A ceiling is not an expectation. `max_tokens: 128000` with no hint must not produce a
        deadline sized for 128,000 tokens; the p90 of real answers is the expectation instead."""
        request = (b'{"model": "qwen3-5-9b-4bit", "max_tokens": 128000, "messages": '
                   b'[{"role": "user", "content": "write a scene"}]}')
        computed = deadline(FakeMonitor(), request_body=request, floor_s=self.FLOOR)
        self.assertEqual(
            chat_deadline.expected_output_tokens(None)[0],
            chat_deadline.DEFAULT_EXPECTED_OUTPUT_TOKENS)
        # 128,000 tokens at the M1's 12.0 tok/s is 10,667 s of decode, which would cap the
        # deadline at the ceiling. The p90 expectation must land well below that.
        self.assertLess(computed.seconds, chat_deadline.CEILING_S)

    def test_the_ceiling_bounds_a_job_that_asks_for_hours(self) -> None:
        computed = deadline(FakeMonitor(), request_body=body(400_000), hint=400_000,
                            floor_s=self.FLOOR)
        self.assertEqual(computed.seconds, chat_deadline.CEILING_S)
        self.assertIn("ceiling", computed.note)

    def test_a_measured_rate_on_this_mac_beats_the_chip_estimate(self) -> None:
        """The worker's own stated rate, already polled into the Hub, is the measurement the
        deadline should use — a 30 tok/s M4 is not given an M1's 12.0."""
        monitor = FakeMonitor()
        monitor.status["chat"]["activity"] = {
            "active": None,
            "latest": {"id": "job-0", "state": "done", "prompt_tps": 240.0,
                       "generation_tps": 30.0},
        }
        measured = deadline(monitor, chip="Apple M1", request_body=body(4_000), hint=30_000,
                            floor_s=self.FLOOR)
        stated = deadline(FakeMonitor(), chip="Apple M1", request_body=body(4_000), hint=30_000,
                          floor_s=self.FLOOR)
        self.assertLess(measured.seconds, stated.seconds)
        self.assertIn("measured rate", measured.note)

    def test_the_hint_header_is_read_off_the_request(self) -> None:
        self.assertEqual(chat_deadline.hint_from("30000"), 30_000)
        for junk in ("", "many", 0, -5, None, True, 1.5):
            self.assertIsNone(chat_deadline.hint_from(junk), f"{junk!r} is not a token count")

    def test_the_chip_lookup_is_paid_for_once_a_minute_not_once_a_request(self) -> None:
        """`broker._host_for_studio` spawns `sysctl`, and this runs before every completion on a
        Mac that has no measured rate yet. The chip cannot change under a running Hub."""
        chat_deadline._chip_cache.clear()
        lookups = []

        def host(_studio):
            lookups.append(1)
            return {"chip": "Apple M4"}

        with mock.patch.object(chat_deadline, "_host", host):
            for _ in range(5):
                computed = chat_deadline.chat_deadline(
                    FakeMonitor(), studio(), request_body=body(400), hint=1_000,
                    floor_s=self.FLOOR)
        self.assertEqual(len(lookups), 1, "the chip was looked up once per request")
        self.assertIn("M4", computed.note)
        chat_deadline._chip_cache.clear()

    def test_a_pack_with_scene_ids_sizes_itself_without_a_hint(self) -> None:
        expected, note = chat_deadline.expected_output_tokens(None, scene_count=10)
        self.assertEqual(expected, 3_000)
        self.assertIn("10 scene prompts", note)


class StallStopTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_job_that_stops_writing_is_stopped_within_the_window(self) -> None:
        """No new token, so the wait ends — and it ends *within* the window, not a poll after."""
        monitor = FakeMonitor()
        monitor.activity(written=0)
        started = time.monotonic()

        async def never_answers() -> str:
            await asyncio.sleep(60)
            return "too late"

        with self.assertRaises(chat_deadline.ChatStalled) as raised:
            await chat_deadline.await_with_progress(
                monitor, "chat", never_answers(), stall_after=0.3)
        elapsed = time.monotonic() - started
        self.assertGreaterEqual(elapsed, 0.3)
        self.assertLess(elapsed, 2.0)
        self.assertEqual(str(raised.exception), "stopped: no new text for 1 second")
        self.assertNotIn("limit", str(raised.exception))

    async def test_a_job_that_is_still_writing_is_not_stopped(self) -> None:
        """Every new token restarts the window, so a long job that is working is left alone."""
        monitor = FakeMonitor()
        monitor.activity(written=0)

        async def writing_then_answers() -> str:
            for written in range(1, 9):
                await asyncio.sleep(0.1)
                monitor.activity(written=written * 50)
            return "done"

        result = await chat_deadline.await_with_progress(
            monitor, "chat", writing_then_answers(), stall_after=0.3)
        self.assertEqual(result, "done")

    async def test_a_new_job_id_restarts_the_window(self) -> None:
        """A pack retried on the same Mac is a new job, and its first token is not measured
        against the previous job's silence."""
        monitor = FakeMonitor()
        monitor.activity(job_id="job-old", written=9_000)

        async def new_job() -> str:
            await asyncio.sleep(0.25)
            monitor.activity(job_id="job-new", written=0)
            await asyncio.sleep(0.25)
            monitor.activity(job_id="job-new", written=10)
            return "second try"

        result = await chat_deadline.await_with_progress(
            monitor, "chat", new_job(), stall_after=0.3)
        self.assertEqual(result, "second try")

    async def test_a_studio_that_never_reported_is_not_read_as_progress(self) -> None:
        self.assertIsNone(chat_deadline.progress_signature(FakeMonitor(support=None), "chat"))

    async def test_a_worker_with_no_activity_report_is_never_stopped(self) -> None:
        """An unseeing Hub is not a quiet worker. A Chat worker too old to publish an activity
        snapshot — or one whose reporter is failing — must not have its jobs stopped for silence
        the Hub cannot actually observe; the deadline is the only bound there."""
        monitor = FakeMonitor(support="error")

        async def slow_but_working() -> str:
            await asyncio.sleep(0.9)
            return "answered"

        result = await chat_deadline.await_with_progress(
            monitor, "chat", slow_but_working(), stall_after=0.3)
        self.assertEqual(result, "answered")

    def test_the_stall_reason_and_the_deadline_reason_are_different_words(self) -> None:
        stall = chat_deadline.stall_reason(300.0)
        deadline = chat_deadline.deadline_reason(
            chat_deadline.ChatDeadline(seconds=3600.0, note="60 minutes: ..."))
        self.assertEqual(stall, "stopped: no new text for 5 minutes")
        self.assertNotEqual(stall, deadline)
        self.assertIn("no answer within", deadline)

    def test_the_stall_window_is_configurable_and_refuses_a_zero(self) -> None:
        import os
        original = os.environ.get("STUDIOHUB_CHAT_STALL_SECONDS")
        try:
            os.environ["STUDIOHUB_CHAT_STALL_SECONDS"] = "60"
            self.assertEqual(chat_deadline.stall_after_seconds(), 60.0)
            for junk in ("0", "-1", "soon", "nan", "inf"):
                os.environ["STUDIOHUB_CHAT_STALL_SECONDS"] = junk
                self.assertEqual(chat_deadline.stall_after_seconds(),
                                 chat_deadline.STALL_AFTER_DEFAULT_S, junk)
        finally:
            if original is None:
                os.environ.pop("STUDIOHUB_CHAT_STALL_SECONDS", None)
            else:
                os.environ["STUDIOHUB_CHAT_STALL_SECONDS"] = original


if __name__ == "__main__":
    unittest.main()
