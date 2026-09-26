#!/usr/bin/env python3
"""Run ONE video generation through the Video worker's own code path, and record what happened.

This is the bridge between `ltx-video-measure.py` (a standalone runner) and the lane: it builds
the same `GenerationManager` the packaged worker builds, drives the same FastAPI app the Hub
drives, submits through the worker's own routes, and fetches the artifact from the worker's own
route. Nothing about the request path is stubbed; the only thing this script supplies is the
library receipt, which it writes with `model_packages.verify_installed` before the run.

Why it exists: the plan's verification order is *worker alone → through the chain → tier gating →
hardware*. The worker-alone step must prove the lane reproduces the qualification runner's
numbers, and it must prove the worker's own artifact checks (MP4 box parse, audio track, duration)
accept what the pack produces. A standalone measurement cannot prove either.

Run it from the repository root with the Video runtime:

    .build/video-runtime/bin/python3 qualification/ltx-video-worker-run.py \
        --models-dir "$HOME/StudioHubModels" --image qualification/scene.png

It also runs on a Mac that has no checkout at all, which is what `RUN-LTX-VIDEO-WORKER.command`
uses to measure a fleet node from the SSD: it then takes `components/` and `model-manifests/`
from the installed app's `Contents/Resources` (or from `STUDIOHUB_HUB_ROOT`), and the runtime
from the app's `runtimes/video` — the same runtime the worker itself is launched with.

Every number it writes is either measured here or absent. The receipt names the exact command.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time


def hub_root() -> Path:
    """The folder holding `components/` and `model-manifests/`.

    A checkout has them beside this file's parent. A Mac with only the app has the same two
    folders inside the bundle, and that is not a fallback for convenience — it is what makes the
    lane's own measurement runnable on a node that has the app and nothing else.
    """
    candidates = [
        os.environ.get('STUDIOHUB_HUB_ROOT', ''),
        str(Path(__file__).resolve().parents[1]),
        '/Applications/Studio Hub.app/Contents/Resources',
    ]
    marker = Path('components') / 'video' / 'app' / 'backend' / 'main.py'
    for candidate in candidates:
        if candidate and (Path(candidate) / marker).is_file():
            return Path(candidate)
    raise SystemExit(
        "cannot find Studio Hub's components/ and model-manifests/ on this Mac.\n"
        "Looked in: " + ', '.join(c for c in candidates if c) + "\n"
        "Install Studio Hub, or set STUDIOHUB_HUB_ROOT to a checkout or a Resources folder.")


ROOT = hub_root()
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'components' / 'video' / 'app' / 'backend'))

from components import model_packages                       # noqa: E402
from components.video.app.backend import generation          # noqa: E402
from components.video.app.backend.main import create_app     # noqa: E402


def load_manifest(package_id: str) -> dict:
    path = ROOT / 'model-manifests' / f'{package_id}.json'
    manifest = json.loads(path.read_text())
    model_packages.validate_manifest(manifest)
    return manifest


def ensure_installed(manifest: dict, catalog: dict, library: Path, *, verify: bool) -> dict:
    """Register the package if it is not already, and say which state it ended in."""
    state = model_packages.preflight(manifest, library, catalog=catalog)
    if state['state'] == 'files_verified':
        return state
    if not verify:
        raise SystemExit(
            f"{manifest['package_id']} is {state['state']} in {library}: {state.get('reason')}\n"
            f"Install it there, or re-run with --verify to hash and record it once.")
    print(f"verifying {manifest['package_id']} …", flush=True)
    result = model_packages.verify_installed(manifest, library, catalog=catalog)
    if result['state'] != 'files_verified':
        raise SystemExit(
            f"{manifest['package_id']} could not be verified: {result['state']} — "
            f"{result.get('reason')}")
    return result


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models-dir', type=Path, default=Path.home() / 'StudioHubModels')
    parser.add_argument('--data-dir', type=Path, default=None)
    parser.add_argument('--repo', default='baa-ai/LTX-2.3-22B-RAM-12GB-MLX')
    parser.add_argument('--prompt', default='A slow, steady camera push toward a quiet harbour at dawn.')
    parser.add_argument('--image', type=Path, default=None,
                        help='Source scene. Omit for text-to-video.')
    parser.add_argument('--aspect-ratio', default=None)
    parser.add_argument('--resolution', default=None)
    parser.add_argument('--duration', type=int, default=None)
    parser.add_argument('--results', type=Path, default=None)
    parser.add_argument('--verify', action='store_true',
                        help='Hash and record the packages when no receipt exists')
    parser.add_argument('--timeout', type=float, default=1800.0)
    args = parser.parse_args(argv)

    stamp = time.strftime('%Y%m%d-%H%M%S')
    results = (args.results or ROOT / 'qualification' / f'ltx-video-worker-results-{stamp}')
    # An existing folder is allowed when it holds nothing but this run's log: the double-clickable
    # wrapper creates the folder first so it can tee the console output into it, and refusing that
    # would make the wrapper fail before the run started. A folder with a previous receipt in it is
    # still refused — overwriting one would destroy the only record of a run that cost 20 minutes.
    results.mkdir(parents=True, exist_ok=True)
    stray = [entry.name for entry in results.iterdir() if entry.name != 'run.log']
    if stray:
        raise SystemExit(
            f"{results} already holds {', '.join(sorted(stray)[:4])} — refusing to overwrite a "
            f"previous run's receipt. Give this run its own --results folder.")
    data = (args.data_dir or results / 'state').expanduser().resolve()
    data.mkdir(parents=True, exist_ok=True)
    library = args.models_dir.expanduser().resolve()

    record: dict = {
        'schema': 'studiohub.video-worker-run.v1',
        'started_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
        'args': {k: str(v) for k, v in vars(args).items() if k != 'results'},
        'result': {'status': 'started'},
    }

    def save(**extra: object) -> None:
        record['result'].update(extra)
        record['finished_at'] = time.strftime('%Y-%m-%dT%H:%M:%S%z')
        (results / 'measurement.json').write_text(json.dumps(record, indent=2, default=str))

    ltx = load_manifest('ltx-2-3-22b-ram-12gb-mlx')
    gemma = load_manifest('gemma-3-12b-it-4bit')
    catalog = {ltx['package_id']: ltx, gemma['package_id']: gemma}
    # The companion first, and not for tidiness: `verify_installed` checks that every required
    # package is itself verified *after* hashing this one, so a parent verified before its
    # companion returns `dependency_missing` even though its own receipt was just written.
    ensure_installed(gemma, catalog, library, verify=args.verify)
    installed = ensure_installed(ltx, catalog, library, verify=args.verify)
    record['library'] = {'models_dir': str(library), 'folder': installed.get('folder')}
    save()

    manager = generation.GenerationManager(
        data, library, ltx, 'verification-run', manifests=catalog,
        total_memory_bytes=generation.host_memory_bytes())
    app = create_app(manager, 'verification-token')
    try:
        from fastapi.testclient import TestClient
    except Exception as exc:  # pragma: no cover - environment dependent
        raise SystemExit(f'the Video runtime cannot build a test client: {exc}')

    headers = {'X-Hub-Token': 'verification-token'}
    with TestClient(app) as client:
        if args.image is not None:
            with args.image.open('rb') as stream:
                response = client.post(
                    '/api/generate/video2video',
                    files={'file': (args.image.name, stream, 'image/png')},
                    data={'repo': args.repo, 'mode': 'img2video',
                          'prompt': args.prompt,
                          **({'aspect_ratio': args.aspect_ratio} if args.aspect_ratio else {}),
                          **({'resolution': args.resolution} if args.resolution else {}),
                          **({'duration': str(args.duration)} if args.duration else {})},
                    headers=headers)
        else:
            body = {'repo': args.repo, 'mode': 'text-to-video', 'prompt': args.prompt}
            if args.aspect_ratio:
                body['aspect_ratio'] = args.aspect_ratio
            if args.resolution:
                body['resolution'] = args.resolution
            if args.duration:
                body['duration'] = args.duration
            response = client.post('/api/generate/txt2video', json=body, headers=headers)
        if response.status_code >= 400:
            record['acceptance'] = {'status': response.status_code, 'body': response.text[:500]}
            save(status='refused', reason=f'accepted HTTP {response.status_code}')
            print(response.text)
            return 2
        accepted = response.json()['job']
        job_id = accepted['id']
        record['acceptance'] = {'status': response.status_code,
                                'job_id': job_id, 'state': accepted['state'],
                                'params': accepted.get('params')}
        print(f'accepted {job_id} ({accepted["state"]})', flush=True)
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
        }
        save(status='ok', artifact=record['artifact'])
    print(json.dumps({'status': record['result']['status'], 'job': record.get('job', {}).get('id'),
                      'wall_seconds': record.get('job', {}).get('duration_seconds'),
                      'peak_footprint_bytes': record.get('job', {}).get('peak_footprint_bytes'),
                      'peak_mlx_bytes': record.get('job', {}).get('peak_mlx_bytes'),
                      'artifact': record.get('artifact', {}).get('path'),
                      'results': str(results)}, indent=2))
    return 0 if record['result']['status'] == 'ok' else 5


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
