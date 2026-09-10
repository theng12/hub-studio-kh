"""Run a staged Voice artifact twice from different paths, with isolated node data.

This local qualification helper never enrolls a node or downloads a model.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import urllib.parse

from stage_voice import tree_digest


def request(url: str, token: str | None = None, body=None):
    headers = {'X-Studio-Token': token} if token else {}
    if body is not None:
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, headers=headers,
        data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as response:
        return response.code, json.load(response)


def validate_pilot_inputs(models: Path, expected: dict) -> None:
    """Verify this proof's recorded model snapshots before starting a worker."""
    for model in expected['models']:
        cache = models / 'hub' / ('models--' + model['repo'].replace('/', '--'))
        reference = cache / 'refs/main'
        if not reference.is_file() or reference.read_bytes() != model['revision'].encode():
            raise ValueError(f"Invalid cache reference for {model['repo']}; expected exact revision bytes without newline")
        snapshot = cache / 'snapshots' / model['revision']
        for entry in model['files']:
            path = snapshot / entry['path']
            digest = hashlib.sha256()
            if not path.is_file() or path.stat().st_size != entry['bytes']:
                raise ValueError(f'Missing or wrong-size pilot input: {path}')
            with path.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(chunk)
            if digest.hexdigest() != entry['sha256']:
                raise ValueError(f'Pilot input checksum mismatch: {path}')


def exercise_models(url: str, token: str, output: Path) -> dict:
    started = time.monotonic()
    status, response = request(url + '/api/generate/txt2speech', token, body={
        'repo': 'mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit',
        'text': 'This is a standalone Voice Studio test. The bundled engine is speaking clearly.',
        'preset_speaker': 'Ryan', 'language': 'en', 'seed': 42,
        'client_request_id': 'standalone-packaging-smoke',
    })
    assert status == 200, response
    job = response['job']
    deadline = time.monotonic() + 300
    while job['state'] not in {'done', 'error', 'cancelled'} and time.monotonic() < deadline:
        time.sleep(1)
        status, response = request(url + '/api/generate/jobs/' + job['id'], token)
        assert status == 200, response
        job = response['job']
    assert job['state'] == 'done', job
    with urllib.request.urlopen(url + '/api/generate/jobs/' + job['id'] + '/audio', timeout=30) as response:
        audio = response.read()
        expected_hash = response.headers.get('X-Content-SHA256')
    digest = hashlib.sha256(audio).hexdigest()
    assert expected_hash == digest and len(audio) > 44
    audio_path = output / 'generated-audio.wav'
    audio_path.write_bytes(audio)
    generation_seconds = round(time.monotonic() - started, 3)
    form = urllib.parse.urlencode({'job_id': job['id'],
        'model': 'mlx-community/whisper-large-v3-turbo', 'language': 'en'}).encode()
    req = urllib.request.Request(url + '/api/transcribe', data=form,
        headers={'Content-Type': 'application/x-www-form-urlencoded', 'X-Studio-Token': token})
    with urllib.request.urlopen(req, timeout=300) as response:
        transcript = json.load(response)
    assert str(transcript.get('text', '')).strip(), transcript
    (output / 'transcription.json').write_text(json.dumps(transcript, indent=2) + '\n')
    return {'generation_seconds': generation_seconds, 'audio_bytes': len(audio),
            'audio_sha256': digest, 'transcription_text': transcript['text'],
            'total_seconds': round(time.monotonic() - started, 3)}


def run_worker(artifact: Path, state: Path, models: Path, log: Path, *, generate: bool = False) -> dict:
    started = time.monotonic()
    executable = artifact / 'Contents/MacOS/StudioHubVoice'
    release = json.loads((artifact / 'Contents/Resources/release-manifest.json').read_text())
    with log.open('w') as stream:
        process = subprocess.Popen([str(executable), '--data-dir', str(state),
            '--models-dir', str(models), '--offline'], stdout=stream, stderr=subprocess.STDOUT,
            env={'PATH': '/usr/bin:/bin', 'LANG': 'en_US.UTF-8',
                 'KH_STUDIO_TOKEN': 'smoke-inherited-token-must-be-ignored',
                 'HF_HOME': '/nonexistent/inherited-cache',
                 'PINOKIO_SHARE_LOCAL': 'true',
                 'PINOKIO_SHARE_PASSCODE': 'smoke-passcode-ignore',
                 'PYTHONPATH': '/nonexistent/inherited-python'})
        try:
            deadline = time.monotonic() + 60
            url = None
            while time.monotonic() < deadline:
                match = re.search(r'Uvicorn running on (http://127\.0\.0\.1:\d+)', log.read_text())
                if match and 'Application startup complete.' in log.read_text():
                    url = match[1]
                    break
                if process.poll() is not None:
                    raise RuntimeError(f'Worker exited {process.returncode}; inspect {log}')
                time.sleep(0.1)
            if url is None:
                raise RuntimeError(f'Worker did not become ready; inspect {log}')
            startup_seconds = round(time.monotonic() - started, 3)
            status, health = request(url + '/api/health')
            assert status == 200, health
            identity = health['release']
            for key in ('version', 'payload_sha256', 'runtime_sha256', 'components'):
                assert identity[key] == release[key], (key, identity)
            expected_commit = 'unknown' if release['development_build'] else release['implementation_commit']
            assert identity['implementation_commit'] == expected_commit, identity
            status, version = request(url + '/api/version')
            assert status == 200 and version['release'] == identity, version
            status, notes = request(url + '/api/release-notes')
            assert status == 200 and notes['releases'][0]['version'] == release['version'], notes
            status, catalog = request(url + '/api/catalog')
            assert status == 200, catalog
            status, connectivity = request(url + '/api/connectivity')
            assert status == 200 and connectivity['bind_host'] == '127.0.0.1', connectivity
            assert connectivity['listen_port'] == urllib.parse.urlparse(url).port, connectivity
            assert connectivity['share_local_enabled'] is False, connectivity
            token_path = state / 'voice/fleet_token'
            token = token_path.read_text().strip()
            assert token and token != 'smoke-inherited-token-must-be-ignored'
            assert (token_path.stat().st_mode & 0o777) == 0o600
            assert Path(health['hf_home']) == models
            status, updates = request(url + '/api/auto-update/status', token)
            assert status == 200 and updates['capabilities']['managed_exact_commit'] is False, updates
            denied, _ = request(url + '/api/auto-update/update', body={})
            assert denied == 409, denied  # Existing local UI access is loopback-exempt.
            unsupported, _ = request(url + '/api/auto-update/update', token, body={})
            assert unsupported >= 400, unsupported
            result = {'startup_seconds': startup_seconds, 'health': health,
                    'catalog_status': status, 'managed_exact_commit': False,
                    'local_update_status': denied,
                    'packaged_update_status': unsupported}
            if generate:
                result['model_execution'] = exercise_models(url, token, log.parent)
            return result
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def smoke(artifact: Path, output: Path, models: Path | None = None) -> None:
    if models is not None:
        inputs = Path(__file__).resolve().parents[1] / 'qualification/model-inputs.json'
        validate_pilot_inputs(models, json.loads(inputs.read_text()))
    output.mkdir(parents=True, exist_ok=False)
    proof = output / 'Initial.app'
    shutil.copytree(artifact, proof, symlinks=True)
    before = tree_digest(proof)
    generate = models is not None
    if models is None:
        models = output / 'Model Library'
        models.mkdir()
    state = output / 'Node State'
    first = run_worker(proof, state, models, output / 'first-start.log')
    assert tree_digest(proof) == before, 'First startup modified the app bundle'
    # Move the same bytes; do not rebuild or rewrite paths between launches.
    moved = output / 'Relocated Folder with Spaces' / 'Voice Proof.app'
    moved.parent.mkdir()
    proof.rename(moved)
    second = run_worker(moved, state, models, output / 'second-start.log', generate=generate)
    assert tree_digest(moved) == before, 'Relocated startup modified the app bundle'
    report = {'platform': platform.platform(), 'machine': platform.machine(),
        'first': first, 'relocated': second, 'bundle_sha256': before,
        'bundle_unchanged': True, 'minimal_process_environment': True,
        'inherited_credential_stripping_tested': True,
        'fresh_os_install': False, 'generation_tested': generate,
        'pilot_input_hashes_verified': generate,
        'huggingface_offline_mode': True, 'network_physically_disabled': False,
        'sonoma_qualified': False}
    (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'report': str(output / 'report.json'), 'bundle_unchanged': True}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifact', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--models-dir', type=Path, help='Exercise Qwen + Whisper from imported pilot snapshots')
    args = parser.parse_args()
    smoke(args.artifact.resolve(), args.output.resolve(),
          args.models_dir.resolve() if args.models_dir else None)
