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

from dataclasses import dataclass
from hashlib import sha256
import importlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import threading
import time
from typing import Callable, Optional

# The existing Voice transcription module — the single tracked source location
# for decoding/export behavior. Imported as a package module so its relative
# imports resolve exactly as they do inside the Voice service.
VOICE_TRANSCRIPTION_MODULE = 'backend.transcription'
VOICE_TRANSCRIPTION_SOURCE = 'components/voice/app/backend/transcription.py'

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
    if memory < MINIMUM_UNIFIED_MEMORY_BYTES:
        return {'eligible': False,
                'reason': 'Whisper transcription requires 8 GB unified memory'}
    return {'eligible': True, 'macos': version, 'unified_memory_bytes': memory,
            'minimum_unified_memory_bytes': MINIMUM_UNIFIED_MEMORY_BYTES}


def voice_export_module():
    """Import the existing Voice transcription module at its one source path.

    ``STUDIOHUB_VOICE_APP_DIR`` points at the tracked ``components/voice/app``
    directory. In the packaged bundle the Voice source ships for exactly this
    shared reuse; the private Subtitle process still owns only its own state.
    """
    voice_app = os.environ.get('STUDIOHUB_VOICE_APP_DIR', '').strip()
    if not voice_app:
        raise RuntimeError('STUDIOHUB_VOICE_APP_DIR is required to reuse the Voice export source')
    root = str(Path(voice_app).expanduser().resolve())
    if root not in sys.path:
        sys.path.insert(0, root)
    return importlib.import_module(VOICE_TRANSCRIPTION_MODULE)


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
    module = voice_export_module()
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
    module = voice_export_module()
    return module._normalize_segments(
        raw_segments, word_timestamps=word_timestamps, audio_duration=audio_duration)


def audio_duration_seconds(path: Path) -> Optional[float]:
    """True media duration, so padded-silence output can be discarded.

    Delegates to the existing Voice probe helper rather than adding a second
    media-probing implementation.
    """
    module = voice_export_module()
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
        self.offline = bool(offline)
        self._model = None
        self._model_package: Optional[str] = None
        self._lock = threading.RLock()
        self.inference_lock = threading.Lock()
        self._export = one_source_location(self.voice_app_dir)

    # ── catalog / readiness ────────────────────────────────────────────────

    def export_source(self) -> dict:
        return dict(self._export)

    def package(self, package_id: str) -> PackageLocation:
        manifest = self.manifests.get(package_id)
        if manifest is None:
            raise ModelPackageError(f'unknown Subtitle model package {package_id!r}')
        folder = self.library / package_id / manifest['revision']
        return PackageLocation(
            package_id=package_id, revision=manifest['revision'], folder=folder,
            model_folder=folder / 'model',
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
                'family': manifest.get('family') or 'whisper',
                'modality': 'transcription',
                'engine': manifest['engine'],
                'capabilities': ['transcribe', 'subtitles'],
                'hub_operation': 'audio.transcription',
                'model_revision': manifest['revision'],
                'qualified_revision': manifest['revision'],
                'qualified_revision_match': True,
                'runtime_revision': self.runtime_sha256,
                'runtime_compatible': True,
                'recommended': package_id == self.default_package(),
                'default': package_id == self.default_package(),
                'cache': {'state': 'cached' if files['state'] == 'files_verified'
                          else files['state'], 'snapshot_revision': manifest['revision']},
                'readiness': {'ready': state['ready'], 'reason': state['reason'],
                              'files': files, 'host': state['host']},
                'execution_ready': state['ready'],
                'available': state['ready'],
                'languages': 'Multilingual',
                'supports_segment_timestamps': True,
                'supports_word_timestamps': True,
                'supports_long_form': True,
                'min_total_memory_gb': 8,
                'qualification': manifest.get('qualification') or {},
            })
        return entries

    def default_package(self) -> str:
        """Turbo is the recommended editable initial default."""
        for package_id, manifest in self.manifests.items():
            if manifest.get('default') is True:
                return package_id
        if 'whisper-turbo' in self.manifests:
            return 'whisper-turbo'
        return sorted(self.manifests)[0]

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
            duration = audio_duration_seconds(path)
            result = model.generate(
                str(path),
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
