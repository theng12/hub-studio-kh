"""The Video worker's model registry: one dispatch table, one engine (LTX-2.3).

Every model this worker can run is described once, in :data:`MODELS`: which engine loads it,
the tasks it accepts, the canvases and durations it will actually answer, the memory
configuration this host needs, and the tier below which the model must not be offered. The
validator, the job runner and ``/api/catalog`` all read that table, so adding a model is one
entry plus its manifest.

**The memory tier is the load-bearing part.** A model whose weights need more unified memory
than the machine has must not be published as eligible: the gateway withholds a row whose
``memory_eligible`` is false (``providers/models.py``), so an 8/16 GB Mac is never offered the
22B model, and the failure is a routing decision instead of a node-side load error. The tier is
published per model (``qualified_minimum_memory_gb``) and evaluated per host, because the same
package is installed on machines of different sizes.

**The engine.**

* ``video-ltx`` runs in this interpreter (``ltx_pipelines_mlx``), exactly the way the
  qualification runner measured it. The pack's own loader is applied **before** the pipeline is
  constructed — the stock ``apply_quantization`` applies ONE bit width to a mixed 2–8-bit pack
  and dies on a shape error deep in ``quantized_matmul``.

Three things the qualification runner learned and this worker must not relearn:

1. **The pack's own loader**, not the runtime default (see above).
2. **``--low-ram-streaming`` does not work on this pack.** It fails before generating anything
   with ``Received 2 parameters not in model: video_to_audio_attn.to_gate_logits.{biases,scales}``.
   The flag is therefore never enabled here, and no loading mode offers it.
3. **A partial local copy reads as a corrupt model.** The manager's preflight is the
   authoritative answer, and it is computed from the model library the operator selected —
   never from a directory this worker guesses at.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import ctypes
import importlib.util
import inspect
import json
import math
import os
from pathlib import Path
import random
import resource
import shutil
import subprocess
import sys
import threading
import time
from uuid import uuid4

from components import model_defaults

WEIGHTS_SUBDIRECTORY = 'model'
TERMINAL = {'done', 'error', 'cancelled'}

# The engines this worker knows. A manifest whose engine is not in here is not this worker's
# (`load_video_manifests` filters on this tuple), which is how a bundled manifest belonging to
# another studio is reported rather than loaded.
VIDEO_ENGINES = ('video-ltx',)
LTX_ENGINE = 'video-ltx'

# A frame count must be ``8k + 1`` for the pack's temporal grid, and both canvas dimensions must
# be multiples of 32 for the DistilledPipeline (the half-res grids it builds internally need 64;
# this pipeline is single-stage, so 32 is the enforced bound).
FRAME_GRID = 8
CANVAS_GRID = 32
DEFAULT_FRAME_RATE = 24

# **The grid a published canvas has to sit on, and the reason the table below has odd ratios.**
# The LTX pack's two-stage pipeline floors each output dimension to a multiple of 64 before it
# generates (``snap_output_dimensions(height, width, two_stage=True)`` in the pinned runtime's
# ``ltx_core_mlx/components/patchifiers.py``): stage 1 runs at half resolution and is upscaled 2x,
# so the *full* dimension must be a multiple of 64 for the half-res grid to be a multiple of 32.
# The floor is silent inside the pack and it changes the artifact, not just the arithmetic:
# measured here 2026-09-27, a request for the published 288p 16:9 canvas came back as 512x256 —
# 2:1, not 16:9 — while the job's own acceptance still said 512x288. The runtime is pinned by
# content (``stage_voice.VIDEO_RUNTIME_SHA256``), so restating its rule here cannot drift out from
# under this table, and `_assert_canvases_are_snap_stable` refuses to import a table that is not a
# fixed point of it.
TWO_STAGE_GRID = 64

# Canvases the lane publishes, keyed by an orientation-free size class and then by aspect ratio.
# Every side is a multiple of TWO_STAGE_GRID and every published (aspect_ratio, resolution) pair
# below is one the validator accepts, so the two fields a caller chooses independently cannot
# disagree by construction. '11:7' 704x448 is the configuration measured on this Mac (97 frames,
# 298.5 s) and is the model's default; the others are exact-ratio canvases inside the pack's stated
# production envelope.
#
# **There is no 16:9 pair, and that is a measurement rather than an omission.** Both sides must be
# multiples of 64, so the smallest exact 16:9 canvas the two-stage pipeline can produce is
# 1024x576 — far outside the published envelope — and every smaller "16:9" is a 2:1 or 3:2 clip
# wearing the wrong label. The classes name the *short* side, so 256p is 448x256 or 256x448 and the
# ratio beside it is the ratio that canvas actually has.
LTX_CANVASES: dict[str, dict[str, tuple[int, int]]] = {
    '256p': {'7:4': (448, 256), '4:7': (256, 448)},
    '448p': {'1:1': (448, 448), '11:7': (704, 448)},
    # **A true 16:9 canvas, measured 2026-09-27.** 1024x576 is the smallest exact 16:9 the
    # two-stage grid can produce (both sides multiples of 64), and it is the shape an episode is
    # actually cut in — the 11:7 canvas above is the qualified one but it is 1.57:1, not 1.78:1.
    # It is published at 2 s only, and not by hand: `canvas_durations` computes the durations from
    # the same token ceiling the validator enforces, and 1024x576 at 4 s is 55,872 tokens — past
    # the 30,000 cliff where this pack produces mesh artifacts. The measurement: 49 frames,
    # 351.6 s, peak MLX Metal 9.36 GB, clean frames (qualification/ltx-video-results-16x9-20260927).
    '576p': {'16:9': (1024, 576)},
}

def _assert_canvases_are_snap_stable(
    table: dict[str, dict[str, tuple[int, int]]], *, grid: int, why: str, name: str
) -> None:
    """Refuse to import a canvas the pack would silently resize.

    A table entry off the pipeline's grid is not a smaller picture or a bigger one — it is a
    *different aspect ratio* than the one advertised, which is how Story Studio's widescreen
    request received a 2:1 clip. Failing at import names the entry and the rule, instead of
    publishing it and discovering it in an artifact.
    """
    for klass, canvases in table.items():
        for ratio, (width, height) in canvases.items():
            if width % grid or height % grid:
                raise ValueError(
                    f'{name} canvas {klass} {ratio} {width}x{height} is not a multiple of '
                    f'{grid} on both sides, so {why} would snap it to '
                    f'{width - width % grid}x{height - height % grid} and the artifact would not '
                    f'be {ratio}. Publish the snapped canvas, or a canvas that is already on the '
                    f'grid.')


_assert_canvases_are_snap_stable(
    LTX_CANVASES, grid=TWO_STAGE_GRID, name='LTX',
    why='the two-stage pipeline')
# The table a model falls back to when it declares none: the LTX one, which is what every model in
# this lane published before the table became per-model.
CANVAS_CLASSES = LTX_CANVASES

# Durations the lane answers, in whole seconds at the published frame rate. 49 and 97 frames are
# 8k+1 at 24 fps; 97 frames at 704x448 is the measured configuration.
DURATIONS_SECONDS = (2, 4)
MAX_PROMPT_CHARACTERS = 4000

# The pack's own quality ceiling, in the units it states it in. LTX's card says production runs
# through 768x768x25f and that at n_lat >= ~30k (768x768x49f) the VAE produces mesh artifacts
# and is unusable. The latent token count is (width/32) * (height/32) * frames; the worker
# computes it from the request and refuses a request that crosses the cliff, rather than
# discovering it in the decode.
MAX_LATENT_TOKENS = 30_000

def canvas_durations(model: 'VideoModel', width: int, height: int) -> list[int]:
    """The published durations this canvas accepts, from the same guard the validator enforces.

    Per canvas rather than one list for all of them, because the two are not the same statement:
    the pack's latent ceiling makes a big canvas expensive at the longer duration, and a contract
    that offers that pair is offering work the lane refuses. Both the contract and
    :func:`validate_request` read this function, so the published pairs and the accepted pairs are
    the same set by construction.
    """
    return [seconds for seconds in DURATIONS_SECONDS
            if latent_load(model, width, height,
                           frames_for(seconds, model.frame_rate, model.frame_grid))
            < model.max_latent_tokens]


def published_canvases(model: 'VideoModel') -> list[dict]:
    """Every canvas this model publishes: the pair, its exact size, and the durations it takes."""
    return [
        {'resolution': klass, 'aspect_ratio': ratio, 'width': width, 'height': height,
         'durations': canvas_durations(model, width, height)}
        for klass, entries in sorted(model.canvases.items())
        for ratio, (width, height) in sorted(entries.items())
    ]


def aspect_ratios(model: 'VideoModel') -> list[str]:
    return sorted({ratio for canvases in model.canvases.values() for ratio in canvases})


def resolutions(model: 'VideoModel') -> list[str]:
    return sorted(model.canvases)


def canvas_for(model: 'VideoModel', aspect_ratio: str, resolution: str) -> tuple[int, int] | None:
    return (model.canvases.get(resolution) or {}).get(aspect_ratio)


def resolution_classes_for(model: 'VideoModel', aspect_ratio: str) -> list[str]:
    return sorted(name for name, canvases in model.canvases.items()
                  if aspect_ratio in canvases)


@dataclass(frozen=True)
class MemoryPolicy:
    """The runtime memory configuration one video model is loaded with on this host.

    ``low_memory`` is the pack's own constructor flag (the qualification run measured with it
    on). ``low_ram_streaming`` is deliberately a field with one allowed value: block streaming
    does not work on the LTX pack, so this worker never enables it — a field is better than a
    comment because it is the thing a future edit would flip.

    """
    low_memory: bool = True
    low_ram_streaming: bool = False
    single_mode_reason: str | None = None


@dataclass(frozen=True)
class VideoModel:
    """One registered video model: how to load it, what it accepts, and where it may run."""
    repo: str
    revision: str
    package_id: str
    label: str
    family: str
    engine: str
    tasks: tuple[str, ...]              # 'image-to-video' and/or 'text-to-video'
    defaults: dict
    limits: dict
    memory: MemoryPolicy
    qualification: dict
    # The fleet tier this model is qualified at. Published per model and evaluated per host:
    # below it the model is *not eligible* on that machine, which is what keeps the gateway
    # from offering 22B weights to an 8/16 GB location.
    qualified_minimum_memory_gb: float
    # Live-free-memory floor for Hub dispatch, where the model's own measurement states one.
    min_free_memory_gb: float | None = None
    # What one item costs where it has been measured. Published as the lane contract's
    # `runtime_seconds`; absent means unmeasured and nothing is estimated.
    measured_runtime: dict | None = None
    # Companion packages the engine loads. The runner resolves their installed folders and
    # passes them as local paths, so an offline node never reaches for the Hub.
    requires: tuple[str, ...] = ()
    # **The four facts that differ per engine, and the reason this lane is model-agnostic rather
    # than LTX-shaped.** Canvases and the frame grid are the model's own (see the tables above);
    # the latent rule is the quantity each card states its cliff in. The defaults are LTX's, so a
    # model that declares none keeps exactly the behaviour this lane published before they existed.
    canvases: dict[str, dict[str, tuple[int, int]]] = field(
        default_factory=lambda: dict(LTX_CANVASES))
    frame_rate: int = DEFAULT_FRAME_RATE
    frame_grid: int = FRAME_GRID
    latent_grid: int = CANVAS_GRID
    latent_temporal_divisor: int = 1
    max_latent_tokens: int = MAX_LATENT_TOKENS
    # False when this build has no runner for the engine. The row is still published, with the
    # reason and `execution_ready: false`, because an absent row is invisible everywhere.
    runnable: bool = True
    unavailable_reason: str | None = None
    # Whether the clip carries its own audio track. Tri-state on purpose: True where it was
    # measured, False only where the model states it, None when nobody said — which is not "no".
    generates_audio: bool | None = None


LTX_23 = VideoModel(
    repo='baa-ai/LTX-2.3-22B-RAM-12GB-MLX',
    revision='d8a9d2f6081c4136130983a0b612962418ddf203',
    package_id='ltx-2-3-22b-ram-12gb-mlx',
    label='LTX-2.3 22B MLX mixed 2–8 bit (RAM-12GB)',
    family='ltx-2-3',
    engine=LTX_ENGINE,
    tasks=('image-to-video', 'text-to-video'),
    # The default canvas is the measured one (704x448, 97 frames, 298.5 s). It used to be
    # `16:9` at `288p`, which the pack floors to 512x256 — so the lane's *default* request was a
    # canvas the lane does not produce, and every caller that omitted both fields received a 2:1
    # clip while the acceptance said 512x288.
    defaults={'mode': 'image-to-video', 'aspect_ratio': '11:7',
              'resolution': '448p', 'duration_seconds': 4,
              'frame_rate': DEFAULT_FRAME_RATE, 'seed': -1},
    limits={'max_prompt': MAX_PROMPT_CHARACTERS, 'min_frames': 9, 'max_frames': 97,
            'min_frame_rate': 24, 'max_frame_rate': 24,
            'min_seed': -1, 'max_seed': 2**32 - 1},
    memory=MemoryPolicy(single_mode_reason=(
        'One loading configuration: the pack is loaded through its own mixed-precision loader '
        'with low_memory on, which is the configuration the qualification run measured. Block '
        'streaming (low_ram_streaming) is unavailable on this pack — it fails before generating '
        'anything on two keys the streaming block does not carry — so no mode offers it.')),
    qualification={
        'measured_on': ('32 GiB M2 Max (Mac14,13), macOS 27.0, mlx 0.32.2, LTX runtime '
                        '3b2e77fd73af5f08961f4436ae979c0579d5deed, pack mixed 2–8 bit'),
        'measured_runs': [{
            'task': 'image-to-video', 'width': 704, 'height': 448, 'frames': 97,
            'frame_rate': 24.0, 'seed': 42, 'wall_seconds': 298.5,
            'output_seconds': 4.0, 'output_bytes': 1800551,
            'peak_mlx_metal_gb': 13.19, 'peak_process_footprint_gb': 16.38,
            'rss_gb': 9.91, 'audio': True,
            'source': ('qualification/ltx-video-results-20260926-32gb-first-success/'
                       'measurement.json and run.log'),
        }, {
            # The true-16:9 canvas. Measured because the 576p pair is published and a canvas
            # nobody ran is a canvas whose runtime is a guess; the 4 s configuration of this
            # canvas is not offered (latent ceiling), so 2 s is the whole of what was measured.
            'task': 'image-to-video', 'width': 1024, 'height': 576, 'frames': 49,
            'frame_rate': 24.0, 'seed': 42, 'wall_seconds': 351.628,
            'output_seconds': 2.04, 'output_bytes': 1403485,
            'peak_mlx_metal_gb': 9.36, 'audio': True,
            'source': ('qualification/ltx-video-results-16x9-20260927/'
                       'measurement.json and run.log'),
        }],
        'memory_evidence': (
            'The 22B pack runs on this 32 GB M2 Max (peak process footprint 16.38 GB). The 16 GB '
            'tier is a vendor claim this measurement contradicts for the 22B model, so the tier '
            'is 24 GB and a 16 GB machine is not offered the model.'),
        'status': 'partially-qualified',
    },
    # Tier: 24 GB. The measured footprint is 16.38 GB on a machine where MLX had no pressure to
    # trim; the vendor's "12 GB RAM" name describes the packed weights, not the process.
    qualified_minimum_memory_gb=24.0,
    # **A start guard, not the footprint. This number was 16.5 and it deadlocked the lane.**
    # Measured 2026-09-26 on this 32 GB Mac, on the first real chain job: the Hub's governor
    # answered `waiting for memory: needs ~16.5GB, ~14.2GB free` and held the item queued while
    # it released every idle sibling model. Available memory (free+inactive+speculative) sits
    # around 13-20 GB on a healthy 32 GB Mac, because macOS fills inactive pages with file
    # cache — and those pages ARE reclaimable, which is exactly what the 16.38 GB footprint is
    # made of. The Image lane recorded this same lesson when it lowered Z-Image's floor from
    # its 7.1 GB footprint to 3.0 GB: a *footprint* is not a *free-memory* requirement, and the
    # Hub answers an unmet free floor with "wait", not "skip", so an over-strict floor waits
    # for memory that is never going to arrive.
    #
    # 8 GB stops a start into a genuinely full machine while leaving the run that was measured
    # here room to begin. What would raise it: a hardware measurement showing a start below
    # 8 GB free swapping through the load on a 24/32 GB Mac.
    min_free_memory_gb=8.0,
    measured_runtime={
        'measured': [{
            'seconds': 298.5, 'task': 'image-to-video', 'frames': 97, 'frame_rate': 24.0,
            'scene': '704x448, 97 frames, seed 42',
            'source': ('qualification/ltx-video-results-20260926-32gb-first-success/'
                       'measurement.json'),
        }, {
            'seconds': 351.628, 'task': 'image-to-video', 'frames': 49, 'frame_rate': 24.0,
            'scene': '1024x576, 49 frames, seed 42',
            'source': ('qualification/ltx-video-results-16x9-20260927/measurement.json'),
        }],
        'measured_on': '32 GiB M2 Max (Mac14,13), mlx 0.32.2, pack mixed 2–8 bit',
        'evidence': 'measured',
    },
    # The text encoder is a second package. The pipeline loads it by its own model id, so the
    # worker resolves the installed companion folder and passes that path — the Hub mirrors
    # companions into the engine cache, and a local path is what keeps that honest offline.
    requires=('gemma-3-12b-it-4bit',),
    canvases=LTX_CANVASES,
    # The measured run produced 4.04 s of output with an audio track; the worker refuses a
    # completion whose MP4 has no audio, so this is a checked claim rather than a hope.
    generates_audio=True,
)

MODELS: dict[str, VideoModel] = {model.repo: model for model in (LTX_23,)}

# The model the worker defaults to when nothing names one. LTX is the one this build can run.
MODEL_ID = LTX_23.repo
MODEL_REVISION = LTX_23.revision
PACKAGE_ID = LTX_23.package_id
DEFAULTS = dict(LTX_23.defaults)


_MUXER_STATE: list = []  # [] = not checked yet; [None] = fine; [reason] = refused


def muxer_problem() -> str | None:
    """Why this worker cannot mux an MP4, or None when it can.

    The LTX pipeline writes the finished clip with an `ffmpeg` subprocess — it decodes the video
    and audio latents itself, then hands raw frames and samples to it. Two separate failures were
    measured on 2026-09-26, and this check is written for both:

    1. **No ffmpeg on PATH at all.** The first through-the-chain run generated the whole clip and
       then died with the pack's own *"ffmpeg not found on PATH. Install it with: brew install
       ffmpeg"*, because the packaged worker's PATH held only its own runtime and `/usr/bin`.
    2. **An ffmpeg with no H.264 encoder.** The app's other ffmpeg (the Voice runtime's, built from
       source with a minimal configuration) answers `-h encoder=libx264` with *"Codec 'libx264'
       is not recognized by FFmpeg"* **while exiting 0** — so "there is an ffmpeg" is not the
       question the pack needs answered. It names `libx264` explicitly, and the output is what
       says whether the build has it.

    Asking here lets the catalog say *why* a model is unready instead of advertising one that
    generates for five minutes and then fails at the last step. The answer is cached: a catalog
    request must not fork a subprocess per model per read.
    """
    if _MUXER_STATE:
        return _MUXER_STATE[0]
    import shutil
    import subprocess

    problem = None
    ffmpeg = shutil.which('ffmpeg')
    if ffmpeg is None:
        problem = ("ffmpeg is not on this worker's PATH, and the LTX pack muxes its MP4 with it; "
                   "the app ships one in the Voice runtime and the packaged worker adds it to PATH")
    else:
        try:
            probe = subprocess.run([ffmpeg, '-hide_banner', '-h', 'encoder=libx264'],
                                   capture_output=True, text=True, timeout=15)
            output = (probe.stdout or '') + (probe.stderr or '')
            if 'Encoder libx264' not in output:
                problem = (f'the ffmpeg on this worker\'s PATH ({ffmpeg}) has no libx264 encoder; '
                           f'the LTX pack muxes its MP4 with libx264 by name. Rebuild the video '
                           f'runtime (packaging/video-runtime/build.sh) so its pinned ffmpeg '
                           f'ships in runtimes/video/bin.')
        except (OSError, subprocess.SubprocessError) as exc:
            problem = f'the ffmpeg on PATH could not be checked for libx264: {exc}'
    _MUXER_STATE.append(problem)
    return problem


def host_memory_bytes() -> int:
    """This Mac's physical memory, or 0 when it cannot be read."""
    try:
        return int(subprocess.check_output(['/usr/sbin/sysctl', '-n', 'hw.memsize'],
                                           text=True, timeout=3).strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    try:
        return os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES')
    except (ValueError, OSError, AttributeError):
        return 0


def physical_footprint_bytes() -> int | None:
    """This process's ``ri_phys_footprint`` — the OS-committed footprint, in bytes.

    ``mx.get_peak_memory()`` counts MLX's Metal allocations only; on macOS those do not appear
    in the process resident set, and the pack's own card uses ``ri_phys`` for exactly that
    reason. This reads the same number from ``libproc``'s ``rusage_info_v4``: a 16-byte uuid,
    then the ``uint64`` fields in order, of which ``ri_phys_footprint`` is the eighth
    (``ri_user_time``, ``ri_system_time``, ``ri_pkg_idle_wkups``, ``ri_interrupt_wkups``,
    ``ri_pageins``, ``ri_wired_size``, ``ri_resident_size``, ``ri_phys_footprint``).

    Returns ``None`` when the platform does not expose it; a worker that cannot measure must
    not report a number.
    """
    try:
        libproc = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
    except OSError:
        return None
    buffer = ctypes.create_string_buffer(4096)
    # RUSAGE_INFO_V4 == 4; unsigned int return, int pid, int flavor, void* buffer.
    result = libproc.proc_pid_rusage(ctypes.c_int(os.getpid()), ctypes.c_int(4),
                                     ctypes.byref(buffer))
    if result != 0:
        return None
    return int.from_bytes(buffer.raw[72:80], 'little')


class FootprintSampler:
    """Samples ``ri_phys_footprint`` while a job runs, and keeps the highest reading.

    A peak has to be observed: ``rusage_info_v4`` reports the footprint now, not its
    high-water mark. The image lane's numbers are quoted from ``mx.get_peak_memory()``; for
    video the tier is a claim about the *process*, so the peak is sampled at 1 Hz and the
    receipt says how it was obtained.
    """

    def __init__(self, interval_s: float = 1.0) -> None:
        self.interval_s = interval_s
        self.peak_bytes: int | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sample, daemon=True,
                                        name='video-footprint')

    def _sample(self) -> None:
        while not self._stop.wait(self.interval_s):
            value = physical_footprint_bytes()
            if value is not None and (self.peak_bytes is None or value > self.peak_bytes):
                self.peak_bytes = value

    def __enter__(self) -> 'FootprintSampler':
        self._thread.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)
        value = physical_footprint_bytes()
        if value is not None and (self.peak_bytes is None or value > self.peak_bytes):
            self.peak_bytes = value


def peak_mlx_bytes() -> int | None:
    """MLX's own Metal high-water mark, after the pipeline has run."""
    import sys
    module = sys.modules.get('mlx.core') or sys.modules.get('mlx')
    getter = getattr(module, 'get_peak_memory', None)
    try:
        return int(getter()) if callable(getter) else None
    except Exception:  # noqa: BLE001 - reporting must never fail a finished generation
        return None


def peak_rss_bytes() -> int | None:
    """The process's maximum resident set, from ``getrusage`` (bytes on macOS)."""
    try:
        return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except (OSError, ValueError):
        return None


# ── the pack's own loader ────────────────────────────────────────────────────

def apply_pack_quantization(model_dir: Path) -> str | None:
    """Apply the model pack's own mixed-precision loader, when it ships one.

    ``ltx_core_mlx.apply_quantization`` detects ONE bit width and applies it to every layer.
    This pack is quantized per-tensor across 2/3/4/5/6/8 bits and publishes no scheme, so the
    stock loader dequantizes a 5-bit layer as 8-bit and fails on shapes. The pack ships the
    loader that knows, in its own ``generate.py``, and inferring those bits a second time here
    would be a second copy free to disagree with the one that produced the weights.

    Nothing happens for a pack that is not mixed-precision: a ``split_model.json`` without
    ``quantized: true``, or any pack with no such script, is left alone. Returns the provenance
    string when a patch was applied, so the job receipt says which loader ran.
    """
    script = model_dir / 'generate.py'
    manifest_path = model_dir / 'split_model.json'
    if not script.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
    except (OSError, ValueError):
        manifest = {}
    if not isinstance(manifest, dict) or not manifest.get('quantized'):
        return None

    spec = importlib.util.spec_from_file_location('_studiohub_video_pack_generate', script)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:  # noqa: BLE001 - a pack script we cannot read is not fatal
        return None
    patch = getattr(module, '_patch_pipeline_quantization', None)
    if not callable(patch):
        return None
    try:
        patch()
    except Exception:  # noqa: BLE001
        return None
    return 'pack_generate.py:_patch_pipeline_quantization'


def _pipeline_class(model: VideoModel):
    """The runtime's pipeline class for one registered LTX model."""
    import ltx_pipelines_mlx
    name = model.limits.get('pipeline') or 'DistilledPipeline'
    pipeline = getattr(ltx_pipelines_mlx, name, None)
    if pipeline is None:
        available = sorted(n for n in dir(ltx_pipelines_mlx) if n.endswith('Pipeline'))
        raise RuntimeError(
            f'MODEL_RUNTIME_UNAVAILABLE: this LTX runtime has no {name}; it exports: '
            + ', '.join(available))
    return pipeline


# ── the lane's own limits, published and enforced from one place ─────────────

def frames_for(duration_seconds: int, frame_rate: int, grid: int = FRAME_GRID) -> int:
    """The frame count a whole-second duration maps to on the pack's `grid*k + 1` grid.

    The grid is the model's: LTX is `8k+1` (its distilled pipeline's temporal grid).
    """
    return round(duration_seconds * frame_rate) // grid * grid + 1


def duration_for(frames: int, frame_rate: int, grid: int = FRAME_GRID) -> int:
    """The whole-second duration a legacy frame count maps to, or 0 when it maps to none."""
    seconds = round(frames / frame_rate)
    return seconds if frames == frames_for(seconds, frame_rate, grid) else 0


def latent_load(model: 'VideoModel', width: int, height: int, frames: int) -> int:
    """The model's own measure of how much latent work a request is.

    **LTX** counts latent *tokens* on a 32-px grid: `(w/32) * (h/32) * frames`. Its card's
    cliff is n_lat ≥ ~30k (768×768×49f), where the VAE produces mesh artifacts. The grid and the
    ceiling are the model's own, so a future model carries its own.
    """
    spatial = (width // model.latent_grid) * (height // model.latent_grid)
    latent_frames = ((frames - 1) // model.latent_temporal_divisor + 1
                     if model.latent_temporal_divisor > 1 else frames)
    return spatial * latent_frames


def latent_tokens(width: int, height: int, frames: int) -> int:
    """LTX's latent token count — kept as a named function because tests and receipts cite it."""
    return (width // CANVAS_GRID) * (height // CANVAS_GRID) * frames


def memory_eligible(model: VideoModel, total_memory_bytes: int) -> bool:
    """Whether this host meets the model's published tier.

    This is the whole point of the tier: a model whose weights need more unified memory than
    the machine has is published ``memory_eligible: false``, and the gateway withholds the
    row. ``FLEET-3`` retracted the finding that the *Hub's* total-memory floor is dead code —
    it is deliberately not enforced by the Hub's governor — so the per-model verdict has to be
    published by the worker that knows the host it runs on.
    """
    if not total_memory_bytes:
        return False
    return total_memory_bytes >= model.qualified_minimum_memory_gb * 1e9


def lane_parameters(model: VideoModel) -> list[dict]:
    """The parameters this model's lane publishes, from its own limits and defaults.

    One list read by two consumers that must agree: :func:`lane_contract` publishes it, and
    ``components.model_defaults`` validates an operator's stored default against exactly these
    bounds. A second copy would be free to disagree with the code that rejects the request.

    Deliberately absent: ``negative_prompt``, ``steps`` and ``guidance``. The distilled pipeline
    accepts no such arguments (its ``generate_and_save`` signature is the evidence), and a
    contract that offers what the runner discards is a lie with a schema.
    """
    return [
        {'name': 'prompt', 'type': 'string', 'required': True,
         'max_characters': model.limits['max_prompt']},
        {'name': 'mode', 'type': 'string', 'required': False,
         'enum': list(model.tasks), 'default': model.defaults['mode'],
         'note': 'image-to-video needs a source image; text-to-video does not'},
        {'name': 'aspect_ratio', 'type': 'string', 'required': False,
         'enum': aspect_ratios(model), 'default': model.defaults['aspect_ratio'],
         'note': 'width and height follow from the aspect ratio and the size class; a pair this '
                 'lane does not publish is refused'},
        {'name': 'resolution', 'type': 'string', 'required': False,
         'enum': resolutions(model), 'default': model.defaults['resolution'],
         'note': ("an orientation-free size class naming the canvas's short side. Which aspect "
                  "ratios it carries, the exact width and height, and the durations that canvas "
                  "accepts are in `canvases` — the pack's two-stage pipeline floors each side to "
                  "a multiple of 64, so not every ratio exists at every class and a pair outside "
                  "`canvases` is refused")},
        {'name': 'duration_seconds', 'type': 'integer', 'required': False,
         'enum': list(DURATIONS_SECONDS), 'default': model.defaults['duration_seconds']},
        {'name': 'frame_rate', 'type': 'number', 'required': False,
         # Per model, like the canvases: LTX's distilled pipeline publishes 24 fps.
         'enum': [model.frame_rate], 'default': model.frame_rate},
        # **Sound, where the model has any.** LTX denoises picture and audio jointly, so this
        # does not change the diffusion time — but `generate_audio = False` is the pack's own
        # switch (`--no-audio`, issue #126): it never loads the audio decode block and muxes an
        # MP4 with no audio track. A model with no audio at all publishes no such
        # parameter, because offering a control the lane ignores is a lie with a schema.
        *([{'name': 'audio', 'type': 'boolean', 'required': False, 'default': True,
            'note': ('the model generates picture and sound together, so this does not change '
                     'generation time; unticking it skips the audio decode and writes a silent '
                     'MP4. The track can be added back in an editor.')}]
          if model.generates_audio else []),
        {'name': 'seed', 'type': 'integer', 'required': False,
         'minimum': model.limits['min_seed'], 'maximum': model.limits['max_seed'],
         'default': model.defaults['seed'], 'note': '-1 draws a random seed'},
    ]


def defaults_in_force(model: VideoModel, data_dir=None) -> dict:
    """Every published parameter's default: what this model states, over what was chosen."""
    parameters = lane_parameters(model)
    if data_dir is None:
        return model_defaults.stated(parameters)
    resolved, _not_used = model_defaults.resolved(data_dir, model.repo, parameters)
    return resolved


def _number(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _int(value: object) -> int | None:
    parsed = _number(value)
    if parsed is None or parsed != int(parsed):
        return None
    return int(parsed)


def validate_request(raw: dict, data_dir=None) -> dict:
    """One request, resolved against the model it names and this Mac's stored defaults.

    Two request vocabularies are accepted, because two callers exist and both are real:

    * the lane's own published vocabulary — ``prompt``, ``mode``, ``aspect_ratio``,
      ``resolution``, ``duration_seconds``, ``frame_rate``, ``seed``;
    * the Hub Video Studio's multipart vocabulary — ``prompt``, ``width``, ``height``,
      ``frames``, ``fps``, ``duration`` — which is what the broker's img2video path sends.

    They are resolved into one canonical parameter set here, so the job runner never has to
    know which door the request came through. A request that contradicts itself (a canvas that
    is not the chosen aspect ratio's, a frame count that is not a whole published duration) is
    refused by naming both values rather than silently picking one.
    """
    if not isinstance(raw, dict):
        raise ValueError('Video request must be an object')
    model = MODELS.get(raw.get('repo'))
    if model is None:
        raise ValueError('Only a registered video model is available: '
                         + ', '.join(sorted(MODELS)))
    defaults = defaults_in_force(model, data_dir)
    if raw.get('model_revision', model.revision) != model.revision:
        raise ValueError(f'Only the pinned {model.label} revision is available')
    for refused in ('negative_prompt', 'steps', 'guidance', 'quantize', 'lora_names',
                    'lora_paths', 'stage1_steps', 'stage2_steps', 'cfg_scale', 'stg_scale'):
        if raw.get(refused) not in (None, ''):
            raise ValueError(
                f'{refused} is not offered by this lane: the distilled pipeline accepts no '
                f'such argument, and a parameter this runner discards must not be advertised '
                f'or silently dropped')
    prompt = raw.get('prompt')
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > model.limits['max_prompt']:
        raise ValueError(f'Prompt must contain 1–{model.limits["max_prompt"]} characters')

    has_image = bool(raw.get('image_path') or raw.get('image'))
    mode = raw.get('mode') or (model.defaults['mode'] if has_image else model.tasks[-1])
    aliases = {'img2video': 'image-to-video', 'txt2video': 'text-to-video'}
    mode = aliases.get(str(mode), str(mode))
    if mode not in model.tasks:
        raise ValueError(f'{model.label} does not offer {mode!r}; it offers: '
                         + ', '.join(model.tasks))
    if mode == 'image-to-video' and not has_image:
        raise ValueError('image-to-video needs a source image; none was supplied')
    if mode == 'text-to-video' and has_image:
        raise ValueError('text-to-video does not take a source image; '
                         'use mode=image-to-video to animate one')

    # Canvas: the lane's own (aspect_ratio, resolution class) pair, or the Hub's explicit
    # width/height, resolved to the one published canvas they describe.
    ratio = raw.get('aspect_ratio')
    resolution = raw.get('resolution')
    width, height = _int(raw.get('width')), _int(raw.get('height'))
    if width or height:
        if not width or not height:
            raise ValueError('width and height must be supplied together')
        match = next(((name, klass) for klass, canvases in model.canvases.items()
                      for name, canvas in canvases.items() if canvas == (width, height)), None)
        if match is None:
            canvases = ', '.join(f'{name} {klass} {w}x{h}'
                                 for klass, entries in sorted(model.canvases.items())
                                 for name, (w, h) in sorted(entries.items()))
            raise ValueError(f'{width}x{height} is not a published canvas; this lane offers: '
                             f'{canvases}')
        ratio, resolution = match
    if resolution is not None:
        resolution = str(resolution)
        if resolution not in model.canvases:
            raise ValueError(f'{resolution!r} is not a published size class; '
                             f'{model.label} offers: ' + ', '.join(resolutions(model)))
    if ratio is None:
        ratio = defaults['aspect_ratio']
    ratio = str(ratio)
    if ratio not in aspect_ratios(model):
        raise ValueError(f'{ratio!r} is not a published aspect ratio; {model.label} offers: '
                         + ', '.join(aspect_ratios(model)))
    if resolution is None:
        # The model's default class, unless the requested ratio does not publish it.
        resolution = (defaults['resolution'] if ratio in model.canvases[defaults['resolution']]
                      else resolution_classes_for(model, ratio)[0])
    if canvas_for(model, ratio, resolution) is None:
        raise ValueError(
            f'{resolution} is not published for {ratio}; that aspect ratio offers: '
            + ', '.join(resolution_classes_for(model, ratio)))
    width, height = canvas_for(model, ratio, resolution)

    frame_rate = _int(raw.get('frame_rate', raw.get('fps', defaults['frame_rate'])))
    if frame_rate != model.frame_rate:
        # Per model: LTX publishes 24 fps.
        raise ValueError(f'{model.label} publishes {model.frame_rate} fps only')
    # Duration: whole published seconds, or the Hub's frame count mapped back onto them.
    duration = _int(raw.get('duration_seconds', raw.get('duration')))
    frames = _int(raw.get('frames', raw.get('num_frames')))
    if frames is not None:
        mapped = duration_for(frames, frame_rate, model.frame_grid)
        if not mapped:
            raise ValueError(
                f'{frames} frames is not a published duration at {frame_rate} fps; this lane '
                f'offers ' + ', '.join(
                    f'{seconds}s ({frames_for(seconds, frame_rate, model.frame_grid)} frames)'
                                       for seconds in DURATIONS_SECONDS))
        if duration is not None and duration != mapped:
            raise ValueError(f'duration {duration}s and {frames} frames disagree')
        duration = mapped
    if duration is None:
        # **The canvas decides when the model's default does not fit it.** A caller who names a
        # canvas and no length should get that canvas, not a refusal because the model's own
        # default (4 s here) is one the canvas does not publish — measured 2026-09-27: asking for
        # 16:9 at 576p with no duration came back "1024x576 is not published at 4s", which is a
        # true sentence about a request nobody made. The canvas's longest published duration is
        # the honest fallback: it is what the caller's chosen canvas can actually carry.
        allowed_defaults = canvas_durations(model, width, height)
        duration = (defaults['duration_seconds']
                    if defaults['duration_seconds'] in allowed_defaults
                    else (allowed_defaults[-1] if allowed_defaults else defaults['duration_seconds']))
    if duration not in DURATIONS_SECONDS:
        raise ValueError(f'Duration must be one of {list(DURATIONS_SECONDS)} seconds, '
                         f'not {duration}')
    # The canvas's own durations, not the lane's whole list: a big canvas at the longer duration is
    # the pair the token ceiling refuses, and `canvases` publishes exactly this set.
    allowed = canvas_durations(model, width, height)
    if duration not in allowed:
        frames_at = frames_for(duration, frame_rate, model.frame_grid)
        raise ValueError(
            f'{width}x{height} is not published at {duration}s '
            f'({latent_load(model, width, height, frames_at)} latent units, at or above the '
            f'{model.max_latent_tokens}-unit point where this model produces mesh artifacts); '
            f'this canvas takes ' + ', '.join(f'{seconds}s' for seconds in allowed) +
            ' or a smaller canvas')
    frames = frames_for(duration, frame_rate, model.frame_grid)
    if not model.limits['min_frames'] <= frames <= model.limits['max_frames']:
        raise ValueError(f'This lane generates {model.limits["min_frames"]}–'
                         f'{model.limits["max_frames"]} frames')

    # Sound: what the caller asked for. Absent means the model's default (on) — a field nobody
    # sent must not read as "off".
    audio = raw.get('audio')
    audio = True if audio is None else (audio is True or str(audio).strip().lower()
                                        in ('1', 'true', 'yes', 'on'))

    seed = raw.get('seed', defaults['seed'])
    if seed is None:
        seed = -1
    seed = _int(seed)
    if seed is None or not model.limits['min_seed'] <= seed <= model.limits['max_seed']:
        raise ValueError('Seed must be -1 or a 32-bit unsigned integer')

    params = {
        'repo': model.repo, 'model_revision': model.revision, 'prompt': prompt.strip(),
        'mode': mode, 'aspect_ratio': str(ratio), 'resolution': resolution,
        'width': width, 'height': height, 'duration_seconds': duration,
        'frames': frames, 'frame_rate': frame_rate, 'seed': seed, 'audio': audio,
    }
    if has_image:
        params['image_path'] = str(raw.get('image_path') or raw.get('image'))
    return params


LANE_CONTRACT_SCHEMA = 'studiohub.lane-contract.v1'


def lane_contract(model: VideoModel, data_dir=None) -> dict:
    """The Video lane's dispatch contract for one model.

    Built from ``model.limits`` and the canvases above rather than from literals, because those
    are the numbers :func:`validate_request` enforces: a second copy here would be free to
    disagree with the code that rejects the request, and the contract would be the thing that
    is wrong.

    ``deadline_seconds`` is deliberately absent — the Hub adds it from its own per-modality
    floor, and a worker that restated it would be a second copy of the Hub's number.
    """
    parameters = lane_parameters(model)
    in_force = defaults_in_force(model, data_dir)
    for parameter in parameters:
        if parameter['name'] in in_force:
            parameter['default'] = in_force[parameter['name']]
    contract = {
        'schema': LANE_CONTRACT_SCHEMA,
        'modality': 'video',
        'operation': 'video.generation',
        'modes': list(model.tasks),
        'parameters': parameters,
        # The (aspect_ratio, resolution) pairs that exist, with their exact sizes and the durations
        # each one accepts. Published because the two parameter enums above cannot express the
        # pairing: a scheduler reading only them would offer `448p` with the ratio that only exists
        # at `256p`, which is a request this lane refuses. Measured on the first consumer to try it
        # (Story Studio, whose widescreen request is 16:9) — the cross product of two enums is not
        # a set of canvases.
        'canvases': published_canvases(model),
        'artifact': {'modality': 'video', 'media_type': 'video/mp4', 'extension': 'mp4',
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
            'semantics': ('asks the worker to stop. A generation already inside the diffusion '
                          'loop cannot be interrupted (MLX has no safe cancellation point '
                          'mid-graph), so a cancelled item may still write its MP4 — which is '
                          'why a cancelled item is not evidence that nothing was written, and '
                          'the terminal state is checked again after the run.'),
        },
    }
    if model.measured_runtime:
        contract['runtime_seconds'] = model.measured_runtime
    return contract


def missing_runtime_model_reason(manifest: dict) -> str:
    """Why an accepted Video manifest cannot run here, and the file that must learn about it."""
    return (f'{manifest["package_id"]}.json declares engine {manifest["engine"]} for '
            f'{manifest["model_id"]}, but no VideoModel in MODELS serves package '
            f'{manifest["package_id"]}; add one in components/video/app/backend/generation.py '
            f'or adopt the engine its runner names')


def load_video_manifests(directory: Path, required: Path) -> tuple[dict, dict]:
    """Every Video manifest this build ships, keyed by ``package_id``.

    ``required`` is the manifest the bundle names explicitly and must be valid; its siblings
    are read too, and a sibling that is another engine's or that does not validate is reported
    instead of taking the worker down.
    """
    from components import model_packages

    errors: dict[str, str] = {}
    manifests: dict[str, dict] = {}

    def read(path: Path, *, required_file: bool) -> None:
        try:
            candidate = json.loads(path.read_text())
            if candidate.get('engine') not in VIDEO_ENGINES:
                if required_file:
                    raise ValueError(
                        f'Not a Video manifest: {candidate.get("engine")!r}')
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


# ── the artifact is checked, not assumed ─────────────────────────────────────

def _boxes(data: bytes, start: int, end: int):
    """Yield ``(type, payload_start, box_end)`` for the ISO-BMFF boxes in one range."""
    position = start
    while position + 8 <= end:
        size = int.from_bytes(data[position:position + 4], 'big')
        kind = data[position + 4:position + 8]
        header = 8
        if size == 1:
            if position + 16 > end:
                return
            size = int.from_bytes(data[position + 8:position + 16], 'big')
            header = 16
        elif size == 0:
            size = end - position
        if size < header or position + size > end:
            return
        yield kind, position + header, position + size
        position += size


def _track_kinds_and_sizes(data: bytes, moov_start: int, moov_end: int) -> tuple[list, list]:
    """``(video_boxes, audio_boxes)`` for one ``moov`` — each a ``(width, height)`` tuple."""
    videos: list = []
    audios: list = []
    for kind, start, end in _boxes(data, moov_start, moov_end):
        if kind != b'trak':
            continue
        handler, width, height = None, None, None
        for child, child_start, child_end in _boxes(data, start, end):
            if child == b'tkhd':
                version = data[child_start]
                offset = child_start + 4
                if version == 1:
                    offset += 8 + 8 + 4 + 4 + 8
                else:
                    offset += 4 + 4 + 4 + 4 + 4
                offset += 8 + 2 + 2 + 2 + 2 + 36
                if offset + 8 <= child_end:
                    width = int.from_bytes(data[offset:offset + 4], 'big') / 65536
                    height = int.from_bytes(data[offset + 4:offset + 8], 'big') / 65536
            elif child == b'mdia':
                for grand, grand_start, grand_end in _boxes(data, child_start, child_end):
                    if grand == b'hdlr' and grand_start + 12 <= grand_end:
                        handler = data[grand_start + 8:grand_start + 12]
        if handler == b'vide':
            videos.append((width, height))
        elif handler == b'soun':
            audios.append((width, height))
    return videos, audios


def mp4_facts(path: Path) -> dict:
    """What the produced MP4 is, read from its own box structure.

    A file existing is not evidence it is a video: the image lane verifies a PNG signature and
    its exact dimensions, and the equivalent here is the ``ftyp`` brand, at least one video
    track with non-zero dimensions, a positive duration, and — for a model whose card says the
    clip carries its own audio — at least one audio track. Reading the boxes keeps the check
    dependency-free and works on a machine with no ffprobe.
    """
    data = path.read_bytes()
    facts: dict = {'bytes': len(data), 'ftyp': False, 'duration_seconds': None,
                   'width': None, 'height': None, 'video_tracks': 0, 'audio_tracks': 0}
    if len(data) < 16:
        return facts
    duration = None
    videos: list = []
    audios: list = []
    for kind, start, end in _boxes(data, 0, len(data)):
        if kind == b'ftyp':
            facts['ftyp'] = data[start:start + 4].decode('latin-1', 'replace')
        elif kind == b'moov':
            for child, child_start, child_end in _boxes(data, start, end):
                if child == b'mvhd' and child_start + 4 <= child_end:
                    version = data[child_start]
                    if version == 1:
                        offset = child_start + 4 + 8 + 8
                        if offset + 12 <= child_end:
                            timescale = int.from_bytes(data[offset:offset + 4], 'big')
                            ticks = int.from_bytes(data[offset + 4:offset + 12], 'big')
                            duration = ticks / timescale if timescale else None
                    else:
                        offset = child_start + 4 + 4 + 4
                        if offset + 8 <= child_end:
                            timescale = int.from_bytes(data[offset:offset + 4], 'big')
                            ticks = int.from_bytes(data[offset + 4:offset + 8], 'big')
                            duration = ticks / timescale if timescale else None
            found_videos, found_audios = _track_kinds_and_sizes(data, start, end)
            videos.extend(found_videos)
            audios.extend(found_audios)
    facts['duration_seconds'] = duration
    facts['video_tracks'] = len(videos)
    facts['audio_tracks'] = len(audios)
    if videos:
        facts['width'] = int(videos[0][0]) if videos[0][0] else None
        facts['height'] = int(videos[0][1]) if videos[0][1] else None
    return facts


def final_matches(job: dict, path: Path) -> bool:
    try:
        return bool(not path.is_symlink() and path.is_file()
                    and job.get('output_path') == str(path)
                    and path.stat().st_size == job.get('bytes')
                    and sha256(path.read_bytes()).hexdigest() == job.get('sha256'))
    except OSError:
        return False


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


def _activity_time(value: object) -> float | None:
    parsed = _number(value)
    return parsed


def _activity_progress(value: object) -> float:
    parsed = _number(value)
    if parsed is None:
        parsed = 0.0
    return max(0.0, min(1.0, parsed))


def _activity_error_code(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip()[:80] or None


# ── the runner ───────────────────────────────────────────────────────────────

def _companion_folder(manager, package_id: str, model: VideoModel) -> Path | None:
    """The installed folder of one companion package, or ``None``.

    The runner resolves the text encoder by path so an offline node never reaches the network.
    A companion that is not installed is not fatal here: the engine's own lookup is the
    fallback, and the job receipt records which path was used. This function's job is only to
    answer where it is.

    A `manager` of ``None`` answers "nowhere": callers that have no library yet (a unit test
    exercising the refusal path, a worker before its first package scan) get the same `None` as a
    missing package rather than an `AttributeError` from inside a helper.
    """
    from components import model_packages

    if manager is None:
        return None
    manifest = manager.manifests.get(package_id)
    if not isinstance(manifest, dict):
        return None
    try:
        state = model_packages.preflight(manifest, manager.library)
    except Exception:  # noqa: BLE001 - a companion's state must not fail the model's row
        return None
    if state.get('state') != 'files_verified':
        return None
    folder = Path(state['folder']) / WEIGHTS_SUBDIRECTORY
    return folder if folder.is_dir() else None


def run_ltx(model: VideoModel, manager, folder: Path, params: dict, output: Path,
            progress) -> dict:
    """Generate one clip with the LTX pack, in this interpreter.

    The order is load-bearing and was measured: the pack's quantization patch is applied
    **before** the pipeline is constructed, because ``nn.quantize`` is applied as the modules
    are created — a patch that arrives once the pipeline exists has nothing left to patch, and
    the failure it prevents is a shape error deep in ``quantized_matmul``, not a clear
    "you forgot the loader".
    """
    model_dir = folder
    loader = apply_pack_quantization(model_dir)
    progress('loading', 0.05)
    pipeline_cls = _pipeline_class(model)

    constructor = inspect.signature(pipeline_cls.__init__).parameters
    ctor_kwargs: dict = {'model_dir': str(model_dir)}
    if 'low_memory' in constructor:
        ctor_kwargs['low_memory'] = model.memory.low_memory
    if 'low_ram_streaming' in constructor:
        # Never enabled on this pack; see the module docstring.
        ctor_kwargs['low_ram_streaming'] = model.memory.low_ram_streaming
    gemma_folder = None
    for companion in model.requires:
        candidate = _companion_folder(manager, companion, model)
        if candidate is not None:
            gemma_folder = candidate
            break
    if gemma_folder is not None and 'gemma_model_id' in constructor:
        ctor_kwargs['gemma_model_id'] = str(gemma_folder)
    pipeline = pipeline_cls(**ctor_kwargs)
    # The pack's own switch, set exactly the way its CLI does after construction
    # (`cli.py`: `pipe.generate_audio = not args.no_audio`). With it off the audio decode block
    # is never loaded and the mux writes a silent MP4 — the joint denoise is unchanged, so this
    # buys the decode, not the diffusion.
    wants_audio = bool(params.get('audio', True))
    if not wants_audio:
        setattr(pipeline, 'generate_audio', False)
    progress('generating', 0.15)
    try:
        call = inspect.signature(pipeline_cls.generate_and_save).parameters
        call_kwargs: dict = {
            'prompt': params['prompt'], 'output_path': str(output),
            'height': params['height'], 'width': params['width'],
            'num_frames': params['frames'], 'frame_rate': params['frame_rate'],
            'seed': params['seed'],
        }
        if params.get('image_path') and 'image' in call:
            call_kwargs['image'] = params['image_path']
        call_kwargs = {key: value for key, value in call_kwargs.items() if key in call}
        pipeline.generate_and_save(**call_kwargs)
    finally:
        # Released after every job, like Image: the weights must not outlive the job.
        del pipeline
        import gc
        gc.collect()
        _free_mlx_cache()
    return {'quantization_loader': loader or 'runtime default',
            'gemma_model_path': str(gemma_folder) if gemma_folder else None}


RUNNERS = {LTX_ENGINE: run_ltx}


def _free_mlx_cache() -> None:
    import sys
    module = sys.modules.get('mlx.core') or sys.modules.get('mlx')
    clear = getattr(module, 'clear_cache', None)
    if callable(clear):
        try:
            clear()
        except Exception:  # noqa: BLE001 - release is best effort
            pass


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
            raise ValueError('Video output directory cannot be a symlink')
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
        index: dict[str, dict] = {}
        if isinstance(manifest, dict) and manifest.get('package_id'):
            index[manifest['package_id']] = manifest
        values = manifests.values() if isinstance(manifests, dict) else (manifests or [])
        for candidate in values:
            if isinstance(candidate, dict) and candidate.get('package_id'):
                index[candidate['package_id']] = candidate
        return index

    def _restore(self) -> None:
        from components import job_history
        try:
            saved = json.loads(self.state_file.read_text())
            if not isinstance(saved, dict):
                raise ValueError('Video job history is malformed')
        except FileNotFoundError:
            saved = {}
        for job_id, job in saved.items():
            if not isinstance(job, dict) or job_id != job.get('id'):
                continue
            if job.get('state') not in TERMINAL:
                uncertain_output = (self.outputs / f'{job_id}.mp4').exists()
                job.update(state='error',
                    error=('Final MP4 exists without a durable completion receipt; reconcile '
                           'before retry' if uncertain_output
                           else 'Worker restarted before completion'),
                    error_code=('JOB_PERSISTENCE_UNCERTAIN' if uncertain_output
                                else 'WORKER_RESTART_UNCERTAIN'), finished_at=time.time())
            if job.get('state') == 'done':
                path = self.outputs / f'{job_id}.mp4'
                if not final_matches(job, path):
                    job.update(state='error', error='Final MP4 is unavailable after restart',
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
        """Readability of one registered model's installed package."""
        from components import model_packages

        model = MODELS.get(repo)
        if model is None:
            return {'state': 'unknown_model', 'reason': f'Unregistered video model: {repo}'}
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
        # The catalog is required, not optional: this model loads a text encoder companion, and
        # `model_packages.preflight` answers `dependency_missing` for a companion it cannot
        # resolve. `self.manifests` is every Video manifest this build read, keyed by
        # package_id — the same index `_companion_folder` resolves the runner's path from.
        return model_packages.preflight(manifest, self.library, catalog=self.manifests)

    def packages_without_a_runtime_model(self) -> list[dict]:
        """Every Video manifest this build read that no registered model serves.

        **A companion is not a model and gets no catalog row.** The text encoder is a
        `video-ltx` manifest because the runner loads it, but nobody chooses it and it has no
        tier of its own; publishing it as a model produced a row the gateway withheld with
        "this Mac does not have the memory" — a true sentence about a question nobody asked.
        The Hub separately marks such a package a dependency, but the cleanest place to not
        offer it is where the rows are built.
        """
        from components import model_packages

        served = {model.package_id for model in MODELS.values()}
        companions: set[str] = set()
        for manifest in self.manifests.values():
            try:
                companions.update(model_packages.required_packages(manifest))
            except ValueError:  # an unusable manifest is already reported by its own row
                continue
        return [manifest for package_id, manifest in sorted(self.manifests.items())
                if package_id not in served and package_id not in companions]

    def get(self, job_id: str) -> dict | None:
        with self.lock:
            return dict(self.jobs[job_id]) if job_id in self.jobs else None

    def list(self) -> list[dict]:
        with self.lock:
            return [dict(job) for job in sorted(self.jobs.values(),
                    key=lambda value: value['created_at'], reverse=True)]

    @staticmethod
    def _activity_projection(job: dict, *, observed_at: float) -> dict:
        params = job.get('params') if isinstance(job.get('params'), dict) else {}
        model = str(params.get('repo') or '').strip()[:500] or None
        state = str(job.get('state') or '')
        created_at = _activity_time(job.get('created_at'))
        started_at = _activity_time(job.get('started_at'))
        finished_at = _activity_time(job.get('finished_at'))
        projection = {
            'id': str(job.get('id') or '')[:160], 'state': state, 'model': model,
            'operation': 'video', 'progress': _activity_progress(job.get('progress')),
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
        """The bounded activity contract the Hub polls; an idle worker reports an empty one."""
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
            'schema': 'kh-studio.activity.v1', 'studio': 'video',
            'observed_at': observed,
            'active': self._activity_projection(active, observed_at=observed) if active else None,
            'latest': self._activity_projection(latest, observed_at=observed) if latest else None,
        }

    def submit(self, raw: dict) -> dict:
        params = validate_request(raw, data_dir=self.data)
        model = MODELS[params['repo']]
        if not model.runnable:
            raise RuntimeError(f'MODEL_RUNTIME_UNAVAILABLE: {model.unavailable_reason}')
        files = self.preflight(params['repo'])
        if files['state'] != 'files_verified':
            raise RuntimeError(f'MODEL_FILES_UNAVAILABLE: {files.get("reason") or files["state"]}')
        job_id = uuid4().hex
        job = {'id': job_id, 'mode': params['mode'], 'params': params, 'state': 'queued',
               'progress': 0.0, 'progress_stage': None, 'created_at': time.time(),
               'started_at': None, 'finished_at': None, 'generated_at': None,
               'duration_seconds': None, 'output_path': None, 'output_url': None,
               'sha256': None, 'bytes': None, 'width': None, 'height': None,
               'frames': None, 'frame_rate': None, 'output_seconds': None,
               'has_audio': None, 'resolved_seed': None, 'media_type': None,
               'error': None, 'error_code': None,
               'model_revision': params['model_revision'],
               'runtime_revision': self.runtime_sha256,
               'quantization_loader': None, 'gemma_model_path': None,
               'memory_mode': None, 'peak_mlx_bytes': None,
               'peak_process_rss_bytes': None, 'peak_footprint_bytes': None,
               'footprint_method': None, 'has_source_image': bool(params.get('image_path')),
               'worker_id': 'video', 'machine_id': self.machine, 'acknowledged': False}
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
                         name=f'video-{job_id[:8]}').start()
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
                elif job['state'] == 'running':
                    # A five-minute job is long enough that "cancelled" vs "cancel requested"
                    # is a real distinction: the request is recorded now and the run's own
                    # terminal check decides the final state.
                    job.update(state='cancel_requested')
                self._persist()
            return dict(job)

    def ack(self, job_id: str) -> dict | None:
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None or job['state'] != 'done':
                return None
            if not final_matches(job, self.outputs / f'{job_id}.mp4'):
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

    def _stage_image(self, job_id: str, params: dict) -> str | None:
        """The source image for an image-to-video job, as a path the runner can read.

        The Hub's video2video door hands the bytes to the API layer, which writes them into
        this worker's own state directory (owner-only) and passes the path in the request. A
        path that is not inside the worker's data directory is refused: this worker must not be
        usable to read arbitrary files.
        """
        value = params.get('image_path')
        if not value:
            return None
        path = Path(value)
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise RuntimeError(f'MODEL_FILES_UNAVAILABLE: source image is unreadable: {exc}')
        if not resolved.is_relative_to(self.data.resolve()):
            raise RuntimeError('source image must be staged inside the worker state directory')
        if not resolved.is_file() or resolved.stat().st_size == 0:
            raise RuntimeError('source image is empty')
        return str(resolved)

    def _run(self, job_id: str, model_folder: str) -> None:
        staged = self.outputs / f'.{job_id}.mp4'
        final = self.outputs / f'{job_id}.mp4'
        with self.inference_lock:
            with self.lock:
                event = self.cancel_events[job_id]
                job = self.jobs[job_id]
                if event.is_set():
                    job.update(state='cancelled', finished_at=time.time())
                    self._persist()
                    self.cancel_events.pop(job_id, None)
                    return
                job.update(state='running', progress=0.02, progress_stage='preparing',
                           started_at=time.time())
                try:
                    self._persist()
                except Exception:
                    job.update(state='error', error='Running state could not be persisted',
                               error_code='JOB_PERSISTENCE_FAILED', finished_at=time.time())
                    try:
                        self._persist()
                    except Exception:
                        pass
                    self.cancel_events.pop(job_id, None)
                    return
            try:
                repo = job['params']['repo']
                model = MODELS.get(repo)
                if model is None:
                    raise RuntimeError(f'MODEL_FILES_UNAVAILABLE: unregistered model {repo}')
                files = self.preflight(repo)
                if files['state'] != 'files_verified' or files['folder'] != model_folder:
                    raise RuntimeError(
                        'MODEL_FILES_UNAVAILABLE: pinned model changed before inference')
                runner = RUNNERS.get(model.engine)
                if runner is None:
                    raise RuntimeError(
                        f'MODEL_RUNTIME_UNAVAILABLE: no runner for engine {model.engine}')
                staged.unlink(missing_ok=True)
                seed = job['params']['seed']
                if seed == -1:
                    seed = random.randint(0, 2**32 - 1)
                job['params']['image_path'] = self._stage_image(job_id, job['params'])
                with self.lock:
                    job['resolved_seed'] = seed
                    job['memory_mode'] = 'default'
                    self._persist()

                def progress(stage: str, value: float) -> None:
                    with self.lock:
                        job['progress_stage'] = stage
                        job['progress'] = max(job.get('progress') or 0.0, value)
                        try:
                            self._persist()
                        except Exception:
                            pass

                model_dir = Path(model_folder) / WEIGHTS_SUBDIRECTORY
                params = dict(job['params'])
                params['seed'] = seed
                with FootprintSampler() as sampler:
                    detail = runner(model, self, model_dir, params, staged, progress)
                footprint = sampler.peak_bytes
                facts = mp4_facts(staged) if staged.is_file() else {}
                if not facts.get('ftyp'):
                    raise RuntimeError('the runner wrote no MP4: no ftyp box in the output')
                if not facts.get('video_tracks') or not facts.get('width') or not facts.get('height'):
                    raise RuntimeError('the runner wrote an MP4 with no decodable video track')
                if not facts.get('duration_seconds'):
                    raise RuntimeError('the runner wrote an MP4 with no duration')
                if (model.engine == LTX_ENGINE and params.get('audio', True)
                        and not facts.get('audio_tracks')):
                    # The pack's own runs carry audio ("4.0 s with audio"); a silent file would
                    # mean the audio half of the pipeline changed, not that we should shrug.
                    # Only when the request asked for sound: a caller who unticked the box gets a
                    # silent MP4 on purpose, and that is checked *for* it below.
                    raise RuntimeError(
                        'the LTX pack produced no audio track; the measured configuration has one')
                if (model.engine == LTX_ENGINE and not params.get('audio', True)
                        and facts.get('audio_tracks')):
                    raise RuntimeError(
                        'the request asked for no audio but the file carries a track')
                content_size = staged.stat().st_size
                if content_size <= 0:
                    raise RuntimeError('the runner wrote an empty MP4')
                content = staged.read_bytes()
                with self.lock:
                    if event.is_set():
                        job.update(state='cancelled', finished_at=time.time())
                    else:
                        os.replace(staged, final)
                        now = time.time()
                        job.update(state='done', progress=1.0, finished_at=now,
                            generated_at=now, duration_seconds=now - job['started_at'],
                            output_path=str(final),
                            output_url=f'/api/generate/jobs/{job_id}/video',
                            sha256=sha256(content).hexdigest(), bytes=len(content),
                            media_type='video/mp4', width=facts['width'],
                            height=facts['height'], frames=job['params']['frames'],
                            frame_rate=job['params']['frame_rate'],
                            output_seconds=facts['duration_seconds'],
                            has_audio=bool(facts['audio_tracks']),
                            quantization_loader=detail.get('quantization_loader'),
                            gemma_model_path=detail.get('gemma_model_path'),
                            # **Which engine actually ran, and what it loaded.** A job record
                            # that says only "video" cannot answer "which engine made this clip".
                            runner=detail.get('runner') or model.engine,
                            companion_path=(detail.get('companion_path')
                                            or detail.get('gemma_model_path')),
                            runner_detail={key: detail[key] for key in
                                           ('memory_mode', 'load_seconds', 'generate_seconds',
                                            'lossless_decode') if key in detail},
                            peak_mlx_bytes=peak_mlx_bytes(),
                            peak_process_rss_bytes=(detail.get('peak_rss_bytes')
                                                    or peak_rss_bytes()),
                            # **Whose memory was measured.** The work is in this process, so the
                            # sampler above is right; a runner's own number wins where it has one.
                            peak_footprint_bytes=(detail.get('peak_footprint_bytes') or footprint),
                            footprint_method=(detail.get('footprint_method')
                                              or ('sampled ri_phys_footprint at 1 Hz'
                                                  if footprint else None)))
                    try:
                        self._persist()
                    except Exception:
                        job.update(state='error', output_path=None, output_url=None,
                                   sha256=None, bytes=None, media_type=None,
                                   error=('Final output could not be persisted; reconcile '
                                          'before retry'),
                                   error_code='JOB_PERSISTENCE_UNCERTAIN')
                        try:
                            final.unlink(missing_ok=True)
                            job.update(error='Final output could not be persisted; generation '
                                             'was discarded',
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
                                       error_code=('MODEL_FILES_UNAVAILABLE'
                                                   if 'MODEL_FILES_UNAVAILABLE' in detail
                                                   else 'MODEL_RUNTIME_UNAVAILABLE'
                                                   if 'MODEL_RUNTIME_UNAVAILABLE' in detail
                                                   else 'VIDEO_GENERATION_FAILED'))
                    job['finished_at'] = time.time()
                    if job.get('started_at'):
                        job['duration_seconds'] = job['finished_at'] - job['started_at']
                    self._persist()
            finally:
                staged.unlink(missing_ok=True)
                self.cancel_events.pop(job_id, None)
