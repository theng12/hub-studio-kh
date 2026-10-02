#!/usr/bin/env python3
"""Turn ``image_memory_probe.py`` rows and images into a table and a before/after page.

    <image-runtime>/bin/python qualification/image_memory_report.py <folder>

``<folder>`` holds ``rows.jsonl`` (one probe row per generation) and ``<model>_<config>_seed<N>.png``.
It writes ``table.md`` (config -> peak -> seconds, with the 8 GB projection) and ``index.html`` (the same
seeds and prompts through each configuration, side by side, with the pixel difference to ``current``) next to
them.  Nothing here measures anything: every number is read from a probe row, and a configuration with no
row is left out rather than estimated.
"""
from __future__ import annotations

import html
import json
from pathlib import Path
import statistics
import sys

import numpy as np
from PIL import Image

# What the Hub measured on the real 8 GB M1 (image-8gb-run1, Hub 0.6.126, guard bypassed) for the *current*
# configuration: the anchor the projection scales from.
M1_8GB = {'klein': {'seconds': 103.0, 'footprint_gb': 9.1, 'mlx_gb': 9.8, 'swap_written_gb_per_image': 3.88},
          'zimage': {'seconds': 349.0, 'footprint_gb': 5.8, 'mlx_gb': 5.7, 'swap_written_gb_per_image': 17.6}}
NAMES = {'klein': 'FLUX.2 Klein 4B 4-bit (scenes, 4 steps)', 'zimage': 'Z-Image-Turbo 4-bit (thumbnails, 9 steps)'}
ORDER = ['current', 'default', 'lean', 'bounded', 'streamed']
LABELS = {'current': 'before (what the worker did)', 'default': 'mflux default', 'lean': 'lean (16 GB tier)',
          'bounded': 'staged (8 GB tier, no streaming)', 'streamed': 'staged + streamed (8 GB tier)'}
# The owner's target for the Image worker on an 8 GB Mac: a peak footprint of about 4.5-5 GB, with 3-3.5 GB free before
# the model loads (image-8gb-run1 saw 3.65 GB free for Z-Image and 5.42 GB for Klein).
TARGET_GB, TARGET_MAX_GB = 4.5, 5.0


def load_rows(folder: Path) -> list[dict]:
    path = folder / 'rows.jsonl'
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def is_plain(row: dict) -> bool:
    """A comparison row: the default-seed, default-cache, single-run probe of a named configuration."""
    return row['run'] == 1 and not row['label'].split(' ', 2)[-1].startswith(('cache=', 'resident', 'fresh'))


def psnr(a: np.ndarray, b: np.ndarray) -> float:
    mse = float(((a.astype(float) - b.astype(float)) ** 2).mean())
    return float('inf') if mse == 0 else 10 * np.log10(255 ** 2 / mse)


def fmt(value, digits=1):
    return '-' if value is None else f'{value:.{digits}f}'


def table(rows: list[dict]) -> str:
    lines = ['| model | config | MLX peak GB | footprint peak GB | s/image (this Mac) | load avg | projected 8 GB: footprint GB | within the 4.5-5 GB target? | projected s/image |',
             '|---|---|---|---|---|---|---|---|---|']
    for model in ('klein', 'zimage'):
        anchor = M1_8GB[model]
        base = [r['generate_seconds'] for r in rows if r['model'] == model and r['config'] == 'current'
                and is_plain(r) and r.get('uncompiled_m1_path', True)]
        for config in ORDER:
            sel = [r for r in rows if r['model'] == model and r['config'] == config and is_plain(r)]
            if not sel:
                continue
            seconds = statistics.mean(r['generate_seconds'] for r in sel)
            mlx = max(r['mlx_peak_gb'] for r in sel)
            foot = max(r['footprint_peak_gb'] for r in sel)
            load = statistics.mean((r.get('loadavg_before', float('nan')) + r.get('loadavg_after', float('nan'))) / 2 for r in sel)
            projected_s = (anchor['seconds'] * seconds / statistics.mean(base)) if base and config in ('current', 'bounded', 'streamed') else None
            fits = 'yes' if foot <= TARGET_GB else ('borderline' if foot <= TARGET_MAX_GB else 'no')
            lines.append(f"| {NAMES[model].split(' (')[0]} | {LABELS[config]} | {mlx:.2f} | {foot:.2f} | {seconds:.0f} "
                         f"(n={len(sel)}) | {load:.0f} | {foot:.1f} | {fits} | {fmt(projected_s, 0)} |")
    sweep = [r for r in rows if r['label'].split(' ', 2)[-1].startswith('cache=')]
    if sweep:
        lines += ['', '**MLX cache cap (seed 42)**', '',
                  '| model, configuration | cache cap GB | MLX peak GB | footprint peak GB | s/image |', '|---|---|---|---|---|']
        for r in sorted(sweep, key=lambda r: (r['model'], r['config'], r['cache_gb'])):
            lines.append(f"| {NAMES[r['model']].split(' (')[0]}, {r['config']} | {r['cache_gb']} | {r['mlx_peak_gb']:.2f} | "
                         f"{r['footprint_peak_gb']:.2f} | {r['generate_seconds']:.0f} |")
    resident = [r for r in rows if 'resident' in r['label']]
    fresh = [r for r in rows if 'fresh process' in r['label']]
    if resident and fresh:
        later = [r['generate_seconds'] for r in resident if r['reused_model']]
        first = [r['generate_seconds'] for r in resident if not r['reused_model']] + [r['generate_seconds'] for r in fresh]
        lines += ['', '**Residency, FLUX.2 Klein 4-bit, lean (32 GB path)**', '',
                  f"| model loaded fresh (n={len(first)}) | model already resident (n={len(later)}) | saved per image |", '|---|---|---|',
                  f"| {statistics.mean(first):.1f} s | {statistics.mean(later):.1f} s | {statistics.mean(first) - statistics.mean(later):.1f} s |"]
    return '\n'.join(lines)


def page(folder: Path, rows: list[dict]) -> str:
    out = ['<!doctype html><meta charset="utf-8"><title>Image memory: before / after</title>',
           '<style>body{font:14px system-ui;margin:24px;max-width:1500px}img{width:100%;border:1px solid #888}'
           '.row{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px;margin:8px 0 28px}'
           'figure{margin:0}figcaption{font-size:12px;color:#555;padding:4px 0}table{border-collapse:collapse}'
           'td,th{border:1px solid #bbb;padding:3px 8px;text-align:right}th:first-child,td:first-child{text-align:left}'
           'code{background:#eee;padding:0 3px}</style>',
           '<h1>Image memory: same seeds, same prompts, before and after</h1>',
           '<p>Every image below was generated by <code>qualification/image_memory_probe.py</code> through the Image worker&rsquo;s own '
           'code path. &ldquo;before&rdquo; is the configuration the worker used until now; the others are the new memory-bounded '
           'configurations. The pixel difference is measured against &ldquo;before&rdquo; for the same seed.</p>',
           '<h2>Memory and time</h2>']
    md = table(rows)
    out.append('<pre>' + html.escape(md) + '</pre>')
    for model in ('klein', 'zimage'):
        out.append(f'<h2>{html.escape(NAMES[model])}</h2>')
        seeds = sorted({r['seed'] for r in rows if r['model'] == model and r['run'] == 1})
        for seed in seeds:
            figures, reference = [], None
            for config in ORDER:
                path = folder / f'{model}_{config}_seed{seed}.png'
                if not path.exists():
                    continue
                array = np.asarray(Image.open(path).convert('RGB'))
                if config == 'current':
                    reference = array
                note = ''
                if reference is not None and config != 'current':
                    diff = np.abs(array.astype(int) - reference.astype(int))
                    value = psnr(array, reference)
                    note = (f' &mdash; vs before: PSNR {"identical" if value == float("inf") else f"{value:.1f} dB"}, '
                            f'max {int(diff.max())}/255, {100 * float((diff > 0).mean()):.0f}% of pixels differ')
                figures.append(f'<figure><img loading="lazy" src="{path.name}"><figcaption>seed {seed}: '
                               f'{html.escape(LABELS[config])}{note}</figcaption></figure>')
            out.append('<div class="row">' + ''.join(figures) + '</div>')
    if (folder / 'first-pass-compiled' / 'index.html').exists():
        out.append('<h2>16 GB tier (lean) and the compiled path</h2><p>On a Mac that is not an M1/M2 (non-Max), mflux compiles the denoise function, so the 8 GB-tier '
                   'runs above (which use mflux&rsquo;s non-compiled path, as an 8 GB M1/M2 does) are not the right reference for the lean tier. '
                   '<a href="first-pass-compiled/index.html">The lean (16 GB) tier against its own compiled &ldquo;before&rdquo;</a> '
                   '(its &ldquo;projected 8 GB&rdquo; column is not meaningful for the lean tier).</p>')
    return '\n'.join(out)


def main() -> None:
    folder = Path(sys.argv[1])
    rows = load_rows(folder)
    (folder / 'table.md').write_text(table(rows) + '\n')
    (folder / 'index.html').write_text(page(folder, rows))
    print(table(rows))


if __name__ == '__main__':
    main()
