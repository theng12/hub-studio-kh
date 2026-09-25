"""Subtitle decoding from the ST-01 verified Whisper packages.

Scope: exactly the transcription path needed by an independent Subtitle
service. It deliberately does not re-implement the Voice service.

Reuse at one tracked source location
------------------------------------
The export/normalization behavior is imported from the existing Voice Studio
source at ``components/voice/app/backend/transcription.py`` and is never copied:

* ``segments_to_srt`` / ``segments_to_vtt`` — the Text/SRT/VTT formatters.
* ``_normalize_segments`` — MLX Whisper padding-hallucination discard.

``one_source_location()`` reports the exact file and SHA-256 actually loaded so a
receipt can prove which single source produced the exported cues. The decoding
call below uses the same pinned mlx-audio Whisper entry points and the same
non-configurable ``condition_on_previous_text=False`` decision the Voice service
uses; only the model location differs, because Subtitle must load from the
immutable ST-01 package folder and a verified local companion tokenizer instead
of Voice's private Hugging Face cache.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
import importlib
import json
import os
import shutil
from pathlib import Path
import platform
import re
import subprocess
import sys
import tempfile
import threading
import time
from typing import Callable, Optional

# The existing Voice transcription module — the single tracked source location
# for decoding/export behavior. Imported as a package module so its relative
# imports resolve exactly as they do inside the Voice service.
VOICE_TRANSCRIPTION_MODULE = 'backend.transcription'
VOICE_TRANSCRIPTION_SOURCE = 'components/voice/app/backend/transcription.py'

# The same reuse rule, for the snapshot-view builder. Imported as ``backend.cache`` from the
# Voice app directory rather than as ``components.voice.app.backend.cache``: the bundle stages
# Voice at ``Resources/voice``, not under ``Resources/components``, so the dotted path resolved
# in this checkout and nowhere else. It was wrapped in a bare ``except Exception`` and the
# fallback below looked healthy, so the one-source reuse that comments promised had silently
# stopped happening in every shipped build. ``staged_import_gaps`` now catches the shape.
VOICE_CACHE_MODULE = 'backend.cache'
VOICE_CACHE_SOURCE = 'components/voice/app/backend/cache.py'

# The adopted Voice decision: Whisper's 30-second-window conditioning turns one
# bad token into a self-reinforcing hallucination cascade. Voice measured 94%
# fewer invented words with it off. Subtitle keeps the identical value rather
# than exposing a flag that could only turn the bug back on.
CONDITION_ON_PREVIOUS_TEXT = False

# mlx-audio's Whisper loader globs ``*.safetensors`` only. ST-01's pinned Base
# and Small MLX packages ship ``weights.npz``; Turbo ships ``weights.safetensors``.
# Report this honestly instead of attempting a load that cannot work.
SUPPORTED_WEIGHT_GLOBS = ('*.safetensors',)
UNSUPPORTED_WEIGHT_NOTE = (
    'mlx-audio Whisper loading supports *.safetensors weights; this pinned '
    'package ships weights.npz, so it is catalog-only on this runtime'
)

ENGINE = 'subtitle-mlx'
MINIMUM_UNIFIED_MEMORY_BYTES = 8 * 1024**3
_DEFAULT_LABELS = {
    'whisper-turbo': 'Whisper Large v3 Turbo',
    'whisper-small': 'Whisper Small',
    'whisper-base': 'Whisper Base',
}
_MEMORY_CACHE: dict[str, int | None] = {}

# ── The lane contract this worker publishes on every catalog row ───────────
#
# `docs/lane-execution-contract.md`: a scheduler learns what to send, what comes back and what a
# cancel means from the worker that owns the API, instead of guessing from an engine table. The
# vocabulary below is quoted verbatim from that document; the parameters are this worker's own
# `POST /api/transcribe` surface, and nothing is listed that the route does not honour.
LANE_SCHEMA = 'studiohub.lane-contract.v1'
LANE_OPERATION = 'audio.transcription'
LANE_STATES = {
    'accepted': ['queued'],
    'active': ['running', 'cancel_requested'],
    'terminal': ['done', 'error', 'cancelled'],
    'uncertain': 'uncertain',
    'uncertain_is_terminal': False,
    'discard_supported': True,
}
LANE_CANCEL = {
    'supported': True,
    'request': 'DELETE /api/hub/jobs/{batch_id}',
    'semantics': ('asks every studio holding an item of this batch to stop; an item already in '
                  'flight may still land an artifact, so a cancelled item is not evidence that '
                  'nothing was written'),
}
LANE_ARTIFACT = {'modality': 'transcription', 'media_type': 'application/json',
                 'extension': 'json', 'per_item': 1}


def subtitle_capabilities(manifest: dict) -> dict:
    """The capabilities this package declared, or a refusal naming what is missing.

    Read from the manifest and nowhere else. A subtitle package that says nothing about its
    family or its timestamps is not published as Whisper-with-everything — which is what the
    worker used to do for every package it loaded — because a capability nobody declared is a
    capability nobody can be wrong about.
    """
    package_id = manifest.get('package_id')
    block = manifest.get('subtitle')
    if not isinstance(block, dict):
        raise ModelPackageError(
            f"Subtitle manifest {package_id!r} publishes no 'subtitle' capabilities block, so "
            f"there is nothing to publish about it. Add \"subtitle\" to "
            f"model-manifests/{package_id}.json with its family, languages, "
            f"segment_timestamps, word_timestamps and long_form; the worker publishes only what "
            f"the manifest says and never falls back to a family or a capability of its own.")
    missing = [field for field in
               ('family', 'languages', 'segment_timestamps', 'word_timestamps', 'long_form')
               if field not in block]
    if missing:
        raise ModelPackageError(
            f"Subtitle manifest {package_id!r} has an incomplete 'subtitle' block: missing "
            f"{', '.join(missing)} (model-manifests/{package_id}.json)")
    return {
        'family': block['family'],
        'languages': list(block['languages']),
        'supports_segment_timestamps': block['segment_timestamps'],
        'supports_word_timestamps': block['word_timestamps'],
        'supports_long_form': block['long_form'],
        'runtime_seconds': block.get('runtime_seconds'),
    }


def lane_contract(capabilities: dict, parameters: list[dict]) -> dict:
    """The published lane contract: what to send, what comes back, and what a cancel means.

    `deadline_seconds` is deliberately absent: the Hub owns the only dispatch deadline
    (`standalone.py`'s peer-proxy timeout) and adds it when it echoes this contract, so a worker
    restating it would be another copy of a number it does not enforce. `runtime_seconds` is
    published only where a manifest records a measurement, with its evidence.
    """
    contract = {
        'schema': LANE_SCHEMA,
        'modality': 'transcription',
        'operation': LANE_OPERATION,
        'parameters': parameters,
        'artifact': dict(LANE_ARTIFACT),
        'states': {key: (list(value) if isinstance(value, list) else value)
                   for key, value in LANE_STATES.items()},
        'cancel': dict(LANE_CANCEL),
    }
    if isinstance(capabilities.get('runtime_seconds'), dict):
        contract['runtime_seconds'] = capabilities['runtime_seconds']
    return contract


def unified_memory_bytes() -> Optional[int]:
    """Total unified memory, or None when it cannot be measured."""
    if 'value' in _MEMORY_CACHE:
        return _MEMORY_CACHE['value']
    try:
        value = int(subprocess.check_output(
            ['/usr/sbin/sysctl', '-n', 'hw.memsize'], text=True, timeout=3).strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        value = None
    _MEMORY_CACHE['value'] = value
    return value


def host_eligibility() -> dict:
    """The same Apple-Silicon/macOS gate the Image and Voice workers use."""
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        return {'eligible': False, 'reason': 'Apple Silicon macOS is required'}
    memory = unified_memory_bytes()
    if memory is None:
        return {'eligible': False, 'reason': 'Unified memory could not be measured'}
    version = platform.mac_ver()[0]
    if not version:
        return {'eligible': False, 'reason': 'macOS version could not be measured'}
    if int(version.split('.')[0]) < 14:
        return {'eligible': False, 'reason': 'macOS 14 or later is required'}
    # The published floor is the tier Whisper is qualified at, not a wall. A Mac below it still
    # transcribes; it is told what it is trading rather than refused, and the live free-memory
    # check is what protects the machine at the moment a model loads.
    state = {'eligible': True, 'macos': version, 'unified_memory_bytes': memory,
             'minimum_unified_memory_bytes': MINIMUM_UNIFIED_MEMORY_BYTES,
             'memory_warning': None,
             'qualified_minimum_memory_bytes': MINIMUM_UNIFIED_MEMORY_BYTES}
    if memory < MINIMUM_UNIFIED_MEMORY_BYTES:
        state['memory_warning'] = (
            f'This Mac has {memory / 1024**3:.0f} GB of unified memory. Whisper is qualified on '
            '8 GB, so transcription here is below the qualified tier and may be slow if other '
            'Studios are holding models.')
    return state


def _voice_app_root(voice_app_dir: str | Path | None = None) -> str:
    """The Voice app directory, on ``sys.path`` so ``backend.*`` resolves to it."""
    explicit = str(voice_app_dir) if voice_app_dir is not None else ''
    voice_app = explicit.strip() or os.environ.get('STUDIOHUB_VOICE_APP_DIR', '').strip()
    if not voice_app:
        raise RuntimeError('STUDIOHUB_VOICE_APP_DIR is required to reuse the Voice source')
    root = str(Path(voice_app).expanduser().resolve())
    if root not in sys.path:
        sys.path.insert(0, root)
    return root


def _voice_module(name: str, source: str, voice_app_dir: str | Path | None = None):
    """Import one module out of the Voice app, proving it came from the named file.

    The check is not decoration. ``backend`` is a bare package name, and a process that already
    put a *different* ``backend`` on the path would otherwise be handed that other package's
    module while every caller believed it had the Voice one. A mismatch names both files.
    """
    root = _voice_app_root(voice_app_dir)
    module = importlib.import_module(name)
    expected = Path(root) / 'backend' / (name.split('.', 1)[1] + '.py')
    actual = Path(getattr(module, '__file__', '') or '')
    if actual.resolve() != expected.resolve():
        raise RuntimeError(
            f'{name} resolved to {actual or "nowhere"}, not to the Voice source {expected}')
    return module


def voice_export_module(voice_app_dir: str | Path | None = None):
    """Import the existing Voice transcription module at its one source path.

    The caller's own directory wins over the environment so a helper is never silently
    dependent on ambient process state: passing the Voice app path and reading it back out
    of ``STUDIOHUB_VOICE_APP_DIR`` used to be two different things, and only one of them was
    honoured. ``STUDIOHUB_VOICE_APP_DIR`` remains the fallback, which is what the packaged
    Subtitle worker passes at startup.
    """
    return _voice_module(VOICE_TRANSCRIPTION_MODULE, VOICE_TRANSCRIPTION_SOURCE, voice_app_dir)


def voice_cache_module(voice_app_dir: str | Path | None = None):
    """The Voice snapshot-view builder, imported the same way the export module is."""
    return _voice_module(VOICE_CACHE_MODULE, VOICE_CACHE_SOURCE, voice_app_dir)


def source_file(voice_app_dir: str | Path) -> Path:
    return Path(voice_app_dir).expanduser().resolve() / 'backend' / 'transcription.py'


def one_source_location(voice_app_dir: str | Path) -> dict:
    """Prove which single tracked file provides the export behavior."""
    path = source_file(voice_app_dir)
    try:
        digest = sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise RuntimeError(f'Voice export source is unreadable: {exc}') from exc
    return {'module': VOICE_TRANSCRIPTION_MODULE,
            'path': str(path), 'sha256': digest}


def format_artifacts(segments: list[dict], voice_app_dir: str | Path) -> dict:
    """Text/SRT/VTT produced only by the existing Voice formatters."""
    module = voice_export_module(voice_app_dir)
    return {
        'text': ' '.join(
            str(segment.get('text') or '').strip()
            for segment in segments if str(segment.get('text') or '').strip()
        ).strip(),
        'srt': module.segments_to_srt(segments),
        'vtt': module.segments_to_vtt(segments),
    }


def normalize_segments(raw_segments: list[dict], *, word_timestamps: bool,
                       audio_duration: Optional[float],
                       voice_app_dir: str | Path) -> tuple[str, list[dict]]:
    module = voice_export_module(voice_app_dir)
    return module._normalize_segments(
        raw_segments, word_timestamps=word_timestamps, audio_duration=audio_duration)


# Containers whose *video* the decoder cannot open. `.mp4` and `.mov` were accepted by the upload
# validation, dispatched, and then always failed: the Whisper decoder reads audio through miniaudio,
# which does not open those containers, so a screen recording produced nothing and the operator saw
# a failed job with no way to know the file was the reason. The kit already ships ffmpeg, so the
# audio track is extracted and transcribed like any other file.
VIDEO_CONTAINER_EXTENSIONS = {'.mp4', '.mov', '.m4v', '.mkv', '.avi', '.webm'}


def find_ffmpeg(voice_app_dir: str | Path | None = None) -> Optional[Path]:
    """The bundled ffmpeg, or one on PATH.

    Same priority as the Voice service: the tools directory the packaged worker sets
    (`STUDIOHUB_TOOLS_DIR`), then the Voice app's own tools folder, then PATH.
    """
    candidates = []
    configured = os.environ.get('STUDIOHUB_TOOLS_DIR')
    if configured:
        candidates.append(Path(configured) / 'ffmpeg')
    if voice_app_dir:
        candidates.append(Path(voice_app_dir) / 'tools' / 'ffmpeg')
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    found = shutil.which('ffmpeg')
    return Path(found) if found else None


@contextmanager
def decodable_audio(path: Path, voice_app_dir: str | Path | None = None):
    """Yield a file the decoder can read, extracting the audio track of a video when needed."""
    if path.suffix.lower() not in VIDEO_CONTAINER_EXTENSIONS:
        yield path
        return
    ffmpeg = find_ffmpeg(voice_app_dir)
    if ffmpeg is None:
        raise RuntimeError(
            f'{path.name} is a video and its audio cannot be extracted: the bundled ffmpeg was '
            'not found. Copy the model library and tools again from the kit.')
    with tempfile.TemporaryDirectory(prefix='studiohub-subtitle-') as workspace:
        extracted = Path(workspace) / 'audio.wav'
        completed = subprocess.run(
            [str(ffmpeg), '-nostdin', '-y', '-i', str(path), '-vn', '-ac', '1', '-ar', '16000',
             '-f', 'wav', str(extracted)],
            capture_output=True)
        if completed.returncode != 0 or not extracted.is_file():
            detail = (completed.stderr or b'').decode('utf-8', 'replace').strip().splitlines()
            raise RuntimeError(
                f'could not read the audio track from {path.name}'
                + (f': {detail[-1]}' if detail else ''))
        yield extracted


def audio_duration_seconds(path: Path, voice_app_dir: str | Path | None = None) -> Optional[float]:
    """True media duration, so padded-silence output can be discarded.

    Delegates to the existing Voice probe helper rather than adding a second
    media-probing implementation.
    """
    module = voice_export_module(voice_app_dir)
    return module._audio_duration_seconds(Path(path))


class ModelPackageError(RuntimeError):
    """The pinned package cannot run on this runtime or host."""


class _Cancelled(RuntimeError):
    """Internal cooperative-cancellation signal."""


@dataclass(frozen=True)
class PackageLocation:
    package_id: str
    revision: str
    folder: Path
    model_folder: Path
    processor_folder: Path
    manifest: dict


class SubtitleEngine:
    """One process-wide Whisper engine with explicit-path verified loading."""

    def __init__(self, library: Path, manifests: list[dict],
                 runtime_sha256: str, *, voice_app_dir: Path,
                 offline: bool = True, processor_cache: Path | None = None) -> None:
        self.library = Path(library)
        self.runtime_sha256 = runtime_sha256
        self.voice_app_dir = Path(voice_app_dir)
        # Private tokenizer staging: the verified package is never loaded from
        # in place, so the model library stays immutable.
        self.processor_cache = Path(processor_cache or (self.library / '.subtitle-processor'))
        # Test seam only: the product path always loads the real mlx-audio model.
        # When set, it receives the verified PackageLocation and returns an
        # object exposing ``generate(...)``; normalization and export still run
        # through the shared Voice source.
        self.model_factory: Optional[Callable[[PackageLocation], object]] = None
        # The one configured Voice app directory is authoritative for both the
        # export module import and its sibling media/telemetry helpers.
        os.environ['STUDIOHUB_VOICE_APP_DIR'] = str(self.voice_app_dir)
        self.manifests = {manifest['package_id']: manifest for manifest in manifests}
        # Read every package's capabilities once, at construction: a manifest that cannot say
        # what its model does must stop the worker from starting rather than be published with a
        # default. `load_subtitle_manifests` has already refused a malformed block by file name,
        # so this only fires for a caller that built a manifest in code.
        self.capabilities = {package_id: subtitle_capabilities(manifest)
                             for package_id, manifest in self.manifests.items()}
        self._default_package, self.default_rule = self._select_default()
        self.offline = bool(offline)
        self._model = None
        self._model_package: Optional[str] = None
        self._lock = threading.RLock()
        self.inference_lock = threading.Lock()
        self._export = one_source_location(self.voice_app_dir)
        # Which builder produced each package's loadable folder, and why it was the one that
        # answered. Published read-only through ``view_sources()``.
        self._view_source: dict[str, dict] = {}

    # ── catalog / readiness ────────────────────────────────────────────────

    def export_source(self) -> dict:
        return dict(self._export)

    def view_sources(self) -> dict[str, dict]:
        """Per package, the folder a load will use and which builder chose it.

        ``voice-cache`` is the designed path (one implementation, shared with Voice);
        ``local-view`` and ``library`` are the fallbacks, each carrying the reason it ran.
        """
        return {package_id: dict(row) for package_id, row in self._view_source.items()}

    def _loadable_model_folder(self, folder: Path, package_id: str, revision: str,
                               model_id: str | None = None) -> Path:
        """Return a folder mlx-audio can actually load.

        mlx-audio's Whisper loader globs ``*.safetensors`` while the pinned base/small packages
        ship ``weights.npz``. The shared Voice cache module already builds a complete, loadable
        view for a repository — merged weights+tokenizer, with npz converted — so reuse that
        rather than maintaining a second copy of the same logic. Verified library bytes are
        never modified either way.

        Which of the two produced the folder is recorded, not inferred: the local builder below
        is a fallback for the case where the shared module cannot answer (an unindexed package,
        a library that moved), and when it runs for any other reason that is a fact an operator
        should be able to read rather than a silence.
        """
        if model_id:
            try:
                voice_cache = voice_cache_module(self.voice_app_dir)
                shared = voice_cache.package_snapshot(model_id)
                # `exists()` and not `is_file()`: a broken symlink satisfies `is_file()` on
                # macOS, so this passed while the config it named did not exist, and mlx-audio
                # then failed with "Config not found" pointing at a folder it had been told was
                # complete.
                if shared is not None and (shared[1] / 'config.json').exists():
                    self._view_source[package_id] = {
                        'source': 'voice-cache',
                        'module': VOICE_CACHE_MODULE,
                        'path': str(Path(voice_cache.__file__).resolve()),
                        'folder': str(shared[1]),
                    }
                    return shared[1]
                reason = 'the shared Voice cache did not resolve this package'
            except Exception as exc:
                reason = f'{type(exc).__name__}: {exc}'
        else:
            reason = 'the manifest states no model_id to resolve through the shared Voice cache'
        model = folder / 'model'
        if any(model.glob('*.safetensors')):
            self._view_source[package_id] = {
                'source': 'library', 'folder': str(model),
                'reason': (f'the shared Voice cache did not answer ({reason}); the library folder '
                           'already ships *.safetensors weights this runtime can load')}
            return model
        npz_files = sorted(model.glob('*.npz'))
        # This worker carries its own state root name, so accept either.
        state = (os.environ.get('STUDIOHUB_DATA_DIR')
                 or os.environ.get('STUDIOHUB_SUBTITLE_DATA_DIR'))
        if not npz_files or not state:
            self._view_source[package_id] = {
                'source': 'library', 'folder': str(model),
                'reason': (f'the shared Voice cache did not answer ({reason}); the library ships '
                           + ('weights this runtime can load' if not npz_files
                              else 'no state root to build a converted view under')),
            }
            return model
        view = Path(state) / 'snapshot-views' / f'{package_id}-{revision[:16]}' / 'model'
        marker = view / '.studiohub-weights'
        # A view is built from absolute symlinks into the library, so it is only valid while that
        # library stays where it was. Reusing one whose links dangle is what produced "Config not
        # found" for a model whose config was present in the library all along.
        if marker.is_file():
            try:
                entries = [e for e in view.iterdir() if not e.name.startswith('.')]
            except OSError:
                entries = []
            # Every entry must resolve, and there must be at least one. An empty view is not a
            # finished view - it is the state that got reused forever and produced "Config not
            # found" for a model whose config was in the library the whole time.
            if entries and all(e.exists() for e in entries):
                self._view_source[package_id] = {
                    'source': 'local-view', 'folder': str(view),
                    'reason': f'the local builder answered because {reason}'}
                return view
            shutil.rmtree(view, ignore_errors=True)
        try:
            view.mkdir(parents=True, exist_ok=True)
            for entry in model.iterdir():
                link = view / entry.name
                if not link.exists() and not link.is_symlink():
                    os.symlink(entry.resolve(), link)
            companions = folder / 'companions' / 'processor'
            if companions.is_dir():
                for entry in companions.iterdir():
                    link = view / entry.name
                    if not link.exists() and not link.is_symlink():
                        os.symlink(entry.resolve(), link)
            import mlx.core as mx
            for npz in npz_files:
                mx.save_safetensors(str(view / (npz.stem + '.safetensors')),
                                    dict(mx.load(str(npz))))
            marker.write_text(f'{package_id}@{revision}\n', encoding='utf-8')
        except (OSError, ValueError, ImportError) as exc:
            self._view_source[package_id] = {
                'source': 'library', 'folder': str(model),
                'reason': (f'the local builder could not convert this package '
                           f'({type(exc).__name__}: {exc}) and {reason}')}
            return model
        self._view_source[package_id] = {
            'source': 'local-view', 'folder': str(view),
            'reason': f'the local builder answered because {reason}'}
        return view

    def package(self, package_id: str) -> PackageLocation:
        manifest = self.manifests.get(package_id)
        if manifest is None:
            raise ModelPackageError(f'unknown Subtitle model package {package_id!r}')
        folder = self.library / package_id / manifest['revision']
        return PackageLocation(
            package_id=package_id, revision=manifest['revision'], folder=folder,
            model_folder=self._loadable_model_folder(
                folder, package_id, manifest['revision'], manifest.get('model_id')),
            processor_folder=folder / 'companions' / 'processor', manifest=manifest)

    def _weight_files(self, location: PackageLocation) -> list[Path]:
        model = location.model_folder
        if not model.is_dir():
            return []
        return [path for pattern in SUPPORTED_WEIGHT_GLOBS
                for path in sorted(model.glob(pattern)) if path.is_file()]

    def file_state(self, package_id: str) -> dict:
        """Package files plus the exact reason this runtime cannot run them."""
        from components import model_packages
        location = self.package(package_id)
        try:
            receipt = model_packages.preflight(location.manifest, self.library)
        except Exception as exc:  # an unreadable library is an explicit state
            return {'state': 'invalid', 'reason': f'{type(exc).__name__}: {exc}',
                    'package_id': package_id, 'revision': location.revision,
                    'runtime_supported': False}
        weights = self._weight_files(location)
        runtime_supported = bool(weights)
        state = receipt['state']
        reason = receipt.get('reason')
        if state == 'files_verified' and not runtime_supported:
            state, reason = 'unsupported_weight_format', UNSUPPORTED_WEIGHT_NOTE
        return {**receipt, 'state': state, 'reason': reason,
                'package_id': package_id, 'revision': location.revision,
                'folder': str(location.folder) if location.folder.is_dir() else None,
                'model_folder': str(location.model_folder)
                if location.model_folder.is_dir() else None,
                'processor_folder': str(location.processor_folder)
                if location.processor_folder.is_dir() else None,
                'weight_files': [path.name for path in weights],
                'view_source': self._view_source.get(package_id),
                'runtime_supported': runtime_supported}

    def readiness(self, package_id: str) -> dict:
        files = self.file_state(package_id)
        host = host_eligibility()
        ready = files['state'] == 'files_verified' and host['eligible'] is True
        reason = None
        if not host['eligible']:
            reason = host.get('reason')
        elif not ready:
            reason = files.get('reason') or files['state']
        return {'ready': ready, 'reason': reason, 'files': files, 'host': host,
                'runtime_identity': {'id': self.runtime_sha256, 'compatible': True}}

    def catalog(self) -> list[dict]:
        entries = []
        for package_id in sorted(self.manifests):
            manifest = self.manifests[package_id]
            capabilities = self.capabilities[package_id]
            state = self.readiness(package_id)
            files = state['files']
            label = manifest.get('label') or _DEFAULT_LABELS.get(
                package_id, package_id.replace('-', ' ').title())
            entries.append({
                'package_id': package_id,
                'repo': manifest['model_id'],
                'model_id': manifest['model_id'],
                'label': label,
                'name': label,
                'family': capabilities['family'],
                'modality': 'transcription',
                'engine': manifest['engine'],
                'capabilities': ['transcribe', 'subtitles'],
                'hub_operation': 'audio.transcription',
                'model_revision': manifest['revision'],
                'qualified_revision': manifest['revision'],
                'qualified_revision_match': True,
                'runtime_revision': self.runtime_sha256,
                'runtime_compatible': True,
                'recommended': package_id == self._default_package,
                'default': package_id == self._default_package,
                'default_rule': self.default_rule,
                'cache': {'state': 'cached' if files['state'] == 'files_verified'
                          else files['state'], 'snapshot_revision': manifest['revision']},
                'readiness': {'ready': state['ready'], 'reason': state['reason'],
                              'files': files, 'host': state['host']},
                # The below-tier warning, in the one place a consumer reads it. It was computed by
                # `host_eligibility()` and published only inside `readiness.host`, one level down
                # and under a key no screen reads, so the sentence existed in the payload and could
                # never appear: the operator would have been told nothing while the app offered a
                # model this Mac is below the tier for. Every other worker publishes it at the top
                # level of the row (`image`, `music`, `chat`), and `tests/test_below_tier_warning.py`
                # now requires a page to render whatever a worker publishes here.
                'memory_warning': state['host'].get('memory_warning'),
                'execution_ready': state['ready'],
                'available': state['ready'],
                # Published from the manifest, never asserted here: this worker used to claim
                # Multilingual, segment and word timestamps and long form for every package it
                # loaded, because no manifest declared any of them and nothing could be wrong.
                'languages': capabilities['languages'],
                'supports_segment_timestamps': capabilities['supports_segment_timestamps'],
                'supports_word_timestamps': capabilities['supports_word_timestamps'],
                'supports_long_form': capabilities['supports_long_form'],
                # What a scheduler sends and gets back for this lane, from the worker that owns
                # the API (`docs/lane-execution-contract.md`).
                'lane_contract': lane_contract(capabilities, self.lane_parameters()),
                'min_total_memory_gb': 8,
                'qualification': manifest.get('qualification') or {},
            })
        return entries

    def lane_parameters(self) -> list[dict]:
        """The asked-for surface of ``POST /api/transcribe``, with the bounds it enforces.

        Built from the same constants the route and the request validator use, so a parameter
        that is accepted and dropped cannot appear here, and the model list is the catalog's own
        manifests - a package added to the catalog appears without editing this.
        """
        # Deferred: the upload bounds live with the manager that enforces them, and the manager
        # imports this module.
        from .manager import ALLOWED_EXTENSIONS, MAX_INPUT_BYTES
        return [
            {'name': 'file', 'type': 'string', 'required': True, 'max_bytes': MAX_INPUT_BYTES,
             'enum': sorted(ALLOWED_EXTENSIONS),
             'note': ('multipart upload; the filename extension must be one of the listed values '
                      'and the body must be no larger than max_bytes, or the request is refused '
                      'before anything is read')},
            {'name': 'model', 'type': 'string', 'required': False,
             'enum': sorted({spelling for package_id, manifest in self.manifests.items()
                             for spelling in (package_id, manifest['model_id'])}),
             'default': self._default_package,
             'note': 'either a package id or the model repository it was published under'},
            {'name': 'language', 'type': 'string', 'required': False,
             'note': ('a language hint shaped like a BCP-47 tag; omitted or empty means the '
                      'engine detects the spoken language')},
            {'name': 'word_timestamps', 'type': 'boolean', 'required': False, 'default': False,
             'note': 'per-word timings in the returned segments; false returns segment timings'},
            {'name': 'activity_id', 'type': 'string', 'required': False, 'max_characters': 32,
             'note': ('the Hub activity this item belongs to; a longer value is truncated to 32 '
                      'characters, not refused')},
        ]

    def default_selection(self) -> dict:
        """Which package is the default, and by which rule — the rule is published, not silent.

        ``package_id`` is ``None`` only when the engine was built with no manifest at all, which
        the packaged worker refuses before it starts.
        """
        return {'package_id': self._default_package, 'rule': self.default_rule}

    def _select_default(self) -> tuple[Optional[str], str]:
        """The default model, read from the data; the fallback rule names itself.

        `"default": true` is a declaration in one manifest, and exactly one manifest may carry
        it. With none, the worker still has to pick something, so it picks the first package id
        in sort order and says that is what it did - a silent alphabetical fallback is how
        retiring the model a hardcoded name pointed at changed the default without anyone
        choosing.
        """
        if not self.manifests:
            # A worker with no subtitle manifest has nothing to pick a default *from*. That is a
            # state the packaged worker refuses before it starts (`load_subtitle_manifests` names
            # the manifest directory and the engine it looked for), so it is not made fatal here:
            # a caller that builds an engine for something else - the cancellation and credential
            # tests do - must not be stopped by a catalog it never asked about.
            print('[subtitle] no subtitle manifest is loaded; there is no default model',
                  flush=True)
            return None, 'no-catalog'
        declared = sorted(package_id for package_id, manifest in self.manifests.items()
                          if manifest.get('default') is True)
        if len(declared) > 1:
            raise ModelPackageError(
                f'{len(declared)} subtitle manifests declare "default": true '
                f'({", ".join(declared)}); at most one package in the catalog may be the default. '
                f'Remove the flag from all but one manifest in model-manifests/.')
        if declared:
            return declared[0], 'declared'
        fallback = sorted(self.manifests)[0]
        print(f'[subtitle] no manifest declares "default": true; using {fallback!r}, '
              f'the first package id in sort order', flush=True)
        return fallback, 'first-package-id'

    def default_package(self) -> Optional[str]:
        """The default package id, or None when this engine was built with no manifest."""
        return self._default_package

    # ── loading / decoding ─────────────────────────────────────────────────

    def _companion_target(self) -> Path:
        """Private processor staging root, inside Subtitle's own state."""
        return self.processor_cache

    def _private_processor(self, location: PackageLocation) -> Path:
        """Copy the verified companion tokenizer into Subtitle's own state.

        The pinned package must stay byte- and metadata-immutable: loading the
        tokenizer straight out of it changes that tree's file metadata and makes
        the ST-01 receipt report ``File changed; verify again`` after every real
        transcription (observed: all nine package files bumped ctime). The
        Subtitle worker therefore loads the tokenizer from its own copy and
        never writes into the model library.

        Each copy is re-verified against the manifest's ``companions/`` SHA-256
        entries before use, so a tampered or stale copy cannot be loaded.
        """
        source = location.processor_folder
        if not source.is_dir():
            raise ModelPackageError(
                f'companion processor folder is missing for {location.package_id}')
        from components import model_packages
        target = self._companion_target() / location.package_id / location.revision
        entries = {}
        for entry in location.manifest['files']:
            relative = Path(entry['path'])
            try:
                parts = relative.parts
            except (TypeError, ValueError):
                continue
            if len(parts) > 1 and parts[0] == 'companions':
                entries[relative] = entry
        if not entries:
            raise ModelPackageError(
                f'{location.package_id} declares no companion processor files')
        target.mkdir(parents=True, exist_ok=True)
        for relative, entry in entries.items():
            # ``source`` is the processor folder; the manifest path adds the
            # ``companions/<folder>/`` prefix in front of the file name.
            source_path = source / relative.name
            if not source_path.is_file() or source_path.is_symlink():
                raise ModelPackageError(
                    f'companion file is missing or a symlink: {relative}')
            body = source_path.read_bytes()
            if sha256(body).hexdigest() != entry['sha256'] or len(body) != entry['size']:
                raise ModelPackageError(
                    f'companion file failed verification: {relative}')
            destination = target / source_path.name
            temporary = target / f'.{source_path.name}.part'
            temporary.write_bytes(body)
            os.chmod(temporary, 0o600)
            os.replace(temporary, destination)
        return target

    def _attach_local_processor(self, model, location: PackageLocation) -> str:
        """Attach a tokenizer from the verified companion copy, never the net.

        The narrow ``WhisperTokenizer`` import mirrors Voice Studio's adopted
        path: the full ``WhisperProcessor`` chain is avoided because it can raise
        an unrelated ImportError on a version-drifted environment, and mlx-audio
        only reads ``model._processor.tokenizer``.
        """
        processor = self._private_processor(location)
        from transformers import WhisperTokenizer
        tokenizer = WhisperTokenizer.from_pretrained(str(processor))
        model._processor = _TokenizerOnlyProcessor(tokenizer)
        return str(processor)

    def load(self, package_id: str) -> tuple[object, dict]:
        """Load one Whisper model from its verified folder; cache per package."""
        with self._lock:
            if self._model is not None and self._model_package == package_id:
                return self._model, {'loaded': False, 'reused': True}
            location = self.package(package_id)
            state = self.file_state(package_id)
            if state['state'] != 'files_verified':
                raise ModelPackageError(
                    f'{package_id} is not runnable here: {state.get("reason") or state["state"]}')
            if self._model is not None:
                self._model = None
                self._model_package = None
            started = time.time()
            if self.model_factory is not None:
                model = self.model_factory(location)
                attached = str(location.processor_folder)
            else:
                from mlx_audio.stt.utils import load_model
                model = load_model(location.model_folder)
                attached = self._attach_local_processor(model, location)
            self._model = model
            self._model_package = package_id
            return model, {'loaded': True, 'reused': False,
                           'model_folder': str(location.model_folder),
                           'processor_folder': attached,
                           'load_seconds': round(time.time() - started, 3)}

    def unload(self, reason: str = 'manual') -> dict:
        with self._lock:
            had = self._model is not None
            self._model = None
            self._model_package = None
        return {'released': had, 'reason': reason}

    @property
    def loaded_package(self) -> Optional[str]:
        return self._model_package

    def transcribe(self, audio_path: str | Path, *, package_id: str,
                   language: Optional[str] = None, word_timestamps: bool = False,
                   cancelled: Optional[Callable[[], bool]] = None,
                   progress: Optional[Callable[[float], None]] = None) -> dict:
        """Decode one audio/video file into text, segments and SRT/VTT.

        The decode call matches the Voice service's pinned mlx-audio Whisper
        entry point. Cancellation is cooperative and checked at the boundaries
        where the current mlx-audio release permits it: before the model load,
        and immediately after decoding. A cancel that arrives during decoding
        still discards the result and publishes no artifact.
        """
        path = Path(audio_path)
        if not path.is_file():
            raise FileNotFoundError(f'audio file not found: {path}')
        if cancelled is not None and cancelled():
            raise _Cancelled()
        started = time.time()
        with self.inference_lock:
            if cancelled is not None and cancelled():
                raise _Cancelled()
            if progress is not None:
                progress(0.1)
            model, load_receipt = self.load(package_id)
            if progress is not None:
                progress(0.2)
            with decodable_audio(path, self.voice_app_dir) as media:
                duration = audio_duration_seconds(media, self.voice_app_dir)
                result = model.generate(
                    str(media),
                    language=(language or '').strip() or None,
                    word_timestamps=bool(word_timestamps),
                    return_timestamps=True,
                    condition_on_previous_text=CONDITION_ON_PREVIOUS_TEXT,
                )
            if cancelled is not None and cancelled():
                raise _Cancelled()
            if progress is not None:
                progress(0.9)
        raw_text = getattr(result, 'text', None)
        detected = getattr(result, 'language', None)
        raw_segments = getattr(result, 'segments', None) or []
        normalized_text, segments = normalize_segments(
            raw_segments, word_timestamps=bool(word_timestamps),
            audio_duration=duration, voice_app_dir=self.voice_app_dir)
        text = (normalized_text or (raw_text or '').strip()).strip()
        media_duration = (duration if duration is not None
                          else (segments[-1]['end'] if segments else 0.0))
        artifacts = format_artifacts(segments, self.voice_app_dir)
        return {
            'text': text,
            'language': detected or (language or '').strip() or 'en',
            'duration': round(float(media_duration), 3),
            'model': self.manifests[package_id]['model_id'],
            'package_id': package_id,
            'model_revision': self.manifests[package_id]['revision'],
            'segments': segments,
            'srt': artifacts['srt'],
            'vtt': artifacts['vtt'],
            'text_file': artifacts['text'],
            'elapsed_seconds': round(time.time() - started, 2),
            'model_load': load_receipt,
        }


class _TokenizerOnlyProcessor:
    """Minimal ``.tokenizer`` carrier for mlx-audio's ``get_tokenizer()``.

    Same narrow stand-in the Voice service adopted; kept here so the Subtitle
    source does not need the full WhisperProcessor import chain.
    """
    def __init__(self, tokenizer) -> None:
        self.tokenizer = tokenizer
