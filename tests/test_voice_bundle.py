"""Exercise relocation, external state and the worker's launch boundary."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.parse
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]

# The runtime names the installer already installed on the fleet (0.6.198, commit 20670dd)
# accepts. `verifyDeclaredComponents` refuses any manifest component whose runtime is not here,
# before a byte is copied; 0.6.199's top-level 'khu-agent' component was refused that way on
# every 0.6.198 Mac. Pinned as a fixture, re-derived from the commit in
# tests/test_installer_knows_shipped_runtimes.py.
FLEET_198_INSTALLER_RUNTIMES = frozenset(json.loads(
    (ROOT / 'tests/native/fixtures/update-client-runtimes-0.6.198.json').read_text())['runtimes'])


class BundleTests(unittest.TestCase):
    def _running_uvicorn(self):
        return '''\
import asyncio
from contextlib import nullcontext
import signal
import time

class Socket:
    def getsockname(self):
        return ('127.0.0.1', 54321)

class Config:
    def __init__(self, app, host, port, log_level):
        self.app = app
    def bind_socket(self):
        return nullcontext(Socket())

class Server:
    def __init__(self, config):
        self.config = config
        self.should_exit = False
    async def startup(self, sockets=None):
        await asyncio.sleep(0)
    def run(self, sockets=None):
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, 'should_exit', True))
        asyncio.run(self.startup(sockets=sockets))
        while not self.should_exit:
            time.sleep(0.01)
'''

    def _worker_fixture(self, base, uvicorn_source):
        resources = base / 'Proof.app' / 'Contents' / 'Resources'
        resources.mkdir(parents=True)
        shutil.copyfile(ROOT / 'packaging/voice_worker.py', resources / 'voice_worker.py')
        source = resources / 'voice' / 'app'
        source.mkdir(parents=True)
        if uvicorn_source is not None:
            (source / 'uvicorn.py').write_text(uvicorn_source)
        (resources / 'runtimes' / 'voice' / 'bin').mkdir(parents=True)
        (resources / 'release-manifest.json').write_text(json.dumps({
            'schema': 'studiohub.standalone-release', 'schema_version': 1,
            'version': '0.2.0', 'implementation_commit': 'a' * 40,
            'components': {'voice': {'upstream_commit': 'b' * 40,
                'upstream_version': '2.7.2', 'source_sha256': 'c' * 64}},
            'runtime_sha256': 'd' * 64,
        }))
        return resources

    def test_worker_validates_missing_selected_model_library_without_creating_it(self):
        with tempfile.TemporaryDirectory() as temp:
            missing = Path(temp) / 'unmounted SSD' / 'models'
            command = [sys.executable, '-I', '-B', str(ROOT / 'packaging/voice_worker.py'),
                       '--models-dir', str(missing), '--check']
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn('Selected model library is unavailable', result.stderr)
            self.assertFalse(missing.exists())

    def test_worker_check_uses_bundle_paths_after_relocation(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            resources = base / 'Moved app with spaces' / 'Contents' / 'Resources'
            resources.mkdir(parents=True)
            shutil.copyfile(ROOT / 'packaging/voice_worker.py', resources / 'voice_worker.py')
            (resources / 'voice' / 'app').mkdir(parents=True)
            (resources / 'runtimes' / 'voice' / 'bin').mkdir(parents=True)
            (resources / 'release-manifest.json').write_text(json.dumps({
                'schema': 'studiohub.standalone-release', 'schema_version': 1,
                'version': '0.1.0', 'implementation_commit': 'a' * 40,
                'components': {'voice': {'upstream_commit': 'b' * 40,
                    'upstream_version': '2.7.2', 'source_sha256': 'c' * 64}},
                'runtime_sha256': 'd' * 64,
            }))
            models = base / 'Model Library'
            models.mkdir()
            state = base / 'Node State'
            result = subprocess.run([sys.executable, '-I', '-B', str(resources / 'voice_worker.py'),
                '--models-dir', str(models), '--data-dir', str(state), '--offline', '--check'],
                capture_output=True, text=True, env={'PATH': '/usr/bin:/bin',
                    'HF_HOME': '/must/not/inherit', 'PYTHONPATH': '/must/not/inherit',
                    'KH_STUDIO_TOKEN': 'must-not-inherit'})
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(result.stdout)
            self.assertEqual(data['models_dir'], str(models.resolve()))
            self.assertEqual(data['data_dir'], str(state.resolve()))
            self.assertEqual(data['source_dir'], str((resources / 'voice' / 'app').resolve()))
            self.assertEqual(data['managed_updates'], False)
            self.assertTrue(data['offline'])
            self.assertNotIn('must-not-inherit', result.stdout)
            self.assertFalse((resources / 'voice' / 'app' / 'output').exists())

    def test_worker_rejects_state_inside_immutable_bundle(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            resources = base / 'Proof.app' / 'Contents' / 'Resources'
            resources.mkdir(parents=True)
            shutil.copyfile(ROOT / 'packaging/voice_worker.py', resources / 'voice_worker.py')
            models = base / 'models'
            models.mkdir()
            state = resources / 'state'
            result = subprocess.run([sys.executable, '-I', '-B', str(resources / 'voice_worker.py'),
                '--models-dir', str(models), '--data-dir', str(state), '--offline', '--check'],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn('outside the app bundle', result.stderr)
            self.assertFalse(state.exists())

    def test_worker_publishes_readiness_after_startup_and_removes_it_on_shutdown(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            resources = self._worker_fixture(base, self._running_uvicorn())
            data = base / 'Node State'
            models = base / 'Models'
            models.mkdir()
            ready = data / 'runtime' / 'voice-ready.json'
            process = subprocess.Popen([
                sys.executable, '-I', '-B', str(resources / 'voice_worker.py'),
                '--models-dir', str(models), '--data-dir', str(data),
                '--port', '0', '--ready-file', str(ready),
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                deadline = time.monotonic() + 5
                while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertIsNone(process.poll(), process.stderr.read() if process.poll() is not None else '')
                payload = json.loads(ready.read_text())
                # The worker now binds its own sockets, so the port is the one the OS actually
                # assigned rather than a number a stubbed bind_socket handed back. What matters
                # is unchanged: readiness is published at a real loopback address, for this
                # process, and removed on shutdown.
                url = urllib.parse.urlsplit(payload['url'])
                self.assertEqual(url.hostname, '127.0.0.1')
                self.assertIsNotNone(url.port)
                self.assertTrue(1 <= url.port <= 65535, payload)
                self.assertEqual(payload['pid'], process.pid)
                self.assertEqual(payload['version'], '0.2.0')
                self.assertEqual(ready.stat().st_mode & 0o777, 0o600)
            finally:
                process.terminate()
                process.communicate(timeout=5)
            self.assertFalse(ready.exists())

    def test_parent_monitor_stops_during_normal_worker_shutdown(self):
        process = None
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            resources = self._worker_fixture(base, self._running_uvicorn())
            data = base / 'Node State'
            models = base / 'Models'
            models.mkdir()
            ready = data / 'voice-ready.json'
            try:
                process = subprocess.Popen([
                    sys.executable, '-I', '-B', str(resources / 'voice_worker.py'),
                    '--models-dir', str(models), '--data-dir', str(data),
                    '--ready-file', str(ready), '--parent-pid', str(os.getpid()),
                ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                deadline = time.monotonic() + 5
                while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(ready.exists(), 'worker did not publish readiness')
                self.assertEqual(os.getpgid(process.pid), process.pid)
                process.terminate()
                _, stderr = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 0, stderr)
                self.assertFalse(ready.exists())
            finally:
                if process is not None and process.poll() is None:
                    try:
                        if os.getpgid(process.pid) == process.pid:
                            os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait(timeout=5)

    def test_real_uvicorn_sigterm_removes_readiness_before_signal_reraise(self):
        runtime_python = ROOT / '.build/voice-runtime/bin/python3'
        if not runtime_python.is_file():
            self.skipTest('built Voice runtime is unavailable')
        process = None
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            resources = self._worker_fixture(base, None)
            backend = resources / 'voice/app/backend'
            backend.mkdir()
            (backend / '__init__.py').write_text('')
            (backend / 'main.py').write_text('''\
async def app(scope, receive, send):
    if scope['type'] != 'lifespan':
        return
    while True:
        message = await receive()
        if message['type'] == 'lifespan.startup':
            await send({'type': 'lifespan.startup.complete'})
        elif message['type'] == 'lifespan.shutdown':
            await send({'type': 'lifespan.shutdown.complete'})
            return
''')
            data = base / 'Node State'
            models = base / 'Models'
            models.mkdir()
            ready = data / 'voice-ready.json'
            try:
                process = subprocess.Popen([
                    str(runtime_python), '-I', '-B', str(resources / 'voice_worker.py'),
                    '--models-dir', str(models), '--data-dir', str(data),
                    '--ready-file', str(ready), '--parent-pid', str(os.getpid()),
                ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                deadline = time.monotonic() + 15
                while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(ready.exists(), 'real Uvicorn worker did not publish readiness')
                process.terminate()
                process.communicate(timeout=10)
                self.assertEqual(process.returncode, -signal.SIGTERM)
                self.assertFalse(ready.exists(), 'Uvicorn left stale readiness after SIGTERM')
            finally:
                if process is not None and process.poll() is None:
                    try:
                        if os.getpgid(process.pid) == process.pid:
                            os.killpg(process.pid, signal.SIGKILL)
                        else:
                            os.kill(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait(timeout=5)

    def test_worker_removes_stale_readiness_when_startup_fails(self):
        fake_uvicorn = '''\
import asyncio
from contextlib import nullcontext

class Socket:
    def getsockname(self):
        return ('127.0.0.1', 54321)

class Config:
    def __init__(self, app, host, port, log_level):
        self.app = app
    def bind_socket(self):
        return nullcontext(Socket())

class Server:
    def __init__(self, config):
        self.config = config
        self.should_exit = False
    async def startup(self, sockets=None):
        raise RuntimeError('fixture startup failed')
    def run(self, sockets=None):
        asyncio.run(self.startup(sockets=sockets))
'''
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            resources = self._worker_fixture(base, fake_uvicorn)
            data = base / 'Node State'
            models = base / 'Models'
            models.mkdir()
            ready = data / 'voice-ready.json'
            ready.parent.mkdir()
            ready.write_text('{"pid": 1}')
            result = subprocess.run([
                sys.executable, '-I', '-B', str(resources / 'voice_worker.py'),
                '--models-dir', str(models), '--data-dir', str(data),
                '--ready-file', str(ready),
            ], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('fixture startup failed', result.stderr)
            self.assertFalse(ready.exists())

    def test_worker_removes_stale_readiness_before_server_load(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            resources = self._worker_fixture(base, "raise RuntimeError('fixture import failed')\n")
            data = base / 'Node State'
            models = base / 'Models'
            models.mkdir()
            ready = data / 'voice-ready.json'
            ready.parent.mkdir()
            ready.write_text('{"pid": 1}')
            result = subprocess.run([
                sys.executable, '-I', '-B', str(resources / 'voice_worker.py'),
                '--models-dir', str(models), '--data-dir', str(data),
                '--ready-file', str(ready),
            ], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('fixture import failed', result.stderr)
            self.assertFalse(ready.exists())

    def test_worker_rejects_readiness_outside_data_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            resources = self._worker_fixture(base, '')
            models = base / 'Models'
            models.mkdir()
            ready = base / 'elsewhere' / 'voice-ready.json'
            result = subprocess.run([
                sys.executable, '-I', '-B', str(resources / 'voice_worker.py'),
                '--models-dir', str(models), '--data-dir', str(base / 'Node State'),
                '--ready-file', str(ready),
            ], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn('inside the external data directory', result.stderr)
            self.assertFalse(ready.exists())

    def test_worker_rejects_parent_pid_that_is_not_its_actual_parent(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            resources = self._worker_fixture(base, '')
            models = base / 'Models'
            models.mkdir()
            data = base / 'Node State'
            result = subprocess.run([
                sys.executable, '-I', '-B', str(resources / 'voice_worker.py'),
                '--models-dir', str(models), '--data-dir', str(data),
                '--parent-pid', '1',
            ], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn('actual parent process', result.stderr)
            self.assertFalse(data.exists())

    def test_worker_exits_and_clears_readiness_when_parent_is_killed(self):
        guardian = worker_pid = unrelated = None
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            resources = self._worker_fixture(base, self._running_uvicorn())
            models = base / 'Models'
            models.mkdir()
            data = base / 'Node State'
            ready = data / 'voice-ready.json'
            child_pid_file = base / 'worker.pid'
            guardian_script = base / 'guardian.py'
            guardian_script.write_text('''\
import json
import os
from pathlib import Path
import subprocess
import sys
import time

command = json.loads(sys.argv[1]) + ['--parent-pid', str(os.getpid())]
child = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
Path(sys.argv[2]).write_text(str(child.pid))
while True:
    time.sleep(1)
''')
            command = [
                sys.executable, '-I', '-B', str(resources / 'voice_worker.py'),
                '--models-dir', str(models), '--data-dir', str(data),
                '--ready-file', str(ready),
            ]
            try:
                unrelated = subprocess.Popen([
                    sys.executable, '-c', 'import time; time.sleep(30)'])
                guardian = subprocess.Popen([
                    sys.executable, str(guardian_script), json.dumps(command), str(child_pid_file)])
                deadline = time.monotonic() + 5
                while not ready.exists() and guardian.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertIsNone(guardian.poll())
                self.assertTrue(ready.exists(), 'worker did not publish readiness')
                worker_pid = json.loads(ready.read_text())['pid']
                self.assertEqual(worker_pid, int(child_pid_file.read_text()))
                self.assertEqual(os.getpgid(worker_pid), worker_pid)

                guardian.terminate()
                guardian.wait(timeout=5)
                deadline = time.monotonic() + 7
                while ready.exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertFalse(ready.exists())
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    try:
                        os.kill(worker_pid, 0)
                    except ProcessLookupError:
                        break
                    time.sleep(0.02)
                else:
                    self.fail('worker survived its parent process')
                self.assertIsNone(unrelated.poll(), 'unrelated test process was signaled')
            finally:
                if guardian is not None and guardian.poll() is None:
                    guardian.terminate()
                    guardian.wait(timeout=5)
                if worker_pid is None and child_pid_file.exists():
                    worker_pid = int(child_pid_file.read_text())
                if worker_pid is not None:
                    try:
                        if os.getpgid(worker_pid) == worker_pid:
                            os.killpg(worker_pid, signal.SIGKILL)
                        else:
                            os.kill(worker_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                if unrelated is not None and unrelated.poll() is None:
                    unrelated.terminate()
                    unrelated.wait(timeout=5)


class StagingTests(unittest.TestCase):
    def test_staging_refuses_external_runtime_symlinks_and_existing_output(self):
        spec = importlib.util.spec_from_file_location('stage_voice', ROOT / 'packaging/stage_voice.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            runtime = base / 'runtime'
            (runtime / 'bin').mkdir(parents=True)
            (runtime / 'bin/python3').symlink_to(sys.executable)
            with self.assertRaisesRegex(ValueError, 'outside runtime'):
                module.validate_runtime(runtime)
            output = base / 'existing.app'
            output.mkdir()
            with self.assertRaises(FileExistsError):
                module.stage(runtime, output)

    def test_a_studio_source_tree_with_no_stager_is_refused(self):
        """A new studio must stop the build, not ship nothing while the build succeeds."""
        spec = importlib.util.spec_from_file_location('stage_voice', ROOT / 'packaging/stage_voice.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            repo = base / 'repo'
            repo.mkdir()
            files = {
                'VERSION': '0.1.0',
                'CHANGELOG.md': '## 0.1.0 — 2026-11-02\n\nPlain release changes.\n\n### Technical notes\n\nInternal details.',
                'assets/app-icon.icns': 'icns-fixture',
                'assets/menubar-icon.png': 'png-fixture',
                'assets/menubar-icon@2x.png': 'png-fixture',
                'components/voice/app/backend/main.py': '# source',
                'components/voice/app/frontend/index.html': '<html>Proof</html>',
                'components/voice/model-audits/example.audit.json': '{}',
                'components/voice/VERSION': '2.7.2',
                'components/voice/CHANGELOG.md': '# Upstream',
                'research/voice-adoption.json': json.dumps({'commit': 'b' * 40, 'version': '2.7.2'}),
                'packaging/voice_worker.py': '# worker',
                # A studio adopted tomorrow, with its worker wrapper and no stager entry.
                # `semantic` is deliberately a name nothing registers — `video` was this
                # fixture's example until the Video Studio was adopted for real.
                'components/semantic/app/backend/main.py': '# semantic source',
                'packaging/semantic_worker.py': '# semantic worker',
            }
            for name, content in files.items():
                path = repo / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
            subprocess.run(['git', 'init', '-q', str(repo)], check=True)
            subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
            subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Fixture',
                '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'Fixture'], check=True)
            runtime = base / 'runtime'
            (runtime / 'bin').mkdir(parents=True)
            (runtime / 'bin/python3').write_text('#!/bin/sh\n/usr/bin/env\n')
            (runtime / 'bin/python3').chmod(0o755)
            artifact = base / 'Proof.app'
            with mock.patch.object(module, 'ROOT', repo):
                with self.assertRaises(ValueError) as caught:
                    module.stage(runtime, artifact)
            message = str(caught.exception)
            self.assertIn('semantic', message, 'the refusal must name the unregistered studio')
            self.assertIn('stage_voice.py', message, 'the refusal must name the file to update')
            self.assertIn('hub_worker.py', message,
                          'a new studio also needs a worker the Hub can spawn')
            self.assertFalse(artifact.exists(), 'a refused stage must leave no artifact')

    def test_complete_stage_excludes_untracked_state_and_hashes_the_payload(self):
        spec = importlib.util.spec_from_file_location('stage_voice', ROOT / 'packaging/stage_voice.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            repo = base / 'repo'
            repo.mkdir()
            files = {
                'VERSION': '0.1.0',
                'CHANGELOG.md': '## 0.1.0 — 2026-11-02\n\nPlain release changes.\n\n### Technical notes\n\nInternal details.',
                'assets/app-icon.icns': 'icns-fixture',
                'assets/menubar-icon.png': 'png-fixture',
                'assets/menubar-icon@2x.png': 'png-fixture',
                'components/voice/app/backend/main.py': '# source',
                'components/voice/app/frontend/index.html': '<html>Proof</html>',
                'components/voice/model-audits/example.audit.json': '{}',
                'components/voice/VERSION': '2.7.2',
                'components/voice/CHANGELOG.md': '# Upstream',
                'research/voice-adoption.json': json.dumps({'commit': 'b'*40, 'version': '2.7.2'}),
                'packaging/voice_worker.py': '# worker',
            }
            for name, content in files.items():
                path = repo / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
            subprocess.run(['git', 'init', '-q', str(repo)], check=True)
            subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
            subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Fixture',
                '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'Fixture'], check=True)
            (repo/'components/voice/app/frontend/settings.json').write_text('fixture-secret')
            (repo/'components/voice/app/backend/untracked_secret.py').write_text('fixture-secret')
            (repo/'components/voice/model-audits/.fleet_token').write_text('fixture-secret')
            runtime = base / 'runtime'
            (runtime/'bin').mkdir(parents=True)
            (runtime/'bin/python3').write_text('#!/bin/sh\n/usr/bin/env\n')
            (runtime/'bin/python3').chmod(0o755)
            artifact = base/'Proof.app'
            with mock.patch.object(module, 'ROOT', repo):
                module.stage(runtime, artifact)
            resources = artifact/'Contents/Resources'
            launch = subprocess.run([str(artifact/'Contents/MacOS/StudioHubVoice')],
                env={'PATH': '/usr/bin:/bin', 'KH_STUDIO_TOKEN': 'inherited-secret'},
                text=True, capture_output=True)
            self.assertEqual(launch.returncode, 0, launch.stderr)
            self.assertNotIn('inherited-secret', launch.stdout)
            self.assertFalse((resources/'voice/app/frontend/settings.json').exists())
            self.assertFalse((resources/'voice/app/backend/untracked_secret.py').exists())
            self.assertFalse((resources/'voice/model-audits/.fleet_token').exists())
            manifest_path = resources/'release-manifest.json'
            manifest = json.loads(manifest_path.read_text())
            self.assertTrue(manifest['development_build'])
            # payload_sha256 is the POST-sign digest excluding release-manifest.json and
            # _CodeSignature; this fixture's native is a shell script, not a Mach-O, so the
            # seal rewrites nothing. Real-Mach-O proof: tests/test_sealed_manifest_packaging.py.
            self.assertEqual(manifest['payload_sha256'],
                module.tree_digest(artifact/'Contents', exclude={Path('Resources/release-manifest.json'), Path('_CodeSignature')}))
            with (artifact/'Contents/Info.plist').open('rb') as stream:
                plist = plistlib.load(stream)
            self.assertEqual(plist['CFBundleExecutable'], 'StudioHubVoice')
            self.assertTrue(plist['LSUIElement'])
            self.assertFalse((artifact/'Contents/MacOS/Hub Studio KH').exists())
            self.assertEqual((base/'Proof.app.tree-sha256').read_text().strip(), module.tree_digest(artifact))
            (resources/'voice_worker.py').write_text('# changed worker')
            self.assertNotEqual(manifest['payload_sha256'],
                module.tree_digest(artifact/'Contents', exclude={Path('Resources/release-manifest.json'), Path('_CodeSignature')}))

    def test_native_shell_stage_keeps_cli_and_creates_foreground_app(self):
        self._native_stage()

    def test_native_shell_stage_includes_separate_image_runtime_and_source(self):
        manifest = self._native_stage(image=True)
        # The fixture runtime is synthetic, so the bundle must say its Image runtime is not the
        # qualified one. A build that could stage the wrong engine silently is how five releases
        # shipped mflux 0.17.5 while publishing numbers measured on 0.20.0.
        image = manifest['components']['image']
        self.assertIs(image['runtime_qualified'], False,
                      'an unqualified Image runtime must be recorded as unqualified')
        self.assertIn('fixture', image['runtime_note'])
        # Both digests are recorded, and they differ: the bundle names the tree it got and the tree
        # it should have had, so a reader can see the gap without reproducing it.
        self.assertRegex(image['runtime_sha256_qualified'], r'^[0-9a-f]{64}$')
        self.assertRegex(image['runtime_sha256_found'], r'^[0-9a-f]{64}$')
        self.assertNotEqual(image['runtime_sha256_found'], image['runtime_sha256_qualified'])

    def test_retired_video_is_not_staged_but_yue2_still_is(self):
        """LTX-2.3 is retired: `--video-runtime` is ignored, the release has no Video component, and
        the YuE2 sung-song runtime (which used to be staged inside the Video stager, so it vanished
        with it) still ships, recorded on Music."""
        manifest = self._native_stage(video=True)
        self.assertNotIn('video', manifest['components'])
        self.assertIn('music', manifest['components'])
        music = manifest['components']['music']
        self.assertRegex(music['yue2_runtime_sha256'], r'^[0-9a-f]{64}$')
        self.assertIs(music['yue2_runtime_qualified'], False)   # the fixture tree is synthetic

    def test_a_hub_stage_without_the_image_and_music_runtimes_is_refused(self):
        """0.6.199 shipped a 592 MB DMG whose Resources/runtimes held only `voice`.

        The helper that drove this stager passed `--hub` and the two KH Updater flags, never
        `--image-runtime` or `--yue2-runtime`. Both stagers returned None, the release manifest
        matched the smaller bundle, and the build exited 0 over an app whose Image and sung-song
        Music could not start. The build must refuse that shape and name what is missing.
        """
        message = self._native_stage(image=False, yue2=False, expect_refusal=True)
        for missing in ('runtimes/image', 'runtimes/music-yue2',
                        'image_worker.py', 'yue2_runner.py'):
            self.assertIn(missing, message, f'the refusal must name {missing}')
        self.assertIn('--image-runtime', message, 'the refusal must name the flag that stages it')
        self.assertIn('--yue2-runtime', message, 'the refusal must name the flag that stages it')

    def test_the_manifest_a_release_writes_installs_on_a_0_6_198_mac(self):
        """The fleet's own installer validates the release, so the manifest must suit it.

        It reads every key in `components`; a name whose runtime it does not know is refused
        before any file is copied (0.6.199, 'khu-agent'). The agent kit therefore rides inside the
        `hub` record, and every component still ships the `<name>_worker.py` the installer derives.
        """
        manifest = self._native_stage(khu=True)
        self.assertNotIn('khu-agent', manifest['components'])
        hub = manifest['components']['hub']
        self.assertEqual(hub['khu_agent']['agent_version'], '0.0.1')
        self.assertRegex(hub['khu_agent']['kit_manifest_sha256'], r'^[0-9a-f]{64}$')

    def _native_stage(self, image=True, yue2=True, video=False, expect_refusal=False, khu=False):
        spec = importlib.util.spec_from_file_location('stage_voice', ROOT / 'packaging/stage_voice.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            repo = base / 'repo'
            repo.mkdir()
            files = {
                'VERSION': '0.2.0',
                'CHANGELOG.md': '## 0.2.0 — 2026-11-02\n\nPlain release changes.\n\n### Technical notes\n\nInternal details.',
                'assets/app-icon.icns': 'icns-fixture',
                'assets/menubar-icon.png': 'png-fixture',
                'assets/menubar-icon@2x.png': 'png-fixture',
                'components/voice/app/backend/main.py': '# source',
                'components/voice/app/backend/mlx_memory.py': 'def bound_cache(): pass\n',
                'components/voice/app/frontend/index.html': '<html>Proof</html>',
                'components/voice/model-audits/example.audit.json': '{}',
                'components/voice/VERSION': '2.7.2',
                'components/voice/CHANGELOG.md': '# Upstream',
                'research/voice-adoption.json': json.dumps({'commit': 'b' * 40, 'version': '2.7.2'}),
                'packaging/voice_worker.py': '# worker',
                'packaging/hub_worker.py': '# hub worker',
                'components/hub/standalone.py': '# packaged hub',
                'components/hub/backend/migrations/001.sql': 'SELECT 1;',
                'components/model_packages.py': '# package helper',
                'model-manifests/fixture.json': '{}',
                'research/hub-adoption.json': json.dumps({'source': {'commit': 'c' * 40, 'version': '2.22.0'}}),
                'research/image-adoption.json': json.dumps({'source': {'commit': 'e' * 40, 'version': '1.32.2'}}),
                'components/image/app/backend/main.py': '# image fixture',
                'packaging/image_worker.py': '# image worker',
                'components/subtitle/app/backend/main.py': '# subtitle fixture',
                'packaging/subtitle_worker.py': '# subtitle worker',
                # Chat is the fifth component, so a fixture that predates it stages nothing at
                # all: `stage_voice.stage` copies every component it knows about, and a missing
                # file failed the whole stage rather than the Chat part of it. Kept in the same
                # list as the rest so a sixth component cannot repeat this.
                'components/chat/app/backend/main.py': '# chat fixture',
                'packaging/chat_worker.py': '# chat worker',
                # Music runs on the Voice runtime (no overlay of its own since ACE-Step 1.5 was
                # retired), so every Hub bundle stages it and the fixture has to carry it.
                'components/music/app/backend/main.py': '# music fixture',
                'components/music/app/backend/yue2_runner.py': '# yue2 bridge',
                'packaging/music_worker.py': '# music worker',
                'components/embeddings/app/backend/main.py': '# embeddings fixture',
                'packaging/embeddings_worker.py': '# embeddings worker',
                'components/vision/app/backend/main.py': '# vision fixture',
                'packaging/vision_worker.py': '# vision worker',
            }
            # Frozen gate assets every Hub bundle stages. The fixture repo is synthetic, so it
            # carries the real tracked files instead of weakening the stage or skipping tests.
            for relative in (
                    'contracts/developer-gate-v1.schema.json',
                    'contracts/developer-gate-v1-wire.txt',
                    'packaging/developer-gates/library-portable.commands.json',
                    'packaging/developer-gates/library-portable.gate.json',
                    'packaging/developer-gates/library-portable.registry.json',
                    'packaging/developer-gates/library-portable.dependency-manifest.json',
                    'packaging/developer-gates/official-tools.json'):
                files[relative] = (ROOT / relative).read_text()
            for name, content in files.items():
                path = repo / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
            subprocess.run(['git', 'init', '-q', str(repo)], check=True)
            subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
            subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Fixture',
                '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'Fixture'], check=True)
            runtime = base / 'runtime'
            (runtime / 'bin').mkdir(parents=True)
            (runtime / 'bin/python3').write_text('#!/bin/sh\n')
            native = base / 'StudioHub'
            native.write_bytes(b'native-shell')
            native.chmod(0o755)
            helper = base / 'HubStudioHelper'
            helper.write_bytes(b'native-helper')
            helper.chmod(0o755)
            # A Hub build requires the two pinned render tools. The real pins name ~132 MB
            # universal binaries, so the fixture supplies its own tiny pins through the seam
            # `stage_voice` imports at stage time; the staging code still copies and verifies
            # them by size and sha256 exactly as it does for production. Loaded by path (and
            # placed in sys.modules for that import) so this never shadows the runtime's PyPI
            # `packaging` with this repo's packaging directory.
            tools_spec = importlib.util.spec_from_file_location(
                'stage_render_tools', ROOT / 'packaging' / 'stage_render_tools.py')
            stage_render_tools = importlib.util.module_from_spec(tools_spec)
            # `@dataclass` resolves its own class through `sys.modules` while the class is
            # being created, so the module has to be registered BEFORE it is executed or the
            # decorator raises `AttributeError: 'NoneType' object has no attribute '__dict__'`.
            # `mock.patch.dict` registers it for the import and restores the registry
            # afterwards, so the fixture leaves no module behind.
            with mock.patch.dict(sys.modules, {'stage_render_tools': stage_render_tools}):
                tools_spec.loader.exec_module(stage_render_tools)
            tools = base / 'render-tools'
            tools.mkdir()
            pins = []
            for tool_name in ('ffmpeg', 'ffprobe'):
                payload = f'#!/bin/sh\n# {tool_name} fixture\n'.encode()
                (tools / tool_name).write_bytes(payload)
                pins.append(stage_render_tools._ToolPin(
                    tool_name, tools / tool_name, len(payload),
                    hashlib.sha256(payload).hexdigest()))
            artifact = base / 'Hub Studio KH.app'
            with mock.patch.object(module, 'ROOT', repo), \
                    mock.patch.dict(sys.modules, {'stage_render_tools': stage_render_tools}), \
                    mock.patch.object(stage_render_tools, 'production_pins',
                                      lambda _directory: tuple(pins)):
                try:
                    # The fixture runtime is synthetic, so it is not the tree the models were
                    # qualified on and `stage_voice` refuses it by design. The override is the
                    # deliberate way through: it stages and records that the runtime is not the
                    # qualified one, which is what this test then checks.
                    stage_kwargs = {}
                    if khu:
                        # A synthetic credential-free kit plus a publisher descriptor: the staging
                        # code under test is the real one (`stage_khu_agent.stage_agent_kit`).
                        kit = base / 'agent-kit'
                        (kit / 'bin').mkdir(parents=True)
                        for relative, payload in {
                                'VERSION': '0.0.1\n',
                                'bin/khu': '#!/bin/sh\n# khu fixture\n',
                                'bin/khu-stub': '#!/bin/sh\n# stub fixture\n',
                                'install-agent.sh': '#!/bin/sh\n# install fixture\n',
                                'com.khupdater.agent.plist': '<plist/>\n',
                                'agent-config.example.json': '{}\n'}.items():
                            (kit / relative).write_text(payload)
                        publisher = base / 'publisher.json'
                        publisher.write_text(json.dumps({
                            'feedURL': 'https://publisher.invalid/feed',
                            'enrolURL': 'https://publisher.invalid/enrol',
                            'publicKey': 'A' * 43,
                            'testAuthority': False}))
                        stage_kwargs.update({'khu_agent_kit': kit,
                                             'khu_publisher_json': publisher})
                    if image:
                        stage_kwargs.update({
                            'image_runtime': runtime,
                            'unqualified_image_runtime': 'synthetic staging fixture'})
                    if yue2:
                        stage_kwargs.update({
                            'yue2_runtime': runtime,
                            'unqualified_yue2_runtime': 'synthetic staging fixture'})
                    if video:
                        stage_kwargs.update({
                            'video_runtime': runtime,
                            'unqualified_video_runtime': 'synthetic staging fixture'})
                    if expect_refusal:
                        # A stage this build must refuse: report why, and prove it left no app.
                        with self.assertRaises(ValueError) as caught:
                            module.stage(runtime, artifact, native_shell=native,
                                         native_helper=helper,
                                         render_tools_dir=tools, **stage_kwargs)
                        self.assertFalse(artifact.exists(),
                                         'a refused stage must leave no artifact')
                        return str(caught.exception)
                    module.stage(runtime, artifact, native_shell=native,
                                 native_helper=helper,
                                 render_tools_dir=tools, **stage_kwargs)
                except TypeError as exc:
                    self.fail(f'stage must accept native_shell: {exc}')
            contents = artifact / 'Contents'
            self.assertEqual((contents / 'MacOS/Hub Studio KH').read_bytes(), b'native-shell')
            self.assertTrue(os.access(contents / 'MacOS/Hub Studio KH', os.X_OK))
            self.assertTrue((contents / 'MacOS/StudioHubService').is_file())
            self.assertFalse((contents / 'MacOS/StudioHubVoice').exists())
            self.assertTrue((contents / 'Resources/components/hub/standalone.py').is_file())
            self.assertTrue((contents / 'Resources/components/hub/backend/migrations/001.sql').is_file())
            with (contents / 'Info.plist').open('rb') as stream:
                plist = plistlib.load(stream)
            self.assertEqual(plist['CFBundleExecutable'], 'Hub Studio KH')
            self.assertEqual(plist['CFBundleName'], 'Hub Studio KH')
            self.assertEqual(plist['CFBundleDisplayName'], 'Hub Studio KH')
            self.assertEqual(plist['CFBundleIdentifier'], 'local.studiohub.standalone')
            self.assertNotIn('LSUIElement', plist)
            self.assertIn('microphone', plist['NSMicrophoneUsageDescription'].lower())
            # The background process is a named helper bundle, not a bare python3 (owner rule).
            helper_bundle = contents / 'Resources/helper/Hub Studio KH Helper.app'
            helper_exe = helper_bundle / 'Contents/MacOS/Hub Studio KH Helper'
            self.assertEqual(helper_exe.read_bytes(), b'native-helper')
            self.assertTrue(os.access(helper_exe, os.X_OK))
            helper_plist = plistlib.loads((helper_bundle / 'Contents/Info.plist').read_bytes())
            self.assertEqual(helper_plist['CFBundleName'], 'Hub Studio KH Helper')
            self.assertEqual(helper_plist['CFBundleDisplayName'], 'Hub Studio KH Helper')
            self.assertEqual(helper_plist['CFBundleIdentifier'], 'local.studiohub.helper')
            self.assertTrue(helper_plist['LSUIElement'])
            manifest = json.loads((contents / 'Resources/release-manifest.json').read_text())
            self.assertFalse(manifest['development_build'])
            # The installer on every fleet Mac is 0.6.198's: it reads the manifest's component keys
            # and refuses any runtime its own table lacks, before copying a byte. Every component
            # this build declares must be one it knows, with the `<name>_worker.py` it derives.
            for component, record in manifest['components'].items():
                runtime_name = (record or {}).get('runtime', component)
                self.assertIn(runtime_name, FLEET_198_INSTALLER_RUNTIMES,
                              f'the 0.6.198 installer refuses component {component!r}')
                self.assertTrue((contents / 'Resources' / f'{component}_worker.py').is_file(),
                                f'{component}_worker.py is missing')
            if khu:
                self.assertNotIn('khu-agent', manifest['components'],
                                 'a top-level khu-agent component is refused by 0.6.198 installers')
                self.assertTrue((contents / 'Resources/khu-agent/bin/khu').is_file())
                self.assertTrue((contents / 'Resources/khu-agent/publisher.json').is_file())
            # What ships is what is recorded, and what is recorded is what ships: the release
            # manifest's components are the data every consumer reads, so a component that is
            # staged without a record (or recorded without being staged) is a broken release.
            recorded = set(manifest['components'])
            self.assertLessEqual(recorded, set(module.COMPONENT_STAGERS),
                                 "the release manifest names a component staging does not know")
            staged = {path.name for path in (contents / 'Resources/components').iterdir()
                      if path.is_dir()}
            staged.add('voice')  # staged at Resources/voice, not under components/
            self.assertEqual(staged, recorded,
                             "a staged component directory and the release manifest disagree")
            for name in recorded:
                self.assertIn('source_sha256', manifest['components'][name])
            if image:
                self.assertEqual(manifest['components']['image']['upstream_commit'], 'e' * 40)
                self.assertEqual(manifest['components']['image']['runtime_sha256'], module.tree_digest(runtime))
                self.assertTrue((contents / 'Resources/image_worker.py').is_file())
                self.assertTrue((contents / 'Resources/components/image/app/backend/main.py').is_file())
                self.assertTrue((contents / 'Resources/runtimes/image/bin/python3').is_file())
            else:
                self.assertNotIn('image', manifest['components'])
            if video:
                # Retired: no Video component, runtime, worker or source - and Music keeps YuE2.
                resources = contents / 'Resources'
                self.assertFalse((resources / 'runtimes/video').exists())
                self.assertFalse((resources / 'video_worker.py').exists())
                self.assertFalse((resources / 'components/video').exists())
                self.assertTrue((resources / 'runtimes/music-yue2/bin/python3').is_file())
            # payload_sha256 is POST-sign (excluding release-manifest.json and
            # _CodeSignature); fixture native is not Mach-O so the seal rewrites nothing -
            # real-Mach-O proof in tests/test_sealed_manifest_packaging.py.
            self.assertEqual(manifest['payload_sha256'], module.tree_digest(
                contents, exclude={Path('Resources/release-manifest.json'), Path('_CodeSignature')}))
            # The per-file manifest a delta update is diffed against: present, self-consistent, and
            # describing exactly the bytes on disk (it lists release-manifest.json, not itself).
            hub_backend = ROOT / 'components/hub/backend'
            sys.path.insert(0, str(hub_backend.parent.parent.parent))
            from components.hub.backend import file_manifest
            sidecar = contents.parent.with_name(contents.parent.name + '.file-manifest')
            index, groups = file_manifest.load_dir(sidecar)
            self.assertEqual(file_manifest.flatten(groups), file_manifest.scan(contents.parent))
            self.assertIn('Contents/Resources/release-manifest.json', file_manifest.flatten(groups))
            self.assertIn('Contents/MacOS/Hub Studio KH', file_manifest.flatten(groups))
            self.assertFalse((contents / 'Resources/file-manifest').exists())
            (contents / 'MacOS/Hub Studio KH').write_bytes(b'changed-native-shell')
            self.assertNotEqual(manifest['payload_sha256'], module.tree_digest(
                contents, exclude={Path('Resources/release-manifest.json'), Path('_CodeSignature')}))
            return manifest

    def test_native_shell_stage_rejects_missing_binary(self):
        spec = importlib.util.spec_from_file_location('stage_voice', ROOT / 'packaging/stage_voice.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertTrue(hasattr(module, 'validate_native_shell'),
            'stage_voice must provide native shell validation')
        with self.assertRaisesRegex((ValueError, FileNotFoundError), 'native shell'):
            module.validate_native_shell(Path('/missing/StudioHub'))


if __name__ == '__main__':
    unittest.main()
