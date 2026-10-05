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
    app, owner = packaged_app
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + peers.fleet_token()
        yield SimpleNamespace(client=client, started=started, owner=owner)
    memory.stop()


def _events():
    from components.hub.backend import startup_control
    return [row["event"] for row in startup_control.recent_audit(50)]


class _Node:
    """The node's side of a relay: records the call, answers what the test says."""

    def __init__(self, status=200, body=None):
        self.status, self.body, self.calls = status, body or {}, []

    async def request(self, method, url, timeout=None, content=None, headers=None):
        import httpx
        self.calls.append((method, url))
        self.sent = (content, headers, timeout)
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


# "Done but absent" (live, 2026-10-04, 1500 controller, Fish Audio S2 Pro 4-bit): the download finished
# and its receipt was in the library the voice worker reads, but the Hub re-reads worker catalogs only
# once a minute. The Gateway's Fleet page reloads the moment it hears `done`, so it read the model as
# absent (and the scheduler would not send it work) until the next cycle.

FISH_REPO = "majentik/fishaudio-s2-pro-MLX-4bit"


class _Worker:
    """The voice worker's /api/catalog: it reports what is on disk now."""

    def __init__(self, state):
        self.state, self.calls = state, 0

    async def get(self, url, headers=None, timeout=None):
        import httpx
        self.calls += 1
        body = {"families": {}, "models": [{"repo": FISH_REPO, "kind": "local",
                                            "cache": {"state": self.state, "package_id": FISH}}]}
        return httpx.Response(200, json=body, request=httpx.Request("GET", url))


def _fish_cache_state(hub):
    states = {row["cache"]["state"] for row in hub.client.get("/api/hub/models").json()["models"]
              if row.get("repo") == FISH_REPO}
    assert len(states) == 1, states
    return states.pop()


@pytest.mark.parametrize("route, who", [("/api/hub/fleet/nodes/local/model-downloads/run-2", "fleet"),
                                        ("/api/hub/model-downloads/run-2", "owner")])   # Gateway / Model library
def test_a_finished_download_shows_installed_on_the_next_read(hub, monkeypatch, route, who):
    import time
    from components.hub import model_downloads
    monitor = hub.client.app.state.monitor
    worker = _Worker("absent")
    monkeypatch.setattr(monitor, "_client", worker)
    monitor.status["voice"] = {"status": "up"}
    assert _fish_cache_state(hub) == "absent", "before the download the worker has no Fish"

    worker.state = "cached"                       # the download published the package
    monkeypatch.setattr(model_downloads, "status", lambda run_id: {
        "id": run_id, "package_id": FISH, "state": "done", "exit_code": 0,
        "finished_at": time.time(), "total_bytes": 100})
    headers = {"Authorization": "Bearer " + hub.owner} if who == "owner" else {}
    answer = hub.client.get(route, headers=headers)
    assert answer.status_code == 200, answer.text
    assert _fish_cache_state(hub) == "cached", "the read straight after `done` must not be a minute old"

    calls = worker.calls
    hub.client.get(route, headers=headers)
    assert worker.calls == calls, "a finished run re-reads the catalogs once, not on every poll"


def test_a_relayed_finished_download_refreshes_the_controllers_view_of_that_node(hub, monkeypatch):
    from components.hub.backend import fleet_nodes
    monitor = hub.client.app.state.monitor
    refreshed = []
    monkeypatch.setattr(monitor, "refresh_machine_catalogs", lambda machine: _record(refreshed, machine))
    running = _Node(200, {"ok": True, "node": "local", "run": {"id": "n-run", "state": "running", "bytes_done": 7}})
    monkeypatch.setattr(fleet_nodes, "_probe_client", lambda: running)
    assert hub.client.get(f"/api/hub/fleet/nodes/{NODE}/model-downloads/n-run").json()["run"]["bytes_done"] == 7
    assert refreshed == [], "nothing to re-read while the download is still running"

    done = _Node(200, {"ok": True, "node": "local", "run": {"id": "n-run", "state": "done", "exit_code": 0}})
    monkeypatch.setattr(fleet_nodes, "_probe_client", lambda: done)
    assert hub.client.get(f"/api/hub/fleet/nodes/{NODE}/model-downloads/n-run").json()["run"]["state"] == "done"
    assert refreshed == [NODE]


async def _record(calls, machine):
    calls.append(machine)


def test_refresh_machine_catalogs_reads_only_that_machines_studios(monkeypatch):
    import asyncio
    from components.hub.backend.monitor import StudioMonitor
    monitor = StudioMonitor.__new__(StudioMonitor)
    monitor.registry = [{"id": "voice", "machine": "local"}, {"id": "image"},
                        {"id": f"{NODE}:voice", "machine": NODE.upper()}]
    seen = []

    async def refresh(studio, *, force):
        seen.append((studio["id"], force))

    monitor._refresh_studio_catalog = refresh
    asyncio.run(monitor.refresh_machine_catalogs(NODE))
    assert seen == [(f"{NODE}:voice", True)]
    seen.clear()
    asyncio.run(monitor.refresh_machine_catalogs("local"))
    assert seen == [("voice", True), ("image", True)]


# A test transcription on one chosen Mac (0.6.147, Phonon-2 qualification from the Gateway): the node's
# own Subtitle /api/transcribe, relayed like a model install and answered in full, words included.

WORDS = {"text": "The Dreamlands sleep.", "segments": [{"start": 0.08, "end": 0.96, "text": "The Dreamlands sleep.",
         "words": [{"word": "The", "start": 0.08, "end": 0.24}]}], "model": "phonon-2-mlx"}


def test_a_test_transcription_is_relayed_to_the_nodes_own_route_with_its_upload_and_words(hub, monkeypatch):
    from components.hub.backend import fleet_nodes
    node = _Node(200, WORDS)
    monkeypatch.setattr(fleet_nodes, "_probe_client", lambda: node)
    answer = hub.client.post(f"/api/hub/fleet/nodes/{NODE}/transcribe",
                             data={"model": "phonon-2-mlx", "language": "en", "word_timestamps": "true"},
                             files={"file": ("clip.wav", b"RIFF-not-really", "audio/wav")})
    assert answer.status_code == 200, answer.text
    assert node.calls == [("POST", f"{ADDRESS}/api/hub/fleet/nodes/local/transcribe")]
    content, headers, timeout = node.sent
    assert headers["Content-Type"].startswith("multipart/form-data; boundary=")
    assert b"RIFF-not-really" in content and b"phonon-2-mlx" in content
    assert timeout is fleet_nodes.TRANSCRIBE_RELAY_TIMEOUT
    assert answer.json()["segments"][0]["words"] == WORDS["segments"][0]["words"]
    assert answer.json()["node"] == NODE
    assert "transcribe_relay" in _events()


def test_an_older_node_is_named_as_too_old_for_a_test_transcription(hub, monkeypatch):
    from components.hub.backend import fleet_nodes
    monkeypatch.setattr(fleet_nodes, "_probe_client", lambda: _Node(403, {"detail": "refused"}))
    answer = hub.client.post(f"/api/hub/fleet/nodes/{NODE}/transcribe", files={"file": ("a.wav", b"x")})
    assert answer.status_code == 502
    assert answer.json()["code"] == "NODE_REFUSED" and "0.6.147" in answer.json()["detail"]
    assert hub.client.post("/api/hub/fleet/nodes/not-joined/transcribe",
                           files={"file": ("a.wav", b"x")}).json()["code"] == "UNKNOWN_NODE"


def test_on_this_mac_it_is_the_subtitle_workers_own_transcribe(hub, monkeypatch):
    import httpx
    monitor = hub.client.app.state.monitor
    seen = []

    async def request(method, url, headers=None, content=None, timeout=None):
        seen.append((method, url, headers, content))
        return httpx.Response(200, json=WORDS, request=httpx.Request(method, url))

    monkeypatch.setattr(monitor, "_client", SimpleNamespace(request=request))
    monkeypatch.setattr(monitor, "registry", [{"id": "subtitle", "machine": "local",
                                               "base_url": "http://127.0.0.1:1", "hub_token": "worker-token"}])
    monitor.status["subtitle"] = {"status": "up"}
    answer = hub.client.post("/api/hub/fleet/nodes/local/transcribe",   # no model: the worker's default
                             data={"word_timestamps": "true"},
                             files={"file": ("clip.wav", b"RIFF-not-really", "audio/wav")})
    assert answer.status_code == 200, answer.text
    assert answer.json()["segments"][0]["words"] == WORDS["segments"][0]["words"]
    method, url, headers, content = seen[0]
    assert (method, url) == ("POST", "http://127.0.0.1:1/api/transcribe")
    assert headers["X-Hub-Token"] == "worker-token", "the worker is re-signed with its own token"
    assert b"RIFF-not-really" in content
    assert "transcribe" in _events()


def test_the_fleet_may_ask_for_a_test_transcription_and_a_node_credential_may_not():
    from components.hub.backend.auth import fleet_path_allowed, node_path_allowed
    assert fleet_path_allowed("POST", "/api/hub/fleet/nodes/x/transcribe")
    assert not fleet_path_allowed("GET", "/api/hub/fleet/nodes/x/transcribe")
    assert not node_path_allowed("POST", "/api/hub/fleet/nodes/x/transcribe")
