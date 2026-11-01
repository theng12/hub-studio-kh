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

    def _fixture(self, base: Path, *, failing_voice: bool = False, flaky_voice: bool = False,
                 image: bool = False, orphan_image: bool = False) -> dict[str, Path]:
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
            """import json\nimport os\nfrom pathlib import Path\nfrom fastapi import FastAPI, Request\nfrom fastapi.responses import JSONResponse\n\napp = FastAPI()\nTOKEN = (Path(os.environ['STUDIOHUB_DATA_DIR']) / '.hub_token').read_text().strip()\n\n# The child watcher reads this Hub's own monitor, exactly as it does in production: a studio's\n# row there is the only figure that knows a silent studio from one with a long job in flight.\n# The fixture cannot run the real monitor, so a test-only route sets the row it would report.\nclass Monitor:\n    def __init__(self):\n        self.status = {}\n\napp.state.monitor = Monitor()\n\ndef _workers():\n    path = Path(os.environ['STUDIOHUB_WORKERS_FILE'])\n    return json.loads(path.read_text())\n\n@app.get('/api/health')\ndef health():\n    return {'ok': True, 'component': 'hub'}\n\n@app.get('/api/version')\ndef version():\n    release = json.loads(Path(os.environ['STUDIOHUB_RELEASE_MANIFEST']).read_text())\n    return {'version': release['version'], 'schema': release['schema']}\n\n@app.get('/api/hub/workers')\ndef workers(request: Request):\n    if request.headers.get('x-hub-token') != TOKEN:\n        return JSONResponse({'detail': 'Hub token required'}, status_code=401)\n    allowed = ('id', 'modality', 'title', 'host', 'port', 'machine', 'local', 'runtime_identity', 'pid')\n    return {'workers': [{key: worker[key] for key in allowed if key in worker}\n                        for worker in _workers()]}\n\n@app.post('/test/studio-status')\nasync def studio_status(request: Request):\n    if request.headers.get('x-hub-token') != TOKEN:\n        return JSONResponse({'detail': 'Hub token required'}, status_code=401)\n    body = json.loads(await request.body() or b'{}')\n    app.state.monitor.status[body['studio']] = {\n        'status': body['status'], 'last_seen': body.get('last_seen')}\n    return {'ok': True}\n"""
        )
        (hub_backend / "alerts.py").write_text(
            """import json\nimport os\nimport time\nfrom pathlib import Path\n\ndef emit(kind, message, data=None):\n    path = Path(os.environ['STUDIOHUB_DATA_DIR']) / 'fixture-alerts.jsonl'\n    with path.open('a') as stream:\n        stream.write(json.dumps({'kind': kind, 'message': message, 'data': data or {}}) + '\\n')\n    return {'kind': kind, 'message': message}\n"""
        )

        voice_backend = resources / "voice/app/backend"
        voice_backend.mkdir(parents=True)
        (voice_backend / "__init__.py").write_text("")
        if failing_voice:
            voice_source = "raise RuntimeError('fixture voice startup failure')\n"
        elif flaky_voice:
            # Starts once, then exits on every later start: the shape of a studio whose restart
            # keeps failing while the Hub itself stays up and answering.
            voice_source = (
                "import os\n"
                "from pathlib import Path\n"
                "marker = Path(os.environ['STUDIOHUB_DATA_DIR']) / 'voice-started-once'\n"
                "if marker.exists():\n"
                "    os._exit(1)\n"
                "marker.write_text('1')\n"
                "from fastapi import FastAPI\n"
                "app = FastAPI()\n"
                "@app.get('/api/health')\n"
                "def health():\n"
                "    return {'ok': True, 'component': 'voice'}\n"
            )
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
                },
                # The fixture stages a real Voice runtime and backend just below, so its release
                # record has to say so. `worker_specs` spawns exactly the components the manifest
                # lists, and the Hub refuses a manifest that does not identify Voice rather than
                # starting with no worker for the studio it always owns.
                "voice": {"runtime_sha256": RUNTIME_ID},
            },
            "runtime_sha256": RUNTIME_ID,
        }
        if image:
            shutil.copyfile(WORKTREE / "packaging/image_worker.py", resources / "image_worker.py")
            backend = resources / "components/image/app/backend"
            backend.mkdir(parents=True)
            for package in [backend, backend.parent, backend.parent.parent]:
                (package / "__init__.py").write_text("")
            image_source = "from fastapi import FastAPI\ndef from_environment():\n    return FastAPI()\n"
            if orphan_image:
                image_source = """from fastapi import FastAPI
import os
from pathlib import Path
import subprocess
import sys

def from_environment():
    marker = Path(os.environ['STUDIOHUB_IMAGE_DATA_DIR']) / 'fixture-leaf.pid'
    code = ('import os,signal,sys,time; from pathlib import Path; '
            'signal.signal(signal.SIGTERM, signal.SIG_IGN); '
            'Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(120)')
    subprocess.Popen([sys.executable, '-c', code, str(marker), 'studiohub-orphan-leaf'])
    return FastAPI()
"""
            (backend / "main.py").write_text(image_source)
            image_python = resources / "runtimes/image/bin/python3"
            image_python.parent.mkdir(parents=True)
            image_python.symlink_to(RUNTIME_SOURCE)
            catalog = resources / "model-manifests"
            catalog.mkdir()
            (catalog / "flux2-klein-4b-mlx-4bit.json").write_text("{}")
            release["components"]["image"] = {"runtime_sha256": "e" * 64}
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
            "alerts": data / "hub/fixture-alerts.jsonl",
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
        if ("voice_worker.py" not in command and "hub_worker.py" not in command
                and "studiohub-orphan-leaf" not in command):
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
    def _running_hub(self, *, failing_voice: bool = False, flaky_voice: bool = False,
                     image: bool = False, orphan_image: bool = False):
        with tempfile.TemporaryDirectory(prefix="studiohub-hub-process-") as temp:
            fixture = self._fixture(Path(temp), failing_voice=failing_voice,
                                    flaky_voice=flaky_voice, image=image,
                                    orphan_image=orphan_image)
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
            self.assertEqual(worker["managed_output_roots"], [str((fixture["data"] / "voice/output").resolve())])

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

    def _await_restart(self, fixture, process, ready_file: Path, old_pid: int) -> dict:
        def restarted():
            try:
                value = self._read_json(ready_file)
            except (OSError, ValueError):
                return None
            return value if value["pid"] != old_pid and self._alive(value["pid"]) else None
        value = self._wait_for(restarted, 10, "Hub did not restart its crashed worker")
        self.assertIsNone(process.poll(), "Hub stopped after one worker crashed")
        return value

    def test_owned_voice_crash_restarts_voice_and_keeps_the_hub(self):
        """One worker exiting must not stop the Hub; it used to stop it and every other studio."""
        with self._running_hub() as (fixture, process, known_children):
            hub_ready, child_ready, _, child_pid = self._await_started(fixture, process, known_children)
            os.kill(child_pid, signal.SIGKILL)
            again = self._await_restart(fixture, process, fixture["child_ready"], child_pid)
            known_children.append(again["pid"])
            # Same endpoint, so the Hub's worker descriptor stays valid; only the pid changes.
            self.assertEqual(again["url"], child_ready["url"])
            self._wait_for(lambda: self._read_json(fixture["workers"])[0]["pid"] == again["pid"], 5,
                           "Worker descriptor still names the dead pid")
            status, health = self._get_json(again["url"] + "/api/health", TOKEN)
            self.assertEqual((status, health), (200, {"ok": True, "component": "voice"}))
            status, _ = self._get_json(hub_ready["url"] + "/api/health")
            self.assertEqual(status, 200)

    def test_image_crash_restarts_image_and_leaves_voice_running(self):
        with self._running_hub(image=True) as (fixture, process, known_children):
            _, _, workers, voice_pid = self._await_started(fixture, process, known_children)
            self.assertEqual({worker["id"] for worker in workers}, {"voice", "image"})
            image = next(worker for worker in workers if worker["id"] == "image")
            known_children.append(image["pid"])
            self.assertEqual(image["runtime_identity"], {"id": "e" * 64, "compatible": True})
            self.assertEqual(image["studio_token"], TOKEN)
            self.assertNotEqual(image["port"], workers[0]["port"])
            self.assertEqual(image["managed_output_roots"], [str((fixture["data"] / "image/outputs").resolve())])
            os.kill(image["pid"], signal.SIGKILL)
            again = self._await_restart(fixture, process, fixture["data"] / "hub/image-ready.json",
                                        image["pid"])
            known_children.append(again["pid"])
            self.assertTrue(self._alive(voice_pid), "Voice was stopped because Image crashed")
            self.assertEqual(int(again["url"].rsplit(":", 1)[1]), image["port"])
            process.terminate()
            process.communicate(timeout=15)
            self.assertFalse(self._alive(again["pid"]), "Restarted Image survived Hub shutdown")
            self.assertFalse(self._alive(voice_pid), "Voice survived Hub shutdown")
            self.assertFalse(fixture["workers"].exists())

    def test_image_restart_reaps_an_orphaned_leaf_from_the_old_worker_group(self):
        """A dead worker leader must not leave its native inference leaf beside its replacement."""
        with self._running_hub(image=True, orphan_image=True) as (
                fixture, process, known_children):
            _, _, workers, _ = self._await_started(fixture, process, known_children)
            image = next(worker for worker in workers if worker["id"] == "image")
            known_children.append(image["pid"])
            marker = fixture["data"] / "image/fixture-leaf.pid"
            self._wait_for(marker.exists, 5, "Image fixture leaf did not start")
            old_leaf = int(marker.read_text())
            known_children.append(old_leaf)
            self.assertTrue(self._alive(old_leaf))
            self.assertEqual(os.getpgid(old_leaf), image["pid"])

            os.kill(image["pid"], signal.SIGKILL)
            again = self._await_restart(
                fixture, process, fixture["data"] / "hub/image-ready.json", image["pid"])
            known_children.append(again["pid"])
            self._wait_for(lambda: marker.exists() and int(marker.read_text()) != old_leaf,
                           5, "Restarted Image fixture leaf did not start")
            new_leaf = int(marker.read_text())
            known_children.append(new_leaf)
            self._wait_for(lambda: not self._alive(old_leaf), 5,
                           "Old Image leaf survived its worker restart")
            self.assertTrue(self._alive(new_leaf))
            self.assertEqual(os.getpgid(new_leaf), again["pid"])

    @staticmethod
    def _set_studio_status(hub_ready: dict, studio: str, status: str, last_seen: float | None = None):
        """Tell the fixture's monitor row what the real monitor would report for that studio."""
        body = json.dumps({"studio": studio, "status": status, "last_seen": last_seen}).encode()
        request = Request(hub_ready["url"] + "/test/studio-status", data=body, method="POST",
                          headers={"X-Hub-Token": TOKEN, "Content-Type": "application/json"})
        with urlopen(request, timeout=5) as response:
            assert json.loads(response.read())["ok"]

    def test_a_silent_studio_is_restarted_with_a_reason_the_fleet_can_read(self):
        """A studio that is alive but has stopped answering is the shape of the outages at sites
        1400 and 1301: the Hub answered every request while no studio ever came back, and only a
        hand-run `start_services` fixed it. The Hub restarts it itself, and says why on the
        alert channel the service log and a configured webhook already carry."""
        with self._running_hub() as (fixture, process, known_children):
            hub_ready, child_ready, _, child_pid = self._await_started(
                fixture, process, known_children)
            self._set_studio_status(hub_ready, "voice", "down", time.time() - 1000)

            again = self._await_restart(fixture, process, fixture["child_ready"], child_pid)
            known_children.append(again["pid"])
            self.assertEqual(again["url"], child_ready["url"],
                             "the restart must keep the studio on its own port")
            status, _ = self._get_json(hub_ready["url"] + "/api/health")
            self.assertEqual(status, 200, "the Hub must stay up across a studio restart")

            def published():
                if not fixture["alerts"].exists():
                    return None
                rows = [json.loads(line) for line in fixture["alerts"].read_text().splitlines()]
                return next((row for row in rows if row["kind"] == "studio_restarted"), None)

            alert = self._wait_for(published, 5,
                                   "the restart reason was not published on the alert channel")
            self.assertEqual(alert["data"]["studio"], "voice")
            self.assertIn("stopped answering health", alert["message"])
            self.assertIn("monitor reports it down", alert["message"])

            # The reason stops firing once the monitor says the studio is answering again, so a
            # healthy studio is never restarted twice for one silence.
            self._set_studio_status(hub_ready, "voice", "up", time.time())
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                self.assertEqual(self._read_json(fixture["child_ready"])["pid"], again["pid"],
                                 "the studio was restarted while the monitor reported it up")
                time.sleep(0.2)

    def test_a_studio_the_monitor_reports_up_is_never_restarted(self):
        """Silence alone must not restart a studio: a long model job may answer slowly, and
        killing it is worse than the silence it is answering with."""
        with self._running_hub() as (fixture, process, known_children):
            hub_ready, _, _, child_pid = self._await_started(fixture, process, known_children)
            self._set_studio_status(hub_ready, "voice", "up", time.time())
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                self.assertEqual(self._read_json(fixture["child_ready"])["pid"], child_pid,
                                 "a studio the monitor reports up was restarted")
                self.assertIsNone(process.poll(), "the Hub stopped")
                time.sleep(0.2)

    def test_a_restart_that_keeps_failing_never_ends_the_hub_or_its_watcher(self):
        """The watcher thread is the only thing that brings a studio back. If a restart fails and
        the thread ends with it, the Hub answers every request for ever while no studio ever
        returns - which is the shape of the outages this change exists to stop. The restart
        ladder is expected to keep trying; the Hub is expected to keep serving."""
        with self._running_hub(flaky_voice=True) as (fixture, process, known_children):
            hub_ready, _, _, child_pid = self._await_started(fixture, process, known_children)
            os.kill(child_pid, signal.SIGKILL)
            deadline = time.monotonic() + 20

            def attempts():
                if not fixture["alerts"].exists():
                    return 0
                return len([line for line in fixture["alerts"].read_text().splitlines()
                            if '"studio_restarted"' in line])

            def enough():
                value = attempts()
                return value if value >= 3 else None

            count = self._wait_for(enough, deadline,
                                   "the Hub never retried a studio whose restart kept failing")
            self.assertIsNone(process.poll(), "the Hub stopped while its studio kept failing")
            status, health = self._get_json(hub_ready["url"] + "/api/health")
            self.assertEqual((status, health), (200, {"ok": True, "component": "hub"}),
                             "the Hub must keep answering while a studio cannot start")
            self.assertGreaterEqual(count, 3)

    def test_voice_startup_failure_never_publishes_hub_readiness(self):
        with self._running_hub(failing_voice=True) as (fixture, process, known_children):
            self._wait_for(lambda: process.poll() is not None, 8,
                           "Hub stayed alive after private Voice startup failure")
            self.assertFalse(fixture["ready"].exists())
            self.assertFalse(fixture["child_ready"].exists())
            self.assertFalse(fixture["workers"].exists())


if __name__ == "__main__":
    unittest.main()
