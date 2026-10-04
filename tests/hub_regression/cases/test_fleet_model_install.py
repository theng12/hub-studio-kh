"""Installing a model on a Mac from the Gateway (owner decision 2026-10-04).

The Gateway holds only the Fleet token. It asks a controller to start a model download on one of its
Macs - a joined node (relayed to the node's own ``local`` route) or the controller itself - and reads
how the download is going. The same refusals as the Model library's button, plus the memory class:
a Mac is never made to download a model it could never run. Nothing here downloads anything.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest
from test_discard_route_fleet import packaged_app  # noqa: F401  (the shared packaged-Hub fixture)

FISH = "fish-audio-s2-pro-4bit"
NODE = "studio-mac-1201-hub"
ADDRESS = "http://100.96.171.120:47873"
GIB = 1024 ** 3


@pytest.fixture
def hub(packaged_app, tmp_path, monkeypatch):  # noqa: F811
    from fastapi.testclient import TestClient
    from components.hub import model_downloads
    from components.hub.backend import control_plane, fleet_nodes, peers

    library = tmp_path / "throwaway-library"          # never a real model library (DATA-LOSS hazard)
    library.mkdir()
    monkeypatch.setenv("STUDIOHUB_MODELS_DIR", str(library))
    monkeypatch.setattr(control_plane, "load_settings",
                        lambda: {**control_plane.defaults(), "role": "controller"})
    monkeypatch.setattr(fleet_nodes, "_read", lambda: {NODE: {"id": NODE, "address": ADDRESS}})
    started = []

    def start(**kwargs):
        started.append(kwargs["package_id"])
        return {"id": "run-1", "package_id": kwargs["package_id"], "state": "running",
                "total_bytes": 100, "bytes_done": 0}

    monkeypatch.setattr(model_downloads, "start", start)
    monkeypatch.setattr(model_downloads, "status", lambda run_id: (
        {"id": run_id, "package_id": FISH, "state": "running", "total_bytes": 100, "bytes_done": 42}
        if run_id == "run-1" else None))
    memory = mock.patch("psutil.virtual_memory", lambda: SimpleNamespace(total=16 * GIB))
    memory.start()
    app, _owner = packaged_app
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + peers.fleet_token()
        yield SimpleNamespace(client=client, started=started)
    memory.stop()


def _events():
    from components.hub.backend import startup_control
    return [row["event"] for row in startup_control.recent_audit(50)]


class _Node:
    """The node's side of a relay: records the call, answers what the test says."""

    def __init__(self, status=200, body=None):
        self.status, self.body, self.calls = status, body or {}, []

    async def request(self, method, url, timeout=None):
        import httpx
        self.calls.append((method, url))
        return httpx.Response(self.status, json=self.body, request=httpx.Request(method, url))

    async def aclose(self):
        return None


def test_the_controller_installs_on_its_own_mac(hub):
    answer = hub.client.post(f"/api/hub/fleet/nodes/local/model-packages/{FISH}/download")
    assert answer.status_code == 200, answer.text
    assert answer.json()["ok"] is True
    assert answer.json()["run"]["id"] == "run-1"
    assert hub.started == [FISH]
    assert "model_download" in _events()


def test_a_relay_goes_to_the_nodes_own_local_route(hub, monkeypatch):
    from components.hub.backend import fleet_nodes
    node = _Node(200, {"ok": True, "node": "local", "run": {"id": "n-run", "state": "running"}})
    monkeypatch.setattr(fleet_nodes, "_probe_client", lambda: node)
    answer = hub.client.post(f"/api/hub/fleet/nodes/{NODE}/model-packages/{FISH}/download")
    assert answer.status_code == 200, answer.text
    assert node.calls == [("POST", f"{ADDRESS}/api/hub/fleet/nodes/local/model-packages/{FISH}/download")]
    assert answer.json()["node"] == NODE and answer.json()["run"]["id"] == "n-run"
    assert hub.started == [], "a relayed install never starts a download on the controller"
    assert "model_download_relay" in _events()

    status = hub.client.get(f"/api/hub/fleet/nodes/{NODE}/model-downloads/n-run")
    assert status.status_code == 200
    assert node.calls[-1] == ("GET", f"{ADDRESS}/api/hub/fleet/nodes/local/model-downloads/n-run")


def test_the_nodes_refusal_comes_back_with_its_own_sentence(hub, monkeypatch):
    from components.hub.backend import fleet_nodes
    refusal = {"ok": False, "code": "REFUSED", "detail": "Not on this Mac: needs 16 GB"}
    monkeypatch.setattr(fleet_nodes, "_probe_client", lambda: _Node(409, refusal))
    answer = hub.client.post(f"/api/hub/fleet/nodes/{NODE}/model-packages/{FISH}/download")
    assert answer.status_code == 409
    assert answer.json()["detail"] == refusal["detail"]


def test_an_older_node_is_named_as_too_old(hub, monkeypatch):
    from components.hub.backend import fleet_nodes
    monkeypatch.setattr(fleet_nodes, "_probe_client", lambda: _Node(403, {"detail": "refused"}))
    answer = hub.client.post(f"/api/hub/fleet/nodes/{NODE}/model-packages/{FISH}/download")
    assert answer.status_code == 502
    assert answer.json()["code"] == "NODE_REFUSED" and "0.6.143" in answer.json()["detail"]


def test_an_unknown_node_is_a_404(hub):
    answer = hub.client.post(f"/api/hub/fleet/nodes/not-joined/model-packages/{FISH}/download")
    assert answer.status_code == 404
    assert answer.json()["code"] == "UNKNOWN_NODE"


def test_an_unknown_package_is_a_404(hub):
    answer = hub.client.post("/api/hub/fleet/nodes/local/model-packages/no-such-model/download")
    assert answer.status_code == 404
    assert answer.json()["code"] == "UNKNOWN_PACKAGE"
    assert hub.started == []


def test_a_shelved_model_is_refused(hub, monkeypatch):
    from components import shelved_models
    from components.hub import standalone
    monkeypatch.setattr(shelved_models, "manifest_shelved", lambda manifest: True)
    monkeypatch.setattr(standalone, "_keep_all_models", lambda: False)
    answer = hub.client.post(f"/api/hub/fleet/nodes/local/model-packages/{FISH}/download")
    assert answer.status_code == 409
    assert "shelved model" in answer.json()["detail"]
    assert hub.started == []


def test_a_model_this_mac_cannot_run_is_refused_in_the_hubs_words(hub):
    with mock.patch("psutil.virtual_memory", lambda: SimpleNamespace(total=8 * GIB)):
        answer = hub.client.post(f"/api/hub/fleet/nodes/local/model-packages/{FISH}/download")
    assert answer.status_code == 409
    assert answer.json()["detail"] == (
        f"Not on this Mac: {FISH} needs a Mac with at least 16 GB of memory and this one has 8 GB, "
        "so it could never run here. Nothing was downloaded.")
    assert hub.started == []


def test_the_status_read_on_this_mac(hub):
    answer = hub.client.get("/api/hub/fleet/nodes/local/model-downloads/run-1")
    assert answer.status_code == 200
    assert answer.json()["run"]["bytes_done"] == 42
    assert hub.client.get("/api/hub/fleet/nodes/local/model-downloads/nope").status_code == 404


def test_the_fleet_still_cannot_import_cancel_or_use_the_plain_model_routes():
    from components.hub.backend.auth import fleet_path_allowed
    assert fleet_path_allowed("POST", f"/api/hub/fleet/nodes/x/model-packages/{FISH}/download")
    assert fleet_path_allowed("GET", "/api/hub/fleet/nodes/x/model-downloads/run-1")
    for method, path in (("POST", f"/api/hub/model-packages/{FISH}/download"),
                         ("POST", f"/api/hub/model-packages/{FISH}/import"),
                         ("POST", f"/api/hub/model-packages/{FISH}/verify"),
                         ("GET", "/api/hub/model-downloads/run-1"),
                         ("DELETE", "/api/hub/model-downloads/run-1"),
                         ("DELETE", "/api/hub/fleet/nodes/x/model-downloads/run-1")):
        assert not fleet_path_allowed(method, path), (method, path)
