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


# The one Voice engine this smoke knows how to build a request for. A request body is
# engine-shaped, so a family on another engine is recorded as skipped with the engine named -
# never dropped without a word.
DRIVEN_ENGINE = 'mlx-audio'
SMOKE_FILE = 'packaging/smoke_voice.py'
PILOT_INPUTS = Path(__file__).resolve().parents[1] / 'qualification/model-inputs.json'


def pilot_models() -> dict:
    """The recorded pilot snapshot by role, read from `qualification/model-inputs.json`.

    Used only when a caller supplies no packaged catalogue. `run_worker` passes the worker's own
    `/api/catalog`, so the exercised set is the packaged data and a model added there is smoked
    without editing this file. Each recorded entry states its role - `tts`, `asr` or `companion` -
    and a `tts` entry may carry the `smoke_params` that give it a voice, so the snapshot is data
    rather than a pair of literals in this module.
    """
    plan: dict[str, list[dict]] = {'tts': [], 'asr': []}
    for model in json.loads(PILOT_INPUTS.read_text())['models']:
        role = model.get('role')
        row = {'repo': model['repo'], 'family': '', 'capabilities': ['tts', 'voice-cloning'],
               'languages': ['en'], 'engine': '', 'cached': True,
               'smoke_params': model.get('smoke_params')}
        if role in plan:
            plan[role].append(row)
        elif role != 'companion':
            raise ValueError(
                f"{PILOT_INPUTS}: {model.get('repo')!r} has no role; add \"role\": \"tts\", "
                f"\"asr\" or \"companion\" so the smoke knows what to do with it")
    if not plan['tts'] or not plan['asr']:
        raise ValueError(f'{PILOT_INPUTS} must name at least one tts and one asr model')
    return plan


def model_rows(catalogue: dict | None, transcriptions: dict | None,
               availability: dict | None) -> tuple[list[dict], list[dict]]:
    """Every model the packaged catalogue offers, as (tts rows, asr rows).

    A row that cannot be exercised is still returned, with `skip_reason` set: a model this smoke
    does not run is a fact the report has to carry, which is what B37 was missing - the pilot
    snapshot held two models and nothing said a third had been added to the catalogue.
    """
    if catalogue is None:
        plan = pilot_models()
        return plan['tts'], plan['asr']
    tts_rows, asr_rows = [], []
    for model in catalogue.get('models') or []:
        repo = str(model.get('repo') or '')
        capabilities = [str(value) for value in (model.get('capabilities') or [])]
        languages = [str(value) for value in (model.get('languages') or [])]
        row = {'repo': repo, 'family': str(model.get('family') or ''), 'capabilities': capabilities,
               'languages': languages, 'engine': str(model.get('engine') or ''),
               'cached': (model.get('cache') or {}).get('state') == 'cached'}
        if not row['cached']:
            row['skip_reason'] = 'not present in the pilot snapshot this run was given'
        elif 'tts' not in capabilities:
            row['skip_reason'] = f'capabilities {capabilities} declare no "tts"'
        elif row['engine'] and row['engine'] != DRIVEN_ENGINE:
            row['skip_reason'] = (
                f'engine {row["engine"]!r} has no request recipe in {SMOKE_FILE}; add one so '
                f'every engine the catalogue ships is exercised')
        elif languages and 'en' not in languages:
            row['skip_reason'] = (
                f'languages {languages} do not include "en", the only text this smoke sends; add '
                f'a per-language sample to {SMOKE_FILE}')
        tts_rows.append(row)
    for model in (transcriptions or {}).get('models') or []:
        repo = str(model.get('repo') or '')
        row = {'repo': repo, 'cached': bool(model.get('cached')),
               'default': bool(model.get('recommended')) or model.get('repo') == (transcriptions or {}).get('default_model')}
        if not row['cached']:
            row['skip_reason'] = 'not present in the pilot snapshot this run was given'
        asr_rows.append(row)
    return tts_rows, asr_rows


def _run_tts(url: str, token: str, repo: str, params: dict, output: Path) -> dict:
    status, response = request(url + '/api/generate/txt2speech', token, body={
        'repo': repo,
        'text': 'This is a standalone Voice Studio test. The bundled engine is speaking clearly.',
        **params,
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
    slug = re.sub(r'[^a-z0-9]+', '-', repo.rsplit('/', 1)[-1].lower()).strip('-')
    (output / f'generated-audio-{slug}.wav').write_bytes(audio)
    return {'repo': repo, 'job_id': job['id'], 'audio_bytes': len(audio),
            'audio_sha256': digest}


def _run_asr(url: str, token: str, job_id: str, repo: str, output: Path, *, primary: bool) -> dict:
    form = urllib.parse.urlencode({'job_id': job_id, 'model': repo, 'language': 'en'}).encode()
    req = urllib.request.Request(url + '/api/transcribe', data=form,
        headers={'Content-Type': 'application/x-www-form-urlencoded', 'X-Studio-Token': token})
    with urllib.request.urlopen(req, timeout=300) as response:
        transcript = json.load(response)
    assert str(transcript.get('text', '')).strip(), transcript
    slug = re.sub(r'[^a-z0-9]+', '-', repo.rsplit('/', 1)[-1].lower()).strip('-')
    name = 'transcription.json' if primary else f'transcription-{slug}.json'
    (output / name).write_text(json.dumps(transcript, indent=2) + '\n')
    return {'repo': repo, 'artifact': name, 'transcription_text': transcript['text']}


def exercise_models(url: str, token: str, output: Path, *, catalogue: dict | None = None,
                    transcriptions: dict | None = None,
                    availability: dict | None = None) -> dict:
    """Run one generation per catalogue TTS model and one transcript per catalogue ASR model.

    The model set comes from the worker's own published catalogue (or, when a caller has none, the
    recorded pilot snapshot's roles). Every model is reported as run or skipped with the reason,
    and a run that exercises nothing raises: the previous version sent one frozen TTS repo and one
    frozen ASR repo, so a model added to the catalogue was simply not smoked and nothing said so.
    """
    started = time.monotonic()
    tts_rows, asr_rows = model_rows(catalogue, transcriptions, availability)
    report: dict = {'models': [], 'tts': [], 'asr': []}
    first_job: str | None = None
    for row in tts_rows:
        if row.get('skip_reason'):
            report['models'].append({'repo': row['repo'], 'role': 'tts', 'verdict': 'skipped',
                                     'reason': row['skip_reason']})
            continue
        params = dict(row.get('smoke_params') or {})
        run = _run_tts(url, token, row['repo'], params, output)
        first_job = first_job or run['job_id']
        report['tts'].append(run)
        report['models'].append({'repo': row['repo'], 'role': 'tts', 'verdict': 'ran',
                                 'audio_sha256': run['audio_sha256'],
                                 'audio_bytes': run['audio_bytes']})
    if not report['tts']:
        reasons = '; '.join(f"{row['repo']}: {row.get('skip_reason') or 'not exercised'}"
                            for row in tts_rows) or 'the catalogue published no TTS model'
        raise RuntimeError(
            f'no TTS model was exercised, so this smoke proves nothing: {reasons}. Refresh '
            f'qualification/model-inputs.json (or pass --models-dir holding a model the packaged '
            f'catalogue still offers) and run again.')
    for index, row in enumerate(asr_rows):
        if row.get('skip_reason'):
            report['models'].append({'repo': row['repo'], 'role': 'asr', 'verdict': 'skipped',
                                     'reason': row['skip_reason']})
            continue
        run = _run_asr(url, token, first_job, row['repo'], output, primary=index == 0)
        report['asr'].append(run)
        report['models'].append({'repo': row['repo'], 'role': 'asr', 'verdict': 'ran',
                                 'artifact': run['artifact']})
    if not report['asr']:
        reasons = '; '.join(f"{row['repo']}: {row.get('skip_reason') or 'not exercised'}"
                            for row in asr_rows) or 'the catalogue published no ASR model'
        raise RuntimeError(f'no transcription model was exercised, so the audio was not read '
                           f'back: {reasons}.')
    elapsed = round(time.monotonic() - started, 3)
    # The single-model summary keeps the shape report readers already parse; the per-model lists
    # above are the coverage record.
    report.update({'generation_seconds': elapsed, 'total_seconds': elapsed,
                   'audio_bytes': report['tts'][0]['audio_bytes'],
                   'audio_sha256': report['tts'][0]['audio_sha256'],
                   'transcription_text': report['asr'][0]['transcription_text']})
    return report


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
            status, transcription = request(url + '/api/transcribe/availability')
            assert status == 200, transcription
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
                # The worker's own published model lists, so the exercised set is the packaged
                # catalogue rather than a frozen pair of repos in this file.
                result['model_execution'] = exercise_models(
                    url, token, log.parent, catalogue=catalog, transcriptions=transcription)
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
        # What the run actually exercised, per model, including the ones it did not: a model the
        # packaged catalogue offers and this receipt does not mention is the coverage hole B37
        # was about.
        'model_coverage': (second.get('model_execution') or {}).get('models', []),
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
