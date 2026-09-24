"""The Image worker's model registry: one dispatch table, two mflux models.

Every model this worker can run is described once, in :data:`MODELS`: which mflux class
loads it, its own generation defaults and limits, its family/label, and the memory policy
that decides which mflux memory knobs this host needs.  :func:`validate_request`, the job
runner and ``/api/catalog`` all read that table, so adding a model is one entry plus its
manifest.

FLUX.2 Klein's entry is the exact behaviour the worker shipped before this table existed:
4 steps, guidance 1.0, the same family/label/limits, and no mflux memory knobs — it is
qualified and running on the owner's 8 GB Macs, and nothing here changes what it emits.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import importlib
import json
import math
import os
from pathlib import Path
import random
import resource
import subprocess
import threading
import time
from uuid import uuid4

from PIL import Image


# Where a model's weights live inside an installed package folder.
#
# One rule for every model, and deliberately not a per-model field.  A package is installed
# as ``<library>/<package_id>/<revision>/model/`` whatever the publisher's own layout was:
# Z-Image-Turbo's pack root *is* its model directory, so its manifest carries
# ``source_path='transformer/...'`` against ``path='model/transformer/...'`` and
# ``components/model_packages.py:_file_path`` maps that on import.  The worker therefore has
# exactly one load path, and nothing downstream has to know which publisher used which shape.
WEIGHTS_SUBDIRECTORY = 'model'

# The engine this worker serves. It is compared against a manifest's own ``engine`` field in
# exactly one place - ``load_image_manifests`` - which is how a bundled manifest belonging to
# another studio is recognised and reported instead of being loaded. Named once so the fact has
# one home in the component; the Hub's engine table is what routes a manifest here in the first
# place (``components/hub/standalone.py:_ENGINE_ROUTE``).
IMAGE_ENGINE = 'image-mflux'

SIZE_PRESETS = {
    '16:9': (1280, 720), '9:16': (720, 1280), '1:1': (1024, 1024),
    '4:3': (1152, 864), '3:4': (864, 1152), '3:2': (1200, 800),
    '2:3': (800, 1200), '5:4': (1120, 896), '4:5': (896, 1120),
    '21:9': (1680, 720), '2:1': (1408, 704), '1:2': (704, 1408),
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
# 24 GB and up keep the default.  The 8 GB tier is unmeasured while the owner runs it; if it
# turns out to need 256-pixel VAE tiles, ``low_ram_vae_tile_size`` below is the single knob to
# set, and this constant is the single tier boundary to move.
Z_IMAGE_LOW_RAM_BELOW_BYTES = 24_000_000_000


@dataclass(frozen=True)
class MemoryPolicy:
    """The mflux memory knobs one model uses, and when.

    ``low_ram_below_bytes=None`` means this model needs none of them: it is loaded exactly
    the way it always was.  When a host is below the threshold, the CLI's own ``--low-ram``
    mapping is applied (see :func:`apply_memory_plan`).
    """
    low_ram_below_bytes: int | None = None
    # mflux's ``--low-ram`` implies ``--mlx-cache-limit-gb 1``; kept as a field so the two can
    # be separated the way the CLI separates them.
    low_ram_cache_limit_gb: float | None = 1.0
    # ``--low-ram`` already installs mflux's default tiling (512-pixel tiles).  An explicit
    # smaller tile is the extra lever for the 8 GB tier, which is unmeasured.
    low_ram_vae_tile_size: int | None = None


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
    # No memory knobs on purpose.  This model is qualified at 16 GB and has run thousands of
    # generations on the owner's 8 GB Macs; adding a knob would be an unverified change to a
    # verified path.
    memory=MemoryPolicy(),
    qualification={
        'historical_source_workload': '1K, four steps, guidance 1.0 on 16 GB Apple Silicon',
        'packaged_inference': 'pending',
    },
    # Measured on the host in `qualification`'s own terms: this Mac, this engine, this scene.
    # `measured` is always a list of rows — one per configuration that was run — so a lane whose
    # cost depends on a request parameter (music's duration, image's memory mode) publishes the
    # same shape as one whose cost does not.
    measured_runtime={
        'measured': [
            {'seconds': 79.34, 'steps': 4,
             'scene': '1280x720, 4 steps, guidance 1.0, seed 42'},
        ],
        'measured_on': '32 GiB M2 Max, mflux 0.20.0, 4-bit pack',
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
    memory=MemoryPolicy(low_ram_below_bytes=Z_IMAGE_LOW_RAM_BELOW_BYTES),
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
    # (min_unified_memory_gb = 16): reported to the operator with a below-tier warning, and
    # deliberately never a wall, per memory_admission's own doctrine.
    min_free_memory_gb=3.0,
    qualification={
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
    # memory mode changes the answer by 27 % and a scheduler reading one number would set the
    # wrong deadline for whichever mode this host selects.
    measured_runtime={
        'measured': [
            {'seconds': 185.61, 'steps': 9, 'memory_mode': 'default',
             'scene': '1280x720, 9 steps, seed 42'},
            {'seconds': 235.53, 'steps': 9, 'memory_mode': 'low_ram',
             'scene': '1280x720, 9 steps, seed 42'},
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

MODELS: dict[str, ImageModel] = {model.repo: model for model in (FLUX2_KLEIN, Z_IMAGE_TURBO)}


def missing_runtime_model_reason(manifest: dict) -> str:
    """Why an accepted Image package cannot run here, and the file that must learn about it.

    ``MODELS`` is the runtime registry: a manifest alone does not say which mflux class to
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


def memory_plan(model: ImageModel, total_memory_bytes: int) -> dict:
    """The mflux memory knobs this host gets for this model.

    Read-only projection of the model's :class:`MemoryPolicy` and the Mac's actual memory —
    the same decision the CLI makes from ``--low-ram``/``--mlx-cache-limit-gb``/
    ``--vae-tiling``/``--vae-tile-size``, but chosen from the host instead of typed.
    """
    policy = model.memory
    low_ram = bool(
        policy.low_ram_below_bytes is not None
        and 0 < total_memory_bytes < policy.low_ram_below_bytes
    )
    return {
        'mode': 'low-ram' if low_ram else 'default',
        'low_ram': low_ram,
        'mlx_cache_limit_gb': policy.low_ram_cache_limit_gb if low_ram else None,
        'vae_tiling': low_ram,
        'vae_tile_size': policy.low_ram_vae_tile_size if low_ram else None,
        'total_memory_bytes': total_memory_bytes,
        'low_ram_below_bytes': policy.low_ram_below_bytes,
    }


def apply_memory_plan(instance: object, plan: dict) -> object | None:
    """Apply a plan to a loaded mflux model through the CLI's own mapping.

    ``CallbackManager._register_memory_saver`` is the function the CLI uses to turn those four
    flags into model state (``mflux/callbacks/callback_manager.py:82``): it sets
    ``model.tiling_config`` from ``--vae-tiling``/``--vae-tile-size``, and constructs the
    ``MemorySaver`` that caps the MLX cache, evicts the text encoders before the loop and — in
    low-RAM mode with a single seed — the transformer after it (``memory_saver.py:12``).

    This deliberately does not call ``CallbackManager.register_callbacks``: that also registers
    mflux's battery saver, and stopping a job at a battery percentage is a policy the owning app
    has never been asked about.  Everything else the CLI does to memory is here.
    """
    if not plan['low_ram'] and plan['mlx_cache_limit_gb'] is None and not plan['vae_tiling']:
        return None
    from argparse import Namespace

    from mflux.callbacks.callback_manager import CallbackManager

    register = getattr(CallbackManager, '_register_memory_saver', None)
    if register is None:
        raise RuntimeError('mflux no longer exposes the CLI memory mapping this worker pins')
    args = Namespace(low_ram=plan['low_ram'], mlx_cache_limit_gb=plan['mlx_cache_limit_gb'],
                     vae_tiling=plan['vae_tiling'], vae_tile_size=plan['vae_tile_size'])
    return register(args=args, model=instance)


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


def model_class(model: ImageModel):
    """Resolve ``'package.module:Class'`` for the requested model."""
    module_name, _, class_name = model.model_class.partition(':')
    if not module_name or not class_name:
        raise ValueError(f'Invalid model class for {model.repo}: {model.model_class}')
    return getattr(importlib.import_module(module_name), class_name)


def validate_request(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValueError('Image request must be an object')
    model = MODELS.get(raw.get('repo'))
    if model is None:
        raise ValueError('Only a registered image model is available: '
                         + ', '.join(sorted(MODELS)))
    if raw.get('model_revision', model.revision) != model.revision:
        raise ValueError(f'Only the pinned {model.label} revision is available')
    if any(raw.get(key) for key in ('reference_images', 'image_path', 'lora_names',
                                     'lora_paths', 'negative_prompt', 'quantize')):
        raise ValueError('Reference images, LoRAs, negative prompt and quantization are unavailable')
    prompt = raw.get('prompt')
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > model.limits['max_prompt']:
        raise ValueError(f'Prompt must contain 1–{model.limits["max_prompt"]} characters')
    ratio = raw.get('aspect_ratio')
    if ratio is None:
        dimensions = (raw.get('width'), raw.get('height'))
        if dimensions == (None, None):
            ratio = model.defaults['aspect_ratio']
        else:
            ratio = next((name for name, pixels in SIZE_PRESETS.items()
                          if dimensions == pixels), None)
    if ratio not in SIZE_PRESETS or raw.get('resolution', '1K') != '1K':
        raise ValueError('Choose a qualified 1K aspect ratio')
    width, height = SIZE_PRESETS[ratio]
    if type(raw.get('width', width)) is not int or type(raw.get('height', height)) is not int:
        raise ValueError('Width and height must be integer 1K preset pixels')
    if (raw.get('width', width), raw.get('height', height)) != (width, height):
        raise ValueError('Dimensions must match the selected 1K aspect ratio')
    steps = raw.get('steps', model.defaults['steps'])
    guidance = raw.get('guidance', model.defaults['guidance'])
    if type(steps) is not int or not model.limits['min_steps'] <= steps <= model.limits['max_steps']:
        raise ValueError(f'Steps must be between {model.limits["min_steps"]} '
                         f'and {model.limits["max_steps"]}')
    if (type(guidance) not in (int, float) or not math.isfinite(guidance)
            or not model.limits['min_guidance'] <= guidance <= model.limits['max_guidance']):
        raise ValueError(f'Guidance must be finite and between {model.limits["min_guidance"]:g} '
                         f'and {model.limits["max_guidance"]:g}')
    seed = raw.get('seed', model.defaults['seed'])
    if seed is None:
        seed = -1
    if type(seed) is not int or not model.limits['min_seed'] <= seed <= model.limits['max_seed']:
        raise ValueError('Seed must be -1 or a 32-bit unsigned integer')
    return {'repo': model.repo, 'model_revision': model.revision, 'prompt': prompt.strip(),
            'aspect_ratio': ratio, 'resolution': '1K', 'width': width, 'height': height,
            'steps': steps, 'guidance': float(guidance), 'seed': seed}


LANE_CONTRACT_SCHEMA = 'studiohub.lane-contract.v1'


def lane_contract(model: ImageModel) -> dict:
    """The Image lane's dispatch contract for one model.

    What a scheduler needs in order to *ask for* this lane's work: the operation name, the
    parameters it may send and the bounds that will actually be enforced, the artifact it gets
    back, the terminal vocabulary, and what a cancel does not promise. Published beside the
    model's own defaults so a caller never has to read this worker's source.

    Built from ``model.limits`` and ``model.defaults`` rather than from literals, because those
    are the numbers ``validate_request`` actually enforces: a second copy here would be free to
    disagree with the code that rejects the request, and the contract would be the thing that is
    wrong. A new image model therefore publishes a correct contract with no edit in this function.

    Deliberately **not** listed: the parameters ``validate_request`` refuses outright
    (``reference_images``, ``image_path``, ``lora_names``, ``lora_paths``, ``negative_prompt``,
    ``quantize``) and ``width``/``height``, which are derived from ``aspect_ratio`` and refused
    unless they match the preset. A contract that offers a parameter the lane rejects is worse
    than one that omits it.
    """
    contract = {
        'schema': LANE_CONTRACT_SCHEMA,
        'modality': 'image',
        # The same token `capabilities.OPERATION_BY_MODALITY` publishes, so a reader can join the
        # contract to the Hub's operation namespace without a table of its own.
        'operation': 'image.text_to_image',
        'parameters': [
            {'name': 'prompt', 'type': 'string', 'required': True,
             'max_characters': model.limits['max_prompt']},
            {'name': 'aspect_ratio', 'type': 'string', 'required': False,
             'enum': sorted(SIZE_PRESETS), 'default': model.defaults['aspect_ratio'],
             'note': 'width and height follow from the preset; a mismatched pair is refused'},
            {'name': 'resolution', 'type': 'string', 'required': False,
             'enum': ['1K'], 'default': '1K'},
            {'name': 'steps', 'type': 'integer', 'required': False,
             'minimum': model.limits['min_steps'], 'maximum': model.limits['max_steps'],
             'default': model.defaults['steps']},
            {'name': 'guidance', 'type': 'number', 'required': False,
             'minimum': model.limits['min_guidance'], 'maximum': model.limits['max_guidance'],
             'default': model.defaults['guidance'],
             'note': ('accepted and ignored: this model runs without classifier-free guidance'
                      if model.guidance_ignored else None)},
            {'name': 'seed', 'type': 'integer', 'required': False,
             'minimum': model.limits['min_seed'], 'maximum': model.limits['max_seed'],
             'default': model.defaults['seed'], 'note': '-1 draws a random seed'},
        ],
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
            if candidate.get('engine') != IMAGE_ENGINE:
                if required_file:
                    raise ValueError(
                        f'Not an {IMAGE_ENGINE} manifest: {candidate.get("engine")!r}')
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

    def _restore(self) -> None:
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
                uncertain_output = (self.outputs / f'{job_id}.png').exists()
                job.update(state='error',
                    error=('Final PNG exists without a durable completion receipt; reconcile before retry'
                           if uncertain_output else 'Worker restarted before completion'),
                    error_code=('JOB_PERSISTENCE_UNCERTAIN' if uncertain_output
                                else 'WORKER_RESTART_UNCERTAIN'), finished_at=time.time())
            if job.get('state') == 'done':
                path = self.outputs / f'{job_id}.png'
                if not final_matches(job, path):
                    job.update(state='error', error='Final PNG is unavailable after restart',
                               error_code='OUTPUT_UNAVAILABLE', output_path=None, output_url=None)
            self.jobs[job_id] = job
        self._persist()

    def _persist(self) -> None:
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
        return model_packages.preflight(manifest, self.library)

    def packages_without_a_runtime_model(self) -> list[dict]:
        """Every Image manifest this build read that no registered model serves.

        ``preflight`` above is one direction of the reconciliation — a registered model whose
        manifest is missing or revision-drifted, reported for that model only. This is the
        other: ``load_image_manifests`` accepts every ``image-mflux`` manifest the bundle
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
        params = validate_request(raw)
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
               'memory_mode': None, 'peak_mlx_bytes': None,
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
        threading.Thread(target=self._run, args=(job_id, files['folder']), daemon=True,
                         name=f'image-{job_id[:8]}').start()
        return dict(job)

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
            if not final_matches(job, self.outputs / f'{job_id}.png'):
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

    def _run(self, job_id: str, model_folder: str) -> None:
        staged = self.outputs / f'.{job_id}.png'
        final = self.outputs / f'{job_id}.png'
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
                    seed = random.randint(0, 2**32 - 1)
                plan = memory_plan(model, self.total_memory_bytes)
                with self.lock:
                    job['resolved_seed'] = seed
                    job['memory_mode'] = plan['mode']
                    self._persist()
                # Pinned mflux 0.20.0 path, dispatched from the registry. ST-01's verified
                # package adds one model/ prefix to every pack (see WEIGHTS_SUBDIRECTORY).
                memory_saver = None
                instance = model_class(model)(
                    model_path=str(Path(model_folder) / WEIGHTS_SUBDIRECTORY), quantize=None,
                    lora_paths=None, lora_scales=None)
                try:
                    memory_saver = apply_memory_plan(instance, plan)
                    result = instance.generate_image(seed=seed, prompt=job['params']['prompt'],
                        num_inference_steps=job['params']['steps'], height=job['params']['height'],
                        width=job['params']['width'], guidance=job['params']['guidance'])
                    peak = peak_mlx_bytes()
                finally:
                    # Image releases its model after every job. mflux registers its MemorySaver
                    # on the model and the saver holds the model, so without breaking that cycle
                    # the weights would outlive the job until a gc pass.
                    instance.callbacks = None
                    mlx = _loaded_mlx()
                    if memory_saver is not None and mlx is not None:
                        try:
                            mlx.clear_cache()
                        except Exception:  # noqa: BLE001 - release is best effort
                            pass
                    del instance, memory_saver, plan
                if not hasattr(result, 'save'):
                    raise RuntimeError('mflux did not return a GeneratedImage')
                result.save(str(staged), overwrite=True)
                with Image.open(staged) as image:
                    if image.format != 'PNG':
                        raise RuntimeError('mflux did not produce a PNG')
                    image.verify()
                with Image.open(staged) as image:
                    dimensions = image.size
                expected = (job['params']['width'], job['params']['height'])
                if dimensions != expected:
                    raise RuntimeError(f'Generated dimensions {dimensions} differ from requested {expected}')
                content = staged.read_bytes()
                if not content.startswith(b'\x89PNG\r\n\x1a\n'):
                    raise RuntimeError('mflux PNG signature is invalid')
                with self.lock:
                    if event.is_set():
                        job.update(state='cancelled', finished_at=time.time())
                    else:
                        os.replace(staged, final)
                        now = time.time()
                        job.update(state='done', progress=1.0, finished_at=now,
                            generated_at=now, duration_seconds=now-job['started_at'],
                            output_path=str(final),
                            output_url=f'/api/generate/jobs/{job_id}/image',
                            sha256=sha256(content).hexdigest(), bytes=len(content),
                            media_type='image/png', width=dimensions[0],
                            height=dimensions[1], steps=job['params']['steps'],
                            peak_mlx_bytes=peak,
                            peak_process_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
                    try:
                        self._persist()
                    except Exception:
                        job.update(state='error', output_path=None, output_url=None,
                                   sha256=None, bytes=None, media_type=None,
                                   error='Final output could not be persisted; reconcile before retry',
                                   error_code='JOB_PERSISTENCE_UNCERTAIN')
                        try:
                            final.unlink(missing_ok=True)
                            job.update(error='Final output could not be persisted; generation was discarded',
                                       error_code='JOB_PERSISTENCE_FAILED')
                        except OSError:
                            pass
                        raise
            except Exception as exc:
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
            finally:
                staged.unlink(missing_ok=True)
                self.cancel_events.pop(job_id, None)
