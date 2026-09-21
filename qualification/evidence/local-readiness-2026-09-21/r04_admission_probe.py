"""Disposable real peer-route probe, not physical/fleet qualification."""
import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tests"))
from test_studio_proxy import StudioProxyTests, TOKEN

StudioProxyTests.setUpClass()
case = StudioProxyTests("test_a_peer_submission_carries_its_body_and_reaches_the_worker")
try:
    client = case.client()
    from components.hub.backend import broker, peers
    from components.hub import standalone_config, output_storage

    fleet_headers = {"X-Hub-Token": peers.fleet_token()}
    body = {"repo": "fixture/flux", "prompt": "local admission probe"}
    results = []
    pause = {("image", "fixture/flux"): {
        "studio": "image", "model": "fixture/flux", "blocked": True,
        "reason": "fixture model pause", "failures": 3,
    }}
    with patch.object(broker, "_model_protection", pause):
        assert broker.model_block_state("image", "fixture/flux") is not None
        before = len(case.worker.requests)
        response = client.post("/studio/image/api/generate/txt2img",
                               json=body, headers=fleet_headers)
        results.append({"case": "node_model_paused", "status": response.status_code,
                        "worker_requests": len(case.worker.requests) - before})

    disabled = client.post("/api/hub/registry/studios/image/enabled",
                           json={"enabled": False}, headers={"X-Hub-Token": TOKEN})
    assert disabled.status_code == 200, disabled.text
    before = len(case.worker.requests)
    response = client.post("/studio/image/api/generate/txt2img",
                           json=body, headers=fleet_headers)
    results.append({"case": "node_studio_disabled", "status": response.status_code,
                    "worker_requests": len(case.worker.requests) - before})
    enabled = client.post("/api/hub/registry/studios/image/enabled",
                          json={"enabled": True}, headers={"X-Hub-Token": TOKEN})
    assert enabled.status_code == 200

    root = Path(case.temporary.name) / "out"
    root.mkdir(exist_ok=True)
    settings = client.put("/api/hub/settings", json={"minimum_free_bytes": 10**18},
                          headers={"X-Hub-Token": TOKEN})
    assert settings.status_code == 200, settings.text
    policy = standalone_config.load_device_settings()["effective"]
    blocked = output_storage.admission(
        machine="local", max_bytes=policy["max_bytes"],
        retention_days=policy["retention_days"], enabled=policy["cleanup_enabled"],
        managed_output_roots=[str(root)], minimum_free_bytes=policy["minimum_free_bytes"],
    )
    assert blocked and blocked["code"] == "OUTPUT_STORAGE_BLOCKED"
    before = len(case.worker.requests)
    response = client.post("/studio/image/api/generate/txt2img",
                           json=body, headers=fleet_headers)
    results.append({"case": "node_storage_blocked", "status": response.status_code,
                    "worker_requests": len(case.worker.requests) - before})
    restored = client.put("/api/hub/settings", json={"minimum_free_bytes": 0},
                          headers={"X-Hub-Token": TOKEN})
    assert restored.status_code == 200

    assert broker.acquire_external_machine("local", "transcription:r04-probe")
    try:
        assert "local" in broker.busy_machines()
        before = len(case.worker.requests)
        response = client.post("/studio/image/api/generate/txt2img",
                               json=body, headers=fleet_headers)
        results.append({"case": "node_physical_machine_reserved", "status": response.status_code,
                        "worker_requests": len(case.worker.requests) - before})
    finally:
        broker.release_external_machine("local", "transcription:r04-probe")
    print(json.dumps(results, indent=2))
finally:
    case.doCleanups()
    StudioProxyTests.tearDownClass()
