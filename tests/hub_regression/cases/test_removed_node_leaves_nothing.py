"""Removing a node leaves nothing of it on the controller: status, catalogs, peer snapshot."""
import time

from components.hub.backend import peers


def test_reload_registry_forgets_a_machine_that_is_no_longer_registered(fixture_monitor):
    monitor = fixture_monitor
    ghost, kept = "gone-node:image", monitor.registry[0]["id"]
    monitor.status[ghost] = {"status": "up", "last_seen": time.time(), "last_checked": time.time()}
    monitor._catalog_cache[ghost] = (time.time(), {"models": [{"repo": "m"}]})
    monitor._transcribe_cache[ghost] = (time.time(), {"models": []})
    monitor._catalog_meta[ghost] = {"last_success_at": 1.0}
    peers._cache["gone-node"] = (time.time(), {"reachable": True, "auth": True, "status": "connected"})
    monitor._catalog_cache[kept] = (time.time(), {"models": []})

    monitor.reload_registry()

    assert ghost not in monitor.status
    assert ghost not in monitor._catalog_cache and ghost not in monitor._transcribe_cache
    assert ghost not in monitor._catalog_meta
    assert "gone-node" not in peers._cache
    assert kept in monitor.status and kept in monitor._catalog_cache, "a registered studio was forgotten"
