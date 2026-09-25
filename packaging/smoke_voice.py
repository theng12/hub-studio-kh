"""Run a staged Voice artifact twice from different paths, with isolated node data.

This local qualification helper never enrolls a node or downloads a model.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
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


def _verify_pilot_file(path: Path, entry: dict, repo: str) -> None:
    """One recorded file, by exact size and SHA-256. The repo names it in the failure."""
    digest = hashlib.sha256()
    if not path.is_file() or path.stat().st_size != entry['bytes']:
        raise ValueError(f'Missing or wrong-size pilot input for {repo}: {path}')
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    if digest.hexdigest() != entry['sha256']:
        raise ValueError(f'Pilot input checksum mismatch for {repo}: {path}')


def selected_pilot_models(record: dict) -> list[dict]:
    """The recorded models a run is planned and verified from.

    `shipped_smoke` supersedes the historical `models` array **per role**: its `tts` entry
    names a model the packaged catalogue still offers, while the 0.1.0 pilot's `tts` entry
    (Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit) was cut from the catalog in ST-13.B. The
    historical entries stay in the file as evidence of what that pilot ran, and the roles
    the shipped record does not name (the Whisper `asr` and its companion) stay in force.
    """
    shipped = ((record.get('shipped_smoke') or {}).get('models')) or []
    superseded = {entry.get('role') for entry in shipped if isinstance(entry, dict)}
    historical = [
        model for model in record.get('models') or []
        if isinstance(model, dict) and model.get('role') not in superseded
    ]
    return [entry for entry in shipped if isinstance(entry, dict)] + historical


def validate_pilot_inputs(models: Path, expected: dict) -> None:
    """Verify this proof's recorded model snapshots before starting a worker.

    Two layouts, because the records describe two kinds of install. `hf-cache` is a
    Hugging Face cache snapshot under ``<models>/hub/models--<repo>/`` (the 0.1.0 pilot's
    copied snapshots, with the exact `refs/main` bytes); `package` is the app's own
    package library, ``<models>/<package_id>/<revision>/<file>``, which is how a shipped
    model is installed on this Mac - and the layout the old check could not see, so a
    shipped-model record would have failed validation before it ran.
    """
    for model in selected_pilot_models(expected):
        if (model.get('layout') or 'hf-cache') == 'package':
            root = models / model['package_id'] / model['revision']
            for entry in model['files']:
                _verify_pilot_file(root / entry['path'], entry, model['repo'])
            continue
        cache = models / 'hub' / ('models--' + model['repo'].replace('/', '--'))
        reference = cache / 'refs/main'
        if not reference.is_file() or reference.read_bytes() != model['revision'].encode():
            raise ValueError(f"Invalid cache reference for {model['repo']}; expected exact revision bytes without newline")
        snapshot = cache / 'snapshots' / model['revision']
        for entry in model['files']:
            _verify_pilot_file(snapshot / entry['path'], entry, model['repo'])


# The one Voice engine this smoke knows how to build a request for. A request body is
# engine-shaped, so a family on another engine is recorded as skipped with the engine named -
# never dropped without a word.
DRIVEN_ENGINE = 'mlx-audio'
SMOKE_FILE = 'packaging/smoke_voice.py'
ROOT = Path(__file__).resolve().parents[1]
PILOT_INPUTS = ROOT / 'qualification/model-inputs.json'


def pilot_models() -> dict:
    """The recorded pilot snapshot by role, read from `qualification/model-inputs.json`.

    Used only when a caller supplies no packaged catalogue. `run_worker` passes the worker's own
    `/api/catalog`, so the exercised set is the packaged data and a model added there is smoked
    without editing this file. Each recorded entry states its role - `tts`, `asr` or `companion` -
    and a `tts` entry may carry the `smoke_params` that give it a voice, so the snapshot is data
    rather than a pair of literals in this module. The plan is read through
    `selected_pilot_models`, so the shipped-model entry supersedes the historical 0.1.0 `tts`
    entry whose model ST-13.B cut.
    """
    record = json.loads(PILOT_INPUTS.read_text())
    plan: dict[str, list[dict]] = {'tts': [], 'asr': []}
    for model in selected_pilot_models(record):
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


def recorded_reference(record: dict) -> dict | None:
    """The reference clip a recorded `tts` entry names, resolved and checksum-verified.

    A cloning model - and the shipped Qwen3-TTS Base tiers are cloning models - refuses a
    request carrying neither a Voices-library id nor a private reference, and a smoke state
    directory starts empty. The recorded clip is the project's own tracked evidence, the
    same one the stored r07 clone probes staged, so the run exercises the shipped model
    instead of reporting it skipped. Returns None when no entry names one, which keeps the
    plain request path (and a model that needs no reference) exactly as it was.
    """
    for entry in selected_pilot_models(record):
        reference = entry.get('reference')
        if entry.get('role') != 'tts' or not isinstance(reference, dict):
            continue
        path = (ROOT / str(reference['file'])).resolve()
        expected = str(reference.get('sha256') or '')
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(
                f'the recorded reference clip is missing or changed: {path} (expected sha256 '
                f'{expected}); the clone this smoke runs would not be the recorded run')
        return {'repo': entry['repo'], 'path': path, 'transcript': reference.get('transcript', ''),
                'sha256': expected}
    return None


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


def _request_with_reference(url: str, token: str, repo: str, text: str, params: dict,
                            reference: dict) -> tuple[int, dict]:
    """Start one clone job through the worker's own private-reference route.

    `POST /api/generate/txt2speech/reference` takes the same JSON body as the plain route
    plus the reference clip, and derives the model-compatible execution copy itself - the
    path the stored r07 clone probes used. Kept as one function so the plain route below is
    byte-for-byte what it was for every model that does not need a reference.
    """
    boundary = '----studiohub-smoke-' + hashlib.sha256(os.urandom(16)).hexdigest()[:24]
    body = json.dumps({
        'repo': repo,
        'text': text,
        # The recorded reference states the words it says, and this worker's reference route
        # takes them from the request (`reference_audio.prepare(transcript=...)`). Omitting it
        # is a refusal by name for a model whose profile requires alignment - Qwen3-TTS Base
        # does - and the run would never reach the engine.
        **({'ref_transcript': reference['transcript']} if reference.get('transcript') else {}),
        **params,
        'client_request_id': 'standalone-packaging-smoke',
    }).encode()
    audio = Path(reference['path']).read_bytes()

    def field(name: str, value: str) -> bytes:
        return (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
                f'{value}\r\n').encode()

    payload = b''.join([
        field('request_json', body.decode()),
        field('source_sha256', reference.get('sha256', '')),
        (f'--{boundary}\r\nContent-Disposition: form-data; name="audio"; '
         f'filename="reference.wav"\r\nContent-Type: audio/wav\r\n\r\n').encode(),
        audio,
        b'\r\n',
        f'--{boundary}--\r\n'.encode(),
    ])
    req = urllib.request.Request(
        url + '/api/generate/txt2speech/reference', data=payload,
        headers={'Content-Type': f'multipart/form-data; boundary={boundary}',
                 'X-Studio-Token': token})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as response:
        return response.code, json.load(response)


def _run_tts(url: str, token: str, repo: str, params: dict, output: Path,
             reference: dict | None = None) -> dict:
    text = 'This is a standalone Voice Studio test. The bundled engine is speaking clearly.'
    if reference is None:
        status, response = request(url + '/api/generate/txt2speech', token, body={
            'repo': repo,
            'text': text,
            **params,
            'client_request_id': 'standalone-packaging-smoke',
        })
    else:
        status, response = _request_with_reference(url, token, repo, text, params, reference)
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
                    availability: dict | None = None,
                    reference: dict | None = None) -> dict:
    """Run one generation per catalogue TTS model and one transcript per catalogue ASR model.

    The model set comes from the worker's own published catalogue (or, when a caller has none, the
    recorded pilot snapshot's roles). Every model is reported as run or skipped with the reason,
    and a run that exercises nothing raises: the previous version sent one frozen TTS repo and one
    frozen ASR repo, so a model added to the catalogue was simply not smoked and nothing said so.

    `reference` is the recorded clone clip for exactly one repo (`recorded_reference`). It is
    used for that model only: a cloning model cannot run without one, and every other model's
    request stays the plain JSON body it has always been.
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
        clone_reference = (
            reference if reference and reference.get('repo') == row['repo'] else None
        )
        run = _run_tts(url, token, row['repo'], params, output, reference=clone_reference)
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


def _await_ready(process: subprocess.Popen, ready_file: Path, log: Path,
                 timeout: float = 60.0) -> str:
    """The URL the worker published in its own ready file.

    Readiness comes from that file and not from the log. The worker binds its sockets itself and
    hands them to uvicorn, and uvicorn deliberately logs no "Uvicorn running on ..." line when
    sockets are supplied (`uvicorn/server.py`: the message is skipped when `sockets is not None`),
    so matching the log line made this smoke unable to see a worker that had started perfectly -
    it waited out its timeout and reported "did not become ready" with "Application startup
    complete." sitting in the log beside it. The recorded pid must be the process we launched, so
    a file left by an earlier launch cannot make a dead worker look ready.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            ready = json.loads(ready_file.read_text())
        except (FileNotFoundError, ValueError):
            ready = None
        if ready and ready.get('pid') == process.pid and ready.get('url'):
            return str(ready['url'])
        if process.poll() is not None:
            raise RuntimeError(f'Worker exited {process.returncode}; inspect {log}')
        time.sleep(0.1)
    raise RuntimeError(f'Worker did not become ready; inspect {log}')


def run_worker(artifact: Path, state: Path, models: Path, log: Path, *, generate: bool = False,
               reference: dict | None = None) -> dict:
    started = time.monotonic()
    executable = artifact / 'Contents/MacOS/StudioHubVoice'
    release = json.loads((artifact / 'Contents/Resources/release-manifest.json').read_text())
    # The worker refuses a ready file outside its own external data directory, so it goes beside
    # the state the two launches share, named per launch so the second cannot read the first's.
    ready_file = state / f'{log.stem}.ready.json'
    ready_file.unlink(missing_ok=True)
    with log.open('w') as stream:
        process = subprocess.Popen([str(executable), '--data-dir', str(state),
            '--models-dir', str(models), '--ready-file', str(ready_file), '--offline'],
            stdout=stream, stderr=subprocess.STDOUT,
            env={'PATH': '/usr/bin:/bin', 'LANG': 'en_US.UTF-8',
                 'KH_STUDIO_TOKEN': 'smoke-inherited-token-must-be-ignored',
                 'HF_HOME': '/nonexistent/inherited-cache',
                 'PINOKIO_SHARE_LOCAL': 'true',
                 'PINOKIO_SHARE_PASSCODE': 'smoke-passcode-ignore',
                 'PYTHONPATH': '/nonexistent/inherited-python'})
        try:
            url = _await_ready(process, ready_file, log)
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
                    url, token, log.parent, catalogue=catalog, transcriptions=transcription,
                    reference=reference)
            return result
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def smoke(artifact: Path, output: Path, models: Path | None = None) -> None:
    record = json.loads(PILOT_INPUTS.read_text())
    reference = recorded_reference(record) if models is not None else None
    if models is not None:
        validate_pilot_inputs(models, record)
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
    second = run_worker(moved, state, models, output / 'second-start.log', generate=generate,
                        reference=reference)
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
