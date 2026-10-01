"""A freed Mac is offered the next item before the finished item's webhook returns.

`_run_item` released the studio, then awaited the per-item webhook and `_maybe_finish`, and only then
woke the scheduler; a slow callback target held the idle Mac up to the scheduler's 3 s heartbeat.
"""
import asyncio

import httpx

from components.hub.backend import broker


def test_the_scheduler_is_woken_before_the_item_webhook_is_awaited(fixture_monitor, monkeypatch):
    submitted = broker.submit_batch({"modality": "image", "model": "model-a",
                                     "items": [{"prompt": "hello"}]})
    batch = broker.batches[submitted["batch_id"]]
    item = batch["items"][0]
    studio = next(s for s in fixture_monitor.registry if s["id"] == "image")
    batch["item_webhook"] = "http://127.0.0.1:9/item"
    item.update(state="running", studio="image", tries=1)
    broker._busy.add("image")
    woken_during_webhook = []

    class Client:
        async def post(self, url, **kwargs):
            return httpx.Response(400, json={"detail": "bad request"})

    async def slow_webhook(*_args, **_kwargs):
        woken_during_webhook.append(broker._wakeup.is_set() and "image" not in broker._busy)

    async def no_finish(*_args, **_kwargs):
        return None

    monkeypatch.setattr(broker, "_post_item_webhook", slow_webhook)
    monkeypatch.setattr(broker, "_maybe_finish", no_finish)
    broker._wakeup.clear()
    asyncio.run(broker._run_item(Client(), batch, item, studio))
    assert woken_during_webhook == [True], \
        "the scheduler was not woken, and the Mac not released, before the webhook ran"
