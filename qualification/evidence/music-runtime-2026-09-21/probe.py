"""Exercise a staged Music bundle with disposable state and no model assets."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[3]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    app = args.app.resolve()
    resources = app / "Contents/Resources"
    spec = importlib.util.spec_from_file_location("stage_voice", ROOT / "packaging/stage_voice.py")
    stage = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stage)
    digest = stage.tree_digest(app)
    assert digest == app.with_name(app.name + ".tree-sha256").read_text().strip()
    release = json.loads((resources / "release-manifest.json").read_text())
    receipt = {"app": str(app), "tree_sha256": digest,
               "identity": {key: release[key] for key in
                            ("version", "implementation_commit", "development_build")},
               "model_inference": False, "source_sha256": {}, "checks": {}}
    for source, bundled in (
        ("components/hub/backend/broker.py", "components/hub/backend/broker.py"),
        ("components/hub/standalone.py", "components/hub/standalone.py"),
        ("components/music/app/backend/generation.py", "components/music/app/backend/generation.py"),
        ("components/music/app/backend/engine_child.py", "components/music/app/backend/engine_child.py"),
        ("packaging/music_worker.py", "music_worker.py"),
        ("packaging/hub_worker.py", "hub_worker.py"),
    ):
        content = (ROOT / source).read_bytes()
        assert content == (resources / bundled).read_bytes(), source
        receipt["source_sha256"][source] = hashlib.sha256(content).hexdigest()
    with tempfile.TemporaryDirectory(prefix="studiohub-music-proof-") as temporary:
        root = Path(temporary)
        data, models = root / "state", root / "models"
        data.mkdir(mode=0o700)
        models.mkdir(mode=0o700)
        ready = data / "hub-ready.json"
        owned = set()
        with (root / "service.log").open("w") as log:
            process = subprocess.Popen([
                str(app / "Contents/MacOS/StudioHubService"),
                "--data-dir", str(data), "--models-dir", str(models),
                "--ready-file", str(ready), "--parent-pid", str(os.getpid()),
                "--host", "127.0.0.1", "--port", "0", "--offline",
            ], stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            started = time.monotonic()
            try:
                while not ready.exists():
                    assert process.poll() is None, "service exited before readiness"
                    assert time.monotonic() - started < 90, "startup timed out"
                    time.sleep(0.1)
                info = json.loads(ready.read_text())
                assert (data / "hub/owned-workers.json").is_file()
                receipt["ready_seconds"] = round(time.monotonic() - started, 2)

                def request(path, authenticated=True):
                    headers = {"Authorization": "Bearer " + info["token"]} if authenticated else {}
                    with urlopen(Request(info["url"] + path, headers=headers), timeout=15) as response:
                        assert response.status == 200
                        receipt["checks"][path] = 200
                        return json.load(response)

                assert request("/api/health")["ok"] is True
                workers = request("/api/hub/workers")["workers"]
                receipt["modalities"] = sorted(row["modality"] for row in workers)
                assert {"voice", "transcription", "music"} <= set(receipt["modalities"])
                music = next(row for row in workers if row["modality"] == "music")
                health = request("/studio/music/api/health")
                identity = release["components"]["music"]["runtime_sha256"]
                assert music["runtime_identity"]["id"] == identity
                assert health["generation"]["runtime_revision"] == identity
                assert health["generation"]["checkpoints"] == "missing"
                assert health["generation"]["engine_loaded"] is False
                receipt["music_runtime_sha256"] = identity
                request("/studio/music/api/catalog")
                try:
                    request("/studio/music/api/health", authenticated=False)
                    raise AssertionError("Music route allowed unauthenticated access")
                except HTTPError as error:
                    assert error.code in (401, 403)
                    receipt["checks"]["unauthenticated_music"] = error.code
            finally:
                for descriptor in data.rglob("*ready.json"):
                    owned.add(json.loads(descriptor.read_text())["pid"])
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=5)
                receipt["service_exit"] = process.returncode
        deadline = time.monotonic() + 10
        while owned and time.monotonic() < deadline:
            for pid in list(owned):
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    owned.remove(pid)
            time.sleep(0.1)
        assert not owned, f"owned processes remain: {owned}"
        assert not list(data.rglob("*ready.json")), "readiness survived shutdown"
        assert not (data / "hub/owned-workers.json").exists(), "worker descriptor survived shutdown"
        log = (root / "service.log").read_text()
        assert "Traceback (most recent call last)" not in log
        assert "Application shutdown failed" not in log
        receipt["checks"]["owned_processes_stopped"] = True
    assert stage.tree_digest(app) == digest, "bundle changed during execution"
    receipt["checks"]["bundle_unchanged"] = True
    receipt["ok"] = True
    args.output.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
