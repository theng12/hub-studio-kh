"""The Image worker's model registry: one dispatch table for supported Image engines.

Every model this worker can run is described once, in :data:`MODELS`: which engine and class
load it, its own generation defaults and limits, its family/label, and the memory policy
that decides which memory controls this host needs.  :func:`validate_request`, the job
runner and ``/api/catalog`` all read that table, so adding a model is one entry plus its
manifest.

FLUX.2 Klein's entry keeps the behaviour the worker shipped before this table existed (4 steps,
guidance 1.0, the same family/label/limits) and, from 24 GB up, the same configuration; below that
it runs in the memory-bounded configurations the policy below describes (``staging``,
``streaming``, ``vae_bounded``), which change no arithmetic of the sampling loop.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import gc
import importlib
import json
import math
import os
from pathlib import Path
import queue
import random
import resource
import subprocess
import threading
import time
import traceback
from uuid import uuid4

from PIL import Image

from components import model_defaults

from . import loading_mode
from .loading_mode import DEFAULT_MODE as DEFAULT_LOADING_MODE
from .loading_mode import MODE_AUTO, MODE_FULL, MODE_LOW
from .loading_mode import MODES as LOADING_MODES


# Where a model's weights live inside an installed package folder.
#
# One rule for every model, and deliberately not a per-model field.  A package is installed
# as ``<library>/<package_id>/<revision>/model/`` whatever the publisher's own layout was:
# Z-Image-Turbo's pack root *is* its model directory, so its manifest carries
# ``source_path='transformer/...'`` against ``path='model/transformer/...'`` and
# ``components/model_packages.py:_file_path`` maps that on import.  The worker therefore has
# exactly one load path, and nothing downstream has to know which publisher used which shape.
WEIGHTS_SUBDIRECTORY = 'model'

# Optional request field ``output_format``. Absent = the PNG mflux wrote (today's behaviour).
# ``jpeg`` is one fixed, measured preset (2026-10-01 compression plan: 1280x720 FLUX.2 Klein
# 1,737 KB PNG -> 263 KB, PSNR 37.6 dB): RGB, quality 90, 4:2:0, progressive, optimised, no EXIF.
OUTPUT_FORMATS = ('png', 'jpeg')
JPEG_QUALITY = 90
MEDIA_TYPE_BY_FORMAT = {'png': 'image/png', 'jpeg': 'image/jpeg'}
EXTENSION_BY_FORMAT = {'png': 'png', 'jpeg': 'jpg'}
MEDIA_TYPE_BY_SUFFIX = {'.png': 'image/png', '.jpg': 'image/jpeg'}
_DELIVERED_SUFFIXES = tuple(MEDIA_TYPE_BY_SUFFIX)

# The engine this worker serves. It is compared against a manifest's own ``engine`` field in
# exactly one place - ``load_image_manifests`` - which is how a bundled manifest belonging to
# another studio is recognised and reported instead of being loaded. Named once so the fact has
# one home in the component; the Hub's engine table is what routes a manifest here in the first
# place (``components/hub/standalone.py:_ENGINE_ROUTE``).
IMAGE_ENGINE = 'image-mflux'
IMAGE_ENGINES = (IMAGE_ENGINE, 'image-mlx-serve')

SIZE_PRESETS = {
    '16:9': (1280, 720), '9:16': (720, 1280), '1:1': (1024, 1024),
    # 1024x576 is *exactly* 16:9 as well, so it cannot reuse the '16:9' key: the native app
    # builds one row per entry with `id = aspectRatio`, and two rows sharing an id is a
    # duplicate identity in a `ForEach`. The key therefore names the size, not the ratio.
    # **Why it exists:** 1280x720 is not a valid canvas for the video lane (720 is not a
    # multiple of 32), so a still destined to be animated had to be cropped or resized
    # between the two lanes. This is the same 16:9 frame at a video-valid size, offered as
    # an ADDITION, not a replacement -- the default stays 1280x720, because most image jobs
    # are standalone stills that never touch video.
    '16:9 small': (1024, 576),
    '4:3': (1152, 864), '3:4': (864, 1152), '3:2': (1200, 800),
    '2:3': (800, 1200), '5:4': (1120, 896), '4:5': (896, 1120),
    '21:9': (1680, 720), '2:1': (1408, 704), '1:2': (704, 1408),
}
QWEN_SIZE_PRESETS = {
    '16:9': (1280, 720), '9:16': (720, 1280), '1:1': (1024, 1024),
}
TERMINAL = {'done', 'error', 'cancelled'}

# The fleet tier the Image Studio qualifies models at, and the floor below which a host is
# told it is under-qualified.  It is a warning threshold, never a dispatch refusal: the Hub's
# admission governor owns the live decision (see ``components/hub/backend/memory_admission.py``).
QUALIFIED_MINIMUM_MEMORY_BYTES = 16_000_000_000

# Z-Image-Turbo: run the bounded low-RAM configuration on every tier below the one where the
# default was measured.
#
# [M] On this project's 32 GiB M2 Max at 1280x720, mflux 0.20.0 + the 4-bit pack measure a
# 17.67 GB peak footprint by default and 7.12 GB with ``--low-ram``.  The default number is
# partly a property of the host: MLX's buffer cache grows into whatever RAM is free, which is
# why FLUX.2 Klein measures ~19-24 GB here and still runs on 16 GB Macs.
#
# What the worker can rely on is the bounded side: low-RAM's 7.1-7.2 GB fits a 16 GB Mac with
# the OS, the Hub and the app resident.  Whether the *default* also fits 16 GB is unmeasured,
# so on tiers below the measured one this worker chooses the configuration with the known
# bound and pays the measured wall-time cost (166 s -> 236 s at 1280x720 on the same host).
# 24 GB and up keep the default.  **The 8 GB tier is now measured** (owner run, 2026-09-26, Apple
# M1 Mac mini `Macmini9,1`, macOS 26.6.2, mflux 0.20.0): low-RAM completed 1024x1024 in 392 s and
# 1280x720 in 336 s, 5.70 GB peak MLX / 5.85 GB footprint, with 1.3-1.9 GB of swap.
#
# That run also tried the *default* configuration on the same Mac and it completed too — which
# looks like this rule is unnecessary, and is not. It finished with an **8.72 GB footprint on an
# 8.0 GB machine** and **3443 MB peak swap**, roughly 2.7x the low-RAM runs: it fit by paging, not
# by fitting. The boundary stays where it is. If a future measurement shows low-RAM needs
# 256-pixel VAE tiles on some host, ``low_ram_vae_tile_size`` below is the single knob to set, and
# this constant is the single tier boundary to move.
Z_IMAGE_LOW_RAM_BELOW_BYTES = 24_000_000_000

# FLUX.2 Klein 4-bit: the bounded (staged) configuration below 12 GB - the 8 GB tier - and a lean
# one (cache capped, VAE decode bounded, model still fully loadable and resident) below 24 GB.
# Its default configuration peaks at 12.1 GB of MLX memory on a 32 GB Mac and 9.8 GB on the 8 GB
# M1 (3.7-4.1 GB of swap per image), 9 GB of which is the VAE decode of a 0.17 GB decoder.
# The bounded configurations cap MLX's buffer cache at 0.5 GB (mflux's own --low-ram uses 1 GB). Measured on the
# streamed 8 GB-tier configuration: FLUX.2 Klein 4-bit 2.3-2.9 GB footprint at 0.25-1 GB with no change in time
# (79-82 s at 0.25-2 GB, staged), Z-Image-Turbo 4-bit 2.0-4.1 GB.
BOUNDED_CACHE_LIMIT_GB = 0.5
STREAM_BELOW_BYTES = 12_000_000_000   # block streaming (`streaming`): the 8 GB tier only
FLUX2_LOW_RAM_BELOW_BYTES = 12_000_000_000
FLUX2_LEAN_BELOW_BYTES = 24_000_000_000


@dataclass(frozen=True)
class MemoryPolicy:
    """The mflux memory knobs one model uses, and when.

    ``low_ram_below_bytes=None`` means this model needs none of them: it is loaded exactly
    the way it always was, and it has one loading mode.  ``single_mode_reason`` is then the
    model's own explanation of that fact, published as the catalog's ``loading_modes.reason``
    so the app can explain the control it is not offering instead of implying the
    machine-wide ``loading_mode`` setting applies.
    """
    low_ram_below_bytes: int | None = None
    # mflux's ``--low-ram`` implies ``--mlx-cache-limit-gb 1``; kept as a field so the two can
    # be separated the way the CLI separates them.
    low_ram_cache_limit_gb: float | None = 1.0
    # ``--low-ram`` already installs mflux's default tiling (512-pixel tiles).  An explicit
    # smaller tile is the extra lever for the 8 GB tier, which is unmeasured.
    low_ram_vae_tile_size: int | None = None
    # Why this model declares no bounded configuration, in the model's own terms.
    single_mode_reason: str | None = None
    # The bounded configuration also runs the stage hooks (`staging.apply`): the prompt is encoded
    # and its encoder freed before the transformer loads, and the transformer is freed before the
    # VAE decodes. They change no arithmetic; they only stop three components being resident at
    # once. `bounded_vae_decode` additionally replaces the FLUX.2 VAE's decode (peak 9 GB at
    # 1280x720) with `vae_bounded.decode`, which cannot be done by tiling on that decoder.
    bounded_vae_decode: bool = False
    # A "lean" configuration for the tier above the bounded one: the model stays fully loaded (so it
    # can stay resident between jobs) but MLX's buffer cache is capped and the VAE decode is bounded.
    # Below this host memory and at or above `low_ram_below_bytes`. None: no lean tier.
    lean_below_bytes: int | None = None
    lean_cache_limit_gb: float | None = 2.0
    # Stream the blocks of these components one at a time (`streaming`): ``(component, block lists,
    # multi_pass)``, with ``weight_definition`` ('module:Class') naming mflux's loader for the model. Applies in
    # the bounded configuration below `stream_below_bytes`; None streams nothing.
    stream_below_bytes: int | None = None
    streamed_components: tuple = ()
    weight_definition: str | None = None


@dataclass(frozen=True)
class ImageModel:
    """One registered image model: how to load it, what it defaults to, how it must fit."""
    repo: str
    revision: str
    package_id: str
    label: str
    family: str
    model_class: str                  # '<import path>:<ClassName>'
    defaults: dict
    limits: dict
    memory: MemoryPolicy
    qualification: dict
    engine: str = IMAGE_ENGINE
    size_presets: dict | None = None
    minimum_macos: tuple[int, int] = (14, 0)
    resident_allowed: bool = True
    # The fleet tier this model is qualified at. Published per model, because a tier is a
    # property of the model and its memory policy, not of the Studio.
    qualified_minimum_memory_gb: float = 16.0
    # Live-free-memory floor for Hub dispatch. This is the one admission floor the Hub
    # actually enforces (`broker._memory_gate` -> `memory_admission.describe` ->
    # `workload_policy.required_free_memory_gb`, which reads this key); without it the Hub's
    # 2 GB default applies. None keeps whatever default the Hub has today.
    min_free_memory_gb: float | None = None
    # True where mflux accepts a guidance value and then ignores it (CFG disabled).  Published
    # in the catalog so the app can explain the control instead of pretending it works.
    guidance_ignored: bool = False
    # Warn a host below the qualified tier.  ``{memory_gb}`` is formatted by the worker.
    below_tier_warning: str | None = None
    # What one item of this model actually costs, where it has been measured. Published as the
    # lane contract's `runtime_seconds`; absent means unmeasured, and an unmeasured model must
    # publish nothing rather than an estimate - a guessed runtime is how a scheduler sets a
    # deadline that fails on use.
    measured_runtime: dict | None = None


FLUX2_KLEIN = ImageModel(
    repo='AITRADER/FLUX2-klein-4B-mlx-4bit',
    revision='7fd24828501390b67a92c8b66d2fc5a707d0ba1a',
    package_id='flux2-klein-4b-mlx-4bit',
    label='FLUX.2 Klein 4B MLX 4-bit',
    family='flux2-klein',
    model_class='mflux.models.flux2.variants.txt2img.flux2_klein:Flux2Klein',
    defaults={'aspect_ratio': '16:9', 'resolution': '1K', 'steps': 4,
              'guidance': 1.0, 'seed': -1},
    limits={'min_steps': 2, 'max_steps': 100, 'min_guidance': 0.0, 'max_guidance': 30.0,
            'min_seed': -1, 'max_seed': 2**32 - 1, 'max_prompt': 4000},
    # Free memory needed before start; owner chose 2.8 GB on 2026-10-02 for headroom - measured 8 GB M1+M2 runs started cleanly from 3.76 GB free
    # (3.76-4.14 GB free; peak 2.16-2.68 GB; ~0 swap). This is the free memory a job needs to begin, NOT its peak. The
    # Hub's fleet default (`memory_admission._FLEET_DEFAULTS`) carries the same 2.8, because it outranks this row.
    # The bounded configurations (tiers in `FLUX2_*_BELOW_BYTES` above) were added after the 8 GB runs showed the
    # unbounded one at 9.8 GB of MLX memory and ~4 GB of swap per image; 24 GB and up are unchanged.
    min_free_memory_gb=2.8,
    memory=MemoryPolicy(low_ram_below_bytes=FLUX2_LOW_RAM_BELOW_BYTES,
                        low_ram_cache_limit_gb=BOUNDED_CACHE_LIMIT_GB, bounded_vae_decode=True,
                        lean_below_bytes=FLUX2_LEAN_BELOW_BYTES, lean_cache_limit_gb=2.0,
                        stream_below_bytes=STREAM_BELOW_BYTES,
                        weight_definition=('mflux.models.flux2.weights.flux2_weight_definition:'
                                           'Flux2KleinWeightDefinition'),
                        streamed_components=(('text_encoder', ('layers',), False),
                                             ('transformer', ('transformer_blocks',
                                                              'single_transformer_blocks'), True))),
    qualification={
        # Per-stage memory, 1280x720, four steps, seeds 42-44, measured by `qualification/image_memory_probe.py`
        # on a 32 GiB M2 Max with an 8 GB Mac's MLX limits and mflux's non-compiled (M1/M2) denoise function;
        # the "before" row reproduced the real 8 GB M1 run (9.8 GB MLX there). `source` holds every figure.
        'memory_profile': {
            'before': {'mlx_peak_gb': 9.79, 'footprint_peak_gb': 9.41},
            'staged_streamed': {'mlx_peak_gb': 1.64, 'footprint_peak_gb': 2.93},
            'measured_on': '32 GiB M2 Max (Mac14,13) emulating an 8 GB Mac, mflux 0.20.0',
            'source': 'qualification/evidence/image-memory-2026-10-01.jsonl',
        },
        'historical_source_workload': '1K, four steps, guidance 1.0 on 16 GB Apple Silicon',
        # This said `pending` while a completed packaged run sat in the tree: one real 1K
        # generation driven through the assembled Hub + Image worker + native UI, job `af4913b2`,
        # recorded 2026-09-15 in `qualification/st03-image-real-inference.md` after being found in
        # a worktree's gitignored `.build/` during the record reconciliation. The record is not a
        # plan; it is the receipt.
        'packaged_inference': (
            'proven: one real 1K generation through the assembled Hub + Image worker + native UI, '
            'job af4913b2, 2026-09-15 (qualification/st03-image-real-inference.md)'),
    },
    # Measured in `qualification`'s own terms: this engine, this scene. `measured` is always a list
    # of rows — one per configuration that was run — so a lane whose cost depends on a request
    # parameter (music's duration, image's memory mode) publishes the same shape as one whose cost
    # does not, and each row names the record its number came from.
    #
    # This row published `79.34 s` from the beginning with no artefact behind it. The stored run of
    # **this exact scene** — same model, same pinned revision, 1280x720, 4 steps, guidance 1.0,
    # seed 42 — is `105.943 s`, in `qualification/st03-image-real-inference.md` and again as
    # `worker_terminal.duration_seconds` in `evidence/st03-image-real-inference.jsonl`. Two records
    # agree; the published number matched neither and appeared in no file in this repository. The
    # number is the record's now, and `tests/test_image_loading_mode.py` reads the row's own
    # `source` and fails if it cannot find the figure there — which is what would have caught this
    # on the day it was written.
    #
    # The old `measured_on` named a 32 GiB M2 Max and mflux 0.20.0, and the record states neither:
    # it says **"Captured on Apple M4, 16 GiB RAM, macOS 26.6.2"**, and the run predates mflux
    # 0.20.0, naming an image-runtime tree SHA instead. So the wrong number came with a wrong host,
    # and the host it should have named is *better news*: this is a 16 GB measurement of the scene
    # the row describes, on the tier the fleet actually runs, not a 32 GB one. A second packaged run
    # in the same record reports 107.73 s, which corroborates ~106 s; it is not published as a row
    # because that table does not state its resolution or steps.
    measured_runtime={
        'measured': [
            {'seconds': 105.94, 'steps': 4,
             'scene': '1280x720, 4 steps, guidance 1.0, seed 42',
             'source': ('qualification/st03-image-real-inference.md, "Duration"; '
                        'qualification/evidence/st03-image-real-inference.jsonl, '
                        'worker_terminal.duration_seconds')},
        ],
        'measured_on': ('Apple M4, 16 GiB, macOS 26.6.2; packaged app at 4377237e, image runtime '
                        'tree c9cd56a6'),
        'evidence': 'measured',
    },
    below_tier_warning=(
        'This Mac has {memory_gb:.0f} GB of unified memory. FLUX is qualified on 16 GB and has '
        'run on 8 GB in production, but Image work here is below the qualified tier and may be '
        'slow or fail if other Studios are holding models.'),
)

Z_IMAGE_TURBO = ImageModel(
    repo='filipstrand/Z-Image-Turbo-mflux-4bit',
    revision='b3a8f31115a11f2f9e2fa0bfbc8d78dcc3e6568b',
    package_id='z-image-turbo-mflux-4bit',
    label='Z-Image-Turbo 6B MLX 4-bit',
    family='z-image-turbo',
    model_class='mflux.models.z_image.variants.z_image:ZImage',
    # Nine steps is this model's own default in mflux 0.20.0 (``ui_defaults
    # .model_inference_steps('z-image-turbo')``); it is guidance-distilled, so the guidance
    # number is carried through to ``generate_image`` exactly as ``--guidance`` is and then
    # ignored by the model (``z_image.py`` forces 0.0 when ``supports_guidance`` is False).
    defaults={'aspect_ratio': '16:9', 'resolution': '1K', 'steps': 9,
              'guidance': 1.0, 'seed': -1},
    limits={'min_steps': 2, 'max_steps': 100, 'min_guidance': 0.0, 'max_guidance': 30.0,
            'min_seed': -1, 'max_seed': 2**32 - 1, 'max_prompt': 4000},
    memory=MemoryPolicy(low_ram_below_bytes=Z_IMAGE_LOW_RAM_BELOW_BYTES,
                        low_ram_cache_limit_gb=BOUNDED_CACHE_LIMIT_GB,
                        stream_below_bytes=STREAM_BELOW_BYTES,
                        weight_definition=('mflux.models.z_image.weights.z_image_weight_definition:'
                                           'ZImageWeightDefinition'),
                        streamed_components=(('text_encoder', ('layers',), False),
                                             ('transformer', ('noise_refiner', 'context_refiner',
                                                              'layers'), True))),
    # The Hub's live-free floor. It stops a job being started into a nearly-full Mac, which is
    # what turns a slow generation into a killed worker — and nothing more than that.
    #
    # It was 8.0, taken from the bounded low-RAM footprint (7.12-7.24 GB measured on a 32 GiB
    # M2 Max). That was a mistake worth naming: a *footprint* is not a *free-memory*
    # requirement, and on an 8 GB Mac — the very tier this model has no measurement for — a
    # floor of 8 GB can never be met. Worse, the Hub answers an unmet free floor with "wait",
    # not "skip" (broker._memory_gate), so the job would have waited for memory that was never
    # going to arrive rather than failing or running. The floor is now a guard against starting
    # into a full machine — a little above FLUX's 2.0 because this model's weights are 28 %
    # larger — and it leaves an 8 GB Mac free to attempt the model in low-RAM mode, which is
    # exactly the measurement that is outstanding.
    #
    # What decides "this machine class is unqualified" is the published *tier*
    # (min_unified_memory_gb = 16): reported to the operator with a below-tier warning; the Hub's
    # requirement (measured, else manifest, else this tier) is what gates dispatch, since 2026-09-30.
    # Free memory needed before start; owner chose 2.8 GB on 2026-10-02 for headroom - measured 8 GB M1+M2 runs started
    # cleanly from 3.53 GB free (3.53-4.24 GB free; peak 2.77-2.97 GB; ~0 swap) - NOT the peak.
    min_free_memory_gb=2.8,
    qualification={
        'memory_profile': {
            'before': {'mlx_peak_gb': 6.26, 'footprint_peak_gb': 7.46},
            'staged_streamed': {'mlx_peak_gb': 2.79, 'footprint_peak_gb': 4.13},
            'measured_on': '32 GiB M2 Max (Mac14,13) emulating an 8 GB Mac, mflux 0.20.0',
            'source': 'qualification/evidence/image-memory-2026-10-01.jsonl',
        },
        'measured_on': '32 GiB M2 Max (Mac14,13), mflux 0.20.0, 4-bit pack',
        'measured_resolutions': ['1280x720', '1024x1024'],
        'measured_peak_gb': {'default': 17.67, 'low_ram': 7.12},
        'fleet_16gb': ('low-RAM mode is selected from this Mac\'s memory so the peak stays at '
                       'the measured 7.1-7.2 GB bound; the default configuration has not been '
                       'measured on 16 GB'),
        'fleet_8gb': 'pending: this tier is being measured by the owner and is not claimed here',
        'packaged_inference': 'pending',
    },
    # Two settings of the same model, measured the same way. Both are published because the
    # memory mode changes the answer by 42 % and a scheduler reading one number would set the
    # wrong deadline for whichever mode this host selects.
    #
    # Both figures are the ones the qualification record states, and each names it. The registry
    # published 185.61 s for the default configuration from 0.6.61 until 0.6.63, in a row whose
    # `scene` named the record's own scene; the record says 166 s, the constant above says 166 s,
    # `README-8gb-z-image.md` says 166-236 s and the 0.6.60 notes say 166 s / 236 s. Nothing in
    # the tree stored the 185.61 s figure, so the registry was the only place it existed - and a
    # number with no artefact behind it, disagreeing with the artefact that does exist, is the
    # thing this project refuses to publish. Where a stored record is found for a third run, it
    # belongs here as its own row with its own `source`, not as an edit to these.
    measured_runtime={
        'measured': [
            {'seconds': 166, 'steps': 9, 'memory_mode': 'default',
             'scene': '1280x720, 9 steps, seed 42',
             'source': 'qualification/z-image-turbo-2026-09-24.md, "Generation time [M]"'},
            {'seconds': 236, 'steps': 9, 'memory_mode': 'low_ram',
             'scene': '1280x720, 9 steps, seed 42',
             'source': 'qualification/z-image-turbo-2026-09-24.md, "Generation time [M]"'},
        ],
        'measured_on': '32 GiB M2 Max, mflux 0.20.0, 4-bit pack',
        'evidence': 'measured',
    },
    guidance_ignored=True,
    below_tier_warning=(
        'This Mac has {memory_gb:.0f} GB of unified memory. Z-Image-Turbo is qualified at '
        '16 GB and above in its bounded low-RAM configuration; the 8 GB tier is still being '
        'measured, so Image work here is below the qualified tier and may be slow or fail if '
        'other Studios are holding models.'),
)

QWEN_IMAGE_21 = ImageModel(
    repo='ddalcu/Qwen-Image-2.1-MLX-Serve-4bit',
    revision='88eb1b3bb5591ed59b68a6e1a1c2d9baade73e38',
    package_id='qwen-image-2-1-mlx-serve-4bit',
    label='Qwen Image 2.1',
    family='qwen-image-2.1',
    model_class='',
    defaults={'aspect_ratio': '1:1', 'resolution': '1K', 'steps': 16,
              'guidance': 1.0, 'seed': -1},
    limits={'min_steps': 1, 'max_steps': 50, 'min_guidance': 0.0, 'max_guidance': 20.0,
            'min_seed': -1, 'max_seed': 2**31 - 1, 'max_prompt': 4000},
    memory=MemoryPolicy(single_mode_reason=(
        'The native Qwen runtime has one bounded packaged configuration.')),
    qualification={
        'measured_on': '24 GiB Apple M4 (packaged, Hub 0.6.152); earlier 32 GiB M2 Max standalone, mlx-serve 26.10.1, pinned 4-bit pack',
        'measured_resolutions': ['1024x1024', '1280x720', '720x1280'],
        'measured_peak_gb': {'default': 9.787},
        'fleet_24gb': ('measured 2026-10-08 on the owner\'s physical 24 GiB M4: four 16-step cases, '
                       '440.6-500.3 s, worker-tree peak 9.235-9.787 GB, normal pressure, zero swap growth; '
                       'qualification/evidence/qwen-image-21-20261008-24gb/measurement.json'),
        'fleet_16gb': 'unsupported by this normal pack; use the qualified low-RAM package',
        'packaged_inference': ('proven on 24 GiB M4: four 1K-class cases; '
                               'qualification/evidence/qwen-image-21-20261008-24gb/measurement.json'),
    },
    engine='image-mlx-serve',
    size_presets=QWEN_SIZE_PRESETS,
    minimum_macos=(26, 2),
    resident_allowed=False,
    qualified_minimum_memory_gb=24.0,
    # ponytail: measured peak demand (10.35 GB drop in available memory, 24 GiB M4, 2026-10-08)
    # + 2 GB, not the lowest recorded start (19.01 GB). Ceiling: no run started below 19.01 GB;
    # raise it if a lower-headroom start shows pressure or swap. Same rule as the low-RAM package.
    min_free_memory_gb=12.5,
    below_tier_warning=(
        'This Mac has {memory_gb:.0f} GB of unified memory. Qwen Image 2.1 is qualified on '
        '24 GB and above.'),
)

QWEN_IMAGE_21_LOW_RAM = ImageModel(
    repo='mlx-community/Qwen-Image-2.1-mflux-q4',
    revision='746a58556820933a2df5c75887a2570f1ad200c0',
    package_id='qwen-image-2-1-mflux-q4',
    label='Qwen Image 2.1 Low-RAM 4-bit',
    family='qwen-image-2.1',
    model_class='components.image.app.backend.qwen_lowram:QwenLowRam',
    defaults={'aspect_ratio': '1:1', 'resolution': '1K', 'steps': 16,
              'guidance': 1.0, 'seed': -1},
    limits={'min_steps': 1, 'max_steps': 50, 'min_guidance': 0.0, 'max_guidance': 20.0,
            'min_seed': -1, 'max_seed': 2**32 - 1, 'max_prompt': 4000},
    memory=MemoryPolicy(single_mode_reason=(
        'This candidate always uses prompt materialization, cache zero, tiled VAE and eviction.')),
    qualification={
        'fleet_16gb': ('qualified by the owner\'s decision of 2026-10-08 without a physical 16 GB '
                       'receipt; evidence is the 32 GiB M2 Max run below'),
        'fleet_8gb': 'not qualified',
        'packaged_inference': 'proven on 32 GiB M2 Max: four 1K cases, normal pressure, zero swap growth; qualification/evidence/qwen-image-21-20261006/measurement.json',
        'measured_peak_gb': {'default': 5.853},
        'min_free_memory_note': 'Measured peak 5.853 GB + 2 GB, rounded up to 0.5 GB (32 GiB M2 Max, 2026-10-06; every measured start had 14.697 GB or more); qualification/evidence/qwen-image-21-20261006/measurement.json. No physical 16 GB start is recorded.',
    },
    size_presets=QWEN_SIZE_PRESETS,
    qualified_minimum_memory_gb=16.0,
    resident_allowed=False,
    # ponytail: measured peak + 2 GB, not the lowest recorded start (14.697 GB), which no 16 GB Mac
    # reaches (they idle at 8-10 GB available). Ceiling: measured only on 32 GiB; raise it if a
    # 16 GB run shows pressure or swap.
    min_free_memory_gb=8.0,
    guidance_ignored=True,
    below_tier_warning=(
        'This Mac has {memory_gb:.0f} GB of unified memory. Qwen Image 2.1 Low-RAM is '
        'qualified on 16 GB and above; 8 GB is not qualified.'),
)

MODELS: dict[str, ImageModel] = {
    model.repo: model for model in (
        FLUX2_KLEIN, Z_IMAGE_TURBO, QWEN_IMAGE_21, QWEN_IMAGE_21_LOW_RAM,
    )
}


def size_presets(model: ImageModel) -> dict:
    return model.size_presets or SIZE_PRESETS


def mlx_serve_problem() -> str | None:
    from .mlx_serve_runner import runtime_problem
    return runtime_problem()


def missing_runtime_model_reason(manifest: dict) -> str:
    """Why an accepted Image package cannot run here, and the file that must learn about it.

    ``MODELS`` is the runtime registry: a manifest alone does not say which runtime class to
    load or what its defaults are, so a package with no entry genuinely cannot run. The
    message therefore names the manifest *and* the entry to add — "unavailable" with nothing
    to act on is the silence this reconciliation exists to remove.
    """
    return (f'{manifest["package_id"]}.json declares engine {manifest["engine"]} for '
            f'{manifest["model_id"]}, but no ImageModel in MODELS serves package '
            f'{manifest["package_id"]}; add one in components/image/app/backend/generation.py')


# The model the worker defaults to when nothing names one: the shipped FLUX.2 Klein.
MODEL_ID = FLUX2_KLEIN.repo
MODEL_REVISION = FLUX2_KLEIN.revision
PACKAGE_ID = FLUX2_KLEIN.package_id
DEFAULTS = dict(FLUX2_KLEIN.defaults)


def host_memory_bytes() -> int:
    """This Mac's physical memory, or 0 when it cannot be read.

    One implementation for the worker: ``memory_policy`` reports the same number through the
    fleet contract.  ``hw.memsize`` is the value the app already reads for admission; the
    ``sysconf`` path is the fallback for a stripped environment.
    """
    try:
        return int(subprocess.check_output(['/usr/sbin/sysctl', '-n', 'hw.memsize'],
                                           text=True, timeout=3).strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    try:
        return os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES')
    except (ValueError, OSError, AttributeError):
        return 0


def _under_memory_pressure() -> bool:
    """True when macOS reports warning or critical memory pressure (``kern.memorystatus_vm_pressure_level``
    2 or 4; 1 is normal). A resident model is not reused into a Mac that is already short of memory.
    An unreadable level is not pressure: the idle window and the Hub's release are the other two guards."""
    try:
        level = int(subprocess.check_output(['/usr/sbin/sysctl', '-n', 'kern.memorystatus_vm_pressure_level'],
                                            text=True, timeout=3).strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return False
    return level >= 2


def memory_plan(model: ImageModel, total_memory_bytes: int,
                mode: str = DEFAULT_LOADING_MODE) -> dict:
    """The mflux memory knobs this host gets for this model, under this machine's setting.

    Read-only projection of the model's :class:`MemoryPolicy`, the Mac's actual memory and the
    operator's per-machine ``loading_mode`` choice — the same decision the CLI makes from
    ``--low-ram``/``--mlx-cache-limit-gb``/``--vae-tiling``/``--vae-tile-size``, but chosen
    from the host instead of typed.

    ``mode`` is that choice: ``auto`` keeps the model's own threshold decision, ``low`` forces
    the bounded configuration and ``full`` forces the default. Forcing ``low`` cannot invent a
    bounded configuration a model does not declare — a model with no
    ``low_ram_below_bytes`` has one loading mode, so it stays on the default and its catalog
    entry publishes that the setting does not apply (see :func:`loading_modes`).
    """
    if mode not in LOADING_MODES:
        raise ValueError(
            f'unknown memory mode {mode!r}; must be one of: {", ".join(LOADING_MODES)}')
    policy = model.memory
    if policy.low_ram_below_bytes is None:
        low_ram = False
    elif mode == MODE_LOW:
        low_ram = True
    elif mode == MODE_FULL:
        low_ram = False
    elif mode == MODE_AUTO:
        low_ram = 0 < total_memory_bytes < policy.low_ram_below_bytes
    else:  # unreachable while the vocabulary above is the only source of ids
        raise ValueError(f'unhandled memory mode {mode!r}')
    # The lean tier sits between the bounded one and the default: only `auto` chooses it, because
    # `full` is "the default configuration whatever this Mac's memory is" and `low` is the bounded one.
    lean = (not low_ram and mode == MODE_AUTO and policy.lean_below_bytes is not None
            and 0 < total_memory_bytes < policy.lean_below_bytes)
    cache_limit = (policy.low_ram_cache_limit_gb if low_ram
                   else policy.lean_cache_limit_gb if lean else None)
    return {
        'mode': 'low-ram' if low_ram else 'default',
        # The operator's choice travels with the plan: the catalog publishes it as `selected`
        # and the job receipt records it, so a configuration can be traced to what asked for it.
        'requested_mode': mode,
        'low_ram': low_ram,
        'mlx_cache_limit_gb': cache_limit,
        'vae_tiling': low_ram and not policy.bounded_vae_decode,
        'vae_tile_size': policy.low_ram_vae_tile_size if low_ram else None,
        # The stage hooks and the bounded FLUX.2 decode (see `staging` / `vae_bounded`). `lean`
        # keeps the model fully loaded and only caps the cache and bounds the decode.
        'staged': low_ram,
        'lean': lean,
        'bounded_vae_decode': (low_ram or lean) and policy.bounded_vae_decode,
        # A model whose components are freed as the job goes cannot serve a second job.
        'resident_ok': not low_ram,
        'stream': (tuple(policy.streamed_components) if low_ram and policy.stream_below_bytes
                   and 0 < total_memory_bytes < policy.stream_below_bytes else ()),
        'weight_definition': policy.weight_definition,
        'total_memory_bytes': total_memory_bytes,
        'low_ram_below_bytes': policy.low_ram_below_bytes,
    }


def _set_mlx_cache_limit(limit_gb: float | None) -> None:
    """Cap MLX's buffer cache for this job, or put it back to what the process started with.

    MLX keeps freed buffers for reuse, up to the whole memory limit by default; on a small Mac that
    is how a finished stage's weights stay resident (and get swapped) instead of going back to the
    OS. ``set_cache_limit`` returns the previous limit, so the first call remembers the original and
    a job with no cap restores it.
    """
    global _ORIGINAL_CACHE_LIMIT
    module = _loaded_mlx()
    setter = getattr(module, 'set_cache_limit', None) if module is not None else None
    if not callable(setter):
        return
    try:
        if limit_gb is None:
            if _ORIGINAL_CACHE_LIMIT is not None:
                setter(_ORIGINAL_CACHE_LIMIT)
                _ORIGINAL_CACHE_LIMIT = None
            return
        previous = setter(int(limit_gb * 1000 ** 3))
        if _ORIGINAL_CACHE_LIMIT is None:
            _ORIGINAL_CACHE_LIMIT = previous
    except Exception:  # noqa: BLE001 - a memory knob must never fail a job
        pass


_ORIGINAL_CACHE_LIMIT: int | None = None


def apply_memory_plan(instance: object, plan: dict, on_stage=None, model_dir=None) -> object | None:
    """Apply a plan to a loaded mflux model through the CLI's own mapping, plus the stage hooks.

    ``CallbackManager._register_memory_saver`` is the function the CLI uses to turn those four
    flags into model state (``mflux/callbacks/callback_manager.py:82``): it sets
    ``model.tiling_config`` from ``--vae-tiling``/``--vae-tile-size``, and constructs the
    ``MemorySaver`` that caps the MLX cache, evicts the text encoders before the loop and — in
    low-RAM mode with a single seed — the transformer after it (``memory_saver.py:12``).

    This deliberately does not call ``CallbackManager.register_callbacks``: that also registers
    mflux's battery saver, and stopping a job at a battery percentage is a policy the owning app
    has never been asked about.  Everything else the CLI does to memory is here.

    ``plan['staged']`` additionally installs :mod:`staging`'s hooks, which is what makes the
    saver's evictions free memory (see that module); ``plan['lean']`` only caps the cache and
    bounds the VAE decode, and leaves the model fully loaded.
    """
    saver = None
    # Ours to set in every plan (a plan with no cap restores MLX's own), so the cap never outlives the
    # configuration that asked for it; mflux's saver sets the same number again below.
    _set_mlx_cache_limit(plan['mlx_cache_limit_gb'])
    if plan['low_ram'] or plan['vae_tiling']:
        from argparse import Namespace

        from mflux.callbacks.callback_manager import CallbackManager

        register = getattr(CallbackManager, '_register_memory_saver', None)
        if register is None:
            raise RuntimeError('mflux no longer exposes the CLI memory mapping this worker pins')
        args = Namespace(low_ram=plan['low_ram'], mlx_cache_limit_gb=plan['mlx_cache_limit_gb'],
                         vae_tiling=plan['vae_tiling'], vae_tile_size=plan['vae_tile_size'])
        saver = register(args=args, model=instance)
    hooks: list[str] = []
    if plan.get('staged') or plan.get('bounded_vae_decode'):
        from . import staging
        hooks = staging.apply(instance, stages=bool(plan.get('staged')),
                              bounded_decode=bool(plan.get('bounded_vae_decode')),
                              uncompiled=bool(plan.get('stream')), on_stage=on_stage)
    if plan.get('stream') and model_dir is not None and 'encode-then-free' in hooks:
        from . import streaming
        for name, groups, multi_pass in plan['stream']:
            component = getattr(instance, name, None)
            if not streaming.supports(component, groups):
                continue
            loader = (streaming.component_loader(plan['weight_definition'], name, model_dir)
                      if multi_pass else None)
            streaming.apply(component, groups, loader=loader)
            hooks.append(f'stream-{name}')
    plan['hooks'] = hooks
    return saver


# The two configurations a bounded model can run, named exactly as `measured_runtime`'s rows
# label them (`memory_mode`) and as `qualification.measured_peak_gb` keys them. A measurement
# row with no `memory_mode` label measured the default configuration — the only one a model
# with no bounded mode has.
DEFAULT_CONFIGURATION = 'default'
LOW_RAM_CONFIGURATION = 'low_ram'


def _is_number(value: object) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _stated_measurement(peak: object, seconds: object) -> dict:
    """One configuration's measured facts, ``None`` where the model states nothing."""
    return {'peak_gb': float(peak) if _is_number(peak) else None,
            'seconds': float(seconds) if _is_number(seconds) else None}


def _stated_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _stated_memory_measurements(model: ImageModel) -> dict:
    """Every memory measurement this model itself states, and where each one came from.

    Peaks come from the model's own ``qualification['measured_peak_gb']``; runtimes come from
    its published ``measured_runtime`` rows, matched on the row's own ``memory_mode`` label. A
    fact the model does not state is ``None`` here, and every sentence built from these facts
    says so rather than estimating: a number the model did not state must never be published.
    """
    peaks = model.qualification.get('measured_peak_gb')
    peaks = peaks if isinstance(peaks, dict) else {}
    runtime = model.measured_runtime if isinstance(model.measured_runtime, dict) else {}
    rows = runtime.get('measured')
    rows = rows if isinstance(rows, list) else []
    seconds: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        label = row.get('memory_mode') or DEFAULT_CONFIGURATION
        if label in seconds or not _is_number(row.get('seconds')):
            continue
        seconds[label] = float(row['seconds'])
    scenes = [_stated_text(row.get('scene')) for row in rows if isinstance(row, dict)]
    scene = next((row.get('scene') for row in rows if isinstance(row, dict)
                  and row.get('memory_mode') == LOW_RAM_CONFIGURATION
                  and _stated_text(row.get('scene'))), None)
    return {
        DEFAULT_CONFIGURATION: _stated_measurement(peaks.get(DEFAULT_CONFIGURATION),
                                                   seconds.get(DEFAULT_CONFIGURATION)),
        LOW_RAM_CONFIGURATION: _stated_measurement(peaks.get(LOW_RAM_CONFIGURATION),
                                                   seconds.get(LOW_RAM_CONFIGURATION)),
        # Where those numbers came from, published beside them rather than left implied.
        'measured_on': (_stated_text(runtime.get('measured_on'))
                        or _stated_text(model.qualification.get('measured_on'))),
        'evidence': _stated_text(runtime.get('evidence')),
        'scene': _stated_text(scene) or next((value for value in scenes if value), None),
        'source': (f'generation.MODELS[{model.repo!r}].qualification.measured_peak_gb and '
                   f'.measured_runtime.measured'),
        'note': None,
    }


def _missing_measurement_note(model: ImageModel, measured: dict, supported: bool) -> str | None:
    """What this model does not state, so a reader is never left to assume a number."""
    missing = []
    default = measured[DEFAULT_CONFIGURATION]
    if default['peak_gb'] is None:
        missing.append('measured peak for the default configuration')
    if default['seconds'] is None:
        missing.append('measured runtime for the default configuration')
    if supported:
        low_ram = measured[LOW_RAM_CONFIGURATION]
        if low_ram['peak_gb'] is None:
            missing.append('measured peak for the bounded configuration')
        if low_ram['seconds'] is None:
            missing.append('measured runtime for the bounded configuration')
    if not missing:
        return None
    return (f'{model.label} states no ' + ', no '.join(missing)
            + '; no number is estimated here.')


def _peak_clause(chosen: dict, other: dict, other_name: str) -> str:
    if chosen['peak_gb'] is not None and other['peak_gb'] is not None:
        return f"about {chosen['peak_gb']:.1f} GB peak instead of {other['peak_gb']:.1f} GB"
    if chosen['peak_gb'] is not None:
        return (f"about {chosen['peak_gb']:.1f} GB peak; the model states no measured peak "
                f"for the {other_name} configuration")
    if other['peak_gb'] is not None:
        return (f"the {other_name} configuration peaks at about {other['peak_gb']:.1f} GB; "
                f"the model states no measured peak for this one")
    return 'no measured peak is stated for either configuration'


def _seconds_clause(chosen: dict, other: dict, other_name: str) -> str:
    if chosen['seconds'] is not None and other['seconds'] is not None:
        return f"about {chosen['seconds']:.0f} s instead of {other['seconds']:.0f} s"
    if chosen['seconds'] is not None:
        return (f"about {chosen['seconds']:.0f} s; the model states no measured runtime for "
                f"the {other_name} configuration")
    if other['seconds'] is not None:
        return (f"the {other_name} configuration takes about {other['seconds']:.0f} s; the "
                f"model states no measured runtime for this one")
    return 'no measured runtime is stated for either configuration'


def _measured_location(measured: dict) -> str:
    scene, where = measured.get('scene'), measured.get('measured_on')
    if scene and where:
        return f' at {scene}, measured on {where}'
    if scene:
        return f' at {scene}'
    if where:
        return f', measured on {where}'
    return ''


def _loading_mode_detail(mode: str, measured: dict, policy: MemoryPolicy) -> str:
    """One option's detail, in the model's own measured numbers and nothing else."""
    default = measured[DEFAULT_CONFIGURATION]
    low_ram = measured[LOW_RAM_CONFIGURATION]
    location = _measured_location(measured)
    if mode == MODE_LOW:
        return (f"Runs mflux's bounded configuration: "
                f"{_peak_clause(low_ram, default, 'default')}, and "
                f"{_seconds_clause(low_ram, default, 'default')}{location}.")
    if mode == MODE_FULL:
        return (f"Runs mflux's default configuration: "
                f"{_peak_clause(default, low_ram, 'bounded low-RAM')}, and "
                f"{_seconds_clause(default, low_ram, 'bounded low-RAM')}{location}.")
    return (f"Chooses from this Mac's unified memory: the bounded configuration below "
            f"{policy.low_ram_below_bytes / 1e9:g} GB, the default at or above it. The trade: "
            f"{_peak_clause(low_ram, default, 'default')}, and "
            f"{_seconds_clause(low_ram, default, 'default')}{location}.")


def loading_modes(model: ImageModel, total_memory_bytes: int, selected: str) -> dict:
    """The machine-wide ``loading_mode`` setting as it applies to one model on this Mac.

    ``selected`` is the operator's persisted choice; ``effective`` is what
    :func:`memory_plan` will actually do for this model's next job on this host, so an
    operator can see that ``auto`` chose the bounded configuration here. ``options`` is empty
    exactly when the model's own :class:`MemoryPolicy` declares no bounded configuration - the
    model has one loading mode, and ``reason`` says so in the model's own terms instead of
    offering a control :func:`apply_memory_plan` would discard.
    """
    policy = model.memory
    plan = memory_plan(model, total_memory_bytes, selected)
    supported = policy.low_ram_below_bytes is not None
    measured = _stated_memory_measurements(model)
    measured['note'] = _missing_measurement_note(model, measured, supported)
    if supported:
        options = [{'id': mode, 'label': spec['label'],
                    'detail': _loading_mode_detail(mode, measured, policy)}
                   for mode, spec in LOADING_MODES.items()]
        reason = None
    else:
        options = []
        why = policy.single_mode_reason or (
            f'{model.label} declares no bounded low-RAM configuration, so it has one '
            f'loading mode.')
        reason = f'{why} This machine-wide setting does not change how it loads.'
        if plan['requested_mode'] != DEFAULT_LOADING_MODE:
            reason = (f'{reason} The setting is {plan["requested_mode"]!r} on this Mac; this '
                      f'model still loads its default configuration.')
    return {
        'supported': supported,
        'selected': plan['requested_mode'],
        'effective': plan['mode'],
        'threshold_bytes': policy.low_ram_below_bytes,
        'options': options,
        'measured': measured,
        'reason': reason,
    }


def _loaded_mlx():
    """The already-imported ``mlx.core``, or None.

    Never import it here.  mlx is only usable inside the pinned image runtime (a bare import
    in a runtime that cannot bring up Metal aborts the process), and by the time a model has
    been loaded mflux has imported it for us.  Importing it for a *report* would be the only
    way this module could kill a worker on a machine where generation itself never ran.
    """
    import sys
    module = sys.modules.get('mlx.core') or sys.modules.get('mlx')
    return module if hasattr(module, 'get_peak_memory') else None


def peak_mlx_bytes() -> int | None:
    """mflux's own memory high-water, the number its CLI prints after a run."""
    module = _loaded_mlx()
    try:
        return int(module.get_peak_memory()) if module is not None else None
    except Exception:  # noqa: BLE001 - reporting must never fail a finished generation
        return None


def _mlx_call(name: str) -> None:
    """Call one memory function of an already-imported ``mlx.core``; never fails a job."""
    module = _loaded_mlx()
    function = getattr(module, name, None) if module is not None else None
    if callable(function):
        try:
            function()
        except Exception:  # noqa: BLE001 - a memory report/release is best effort
            pass


def start_mlx_measurement() -> None:
    """Make the next ``peak_mlx_bytes`` this job's own peak, not the process's all-time maximum.

    ``get_peak_memory`` only ever rises until reset, so every job after the heaviest one reported
    that job's number in its receipt.
    """
    _mlx_call('reset_peak_memory')


def release_mlx_memory() -> None:
    """Hand the model's memory back to macOS after a job.

    MLX keeps freed buffers in a cache for reuse - by default up to the whole memory limit - so
    a model that was deleted still occupied (measured: a 6.4 GB array, freed, left 6.4 GB cached)
    until `clear_cache`. On a 32 GB Mac that left ~22 GB resident after an idle default-mode image
    job and starved the TTS or chat studio that ran next. This runs after the model object is
    dropped (clearing first frees nothing: its buffers are still referenced) and in every
    memory mode, not only when mflux registered a MemorySaver.
    """
    gc.collect()
    _mlx_call('clear_cache')


def model_class(model: ImageModel):
    """Resolve ``'package.module:Class'`` for the requested model."""
    module_name, _, class_name = model.model_class.partition(':')
    if not module_name or not class_name:
        raise ValueError(f'Invalid model class for {model.repo}: {model.model_class}')
    return getattr(importlib.import_module(module_name), class_name)


def lane_parameters(model: ImageModel) -> list[dict]:
    """The parameters this model's lane publishes, from its own limits and defaults.

    One definition read by two consumers that must agree: :func:`lane_contract` publishes it,
    and ``components.model_defaults`` validates an operator's stored default against exactly
    these bounds and enums. A second copy would be free to disagree with the code that rejects
    the request — which is the thing a published bound exists to prevent.

    A fresh list of fresh dicts on every call: the caller may set a resolved ``default`` on a
    parameter without that leaking into the next model's contract.
    """
    guidance = {'name': 'guidance', 'type': 'number', 'required': False,
                'minimum': model.limits['min_guidance'],
                'maximum': model.limits['max_guidance'],
                'default': model.defaults['guidance']}
    if model.guidance_ignored:
        # Two halves of one fact, published together so a lane cannot mark one without the other:
        # `ignored` is the field a consumer matches (a sentence is not a contract, and matching
        # prose is how a rename silently stops a control being explained), and `note` is what a
        # person reads. A parameter with no `ignored` key is not ignored — absent is not true.
        guidance['ignored'] = True
        guidance['note'] = ('accepted and ignored: this model runs without classifier-free '
                            'guidance')
    return [
        {'name': 'prompt', 'type': 'string', 'required': True,
         'max_characters': model.limits['max_prompt']},
        {'name': 'aspect_ratio', 'type': 'string', 'required': False,
         'enum': sorted(size_presets(model)), 'default': model.defaults['aspect_ratio'],
         'note': 'width and height follow from the preset; a mismatched pair is refused'},
        {'name': 'resolution', 'type': 'string', 'required': False,
         'enum': ['1K'], 'default': '1K'},
        {'name': 'steps', 'type': 'integer', 'required': False,
         'minimum': model.limits['min_steps'], 'maximum': model.limits['max_steps'],
         'default': model.defaults['steps']},
        guidance,
        {'name': 'seed', 'type': 'integer', 'required': False,
         'minimum': model.limits['min_seed'], 'maximum': model.limits['max_seed'],
         'default': model.defaults['seed'], 'note': '-1 draws a random seed'},
        # A per-job choice, never an operator default (``model_defaults.editable`` skips
        # ``request_only``): omitting it always means the PNG.
        {'name': 'output_format', 'type': 'string', 'required': False,
         'enum': list(OUTPUT_FORMATS), 'request_only': True,
         'note': ('omit for the PNG mflux wrote; jpeg delivers JPEG quality 90, 4:2:0, '
                  'progressive (about 6.6x smaller) and falls back to the PNG if encoding fails')},
    ]


def defaults_in_force(model: ImageModel, data_dir=None) -> dict:
    """Every published parameter's default: what this model states, over what was chosen.

    ``data_dir`` is this Studio's state directory, where ``model_defaults.json`` lives. With no
    ``data_dir`` nothing has been chosen on this machine as far as this call can see, so the
    answer is exactly what the lane states.

    One call, two readers: :func:`validate_request` applies these values to a request that omits
    a parameter, and :func:`lane_contract` publishes them in each parameter's ``default``. They
    therefore cannot disagree — a contract can never advertise a number this worker would refuse.

    Deliberately ``model_defaults.stated``/``resolved`` rather than the *editable* surface the
    editor renders: ``resolution`` publishes a one-option enum, so there is nothing to choose and
    it has no control — but every job still has to carry it, and the width/height
    ``aspect_ratio`` derives. A map built from the settable subset would silently drop them.
    """
    parameters = lane_parameters(model)
    if data_dir is None:
        return model_defaults.stated(parameters)
    resolved, _not_used = model_defaults.resolved(data_dir, model.repo, parameters)
    return resolved


def validate_request(raw: dict, data_dir=None) -> dict:
    """One request, resolved against the model it names and this Mac's stored defaults.

    ``data_dir`` is where ``model_defaults.json`` lives (the manager's state directory). A
    parameter the request **omits** takes the value in force for this model — what the operator
    stored, over what the model states — and a parameter the request **supplies** always wins.
    With no ``data_dir`` there are no stored defaults to read, so the answer is exactly what the
    lane states: that is the path every direct caller and the existing suite already pins.
    """
    if not isinstance(raw, dict):
        raise ValueError('Image request must be an object')
    model = MODELS.get(raw.get('repo'))
    if model is None:
        raise ValueError('Only a registered image model is available: '
                         + ', '.join(sorted(MODELS)))
    defaults = defaults_in_force(model, data_dir)
    if raw.get('model_revision', model.revision) != model.revision:
        raise ValueError(f'Only the pinned {model.label} revision is available')
    if any(raw.get(key) for key in ('reference_images', 'image_path', 'lora_names',
                                     'lora_paths', 'negative_prompt', 'quantize')):
        raise ValueError('Reference images, LoRAs, negative prompt and quantization are unavailable')
    prompt = raw.get('prompt')
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > model.limits['max_prompt']:
        raise ValueError(f'Prompt must contain 1–{model.limits["max_prompt"]} characters')
    presets = size_presets(model)
    ratio = raw.get('aspect_ratio')
    if ratio is None:
        dimensions = (raw.get('width'), raw.get('height'))
        if dimensions == (None, None):
            ratio = defaults['aspect_ratio']
        else:
            ratio = next((name for name, pixels in presets.items()
                          if dimensions == pixels), None)
    if ratio not in presets or raw.get('resolution', defaults['resolution']) != '1K':
        raise ValueError('Choose a qualified 1K aspect ratio')
    width, height = presets[ratio]
    if type(raw.get('width', width)) is not int or type(raw.get('height', height)) is not int:
        raise ValueError('Width and height must be integer 1K preset pixels')
    if (raw.get('width', width), raw.get('height', height)) != (width, height):
        raise ValueError('Dimensions must match the selected 1K aspect ratio')
    steps = raw.get('steps', defaults['steps'])
    guidance = raw.get('guidance', defaults['guidance'])
    if type(steps) is not int or not model.limits['min_steps'] <= steps <= model.limits['max_steps']:
        raise ValueError(f'Steps must be between {model.limits["min_steps"]} '
                         f'and {model.limits["max_steps"]}')
    if (type(guidance) not in (int, float) or not math.isfinite(guidance)
            or not model.limits['min_guidance'] <= guidance <= model.limits['max_guidance']):
        raise ValueError(f'Guidance must be finite and between {model.limits["min_guidance"]:g} '
                         f'and {model.limits["max_guidance"]:g}')
    seed = raw.get('seed', defaults['seed'])
    if seed is None:
        seed = -1
    if type(seed) is not int or not model.limits['min_seed'] <= seed <= model.limits['max_seed']:
        raise ValueError('Seed must be -1 or a 32-bit unsigned integer')
    normalized = {'repo': model.repo, 'model_revision': model.revision, 'prompt': prompt.strip(),
                  'aspect_ratio': ratio, 'resolution': '1K', 'width': width, 'height': height,
                  'steps': steps, 'guidance': float(guidance), 'seed': seed}
    # Echoed only when supplied, so the params an older caller compares on recovery are unchanged.
    output_format = raw.get('output_format')
    if output_format is not None:
        if (not isinstance(output_format, str)
                or output_format.strip().lower() not in OUTPUT_FORMATS):
            raise ValueError('output_format must be ' + ' or '.join(OUTPUT_FORMATS))
        normalized['output_format'] = output_format.strip().lower()
    return normalized


LANE_CONTRACT_SCHEMA = 'studiohub.lane-contract.v1'


def lane_contract(model: ImageModel, data_dir=None) -> dict:
    """The Image lane's dispatch contract for one model.

    What a scheduler needs in order to *ask for* this lane's work: the operation name, the
    parameters it may send and the bounds that will actually be enforced, the artifact it gets
    back, the terminal vocabulary, and what a cancel does not promise. Published beside the
    model's own defaults so a caller never has to read this worker's source.

    Built from ``model.limits`` and ``model.defaults`` rather than from literals, because those
    are the numbers ``validate_request`` actually enforces: a second copy here would be free to
    disagree with the code that rejects the request, and the contract would be the thing that is
    wrong. A new image model therefore publishes a correct contract with no edit in this function.

    ``data_dir`` resolves each settable parameter's ``default`` to the value in force on this
    machine — what the model states, overridden by what the operator chose. It comes from the same
    :func:`defaults_in_force` call ``validate_request`` applies, so the catalog cannot publish a
    default the worker would not itself accept.

    Deliberately **not** listed: the parameters ``validate_request`` refuses outright
    (``reference_images``, ``image_path``, ``lora_names``, ``lora_paths``, ``negative_prompt``,
    ``quantize``) and ``width``/``height``, which are derived from ``aspect_ratio`` and refused
    unless they match the preset. A contract that offers a parameter the lane rejects is worse
    than one that omits it.
    """
    parameters = lane_parameters(model)
    in_force = defaults_in_force(model, data_dir)
    for parameter in parameters:
        if parameter['name'] in in_force:
            parameter['default'] = in_force[parameter['name']]
    contract = {
        'schema': LANE_CONTRACT_SCHEMA,
        'modality': 'image',
        # The same token `capabilities.OPERATION_BY_MODALITY` publishes, so a reader can join the
        # contract to the Hub's operation namespace without a table of its own.
        'operation': 'image.text_to_image',
        'parameters': parameters,
        'artifact': {'modality': 'image', 'media_type': 'image/png', 'extension': 'png',
                     'per_item': 1},
        'states': {
            'accepted': ['queued'],
            'active': ['running', 'cancel_requested'],
            'terminal': ['done', 'error', 'cancelled'],
            'uncertain': 'uncertain',
            'uncertain_is_terminal': False,
            'discard_supported': True,
        },
        'cancel': {
            'supported': True,
            'request': 'DELETE /api/hub/jobs/{batch_id}',
            'semantics': ('asks the worker to stop; a generation already past its sampling loop '
                          'may still write its PNG, so a cancelled item is not evidence that '
                          'nothing was written'),
        },
    }
    for parameter in contract['parameters']:
        if parameter.get('note') is None:
            parameter.pop('note', None)
    if model.measured_runtime:
        contract['runtime_seconds'] = model.measured_runtime
    return contract


def load_image_manifests(directory: Path, required: Path) -> tuple[dict, dict]:
    """Every Image manifest this build ships, keyed by ``package_id``.

    ``required`` is the manifest the bundle names explicitly and must be valid.  Its siblings
    are read too, because the bundle already copies the whole ``model-manifests`` directory
    (``packaging/stage_voice.py:252``) and one file must not be able to take the worker down:
    a sibling that is another engine's, or that does not validate, is reported instead.

    Returns ``(manifests, errors)`` where ``errors`` maps a manifest file name to why it was
    skipped, so the catalog can say why a registered model is missing rather than just absent.
    """
    from components import model_packages

    errors: dict[str, str] = {}
    manifests: dict[str, dict] = {}

    def read(path: Path, *, required_file: bool) -> None:
        try:
            candidate = json.loads(path.read_text())
            if candidate.get('engine') not in IMAGE_ENGINES:
                if required_file:
                    raise ValueError(
                        f'Not an Image manifest: {candidate.get("engine")!r}')
                return
            model_packages.validate_manifest(candidate)
        except (OSError, ValueError, AttributeError) as error:
            if required_file:
                raise
            errors[path.name] = str(error)
            return
        manifests[candidate['package_id']] = candidate

    read(required, required_file=True)
    try:
        siblings = sorted(directory.glob('*.json'))
    except OSError:
        return manifests, errors
    for path in siblings:
        if path != required:
            read(path, required_file=False)
    return manifests, errors


def _atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_name(f'.{path.name}.{uuid4().hex}.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            os.chmod(temporary, 0o600)
            json.dump(value, stream, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def final_matches(job: dict, path: Path) -> bool:
    try:
        return bool(not path.is_symlink() and path.is_file()
                    and job.get('output_path') == str(path)
                    and path.stat().st_size == job.get('bytes')
                    and sha256(path.read_bytes()).hexdigest() == job.get('sha256'))
    except OSError:
        return False


def _activity_time(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _activity_progress(value: object) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = 0.0
    if not math.isfinite(parsed):
        parsed = 0.0
    return max(0.0, min(1.0, parsed))


def _activity_error_code(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip()[:80] or None


class GenerationManager:
    def __init__(self, data: Path, model_library: Path, manifest: dict,
                 runtime_sha256: str, machine: str = 'local',
                 manifests: dict[str, dict] | list[dict] | None = None,
                 manifest_errors: dict[str, str] | None = None,
                 total_memory_bytes: int | None = None) -> None:
        self.data = data
        self.outputs = data / 'outputs'
        self.outputs.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.outputs.is_symlink():
            raise ValueError('Image output directory cannot be a symlink')
        self.state_file = data / 'jobs.json'
        self.library = model_library
        self.manifest = manifest
        self.manifests = self._manifest_index(manifest, manifests)
        self.manifest_errors = dict(manifest_errors or {})
        self.runtime_sha256 = runtime_sha256
        self.machine = machine
        self.total_memory_bytes = (host_memory_bytes() if total_memory_bytes is None
                                   else int(total_memory_bytes))
        self.jobs: dict[str, dict] = {}
        self.cancel_events: dict[str, threading.Event] = {}
        self.lock = threading.RLock()
        self.inference_lock = threading.Lock()
        # The one model kept loaded between jobs (see `_remember`): {'repo', 'instance', 'key',
        # 'idle_since'}. Only ever touched with `inference_lock` held or from `release_resident`,
        # which takes it.
        self._resident: dict | None = None
        self._work: queue.Queue = queue.Queue()
        self._inference_thread: threading.Thread | None = None
        self._restore()

    @staticmethod
    def _manifest_index(manifest: dict,
                        manifests: dict[str, dict] | list[dict] | None) -> dict[str, dict]:
        """Index the manifests this worker may run, keyed by ``package_id``.

        ``manifest`` is the single pinned manifest the worker has always been constructed
        with; ``manifests`` carries the rest of the build's Image manifests.  Both are
        accepted so an existing caller (and the tests) keep working unchanged.
        """
        index: dict[str, dict] = {}
        if isinstance(manifest, dict) and manifest.get('package_id'):
            index[manifest['package_id']] = manifest
        values = manifests.values() if isinstance(manifests, dict) else (manifests or [])
        for candidate in values:
            if isinstance(candidate, dict) and candidate.get('package_id'):
                index[candidate['package_id']] = candidate
        return index

    # ── residency between jobs ──────────────────────────────────────────────
    # A model whose plan keeps every component loaded (``plan['resident_ok']``) can serve the next
    # job of the same model without reloading it: measured on a 32 GB M2 Max, the first denoise
    # step of a freshly loaded FLUX.2 Klein 4-bit costs 15 s more than the next (the weights are
    # read from disk when first used) and Z-Image-Turbo 4-bit about 9 s. Whether to keep it is the
    # memory policy's call (``memory_policy.keep_resident``: never on a small Mac, an idle window
    # on a large one); whether it may stay is the machine's (pressure) and the Hub's (it asks
    # every Studio to release before another needs the RAM - ``release_resident``).

    @staticmethod
    def _plan_key(plan: dict) -> tuple:
        return (plan['mode'], plan['lean'], plan['bounded_vae_decode'], plan['mlx_cache_limit_gb'])

    def _keep_resident(self) -> bool:
        from . import memory_policy
        return memory_policy.keep_resident(self)

    def _acquire(self, model: ImageModel, model_folder: str, plan: dict) -> tuple[object, bool]:
        """The model object for this job, and whether it was already loaded."""
        resident, self._resident = self._resident, None
        if (resident is not None and resident['repo'] == model.repo
                and resident['key'] == self._plan_key(plan) and plan['resident_ok']
                and not _under_memory_pressure()):
            return resident['instance'], True
        if resident is not None:
            # A different model, a different configuration, or a Mac that is short of memory: the
            # old one goes before the new one loads, never alongside it.
            del resident
            release_mlx_memory()
        return model_class(model)(
            model_path=str(Path(model_folder) / WEIGHTS_SUBDIRECTORY), quantize=None,
            lora_paths=None, lora_scales=None), False

    def _remember(self, repo: str, instance: object, plan: dict) -> None:
        self._resident = {'repo': repo, 'instance': instance, 'key': self._plan_key(plan),
                          'idle_since': time.time()}

    def _forget_resident(self) -> None:
        self._resident = None

    def resident_models(self) -> list[str]:
        resident = self._resident
        return [resident['repo']] if resident else []

    def resident_idle_seconds(self) -> float | None:
        resident = self._resident
        return max(0.0, time.time() - resident['idle_since']) if resident else None

    def release_resident(self, reason: str = 'manual') -> list[str]:
        """Drop the resident model; refuses (``RuntimeError``) while a generation is running."""
        if not self.inference_lock.acquire(blocking=False):
            raise RuntimeError('image generation is active; memory was not released')
        try:
            if any(job.get('state') == 'running' for job in self.list()):
                raise RuntimeError('image generation is active; memory was not released')
            released = self.resident_models()
            self._resident = None
            if released:
                release_mlx_memory()
            return released
        finally:
            self.inference_lock.release()

    def shutdown(self) -> None:
        """Cancel outstanding jobs and stop a native leaf before worker exit."""
        with self.lock:
            for job_id, job in self.jobs.items():
                if job.get('state') not in TERMINAL:
                    event = self.cancel_events.get(job_id)
                    if event is not None:
                        event.set()
        from .mlx_serve_runner import stop_active_leaf
        stop_active_leaf(timeout_s=5)
        if self.inference_lock.acquire(timeout=5):
            try:
                resident, self._resident = self._resident, None
                if resident is not None:
                    del resident
                    release_mlx_memory()
            finally:
                self.inference_lock.release()

    def _restore(self) -> None:
        from components import job_history
        try:
            saved = json.loads(self.state_file.read_text())
            if not isinstance(saved, dict):
                raise ValueError('Image job history is malformed')
        except FileNotFoundError:
            saved = {}
        for job_id, job in saved.items():
            if not isinstance(job, dict) or job_id != job.get('id'):
                continue
            if job.get('state') not in TERMINAL:
                uncertain_output = any((self.outputs / f'{job_id}{suffix}').exists()
                                       for suffix in _DELIVERED_SUFFIXES)
                job.update(state='error',
                    error=('Final PNG exists without a durable completion receipt; reconcile before retry'
                           if uncertain_output else 'Worker restarted before completion'),
                    error_code=('JOB_PERSISTENCE_UNCERTAIN' if uncertain_output
                                else 'WORKER_RESTART_UNCERTAIN'), finished_at=time.time())
            if job.get('state') == 'done':
                path = self.final_path(job)
                if not final_matches(job, path):
                    job.update(state='error', error='Final image is unavailable after restart',
                               error_code='OUTPUT_UNAVAILABLE', output_path=None, output_url=None)
            if job_history.expired(job):
                continue                  # finished more than 90 days ago: the record goes
            self.jobs[job_id] = job
        self._persist()

    def _persist(self) -> None:
        from components import job_history
        job_history.prune_daily(self, self.jobs)
        _atomic_json(self.state_file, self.jobs)

    def manifest_for(self, repo: str) -> dict | None:
        model = MODELS.get(repo)
        return self.manifests.get(model.package_id) if model else None

    def preflight(self, repo: str) -> dict:
        """Readability of one registered model's installed package.

        A registered model with no manifest in this build, or whose manifest pins a different
        model or revision than the registry does, is reported as unavailable for that model
        only — the others keep working.
        """
        from components import model_packages

        model = MODELS.get(repo)
        if model is None:
            return {'state': 'unknown_model', 'reason': f'Unregistered image model: {repo}'}
        manifest = self.manifest_for(repo)
        if manifest is None:
            reason = f'No model manifest for {model.package_id} in this build'
            detail = self.manifest_errors.get(f'{model.package_id}.json')
            if detail:
                reason = f'{reason}: {detail}'
            return {'state': 'not_installed', 'package_id': model.package_id, 'reason': reason}
        if (manifest.get('model_id') != model.repo
                or manifest.get('revision') != model.revision):
            return {'state': 'pin_mismatch', 'package_id': model.package_id,
                    'reason': (f'{model.package_id} is pinned to {manifest.get("revision")} '
                               f'but this worker runs {model.revision}')}
        files = model_packages.preflight(manifest, self.library)
        if files.get('state') == 'files_verified' and model.engine == 'image-mlx-serve':
            problem = mlx_serve_problem()
            if problem:
                return {**files, 'state': 'runtime_unavailable', 'reason': problem}
        return files

    def packages_without_a_runtime_model(self) -> list[dict]:
        """Every Image manifest this build read that no registered model serves.

        ``preflight`` above is one direction of the reconciliation — a registered model whose
        manifest is missing or revision-drifted, reported for that model only. This is the
        other: ``load_image_manifests`` accepts every supported Image-engine manifest the bundle
        ships, while the catalog is enumerated from ``MODELS``, so a package with no
        ``ImageModel`` entry was published by the Hub, offered by the kits, and simply absent
        from this worker's ``/api/catalog`` — no row, no error, no entry in the picker. The
        catalog publishes a row for each one instead, carrying
        :func:`missing_runtime_model_reason`.

        Identity is the manifest's ``package_id``, the same key ``manifest_for`` resolves a
        registered model through, so a package that *is* served is never reported here.
        """
        served = {model.package_id for model in MODELS.values()}
        return [manifest for package_id, manifest in sorted(self.manifests.items())
                if package_id not in served]

    def get(self, job_id: str) -> dict | None:
        with self.lock:
            return dict(self.jobs[job_id]) if job_id in self.jobs else None

    def list(self) -> list[dict]:
        with self.lock:
            return [dict(job) for job in sorted(self.jobs.values(),
                    key=lambda value: value['created_at'], reverse=True)]

    @staticmethod
    def _activity_projection(job: dict, *, observed_at: float) -> dict:
        """Bound one Image job to the Hub's allowlisted activity contract.

        Only job-store fields are used.  The worker cannot tell a Hub-dispatched
        job from a direct one, so it reports ``source: direct`` and the Hub's
        broker ownership reconciliation remains authoritative.
        """
        params = job.get('params') if isinstance(job.get('params'), dict) else {}
        model = str(params.get('repo') or '').strip()[:500] or None
        state = str(job.get('state') or '')
        created_at = _activity_time(job.get('created_at'))
        started_at = _activity_time(job.get('started_at'))
        finished_at = _activity_time(job.get('finished_at'))
        projection = {
            'id': str(job.get('id') or '')[:160], 'state': state, 'model': model,
            'operation': 'image', 'progress': _activity_progress(job.get('progress')),
            'source': 'direct',
        }
        if created_at is not None:
            projection['created_at'] = created_at
        if started_at is not None:
            projection['started_at'] = started_at
        if state in {'queued', 'running', 'cancel_requested'}:
            projection['updated_at'] = observed_at
        else:
            if finished_at is not None:
                projection['finished_at'] = finished_at
            if started_at is not None and finished_at is not None:
                projection['runtime_s'] = max(0.0, finished_at - started_at)
            code = _activity_error_code(job.get('error_code'))
            if code:
                projection['error_code'] = code
        return projection

    def activity_snapshot(self, observed_at: float | None = None) -> dict:
        """Return the private, bounded activity contract the Hub polls.

        The Image job store is the only source.  An empty store yields a valid
        empty snapshot rather than a 404, so a worker that is up but idle never
        reads as an absent reporter.
        """
        observed = float(time.time() if observed_at is None else observed_at)
        with self.lock:
            jobs = [dict(job) for job in self.jobs.values()]
        running = [job for job in jobs
                   if job.get('state') in {'running', 'cancel_requested'}]
        queued = [job for job in jobs if job.get('state') == 'queued']
        terminal = [job for job in jobs if job.get('state') in TERMINAL]
        active = max(
            running or queued,
            key=lambda job: (job.get('state') in {'running', 'cancel_requested'},
                             _activity_time(job.get('started_at'))
                             or _activity_time(job.get('created_at')) or 0.0),
            default=None,
        )
        latest = max(
            terminal,
            key=lambda job: (_activity_time(job.get('finished_at'))
                             or _activity_time(job.get('created_at')) or 0.0,
                             _activity_time(job.get('created_at')) or 0.0),
            default=None,
        )
        return {
            'schema': 'kh-studio.activity.v1', 'studio': 'image',
            'observed_at': observed,
            'active': self._activity_projection(active, observed_at=observed) if active else None,
            'latest': self._activity_projection(latest, observed_at=observed) if latest else None,
        }

    def submit(self, raw: dict) -> dict:
        # The worker's own state directory, so a parameter the request omits takes this Mac's
        # stored default for this model rather than the model's own.
        params = validate_request(raw, data_dir=self.data)
        files = self.preflight(params['repo'])
        if files['state'] != 'files_verified':
            raise RuntimeError(f'MODEL_FILES_UNAVAILABLE: {files.get("reason") or files["state"]}')
        job_id = uuid4().hex
        job = {'id': job_id, 'mode': 'txt2img', 'params': params, 'state': 'queued',
               'progress': 0.0, 'created_at': time.time(), 'started_at': None,
               'finished_at': None, 'generated_at': None, 'duration_seconds': None,
               'output_path': None, 'output_url': None, 'sha256': None, 'bytes': None,
               'width': None, 'height': None, 'steps': None, 'resolved_seed': None,
               'media_type': None, 'error': None, 'error_code': None,
               'model_revision': params['model_revision'], 'runtime_revision': self.runtime_sha256,
               'memory_mode': None, 'loading_mode_setting': None, 'peak_mlx_bytes': None,
               'model_reused': None, 'memory_hooks': None,
               'worker_id': 'image', 'machine_id': self.machine, 'acknowledged': False}
        with self.lock:
            self.jobs[job_id] = job
            self.cancel_events[job_id] = threading.Event()
            try:
                self._persist()
            except Exception:
                self.jobs.pop(job_id, None)
                self.cancel_events.pop(job_id, None)
                raise
        self._enqueue(self._run, job_id, files['folder'])
        return dict(job)

    def _enqueue(self, function, *args) -> None:
        """Run ``function`` on the one inference thread, in submission order.

        Every job used to get its own thread. MLX keeps its default stream per thread, so an array or
        module built on one job's thread cannot be used from another's ("There is no Stream(cpu, 0) in
        current thread" - measured the first time a model was reused for a second job). A model that stays
        resident across jobs therefore needs every job on the same thread; so, for one generation at a time
        anyway, every job runs on this one daemon thread.
        """
        with self.lock:
            if self._inference_thread is None:
                self._inference_thread = threading.Thread(target=self._inference_loop, daemon=True,
                                                          name='image-inference')
                self._inference_thread.start()
        self._work.put((function, args))

    def _inference_loop(self) -> None:
        while True:
            function, args = self._work.get()
            try:
                function(*args)
            except Exception:  # noqa: BLE001 - `_run` records its own failures; nothing may end this thread
                traceback.print_exc()

    def cancel(self, job_id: str) -> dict | None:
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                return None
            if job['state'] not in TERMINAL:
                self.cancel_events[job_id].set()
                if job['state'] == 'queued':
                    job.update(state='cancelled', finished_at=time.time())
                    self._persist()
            return dict(job)

    def ack(self, job_id: str) -> dict | None:
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None or job['state'] != 'done':
                return None
            if not final_matches(job, self.final_path(job)):
                return None
            if not job['acknowledged']:
                job['acknowledged'] = True
                job['receipt_acked_at'] = time.time()
                try:
                    self._persist()
                except Exception:
                    job['acknowledged'] = False
                    job.pop('receipt_acked_at', None)
                    raise
            return {'ok': True, 'job_id': job_id, 'sha256': job['sha256']}

    def final_path(self, job: dict) -> Path:
        """The delivered file this job's record names, only ever inside this worker's outputs.

        Not ``{id}.png``: a job that asked for ``output_format: jpeg`` delivers ``{id}.jpg``. An
        old or foreign record falls back to the PNG name.
        """
        recorded = job.get('output_path')
        if isinstance(recorded, str) and recorded:
            path = Path(recorded)
            if path.parent == self.outputs and path.suffix in _DELIVERED_SUFFIXES:
                return path
        return self.outputs / f"{job['id']}.png"

    def _run(self, job_id: str, model_folder: str) -> None:
        staged = self.outputs / f'.{job_id}.png'
        staged_jpeg = self.outputs / f'.{job_id}.jpg'
        pending = None
        with self.inference_lock:
            with self.lock:
                event = self.cancel_events[job_id]
                job = self.jobs[job_id]
                if event.is_set():
                    job.update(state='cancelled', finished_at=time.time())
                    self._persist()
                    # This is the job's own thread finishing, so nothing will look the event up
                    # again. A job cancelled while it was still queued - before it ever reached this
                    # lock - returned here without this line and left one threading.Event in the
                    # dictionary for every such cancellation, for the life of the process.
                    self.cancel_events.pop(job_id, None)
                    return
                job.update(state='running', progress=0.05, started_at=time.time())
                try:
                    self._persist()
                except Exception:
                    # No inference has started. Never leave an in-memory job
                    # looking active after its running receipt failed.
                    job.update(state='error', error='Running state could not be persisted',
                               error_code='JOB_PERSISTENCE_FAILED', finished_at=time.time())
                    try:
                        self._persist()
                    except Exception:
                        pass  # restored queued receipt becomes restart uncertainty
                    self.cancel_events.pop(job_id, None)
                    return
            try:
                repo = job['params']['repo']
                model = MODELS.get(repo)
                if model is None:
                    raise RuntimeError(f'MODEL_FILES_UNAVAILABLE: unregistered model {repo}')
                files = self.preflight(repo)
                if files['state'] != 'files_verified' or files['folder'] != model_folder:
                    raise RuntimeError('MODEL_FILES_UNAVAILABLE: pinned model changed before inference')
                staged.unlink(missing_ok=True)
                seed = job['params']['seed']
                if seed == -1:
                    seed = random.randint(0, model.limits['max_seed'])
                plan = memory_plan(model, self.total_memory_bytes,
                                   loading_mode.current(self.data))
                with self.lock:
                    job['resolved_seed'] = seed
                    # What actually ran, and which machine-wide setting asked for it: `auto`
                    # choosing the bounded configuration here is a different fact from an
                    # operator forcing it, and a job receipt should not conflate the two.
                    job['memory_mode'] = plan['mode']
                    job['loading_mode_setting'] = plan['requested_mode']
                    self._persist()
                # Pinned mflux 0.20.0 path, dispatched from the registry. ST-01's verified
                # package adds one model/ prefix to every pack (see WEIGHTS_SUBDIRECTORY).
                if model.engine == 'image-mlx-serve':
                    # The native leaf cannot overlap a resident mflux model and never becomes
                    # resident itself. It inherits this worker's process group, so parent loss also
                    # reaches it; the runner owns TERM -> KILL and reaping on every return path.
                    resident, self._resident = self._resident, None
                    if resident is not None:
                        del resident
                        release_mlx_memory()
                    from .mlx_serve_runner import run_image
                    run_image(Path(model_folder) / WEIGHTS_SUBDIRECTORY, {
                        **job['params'], 'seed': seed,
                    }, staged, event)
                    peak, reused = None, False
                elif model.engine == IMAGE_ENGINE:
                    start_mlx_measurement()
                    instance, reused = self._acquire(model, model_folder, plan)
                    memory_saver = None
                    keep = False
                    try:
                        if not reused:
                            memory_saver = apply_memory_plan(
                                instance, plan, model_dir=Path(model_folder) / WEIGHTS_SUBDIRECTORY)
                        set_cancel_event = getattr(instance, 'set_cancel_event', None)
                        if callable(set_cancel_event):
                            set_cancel_event(event)
                        result = instance.generate_image(seed=seed, prompt=job['params']['prompt'],
                            num_inference_steps=job['params']['steps'], height=job['params']['height'],
                            width=job['params']['width'], guidance=job['params']['guidance'])
                        peak = peak_mlx_bytes()
                        keep = bool(model.resident_allowed and plan['resident_ok']
                                    and self._keep_resident())
                    finally:
                        if keep:
                            # Stays loaded for the next job of this model: only what MLX cached beyond
                            # the weights goes back to macOS. Never kept after a failure - a model that
                            # raised mid-generation is not known to be in a state worth reusing.
                            self._remember(model.repo, instance, plan)
                            del instance, memory_saver
                            release_mlx_memory()
                        else:
                            # Image releases its model after every job unless the memory policy says
                            # otherwise. mflux registers its MemorySaver on the model and the saver holds
                            # the model, so without breaking that cycle the weights would outlive the job
                            # until a gc pass.
                            instance.callbacks = None
                            del instance, memory_saver
                            self._forget_resident()
                            release_mlx_memory()
                    if not hasattr(result, 'save'):
                        raise RuntimeError('mflux did not return a GeneratedImage')
                    result.save(str(staged), overwrite=True)
                else:
                    raise RuntimeError(f'Unsupported Image engine: {model.engine}')
                with self.lock:
                    job['model_reused'] = reused
                    job['memory_hooks'] = list(plan.get('hooks') or [])
                del plan
                with Image.open(staged) as image:
                    if image.format != 'PNG':
                        raise RuntimeError('Image engine did not produce a PNG')
                    image.verify()
                with Image.open(staged) as image:
                    dimensions = image.size
                expected = (job['params']['width'], job['params']['height'])
                if dimensions != expected:
                    raise RuntimeError(f'Generated dimensions {dimensions} differ from requested {expected}')
                content = staged.read_bytes()
                if not content.startswith(b'\x89PNG\r\n\x1a\n'):
                    raise RuntimeError('Image engine PNG signature is invalid')
                pending = (content, dimensions, peak)
            except Exception as exc:
                self._record_failure(job, event, exc)
        # The inference lock is released: the model is gone and the next job may start while this
        # one's PNG is compressed (if asked) and published. Every job runs on the one inference
        # thread (`_enqueue`), so a finished PNG is handed to its own short-lived thread: the JPEG
        # encode is PIL/CPU work that touches no MLX stream, and it must not hold up the next job.
        if pending is None:
            self._finish(job_id, job, event, staged, staged_jpeg, None)
        else:
            threading.Thread(target=self._finish, args=(job_id, job, event, staged, staged_jpeg, pending),
                             daemon=True, name=f'image-deliver-{job_id[:8]}').start()

    def _finish(self, job_id: str, job: dict, event: threading.Event, staged: Path, staged_jpeg: Path,
                pending: tuple | None) -> None:
        try:
            if pending is not None:
                self._deliver(job_id, job, event, staged, staged_jpeg, *pending)
        except Exception as exc:
            self._record_failure(job, event, exc)
        finally:
            staged.unlink(missing_ok=True)
            staged_jpeg.unlink(missing_ok=True)
            self.cancel_events.pop(job_id, None)

    def _record_failure(self, job: dict, event: threading.Event, exc: Exception) -> None:
        with self.lock:
            if event.is_set():
                job.update(state='cancelled', error=None, error_code=None)
            else:
                detail = str(exc)
                if job.get('error_code') not in ('JOB_PERSISTENCE_FAILED',
                                                 'JOB_PERSISTENCE_UNCERTAIN'):
                    job.update(state='error', error=detail[:500],
                               error_code=('MODEL_FILES_UNAVAILABLE' if 'MODEL_FILES_UNAVAILABLE' in detail
                                           else 'IMAGE_GENERATION_FAILED'))
            job['finished_at'] = time.time()
            job['duration_seconds'] = job['finished_at'] - job['started_at']
            self._persist()

    def _deliver(self, job_id: str, job: dict, event: threading.Event, staged: Path,
                 staged_jpeg: Path, content: bytes, dimensions: tuple, peak) -> None:
        """Publish the finished image: the PNG as made, or the JPEG the job asked for.

        The PNG master is the hidden staged file and is never published when the JPEG is: it is
        removed by the caller once this returns. An encode that fails delivers the PNG and records
        why, because a paid generation is never failed by compression.
        """
        delivered, source, fmt = self.outputs / f'{job_id}.png', staged, 'png'
        body = content
        delivery = None
        if job['params'].get('output_format') == 'jpeg':
            delivery = {'requested': 'jpeg', 'master_media_type': 'image/png',
                        'master_bytes': len(content), 'master_sha256': sha256(content).hexdigest()}
            try:
                body = encode_jpeg(staged, staged_jpeg, expected_size=dimensions)
            except Exception as exc:  # noqa: BLE001 - deliver the PNG, never fail the paid job
                body = content
                delivery.update(applied='png', delivery_fallback_reason=str(exc)[:400])
            else:
                delivered, source, fmt = self.outputs / f'{job_id}.jpg', staged_jpeg, 'jpeg'
                delivery.update(applied='jpeg', quality=JPEG_QUALITY, subsampling='4:2:0',
                                progressive=True)
        with self.lock:
            if event.is_set():
                job.update(state='cancelled', finished_at=time.time())
            else:
                os.replace(source, delivered)
                now = time.time()
                job.update(state='done', progress=1.0, finished_at=now,
                    generated_at=now, duration_seconds=now-job['started_at'],
                    output_path=str(delivered),
                    output_url=f'/api/generate/jobs/{job_id}/image',
                    sha256=sha256(body).hexdigest(), bytes=len(body),
                    media_type=MEDIA_TYPE_BY_FORMAT[fmt], format=fmt, width=dimensions[0],
                    height=dimensions[1], steps=job['params']['steps'],
                    peak_mlx_bytes=peak,
                    peak_process_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
                if delivery is not None:
                    job['delivery'] = delivery
                    if delivery.get('delivery_fallback_reason'):
                        job['delivery_fallback_reason'] = delivery['delivery_fallback_reason']
            try:
                self._persist()
            except Exception:
                job.update(state='error', output_path=None, output_url=None,
                           sha256=None, bytes=None, media_type=None, format=None,
                           error='Final output could not be persisted; reconcile before retry',
                           error_code='JOB_PERSISTENCE_UNCERTAIN')
                try:
                    delivered.unlink(missing_ok=True)
                    job.update(error='Final output could not be persisted; generation was discarded',
                               error_code='JOB_PERSISTENCE_FAILED')
                except OSError:
                    pass
                raise


def encode_jpeg(source: Path, destination: Path, *, expected_size: tuple) -> bytes:
    """Encode the verified PNG master to the fixed JPEG preset and return the verified bytes.

    RGB (no generated image has alpha), quality 90, 4:2:0, progressive, optimised, no EXIF. The
    result is reopened and must be a JPEG of the master's exact dimensions, or this raises and the
    caller delivers the PNG.
    """
    destination.unlink(missing_ok=True)
    with Image.open(source) as image:
        image.convert('RGB').save(destination, format='JPEG', quality=JPEG_QUALITY,
                                  optimize=True, progressive=True, subsampling='4:2:0')
    body = destination.read_bytes()
    if not body.startswith(b'\xff\xd8\xff'):
        raise RuntimeError('JPEG encode did not produce a JPEG')
    with Image.open(destination) as image:
        image.verify()
    with Image.open(destination) as image:
        if image.format != 'JPEG' or image.size != tuple(expected_size):
            raise RuntimeError('JPEG encode changed the image dimensions')
    return body
