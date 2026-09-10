from fastapi.testclient import TestClient

from backend import main


def test_startup_reconciles_the_existing_auto_update_launchagent(monkeypatch):
    calls = []
    monkeypatch.setattr(
        main.auto_updater,
        "apply_scheduler_if_idle",
        lambda: calls.append("reconciled") or True,
    )

    assert main._reconcile_auto_update_scheduler() is True

    assert calls == ["reconciled"]


def test_startup_does_not_bootout_the_launchagent_during_its_update(monkeypatch):
    calls = []
    monkeypatch.setattr(main.auto_updater, "apply_scheduler_if_idle", lambda: False)
    monkeypatch.setattr(
        main, "_schedule_auto_update_reconciliation",
        lambda: calls.append("scheduled-after-update"),
    )

    assert main._reconcile_auto_update_scheduler() is False
    assert calls == ["scheduled-after-update"]


def test_health_reports_unknown_commit_without_a_release_manifest():
    client = TestClient(main.app)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["app_commit"] == "unknown"


def test_auto_update_status_reports_standalone_capability_limits():
    client = TestClient(main.app, headers={"X-Studio-Token": main.FLEET_TOKEN})

    response = client.get("/api/auto-update/status")

    assert response.status_code == 200
    assert response.json()["capabilities"]["managed_exact_commit"] is False


def test_managed_update_route_requires_auth_and_threads_full_tuple(monkeypatch):
    request = {
        "after_current": True,
        "target_commit": "a" * 40,
        "target_version": "2.0.0",
        "operation_id": "hub-op-1",
    }
    public = TestClient(main.app)
    assert public.post("/api/auto-update/update", json=request).status_code == 401

    calls = []
    monkeypatch.setattr(
        main.auto_updater,
        "trigger_update",
        lambda **kwargs: calls.append(kwargs) or {"state": "deferred"},
    )
    client = TestClient(main.app, headers={"X-Studio-Token": main.FLEET_TOKEN})

    response = client.post("/api/auto-update/update", json=request)

    assert response.status_code == 200
    assert calls == [request]
