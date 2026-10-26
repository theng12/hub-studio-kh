#!/usr/bin/env python3
"""Per-stage memory of one image generation, through the Image worker's own code path.

    <image-runtime>/bin/python qualification/image_memory_probe.py --model klein --config bounded \
        --out /tmp/klein-bounded-42.png --json /tmp/rows.jsonl

It builds the mflux model exactly as ``GenerationManager._run`` does (``generation.model_class``,
the pinned pack, ``generation.apply_memory_plan``) and prints, for the load/encode/denoise/decode
stages, MLX's peak (reset between stages) and this process's physical footprint (what macOS swaps
on), plus seconds.  ``--config`` picks the memory plan:

  current  what the worker did before staging: the plan with no stage hooks and no bounded decode
           (Klein: no knobs at all; Z-Image: ``--low-ram`` with 512-pixel VAE tiles)
  default  mflux's unbounded default configuration (no cache cap, no hooks)
  lean     Klein's lean tier (2 GB cache cap, bounded VAE decode, model stays loadable)
  bounded  the staged configuration (encoder freed before the transformer loads, transformer before the decode)
  streamed bounded + the transformer/encoder blocks streamed one at a time from their files
  tier     whatever ``generation.memory_plan`` chooses for ``--host-gb`` (default 8)

``--cache-gb`` overrides the plan's MLX cache cap.  ``--repeat N`` runs N generations in one
process on one model object to measure residency (only for plans that keep the model loaded).
Same seeds and prompts as ``eight_gb_image_run.py``: Klein 42/43/44 at 1280x720, 4 steps; Z-Image 9 steps.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

THUMB_PROMPT = ('YouTube sleep-story cover art, golden magical theme. Large elegant gold serif title text reading exactly "GOLDEN SLUMBER" '
                'across the top. A moonlit enchanted forest with a winding path, glowing golden fireflies and drifting sparkles, deep '
                'midnight-blue and gold palette, soft dreamy haze, cinematic, highly detailed.')
SCENE_PROMPT = ('A quiet moonlit village at the edge of a pine forest, snow on the rooftops, warm light in a few windows, a winding lane '
                'leading to a small stone bridge over a frozen stream, deep blue night sky full of stars, soft painterly storybook '
                'illustration, calm and cozy, wide cinematic composition.')
SCENES = {'klein': ('flux2-klein-4b-mlx-4bit', SCENE_PROMPT, 4), 'zimage': ('z-image-turbo-mflux-4bit', THUMB_PROMPT, 9),
          # Qwen Image 2.1 Turbo: eight steps is its row's fixed count (`limits`), so `--steps` cannot
          # change it - the wrapper refuses anything else.
          'qwenz': ('qwen-image-2-1-turbo-mflux-q4', SCENE_PROMPT, 8)}
GB = 1e9


class _Usage(ctypes.Structure):
    _fields_ = [('uuid', ctypes.c_uint8 * 16), ('user', ctypes.c_uint64), ('system', ctypes.c_uint64),
                ('idle_wakeups', ctypes.c_uint64), ('interrupt_wakeups', ctypes.c_uint64),
                ('pageins', ctypes.c_uint64), ('wired', ctypes.c_uint64), ('resident', ctypes.c_uint64),
                ('footprint', ctypes.c_uint64), ('pad', ctypes.c_uint64 * 40)]


def footprint() -> int:
    """This process's physical footprint, the number ``footprint(1)`` and the memory-pressure logic use."""
    usage = _Usage()
    ctypes.CDLL('/usr/lib/libproc.dylib').proc_pid_rusage(os.getpid(), 4, ctypes.byref(usage))
    return usage.footprint


class Sampler(threading.Thread):
    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.peak, self.stop = 0, False

    def run(self) -> None:
        while not self.stop:
            self.peak = max(self.peak, footprint())
            time.sleep(0.02)

    def reset(self) -> None:
        self.peak = footprint()


def find_pack(package_id: str) -> Path:
    """The installed pack's ``model`` folder: ~/StudioHubModels first, then a kit drive."""
    bases = [Path.home() / 'StudioHubModels', *sorted(Path('/Volumes').glob('KH-STUDIO-*/model-library')),
             # A kit drive's library sits one level deeper (`<drive>/Video Generation Kit/Studio Hub
             # KH/model-library`); the shallow glob above found nothing on this Mac, so the probe
             # reported "no installed pack" for packs that were installed.
             *sorted(Path('/Volumes').glob('KH-STUDIO-*/*/*/model-library'))]
    for base in bases:
        for pack in sorted((base / package_id).glob('*/model')):
            return pack
    raise SystemExit(f'no installed pack for {package_id}')


def build_plan(generation, model, config: str, host_gb: float, cache_gb: float | None) -> dict:
    plan = generation.memory_plan(model, int(host_gb * 1024 ** 3), 'auto')
    if config == 'tier':
        pass
    else:
        if config == 'current':
            # Before staging: Klein loaded with no knobs, Z-Image with --low-ram and 512-pixel tiles.
            zimage = model is generation.Z_IMAGE_TURBO
            plan.update(low_ram=zimage, vae_tiling=zimage, staged=False, lean=False, bounded_vae_decode=False,
                        resident_ok=not zimage, mlx_cache_limit_gb=1.0 if zimage else None,
                        mode='low-ram' if zimage else 'default')
        elif config == 'default':
            plan.update(low_ram=False, vae_tiling=False, staged=False, lean=False, bounded_vae_decode=False,
                        resident_ok=True, mlx_cache_limit_gb=None, mode='default')
        elif config == 'lean' and model.memory.lean_below_bytes:
            plan.update(low_ram=False, vae_tiling=False, staged=False, lean=True,
                        bounded_vae_decode=model.memory.bounded_vae_decode, resident_ok=True,
                        mlx_cache_limit_gb=model.memory.lean_cache_limit_gb, mode='default')
        elif config in ('bounded', 'streamed'):
            plan.update(low_ram=True, vae_tiling=not model.memory.bounded_vae_decode, staged=True, lean=False,
                        bounded_vae_decode=model.memory.bounded_vae_decode, resident_ok=False,
                        mlx_cache_limit_gb=model.memory.low_ram_cache_limit_gb or 1.0, mode='low-ram',
                        stream=tuple(model.memory.streamed_components) if config == 'streamed' else ())
        else:
            raise SystemExit(f'config {config!r} does not apply to this model')
    if cache_gb is not None:
        plan['mlx_cache_limit_gb'] = cache_gb
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--model', choices=sorted(SCENES), required=True)
    parser.add_argument('--config', default='tier', choices=['current', 'default', 'lean', 'bounded', 'streamed', 'tier'])
    parser.add_argument('--host-gb', type=float, default=8)
    parser.add_argument('--cache-gb', type=float)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--width', type=int, default=1280)
    parser.add_argument('--height', type=int, default=720)
    parser.add_argument('--steps', type=int)
    parser.add_argument('--repeat', type=int, default=1)
    parser.add_argument('--out', help='PNG path; with --repeat, _N is added before the extension')
    parser.add_argument('--json', help='append one JSON row per generation to this file')
    parser.add_argument('--label', default='')
    parser.add_argument('--m1', action=argparse.BooleanOptionalAction, default=None,
                        help="run mflux's non-compiled denoise function, which mflux itself chooses on M1/M2 "
                             '(non-Max) Macs - the 8 GB tier - and not on a Max/Ultra or M3+; default: on when --host-gb < 16')
    args = parser.parse_args()

    from components.image.app.backend import generation
    package, prompt, steps = SCENES[args.model]
    steps = args.steps or steps
    model = next(m for m in generation.MODELS.values() if m.package_id == package)
    pack = find_pack(package)
    plan = build_plan(generation, model, args.config, args.host_gb, args.cache_gb)
    emulate_m1 = args.host_gb < 16 if args.m1 is None else args.m1
    if emulate_m1:
        from mflux.utils.apple_silicon import AppleSiliconUtil
        AppleSiliconUtil.is_m1_or_m2 = staticmethod(lambda: True)

    import mlx.core as mx
    if args.host_gb < 16:
        # MLX sizes its memory limit - and so its buffer cache - from the Mac it runs on (about 1.5x the
        # recommended working set: ~8.5 GB on an 8 GB Mac). On this bigger Mac the cache of an uncapped
        # configuration would grow to ~40 GB, which says nothing about an 8 GB Mac, so emulate its limits.
        # A configuration that sets its own cache cap (every bounded one) overrides this.
        mx.set_memory_limit(int(8.5 * GB))
        mx.set_cache_limit(int(8.5 * GB))
    sampler = Sampler()
    sampler.start()
    stages: list[dict] = []
    clock = {'t': time.time()}

    def mark(name: str) -> None:
        now = time.time()
        stages.append({'stage': name, 'seconds': round(now - clock['t'], 2),
                       'mlx_peak_gb': round(mx.get_peak_memory() / GB, 2),
                       'footprint_peak_gb': round(sampler.peak / GB, 2),
                       'mlx_active_gb': round(mx.get_active_memory() / GB, 2),
                       'mlx_cache_gb': round(mx.get_cache_memory() / GB, 2)})
        mx.reset_peak_memory()
        sampler.reset()
        clock['t'] = time.time()

    instance, hooks = None, []
    for run in range(args.repeat):
        stages.clear()
        generation.start_mlx_measurement()
        sampler.reset()
        clock['t'] = time.time()
        reused = instance is not None
        if not reused:
            instance = generation.model_class(model)(model_path=str(pack), quantize=None, lora_paths=None,
                                                      lora_scales=None)
            applied = dict(plan)
            generation.apply_memory_plan(instance, applied, on_stage=mark, model_dir=pack)
            hooks = applied.get('hooks')
        mark('load')
        started = time.time()
        load_before = os.getloadavg()[0]
        # The two stage names the hooks report ('encoded', 'denoised'); 'decode' closes the job.
        result = instance.generate_image(seed=args.seed + run, prompt=prompt, num_inference_steps=steps,
                                         height=args.height, width=args.width, guidance=1.0)
        mark('decode+finish')
        seconds = round(time.time() - started, 1)
        if args.out:
            path = Path(args.out)
            if args.repeat > 1:
                path = path.with_name(f'{path.stem}_{run + 1}{path.suffix}')
            result.save(str(path), overwrite=True)
        row = {'label': args.label, 'model': args.model, 'config': args.config, 'host_gb': args.host_gb,
               'cache_gb': plan['mlx_cache_limit_gb'], 'uncompiled_m1_path': emulate_m1, 'seed': args.seed + run, 'run': run + 1,
               'reused_model': reused, 'loadavg_before': round(load_before, 1), 'loadavg_after': round(os.getloadavg()[0], 1), 'hooks': hooks, 'generate_seconds': seconds,
               'mlx_peak_gb': max(s['mlx_peak_gb'] for s in stages),
               'footprint_peak_gb': max(s['footprint_peak_gb'] for s in stages), 'stages': list(stages)}
        print(json.dumps(row), flush=True)
        if args.json:
            with open(args.json, 'a') as handle:
                handle.write(json.dumps(row) + '\n')
        if not plan['resident_ok'] or args.repeat == 1:
            instance.callbacks = None
            instance = None
            generation.release_mlx_memory()
    sampler.stop = True


if __name__ == '__main__':
    main()
