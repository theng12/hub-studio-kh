"""The queue clock: a wait nothing else can end is ended, retryable, with its minutes named.

Owner rule, 2026-11-15: every wait is bounded by its own ceiling, and past the bound it fails
retryable with the reason. The machine-reservation wait had no clock: a refusal restored the
item's tries every time, so MAX_TRIES could never end it and eleven jobs sat 137 minutes on
mac-1200 (2026-11-18).
"""
import unittest

from components.hub.backend import broker


def batch(*items):
    return {"id": "b1", "modality": "image", "items": list(items)}


class QueueClock(unittest.TestCase):
    def test_a_wait_past_its_ceiling_ends_retryable_with_the_minutes_named(self) -> None:
        now = 1_000_000.0
        item = {"id": "i1", "state": "queued", "retry_at": None,
                "queue_wait_started_at": now - 3_600.0}
        ended, changed = broker.expire_queue_wait(batch(item), now, ceiling=2_700.0)
        self.assertEqual((ended, changed), (1, 1))
        self.assertEqual(item["state"], "error")
        self.assertEqual(item["error_code"], "QUEUE_WAIT_EXCEEDED")
        self.assertIs(item["retryable"], True)
        self.assertIn("60 minutes", item["error"])
        self.assertIn("45 minutes", item["error"])
        self.assertIsNone(item["retry_at"])
        self.assertEqual(item["finished_at"], now)

    def test_an_item_seen_for_the_first_time_is_stamped_not_ended_or_saved(self) -> None:
        now = 5.0
        item = {"id": "i2", "state": "queued"}
        ended, changed = broker.expire_queue_wait(batch(item), now, ceiling=10.0)
        # Stamped, but not a reason to write: the stamp only starts a clock, and a write here
        # would be the dispatcher saving on every pass (see the dispatch-trace regression).
        self.assertEqual((ended, changed), (0, 0))
        self.assertEqual(item["queue_wait_started_at"], now)
        self.assertEqual(item["state"], "queued")

    def test_a_wait_inside_its_ceiling_is_left_alone(self) -> None:
        now = 1_000.0
        item = {"id": "i3", "state": "queued", "queue_wait_started_at": now - 60.0}
        self.assertEqual(broker.expire_queue_wait(batch(item), now, ceiling=600.0), (0, 0))
        self.assertEqual(item["state"], "queued")

    def test_a_scheduled_retry_is_not_the_queue_clock_s_business(self) -> None:
        now = 1_000.0
        item = {"id": "i4", "state": "queued", "retry_at": now + 5.0,
                "queue_wait_started_at": now - 99_999.0}
        self.assertEqual(broker.expire_queue_wait(batch(item), now, ceiling=600.0), (0, 0))
        self.assertEqual(item["state"], "queued")

    def test_running_and_finished_items_are_never_touched(self) -> None:
        now = 1_000.0
        for state in ("running", "done", "error", "cancelled", "uncertain"):
            item = {"id": state, "state": state, "queue_wait_started_at": now - 99_999.0}
            self.assertEqual(broker.expire_queue_wait(batch(item), now, ceiling=600.0), (0, 0),
                             state)
            self.assertEqual(item["state"], state)

    def test_the_ceiling_defaults_to_the_module_setting(self) -> None:
        now = 1_000.0
        inside = {"id": "a", "state": "queued",
                  "queue_wait_started_at": now - broker.QUEUE_WAIT_CEILING_S + 1}
        self.assertEqual(broker.expire_queue_wait(batch(inside), now), (0, 0))
        past = {"id": "b", "state": "queued",
                "queue_wait_started_at": now - broker.QUEUE_WAIT_CEILING_S - 1}
        self.assertEqual(broker.expire_queue_wait(batch(past), now)[0], 1)


if __name__ == "__main__":
    unittest.main()
