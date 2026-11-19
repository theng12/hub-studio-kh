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
from functools import lru_cache
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
import statistics
import subprocess
import threading
import time
import traceback
from uuid import uuid4

from PIL import Image

from components import model_defaults

from . import loading_mode
from .qwen_lowram import DenoiseCancelled
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

# The reference images an edit lane accepts, from the bytes rather than from a caller's claim:
# PIL's own format name decides, and the media type and file suffix follow from it. WebP has no
# delivered suffix above (this lane never *writes* one); it is accepted as input like PNG/JPEG.
REFERENCE_MEDIA_TYPE_BY_FORMAT = {'PNG': 'image/png', 'JPEG': 'image/jpeg', 'WEBP': 'image/webp'}
REFERENCE_SUFFIX_BY_MEDIA_TYPE = {'image/png': '.png', 'image/jpeg': '.jpg', 'image/webp': '.webp'}
REFERENCE_MEDIA_TYPES = tuple(REFERENCE_MEDIA_TYPE_BY_FORMAT.values())
# One reference image's ceiling, the video lane's constant: a hand-placed file is the only case
# that can reach it (Story's own character upload caps at 5 MB).
MAX_REFERENCE_BYTES = 25 * 1024 * 1024

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
    # 1344x768 is 7:4, 1.6% narrower than 16:9: its own key and pixels, never relabelled 16:9.
    '7:4': (1344, 768),
    '4:3': (1152, 864), '3:4': (864, 1152), '3:2': (1200, 800),
    '2:3': (800, 1200), '5:4': (1120, 896), '4:5': (896, 1120),
    '21:9': (1680, 720), '2:1': (1408, 704), '1:2': (704, 1408),
}
QWEN_SIZE_PRESETS = {
    '16:9': (1280, 720), '9:16': (720, 1280), '1:1': (1024, 1024),
    # 1344x768 is 7:4, 1.6% narrower than 16:9: its own key and pixels, never relabelled 16:9.
    '7:4': (1344, 768),
}
# The two **mflux** Qwen rows (Low-RAM and Turbo, text lane and reference lane alike): mflux's
# Qwen-Image-2.1 latent creator refuses any dimension that is not a positive multiple of 32
# (`mflux/models/qwen21/latent_creator/qwen_image21_latent_creator.py`), so 720-height canvases
# pass `validate_request` and then die inside the engine. The family table above stays only on
# the mlx-serve row, whose own qualification measured 1280x720 on that different engine.
# These are the same frames at engine-valid pixels — producer data, never a consumer's rule.
QWEN_MFLUX_SIZE_PRESETS = {
    '16:9': (1024, 576), '9:16': (576, 1024), '1:1': (1024, 1024),
    # 1344x768 is 7:4, 1.6% narrower than 16:9: its own key and pixels, never relabelled 16:9.
    '7:4': (1344, 768),
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
# The edit lane's thresholds, from its own measurements (see `reference_limits`). They are the
# memory at which the *reference render* stops fitting, not the text render's: a job carries N
# encoded references and a concatenated sequence on top of the same weights.
#
# Measured 2026-10-26 on one 32 GiB M2 Max, mflux 0.23.0, 4 steps, 1024 canvas, references at
# 1024 px (qualification/evidence/image-edit-2026-10-26.jsonl): `default` peaks at 12.0 GB and has
# only been run on a 32 GiB host, so it is offered from 32 GiB up; `bounded-staged` peaks at
# 5.76 GB and ran 121 s -> 101 s faster than `default` on this host, so it is the configuration
# for 12-32 GiB rather than a fallback; `bounded-streamed` peaks at 5.69 GB for one reference and
# 7.47 GB for two. That second figure is why 8 GiB does not get the lane: 7.5 GB of working set
# leaves no room for macOS on an 8 GiB Mac, so the published floor is 12 GiB (measured peak plus
# the OS margin the fleet's own rows use) and an 8 GiB Mac is refused instead of swapping.
EDIT_BOUNDED_BELOW_BYTES = 32 * 1024 ** 3
EDIT_STREAM_BELOW_BYTES = 12_000_000_000
# Qwen Image 2.1 Turbo's edit lane is a different shape: its unbounded configuration peaks at
# 24.2 GB and pushed a 32 GiB host into 9.5 GB of swap with two references, while the bounded one
# peaks at 12.8 GB and is no slower (183 s against 192 s at two references). So every Mac below
# the next Apple tier above the host it failed on takes the bounded configuration, and the
# unbounded one is published without a floor until a larger host is measured.
QWEN_EDIT_BOUNDED_BELOW_BYTES = 48 * 1024 ** 3


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
    # The engine's own geometry bound, stated once on the row that owns it: every published
    # preset (and every request `validate_request` queues) must be a positive multiple of this.
    # None means the engine states no such bound here — a number this row did not get from its
    # engine is never guessed. Published in `lane_contract` so a consumer reads the same figure
    # instead of inventing one.
    dimension_multiple: int | None = None
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
    # Seconds per step on each measured chip (`machdep.cpu.brand_string`): a stored run's whole wall
    # time divided by its steps, so load and decode are inside the figure. Read by `job_deadline_s`.
    seconds_per_step: dict | None = None
    # The class that runs this model's *reference/edit* lane, when it has one. A second class on
    # one row, never a second row: both read the same installed package (`package_id`), and
    # `Flux2KleinEdit` with no references behaves like text-to-image but encodes a blank negative
    # prompt, so swapping the shipping class would silently change the qualified txt2img lane.
    # Empty means this model takes no reference images at all.
    edit_model_class: str = ''
    # What the edit lane accepts, in the numbers that were actually measured for it:
    # ``{'min', 'max', 'max_dimension', 'modes': {...}, 'measured_on', 'source'}``. Each entry
    # of ``modes`` is one configuration's measured block, keyed by the name
    # `plan_configuration` gives that configuration (`default`, `bounded-staged`,
    # `bounded-streamed`): ``{'measured': [...], 'peak_gb', 'swap_used_mb',
    # 'min_total_memory_gb', 'min_free_memory_gb', 'timeout_floor_s'}``. None means the model
    # has no edit lane, and `validate_request` then refuses `reference_images` in the same words
    # it always has.
    reference_limits: dict | None = None
    # The edit lane's *own* memory tiers (owner, 2026-10-26: "give each edit mode its own memory
    # tiers in its MemoryPolicy, the same way Klein txt2img does"). A reference render carries
    # more than a text render - the reference encode and a concatenated sequence - so the
    # thresholds where a configuration would swap are not the txt2img lane's, and one policy
    # cannot answer for both. Same knobs, same `apply_memory_plan`, chosen for `lane='edit'`.
    # None: the lane falls back to the model's own policy.
    reference_memory: MemoryPolicy | None = None


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
            'min_seed': -1, 'max_seed': 2**32 - 1, 'max_prompt': 1900},
    # Free memory needed before start; owner chose 2.8 GB on 2026-10-02 for headroom - measured 8 GB M1+M2 runs started cleanly from 3.76 GB free
    # (3.76-4.14 GB free; peak 2.16-2.68 GB; ~0 swap). This is the free memory a job needs to begin, NOT its peak. This row is the one home for
    # that number: the Hub reads it through `workload_policy.required_free_memory_gb` and keeps no copy of its own, so an edit here is the edit
    # (the operator's override on the Models screen is the only other number, and it wins).
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
    # 105.94 s / 4 steps, the row above.
    seconds_per_step={'Apple M4': 26.5},
    # Reference-to-image (FLUX.2 Klein 4B MLX 4-bit multi-reference edit, owner-approved
    # 2026-10-26). Same installed package and revision as the txt2img row above; a second class,
    # not a second row. Every number below was measured through this worker on the host named in
    # `measured_on`, and the receipt is `reference_limits['source']` - see
    # `qualification/evidence/image-edit-2026-10-26.jsonl` and `docs/lane-execution-contract.md`.
    #
    # The lane runs the same three configurations the txt2img lane already has (owner, 2026-10-26:
    # "make it use far less memory, the way text-to-image already does, so 8 GB and 16 GB Macs can
    # qualify too. The owner explicitly accepts slower generation for this"), each with its own
    # measured floor and runtime: below the default floor the job is forced into the bounded
    # configuration (`memory_plan(lane='edit')`), so an 8 GB Mac never attempts the 14 GB
    # configuration and a 32 GB Mac keeps the fast one.
    edit_model_class=('mflux.models.flux2.variants.edit.flux2_klein_edit:'
                      'Flux2KleinEdit'),
    # The edit lane's own tiers (owner, 2026-10-26). A reference render carries more than a text
    # render - N references are VAE-encoded and concatenated into the sequence - so the point
    # where the unbounded configuration would swap is not the txt2img lane's, and this policy is
    # the lane's own answer rather than a policy shared with a lane it was not measured on.
    # Thresholds come from `reference_limits['modes']`'s measurements, never a formula.
    reference_memory=MemoryPolicy(
        low_ram_below_bytes=EDIT_BOUNDED_BELOW_BYTES,
        low_ram_cache_limit_gb=BOUNDED_CACHE_LIMIT_GB, bounded_vae_decode=True,
        stream_below_bytes=EDIT_STREAM_BELOW_BYTES,
        weight_definition=('mflux.models.flux2.weights.flux2_weight_definition:'
                           'Flux2KleinWeightDefinition'),
        streamed_components=(('text_encoder', ('layers',), False),
                             ('transformer', ('transformer_blocks',
                                              'single_transformer_blocks'), True))),
    reference_limits={
        # `max` is the engine's cap, not a measurement (owner, 2026-11-05): the maker documents
        # four references for FLUX.2 Klein 4-bit's edit conditioning, and mflux's flux2 edit lane
        # exposes no count constant (only `MAX_REFERENCE_AREA`), so four is published whether or
        # not every count has run. Per-count routing stays deferred until approved rows carry
        # measured floors; execution keeps the existing mode policy unchanged.
        'min': 1,
        'max': 4,
        'max_dimension': 1024,
        'modes': {
            # The model's own configuration: every component resident at once. 12.0 GB peak.
            # The rows are the batch taken with the host quietest (load 7-8); earlier runs under
            # heavier load (a full Hub test gate in parallel) are in the receipt with their own
            # host facts, and are not published as this configuration's cost.
            'default': {
                'measured': [
                    {'references': 1, 'reference_pixels': 1024, 'seconds': 98.03,
                     'peak_gb': 12.01, 'swap_delta_mb': 3091.2},
                    {'references': 2, 'reference_pixels': 1024, 'seconds': 151.56,
                     'peak_gb': 12.02, 'swap_delta_mb': -467.1},
                ],
                'peak_gb': 12.02,
                # Swap is published as measured, and it is not zero: every 1-reference run of this
                # configuration grew this host's swap by 3.1-4.6 GB while other applications were
                # resident, and every 2-reference run reclaimed it. The peak stayed at 12.0 GB
                # with 18-24 GB free, so this is macOS paging other processes' idle pages during
                # the render, not the render exhausting memory - but the figure is the receipt's,
                # not rounded to zero.
                'swap_used_mb': 4639.2,
                # The only host this configuration has run on.
                'min_total_memory_gb': 32.0,
                # Peak + 2 GB, the fleet's own start-guard rule.
                'min_free_memory_gb': 14.0,
                'timeout_floor_s': 900.0,
            },
            # Staged hooks + the bounded FLUX.2 decode, no streaming. 5.76 GB peak, and faster
            # than `default` on this host (101 s vs 182 s for two references).
            'bounded-staged': {
                'measured': [
                    {'references': 1, 'reference_pixels': 1024, 'seconds': 72.39,
                     'peak_gb': 5.69, 'swap_delta_mb': -856.6},
                    {'references': 2, 'reference_pixels': 1024, 'seconds': 101.24,
                     'peak_gb': 5.76, 'swap_delta_mb': -128.1},
                ],
                'peak_gb': 5.76,
                'swap_used_mb': 0.0,
                'min_total_memory_gb': 12.0,
                'min_free_memory_gb': 8.0,
                'timeout_floor_s': 900.0,
            },
            # The 8 GB tier: staged + the blocks streamed one at a time + bounded decode. One
            # reference peaks at 5.69 GB; two peak at 7.47 GB, which is why an 8 GiB Mac is
            # refused rather than offered this configuration.
            'bounded-streamed': {
                'measured': [
                    {'references': 1, 'reference_pixels': 1024, 'seconds': 69.13,
                     'peak_gb': 5.69, 'swap_delta_mb': -48.0},
                    {'references': 2, 'reference_pixels': 1024, 'seconds': 105.56,
                     'peak_gb': 7.47, 'swap_delta_mb': -64.0},
                ],
                'peak_gb': 7.47,
                'swap_used_mb': 0.0,
                'min_total_memory_gb': 12.0,
                'min_free_memory_gb': 9.5,
                'timeout_floor_s': 900.0,
            },
        },
        'measured_on': '32 GiB M2 Max (Mac14,13), mflux 0.23.0, 4-bit pack',
        'source': 'qualification/evidence/image-edit-2026-10-26.jsonl',
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
            'min_seed': -1, 'max_seed': 2**32 - 1, 'max_prompt': 2000},
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
    # The slower (low-RAM) row above: 236 s / 9 steps.
    seconds_per_step={'Apple M2 Max': 26.2},
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
    defaults={'aspect_ratio': '1:1', 'resolution': '1K', 'steps': 20,
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
    # Slowest 16-step case, 500.277 s, qualification/evidence/qwen-image-21-20261008-24gb.
    seconds_per_step={'Apple M4': 31.3},
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
    defaults={'aspect_ratio': '1:1', 'resolution': '1K', 'steps': 20,
              'guidance': 1.0, 'seed': -1},
    limits={'min_steps': 1, 'max_steps': 50, 'min_guidance': 0.0, 'max_guidance': 20.0,
            'min_seed': -1, 'max_seed': 2**32 - 1, 'max_prompt': 8000},
    memory=MemoryPolicy(single_mode_reason=(
        'This candidate always uses prompt materialization, cache zero, tiled VAE and eviction.')),
    qualification={
        'fleet_16gb': ('measured 2026-10-08 on the owner\'s physical 16 GiB M4: four 16-step cases, '
                       '462.9-538.3 s, worker-tree peak 5.487-6.032 GB, starts at 9.31-10.65 GB free, '
                       'normal pressure, zero swap growth; '
                       'qualification/evidence/qwen-image-21-20261008-16gb/measurement.json'),
        'fleet_8gb': 'not qualified',
        'packaged_inference': ('proven on 16 GiB M4 and 32 GiB M2 Max: four 1K cases each, normal '
                               'pressure, zero swap growth; '
                               'qualification/evidence/qwen-image-21-20261008-16gb/measurement.json, '
                               'qualification/evidence/qwen-image-21-20261006/measurement.json'),
        'measured_peak_gb': {'default': 6.032},
        'min_free_memory_note': (
            'Measured peak 5.853 GB + 2 GB, rounded up to 0.5 GB (32 GiB M2 Max, 2026-10-06; '
            'qualification/evidence/qwen-image-21-20261006/measurement.json). The 16 GiB M4 run of '
            '2026-10-08 supports it: starts at 9.31-10.65 GB free, largest drop 4.47 GB, never below '
            '6.05 GB, no swap; qualification/evidence/qwen-image-21-20261008-16gb/measurement.json.'),
    },
    size_presets=QWEN_MFLUX_SIZE_PRESETS,
    dimension_multiple=32,
    qualified_minimum_memory_gb=16.0,
    resident_allowed=False,
    # ponytail: measured peak + 2 GB (32 GiB run). The physical 16 GiB M4 run started at 9.31 GB or
    # more and dropped at most 4.47 GB, so an 8.0 GB start keeps ~3.5 GB. Ceiling: no 16 GB start
    # below 9.31 GB is recorded; raise it if a lower-headroom start shows pressure or swap.
    min_free_memory_gb=8.0,
    # Slowest 16-step case per Mac: 212.859 s on the 32 GiB M2 Max (qualification/evidence/
    # qwen-image-21-20261006) and 538.349 s on the 16 GiB M4 (qwen-image-21-20261008-16gb).
    seconds_per_step={'Apple M2 Max': 13.3, 'Apple M4': 33.6},
    guidance_ignored=True,
    below_tier_warning=(
        'This Mac has {memory_gb:.0f} GB of unified memory. Qwen Image 2.1 Low-RAM is '
        'qualified on 16 GB and above; 8 GB is not qualified.'),
)

QWEN_IMAGE_21_TURBO = ImageModel(
    repo='mflux-community/qwen-image-2-1-turbo-mflux-q4',
    revision='bda8951ec6b8cc288a961432fe15c22433521a1d',
    package_id='qwen-image-2-1-turbo-mflux-q4',
    label='Qwen Image 2.1 Turbo Low-RAM MLX 4-bit',
    family='qwen-image-2.1',
    model_class='components.image.app.backend.qwen_turbo:QwenTurbo',
    defaults={'aspect_ratio': '1:1', 'resolution': '1K', 'steps': 8,
              'guidance': 1.0, 'seed': -1},
    limits={'min_steps': 8, 'max_steps': 8, 'min_guidance': 1.0, 'max_guidance': 1.0,
            'min_seed': -1, 'max_seed': 2**32 - 1, 'max_prompt': 8000},
    memory=MemoryPolicy(single_mode_reason=(
        'This model always uses prompt materialization, zero cache, tiled VAE decoding and '
        'encoder/transformer eviction.')),
    qualification={
        'status': 'unqualified',
        'adoption_basis': 'owner decision 2026-10-13',
        'turbo_measurements': 'unmeasured',
        'fleet_16gb': ('owner-adopted for production without separate physical Turbo '
                       'qualification; regular Qwen Image 2.1 Low-RAM MLX 4-bit operates on '
                       'this tier'),
        'fleet_8gb': 'not claimed',
        'packaged_inference': ('owner-adopted for normal production; Turbo-specific peak memory, '
                               'speed and output results remain unmeasured'),
        'min_free_memory_note': (
            'Provisional inherited operational assumption, not a Turbo measurement: use the '
            'regular Qwen Image 2.1 Low-RAM MLX 4-bit free-before-start floor of 8.0 GB until '
            'Turbo-specific operations establish a different floor.'),
    },
    size_presets=QWEN_MFLUX_SIZE_PRESETS,
    dimension_multiple=32,
    qualified_minimum_memory_gb=16.0,
    resident_allowed=False,
    # Provisional owner-adopted operational assumption: regular Qwen Image 2.1 Low-RAM uses
    # 8.0 GB. This is not a Turbo measurement; keep it until Turbo operations establish a floor.
    min_free_memory_gb=8.0,
    guidance_ignored=True,
    # Reference-image edit on the same installed package and revision (owner, 2026-10-26: the
    # second Reference-to-Image model, "so there's variation"). A second class on this row, never
    # a second row: both lanes read the one q4 pack, and its text lane's behaviour - and its
    # unmeasured status - must not change because the pack gained an edit lane.
    edit_model_class='components.image.app.backend.qwen_turbo:QwenTurboEdit',
    # The edit lane's tiers. mflux's levers for this model are the memory saver (text encoder
    # evicted before the loop, transformer after it), the MLX cache cap and the tiled VAE decode -
    # it has no staging hooks, so nothing is streamed. Both measured configurations are published
    # in `reference_limits`; the threshold is where the full configuration was measured to stop
    # fitting, not a formula.
    reference_memory=MemoryPolicy(
        low_ram_below_bytes=QWEN_EDIT_BOUNDED_BELOW_BYTES,
        low_ram_cache_limit_gb=1.0),
    reference_limits={
        # `max` is the engine's cap, read from mflux at runtime through `max_engine_constant`
        # (`MAX_REFERENCES` is 10 today); the 10 below is only the row's own fallback for when
        # mflux is not importable, and RAM never lowers it (owner, 2026-11-05).
        'min': 1,
        'max': 10,
        'max_engine_constant': (
            'mflux.models.qwen21.variants.edit.qwen_image_21_edit:MAX_REFERENCES'),
        'max_dimension': 1024,
        'modes': {
            # The model's own configuration: nothing evicted, nothing tiled. 24.2 GB peak, and a
            # 32 GiB host swapped 9.5 GB under two references, so no floor is published: a Mac
            # that would select this configuration is refused with "no measured ... configuration"
            # until one is measured on a host that holds it.
            'default': {
                'measured': [
                    {'references': 1, 'reference_pixels': 1024, 'seconds': 154.51,
                     'peak_gb': 24.21, 'swap_delta_mb': 557.2},
                    {'references': 2, 'reference_pixels': 1024, 'seconds': 191.82,
                     'peak_gb': 24.23, 'swap_delta_mb': 9568.3},
                ],
                'peak_gb': 24.23,
                'swap_used_mb': 9568.3,
                'min_total_memory_gb': None,
                'min_free_memory_gb': None,
                'timeout_floor_s': None,
            },
            # The bounded one: encoder/transformer evicted, cache capped, VAE decoded in tiles.
            'bounded-staged': {
                'measured': [
                    {'references': 1, 'reference_pixels': 1024, 'seconds': 162.26,
                     'peak_gb': 10.18, 'swap_delta_mb': -4134.5},
                    {'references': 2, 'reference_pixels': 1024, 'seconds': 182.96,
                     'peak_gb': 12.76, 'swap_delta_mb': -4887.2},
                ],
                'peak_gb': 12.76,
                'swap_used_mb': 0.0,
                'min_total_memory_gb': 24.0,
                'min_free_memory_gb': 14.8,
                'timeout_floor_s': 900.0,
            },
        },
        'measured_on': '32 GiB M2 Max (Mac14,13), mflux 0.23.0, 4-bit pack',
        'source': 'qualification/evidence/image-edit-2026-10-26.jsonl',
    },
    below_tier_warning=(
        'This Mac has {memory_gb:.0f} GB of unified memory. Qwen Image 2.1 Turbo Low-RAM '
        'MLX 4-bit is available on 16 GB and above; 8 GB is not supported.'),
)

MODELS: dict[str, ImageModel] = {
    model.repo: model for model in (
        FLUX2_KLEIN, Z_IMAGE_TURBO, QWEN_IMAGE_21, QWEN_IMAGE_21_LOW_RAM,
        QWEN_IMAGE_21_TURBO,
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


# One image job's time limit (owner request 2026-10-08: "make the timeout scale with steps and mac
# speed"): 120 s + 1.5 x steps x this Mac's seconds per step, clamped to [900 s, 7,200 s]. The worker
# states it on the job (`deadline_s`), the Hub copies it onto the batch item, the native leaf is
# stopped at it and the Gateway polls to it (never below the lane's 1,800 s): one number, one owner.
#
# Seconds per step, in order: this Mac's own median over its last 20 finished jobs of this model
# (3 or more; `jobs.json` keeps 90 days); else the model's reading for this chip; else twice the
# model's slowest reading, so an unmeasured Mac (an M1) is allowed half the slowest measured speed.
#
# Steps only, not pixels: every preset is 0.4-1.3x the pixels of the measured 1K scenes, and the
# measured 1280x720 cases took 0.88x the 1024x1024 ones for 0.88x the pixels, so the 1.5x margin
# already covers the largest preset; a pixel term would add a second reference for no change.
IMAGE_DEADLINE_OVERHEAD_S = 120.0
IMAGE_DEADLINE_MARGIN = 1.5
IMAGE_DEADLINE_FLOOR_S = 900.0  # the native leaf's old flat limit; the Gateway still waits >= 1,800 s
# ponytail: one flat 2 h ceiling (the video lane's and the narration ceiling). Qwen at 50 steps on an
# unmeasured Mac gets ~5,200 s; only 100-step FLUX/Z-Image there, or a very slow own history, reach
# it. Raise it per model if a real job needs longer.
IMAGE_DEADLINE_CEILING_S = 7200.0
UNMEASURED_SLOWDOWN = 2.0
DEADLINE_HISTORY_JOBS = 20
DEADLINE_HISTORY_MIN_JOBS = 3


def job_deadline_s(model: ImageModel, steps, chip: str | None = None, history=(),
                   floor_s: float = IMAGE_DEADLINE_FLOOR_S) -> float:
    """Seconds one job of `steps` may run on this Mac; `history` is its own past seconds per step.

    `floor_s` is the lane's own floor where it has a measured, slower configuration (the edit
    lane's bounded mode), so a first job on a small Mac is not given the general floor and killed
    mid-render; it can only raise the 900 s base, never lower it below the ceiling clamp.
    """
    recent = list(history)[-DEADLINE_HISTORY_JOBS:]
    if len(recent) >= DEADLINE_HISTORY_MIN_JOBS:
        per_step = statistics.median(recent)
    else:
        readings = model.seconds_per_step or {}
        per_step = readings.get(chip) or UNMEASURED_SLOWDOWN * max(readings.values(), default=0.0)
    seconds = IMAGE_DEADLINE_OVERHEAD_S + IMAGE_DEADLINE_MARGIN * int(steps or 0) * per_step
    return round(min(IMAGE_DEADLINE_CEILING_S,
                     max(IMAGE_DEADLINE_FLOOR_S, floor_s, seconds)), 1)


@lru_cache(maxsize=1)
def host_chip() -> str | None:
    """This Mac's chip as `sysctl` names it ("Apple M4"), or None when it cannot be read."""
    try:
        return subprocess.check_output(['/usr/sbin/sysctl', '-n', 'machdep.cpu.brand_string'],
                                       text=True, timeout=3).strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _under_memory_pressure(level: int = 2) -> bool:
    """True when macOS reports memory pressure at or above ``level`` (``kern.memorystatus_vm_pressure_level``
    2 warning, 4 critical; 1 is normal). A resident model is not reused into a Mac that is already
    short of memory, and 4 is the hard stop on every image job (owner, 2026-11-05) whatever any
    swap allowance says. An unreadable level is not pressure: the idle window and the Hub's
    release are the other two guards, and a host that cannot be read must not be reported as
    pressure it did not show."""
    try:
        reported = int(subprocess.check_output(['/usr/sbin/sysctl', '-n', 'kern.memorystatus_vm_pressure_level'],
                                            text=True, timeout=3).strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return False
    return reported >= level


def lane_policy(model: ImageModel, lane: str = 'txt2img') -> MemoryPolicy:
    """The :class:`MemoryPolicy` that governs one lane of this model.

    The edit lane has its own tiers when the row declares them (``reference_memory``), because a
    reference render costs more than a text render and the threshold where the full configuration
    would swap is the lane's own measured fact. A row with no edit-specific policy uses its
    txt2img one: one policy answering for both is what the owner overruled on 2026-10-26.
    """
    if lane == 'edit' and model.reference_memory is not None:
        return model.reference_memory
    return model.memory


def plan_configuration(plan: dict) -> str:
    """The name of the configuration a plan actually runs.

    The same four names the txt2img lane publishes (``memory_mode`` in a measured row) plus the
    bounded split. They are also the keys of an edit row's ``reference_limits['modes']`` and the
    string a job records as ``reference_mode``, so the configuration a job ran, the numbers
    published for it and the measurement it came from cannot drift apart.
    """
    if plan.get('stream'):
        return 'bounded-streamed'
    if plan.get('low_ram'):
        return 'bounded-staged'
    if plan.get('lean'):
        return 'lean'
    return 'default'


def reference_mode(model: ImageModel, total_memory_bytes: int,
                   selected: str = DEFAULT_LOADING_MODE) -> str:
    """Which configuration this Mac runs for the edit lane under the machine's setting.

    Derived, never decided separately: it is :func:`plan_configuration` of the very plan
    :func:`memory_plan` will hand `apply_memory_plan` for ``lane='edit'``, so the floor this Mac
    publishes for a reference job and the configuration it executes are one answer.

    * ``bounded-streamed`` — below ``stream_below_bytes`` (the 8 GB tier): staged hooks, block
      streaming and the bounded VAE decode;
    * ``bounded-staged`` — below ``low_ram_below_bytes`` (the 16 GB tier): staged hooks and the
      bounded VAE decode, no streaming;
    * ``lean`` — between there and ``lean_below_bytes`` where the row declares one;
    * ``default`` — from the last threshold up: the configuration the model shipped with.
    """
    return plan_configuration(memory_plan(model, total_memory_bytes, selected, lane='edit'))


def reference_mode_values(model: ImageModel, mode: str) -> dict:
    """The measured numbers for one edit configuration (``{}`` when none was measured)."""
    modes = (model.reference_limits or {}).get('modes') or {}
    values = modes.get(mode)
    return values if isinstance(values, dict) else {}


def reference_max(model: ImageModel) -> int:
    """The engine's own cap on reference images for this model's edit lane.

    A row may declare ``max_engine_constant`` (``module:ATTRIBUTE``); the engine's number wins
    over the row's fallback, so a future model inherits its engine's cap with no edit here.
    Where the engine states no constant, this project's existing row supplies a validation
    ceiling. FLUX.2 Klein 4-bit's four is an owner limit, not a proved intrinsic engine bound.
    """
    limits = model.reference_limits or {}
    constant = limits.get('max_engine_constant')
    if isinstance(constant, str) and constant:
        module_name, _, attribute = constant.partition(':')
        try:
            value = getattr(importlib.import_module(module_name), attribute, None)
        except Exception:  # noqa: BLE001 - an absent engine falls back to the row's own number
            value = None
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    return int(limits['max'])


# ── the per-node reference limits table (owner, 2026-11-05: caps are data, not constants) ────
# `reference-limits.json`, tracked beside this file and shipped with it (the release stager copies
# every tracked file under components/image/). One row per model x job mode x reference count,
# one cell per physical tier; every consumer - `validate_request`, `lane_contract` (so also the
# Hub/Gateway model-package export, which carries the contract verbatim), `_reference_parameter`,
# the multipart early refusal and `edit_lane` - reads it through this one resolver, so a cap can
# never be published one way and enforced another. The engine's own intrinsic cap stays separate
# (`reference_max`): the table's `max_reference_images` is the effective cap, clamped to it.
REFERENCE_LIMITS_PATH = Path(__file__).with_name('reference-limits.json')


@lru_cache(maxsize=1)
def reference_limit_table() -> dict:
    """The whole published table, loaded once from `reference-limits.json`."""
    with REFERENCE_LIMITS_PATH.open() as stream:
        table = json.load(stream)
    if table.get('schema') != 'studiohub.image-reference-limits.v1':
        raise ValueError(f'{REFERENCE_LIMITS_PATH.name} is not a studiohub image reference table')
    return table


def reference_tier(total_memory_bytes: int | None) -> str:
    """The physical band this host's unified memory falls in (``'8' | '16' | '24' | '32' | '<8'``).

    Fleet telemetry is decimal GB; the band a Mac belongs to is its binary GiB rounded the same
    way the fleet's rows round it, then placed in the first band it clears.
    """
    gib = round((total_memory_bytes or 0) / 1024 ** 3)
    bands = sorted({int(tier) for row in reference_limit_table()['rows']
                    for tier in row.get('tiers', {})}, reverse=True)
    for floor in bands:
        if gib >= floor:
            return str(floor)
    return '<8'


def reference_intrinsic_max(model: ImageModel) -> int | None:
    """The engine's **known** intrinsic cap from the published table, or None when unknown.

    A row's own ``max`` is an owner limit, never an engine bound, so it is deliberately not a
    fallback here: an unknown intrinsic cap must not become the old owner number.
    """
    entry = reference_limit_table().get('engine_intrinsic_cap', {}).get(model.package_id) or {}
    maximum = entry.get('max')
    if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 0:
        return None
    return maximum


def reference_policy_row(model: ImageModel, mode: str, count: int | None = None) -> dict | None:
    """The published row for this model, job mode and exact reference count."""
    return next((row for row in reference_limit_table()['rows']
                 if row.get('model_id') == model.repo and row.get('mode') == mode
                 and row.get('reference_count') == count), None)


def effective_reference_policy(model: ImageModel, mode: str,
                               total_memory_bytes: int | None,
                               count: int | None = None) -> dict:
    """This host's **effective cell** for one model x mode: allowed, cap, allowance and provenance.

    The single admission answer every consumer reads (owner, 2026-11-05). ``allowed`` false means
    the node may not take that work at all; ``max_reference_images`` is 0 then, an honest zero
    rather than a missing number. ``reason`` explains an off cell in the table's own words, and
    ``swap_allowance_gb`` is the decimal-GB host-swap growth the same tier allows from a job's
    start baseline - enforced for a running image job in this release.
    """
    total_memory_bytes = host_memory_bytes() if total_memory_bytes is None else total_memory_bytes
    tier = reference_tier(total_memory_bytes)
    row = reference_policy_row(model, mode, count)
    if row is None and count is not None:
        row = reference_policy_row(model, mode)
    if row is None:
        maximum = reference_max(model) if model.reference_limits else 0
        allowed = mode == 'txt2img' or bool(model.edit_model_class and maximum)
        return {'tier': tier, 'allowed': allowed, 'max_reference_images': maximum,
                'swap_allowance_gb': 0.0, 'source': 'model default',
                'receipt': (model.reference_limits or {}).get('source'), 'tier_found': False,
                'reason': None if allowed else f'{model.label} has no {mode} lane'}
    cell = row.get('tiers', {}).get(tier)
    if not isinstance(cell, dict):
        below = ('; this Mac reports '
                 f'{(total_memory_bytes or 0) / 1e9:.1f} GB of unified memory, below the smallest '
                 'tier the table states (8 GiB)' if tier == '<8' else '')
        return {'tier': tier, 'allowed': False, 'max_reference_images': 0,
                'swap_allowance_gb': 0.0, 'source': 'not measured on this tier',
                'receipt': None, 'tier_found': False,
                'reason': (f'{model.label} {mode} has no {tier} cell in reference-limits.json'
                           + below)}
    allowed = bool(cell.get('allowed'))
    maximum = int(cell.get('max_reference_images') or 0) if allowed else 0
    source = str(cell.get('source') or 'not measured on this tier')
    receipt = cell.get('receipt')
    cap = int(cell.get('max_reference_images') or 0)
    if allowed:
        reason = None
    elif cap > 0:
        # An off count cell with a retained cap is over the lane's cap, not off on the tier.
        reason = (f'{model.label} {mode} takes at most {cap} reference images on the {tier} GiB '
                  f'tier ({source}' + (f'; {receipt}' if receipt else '') + ')')
    else:
        reason = (f'{model.label} {mode} is off on the {tier} GiB tier ({source}'
                  + (f'; {receipt}' if receipt else '') + ')')
    return {'tier': tier, 'allowed': allowed, 'max_reference_images': maximum,
            'swap_allowance_gb': float(cell.get('swap_allowance_gb') or 0),
            'source': source, 'receipt': receipt, 'tier_found': True, 'reason': reason}


def effective_reference_max(model: ImageModel, total_memory_bytes: int | None) -> int:
    """The effective cap this host enforces: the table's cell, clamped to the engine's own cap.

    ``0`` when the cell is off - an off tier takes no references at all, which is also what
    ``supports_reference_images`` publishes. The engine's intrinsic cap (``reference_max``) is
    never raised by the table and never lowers an off cell to a positive number.
    """
    cell = effective_reference_policy(model, 'edit', total_memory_bytes)
    if not cell['allowed']:
        return 0
    return max(0, min(cell['max_reference_images'], reference_max(model)))


def _reference_floor_gb(model: ImageModel, mode: str,
                        total_memory_bytes: int | None,
                        selected: str = DEFAULT_LOADING_MODE) -> float | None:
    """The starting free-before-existing floor for one job, in GB: the mode block's own figure.

    Read from the configuration this host will run (never derived from a peak, owner's MLX
    discipline): edit takes the selected edit mode block's ``min_free_memory_gb``, text-to-image
    takes the model row's. ``None`` means no floor is published and there is nothing to check.
    """
    if mode == 'edit':
        measured = reference_mode_values(model, reference_mode(
            model, int(total_memory_bytes or 0), selected))
        value = measured.get('min_free_memory_gb')
    else:
        value = model.min_free_memory_gb
    return float(value) if isinstance(value, (int, float)) and math.isfinite(value) else None


def _reclaimable_available_gb() -> float | None:
    """This Mac's live reclaimable available memory in decimal GB, or ``None`` when unreadable.

    The approved Gateway metric: `host_memory.available_bytes` (the same figure the fleet's
    available_gb telemetry publishes). An unreadable host reads as *not checked*, never as a
    failure and never as a pass dressed up as one.
    """
    try:
        import psutil
        from components.host_memory import available_bytes
        return available_bytes(psutil.virtual_memory()) / 1e9
    except Exception:  # noqa: BLE001 - a host that cannot be read must not block or lie
        return None


def reference_count_facts(model: ImageModel, mode: str, count: int) -> dict | None:
    """One count's row in one configuration, as recorded — ``None`` when nobody ran it.

    Publishes only what the row itself states: the recorded measurement (``seconds``,
    ``peak_gb``, ``swap_delta_mb``) and any floor or timeout the row carries as its own
    adopted fields (``min_total_memory_gb``, ``min_free_memory_gb``, ``timeout_floor_s``).
    A field nobody recorded is ``None`` — this function derives nothing (owner, 2026-11-05):
    floors and deadlines stay the mode blocks' own mode-scoped policy until per-count rows
    carry measured ones, and an unmeasured count is ``None`` here, never a formula's output.
    """
    limits = model.reference_limits or {}
    block = (limits.get('modes') or {}).get(mode)
    if not isinstance(block, dict):
        return None
    row = next((entry for entry in (block.get('measured') or [])
                if isinstance(entry, dict) and entry.get('references') == count), None)
    if row is None:
        return None

    def recorded(key):
        return float(row[key]) if _is_number(row.get(key)) else None

    return {
        'references': count,
        'mode': mode,
        'seconds': recorded('seconds'),
        'peak_gb': recorded('peak_gb'),
        'swap_delta_mb': recorded('swap_delta_mb'),
        'min_total_memory_gb': recorded('min_total_memory_gb'),
        'min_free_memory_gb': recorded('min_free_memory_gb'),
        'timeout_floor_s': recorded('timeout_floor_s'),
        'source': limits.get('source'),
    }


def reference_counts(model: ImageModel) -> dict:
    """Every measured count's recorded row per configuration, keyed by count (``{}`` when none)."""
    modes = (model.reference_limits or {}).get('modes') or {}
    counts = sorted({entry['references']
                     for block in modes.values() if isinstance(block, dict)
                     for entry in (block.get('measured') or [])
                     if isinstance(entry, dict) and _is_number(entry.get('references'))})
    published = {}
    for count in counts:
        published[str(int(count))] = {
            name: reference_count_facts(model, name, int(count))
            for name in modes}
    return published


def memory_plan(model: ImageModel, total_memory_bytes: int,
                mode: str = DEFAULT_LOADING_MODE, lane: str = 'txt2img') -> dict:
    """The mflux memory knobs this host gets for this model, under this machine's setting.

    Read-only projection of the lane's :class:`MemoryPolicy`, the Mac's actual memory and the
    operator's per-machine ``loading_mode`` choice — the same decision the CLI makes from
    ``--low-ram``/``--mlx-cache-limit-gb``/``--vae-tiling``/``--vae-tile-size``, but chosen
    from the host instead of typed.

    ``mode`` is that choice: ``auto`` keeps the policy's own threshold decision, ``low`` forces
    the bounded configuration and ``full`` forces the default. Forcing ``low`` cannot invent a
    bounded configuration a model does not declare — a model with no
    ``low_ram_below_bytes`` has one loading mode, so it stays on the default and its catalog
    entry publishes that the setting does not apply (see :func:`loading_modes`).

    ``lane='edit'`` reads the edit lane's own tiers (:func:`lane_policy`). The setting applies
    there exactly as it does to text-to-image: its thresholds are the edit lane's measured
    ones, so ``auto`` goes bounded on the Macs where the full reference render would swap and
    stays full on the Macs where it would not (owner, 2026-10-26: "use each Mac's capacity, so
    big Macs run at full speed and only small ones go slow").
    """
    if mode not in LOADING_MODES:
        raise ValueError(
            f'unknown memory mode {mode!r}; must be one of: {", ".join(LOADING_MODES)}')
    policy = lane_policy(model, lane)
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
    lean = (not low_ram and mode == MODE_AUTO
            and policy.lean_below_bytes is not None
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
    # A wrapper that holds an mflux model (`QwenLowRam`) names it: the saver has to see the model's
    # own `text_encoder`/`transformer`/`tiling_config`, not the wrapper around it.
    target = getattr(instance, 'memory_target', instance)
    if plan['low_ram'] or plan['vae_tiling']:
        from argparse import Namespace

        from mflux.callbacks.callback_manager import CallbackManager

        register = getattr(CallbackManager, '_register_memory_saver', None)
        if register is None:
            raise RuntimeError('mflux no longer exposes the CLI memory mapping this worker pins')
        args = Namespace(low_ram=plan['low_ram'], mlx_cache_limit_gb=plan['mlx_cache_limit_gb'],
                         vae_tiling=plan['vae_tiling'], vae_tile_size=plan['vae_tile_size'])
        saver = register(args=args, model=target)
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


def _reference_measurements(model: ImageModel, bounded_name: str | None = None) -> dict:
    """The edit lane's own measured peaks and runtimes, in the shape the loading-mode text reads.

    The lane states its numbers per configuration in ``reference_limits['modes']`` (each block's
    ``peak_gb`` is the worst peak over the reference counts and sizes that ran, and ``measured``
    is every row). ``bounded_name`` is the configuration this Mac's *Low RAM* setting would run,
    so the trade the operator is shown is the one this Mac would actually take; it falls back to
    the strongest bounded configuration that was run. A configuration nobody ran states nothing
    and the sentences built from this say so.
    """
    limits = model.reference_limits or {}
    modes = limits.get('modes') or {}

    def one(name: str) -> dict:
        block = modes.get(name) or {}
        rows = [row for row in (block.get('measured') or []) if isinstance(row, dict)]
        seconds = [float(row['seconds']) for row in rows if _is_number(row.get('seconds'))]
        worst = max(rows, key=lambda row: float(row.get('seconds') or 0), default=None)
        scene = None
        if worst and _is_number(worst.get('references')):
            count = int(worst['references'])
            scene = (f"{count} reference{'s' if count != 1 else ''} at "
                     f"{worst.get('reference_pixels')} px")
        return {**_stated_measurement(block.get('peak_gb'), max(seconds) if seconds else None),
                'scene': scene}

    default, staged, streamed = one('default'), one('bounded-staged'), one('bounded-streamed')
    if bounded_name not in ('bounded-staged', 'bounded-streamed'):
        bounded_name = 'bounded-streamed' if streamed['seconds'] is not None else 'bounded-staged'
    bounded = streamed if bounded_name == 'bounded-streamed' else staged
    return {
        DEFAULT_CONFIGURATION: default,
        LOW_RAM_CONFIGURATION: bounded,
        'measured_on': (_stated_text(limits.get('measured_on'))
                        or _stated_text(model.qualification.get('measured_on'))),
        'evidence': 'measured' if default['seconds'] is not None else None,
        'scene': default['scene'] or bounded['scene'],
        'source': limits.get('source'),
        'configuration': bounded_name,
        'note': None,
    }


def _stated_memory_measurements(model: ImageModel, lane: str = 'txt2img',
                                bounded_name: str | None = None) -> dict:
    """Every memory measurement this model itself states, and where each one came from.

    Peaks come from the model's own ``qualification['measured_peak_gb']``; runtimes come from
    its published ``measured_runtime`` rows, matched on the row's own ``memory_mode`` label. The
    edit lane states its own figures per configuration instead (:func:`_reference_measurements`),
    because its peaks and runtimes are not the text lane's. A fact the model does not state is
    ``None`` here, and every sentence built from these facts says so rather than estimating: a
    number the model did not state must never be published.
    """
    if lane == 'edit' and model.reference_limits:
        return _reference_measurements(model, bounded_name)
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
            f"{round(policy.low_ram_below_bytes / 1e9, 1):g} GB, the default at or above it. The trade: "
            f"{_peak_clause(low_ram, default, 'default')}, and "
            f"{_seconds_clause(low_ram, default, 'default')}{location}.")


def loading_modes(model: ImageModel, total_memory_bytes: int, selected: str,
                  lane: str = 'txt2img') -> dict:
    """The machine-wide ``loading_mode`` setting as it applies to one model on this Mac.

    ``selected`` is the operator's persisted choice; ``effective`` is what
    :func:`memory_plan` will actually do for this model's next job on this host, so an
    operator can see that ``auto`` chose the bounded configuration here. ``options`` is empty
    exactly when the lane's own :class:`MemoryPolicy` declares no bounded configuration - the
    lane has one loading mode, and ``reason`` says so in the model's own terms instead of
    offering a control :func:`apply_memory_plan` would discard.

    ``lane='edit'`` publishes the reference lane's answer from its own tiers and its own measured
    numbers, so the catalogue shows the edit lane's trade (and the configuration a reference job
    will run) instead of the text lane's.
    """
    policy = lane_policy(model, lane)
    plan = memory_plan(model, total_memory_bytes, selected, lane=lane)
    supported = policy.low_ram_below_bytes is not None
    configuration = plan_configuration(plan)
    # The trade an operator is shown is the one *this* Mac takes: the bounded configuration its
    # `Low RAM` setting would run, or the one it is running now.
    bounded_name = (configuration if configuration.startswith('bounded')
                    else plan_configuration(memory_plan(model, total_memory_bytes, MODE_LOW,
                                                        lane=lane)))
    measured = _stated_memory_measurements(model, lane, bounded_name)
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
        'lane': lane,
        'supported': supported,
        'selected': plan['requested_mode'],
        'effective': plan['mode'],
        # The configuration name this lane would run, in the same vocabulary the edit lane's
        # measured blocks and a job's `reference_mode` use.
        'configuration': configuration,
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


def model_class(model: ImageModel, mode: str = 'txt2img'):
    """Resolve ``'package.module:Class'`` for the requested model and lane.

    ``mode='edit'`` resolves the row's ``edit_model_class`` - a second class over the same
    installed package - and refuses, naming the model, when the row has none.
    """
    spec = model.edit_model_class if mode == 'edit' else model.model_class
    if mode == 'edit' and not spec:
        raise ValueError(f'{model.label} has no reference-image (edit) lane')
    module_name, _, class_name = spec.partition(':')
    if not module_name or not class_name:
        raise ValueError(f'Invalid model class for {model.repo}: {spec}')
    return getattr(importlib.import_module(module_name), class_name)


def lane_parameters(model: ImageModel, total_memory_bytes: int | None = None) -> list[dict]:
    """The parameters this model's lane publishes, from its own limits and defaults.

    One definition read by two consumers that must agree: :func:`lane_contract` publishes it,
    and ``components.model_defaults`` validates an operator's stored default against exactly
    these bounds and enums. A second copy would be free to disagree with the code that rejects
    the request — which is the thing a published bound exists to prevent.

    ``total_memory_bytes`` is this host's tier, so ``reference_images``' ``max_items`` is the
    same effective cap :func:`validate_request` enforces here (owner, 2026-11-05).

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
         'note': 'a preset size, or explicit width/height that are positive multiples of 32'},
        {'name': 'resolution', 'type': 'string', 'required': False,
         'enum': ['1K'], 'default': '1K'},
        {'name': 'steps', 'type': 'integer', 'required': False,
         'minimum': model.limits['min_steps'], 'maximum': model.limits['max_steps'],
         'default': model.defaults['steps']},
        guidance,
        {'name': 'seed', 'type': 'integer', 'required': False,
         'minimum': model.limits['min_seed'], 'maximum': model.limits['max_seed'],
         'default': model.defaults['seed'], 'note': '-1 draws a random seed'},
        # Only a model with a measured reference lane publishes this, and only that lane accepts
        # it: a parameter the validator refuses must not appear in the contract, and the reverse
        # is the same rule - a lane that takes references must say how many and of what.
        *_reference_parameter(model, total_memory_bytes),
        # A per-job choice, never an operator default (``model_defaults.editable`` skips
        # ``request_only``): omitting it always means the PNG.
        {'name': 'output_format', 'type': 'string', 'required': False,
         'enum': list(OUTPUT_FORMATS), 'request_only': True,
         'note': ('omit for the PNG mflux wrote; jpeg delivers JPEG quality 90, 4:2:0, '
                  'progressive (about 6.6x smaller) and falls back to the PNG if encoding fails')},
    ]


def _reference_parameter(model: ImageModel, total_memory_bytes: int | None = None) -> list[dict]:
    """The edit lane's ``reference_images`` parameter, or nothing when it cannot be accepted here.

    ``min_items``/``max_items`` come from the same resolver ``validate_request`` enforces: the
    table's effective cell for this host's tier, clamped to the engine's cap. On a tier whose cell
    is off the lane refuses every reference here, so the parameter is omitted entirely — a contract
    must never offer a parameter the validator rejects (owner, 2026-11-05: one resolver, and an
    honest zero rather than an impossible 1–0 window).
    """
    limits = model.reference_limits
    if not limits:
        return []
    maximum = effective_reference_max(model, total_memory_bytes)
    if maximum <= 0:
        return []
    return [{'name': 'reference_images', 'type': 'array', 'required': False,
             'min_items': limits['min'], 'max_items': maximum,
             'media_types': list(REFERENCE_MEDIA_TYPES), 'request_only': True,
             'note': ('character/reference images for the edit lane, in order; each is sent as '
                      f'one multipart "images" part to POST /api/generate/edit, at most '
                      f'{limits["max_dimension"]} px on its longest edge')}]



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


def _reference_paths(raw: dict) -> list[str]:
    """The reference image paths a request names, in order; empty when it names none."""
    value = raw.get('reference_images')
    if value is None:
        return []
    if (not isinstance(value, (list, tuple))
            or any(not isinstance(item, str) or not item.strip() for item in value)):
        raise ValueError('reference_images must be a list of reference image paths')
    return [item.strip() for item in value]


def reference_image_media_type(path) -> tuple[str, str]:
    """``(media_type, suffix)`` for one readable reference image, or ``ValueError`` naming it.

    The bytes decide, not a caller's content type: an image the engine cannot open is refused
    here, before a job is queued, rather than inside a paid render.
    """
    from PIL import UnidentifiedImageError

    try:
        with Image.open(path) as image:
            fmt = (image.format or '').upper()
            image.verify()
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError) as error:
        raise ValueError(f'Reference image {path} is not a readable PNG, JPEG or WebP image: '
                         f'{error}') from error
    media_type = REFERENCE_MEDIA_TYPE_BY_FORMAT.get(fmt)
    if media_type is None:
        raise ValueError(f'Reference image {path} is a {fmt or "unknown"} image; this lane takes '
                         f'{", ".join(REFERENCE_MEDIA_TYPES)}')
    return media_type, REFERENCE_SUFFIX_BY_MEDIA_TYPE[media_type]


_REFERENCE_POLICY_FIELDS = ('model', 'mode', 'node_id', 'physical_tier', 'reference_count',
                            'revision', 'effective')
_REFERENCE_POLICY_EFFECTIVE_FIELDS = ('allowed', 'max_reference_images', 'swap_allowance_gb')
_REFERENCE_POLICY_ABSENT = object()


def reference_policy_admission(model: ImageModel, mode: str, references: list[str],
                               total_memory_bytes: int | None,
                               destination_node_id: str | None,
                               policy: object) -> dict:
    """The Hub's INTERNAL reference_policy snapshot, checked against this destination.

    Present means exact (Gateway ACK 2026-11-07): the model, mode, destination node and physical
    tier must describe this worker and this request, and the effective values must be usable as
    stated. A mismatch is refused by name; only an absent field keeps the old local resolver.
    """
    if not isinstance(policy, dict):
        raise ValueError('reference_policy must be an object')
    missing = [name for name in _REFERENCE_POLICY_FIELDS if name not in policy]
    unknown = sorted(set(policy) - set(_REFERENCE_POLICY_FIELDS))
    if missing or unknown:
        raise ValueError('reference_policy must carry exactly '
                         + ', '.join(_REFERENCE_POLICY_FIELDS)
                         + (f'; missing {", ".join(missing)}' if missing else '')
                         + (f'; unknown {", ".join(unknown)}' if unknown else ''))
    if policy['model'] != model.repo:
        raise ValueError(f'reference_policy is for model {policy["model"]!r}, not {model.repo!r}')
    if policy['mode'] != mode:
        raise ValueError(f'reference_policy is for mode {policy["mode"]!r}, not {mode!r}')
    if not isinstance(destination_node_id, str) or not destination_node_id:
        raise ValueError('reference_policy was supplied but this worker has no destination '
                         'machine identity')
    if policy['node_id'] != destination_node_id:
        raise ValueError(f'reference_policy is for node {policy["node_id"]!r}, not this worker '
                         f'({destination_node_id!r})')
    if (total_memory_bytes is None or isinstance(total_memory_bytes, bool)
            or not isinstance(total_memory_bytes, (int, float))
            or not math.isfinite(total_memory_bytes) or total_memory_bytes <= 0):
        # Missing/invalid host telemetry is an unknown tier, never an invented '<8'.
        raise ValueError('reference_policy physical tier is unknown: this worker could not read '
                         'its total memory')
    tier = reference_tier(total_memory_bytes)
    if policy['physical_tier'] != tier:
        raise ValueError(f'reference_policy is for the {policy["physical_tier"]!r} physical tier; '
                         f'this Mac is {tier!r}')
    count = policy['reference_count']
    if count is not None and (isinstance(count, bool) or not isinstance(count, int) or count < 1):
        raise ValueError('reference_policy reference_count must be null or a positive integer')
    if count is not None and count != len(references):
        raise ValueError(f'reference_policy selects {count} reference images; this request has '
                         f'{len(references)}')
    revision = policy['revision']
    # The revision is the Hub policy state's 64-hex token; this worker has no local copy to
    # compare it with, so format is the only exact check it can make.
    if (not isinstance(revision, str) or len(revision) != 64
            or any(character not in '0123456789abcdef' for character in revision)):
        raise ValueError('reference_policy revision must be 64 lowercase hex characters')
    effective = policy['effective']
    if not isinstance(effective, dict):
        raise ValueError('reference_policy effective must be an object')
    missing = [name for name in _REFERENCE_POLICY_EFFECTIVE_FIELDS if name not in effective]
    unknown = sorted(set(effective) - set(_REFERENCE_POLICY_EFFECTIVE_FIELDS))
    if missing or unknown:
        raise ValueError('reference_policy effective must carry exactly '
                         + ', '.join(_REFERENCE_POLICY_EFFECTIVE_FIELDS)
                         + (f'; missing {", ".join(missing)}' if missing else '')
                         + (f'; unknown {", ".join(unknown)}' if unknown else ''))
    allowed = effective['allowed']
    if type(allowed) is not bool:
        raise ValueError('reference_policy effective.allowed must be true or false, not unknown')
    if not allowed:
        raise ValueError(f'reference_policy refuses {model.label} {mode} on this node')
    maximum = effective['max_reference_images']
    if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 0:
        raise ValueError('reference_policy effective.max_reference_images must be a '
                         'non-negative integer')
    swap = effective['swap_allowance_gb']
    if (swap is None or isinstance(swap, bool) or not isinstance(swap, (int, float))
            or not math.isfinite(swap) or swap < 0):
        raise ValueError('reference_policy effective.swap_allowance_gb must be a finite '
                         'non-negative number; null is unknown')
    if mode == 'edit':
        limits = model.reference_limits or {}
        # Only a known engine bound lowers the Hub cap; an unknown intrinsic stays unknown.
        intrinsic = reference_intrinsic_max(model)
        if intrinsic is not None:
            maximum = min(maximum, intrinsic)
        if len(references) < int(limits.get('min') or 1) or len(references) > maximum:
            raise ValueError(
                f'{model.label} takes between {limits.get("min")} and {maximum} reference '
                f'images; this request had {len(references)}')
    return {'max_reference_images': maximum,
            'swap_allowance_gb': float(swap)}


def _validate_references(model: ImageModel, references: list[str],
                         total_memory_bytes: int | None = None, *,
                         policy: object = _REFERENCE_POLICY_ABSENT,
                         destination_node_id: str | None = None) -> list[str]:
    """Refuse a reference set this **node** cannot take, naming the published figures.

    One admission sentence from one resolver: the table's effective cell for this host's physical
    tier decides whether references are accepted at all and how many (owner, 2026-11-05), clamped
    to the engine's own intrinsic cap - so the count the contract publishes, the count the
    parameter's ``max_items`` states and the count refused here can never disagree. A present
    ``reference_policy`` replaces the local cell with the Hub's exact snapshot; absence keeps it.
    """
    limits = model.reference_limits
    if not limits:
        # The same words this worker has always refused references with, so a model without an
        # edit lane is not quietly offered one by a caller that reuses this validator.
        raise ValueError('Reference images, LoRAs, negative prompt and quantization are unavailable')
    if not model.edit_model_class:
        raise ValueError(f'{model.label} has no reference-image (edit) lane')
    if policy is _REFERENCE_POLICY_ABSENT:
        cell = effective_reference_policy(model, 'edit', total_memory_bytes, len(references))
        if not cell['allowed']:
            raise ValueError(cell['reason'])
        maximum = effective_reference_max(model, total_memory_bytes)
        if not limits['min'] <= len(references) <= maximum:
            raise ValueError(
                f'{model.label} takes between {limits["min"]} and {maximum} reference images; '
                f'this request had {len(references)}')
    else:
        reference_policy_admission(model, 'edit', references, total_memory_bytes,
                                   destination_node_id, policy)
    for entry in references:
        path = Path(entry)
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f'Reference image {entry} is missing or unreadable')
        reference_image_media_type(path)
    return list(references)


def _job_floor_gb(model: ImageModel, mode: str, total_memory_bytes: int | None,
                  selected: str, floor_gb: float | None) -> float | None:
    """The free-before-start floor for one job: the Hub's override, or the mode's own floor.

    Text-to-image takes the override (it wins in ``memory_admission.describe``); edit takes the
    stricter of the override and its own reference floor.
    """
    mode_floor = _reference_floor_gb(model, mode, total_memory_bytes, selected)
    if floor_gb is None:
        return mode_floor
    if mode == 'edit' and mode_floor is not None:
        return max(floor_gb, mode_floor)
    return floor_gb


def _admit_job(model: ImageModel, mode: str, total_memory_bytes: int | None,
               selected: str = DEFAULT_LOADING_MODE,
               floor_gb: float | None = None,
               admission: dict | None = None) -> str | None:
    """Refuse work this node's table cell turns away, or a job starting below its own floor.

    Three checks, in this order, each a reason rather than a guess:

    1. the cell - ``allowed: false`` for this physical tier is the node's hard no;
    2. the starting free floor - the **existing measured free-before-start floor** for the
       configuration this job will run (the mode block's own ``min_free_memory_gb``, or the
       Hub's effective override where it sent one), checked against live reclaimable available
       memory **right before the job starts** (owner's MLX discipline: a floor is never derived
       from a peak, and this is not a new floor);
    3. hard critical memory pressure - an unconditional stop, whatever any allowance says.

    ``swap_allowance_gb`` from the cell (always 0 today) is **enforced for a running job** by
    `_JobMemoryGuard`: host swap growth beyond it, measured from that job's own start baseline,
    stops the job with ``host swap grew beyond allowance`` and its evidence. This pre-flight check
    only reads the cell and the floors - it publishes nothing it does not enforce.
    """
    if admission is None:
        if mode == 'edit':
            cell = effective_reference_policy(model, 'edit', total_memory_bytes)
            if not cell['allowed']:
                return cell['reason']
        elif reference_policy_row(model, mode) is not None:
            # txt2img is gated the same way, but only for a model the table has a row for: a model
            # with no txt2img row (Z-Image, Qwen 2.1) is governed by its existing qualification, not
            # by a table that does not speak about it.
            cell = effective_reference_policy(model, mode, total_memory_bytes)
            if not cell['allowed']:
                return cell['reason']
    # A present Hub policy already admitted this exact request; the local cell is not consulted.
    floor = _job_floor_gb(model, mode, total_memory_bytes, selected, floor_gb)
    available = _reclaimable_available_gb()
    if floor is not None and available is None:
        return f'{model.label}: live available memory could not be checked before starting'
    if floor is not None and available < floor:
        return (f'{model.label} ({mode}) needs {floor:g} GB free before it starts; this Mac has '
                f'{available:.2f} GB reclaimable now')
    if _under_memory_pressure(level=4):
        return (f'{model.label} ({mode}) stopped: macOS reports critical memory pressure '
                f'(hard stop, owner rule 2026-11-05)')
    return None


def validate_request(raw: dict, data_dir=None, mode: str | None = None,
                     total_memory_bytes: int | None = None,
                     destination_node_id: str | None = None) -> dict:
    """One request, resolved against the model it names and this Mac's stored defaults.

    ``data_dir`` is where ``model_defaults.json`` lives (the manager's state directory). A
    parameter the request **omits** takes the value in force for this model — what the operator
    stored, over what the model states — and a parameter the request **supplies** always wins.
    With no ``data_dir`` there are no stored defaults to read, so the answer is exactly what the
    lane states: that is the path every direct caller and the existing suite already pins.

    ``mode`` is ``'txt2img'`` or ``'edit'``. It is resolved from the request itself — a request
    that carries reference images is an edit — and an explicit ``'edit'`` with none is refused by
    the same count check that refuses too few, never quietly turned into text-to-image.

    ``total_memory_bytes`` is this host's unified memory: the physical tier the reference limits
    table keys on. None means "not checked against a tier", which reads as the table's off cell
    rather than as permission.
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
    references = _reference_paths(raw)
    mode = 'edit' if (mode == 'edit' or references) else 'txt2img'
    policy = raw['reference_policy'] if 'reference_policy' in raw else _REFERENCE_POLICY_ABSENT
    if mode == 'edit':
        references = _validate_references(model, references, total_memory_bytes,
                                          policy=policy,
                                          destination_node_id=destination_node_id)
    elif policy is _REFERENCE_POLICY_ABSENT:
        cell = effective_reference_policy(model, mode, total_memory_bytes)
        if not cell['allowed']:
            raise ValueError(cell['reason'])
    else:
        reference_policy_admission(model, mode, [], total_memory_bytes,
                                   destination_node_id, policy)
    if any(raw.get(key) for key in ('image_path', 'lora_names', 'lora_paths',
                                     'negative_prompt', 'quantize')):
        maximum = (effective_reference_max(model, total_memory_bytes)
                   if total_memory_bytes is not None else reference_max(model))
        allowed = (f'; this model takes {model.reference_limits["min"]}-'
                   f'{maximum} reference images'
                   if model.reference_limits else '')
        raise ValueError('LoRAs, negative prompt and quantization are unavailable' + allowed)

    prompt = raw.get('prompt')
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > model.limits['max_prompt']:
        raise ValueError(f'Prompt must contain 1–{model.limits["max_prompt"]} characters')
    presets = size_presets(model)
    ratio = raw.get('aspect_ratio')
    dimensions = (raw.get('width'), raw.get('height'))
    if ratio is None:
        if dimensions == (None, None):
            ratio = defaults['aspect_ratio']
        else:
            ratio = next((name for name, pixels in presets.items()
                          if dimensions == pixels), 'custom')
    if ((ratio not in presets and ratio != 'custom')
            or raw.get('resolution', defaults['resolution']) != '1K'):
        raise ValueError('Choose a qualified 1K aspect ratio')
    width, height = presets.get(ratio, dimensions)
    if dimensions != (None, None):
        matched = next((name for name, pixels in presets.items()
                        if dimensions == pixels), None)
        if matched is not None:
            ratio = matched
        else:
            # The Gateway forwards both pixels verbatim: this is the trust boundary. An explicit
            # custom canvas is admitted as given, never coerced to a preset, rounded, or guessed.
            for name, value in zip(('Width', 'Height'), dimensions):
                if type(value) is not int or value <= 0 or value % 32:
                    raise ValueError(f'{name} {value} must be a positive multiple of 32')
            ratio = 'custom'
        width, height = dimensions
    elif ratio == 'custom':
        raise ValueError('Custom size needs both width and height')
    if model.dimension_multiple:
        # The engine's own bound, stated on the row: a preset that ever regressed to an
        # engine-invalid canvas is refused here, before a job is queued, naming the number —
        # never inside a render that would then fail after the queue had accepted it.
        multiple = model.dimension_multiple
        if width % multiple or height % multiple:
            raise ValueError(
                f'{model.label} takes only dimensions that are positive multiples of '
                f'{multiple}; {width}x{height} is not')
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
    # Echoed only for an edit, so every txt2img request's params are byte-identical to before
    # this lane existed - older callers compare on recovery.
    if mode == 'edit':
        normalized['reference_images'] = references
    # Echoed only when supplied, so the params an older caller compares on recovery are unchanged.
    output_format = raw.get('output_format')
    if output_format is not None:
        if (not isinstance(output_format, str)
                or output_format.strip().lower() not in OUTPUT_FORMATS):
            raise ValueError('output_format must be ' + ' or '.join(OUTPUT_FORMATS))
        normalized['output_format'] = output_format.strip().lower()
    floor_gb = raw.get('memory_floor_gb')
    if floor_gb is not None:
        # The Hub's override: validated once, persisted for both the queue and execution gates.
        if (isinstance(floor_gb, bool) or not isinstance(floor_gb, (int, float))
                or not math.isfinite(floor_gb) or not 0 <= floor_gb <= 512):
            raise ValueError('memory_floor_gb must be a finite number of GB from 0 to 512')
        normalized['memory_floor_gb'] = float(floor_gb)
    return normalized


LANE_CONTRACT_SCHEMA = 'studiohub.lane-contract.v1'


def lane_contract(model: ImageModel, data_dir=None, total_memory_bytes: int | None = None) -> dict:
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
    (``image_path``, ``lora_names``, ``lora_paths``, ``negative_prompt``, ``quantize``) and
    ``width``/``height``, which are either the preset's pixels or an explicit pair that
    ``validate_request`` admits only when both are positive multiples of 32. A contract that offers
    a parameter the lane refuses is worse than one that omits it. ``reference_images`` follows the
    same rule in both directions: a model with a measured edit lane publishes it (and accepts it),
    and one without publishes nothing (and refuses it).

    ``references`` — a top-level block, beside ``parameters`` — is the same statement for the
    scheduler: the multipart field name, the count window, the reference image's pixel ceiling,
    the media types, and the measured cost and memory floor of the edit lane. It is present only
    where the lane exists, so its presence *is* the capability.
    """
    parameters = lane_parameters(model, total_memory_bytes)
    in_force = defaults_in_force(model, data_dir)
    for parameter in parameters:
        if parameter['name'] in in_force:
            parameter['default'] = in_force[parameter['name']]
    contract = {
        'schema': LANE_CONTRACT_SCHEMA,
        'modality': 'image',
        # The same token `capabilities.OPERATION_BY_MODALITY` publishes, so a reader can join the
        # contract to the Hub's operation namespace without a table of its own. The edit lane
        # shares it: `edit` is a capability token, not a second operation name (frozen 2026-10-26).
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
    if model.reference_limits:
        contract['references'] = _reference_contract(
            model, total_memory_bytes,
            selected=loading_mode.current(data_dir) if data_dir is not None else DEFAULT_LOADING_MODE)
    if model.reference_limits:
        # The whole per-node table, beside the effective cell, so a reader (Gateway model-package
        # export, UI) shows one published table instead of writing a second set of native rules
        # (owner, 2026-11-05). The two top-level booleans/integer are the projection
        # `standalone._model_state` picks up: explicit false/0 on a tier whose cell is off, never
        # an absent key, because absent reads as "not stated" downstream.
        cell = effective_reference_policy(model, 'edit', total_memory_bytes)
        contract['supports_reference_images'] = cell['allowed'] and effective_reference_max(
            model, total_memory_bytes) > 0
        contract['max_reference_images'] = effective_reference_max(model, total_memory_bytes)
        contract['reference_limits_table'] = reference_limit_table()
        contract['effective_reference_policy'] = cell
    if model.dimension_multiple:
        # The engine's geometry bound beside the validator that enforces it, so a consumer
        # (catalog, Gateway, native) reads the same figure instead of guessing one.
        contract['dimension_multiple'] = model.dimension_multiple
    for parameter in contract['parameters']:
        if parameter.get('note') is None:
            parameter.pop('note', None)
    if model.measured_runtime:
        contract['runtime_seconds'] = model.measured_runtime
    return contract


def _reference_contract(model: ImageModel, total_memory_bytes: int | None = None,
                        selected: str = DEFAULT_LOADING_MODE) -> dict:
    """The edit lane's own published facts for **this** Mac, from ``model.reference_limits``.

    Read by the Gateway to publish ``min``/``max``/``max_dimension`` and to route a reference job
    only where a location advertises ``edit``; read by the Hub to size an edit item's memory
    admission. The memory floor and the runtime are the ones for the configuration this Mac will
    actually run (``mode``) under its machine-wide setting (``selected``): bounded and slower on a
    small Mac, the model's own on a large one. Every figure here was measured (the row's
    ``source`` names the receipt) — a number this model did not measure is absent, never
    estimated.

    On top of those rows sit this node's **effective** reference facts, all from the one resolver:
    ``available`` (may this node take a reference job at all), ``max_effective`` (the table cell
    clamped to the engine's cap, 0 when off) against ``max`` (the engine's own intrinsic cap,
    which the table never raises), ``tier`` and ``evidence_note`` — the one plain-language string
    a native control decodes and displays beside the mode picker: the effective limit, the
    qualification reason, physical versus inherited tier, the reclaimable-available formula, its
    snapshot, the P10 allowance and sample counts, and the swap allowance with its enforcement
    state.
    """
    limits = model.reference_limits
    mode = reference_mode(model, int(total_memory_bytes or 0), selected)
    measured = reference_mode_values(model, mode)
    cell = effective_reference_policy(model, 'edit', total_memory_bytes)
    effective = effective_reference_max(model, total_memory_bytes)
    return {
        # The multipart part name `POST /api/generate/edit` accepts, one part per reference.
        'field': 'images',
        'min': limits['min'],
        # The engine's cap (owner, 2026-11-05), not the largest count anyone happened to run.
        # It is separate from this node's effective cap below: the engine can take four, and this
        # node's tier may still accept only three (or none).
        'max': effective,
        'max_intrinsic': reference_limit_table().get('engine_intrinsic_cap', {}).get(
            model.package_id, {}).get('max'),
        'limits_table': reference_limit_table(),
        # This node's answer, from reference-limits.json: may it take a reference job, and how
        # many. 0 is an honest zero — an off tier takes no references — never an absent number.
        'available': bool(cell['allowed'] and effective > 0),
        'max_effective': effective,
        'tier': cell['tier'],
        'tier_allowed': cell['allowed'],
        'swap_allowance_gb': cell['swap_allowance_gb'],
        'source': limits.get('source'),
        'policy_source': cell['source'],
        'policy_receipt': cell['receipt'],
        'max_effective_differs_reason': (
            None if effective == reference_max(model) else
            f'effective cap {effective} is this {cell["tier"]} GiB tier\'s reference-limits cell '
            f'({cell["source"]})'),
        'evidence_note': _reference_evidence_note(model, cell, effective),
        'max_dimension': limits['max_dimension'],
        'media_types': list(REFERENCE_MEDIA_TYPES),
        # Reference images ride in the multipart body, never inside the prompt character budget.
        'counts_against_prompt': False,
        # The configuration this Mac runs for the lane, and its measured numbers.
        'mode': mode,
        # Every measured count's own per-configuration floors and timeout (owner, 2026-11-05):
        # the scheduler reads what this Mac would need for the count it is about to send, and a
        # count nobody ran is `null` in every configuration — an unknown, never a number.
        'counts': reference_counts(model),
        'runtime_seconds': {
            'measured': list(measured.get('measured') or []),
            'measured_on': measured.get('measured_on') or limits.get('measured_on'),
            # A configuration nobody ran states no cost and no evidence, in so many keys: the
            # capability gate above already refuses to advertise a lane with no measured floor.
            'evidence': 'measured' if measured.get('measured') else None,
        },
        # Swap is a failure for this lane, so it is published beside the peak. Both are the
        # worst figure over the reference counts and sizes this configuration was measured at;
        # `runtime_seconds.measured` carries every row.
        'peak_gb': measured.get('peak_gb'),
        'swap_used_mb': measured.get('swap_used_mb'),
        'min_total_memory_gb': measured.get('min_total_memory_gb'),
        'min_free_memory_gb': measured.get('min_free_memory_gb'),
        'timeout_floor_s': measured.get('timeout_floor_s'),
    }


def _reference_evidence_note(model: ImageModel, cell: dict, effective: int) -> str:
    """The one string a native control displays: every published fact about this node's limit.

    Decoded by the native mode control (its own format, agreed with the UI lead), so it stays
    plain ``key=value`` pairs separated by ``;`` — no second schema, and nothing in it that the
    table or the measurement receipt does not also say.
    """
    evidence = reference_limit_table().get('evidence') or {}
    tier = cell['tier']
    budget = (evidence.get('budget_bytes') or {}).get(tier)
    sample = (evidence.get('samples') or {}).get(tier)
    sampled = '1 node sampled' if sample == 1 else f'{sample} nodes sampled' if sample else 'not sampled'
    return (f'This Mac: up to {effective} reference images; {cell["source"]}. '
            f'Physical RAM tier: {tier} GB. {cell.get("receipt") or ""}. '
            f'Swap allowance: {cell["swap_allowance_gb"]:g} decimal GB of host swap growth '
            'since the job started. Exceeding it stops the job; critical memory pressure '
            'always stops the job. '
            f'Reclaimable available-memory snapshot: {evidence.get("snapshot")}; {sampled}. '
            f'P10 nearest-rank budget after subtracting 5 MB: {budget} bytes. '
            f'{evidence.get("available_metric")}. {evidence.get("formula")}.')




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


def _host_swap_used_mb() -> float | None:
    """Host swap used right now, in decimal MB; ``None`` when the host cannot be read."""
    try:
        import psutil
        return psutil.swap_memory().used / 1e6
    except Exception:  # noqa: BLE001 - an unreadable host is *not checked*, never a pass
        return None


class _JobMemoryGuard:
    """The two hard stops for a running image job (owner, 2026-11-05), watched from its own thread.

    The inference thread is blocked inside mflux, so it cannot watch itself. While the job runs
    this thread polls at a fixed interval and stops it when either holds:

    * **macOS critical memory pressure** — unconditional, whatever any allowance says;
    * **host swap growth beyond this tier's published allowance** — measured from *this job's own
      start baseline*, never the host's swap total, so the figure is the host's swap growth
      observed during this job's window (host-wide by design: background processes' growth is
      included), not a figure this job's own pages account for. The reason it records is exactly
      ``host swap grew beyond allowance`` with the baseline, the live figure and the allowance as
      its evidence: the allowance is decimal GB (always 0 today, so any growth stops the job), and
      it is **enforced**, not published as if it were;
    * **an unreadable swap monitor** — a missing baseline or live read stops the job with
      ``MEMORY_MONITOR_UNAVAILABLE``: the allowance was not checked, not passed.

    A stop goes through the job's own cancel event — mflux's cancel hook ends the loop — and
    records its own reason and error code in ``manager.hard_stops``, so `_record_failure` reports
    a stop as an error with that code, never as an operator cancel and never as a success.
    """
    interval_s = 1.0

    def __init__(self, manager: 'GenerationManager', job_id: str, model: 'ImageModel',
                 mode: str, swap_allowance_gb: float | None = None):
        self.manager, self.job_id, self.model, self.mode = manager, job_id, model, mode
        self.allowance_gb = (effective_reference_policy(
            model, mode, manager.total_memory_bytes)['swap_allowance_gb']
            if swap_allowance_gb is None else swap_allowance_gb)
        self.baseline_mb = _host_swap_used_mb()

    def run(self, event: threading.Event) -> None:
        while not event.wait(self.interval_s):
            job = self.manager.jobs.get(self.job_id)
            if job is None or job.get('state') != 'running':
                return
            self.check(event)

    def check(self, event: threading.Event) -> None:
        if _under_memory_pressure(level=4):
            self._stop(event, 'MEMORY_PRESSURE_STOPPED',
                       'macOS reports critical memory pressure; job stopped (hard stop)')
            return
        used = _host_swap_used_mb()
        if used is None or self.baseline_mb is None:
            # Not checked is not passed: an unreadable monitor stops the job loudly.
            self._stop(event, 'MEMORY_MONITOR_UNAVAILABLE',
                       'host swap could not be read while this job ran; the published swap '
                       'allowance cannot be verified (baseline '
                       + ('unreadable' if self.baseline_mb is None
                          else f'{self.baseline_mb:.1f} MB') + ')')
            return
        grew_gb = (used - self.baseline_mb) / 1000.0
        if grew_gb > self.allowance_gb:
            self._stop(event, 'SWAP_GROWTH_EXCEEDED',
                       f'host swap grew beyond allowance: {grew_gb:.3f} GB since this job '
                       f'started (job-start baseline {self.baseline_mb:.1f} MB, now {used:.1f} MB, '
                       f'allowance {self.allowance_gb:g} GB)')

    def _stop(self, event: threading.Event, code: str, reason: str) -> None:
        manager = self.manager
        with manager.lock:
            job = manager.jobs.get(self.job_id)
            if job is None or job.get('state') != 'running':
                return
            job['error'], job['error_code'] = reason, code
            manager.hard_stops[self.job_id] = (code, reason)
        event.set()


class GenerationManager:
    def __init__(self, data: Path, model_library: Path, manifest: dict,
                 runtime_sha256: str, machine: str = 'local',
                 manifests: dict[str, dict] | list[dict] | None = None,
                 manifest_errors: dict[str, str] | None = None,
                 total_memory_bytes: int | None = None,
                 qualification_package: str | None = None,
                 destination_node_id: str | None = None) -> None:
        self.data = data
        self.outputs = data / 'outputs'
        self.outputs.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.outputs.is_symlink():
            raise ValueError('Image output directory cannot be a symlink')
        # Reference images staged for an edit job, one directory per job. They live beside
        # `outputs` and are deleted the moment the job is terminal: a reference is input, never
        # an artifact, and nothing here outlives the render that used it.
        self.inputs = data / 'inputs'
        self.inputs.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.inputs.is_symlink():
            raise ValueError('Image input directory cannot be a symlink')
        self.state_file = data / 'jobs.json'
        self.library = model_library
        self.manifest = manifest
        self.manifests = self._manifest_index(manifest, manifests)
        self.manifest_errors = dict(manifest_errors or {})
        self.qualification_package = qualification_package
        self.runtime_sha256 = runtime_sha256
        self.machine = machine
        # The fleet destination this worker serves, resolved by the Hub launcher. Kept apart
        # from `machine` (the stable local worker alias) so the existing health contract is
        # unchanged; a missing value stays unknown and refuses a present reference_policy.
        self.destination_node_id = destination_node_id
        self.total_memory_bytes = (host_memory_bytes() if total_memory_bytes is None
                                   else int(total_memory_bytes))
        self.jobs: dict[str, dict] = {}
        self.cancel_events: dict[str, threading.Event] = {}
        # Job ids stopped by `_JobMemoryGuard`, mapped to (code, reason): `_record_failure` and
        # `_deliver` read it so a hard stop keeps its own error and code (owner, 2026-11-05)
        # instead of the cancelled path or the generic engine failure.
        self.hard_stops: dict[str, tuple[str, str]] = {}
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
    def _plan_key(plan: dict, mode: str = 'txt2img') -> tuple:
        # The lane is part of the identity: a loaded text-to-image model may never serve an edit
        # job (a different class, a different call) and the reverse.
        return (mode, plan['mode'], plan['lean'], plan['bounded_vae_decode'],
                plan['mlx_cache_limit_gb'])

    def _keep_resident(self) -> bool:
        from . import memory_policy
        return memory_policy.keep_resident(self)

    def _acquire(self, model: ImageModel, model_folder: str, plan: dict,
                 mode: str = 'txt2img') -> tuple[object, bool]:
        """The model object for this job, and whether it was already loaded."""
        resident, self._resident = self._resident, None
        key = self._plan_key(plan, mode)
        if (resident is not None and resident['repo'] == model.repo
                and resident['key'] == key and plan['resident_ok']
                and not _under_memory_pressure()):
            return resident['instance'], True
        if resident is not None:
            # A different model, a different configuration, or a Mac that is short of memory: the
            # old one goes before the new one loads, never alongside it.
            del resident
            release_mlx_memory()
        return model_class(model, mode)(
            model_path=str(Path(model_folder) / WEIGHTS_SUBDIRECTORY), quantize=None,
            lora_paths=None, lora_scales=None), False

    def _remember(self, repo: str, instance: object, plan: dict,
                  mode: str = 'txt2img') -> None:
        self._resident = {'repo': repo, 'instance': instance,
                          'key': self._plan_key(plan, mode), 'idle_since': time.time()}


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
            if job.get('state') in TERMINAL:
                self._discard_inputs(job)   # a reference never outlives its render
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

    def production_admission(self, repo: str) -> dict:
        """Manifest-owned production policy plus the launcher's isolated qualification grant."""
        from components import model_packages

        model = MODELS.get(repo)
        manifest = self.manifest_for(repo)
        # A missing manifest remains the existing file-availability refusal in ``preflight``.
        # The production gate governs a manifest that is present: legacy manifests without the
        # field keep their established admission, while an explicit false fails closed.
        allowed = manifest is None or model_packages.production_allowed(manifest)
        declared = (manifest or {}).get('qualification') or {}
        target_gb = declared.get('target_unified_memory_gb')
        target_gb = (float(target_gb) if type(target_gb) in (int, float)
                     and math.isfinite(target_gb) and target_gb > 0 else None)
        target_met = target_gb is not None and self.total_memory_bytes >= target_gb * 1e9
        qualification = bool(
            manifest is not None and model is not None and not allowed
            and self.qualification_package == model.package_id and target_met)
        if (manifest is not None and model is not None and not allowed
                and self.qualification_package == model.package_id and not target_met):
            need = f'{target_gb:g} GB' if target_gb is not None else 'a declared physical tier'
            reason = (f'{model.label} qualification requires a physical {need} Mac; '
                      f'this host reports {self.total_memory_bytes / 1e9:.1f} GB')
        else:
            reason = declared.get('reason') or declared.get('memory_evidence') or (
                f'{model.package_id if model else repo} is not qualified for production inference')
        return {'production_allowed': allowed, 'qualification_allowed': qualification,
                'qualification_target_gb': target_gb,
                'allowed': allowed or qualification, 'reason': reason}

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

    def submit(self, raw: dict, mode: str = 'txt2img',
               job_id: str | None = None) -> dict:
        # The worker's own state directory, so a parameter the request omits takes this Mac's
        # stored default for this model rather than the model's own.
        params = validate_request(raw, data_dir=self.data, mode=mode,
                           total_memory_bytes=self.total_memory_bytes,
                           destination_node_id=self.destination_node_id)
        # Resolved here rather than trusted from the caller: a caller that says edit but sends no
        # references was refused above, and a caller that sends references *is* an edit whatever
        # it called itself. The job's own `mode` is that one fact.
        mode = 'edit' if params.get('reference_images') else 'txt2img'
        policy_admission = None
        if 'reference_policy' in raw:
            # `validate_request` already admitted it; this is the same normalisation for the
            # start gate and the running-job swap guard.
            policy_admission = reference_policy_admission(
                MODELS[params['repo']], mode, params.get('reference_images') or [],
                self.total_memory_bytes, self.destination_node_id, raw['reference_policy'])
        admission = self.production_admission(params['repo'])
        if not admission['allowed']:
            raise RuntimeError(f'MODEL_UNQUALIFIED: {admission["reason"]}')
        files = self.preflight(params['repo'])
        if files['state'] != 'files_verified':
            raise RuntimeError(f'MODEL_FILES_UNAVAILABLE: {files.get("reason") or files["state"]}')
        # The start gate, re-read live at accept time (owner, 2026-11-05): the table cell for this
        # tier, the existing measured free-before-start floor against reclaimable available now,
        # and the unconditional critical-pressure stop. An edit job was checked once in
        # `validate_request`; running the same resolver again here means a queued-but-busy moment
        # cannot slip a job past the floor it was admitted under.
        refused = _admit_job(MODELS[params['repo']], mode, self.total_memory_bytes,
                             loading_mode.current(self.data), params.get('memory_floor_gb'),
                             admission=policy_admission)
        if refused:
            raise RuntimeError(refused)
        job_id = job_id or uuid4().hex
        job = {'id': job_id, 'mode': mode, 'params': params, 'state': 'queued',
               'progress': 0.0, 'denoise_step': None, 'denoise_steps': None,
               'created_at': time.time(), 'started_at': None,
               'finished_at': None, 'generated_at': None, 'duration_seconds': None,
               'output_path': None, 'output_url': None, 'sha256': None, 'bytes': None,
               'width': None, 'height': None, 'render_width': None, 'render_height': None,
               'crop': None, 'steps': None, 'resolved_seed': None,
               'media_type': None, 'error': None, 'error_code': None,
               'model_revision': params['model_revision'], 'runtime_revision': self.runtime_sha256,
               'memory_mode': None, 'loading_mode_setting': None, 'peak_mlx_bytes': None,
               'model_reused': None, 'memory_hooks': None,
               'worker_id': 'image', 'machine_id': self.machine, 'acknowledged': False}
        if policy_admission is not None:
            job['reference_admission'] = policy_admission
        if mode == 'edit':
            # The anti-silent-drop proof the Gateway checks: how many references were actually
            # attached, and which bytes, in order. Absolute paths are this worker's own state and
            # are never echoed; the digests are what the caller can compare against what it sent.
            job['reference_count'] = len(params['reference_images'])
            job['reference_digests'] = [sha256(Path(path).read_bytes()).hexdigest()
                                        for path in params['reference_images']]
        with self.lock:
            job['deadline_s'] = self._deadline_s(params, mode)
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

    def _deadline_s(self, params: dict, mode: str = 'txt2img') -> float:
        """This job's limit on this Mac (`job_deadline_s`), from this Mac's own finished jobs.

        Read from jobs of *this lane*: an edit job's seconds per step includes the reference
        encode and is not the same figure, so applying one lane's history to the other's deadline
        would size it from the wrong measurement. An edit job's floor is the floor of the
        configuration this Mac will run — the bounded mode's measured, longer figure on a small
        Mac, so its first render is not killed by the general 900 s floor. Per-count timeout
        activation is deferred until per-count rows carry measured floors (owner, 2026-11-05).
        """
        model = MODELS[params['repo']]
        floor = IMAGE_DEADLINE_FLOOR_S
        if mode == 'edit':
            measured = reference_mode_values(
                model, reference_mode(model, self.total_memory_bytes,
                                      loading_mode.current(self.data)))
            floor = max(floor, float(measured.get('timeout_floor_s') or 0.0))
        done = sorted((job for job in self.jobs.values()
                       if job.get('state') == 'done' and job.get('steps')
                       and job.get('mode', 'txt2img') == mode
                       and (job.get('params') or {}).get('repo') == params['repo']
                       and isinstance(job.get('duration_seconds'), (int, float))),
                      key=lambda job: job.get('finished_at') or 0)
        return job_deadline_s(model, params['steps'], host_chip(),
                              [job['duration_seconds'] / job['steps'] for job in done],
                              floor_s=floor)



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
                    self._discard_inputs(job)
            return dict(job)

    def _record_denoise_step(self, job_id: str, step: int, total: int) -> None:
        """Persist the observed mflux denoise iteration used by bounded cancellation probes."""
        if not isinstance(step, int) or not isinstance(total, int) or not (0 < step <= total):
            return
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None or job.get('state') != 'running':
                return
            job.update(denoise_step=step, denoise_steps=total,
                       progress=max(job.get('progress', 0.0), step / total))
            self._persist()

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

    def _discard_inputs(self, job: dict) -> None:
        """Delete this job's staged reference images, at its terminal state.

        A reference is input, never an artifact: it must not outlive the render that used it. Only
        a directory this worker created (``<data>/inputs/<job id>``) is ever removed, and never by
        parsing a path out of the record - the check is that the parent is exactly one level below
        ``self.inputs``. Best effort by design: a leftover file may not fail a finished job.
        """
        import shutil

        try:
            root = self.inputs.resolve()
        except OSError:
            return
        directories = set()
        for staged in (job.get('params') or {}).get('reference_images') or []:
            try:
                directory = Path(staged).parent.resolve()
            except OSError:
                continue
            if directory.parent == root and directory != root:
                directories.add(directory)
        for directory in directories:
            shutil.rmtree(directory, ignore_errors=True)


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
                    self._discard_inputs(job)
                    # This is the job's own thread finishing, so nothing will look the event up
                    # again. A job cancelled while it was still queued - before it ever reached this
                    # lock - returned here without this line and left one threading.Event in the
                    # dictionary for every such cancellation, for the life of the process.
                    self.cancel_events.pop(job_id, None)
                    return
                job.update(state='running', progress=0.05, started_at=time.time())
                # Hard stop at the door (owner, 2026-11-05): a job never starts into critical
                # memory pressure, whatever any swap allowance says. Checked live, not from the
                # admission that queued it, because pressure is a fact about right now.
                if _under_memory_pressure(level=4):
                    job.update(state='error',
                               error='macOS reports critical memory pressure; job stopped before '
                                     'it started (hard stop)',
                               error_code='MEMORY_PRESSURE_STOPPED', finished_at=time.time())
                    try:
                        self._persist()
                    except Exception:
                        pass
                    self._discard_inputs(job)
                    self.cancel_events.pop(job_id, None)
                    return
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
                mode = job.get('mode', 'txt2img')
                policy_admission = job.get('reference_admission')
                refused = _admit_job(model, mode, self.total_memory_bytes,
                                     loading_mode.current(self.data),
                                     job['params'].get('memory_floor_gb'),
                                     admission=policy_admission)
                if refused:
                    raise RuntimeError(refused)
                # `_JobMemoryGuard` then watches this job for the hard stops, because the inference
                # thread is blocked inside mflux and cannot watch itself. Critical memory pressure
                # stops it unconditionally, and host swap growth beyond this tier's published
                # allowance stops it with that exact reason. Both end the job through its own
                # cancel event - `self.hard_stops` carries the stop's reason and code, and
                # `_record_failure` keeps them, so a stop is never reported as an operator cancel
                # and never as a success. The baseline is this job's own start; the growth is
                # host-wide during this job's window, not a claim all of it belongs to this job.
                guard = _JobMemoryGuard(self, job_id, model, mode,
                                        (policy_admission or {}).get('swap_allowance_gb'))
                if guard.baseline_mb is None:
                    # No baseline means no enforcement; fail before the model loads, not green.
                    reason = ('host swap baseline could not be read before the model loaded; '
                              'the published swap allowance cannot be enforced, so the job '
                              'did not start')
                    guard._stop(event, 'MEMORY_MONITOR_UNAVAILABLE', reason)
                    raise RuntimeError(reason)
                threading.Thread(target=guard.run, args=(event,),
                                 daemon=True, name=f'image-memory-{job_id[:8]}').start()
                plan = memory_plan(model, self.total_memory_bytes,
                                   loading_mode.current(self.data), lane=mode)
                with self.lock:
                    job['resolved_seed'] = seed
                    # What actually ran, and which machine-wide setting asked for it: `auto`
                    # choosing the bounded configuration here is a different fact from an
                    # operator forcing it, and a job receipt should not conflate the two. An edit
                    # job also records the lane's own configuration name - the very plan this job
                    # is about to apply, so a receipt can never claim a tier the run did not take.
                    job['memory_mode'] = plan['mode']
                    job['loading_mode_setting'] = plan['requested_mode']
                    if mode == 'edit':
                        job['reference_mode'] = plan_configuration(plan)
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
                    }, staged, event, timeout_s=job['deadline_s'])
                    peak, reused = None, False
                elif model.engine == IMAGE_ENGINE:
                    start_mlx_measurement()
                    instance, reused = self._acquire(model, model_folder, plan, mode)
                    memory_saver = None
                    keep = False
                    set_step_callback = None
                    try:
                        if not reused:
                            memory_saver = apply_memory_plan(
                                instance, plan, model_dir=Path(model_folder) / WEIGHTS_SUBDIRECTORY)
                        set_cancel_event = getattr(instance, 'set_cancel_event', None)
                        if callable(set_cancel_event):
                            set_cancel_event(event)
                        set_step_callback = getattr(instance, 'set_step_callback', None)
                        if callable(set_step_callback):
                            set_step_callback(lambda step, total: self._record_denoise_step(
                                job_id, step, total))
                        result = instance.generate_image(
                            seed=seed, prompt=job['params']['prompt'],
                            num_inference_steps=job['params']['steps'],
                            height=job['params']['height'], width=job['params']['width'],
                            guidance=job['params']['guidance'],
                            # An edit conditions on the reference tokens; N is arbitrary in
                            # mflux and capped by this model's own published max (validated
                            # before the job was queued, in submission order).
                            **({'image_paths': [Path(path) for path in
                                                job['params']['reference_images']]}
                               if mode == 'edit' else {}))
                        peak = peak_mlx_bytes()
                        keep = bool(model.resident_allowed and plan['resident_ok']
                                    and self._keep_resident())
                    finally:
                        if callable(set_step_callback):
                            set_step_callback(None)
                        set_step_callback = None
                        set_cancel_event = None
                        if keep:
                            # Stays loaded for the next job of this model: only what MLX cached beyond
                            # the weights goes back to macOS. Never kept after a failure - a model that
                            # raised mid-generation is not known to be in a state worth reusing.
                            self._remember(model.repo, instance, plan, mode)
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
                guard.check(event)
                with self.lock:
                    job['model_reused'] = reused
                    # No `load_s` here: mflux reads weights lazily inside the first denoise
                    # step, so `_acquire` time is construction, not the model load.
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
                # Only the new 7:4 row and operator-chosen pixels take the recorded 16:9 trim;
                # every pre-existing preset keeps the exact canvas its row has always published.
                crop = None
                if job['params']['aspect_ratio'] in ('7:4', 'custom'):
                    crop = centred_16x9_crop(dimensions)
                if crop is not None:
                    left, top = crop['offset']
                    out_width, out_height = crop['output']
                    with Image.open(staged) as image:
                        image.crop((left, top, left + out_width, top + out_height)).save(
                            staged, format='PNG')
                    dimensions = (out_width, out_height)
                content = staged.read_bytes()
                if not content.startswith(b'\x89PNG\r\n\x1a\n'):
                    raise RuntimeError('Image engine PNG signature is invalid')
                pending = (content, dimensions, peak, crop)
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
            hard = self.hard_stops.pop(job['id'], None)
            if hard is not None:
                # A `_JobMemoryGuard` stop: the guard already wrote the reason, and it outranks
                # the cancelled path - this is an error with its own code (critical pressure or
                # host swap growth beyond allowance), never an operator cancel and never a
                # success (owner, 2026-11-05).
                code, reason = hard
                job.update(state='error', error=reason, error_code=code)
            elif event.is_set():
                job.update(state='cancelled', error=None, error_code=None)
                job['denoise_cancel_observed'] = isinstance(exc, DenoiseCancelled)
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
            self._discard_inputs(job)

    def _deliver(self, job_id: str, job: dict, event: threading.Event, staged: Path,
                 staged_jpeg: Path, content: bytes, dimensions: tuple, peak,
                 crop: dict | None = None) -> None:
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
            hard = self.hard_stops.pop(job_id, None)
            if hard is not None:
                # Stopped by `_JobMemoryGuard` while delivering: an error with its own code and
                # reason, never a success and never an operator cancel.
                code, reason = hard
                job.update(state='error', error=reason, error_code=code,
                           finished_at=time.time())
            elif event.is_set():
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
                    height=dimensions[1], render_width=job['params'].get('width'),
                    render_height=job['params'].get('height'), crop=crop,
                    steps=job['params']['steps'], peak_mlx_bytes=peak,
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
            self._discard_inputs(job)


def centred_16x9_crop(size: tuple[int, int]) -> dict | None:
    """The centred, no-resampling 16:9 trim of an engine canvas, or None if already exact.

    Recorded on the job rather than hidden: the caller can see the engine's pixels, the pixels it
    received and the offset of the crop. Integer trim only — scaling would change the render.
    """
    width, height = size
    units = min(width // 16, height // 9)
    if units == 0 or (width == units * 16 and height == units * 9):
        return None
    output_width, output_height = units * 16, units * 9
    left, top = (width - output_width) // 2, (height - output_height) // 2
    return {'source': [width, height], 'output': [output_width, output_height],
            'offset': [left, top]}


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
