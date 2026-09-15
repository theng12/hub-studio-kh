"""Process ownership and readiness tests for the packaged Hub wrapper.

The fixture copies the real Hub and Voice wrappers into a temporary app-like
bundle and supplies tiny FastAPI applications.  It never imports model code or
touches the repository model library.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen


WORKTREE = Path(__file__).resolve().parents[1]
_runtime_override = os.environ.get("STUDIOHUB_TEST_RUNTIME", "").strip()
_runtime_candidates = [
    Path(_runtime_override) if _runtime_override else None,
    WORKTREE / ".build/voice-runtime/bin/python3",
    WORKTREE.parents[1] / ".build/voice-runtime/bin/python3",
]
RUNTIME_SOURCE = next((path for path in _runtime_candidates if path and path.is_file()),
                      Path(sys.executable))
TOKEN = "fixture_hub_token_1234567890_abcdefgh"
VERSION = "0.2.0-fixture"
RUNTIME_ID = "d" * 64


class HubProcessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not RUNTIME_SOURCE.is_file():
            raise unittest.SkipTest(f"root Voice runtime is unavailable: {RUNTIME_SOURCE}")
        probe = subprocess.run([str(RUNTIME_SOURCE), "-I", "-c", "import fastapi, uvicorn"],
                               capture_output=True, text=True)
        if probe.returncode:
            raise unittest.SkipTest(f"FastAPI/Uvicorn unavailable in test runtime: {probe.stderr.strip()}")

    def _fixture(self, base: Path, *, failing_voice: bool = False) -> dict[str, Path]:
        resources = base / "Proof.app" / "Contents" / "Resources"
        resources.mkdir(parents=True)
        shutil.copyfile(WORKTREE / "packaging/hub_worker.py", resources / "hub_worker.py")
        shutil.copyfile(WORKTREE / "packaging/voice_worker.py", resources / "voice_worker.py")

        hub_backend = resources / "components/hub/backend"
        hub_backend.mkdir(parents=True)
        (resources / "components/__init__.py").write_text("")
        (resources / "components/hub/__init__.py").write_text("")
        (hub_backend / "__init__.py").write_text("")
        (hub_backend / "auth.py").write_text(
            """import os\nfrom pathlib import Path\n\ndef load_token():\n    path = Path(os.environ['STUDIOHUB_DATA_DIR']) / '.hub_token'\n    return path.read_text().strip()\n"""
        )
        (resources / "components/hub/standalone.py").write_text(
            """import json\nimport os\nfrom pathlib import Path\nfrom fastapi import FastAPI, Request\nfrom fastapi.responses import JSONResponse\n\napp = FastAPI()\nTOKEN = (Path(os.environ['STUDIOHUB_DATA_DIR']) / '.hub_token').read_text().strip()\n\ndef _workers():\n    path = Path(os.environ['STUDIOHUB_WORKERS_FILE'])\n    return json.loads(path.read_text())\n\n@app.get('/api/health')\ndef health():\n    return {'ok': True, 'component': 'hub'}\n\n@app.get('/api/version')\ndef version():\n    release = json.loads(Path(os.environ['STUDIOHUB_RELEASE_MANIFEST']).read_text())\n    return {'version': release['version'], 'schema': release['schema']}\n\n@app.get('/api/hub/workers')\ndef workers(request: Request):\n    if request.headers.get('x-hub-token') != TOKEN:\n        return JSONResponse({'detail': 'Hub token required'}, status_code=401)\n    allowed = ('id', 'modality', 'title', 'host', 'port', 'machine', 'local', 'runtime_identity', 'pid')\n    return {'workers': [{key: worker[key] for key in allowed if key in worker}\n                        for worker in _workers()]}\n"""
        )

        voice_backend = resources / "voice/app/backend"
        voice_backend.mkdir(parents=True)
        (voice_backend / "__init__.py").write_text("")
        if failing_voice:
            voice_source = "raise RuntimeError('fixture voice startup failure')\n"
        else:
            voice_source = """from fastapi import FastAPI\n\napp = FastAPI()\n\n@app.get('/api/health')\ndef health():\n    return {'ok': True, 'component': 'voice'}\n"""
        (voice_backend / "main.py").write_text(voice_source)

        runtime_python = resources / "runtimes/voice/bin/python3"
        runtime_python.parent.mkdir(parents=True)
        runtime_python.symlink_to(RUNTIME_SOURCE)
        models = base / "Model Library"
        models.mkdir()
        data = base / "Node State"
        token_file = data / "hub/.hub_token"
        token_file.parent.mkdir(parents=True)
        token_file.parent.chmod(0o700)
        token_file.write_text(TOKEN + "\n")
        token_file.chmod(0o600)
        ready = data / "hub-ready.json"
        release = {
            "schema": "studiohub.standalone-release",
            "schema_version": 1,
            "version": VERSION,
            "implementation_commit": "a" * 40,
            "components": {
                "hub": {
                    "upstream_commit": "b" * 40,
                    "upstream_version": "fixture-hub",
                    "source_sha256": "c" * 64,
                }
            },
            "runtime_sha256": RUNTIME_ID,
        }
        release_path = resources / "release-manifest.json"
        release_path.write_text(json.dumps(release))
        return {
            "base": base,
            "resources": resources,
            "runtime_python": runtime_python,
            "models": models,
            "data": data,
            "ready": ready,
            "child_ready": data / "hub/voice-ready.json",
            "workers": data / "hub/owned-workers.json",
            "release": release_path,
        }

    @staticmethod
    def _alive(pid: int) -> bool:
        result = subprocess.run(["ps", "-p", str(pid), "-o", "stat="],
                                capture_output=True, text=True)
        state = result.stdout.strip()
        return bool(state) and not state.startswith("Z")

    @staticmethod
    def _command(pid: int) -> str:
        result = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                                capture_output=True, text=True)
        return result.stdout.strip()

    @classmethod
    def _stop_pid(cls, pid: int | None) -> None:
        if not pid or not cls._alive(pid):
            return
        command = cls._command(pid)
        if "voice_worker.py" not in command and "hub_worker.py" not in command:
            return
        try:
            pgid = os.getpgid(pid)
            if pgid == pid:
                os.killpg(pgid, signal.SIGTERM)
            else:
                os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + 4
        while cls._alive(pid) and time.monotonic() < deadline:
            time.sleep(0.02)
        if cls._alive(pid):
            try:
                pgid = os.getpgid(pid)
                if pgid == pid:
                    os.killpg(pgid, signal.SIGKILL)
                else:
                    os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    @classmethod
    def _wait_for(cls, predicate, timeout: float, message: str):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(0.02)
        raise AssertionError(message)

    @classmethod
    def _read_json(cls, path: Path) -> dict:
        return json.loads(path.read_text())

    @classmethod
    def _get_json(cls, url: str, token: str | None = None) -> tuple[int, dict]:
        headers = {"X-Hub-Token": token} if token else {}
        try:
            with urlopen(Request(url, headers=headers), timeout=3) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            return error.code, json.loads(error.read())

    @contextmanager
    def _running_hub(self, *, failing_voice: bool = False):
        with tempfile.TemporaryDirectory(prefix="studiohub-hub-process-") as temp:
            fixture = self._fixture(Path(temp), failing_voice=failing_voice)
            environment = {
                "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                "LANG": "en_US.UTF-8",
            }
            process = subprocess.Popen([
                str(fixture["runtime_python"]), "-I", "-B",
                str(fixture["resources"] / "hub_worker.py"),
                "--data-dir", str(fixture["data"]),
                "--models-dir", str(fixture["models"]),
                "--ready-file", str(fixture["ready"]),
                "--parent-pid", str(os.getpid()), "--port", "0", "--offline",
            ], cwd=fixture["base"], env=environment,
               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            known_children: list[int] = []
            try:
                yield fixture, process, known_children
            finally:
                self._stop_pid(process.pid)
                if process.poll() is None:
                    process.kill()
                try:
                    process.communicate(timeout=4)
                except subprocess.TimeoutExpired:
                    pass
                for pid in known_children:
                    self._stop_pid(pid)

    def _await_started(self, fixture: dict[str, Path], process: subprocess.Popen, known_children: list[int]):
        self._wait_for(lambda: fixture["ready"].exists() or process.poll() is not None,
                       8, "Hub did not publish readiness")
        if process.poll() is not None:
            self.fail(self._process_error(process))
        hub_ready = self._read_json(fixture["ready"])
        self._wait_for(lambda: fixture["child_ready"].exists(), 5,
                       "Voice worker did not publish readiness")
        child_ready = self._read_json(fixture["child_ready"])
        child_pid = child_ready["pid"]
        known_children.append(child_pid)
        self._wait_for(lambda: fixture["workers"].exists(), 5,
                       "Hub did not publish its worker descriptor")
        workers = self._read_json(fixture["workers"])
        return hub_ready, child_ready, workers, child_pid

    @staticmethod
    def _process_error(process: subprocess.Popen) -> str:
        try:
            stdout, stderr = process.communicate(timeout=2)
            return f"stdout={stdout}\nstderr={stderr}"
        except subprocess.TimeoutExpired as error:
            return f"process exited but descendants still hold output pipes: {error}"

    def test_hub_publishes_private_readiness_and_worker_identity(self):
        with self._running_hub() as (fixture, process, known_children):
            hub_ready, child_ready, workers, child_pid = self._await_started(
                fixture, process, known_children)
            self.assertEqual(hub_ready["pid"], process.pid)
            self.assertEqual(hub_ready["version"], VERSION)
            self.assertEqual(hub_ready["token"], TOKEN)
            self.assertEqual(fixture["ready"].stat().st_mode & 0o777, 0o600)
            self.assertEqual(child_ready["pid"], child_pid)
            self.assertEqual(len(workers), 1)
            worker = workers[0]
            self.assertEqual(worker["id"], "voice")
            self.assertEqual(worker["host"], "127.0.0.1")
            self.assertEqual(worker["port"], int(child_ready["url"].rsplit(":", 1)[1]))
            self.assertEqual(worker["pid"], child_pid)
            self.assertEqual(worker["hub_token"], TOKEN)
            self.assertEqual(worker["studio_token"], TOKEN)
            self.assertEqual(worker["runtime_identity"], {"id": RUNTIME_ID, "compatible": True})

            status, health = self._get_json(hub_ready["url"] + "/api/health")
            self.assertEqual((status, health), (200, {"ok": True, "component": "hub"}))
            status, version = self._get_json(hub_ready["url"] + "/api/version")
            self.assertEqual((status, version),
                             (200, {"version": VERSION, "schema": "studiohub.standalone-release"}))
            status, exposed = self._get_json(hub_ready["url"] + "/api/hub/workers", TOKEN)
            self.assertEqual(status, 200)
            self.assertEqual(exposed["workers"][0]["id"], "voice")
            self.assertEqual(exposed["workers"][0]["port"], worker["port"])
            self.assertNotIn("hub_token", exposed["workers"][0])

            status, voice_health = self._get_json(child_ready["url"] + "/api/health", TOKEN)
            self.assertEqual((status, voice_health), (200, {"ok": True, "component": "voice"}))

    def test_graceful_hub_sigterm_removes_readiness_and_stops_voice(self):
        with self._running_hub() as (fixture, process, known_children):
            _, _, _, child_pid = self._await_started(fixture, process, known_children)
            process.terminate()
            process.communicate(timeout=8)
            self.assertFalse(self._alive(child_pid), "Voice child survived graceful Hub shutdown")
            self.assertFalse(fixture["ready"].exists())
            self.assertFalse(fixture["child_ready"].exists())
            self.assertFalse(fixture["workers"].exists())

    def test_abrupt_hub_death_triggers_voice_parent_watchdog(self):
        with self._running_hub() as (fixture, process, known_children):
            _, _, _, child_pid = self._await_started(fixture, process, known_children)
            self.assertEqual(os.getpgid(child_pid), child_pid)
            os.kill(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
            self._wait_for(lambda: not self._alive(child_pid), 5,
                           "Voice parent watchdog did not stop the orphaned child")
            self.assertFalse(fixture["child_ready"].exists())

    def test_owned_voice_crash_stops_hub_and_removes_worker_descriptor(self):
        with self._running_hub() as (fixture, process, known_children):
            _, _, _, child_pid = self._await_started(fixture, process, known_children)
            os.kill(child_pid, signal.SIGKILL)
            self._wait_for(lambda: not self._alive(child_pid), 5,
                           "Voice child did not exit after the test crash")
            self._wait_for(lambda: process.poll() is not None, 8,
                           "Hub stayed alive after its owned Voice worker crashed")
            self.assertFalse(fixture["ready"].exists())
            self.assertFalse(fixture["child_ready"].exists())
            self.assertFalse(fixture["workers"].exists())

    def test_voice_startup_failure_never_publishes_hub_readiness(self):
        with self._running_hub(failing_voice=True) as (fixture, process, known_children):
            self._wait_for(lambda: process.poll() is not None, 8,
                           "Hub stayed alive after private Voice startup failure")
            self.assertFalse(fixture["ready"].exists())
            self.assertFalse(fixture["child_ready"].exists())
            self.assertFalse(fixture["workers"].exists())


if __name__ == "__main__":
    unittest.main()
