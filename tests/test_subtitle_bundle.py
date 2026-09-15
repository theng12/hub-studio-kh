"""The Subtitle wrapper runs under actual Python -I isolation from a bundle.

This proves the packaging wiring: bundle-relative paths, owner-only state, the
private owner token on every route, the shared immutable Voice runtime, and the
one tracked Voice export source being loaded by the Subtitle process itself.
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
import wave

import httpx


ROOT = Path(__file__).resolve().parents[1]


def _build_python() -> Path:
    """The bundled Voice runtime, which also carries mlx-audio for Subtitle.

    A git worktree has no ``.build/`` of its own, so an isolated worktree falls
    back to the shared checkout's already-built runtime instead of skipping.
    """
    override = os.environ.get('STUDIOHUB_TEST_VOICE_RUNTIME', '').strip()
    if override:
        return Path(override) / 'bin/python3'
    local = ROOT / '.build/voice-runtime/bin/python3'
    if local.is_file():
        return local
    for parent in ROOT.parents:
        candidate = (parent / 'studiohub-standalone/.build/voice-runtime/bin/python3'
                     if (parent / 'studiohub-standalone').is_dir()
                     else parent / '.build/voice-runtime/bin/python3')
        if candidate.is_file():
            return candidate
    return local


BUILD_PYTHON = _build_python()
REAL_LIBRARY = os.environ.get('STUDIOHUB_TEST_MODEL_LIBRARY', '').strip()
SPEECH_WAV = Path('/tmp/st06-bundle-speech.wav')


def _bundle(root: Path, *, library: Path) -> tuple[Path, Path, Path]:
    resources = root / 'Bundle.app/Contents/Resources'
    resources.mkdir(parents=True)
    for name in ('subtitle_worker.py', 'voice_worker.py'):
        shutil.copyfile(ROOT / 'packaging' / name, resources / name)
    shutil.copytree(ROOT / 'components/subtitle', resources / 'components/subtitle',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copyfile(ROOT / 'components/model_packages.py',
                    resources / 'components/model_packages.py')
    # The shared immutable Voice source: Subtitle imports its export behavior
    # from here rather than shipping a copy of the formatters.
    (resources / 'voice' / 'app').mkdir(parents=True)
    shutil.copytree(ROOT / 'components/voice/app/backend',
                    resources / 'voice' / 'app' / 'backend',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'tests'))
    catalog = resources / 'model-manifests'
    catalog.mkdir()
    for name in ('whisper-turbo.json', 'whisper-base.json', 'whisper-small.json'):
        shutil.copyfile(ROOT / 'model-manifests' / name, catalog / name)
    (resources / 'runtimes').mkdir()
    (resources / 'runtimes/voice').symlink_to(BUILD_PYTHON.parent.parent)
    (resources / 'release-manifest.json').write_text(json.dumps({
        'schema': 'studiohub.standalone-release', 'schema_version': 1,
        'version': 'fixture',
        'components': {'subtitle': {'runtime_sha256': 'e' * 64}},
    }))
    data = root / 'External State'
    data.mkdir()
    token = data / 'hub' / '.hub_token'
    token.parent.mkdir()
    token.write_text('subtitle_owner_token_0123456789')
    token.chmod(0o600)
    return resources, data, token


@unittest.skipUnless(BUILD_PYTHON.is_file(), 'the bundled Voice runtime is required')
class SubtitleBundleTests(unittest.TestCase):
    def test_isolated_wrapper_check_uses_private_external_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            resources, data, token = _bundle(root, library=root / 'Models')
            models = root / 'External Models'
            models.mkdir()
            command = [str(BUILD_PYTHON), '-I', '-B', str(resources / 'subtitle_worker.py'),
                       '--data-dir', str(data), '--models-dir', str(models),
                       '--ready-file', str(data / 'subtitle-ready.json'),
                       '--hub-token-file', str(token), '--check']
            result = subprocess.run(command, capture_output=True, text=True,
                                    env={'PATH': '/usr/bin:/bin', 'LANG': 'en_US.UTF-8'},
                                    timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload['component'], 'subtitle')
            self.assertEqual(payload['runtime_sha256'], 'e' * 64)
            self.assertEqual(payload['models'], str(models.resolve()))
            self.assertTrue(payload['offline'])
            self.assertTrue(Path(payload['voice_source']).is_file())
            self.assertFalse((data / 'subtitle-ready.json').exists())
            # An inherited, unreadable token must be refused.
            token.chmod(0o644)
            denied = subprocess.run(command, capture_output=True, text=True,
                                    env={'PATH': '/usr/bin:/bin'}, timeout=30)
            self.assertNotEqual(denied.returncode, 0)
            self.assertIn('owner-only', denied.stderr)

    def test_private_server_requires_the_owner_token(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            resources, data, token = _bundle(root, library=root / 'Models')
            models = root / 'External Models'
            models.mkdir()
            ready = data / 'subtitle-ready.json'
            command = [str(BUILD_PYTHON), '-I', '-B', str(resources / 'subtitle_worker.py'),
                       '--data-dir', str(data), '--models-dir', str(models),
                       '--ready-file', str(ready), '--hub-token-file', str(token),
                       '--parent-pid', str(os.getpid()), '--offline']
            log = root / 'subtitle-worker.log'
            with log.open('w+') as output:
                process = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT,
                    cwd=root, env={'PATH': '/usr/bin:/bin', 'LANG': 'en_US.UTF-8'})
                try:
                    for _ in range(600):
                        if ready.is_file() or process.poll() is not None:
                            break
                        time.sleep(0.1)
                    self.assertTrue(ready.is_file(), log.read_text())
                    value = json.loads(ready.read_text())
                    self.assertEqual(value['pid'], process.pid)
                    with httpx.Client(timeout=30) as client:
                        self.assertEqual(
                            client.get(value['url'] + '/api/health').status_code, 401)
                        headers = {'X-Hub-Token': token.read_text()}
                        health = client.get(value['url'] + '/api/health', headers=headers)
                        self.assertEqual(health.status_code, 200, health.text)
                        self.assertTrue(health.json()['ok'])
                        catalog = client.get(value['url'] + '/api/catalog', headers=headers)
                        self.assertEqual(catalog.status_code, 200, catalog.text)
                        payload = catalog.json()
                        self.assertEqual(payload['default_model'], 'whisper-turbo')
                        states = {entry['package_id']: entry['readiness']['files']['state']
                                  for entry in payload['models']}
                        # An empty library must not be reported as ready.
                        self.assertEqual(set(states.values()), {'not_installed'})
                        self.assertEqual(payload['export_source']['module'],
                                         'backend.transcription')
                        availability = client.get(value['url'] + '/api/transcribe/availability',
                                                  headers=headers)
                        self.assertEqual(availability.status_code, 200)
                        self.assertFalse(availability.json()['available'])
                finally:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=15)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=5)
                self.assertFalse(ready.exists(), log.read_text())


@unittest.skipUnless(REAL_LIBRARY, 'set STUDIOHUB_TEST_MODEL_LIBRARY to run real Whisper')
@unittest.skipUnless(BUILD_PYTHON.is_file(), 'the bundled Voice runtime is required')
class SubtitleBundleRealModelTests(unittest.TestCase):
    """One real transcription through the packaged worker process over HTTP."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.root = Path(cls.temporary.name)
        subprocess.run(['/usr/bin/say', '-v', 'Samantha', '-o', str(SPEECH_WAV),
                        '--data-format=LEI16@22050',
                        'The quick brown fox jumps over the lazy dog. '
                        'Studio Hub subtitle transcription test.'], check=True)

    @classmethod
    def tearDownClass(cls) -> None:
        SPEECH_WAV.unlink(missing_ok=True)
        cls.temporary.cleanup()

    def test_worker_transcribes_real_speech_and_acknowledges(self) -> None:
        root = self.root
        resources, data, token = _bundle(root, library=Path(REAL_LIBRARY))
        models = Path(REAL_LIBRARY)
        ready = data / 'subtitle-ready.json'
        command = [str(BUILD_PYTHON), '-I', '-B', str(resources / 'subtitle_worker.py'),
                   '--data-dir', str(data), '--models-dir', str(models),
                   '--ready-file', str(ready), '--hub-token-file', str(token),
                   '--parent-pid', str(os.getpid()), '--offline']
        log = root / 'subtitle-real.log'
        with log.open('w+') as output:
            process = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT,
                cwd=root, env={'PATH': '/usr/bin:/bin', 'LANG': 'en_US.UTF-8'})
            try:
                for _ in range(900):
                    if ready.is_file() or process.poll() is not None:
                        break
                    time.sleep(0.1)
                self.assertTrue(ready.is_file(), log.read_text())
                value = json.loads(ready.read_text())
                headers = {'X-Hub-Token': token.read_text()}
                with httpx.Client(timeout=300) as client:
                    catalog = client.get(value['url'] + '/api/catalog', headers=headers).json()
                    turbo = next(entry for entry in catalog['models']
                                 if entry['package_id'] == 'whisper-turbo')
                    self.assertTrue(turbo['readiness']['ready'], turbo['readiness']['reason'])
                    with SPEECH_WAV.open('rb') as audio:
                        response = client.post(
                            value['url'] + '/api/transcribe', headers=headers,
                            data={'model': 'whisper-turbo', 'language': 'en',
                                  'word_timestamps': 'false', 'activity_id': 'st06-bundle-1'},
                            files={'file': ('speech.wav', audio, 'audio/wav')})
                    self.assertEqual(response.status_code, 200, response.text)
                    payload = response.json()
                    self.assertIn('quick brown fox', payload['text'].lower())
                    self.assertIn('-->', payload['srt'])
                    self.assertTrue(payload['vtt'].startswith('WEBVTT'))
                    self.assertEqual(
                        payload['model_revision'],
                        'a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb')
                    ack = client.post(
                        value['url'] + f"/api/generate/transcribe/jobs/"
                                        f"{payload['job_id']}/ack", headers=headers)
                    self.assertEqual(ack.status_code, 200, ack.text)
                    self.assertTrue(ack.json()['ok'])
                    detail = client.get(
                        value['url'] + f"/api/generate/transcribe/jobs/"
                                       f"{payload['job_id']}", headers=headers).json()['job']
                    self.assertEqual(detail['state'], 'done')
                    self.assertTrue(detail['acknowledged'])
                    self.assertEqual(detail['text'], payload['text'])
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
            self.assertFalse(ready.exists(), log.read_text())


if __name__ == '__main__':
    unittest.main()
