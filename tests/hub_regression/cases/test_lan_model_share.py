"""LAN model sharing (0.6.146): a Mac copies a package from another Mac on its network.

The source Mac answers a short-lived, read-only token for one verified package revision (fleet
credential, relayed by a controller like the 0.6.143 install); it serves only that package's files,
with Range, at most two at a time. The copying Mac's existing download route takes the share as
`source` and verifies every file against the manifest exactly as for the internet. Every library
here is a throwaway one (DATA-LOSS hazard): nothing touches a real model library.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import socket
import threading
import time
from types import SimpleNamespace
from unittest import mock

import pytest
from test_discard_route_fleet import packaged_app  # noqa: F401  (the shared packaged-Hub fixture)

PKG = "lan-fixture"
NODE = "studio-mac-1104-hub"
ADDRESS = "http://100.96.171.104:47873"
FILES = {"weights.safetensors": b"w" * 5000, "tokenizer/config.json": b'{"a": 1}'}
GIB = 1024 ** 3


def _manifest():
    return {"schema_version": 1, "package_id": PKG, "model_id": "owner/lan-fixture",
            "revision": "a" * 40, "engine": "voice-mlx", "minimum_macos": "14.0",
            "requires_packages": [],
            # Never reachable: the copy must come from the peer, not from here.
            "files": [{"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                       "urls": ["https://10.255.255.1/" + name]} for name, data in FILES.items()]}


@pytest.fixture
def hub(packaged_app, tmp_path, monkeypatch):  # noqa: F811
    from fastapi.testclient import TestClient
    from components import model_packages
    from components.hub import model_share
    from components.hub.backend import control_plane, fleet_nodes, peers

    manifests = tmp_path / "manifests"
    manifests.mkdir()
    (manifests / f"{PKG}.json").write_text(json.dumps(_manifest()))
    monkeypatch.setenv("STUDIOHUB_MODEL_MANIFESTS", str(manifests))
    libraries = {}
    for name in ("source", "target"):                  # never a real model library
        libraries[name] = tmp_path / f"throwaway-{name}-library"
        libraries[name].mkdir()
    folder = tmp_path / "import"
    for name, data in FILES.items():
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(data)
    model_packages.import_folder(_manifest(), folder, libraries["source"])
    monkeypatch.setenv("STUDIOHUB_MODELS_DIR", str(libraries["source"]))
    monkeypatch.setattr(control_plane, "load_settings",
                        lambda: {**control_plane.defaults(), "role": "controller"})
    monkeypatch.setattr(fleet_nodes, "_read", lambda: {NODE: {"id": NODE, "address": ADDRESS}})
    monkeypatch.setattr(model_share, "_shares", {})
    monkeypatch.setattr(model_share, "_active", 0)
    memory = mock.patch("psutil.virtual_memory", lambda: SimpleNamespace(total=16 * GIB))
    memory.start()
    app, owner = packaged_app
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + peers.fleet_token()
        yield SimpleNamespace(client=client, app=app, owner=owner, libraries=libraries,
                              monkeypatch=monkeypatch)
    memory.stop()


def _share(hub):
    answer = hub.client.post(f"/api/hub/fleet/nodes/local/model-packages/{PKG}/share")
    assert answer.status_code == 200, answer.text
    return answer.json()["share"]


def _path(url_base, name):
    """The request path of one shared file (TestClient talks to the app, not the URL's host)."""
    return "/" + url_base.split("/", 3)[3] + "/" + name


def _events():
    from components.hub.backend import startup_control
    return [row["event"] for row in startup_control.recent_audit(50)]


class _Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def _share_response(hub, token, name, *, method="GET", range_header=None):
    from starlette.requests import Request

    route = next(route for route in hub.app.routes
                 if route.path == "/api/hub/model-share/{token}/files/{path:path}")
    headers = [] if range_header is None else [(b"range", range_header.encode())]
    scope = {"type": "http", "http_version": "1.1", "method": method,
             "scheme": "http", "path": _path(f"http://testserver/api/hub/model-share/{token}/files", name),
             "raw_path": b"", "query_string": b"", "root_path": "", "headers": headers,
             "client": ("127.0.0.1", 1234), "server": ("testserver", 80)}
    return route.endpoint(token, name, Request(scope)), scope


def test_a_share_names_one_revision_and_its_files(hub):
    share = _share(hub)
    assert share["revision"] == "a" * 40
    assert share["url_base"] == f"http://testserver/api/hub/model-share/{share['token']}/files"
    assert {f["path"]: f["bytes"] for f in share["files"]} == {n: len(d) for n, d in FILES.items()}
    assert share["expires_at"] > time.time() + 25 * 60
    assert "model_share" in _events()


def test_a_share_is_refused_for_an_absent_or_unknown_package(hub):
    hub.monkeypatch.setenv("STUDIOHUB_MODELS_DIR", str(hub.libraries["target"]))
    answer = hub.client.post(f"/api/hub/fleet/nodes/local/model-packages/{PKG}/share")
    assert answer.status_code == 409 and answer.json()["code"] == "NOT_VERIFIED"
    unknown = hub.client.post("/api/hub/fleet/nodes/local/model-packages/no-such/share")
    assert unknown.status_code == 404 and unknown.json()["code"] == "UNKNOWN_PACKAGE"


def test_a_share_is_refused_for_a_package_whose_files_changed(hub):
    (hub.libraries["source"] / PKG / ("a" * 40) / "weights.safetensors").write_bytes(b"short")
    answer = hub.client.post(f"/api/hub/fleet/nodes/local/model-packages/{PKG}/share")
    assert answer.status_code == 409 and answer.json()["code"] == "NOT_VERIFIED"


def test_serving_needs_the_token_and_resumes_with_range_uncompressed(hub):
    share = _share(hub)
    path = _path(share["url_base"], "weights.safetensors")
    anonymous = {"Authorization": ""}
    whole = hub.client.get(path, headers={**anonymous, "Accept-Encoding": "gzip"})
    assert whole.status_code == 200 and whole.content == FILES["weights.safetensors"]
    assert whole.headers["content-type"] == "application/octet-stream"
    assert "content-encoding" not in whole.headers
    part = hub.client.get(path, headers={**anonymous, "Range": "bytes=3000-"})
    assert part.status_code == 206
    assert part.headers["content-range"] == "bytes 3000-4999/5000"
    assert part.content == FILES["weights.safetensors"][3000:]
    wrong = path.replace(share["token"], "x" * 43)
    assert hub.client.get(wrong, headers=anonymous).status_code == 404
    assert "model_share_served" in _events()


@pytest.mark.parametrize(("range_header", "status"), [
    (None, 200),
    ("bytes=0-4999", 206),
    ("bytes=0-999,2000-2999", 206),
], ids=("whole", "range", "multipart"))
def test_continuous_file_responses_keep_the_share_alive(hub, range_header, status):
    from components.hub import model_share

    clock = _Clock()
    hub.monkeypatch.setattr(model_share, "time", SimpleNamespace(time=clock))
    hub.monkeypatch.setattr(model_share, "TTL_S", 5)
    share = _share(hub)
    response, scope = _share_response(hub, share["token"], "weights.safetensors",
                                      range_header=range_header)
    response.chunk_size = 1000
    messages = []

    async def send(message):
        messages.append(message)
        if message["type"] == "http.response.body" and message.get("body"):
            clock.advance(4)

    async def receive():
        return {"type": "http.disconnect"}

    asyncio.run(response(scope, receive, send))

    assert next(message["status"] for message in messages
                if message["type"] == "http.response.start") == status
    assert len([message for message in messages
                if message["type"] == "http.response.body" and message.get("body")]) > 1
    assert clock.now > 100 + model_share.TTL_S
    next_file = hub.client.get(_path(share["url_base"], "tokenizer/config.json"),
                               headers={"Authorization": ""})
    assert next_file.status_code == 200
    assert next_file.content == FILES["tokenizer/config.json"]


def test_head_does_not_renew_and_releases_the_transfer(hub):
    from components.hub import model_share

    clock = _Clock()
    hub.monkeypatch.setattr(model_share, "time", SimpleNamespace(time=clock))
    hub.monkeypatch.setattr(model_share, "TTL_S", 5)
    share = _share(hub)
    response, scope = _share_response(hub, share["token"], "weights.safetensors",
                                      method="HEAD")

    async def send(message):
        if message["type"] == "http.response.body":
            clock.advance(6)

    async def receive():
        return {"type": "http.disconnect"}

    asyncio.run(response(scope, receive, send))
    assert model_share._active == 0
    assert model_share.file_for(share["token"], "tokenizer/config.json") is None


def test_a_failed_send_does_not_renew_and_releases_the_transfer(hub):
    from components.hub import model_share

    clock = _Clock()
    hub.monkeypatch.setattr(model_share, "time", SimpleNamespace(time=clock))
    hub.monkeypatch.setattr(model_share, "TTL_S", 5)
    share = _share(hub)
    response, scope = _share_response(hub, share["token"], "weights.safetensors")

    async def send(message):
        if message["type"] == "http.response.body":
            clock.advance(6)
            raise RuntimeError("client disconnected")

    async def receive():
        return {"type": "http.disconnect"}

    with pytest.raises(RuntimeError, match="client disconnected"):
        asyncio.run(response(scope, receive, send))
    assert model_share._active == 0
    assert model_share.file_for(share["token"], "tokenizer/config.json") is None


def test_renew_never_revives_expired_or_unknown_grants(hub):
    from components.hub import model_share

    clock = _Clock()
    hub.monkeypatch.setattr(model_share, "time", SimpleNamespace(time=clock))
    hub.monkeypatch.setattr(model_share, "TTL_S", 5)
    share = _share(hub)
    clock.advance(6)

    expired_at = model_share._shares[share["token"]]["expires_at"]
    assert model_share.renew(share["token"]) is False
    assert model_share._shares[share["token"]]["expires_at"] == expired_at
    assert model_share.renew("unknown") is False
    assert "unknown" not in model_share._shares


def test_the_share_route_needs_a_fleet_credential(hub):
    from components.hub.backend.auth import fleet_path_allowed, node_path_allowed
    answer = hub.client.post(f"/api/hub/fleet/nodes/local/model-packages/{PKG}/share",
                             headers={"Authorization": ""})
    assert answer.status_code == 401
    assert fleet_path_allowed("POST", f"/api/hub/fleet/nodes/x/model-packages/{PKG}/share")
    assert not node_path_allowed("POST", f"/api/hub/fleet/nodes/x/model-packages/{PKG}/share")
    # The share prefix opens only reads: nothing else under it bypasses authentication.
    assert hub.client.post("/api/hub/model-share/t/files/x", headers={"Authorization": ""}).status_code == 401


@pytest.mark.parametrize("name", ["../../state/.hub_token", "%2e%2e/%2e%2e/state/.hub_token",
                                  "weights.safetensors/../../../import/weights.safetensors",
                                  "extra.bin", ""])
def test_nothing_but_the_shared_files_is_reachable(hub, name):
    (hub.libraries["source"] / PKG / ("a" * 40) / "extra.bin").write_bytes(b"not in the manifest")
    share = _share(hub)
    answer = hub.client.get(_path(share["url_base"], name), headers={"Authorization": ""})
    assert answer.status_code == 404, answer.text


def test_an_expired_share_is_a_404(hub):
    from components.hub import model_share
    share = _share(hub)
    model_share._shares[share["token"]]["expires_at"] = time.time() - 1
    answer = hub.client.get(_path(share["url_base"], "weights.safetensors"), headers={"Authorization": ""})
    assert answer.status_code == 404


def test_a_third_concurrent_transfer_is_told_to_retry(hub):
    from components.hub import model_share
    share = _share(hub)
    path = _path(share["url_base"], "weights.safetensors")
    assert model_share.acquire() and model_share.acquire()        # two files already streaming
    busy = hub.client.get(path, headers={"Authorization": ""})
    assert busy.status_code == 429 and busy.headers["retry-after"] == str(model_share.RETRY_AFTER_S)
    assert "model_share_busy" in _events()
    model_share.release()
    assert hub.client.get(path, headers={"Authorization": ""}).status_code == 200
    assert model_share._active == 1, "a finished transfer gives its slot back"


class _Node:
    def __init__(self, status=200, body=None):
        self.status, self.body, self.calls = status, body or {}, []

    async def request(self, method, url, timeout=None, json=None):
        import httpx
        self.calls.append((method, url, json))
        return httpx.Response(self.status, json=self.body, request=httpx.Request(method, url))

    async def aclose(self):
        return None


def test_share_and_lan_download_are_relayed_to_the_nodes(hub):
    from components.hub.backend import fleet_nodes
    share = {"url_base": f"{ADDRESS}/api/hub/model-share/t/files", "token": "t",
             "revision": "a" * 40, "files": []}
    node = _Node(200, {"ok": True, "node": "local", "share": share})
    hub.monkeypatch.setattr(fleet_nodes, "_probe_client", lambda: node)
    answer = hub.client.post(f"/api/hub/fleet/nodes/{NODE}/model-packages/{PKG}/share")
    assert answer.status_code == 200 and answer.json()["share"] == share and answer.json()["node"] == NODE
    assert node.calls[-1] == ("POST", f"{ADDRESS}/api/hub/fleet/nodes/local/model-packages/{PKG}/share", None)
    assert "model_share_relay" in _events()

    node.body = {"ok": True, "node": "local", "run": {"id": "n", "state": "running", "source": "lan"}}
    answer = hub.client.post(f"/api/hub/fleet/nodes/{NODE}/model-packages/{PKG}/download",
                             json={"source": share, "peer_only": True})
    assert answer.status_code == 200, answer.text
    assert node.calls[-1] == ("POST", f"{ADDRESS}/api/hub/fleet/nodes/local/model-packages/{PKG}/download",
                              {"source": share, "peer_only": True})
    hub.client.post(f"/api/hub/fleet/nodes/{NODE}/model-packages/{PKG}/download")
    assert node.calls[-1][2] is None, "an internet install relays no body, as before"


def test_the_download_route_hands_the_share_to_the_downloader(hub):
    from components.hub import model_downloads
    started = []
    hub.monkeypatch.setattr(model_downloads, "start", lambda **kw: started.append(
        (kw.get("peer"), kw.get("peer_only"))) or {"id": "r"})
    hub.monkeypatch.setenv("STUDIOHUB_MODELS_DIR", str(hub.libraries["target"]))
    route = f"/api/hub/fleet/nodes/local/model-packages/{PKG}/download"
    base = "http://100.96.171.104:47873/api/hub/model-share/t/files"
    assert hub.client.post(route, json={"source": {"url_base": base, "revision": "a" * 40},
                                        "peer_only": True}).status_code == 200
    assert hub.client.post(route, json={"source": {"url_base": base, "revision": "b" * 40}}).status_code == 200
    assert hub.client.post(route).status_code == 200
    assert started == [(base, True), (None, False), (None, False)], \
        "strict LAN is explicit; manual mismatch and no-share downloads keep internet fallback"
    for content in (b"{", b"[]"):
        answer = hub.client.post(route, content=content, headers={"Content-Type": "application/json"})
        assert answer.status_code == 409
    assert started == [(base, True), (None, False), (None, False)], \
        "malformed and non-object requests cannot silently become internet downloads"
    for body in ({"peer_only": True},
                 {"peer_only": True, "source": {"url_base": base, "revision": "b" * 40}},
                 {"peer_only": True, "source": {"url_base": base}},
                 {"peer_only": True, "source": []},
                 {"peer_only": "true", "source": {"url_base": base, "revision": "a" * 40}}):
        assert hub.client.post(route, json=body).status_code == 409
    bad = hub.client.post(route, json={"source": {"url_base": "file:///etc/passwd"}})
    assert bad.status_code == 409


async def _no_refresh(machine):
    return None


def _free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_a_lan_copy_end_to_end_installs_the_same_record(hub):
    """Share over real HTTP, then the real downloader child copies it into another throwaway library."""
    import httpx
    import uvicorn
    from components.hub.backend import peers
    # `done` re-reads this Mac's worker catalogs; there are no workers here, and the fixture's
    # worker port must never be dialled on a Mac that runs a real Hub.
    hub.monkeypatch.setattr(hub.app.state.monitor, "refresh_machine_catalogs", _no_refresh)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(hub.app, host="127.0.0.1", port=port, log_level="warning",
                                           lifespan="off"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        fleet = {"Authorization": "Bearer " + peers.fleet_token()}
        share = httpx.post(f"http://127.0.0.1:{port}/api/hub/fleet/nodes/local/model-packages/{PKG}/share",
                           headers=fleet).json()["share"]
        assert share["url_base"].startswith(f"http://127.0.0.1:{port}/")
        hub.monkeypatch.setenv("STUDIOHUB_MODELS_DIR", str(hub.libraries["target"]))
        run = hub.client.post(f"/api/hub/fleet/nodes/local/model-packages/{PKG}/download",
                              json={"source": share, "peer_only": True}).json()["run"]
        for _ in range(200):
            status = hub.client.get(f"/api/hub/fleet/nodes/local/model-downloads/{run['id']}").json()["run"]
            if status["state"] not in ("running", "recovery_pending"):
                break
            time.sleep(0.05)
        assert status["state"] == "done", status
        assert status["source_used"] == "lan"
    finally:
        server.should_exit = True
        thread.join(timeout=10)

    def record(library):
        value = json.loads((library / ".studiohub/installed.json").read_text())[PKG]
        for saved in value["files"].values():
            saved.pop("mtime_s")
        return value
    assert record(hub.libraries["target"]) == record(hub.libraries["source"])
