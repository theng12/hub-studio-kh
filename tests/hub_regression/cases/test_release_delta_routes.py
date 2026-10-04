"""The delta update over the wire: a manifest-only push, blobs by sha256, who may do what, a node
mirroring its controller, and the stage job the native app polls.

Real packaged Hub app, real signatures (throwaway keys), real files on disk. The property is that a
release can travel as a manifest plus only the blobs a controller lacks, that every byte is
verified against what the gateway signed, and that a credential can never plant a file.
"""
from __future__ import annotations

import asyncio
import base64
import gzip
import hashlib
import json
import os
import secrets
import shutil
import time
from pathlib import Path

import httpx
import pytest

SCHEMA = "studiohub.app-release.v1"
R = "Contents/Resources/"


@pytest.fixture
def hub(reset):
    from fastapi.testclient import TestClient
    from components.hub.standalone import create_app

    app = create_app(start_background=False)
    with TestClient(app) as client:
        yield client


@pytest.fixture(autouse=True)
def _clean(reset, monkeypatch):
    from components.hub.backend import fleet_nodes, release_delta, release_sync
    fleet_nodes.store_path().unlink(missing_ok=True)
    fleet_nodes.set_node_credential(None)
    release_delta._installed_cache.clear()
    release_sync._job.clear()
    release_sync._job["state"] = "idle"
    monkeypatch.delenv("STUDIOHUB_RELEASE_MANIFEST", raising=False)
    yield
    fleet_nodes.store_path().unlink(missing_ok=True)
    fleet_nodes.set_node_credential(None)


def _hub_headers() -> dict:
    from components.hub.backend import auth
    return {"Authorization": "Bearer " + auth.load_token()}


def _fleet_headers() -> dict:
    from components.hub.backend import peers
    return {"Authorization": "Bearer " + peers.fleet_token()}


def _node_headers() -> dict:
    from components.hub.backend import fleet_nodes
    joined = fleet_nodes.join({"node_id": "node-1", "name": "Node 1",
                               "address": "http://192.168.1.21:47873", "studios": []},
                              actor={"kind": "hub", "node_id": None})
    return {"Authorization": "Bearer " + joined["credential"]}


def _seed() -> bytes:
    return secrets.token_bytes(32)


def _trust(client, seed: bytes) -> None:
    from components.hub.backend import ed25519
    response = client.post("/api/hub/release/trust", headers=_hub_headers(),
                           json={"public_key": ed25519.public_key(seed).hex(), "publisher_id": "gatewaykh"})
    assert response.status_code == 200, response.text


def _tree(root: Path, files: dict) -> Path:
    for rel, data in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return root


def _entries(root: Path) -> dict:
    from components.hub.backend import file_manifest as fm
    return fm.scan(root)


FILES_A = {
    "Contents/Info.plist": b"<plist>a</plist>",
    R + "release-manifest.json": b'{"version":"a"}',
    R + "components/hub/backend/broker.py": b"# broker a\n",
    R + "runtimes/voice/bin/python3": b"\x7fELF python",
    R + "runtimes/voice/lib/big.so": b"S" * 2048,
}


def _release(seed: bytes, files: dict, tmp: Path, *, build: str = "abc1234", **over):
    """(descriptor, body, entries, blobs-by-sha) for a manifest-only release of ``files``."""
    from components.hub.backend import app_release, ed25519
    from components.hub.backend import file_manifest as fm

    root = _tree(tmp / f"app-{secrets.token_hex(3)}", files)
    entries = _entries(root)
    index, tsv = fm.build(entries)
    disk_image = secrets.token_bytes(64)            # the gateway holds it; the controller never sees it
    public = ed25519.public_key(seed)
    descriptor = {
        "schema": SCHEMA, "schema_version": 1, "version": "0.6.99", "build": build,
        "filename": f"StudioHub-0.6.99-{build}.dmg", "bytes": 1_800_000_000,
        "sha256": hashlib.sha256(disk_image).hexdigest(), "published_at": 1790000000,
        "publisher_id": "gatewaykh", "publisher_key_id": app_release.fingerprint(public),
        "files_sha256": index["files_sha256"], "files_count": index["files_count"],
        "files_bytes": index["files_bytes"],
    }
    descriptor.update(over)
    descriptor["signature"] = app_release.sign(descriptor, seed)
    body = {"descriptor": descriptor,
            "file_manifest": {"index": index,
                              "groups": {n: base64.b64encode(fm.gzip_bytes(d)).decode() for n, d in tsv.items()}}}
    blobs = {digest: (root / rel).read_bytes() for rel, (kind, _x, _s, digest) in entries.items() if kind == "f"}
    return descriptor, body, entries, blobs


def _push(hub, body, headers=None):
    return hub.post("/api/hub/release", headers=headers or _fleet_headers(), json=body)


def _send_all(hub, blobs, need):
    for sha in need:
        response = hub.put(f"/api/hub/release/blob/{sha}", headers=_fleet_headers(), content=blobs[sha])
        assert response.status_code == 200, response.text


# ── the push ───────────────────────────────────────────────────────────────


def test_manifest_only_push_answers_with_the_blobs_still_to_send(hub, tmp_path):
    from components.hub.backend import app_release
    seed = _seed()
    _trust(hub, seed)
    descriptor, body, entries, blobs = _release(seed, FILES_A, tmp_path)

    response = _push(hub, body)
    assert response.status_code == 200, response.text
    answer = response.json()
    assert sorted(answer["need"]) == sorted(blobs)              # nothing held yet: everything is needed
    assert answer["descriptor"]["files_sha256"] == descriptor["files_sha256"]
    assert answer["descriptor"]["controller_signature"]
    assert not app_release.ARTIFACT_FILE.exists(), "a manifest-only release holds no disk image"
    # And the release is readable, with its delta marker and the key a node pins.
    read = hub.get("/api/hub/release", headers=_fleet_headers()).json()
    assert read["release"]["build"] == "abc1234"
    assert read["delta"]["manifest"] is True
    assert read["trusted_publisher"]["public_key"]

    sizes = _send_all(hub, blobs, answer["need"])
    assert hub.get("/api/hub/release/need", headers=_fleet_headers()).json() == {"need": []}


def test_blobs_are_verified_on_receipt(hub, tmp_path):
    from components.hub.backend import app_release
    seed = _seed()
    _trust(hub, seed)
    _descriptor, body, _entries_, blobs = _release(seed, FILES_A, tmp_path)
    need = _push(hub, body).json()["need"]
    sha = need[0]
    url = f"/api/hub/release/blob/{sha}"

    wrong = hub.put(url, headers=_fleet_headers(), content=b"x" * len(blobs[sha]))
    assert wrong.status_code == 400, wrong.text
    short = hub.put(url, headers=_fleet_headers(), content=blobs[sha][:-1])
    assert short.status_code == 400, short.text
    long = hub.put(url, headers=_fleet_headers(), content=blobs[sha] + b"!")
    assert long.status_code == 413, long.text
    assert not (app_release.blob_dir() / sha).exists(), "a refused blob must not be stored"
    assert not [p for p in app_release.blob_dir().iterdir()], "and must leave no partial file"
    unknown = hub.put(f"/api/hub/release/blob/{'0' * 64}", headers=_fleet_headers(), content=b"x")
    assert unknown.status_code == 404
    assert hub.put(url, headers=_fleet_headers(), content=blobs[sha]).status_code == 200


def test_a_push_the_signature_does_not_cover_is_refused_and_stores_nothing(hub, tmp_path):
    from components.hub.backend import app_release
    from components.hub.backend import file_manifest as fm
    seed = _seed()
    _trust(hub, seed)
    descriptor, body, _e, _b = _release(seed, FILES_A, tmp_path)

    forged = json.loads(json.dumps(body))
    forged["descriptor"]["files_sha256"] = "0" * 64          # signed digest changed after signing
    assert _push(hub, forged).status_code == 400

    other, other_body, _e2, _b2 = _release(seed, {**FILES_A, R + "x.py": b"other"}, tmp_path)
    mixed = {"descriptor": descriptor, "file_manifest": other_body["file_manifest"]}   # someone else's manifest
    response = _push(hub, mixed)
    assert response.status_code == 400 and "descriptor" in response.json()["detail"]

    tampered = json.loads(json.dumps(body))
    groups = tampered["file_manifest"]["groups"]
    core = gzip.decompress(base64.b64decode(groups["core"]))
    groups["core"] = base64.b64encode(fm.gzip_bytes(core.replace(b"broker", b"brokeR", 1))).decode()
    assert _push(hub, tampered).status_code == 400

    missing_group = json.loads(json.dumps(body))
    missing_group["file_manifest"]["groups"].pop("runtime-voice")
    assert _push(hub, missing_group).status_code == 400

    no_digest, no_digest_body, _e3, _b3 = _release(seed, FILES_A, tmp_path)
    for name in ("files_sha256", "files_count", "files_bytes"):
        stripped = json.loads(json.dumps(no_digest_body))
        stripped["descriptor"].pop(name)
        stripped["descriptor"]["signature"] = app_release.sign(stripped["descriptor"], seed)
        assert _push(hub, stripped).status_code == 400, name    # all three or none
    assert not app_release.RELEASE_FILE.exists()
    assert not (app_release.RELEASE_DIR / "manifests").exists()


def test_a_release_pushed_as_a_disk_image_still_works_beside_the_manifest_one(hub, tmp_path):
    from components.hub.backend import app_release, ed25519
    seed = _seed()
    _trust(hub, seed)
    artifact = secrets.token_bytes(512)
    descriptor = {"schema": SCHEMA, "schema_version": 1, "version": "0.6.50", "build": "abc1234",
                  "filename": "x.dmg", "bytes": len(artifact), "sha256": hashlib.sha256(artifact).hexdigest(),
                  "published_at": 1790000000, "publisher_id": "gatewaykh",
                  "publisher_key_id": app_release.fingerprint(ed25519.public_key(seed))}
    descriptor["signature"] = app_release.sign(descriptor, seed)
    response = _push(hub, {"descriptor": descriptor, "artifact_base64": base64.b64encode(artifact).decode()})
    assert response.status_code == 200
    assert "need" not in response.json(), "the legacy answer keeps its shape"
    assert app_release.ARTIFACT_FILE.read_bytes() == artifact
    # A neither-artifact-nor-manifest push is not a release.
    assert _push(hub, {"descriptor": descriptor}).status_code == 400
    # Pushing a manifest-only release afterwards must not leave the old image served as the new one.
    _d, body, _e, _b = _release(seed, FILES_A, tmp_path)
    assert _push(hub, body).status_code == 200
    assert not app_release.ARTIFACT_FILE.exists()


# ── serving ────────────────────────────────────────────────────────────────


def test_blobs_and_manifest_are_served_with_range_and_only_when_listed(hub, tmp_path):
    from components.hub.backend import file_manifest as fm
    seed = _seed()
    _trust(hub, seed)
    descriptor, body, entries, blobs = _release(seed, FILES_A, tmp_path)
    need = _push(hub, body).json()["need"]
    _send_all(hub, blobs, need)

    big = next(sha for sha, data in blobs.items() if data == b"S" * 2048)
    full = hub.get(f"/api/hub/release/blob/{big}", headers=_node_headers())
    assert full.status_code == 200 and full.content == blobs[big]
    assert full.headers["accept-ranges"] == "bytes"
    # Never gzipped: a node reads blobs with `iter_raw` and resumes them with Range.
    assert "content-encoding" not in full.headers
    part = hub.get(f"/api/hub/release/blob/{big}", headers={**_node_headers(), "Range": "bytes=10-19"})
    assert part.status_code == 206 and part.content == blobs[big][10:20]
    assert hub.get(f"/api/hub/release/blob/{'1' * 64}", headers=_fleet_headers()).status_code == 404

    index = hub.get("/api/hub/release/files", headers=_node_headers()).json()
    assert index["files_sha256"] == descriptor["files_sha256"]
    core = hub.get("/api/hub/release/files/core", headers=_node_headers())
    assert hashlib.sha256(gzip.decompress(core.content)).hexdigest() == index["groups"]["core"]["sha256"]
    assert fm.check_group(index, "core", gzip.decompress(core.content))
    assert hub.get("/api/hub/release/files/nope", headers=_fleet_headers()).status_code == 404
    assert hub.get("/api/hub/release/files/..%2f..%2fx", headers=_fleet_headers()).status_code in (403, 404, 400)


def test_a_controller_that_already_runs_the_release_needs_no_blobs_and_serves_them(hub, tmp_path, monkeypatch):
    from components.hub.backend import app_release
    seed = _seed()
    _trust(hub, seed)
    _d, body, entries, blobs = _release(seed, FILES_A, tmp_path)
    # This Hub is running from an installed app that is already that release (read-only, as installed).
    app = _tree(tmp_path / "Applications" / "Studio Hub.app", FILES_A)
    monkeypatch.setenv("STUDIOHUB_RELEASE_MANIFEST", str(app / R / "release-manifest.json"))
    answer = _push(hub, body).json()
    assert answer["need"] == [], "everything is already in the installed app"
    big = next(sha for sha, data in blobs.items() if data == b"S" * 2048)
    served = hub.get(f"/api/hub/release/blob/{big}", headers=_node_headers())
    assert served.status_code == 200 and served.content == blobs[big]
    assert not list(app_release.blob_dir().glob("*")), "nothing needed storing"


def test_a_blob_put_does_no_rescan_and_runs_off_the_event_loop(hub, tmp_path, monkeypatch):
    """The pilot push took minutes: every PUT rebuilt the whole missing set (a listing, a walk of ~93,000
    manifest entries) on the event loop. A PUT stores one blob; the count is asked once, by `GET /need`."""
    from components.hub.backend import app_release, release_delta
    seed = _seed()
    _trust(hub, seed)
    _d, body, _entries_, blobs = _release(seed, FILES_A, tmp_path)
    need = _push(hub, body).json()["need"]
    assert len(need) > 2

    rescans = []
    real = app_release.missing_blobs
    monkeypatch.setattr(app_release, "missing_blobs", lambda: rescans.append(1) or real())
    on_loop = []

    def note_thread(original):
        def wrapper(self, *args):
            try:
                asyncio.get_running_loop()
                on_loop.append(original.__name__)
            except RuntimeError:
                pass
            return original(self, *args)
        return wrapper
    for name in ("write", "commit"):
        monkeypatch.setattr(app_release.BlobUpload, name, note_thread(getattr(app_release.BlobUpload, name)))

    for sha in need:
        response = hub.put(f"/api/hub/release/blob/{sha}", headers=_fleet_headers(), content=blobs[sha])
        assert response.status_code == 200, response.text
        assert response.json() == {"ok": True, "stored": sha}
    assert rescans == [], "a blob PUT must not recompute the missing set"
    assert on_loop == [], f"disk and hashing work ran on the event loop: {on_loop}"
    assert hub.get("/api/hub/release/need", headers=_fleet_headers()).json() == {"need": []}
    assert len(rescans) == 1, "the sender asks once, at the end"

    app = _tree(tmp_path / "Applications" / "Studio Hub.app", FILES_A)
    first = release_delta.installed_by_sha(app)
    assert release_delta.installed_by_sha(app) is first, "the installed app's sha index is built once, not per blob"


def test_a_push_says_which_installed_app_its_need_is_measured_against(hub, tmp_path, monkeypatch):
    """`need` is the whole release when a controller cannot find its own installed app, and only what
    changed when it can. The answer says which, so a slow push can be told apart from a wrong basis."""
    seed = _seed()
    _trust(hub, seed)
    _d, body, _entries_, blobs = _release(seed, FILES_A, tmp_path)
    lost = _push(hub, body).json()
    assert lost["basis"] == {"installed_app": None, "wanted_blobs": len(blobs), "needed_blobs": len(blobs)}
    app = _tree(tmp_path / "Applications" / "Studio Hub.app", FILES_A)
    monkeypatch.setenv("STUDIOHUB_RELEASE_MANIFEST", str(app / R / "release-manifest.json"))
    found = _push(hub, body).json()
    assert found["basis"] == {"installed_app": str(app), "wanted_blobs": len(blobs), "needed_blobs": 0}


def test_only_the_gateways_credential_may_push_and_a_node_may_only_read(hub, tmp_path):
    seed = _seed()
    _trust(hub, seed)
    _d, body, _e, blobs = _release(seed, FILES_A, tmp_path)
    node = _node_headers()
    # A node credential can read the release and its blobs ...
    assert _push(hub, body).status_code == 200
    assert hub.get("/api/hub/release", headers=node).status_code == 200
    assert hub.get("/api/hub/release/files", headers=node).status_code == 200
    # ... and can never write one, or plant a file, or start an update on some other Mac.
    assert _push(hub, body, node).status_code == 403
    sha = next(iter(blobs))
    assert hub.put(f"/api/hub/release/blob/{sha}", headers=node, content=blobs[sha]).status_code == 403
    assert hub.get("/api/hub/release/need", headers=node).status_code == 403
    assert hub.post("/api/hub/release/stage", headers=node).status_code == 403
    assert hub.post("/api/hub/release/stage", headers=_fleet_headers()).status_code == 403
    assert hub.post("/api/hub/release/trust", headers=node, json={}).status_code == 403
    # The gateway's Fleet token can push and send blobs, and nothing anonymous can.
    assert hub.put(f"/api/hub/release/blob/{sha}", headers=_fleet_headers(), content=blobs[sha]).status_code == 200
    hub.cookies.clear()                       # the client remembers the session cookie a good call set
    assert hub.get("/api/hub/release/files").status_code == 401
    assert hub.put(f"/api/hub/release/blob/{sha}", content=blobs[sha]).status_code == 401


def test_only_the_newest_two_releases_are_kept(hub, tmp_path):
    from components.hub.backend import app_release
    seed = _seed()
    _trust(hub, seed)
    shas = []
    app_release.blob_dir().mkdir(parents=True, exist_ok=True)
    in_flight = app_release.blob_dir() / f".{'a' * 64}.inflight.part"
    abandoned = app_release.blob_dir() / f".{'b' * 64}.abandoned.part"
    in_flight.write_bytes(b"half")
    abandoned.write_bytes(b"half")
    os.utime(abandoned, (time.time() - 7200, time.time() - 7200))
    for number in range(3):
        files = {**FILES_A, R + "components/hub/backend/broker.py": f"# broker {number}\n".encode()}
        descriptor, body, _e, blobs = _release(seed, files, tmp_path, build=f"b{number}00000")
        _send_all(hub, blobs, _push(hub, body).json()["need"])
        shas.append((descriptor["files_sha256"],
                     hashlib.sha256(files[R + "components/hub/backend/broker.py"]).hexdigest()))
        time.sleep(0.02)
    kept = {p.name for p in (app_release.RELEASE_DIR / "manifests").iterdir()}
    assert kept == {shas[1][0], shas[2][0]}
    stored = {p.name for p in app_release.blob_dir().iterdir()}
    assert shas[0][1] not in stored, "the oldest release's own blob is collected"
    assert shas[1][1] in stored and shas[2][1] in stored
    assert in_flight.exists(), "collecting garbage must not delete an upload that is still arriving"
    assert not abandoned.exists(), "an upload abandoned hours ago is collected"


# ── a node mirroring its controller ────────────────────────────────────────


def _controller_answers(hub, seed, tmp_path, *, build="abc1234"):
    """What a controller says over HTTP for a pushed release, captured so a *node* (which shares
    this process's data directory) can be pointed at it with a mock transport."""
    _trust(hub, seed)
    descriptor, body, entries, blobs = _release(seed, FILES_A, tmp_path, build=build)
    need = _push(hub, body).json()["need"]
    _send_all(hub, blobs, need)
    headers = _fleet_headers()
    answers = {
        "/api/version": {"version": "0.6.99", "build": build},
        "/api/hub/release": hub.get("/api/hub/release", headers=headers).json(),
    }
    raw = {"/api/hub/release/files": hub.get("/api/hub/release/files", headers=headers).content}
    for name in body["file_manifest"]["groups"]:
        raw[f"/api/hub/release/files/{name}"] = hub.get(f"/api/hub/release/files/{name}", headers=headers).content
    return descriptor, answers, raw, blobs


def _become_a_node(controller_url="http://10.0.0.5:47873"):
    from components.hub.backend import control_plane, fleet_nodes, peers
    peers.set_fleet_token("N" * 32)
    fleet_nodes.set_node_credential("node-credential-value")
    control_plane.save_settings({"role": "agent", "fleet_credential_origin": "adopted",
                                 "parent_controller_url": controller_url})


def _reset_release_store():
    from components.hub.backend import app_release
    app_release.reset_for_tests()


def _mock_client(answers, raw, seen=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append((request.url.path, request.headers.get("x-hub-token")))
        if request.url.path in answers:
            return httpx.Response(200, json=answers[request.url.path])
        if request.url.path in raw:
            return httpx.Response(200, content=raw[request.url.path])
        return httpx.Response(404)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_a_node_mirrors_the_release_pins_the_gateway_key_and_countersigns_with_its_own(hub, tmp_path):
    from components.hub.backend import app_release, release_sync
    seed = _seed()
    descriptor, answers, raw, _blobs = _controller_answers(hub, seed, tmp_path)
    controller_identity_before = app_release.controller_identity()["fingerprint"]
    _reset_release_store()                 # now this process is the *node*: it holds nothing
    _become_a_node()
    assert app_release.trusted_publisher() is None
    seen: list = []

    result = asyncio.run(release_sync.sync_once(_mock_client(answers, raw, seen)))

    assert result.startswith("mirrored 0.6.99")
    held = app_release.load_release()
    assert held["build"] == descriptor["build"] and held["files_sha256"] == descriptor["files_sha256"]
    # Countersigned by THIS Mac's key, so its own app (which pinned its own Hub's key) accepts it.
    own = app_release.controller_identity()
    assert held["controller_key_id"] == own["fingerprint"]
    assert app_release.verify_countersignature(held, held["controller_signature"], bytes.fromhex(own["public_key"]))
    assert held["signature"] == descriptor["signature"], "the gateway's signature is carried unchanged"
    # Trust on first use: the controller's presented key is now this Mac's pin.
    assert app_release.trusted_publisher()["fingerprint"] == descriptor["publisher_key_id"]
    assert app_release.held_manifest(held) is not None
    assert not app_release.ARTIFACT_FILE.exists()
    # It presented its own node credential, not the group token.
    assert {token for path, token in seen if path.startswith("/api/hub/release")} == {"node-credential-value"}
    # A second poll changes nothing.
    assert asyncio.run(release_sync.sync_once(_mock_client(answers, raw))) == "already mirrored"


def test_a_node_waits_for_its_controller_to_run_the_release_first(hub, tmp_path):
    from components.hub.backend import app_release, release_sync
    seed = _seed()
    _d, answers, raw, _b = _controller_answers(hub, seed, tmp_path)
    _reset_release_store()
    _become_a_node()
    answers["/api/version"] = {"version": "0.6.98", "build": "old0000"}      # controller not updated yet
    result = asyncio.run(release_sync.sync_once(_mock_client(answers, raw)))
    assert "not installed" in result
    assert app_release.load_release() is None and app_release.trusted_publisher() is None


def test_a_node_refuses_a_release_the_pinned_key_did_not_sign(hub, tmp_path):
    from components.hub.backend import app_release, ed25519, release_sync
    seed = _seed()
    _d, answers, raw, _b = _controller_answers(hub, seed, tmp_path)
    _reset_release_store()
    _become_a_node()
    # This Mac already trusts a DIFFERENT gateway key: a controller must not be able to replace it.
    app_release.set_trusted_publisher({"public_key": ed25519.public_key(_seed()).hex()})
    with pytest.raises(app_release.ReleaseRefused):
        asyncio.run(release_sync.sync_once(_mock_client(answers, raw)))
    assert app_release.load_release() is None


def test_a_node_refuses_a_descriptor_whose_signature_or_manifest_was_altered(hub, tmp_path):
    from components.hub.backend import app_release, release_sync
    seed = _seed()
    _d, answers, raw, _b = _controller_answers(hub, seed, tmp_path)
    _reset_release_store()
    _become_a_node()

    forged = json.loads(json.dumps(answers))
    forged["/api/hub/release"]["release"]["version"] = "9.9.9"        # signature no longer covers it
    with pytest.raises(app_release.ReleaseRefused):
        asyncio.run(release_sync.sync_once(_mock_client(forged, raw)))

    bad_group = dict(raw)
    bad_group["/api/hub/release/files/core"] = gzip.compress(b"f\t0\t1\t" + b"a" * 64 + b"\tx\n")
    with pytest.raises(app_release.ReleaseRefused):
        asyncio.run(release_sync.sync_once(_mock_client(answers, bad_group)))
    assert app_release.load_release() is None


def test_a_mac_that_is_not_a_node_does_not_sync(hub, tmp_path):
    from components.hub.backend import release_sync
    assert asyncio.run(release_sync.sync_once(_mock_client({}, {}))) == "not a node"


# ── the stage job ──────────────────────────────────────────────────────────


def _wait_for_stage(hub, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = hub.get("/api/hub/release/stage", headers=_hub_headers()).json()
        if status["state"] != "running":
            return status
        time.sleep(0.05)
    raise AssertionError("the stage job did not finish")


def _installed_app(tmp_path, files, *, with_manifest=True):
    from components.hub.backend import file_manifest as fm
    app = _tree(tmp_path / "Applications" / "Studio Hub.app", files)
    if with_manifest:
        fm.write(app)
    os.environ["STUDIOHUB_RELEASE_MANIFEST"] = str(app / R / "release-manifest.json")
    return app


def test_the_stage_job_builds_the_release_from_the_installed_app_and_the_stored_blobs(hub, tmp_path):
    from components.hub.backend import file_manifest as fm
    seed = _seed()
    _trust(hub, seed)
    new_files = {**FILES_A, R + "components/hub/backend/broker.py": b"# broker b - changed\n",
                 R + "components/hub/backend/added.py": b"# added\n"}
    _d, body, entries, blobs = _release(seed, new_files, tmp_path, build="new0000")
    app = _installed_app(tmp_path, FILES_A)
    try:
        need = _push(hub, body).json()["need"]
        assert len(need) == 2, "only the changed and the new file: the rest is already in the installed app"
        _send_all(hub, blobs, need)
        started = hub.post("/api/hub/release/stage", headers=_hub_headers())
        assert started.status_code == 202
        status = _wait_for_stage(hub)
        assert status["state"] == "staged", status
        staged = Path(status["staged_app"])
        assert fm.scan(staged) == entries
        assert staged.parent.parent == app.parent
        assert fm.scan(app) == _entries(app) == fm.flatten(fm.load_dir(app / fm.DIR_REL)[1])
        # Asking again while a staged build for the same release is waiting returns it, not a rerun.
        again = hub.post("/api/hub/release/stage", headers=_hub_headers()).json()
        assert again["state"] == "staged" and again["staged_app"] == status["staged_app"]
    finally:
        os.environ.pop("STUDIOHUB_RELEASE_MANIFEST", None)


def test_the_stage_job_says_fallback_when_it_cannot_prove_the_release(hub, tmp_path):
    seed = _seed()
    _trust(hub, seed)
    new_files = {**FILES_A, R + "components/hub/backend/added.py": b"# added\n"}
    _d, body, _e, blobs = _release(seed, new_files, tmp_path)
    _installed_app(tmp_path, FILES_A)
    try:
        assert hub.post("/api/hub/release/stage", headers=_hub_headers()).status_code == 409   # nothing held
        _push(hub, body)                                            # blobs never sent, no controller to ask
        hub.post("/api/hub/release/stage", headers=_hub_headers())
        status = _wait_for_stage(hub)
        assert status["state"] == "fallback" and status["code"] == "missing_blob", status
        # A Hub that is not running from an installed app cannot stage either.
        os.environ.pop("STUDIOHUB_RELEASE_MANIFEST", None)
        hub.post("/api/hub/release/stage", headers=_hub_headers())
        status = _wait_for_stage(hub)
        assert status["state"] == "fallback" and status["code"] == "not_installed", status
    finally:
        os.environ.pop("STUDIOHUB_RELEASE_MANIFEST", None)


def test_a_node_fetches_missing_blobs_from_its_controller_by_sha(hub, tmp_path, monkeypatch):
    from components.hub.backend import app_release, file_manifest as fm, release_delta, release_sync
    seed = _seed()
    new_files = {**FILES_A, R + "components/hub/backend/added.py": b"# added by the new release\n"}
    descriptor, body, entries, blobs = _release(seed, new_files, tmp_path)
    _trust(hub, seed)
    _push(hub, body)
    answers = {"/api/version": {"version": "0.6.99", "build": "abc1234"},
               "/api/hub/release": hub.get("/api/hub/release", headers=_fleet_headers()).json()}
    raw = {"/api/hub/release/files": hub.get("/api/hub/release/files", headers=_fleet_headers()).content}
    for name in body["file_manifest"]["groups"]:
        raw[f"/api/hub/release/files/{name}"] = hub.get(f"/api/hub/release/files/{name}", headers=_fleet_headers()).content
    _reset_release_store()
    _become_a_node()
    asyncio.run(release_sync.sync_once(_mock_client(answers, raw)))
    assert app_release.held_manifest() is not None and not list(app_release.blob_dir().glob("*"))

    requested: list[str] = []

    def controller_blob(sha, destination, size):
        requested.append(sha)
        destination.write_bytes(blobs[sha])
    monkeypatch.setattr(release_sync, "_remote_blob", controller_blob)
    app = _installed_app(tmp_path, FILES_A)
    try:
        hub.post("/api/hub/release/stage", headers=_hub_headers())
        status = _wait_for_stage(hub)
        assert status["state"] == "staged", status
        assert fm.scan(Path(status["staged_app"])) == entries
        added = hashlib.sha256(new_files[R + "components/hub/backend/added.py"]).hexdigest()
        assert added in requested
        untouched = hashlib.sha256(FILES_A[R + "runtimes/voice/lib/big.so"]).hexdigest()
        assert untouched not in requested, "a file the installed app already has is never fetched"
    finally:
        os.environ.pop("STUDIOHUB_RELEASE_MANIFEST", None)
