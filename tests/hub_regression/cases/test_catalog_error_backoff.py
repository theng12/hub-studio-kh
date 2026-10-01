"""A studio whose catalog keeps failing is not asked again on every read."""
import asyncio
import time

import httpx

from components.hub.backend import monitor as monitor_module


def test_a_failing_catalog_is_retried_after_a_pause_not_on_every_read(fixture_monitor, monkeypatch):
    studio = next(s for s in fixture_monitor.registry if s["modality"] == "image")
    calls = []

    class Client:
        async def get(self, url, **kwargs):
            calls.append(url)
            return httpx.Response(500, request=httpx.Request("GET", url))

    monkeypatch.setattr(fixture_monitor, "_client", Client())
    clock = [time.time()]
    monkeypatch.setattr(monitor_module.time, "time", lambda: clock[0])

    async def read(force=False):
        return await fixture_monitor.get_catalog(studio, force=force)

    for _ in range(10):
        assert asyncio.run(read()) is None
        clock[0] += 1.0
    assert len(calls) == 1, f"a failing catalog was requested {len(calls)} times in 10 s"
    asyncio.run(read(force=True))
    assert len(calls) == 2, "a forced refresh must always ask"
    clock[0] += monitor_module.CATALOG_ERROR_RETRY_S + 1
    asyncio.run(read())
    assert len(calls) == 3, "a failing catalog was never retried"
