#!/usr/bin/env python3
"""Qualify an Image worker's reference/edit lane and write its receipt (2026-10-26).

Runs **through the worker**, not the CLI: every job goes through `GenerationManager.submit` ->
`validate_request` -> `preflight` -> the lane's memory plan -> the model class's
`generate_image(..., image_paths=...)`, and every number below is the worker's own
(`duration_seconds`, `peak_mlx_bytes`, `memory_mode`, `reference_mode`, `hooks`).

The configuration a run takes is *the worker's own decision* for the host it is told it has
(`--host-gb`) under the machine-wide setting (`--loading-mode`): `memory_plan(lane='edit')` reads
the row's `reference_memory` tiers, so the same command measures the full configuration on a big
Mac and a bounded one on a small Mac. The row records the setting and the configuration that ran
(`reference_mode`), so an emulated tier is never mistaken for a measured host size.

Each row records the host's free memory, swap and pressure level and the 1-minute load
immediately before and after the run, so a run taken while the Mac was also doing other work is
visible in the receipt rather than silently averaged into a published figure. **A run that
swaps is a failure** for this lane, not a slow success: the row's `swap_delta_mb` says.

Usage (from the repository root, with the runtime that ships):

    .build/image-runtime/bin/python3 qualification/image_edit_probe.py --model klein \
        --clone-from "/Volumes/<kit>/1 Install Apps/Hub Studio KH/model-library" \
        --receipt qualification/evidence/image-edit-2026-10-26.jsonl --host-gb 32
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from components import host_memory  # noqa: E402
from components.image.app.backend import generation, loading_mode  # noqa: E402
from components.image.app.backend.generation import (  # noqa: E402
    GenerationManager, host_chip, host_memory_bytes, memory_plan, plan_configuration)

PROMPT = ('Two friends standing together at a night market in Cambodia, warm lantern light, '
          'crowds and food stalls behind them, cinematic photograph, shallow depth of field')
SOURCE_REFERENCE = Path(
    '~/Developer/_orchestra/character-ref-test-20261025/out/src/'
    'char_a_20261025.png').expanduser()
# package id -> (short name, steps, worst measured reference canvas the row publishes)
MODELS = {
    'klein': 'flux2-klein-4b-mlx-4bit',
    'qwenz': 'qwen-image-2-1-turbo-mflux-q4',
}
GIGABYTE = 1000 ** 3


def _sysctl(name: str) -> str | None:
    try:
        return subprocess.check_output(['/usr/sbin/sysctl', '-n', name], text=True,
                                       timeout=5).strip()
    except (OSError, subprocess.SubprocessError):
        return None


class _VM:
    """The three fields `host_memory.available_bytes` reads, from macOS's own counters."""

    def __init__(self) -> None:
        page = int(_sysctl('vm.pagesize') or 16384)
        stats = {}
        for line in subprocess.check_output(['vm_stat'], text=True).splitlines():
            if ':' in line:
                key, _, value = line.partition(':')
                try:
                    stats[key.strip()] = int(value.strip().rstrip('.'))
                except ValueError:
                    continue        # the header line names the page size, not a count
        self.total = int(_sysctl('hw.memsize') or 0)
        self.available = (stats.get('Pages free', 0) + stats.get('Pages inactive', 0)) * page
        self.wired = stats.get('Pages wired down', 0) * page


def host_snapshot() -> dict:
    vm = _VM()
    swap = _sysctl('vm.swapusage') or ''
    used_mb = 0.0
    if 'used =' in swap:
        try:
            used_mb = float(swap.split('used =')[1].split('M')[0].strip())
        except (IndexError, ValueError):
            used_mb = 0.0
    return {
        'available_gb': round(host_memory.available_bytes(vm) / GIGABYTE, 2),
        'swap_used_mb': round(used_mb, 1),
        'pressure_level': int(_sysctl('kern.memorystatus_vm_pressure_level') or 0),
        # Contention evidence: this Mac also runs fleet work, so a run taken under load is
        # visible in the receipt instead of being averaged into a published figure.
        'load_1m': round(os.getloadavg()[0], 2),
    }


def reference_file(directory: Path, source: Path, pixels: int) -> Path:
    """The source portrait, resized so its longest edge is exactly `pixels`."""
    target = directory / f'reference-{pixels}.png'
    if not target.exists():
        with Image.open(source) as image:
            image = image.convert('RGB')
            longest = max(image.size)
            size = (round(image.width * pixels / longest), round(image.height * pixels / longest))
            image.resize(size, Image.LANCZOS).save(target, format='PNG')
    return target


def measured_row(job: dict, *, label: str, references: int, reference_pixels: int,
                 before: dict, after: dict, host_gb: float | None = None) -> dict:
    peak = job.get('peak_mlx_bytes')
    return {
        'label': label,
        # The host size the worker was *told* it has, when it was emulated: the tier this row
        # measures, as distinct from the real host recorded in `host_before`/`host_after`.
        'host_gb_told': host_gb,
        'mode': job.get('mode'),
        'references': references,
        'reference_pixels': reference_pixels,
        'canvas': [job.get('width'), job.get('height')],
        'steps': job.get('steps'),
        'memory_mode': job.get('memory_mode'),
        'loading_mode_setting': job.get('loading_mode_setting'),
        'reference_mode': job.get('reference_mode'),
        'memory_hooks': job.get('memory_hooks'),
        'state': job.get('state'),
        'error': job.get('error'),
        'seconds': round(float(job.get('duration_seconds') or 0.0), 2),
        'peak_mlx_gb': round(peak / GIGABYTE, 2) if peak else None,
        'swap_delta_mb': round(after['swap_used_mb'] - before['swap_used_mb'], 1),
        'reference_count': job.get('reference_count'),
        'host_before': before,
        'host_after': after,
    }


def run_job(manager: GenerationManager, model, label: str, references: list[str],
            reference_pixels: int, *, receipt: list[dict], host_gb: float | None = None) -> dict:
    before = host_snapshot()
    raw = {'repo': model.repo, 'prompt': PROMPT, 'aspect_ratio': '1:1',
           'steps': model.defaults['steps'], 'guidance': model.defaults['guidance'],
           'seed': 20261026, 'reference_images': references}
    started = time.time()
    job = manager.submit(raw, mode='edit')
    while job['state'] not in ('done', 'error', 'cancelled'):
        time.sleep(0.5)
        job = manager.get(job['id'])
    after = host_snapshot()
    row = measured_row(job, label=label, references=len(references),
                       reference_pixels=reference_pixels, before=before, after=after,
                       host_gb=host_gb)
    row['wall_seconds'] = round(time.time() - started, 2)
    receipt.append(row)
    print(json.dumps({k: row[k] for k in ('label', 'state', 'seconds', 'peak_mlx_gb',
                                          'swap_delta_mb', 'memory_mode', 'reference_mode',
                                          'error')}), flush=True)
    return job


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--model', choices=sorted(MODELS), default='klein')
    parser.add_argument('--models-dir', type=Path,
                        help='an already-disposable model library')
    parser.add_argument('--clone-from', type=Path,
                        help='a real library to copy-on-write clone into a temporary one, then '
                             'verify against this build\'s manifest')
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('--source-reference', type=Path, default=SOURCE_REFERENCE)
    parser.add_argument('--max-references', type=int, default=2)
    parser.add_argument('--min-references', type=int, default=1,
                        help='first reference count to run (default 1); pass 3 with '
                             '--max-references to measure only the counts above what the '
                             '2026-10-26 receipt already recorded')
    parser.add_argument('--dimensions', type=int, nargs='+', default=[1024])
    parser.add_argument('--host-gb', type=float, default=None,
                        help='pretend this Mac has N GiB, to measure the configuration the worker '
                             'would choose for that tier (default: this Mac\'s real memory)')
    parser.add_argument('--loading-mode', default='auto', choices=loading_mode.MODES,
                        help='the machine-wide loading_mode setting to run under')
    parser.add_argument('--label-prefix', default='')
    arguments = parser.parse_args()
    if not 1 <= arguments.min_references <= arguments.max_references:
        parser.error('reference counts must satisfy 1 <= min-references <= max-references')
    if bool(arguments.models_dir) == bool(arguments.clone_from):
        parser.error('pass exactly one of --models-dir (disposable) or --clone-from (real library)')

    package_id = MODELS[arguments.model]
    model = next(m for m in generation.MODELS.values() if m.package_id == package_id)
    manifest = json.loads((ROOT / 'model-manifests' / f'{package_id}.json').read_text())
    work = Path(tempfile.mkdtemp(prefix='image-edit-qualification-'))
    data = work / 'data'
    data.mkdir(mode=0o700)
    loading_mode.save(data, arguments.loading_mode)
    reference_dir = work / 'references'
    reference_dir.mkdir(mode=0o700)
    receipt: list[dict] = []
    models_dir = arguments.models_dir
    if arguments.clone_from:
        # A qualification probe never reads a real library in place (an app that predates the
        # library-ownership check shelves and retires weights it finds there). Clone the one
        # package on a copy-on-write filesystem and re-verify it against this build's manifest,
        # which is also how a stale receipt left by an older manifest is repaired on the copy.
        from components import model_packages
        sys.path.insert(0, str(ROOT / 'qualification'))
        import library_guard
        models_dir = work / 'library'
        library_guard.clone(arguments.clone_from, models_dir, [model.package_id])
        verified = model_packages.verify_installed(manifest, models_dir)
        if verified.get('state') != 'files_verified':
            print(f'the clone did not verify: {verified}', file=sys.stderr)
            return 2
    host_gb = host_bytes(arguments.host_gb)
    plan = memory_plan(model, host_gb or host_memory_bytes(), arguments.loading_mode, lane='edit')
    print(json.dumps({'host': {'chip': host_chip(),
                               'total_gb': round(host_memory_bytes() / GIGABYTE, 2),
                               'library': str(models_dir),
                               'reference_source': str(arguments.source_reference),
                               'python': sys.executable,
                               'mflux': _mflux_version()},
                      'model': model.repo,
                      'loading_mode': arguments.loading_mode,
                      'plan': {'configuration': plan_configuration(plan), 'low_ram': plan['low_ram'],
                               'staged': plan['staged'], 'lean': plan['lean'],
                               'stream': bool(plan['stream']),
                               'cache_limit_gb': plan['mlx_cache_limit_gb']},
                      'started_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}),
          flush=True)

    manager = GenerationManager(data, models_dir, manifest, 'qualification',
                                manifests=[manifest], total_memory_bytes=host_gb)
    state = manager.preflight(model.repo)
    if state['state'] != 'files_verified':
        print(f'model files are not verified: {state}', file=sys.stderr)
        return 2

    for pixels in arguments.dimensions:
        reference = reference_file(reference_dir, arguments.source_reference, pixels)
        for count in range(arguments.min_references, arguments.max_references + 1):
            run_job(manager, model, f'{arguments.label_prefix}{arguments.model} {count}x{pixels}',
                    [str(reference)] * count, pixels, receipt=receipt,
                    host_gb=arguments.host_gb)

    arguments.receipt.parent.mkdir(parents=True, exist_ok=True)
    with arguments.receipt.open('a', encoding='utf-8') as stream:
        for row in receipt:
            stream.write(json.dumps(row, sort_keys=True) + '\n')
    print(json.dumps({'receipt': str(arguments.receipt), 'rows': len(receipt)}), flush=True)
    return 0


def host_bytes(gigabytes: float | None) -> int | None:
    """The host size the worker should believe it has: ``None`` means this Mac's own."""
    return None if not gigabytes else int(gigabytes * 1024 ** 3)


def _mflux_version() -> str:
    try:
        import importlib.metadata
        return importlib.metadata.version('mflux')
    except Exception:  # noqa: BLE001 - a receipt field must never fail the run
        return 'unknown'


if __name__ == '__main__':
    raise SystemExit(main())
