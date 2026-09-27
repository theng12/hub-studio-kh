#!/usr/bin/env python3
"""Run ONE Lance text-to-video job through the Video worker's own code path.

The counterpoint to `ltx-video-worker-run.py`: same shape, the other engine. It builds the same
`GenerationManager` the packaged worker builds, drives the same FastAPI app the Hub drives,
submits through `POST /api/generate/txt2video`, and fetches the artifact from the worker's own
route — but the worker is Python 3.11 and Lance's runner is Python **3.12**, so the request path
crosses a process boundary this time. That crossing is the thing worth proving: the bridge, the
spec, the streamed progress, the MP4 it writes and the artifact checks that accept it.

Run it from the repository root with the **Video** runtime (the worker's own interpreter):

    .build/video-runtime/bin/python3 qualification/lance-video-worker-run.py \
        --models-dir /Volumes/KH-STUDIO-NOVA/model-library/_installed

Every number it writes is either measured here or absent. The receipt names the exact command.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'components' / 'video' / 'app' / 'backend'))

from components import model_packages                       # noqa: E402
from components.video.app.backend import generation          # noqa: E402
from components.video.app.backend.main import create_app     # noqa: E402

PACKAGES = ('lance-3b-video-bf16', 'lance-3b-processor-qwen2-5-vl')


def load_manifest(package_id: str) -> dict:
    manifest = json.loads((ROOT / 'model-manifests' / f'{package_id}.json').read_text())
    model_packages.validate_manifest(manifest)
    return manifest


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models-dir', type=Path, default=Path.home() / 'StudioHubModels')
    parser.add_argument('--data-dir', type=Path, default=None)
    parser.add_argument('--repo', default='mlx-community/Lance-3B-Video-bf16')
    parser.add_argument('--prompt', default='A red panda surfing on a sunny wave.')
    parser.add_argument('--resolution', default='256p')
    parser.add_argument('--duration', type=int, default=2)
    parser.add_argument('--results', type=Path, default=None)
    parser.add_argument('--verify', action='store_true')
    parser.add_argument('--timeout', type=float, default=1800.0)
    args = parser.parse_args(argv)

    stamp = time.strftime('%Y%m%d-%H%M%S')
    results = args.results or ROOT / 'qualification' / f'lance-video-worker-results-{stamp}'
    results.mkdir(parents=True, exist_ok=True)
    data = (args.data_dir or results / 'state').expanduser().resolve()
    data.mkdir(parents=True, exist_ok=True)
    library = args.models_dir.expanduser().resolve()

    record: dict = {
        'schema': 'studiohub.video-worker-run.v1',
        'engine': 'video-lance',
        'started_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
        'args': {k: str(v) for k, v in vars(args).items() if k != 'results'},
        'result': {'status': 'started'},
    }

    def save(**extra: object) -> None:
        record['result'].update(extra)
        record['finished_at'] = time.strftime('%Y-%m-%dT%H:%M:%S%z')
        (results / 'measurement.json').write_text(json.dumps(record, indent=2, default=str))

    manifests = {name: load_manifest(name) for name in PACKAGES}
    ltx_manifest = load_manifest('ltx-2-3-22b-ram-12gb-mlx')
    gemma_manifest = load_manifest('gemma-3-12b-it-4bit')
    # The manager's catalog is every manifest this worker may see: Lance and its processor are the
    # subject, the LTX pair is what a real install also holds.
    catalog = {**manifests, ltx_manifest['package_id']: ltx_manifest,
               gemma_manifest['package_id']: gemma_manifest}

    # The companion first: `verify_installed` checks a package's dependencies *after* hashing it,
    # so a parent recorded before its companion comes back `dependency_missing`.
    for name in ('lance-3b-processor-qwen2-5-vl', 'lance-3b-video-bf16'):
        state = model_packages.preflight(manifests[name], library, catalog=catalog)
        if state['state'] != 'files_verified':
            if not args.verify:
                raise SystemExit(f"{name} is {state['state']}: {state.get('reason')}\n"
                                 f"Re-run with --verify to hash and record it once.")
            print(f'verifying {name} …', flush=True)
            state = model_packages.verify_installed(manifests[name], library, catalog=catalog)
        if state['state'] != 'files_verified':
            raise SystemExit(f"{name} could not be verified: {state['state']} — "
                             f"{state.get('reason')}")
        record.setdefault('packages', {})[name] = {'folder': state.get('folder')}
    save()

    manager = generation.GenerationManager(
        data, library, manifests['lance-3b-video-bf16'], 'verification-run',
        manifests=catalog, total_memory_bytes=generation.host_memory_bytes())
    app = create_app(manager, 'verification-token')
    from fastapi.testclient import TestClient

    headers = {'X-Hub-Token': 'verification-token'}
    with TestClient(app) as client:
        response = client.post('/api/generate/txt2video', json={
            'repo': args.repo, 'mode': 'text-to-video', 'prompt': args.prompt,
            'resolution': args.resolution, 'duration': args.duration,
        }, headers=headers)
        if response.status_code >= 400:
            record['acceptance'] = {'status': response.status_code, 'body': response.text[:500]}
            save(status='refused', reason=f'accepted HTTP {response.status_code}')
            print(response.text)
            return 2
        accepted = response.json()['job']
        job_id = accepted['id']
        record['acceptance'] = {'status': response.status_code, 'job_id': job_id,
                                'state': accepted['state'], 'params': accepted.get('params')}
        print(f"accepted {job_id} ({accepted['state']}) {accepted.get('params')}", flush=True)
        save(status='running', job_id=job_id)

        deadline = time.monotonic() + args.timeout
        job = accepted
        last = None
        while time.monotonic() < deadline:
            if job['state'] != last:
                print(f"  {job['state']} {job.get('progress_stage') or ''} "
                      f"{(job.get('progress') or 0) * 100:.0f}%", flush=True)
                last = job['state']
            if job['state'] in generation.TERMINAL:
                break
            time.sleep(2.0)
            job = client.get(f'/api/generate/jobs/{job_id}', headers=headers).json()['job']
        record['job'] = job
        if job['state'] != 'done':
            save(status='failed', reason=f"terminal state {job['state']}: {job.get('error')}")
            print(f"failed: {job.get('error')}")
            return 3

        artifact = client.get(job['output_url'], headers=headers)
        if artifact.status_code != 200:
            save(status='failed', reason=f'artifact HTTP {artifact.status_code}')
            return 4
        produced = results / 'generated.mp4'
        produced.write_bytes(artifact.content)
        checks = generation.mp4_facts(produced)
        record['artifact'] = {
            'path': str(produced), 'http_bytes': len(artifact.content),
            'declared_bytes': job.get('bytes'), 'declared_sha256': job.get('sha256'),
            'sha256_matches': generation.sha256(artifact.content).hexdigest() == job.get('sha256'),
            'boxes': checks,
            'runner': job.get('runner'), 'memory_mode': (job.get('runner_detail') or {}).get('memory_mode'),
        }
        save(status='ok', artifact=record['artifact'])
    print(json.dumps({
        'status': record['result']['status'], 'job': record.get('job', {}).get('id'),
        'wall_seconds': record.get('job', {}).get('duration_seconds'),
        'peak_footprint_bytes': record.get('job', {}).get('peak_footprint_bytes'),
        'peak_mlx_bytes': record.get('job', {}).get('peak_mlx_bytes'),
        'runner': record.get('job', {}).get('runner'),
        'boxes': (record.get('artifact') or {}).get('boxes'),
        'results': str(results)}, indent=2))
    return 0 if record['result']['status'] == 'ok' else 5


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
