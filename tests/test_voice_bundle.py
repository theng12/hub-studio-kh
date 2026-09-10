"""Exercise relocation, external state and the worker's launch boundary."""
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
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


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
                self.assertEqual(payload, {
                    'url': 'http://127.0.0.1:54321',
                    'pid': process.pid,
                    'version': '0.2.0',
                })
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
                'CHANGELOG.md': '# Standalone changes',
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
            self.assertEqual(manifest['payload_sha256'],
                module.tree_digest(artifact/'Contents', exclude={Path('Resources/release-manifest.json')}))
            with (artifact/'Contents/Info.plist').open('rb') as stream:
                plist = plistlib.load(stream)
            self.assertEqual(plist['CFBundleExecutable'], 'StudioHubVoice')
            self.assertTrue(plist['LSUIElement'])
            self.assertFalse((artifact/'Contents/MacOS/StudioHub').exists())
            self.assertEqual((base/'Proof.app.tree-sha256').read_text().strip(), module.tree_digest(artifact))
            (resources/'voice_worker.py').write_text('# changed worker')
            self.assertNotEqual(manifest['payload_sha256'],
                module.tree_digest(artifact/'Contents', exclude={Path('Resources/release-manifest.json')}))

    def test_native_shell_stage_keeps_cli_and_creates_foreground_app(self):
        spec = importlib.util.spec_from_file_location('stage_voice', ROOT / 'packaging/stage_voice.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            repo = base / 'repo'
            repo.mkdir()
            files = {
                'VERSION': '0.2.0',
                'CHANGELOG.md': '# Standalone changes',
                'components/voice/app/backend/main.py': '# source',
                'components/voice/app/frontend/index.html': '<html>Proof</html>',
                'components/voice/model-audits/example.audit.json': '{}',
                'components/voice/VERSION': '2.7.2',
                'components/voice/CHANGELOG.md': '# Upstream',
                'research/voice-adoption.json': json.dumps({'commit': 'b' * 40, 'version': '2.7.2'}),
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
            runtime = base / 'runtime'
            (runtime / 'bin').mkdir(parents=True)
            (runtime / 'bin/python3').write_text('#!/bin/sh\n')
            native = base / 'StudioHub'
            native.write_bytes(b'native-shell')
            native.chmod(0o755)
            artifact = base / 'Studio Hub.app'
            with mock.patch.object(module, 'ROOT', repo):
                try:
                    module.stage(runtime, artifact, native_shell=native)
                except TypeError as exc:
                    self.fail(f'stage must accept native_shell: {exc}')
            contents = artifact / 'Contents'
            self.assertEqual((contents / 'MacOS/StudioHub').read_bytes(), b'native-shell')
            self.assertTrue(os.access(contents / 'MacOS/StudioHub', os.X_OK))
            self.assertTrue((contents / 'MacOS/StudioHubVoice').is_file())
            with (contents / 'Info.plist').open('rb') as stream:
                plist = plistlib.load(stream)
            self.assertEqual(plist['CFBundleExecutable'], 'StudioHub')
            self.assertEqual(plist['CFBundleName'], 'Studio Hub')
            self.assertEqual(plist['CFBundleIdentifier'], 'local.studiohub.standalone')
            self.assertNotIn('LSUIElement', plist)
            self.assertIn('microphone', plist['NSMicrophoneUsageDescription'].lower())
            manifest = json.loads((contents / 'Resources/release-manifest.json').read_text())
            self.assertFalse(manifest['development_build'])
            self.assertEqual(manifest['payload_sha256'], module.tree_digest(
                contents, exclude={Path('Resources/release-manifest.json')}))
            (contents / 'MacOS/StudioHub').write_bytes(b'changed-native-shell')
            self.assertNotEqual(manifest['payload_sha256'], module.tree_digest(
                contents, exclude={Path('Resources/release-manifest.json')}))

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
