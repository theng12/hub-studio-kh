"""Independent Subtitle service: contract, lifecycle and export tests.

The suite separates two kinds of proof:

* Contract/lifecycle cases use a deterministic fake decoder and a temporary
  library built with the ST-01 ``import_folder`` helper. They prove the
  service's own behavior, not model accuracy.
* ``SubtitleRealModelTests`` runs the real Whisper engine against the verified
  local packages and is skipped unless ``STUDIOHUB_TEST_MODEL_LIBRARY`` names
  a library. A skip is reported as a skip, never as a pass.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
from pathlib import Path
import struct
import sys
import tempfile
import time
import unittest
import wave

import httpx
from starlette.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from components import model_defaults
from components import model_packages
from components.subtitle.app.backend import engine as engine_module
from components.subtitle.app.backend.main import create_app
from components.subtitle.app.backend.manager import SubtitleManager

TOKEN = 'subtitle-test-token-0123456789abcdef'
VOICE_APP = PROJECT_ROOT / 'components' / 'voice' / 'app'
REAL_LIBRARY = os.environ.get('STUDIOHUB_TEST_MODEL_LIBRARY', '').strip()
TURBO_REVISION = 'a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb'


def _write_tone(path: Path, seconds: float = 1.2, rate: int = 16000) -> bytes:
    """A small deterministic WAV so media probing and uploads are exercised."""
    frames = bytearray()
    total = int(seconds * rate)
    for index in range(total):
        burst = 1.0 if (index // 2000) % 2 == 0 else 0.0
        value = int(6000 * burst * math.sin(2 * math.pi * 220 * index / rate))
        frames += struct.pack('<h', value)
    with wave.open(str(path), 'wb') as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(bytes(frames))
    return path.read_bytes()


class _FakeModel:
    """Deterministic mlx-audio-shaped result for lifecycle testing."""

    def __init__(self, segments, text='hello world'):
        self.segments = segments
        self.text = text
        self.language = 'en'
        self.calls: list[dict] = []

    def generate(self, audio, **options):
        self.calls.append({'audio': audio, **options})
        return self


def _fake_factory(segments, text='hello world'):
    holder: dict = {}

    def factory(location):
        model = _FakeModel([dict(segment) for segment in segments], text=text)
        holder['model'] = model
        return model
    return factory, holder


class SubtitleFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temporary.name)
        self.library = self.root / 'library'
        self.library.mkdir()
        self.data = self.root / 'state'
        self.audio = self.root / 'clip.wav'
        self.audio_bytes = _write_tone(self.audio)
        # Turbo is the declared default, as its shipped manifest declares it; every fixture
        # carries the capability block its manifest would, because the worker now publishes
        # exactly that block and nothing it was not told.
        self.manifests = [self._package('whisper-turbo', 'weights.safetensors', default=True),
                          self._package('whisper-base', 'weights.npz'),
                          self._package('whisper-small', 'weights.npz')]
        # A saved memory policy must land in this worker's own state directory. Without the
        # environment the poller and the settings writer would resolve it relative to the
        # working directory and drop a memory_policy.json into the checkout.
        self.old_environ = os.environ.copy()
        os.environ['STUDIOHUB_SUBTITLE_DATA_DIR'] = str(self.data)

    def tearDown(self) -> None:
        manager = getattr(self, 'manager', None)
        if manager is not None:
            manager.close()
        os.environ.clear()
        os.environ.update(self.old_environ)
        self.temporary.cleanup()

    def _package(self, package_id: str, weight_name: str, **declared) -> dict:
        revision = {
            'whisper-turbo': TURBO_REVISION,
            'whisper-base': '1e3e249fb8d01c655324bd6841b1deadffd6d04c',
            'whisper-small': '45f3915923c7a79a5a5b5a7d909d39aeb0e5630e',
        }[package_id]
        model_id = {'whisper-turbo': 'mlx-community/whisper-large-v3-turbo',
                    'whisper-base': 'mlx-community/whisper-base-mlx',
                    'whisper-small': 'mlx-community/whisper-small-mlx'}[package_id]
        source = self.root / f'source-{package_id}'
        (source / 'model').mkdir(parents=True, exist_ok=True)
        (source / 'companions' / 'processor').mkdir(parents=True, exist_ok=True)
        (source / 'model' / 'config.json').write_text('{"n_mels": 80}\n')
        (source / 'model' / weight_name).write_bytes(f'{package_id}-weights'.encode())
        for name in ('vocab.json', 'tokenizer.json', 'tokenizer_config.json'):
            (source / 'companions' / 'processor' / name).write_text('{}\n')
        files = []
        for path in sorted((source).rglob('*')):
            if not path.is_file():
                continue
            body = path.read_bytes()
            files.append({
                'path': path.relative_to(source).as_posix(),
                'size': len(body),
                'sha256': hashlib.sha256(body).hexdigest(),
                'urls': [f'https://example.invalid/{path.name}'],
            })
        manifest = {'schema_version': 1, 'package_id': package_id,
                    'model_id': model_id, 'revision': revision,
                    'engine': 'subtitle-mlx', 'minimum_macos': '14.0',
                    'subtitle': declared.pop('subtitle', None) or {
                        'family': 'whisper', 'languages': ['multilingual'],
                        'segment_timestamps': True, 'word_timestamps': True,
                        'long_form': True},
                    'files': files, 'source': {'provider': 'fixture'}}
        manifest.update(declared)
        model_packages.import_folder(manifest, source, self.library)
        return manifest

    def build_manager(self, factory=None, segments=None, **kwargs) -> SubtitleManager:
        if factory is None:
            factory, _ = _fake_factory(segments or [
                {'start': 0.0, 'end': 0.5, 'text': 'hello world'},
                {'start': 0.6, 'end': 1.0, 'text': 'second cue'},
            ])
        manager = SubtitleManager(self.data, self.library, self.manifests, 'b' * 64,
                                  voice_app_dir=VOICE_APP, **kwargs)
        manager.engine.model_factory = factory
        return manager

    def wait(self, manager: SubtitleManager, job_id: str, timeout: float = 30.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            value = manager.get(job_id)
            if value and value['state'] in ('done', 'error', 'cancelled'):
                return value
            time.sleep(0.02)
        self.fail(f'job {job_id} did not reach a terminal state')

    def submit(self, manager: SubtitleManager, **overrides) -> dict:
        source = manager.store_input('test', 'clip.wav', self.audio_bytes)
        request = {'model': 'whisper-turbo', 'word_timestamps': False,
                   'formats': ['srt', 'vtt', 'text'], **overrides}
        return manager.submit(request, source)


class ExportSourceTests(SubtitleFixture):
    """ST-06.1 — one tracked source location for decoding and exports."""

    def test_export_behavior_comes_from_the_voice_source(self) -> None:
        manager = self.build_manager()
        receipt = manager.engine.export_source()
        path = Path(receipt['path'])
        self.assertEqual(path, VOICE_APP / 'backend' / 'transcription.py')
        self.assertTrue(path.is_file())
        self.assertEqual(receipt['sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(manager.engine.voice_app_dir, VOICE_APP)

    def test_no_formatter_is_reimplemented_in_subtitle(self) -> None:
        """The Subtitle tree must not carry its own cue formatters."""
        for path in (PROJECT_ROOT / 'components' / 'subtitle').rglob('*.py'):
            body = path.read_text(encoding='utf-8')
            self.assertNotIn('-->', body,
                             f'{path} appears to build cue timestamps itself')
            self.assertNotIn('def segments_to_srt', body)
            self.assertNotIn('def segments_to_vtt', body)

    def test_exports_match_the_voice_functions_exactly(self) -> None:
        segments = [{'id': 0, 'start': 0.0, 'end': 1.5, 'text': 'First cue'},
                    {'id': 1, 'start': 2.0, 'end': 3.25, 'text': 'Second cue'}]
        artifacts = engine_module.format_artifacts(segments, VOICE_APP)
        from backend import transcription as voice
        self.assertEqual(artifacts['srt'], voice.segments_to_srt(segments))
        self.assertEqual(artifacts['vtt'], voice.segments_to_vtt(segments))
        self.assertTrue(artifacts['srt'].startswith('1\n00:00:00,000 --> 00:00:01,500'))
        self.assertTrue(artifacts['vtt'].startswith('WEBVTT'))
        self.assertEqual(artifacts['text'], 'First cue Second cue')

    def test_the_export_helpers_honour_the_directory_they_are_given(self) -> None:
        """Passing the Voice app path must be enough; the environment is only a fallback.

        These helpers accepted a directory and then read ``STUDIOHUB_VOICE_APP_DIR``
        instead, so calling one correctly still failed unless an unrelated earlier caller
        had happened to set the variable.
        """
        segments = [{'id': 0, 'start': 0.0, 'end': 1.0, 'text': 'Cue'}]
        original = os.environ.pop('STUDIOHUB_VOICE_APP_DIR', None)
        try:
            import sys
            sys.path.insert(0, str(VOICE_APP))
            from backend import transcription as voice
            self.assertEqual(engine_module.format_artifacts(segments, VOICE_APP)['srt'],
                             voice.segments_to_srt(segments))
            self.assertEqual(
                engine_module.normalize_segments(segments, word_timestamps=False,
                                                 audio_duration=1.0,
                                                 voice_app_dir=VOICE_APP)[0], 'Cue')
            with self.assertRaisesRegex(RuntimeError, 'STUDIOHUB_VOICE_APP_DIR'):
                engine_module.format_artifacts(segments, None)
        finally:
            if original is not None:
                os.environ['STUDIOHUB_VOICE_APP_DIR'] = original

    def test_text_srt_vtt_are_published_together(self) -> None:
        manager = self.build_manager()
        job = self.submit(manager)
        value = self.wait(manager, job['id'])
        self.assertEqual(value['state'], 'done', value.get('error'))
        self.assertEqual(sorted(value['artifacts']), ['srt', 'text', 'vtt'])
        srt = Path(value['artifacts']['srt']['path']).read_text()
        vtt = Path(value['artifacts']['vtt']['path']).read_text()
        text = Path(value['artifacts']['text']['path']).read_text()
        self.assertIn('hello world', srt)
        self.assertTrue(vtt.startswith('WEBVTT'))
        self.assertEqual(text, 'hello world second cue')
        self.assertEqual(value['text'], 'hello world second cue')
        self.assertEqual(value['segment_count'], 2)


class CatalogContractTests(SubtitleFixture):
    """ST-06.4 — Base, Small and Turbo pinned, Turbo the recommended default."""

    def test_catalog_publishes_the_manifests_it_was_given(self) -> None:
        """The fixture's own manifests are the expectation, not a hand-kept list."""
        manager = self.build_manager()
        entries = {entry['package_id']: entry for entry in manager.catalog()}
        self.assertEqual(sorted(entries), sorted(m['package_id'] for m in self.manifests))
        self.assertEqual(entries['whisper-turbo']['model_revision'], TURBO_REVISION)
        self.assertTrue(entries['whisper-turbo']['recommended'])
        self.assertEqual(manager.default_package(), 'whisper-turbo')
        for entry in entries.values():
            self.assertEqual(entry['engine'], 'subtitle-mlx')
            self.assertEqual(entry['modality'], 'transcription')
            self.assertEqual(entry['hub_operation'], 'audio.transcription')
            self.assertEqual(entry['runtime_revision'], 'b' * 64)

    def test_a_small_mac_is_warned_about_not_refused(self) -> None:
        """Whisper's 8 GB floor is a tier, not a wall, exactly as FLUX's 16 GB is."""
        from unittest.mock import patch

        def small_mac(_command, **_kwargs):
            return str(6 * 1024 ** 3)

        # The probe caches the host's memory for the process lifetime, so a value read by any
        # earlier test would survive the patch and this test would see this Mac instead of the
        # 6 GB one it is describing.
        engine_module._MEMORY_CACHE.clear()
        self.addCleanup(engine_module._MEMORY_CACHE.clear)
        with patch.object(engine_module.subprocess, 'check_output', small_mac):
            host = engine_module.host_eligibility()
        self.assertTrue(host['eligible'], host)
        self.assertIn('6 GB of unified memory', host['memory_warning'])
        self.assertIn('qualified on 8 GB', host['memory_warning'])
        self.assertEqual(host['minimum_unified_memory_bytes'], engine_module.MINIMUM_UNIFIED_MEMORY_BYTES)

        # At or above the tier there is nothing to warn about. The probe caches the host's
        # memory for the process lifetime, so the cache is cleared between the two Macs.
        def qualified(_command, **_kwargs):
            return str(8 * 1024 ** 3)

        engine_module._MEMORY_CACHE.clear()
        self.addCleanup(engine_module._MEMORY_CACHE.clear)
        with patch.object(engine_module.subprocess, 'check_output', qualified):
            self.assertIsNone(engine_module.host_eligibility()['memory_warning'])

        # A refused machine is still refused: capability is not memory.
        with patch.object(engine_module.platform, 'system', lambda: 'Linux'):
            self.assertFalse(engine_module.host_eligibility()['eligible'])

    def test_the_below_tier_warning_is_published_where_the_app_reads_it(self) -> None:
        """A sentence nothing renders is not a warning.

        `host_eligibility()` has computed this since the worker shipped, and every catalog row
        published it only inside `readiness.host` — one level down, under a key no screen reads —
        so an operator below the tier was told nothing while the app offered the model. `image`,
        `music` and `chat` all publish it at the **top level** of the row, which is where
        `NativeModel.memoryWarning` decodes from; this is that same place.
        """
        from unittest.mock import patch

        manager = self.build_manager()

        def small_mac(_command, **_kwargs):
            return str(6 * 1024 ** 3)

        engine_module._MEMORY_CACHE.clear()
        self.addCleanup(engine_module._MEMORY_CACHE.clear)
        with patch.object(engine_module.subprocess, 'check_output', small_mac):
            small = {entry['package_id']: entry for entry in manager.catalog()}
        self.assertTrue(small, 'the fixture must publish at least one model')
        for package_id, entry in small.items():
            with self.subTest(package=package_id):
                warning = entry.get('memory_warning')
                self.assertIsInstance(warning, str,
                                      'a 6 GB Mac must be told it is below the 8 GB tier')
                self.assertIn('6 GB of unified memory', warning)
                # One source, not two: the top-level field and the nested host block are the same
                # sentence, so a consumer reading either cannot be told something different.
                self.assertEqual(warning, entry['readiness']['host']['memory_warning'])

        engine_module._MEMORY_CACHE.clear()
        with patch.object(engine_module.subprocess, 'check_output',
                          lambda *args, **kwargs: str(8 * 1024 ** 3)):
            qualified = {entry['package_id']: entry for entry in manager.catalog()}
        for package_id, entry in qualified.items():
            with self.subTest(package=package_id):
                self.assertIsNone(entry.get('memory_warning'),
                                  'a Mac at the tier must be told nothing')

        # Advice, never a gate — and this is the assertion that says so without depending on
        # anything else: memory decides **nothing** about what this Mac can run. Each package's
        # availability is identical on the below-tier Mac and the qualified one. (Individual
        # packages differ from each other for their own reasons — a `weights.npz` package that
        # needs a converted view is not available in this fixture — and none of that is memory.)
        for package_id, entry in small.items():
            with self.subTest(package=package_id):
                self.assertFalse(entry['readiness']['host']['eligible'] is False,
                                 'a below-tier Mac is still an eligible host')
                self.assertEqual(entry['available'], qualified[package_id]['available'])
                self.assertEqual(entry['execution_ready'], qualified[package_id]['execution_ready'])
                self.assertEqual(entry['readiness']['ready'], qualified[package_id]['readiness']['ready'])

    def test_npz_only_packages_are_reported_not_runnable(self) -> None:
        """Base/Small ship weights.npz, which this mlx-audio loader cannot read."""
        manager = self.build_manager()
        entries = {entry['package_id']: entry for entry in manager.catalog()}
        for package_id in ('whisper-base', 'whisper-small'):
            files = entries[package_id]['readiness']['files']
            self.assertEqual(files['state'], 'unsupported_weight_format')
            self.assertFalse(files['runtime_supported'])
            self.assertFalse(entries[package_id]['readiness']['ready'])
            self.assertIn('safetensors', entries[package_id]['readiness']['reason'])
        turbo = entries['whisper-turbo']['readiness']
        self.assertTrue(turbo['ready'])
        self.assertTrue(turbo['files']['runtime_supported'])

    def test_submitting_a_non_runnable_package_is_refused(self) -> None:
        manager = self.build_manager()
        source = manager.store_input('test', 'clip.wav', self.audio_bytes)
        with self.assertRaises(engine_module.ModelPackageError) as caught:
            manager.submit({'model': 'whisper-base', 'formats': ['srt']}, source)
        self.assertIn('safetensors', str(caught.exception))

    def test_unknown_or_unpackaged_model_is_refused(self) -> None:
        manager = self.build_manager()
        with self.assertRaises(engine_module.ModelPackageError):
            manager.engine.package('whisper-nonexistent')

    def test_request_validation_rejects_bad_input(self) -> None:
        from components.subtitle.app.backend.manager import validate_request
        packages = ['whisper-turbo']
        with self.assertRaises(ValueError):
            validate_request({'model': 'other', 'formats': ['srt']},
                             packages=packages, default_package='whisper-turbo')
        with self.assertRaises(ValueError):
            validate_request({'language': 'not a language!', 'formats': ['srt']},
                             packages=packages, default_package='whisper-turbo')
        with self.assertRaises(ValueError):
            validate_request({'word_timestamps': 'yes', 'formats': ['srt']},
                             packages=packages, default_package='whisper-turbo')
        with self.assertRaises(ValueError):
            validate_request({'formats': ['pdf']},
                             packages=packages, default_package='whisper-turbo')
        value = validate_request({'formats': ['vtt']},
                                 packages=packages, default_package='whisper-turbo')
        self.assertEqual(value['formats'], ['srt', 'vtt'])
        self.assertIsNone(value['language'])

    def test_media_extension_and_filename_guards(self) -> None:
        from components.subtitle.app.backend.manager import safe_input_name
        for name in ('clip.wav', 'a b (1).mp4', 'x.MOV'):
            self.assertEqual(safe_input_name(name), name)
        for name in ('', '..', '../escape.wav', 'a/b.wav', 'clip.txt', 'clip', 'bad\x00.wav'):
            with self.assertRaises(ValueError, msg=name):
                safe_input_name(name)


class SubtitleCapabilityPublicationTests(SubtitleFixture):
    """B13/B14 — what is published about a package comes from that package's manifest.

    The fixture is the mislabelling the audit found: `family` was read with a `'whisper'`
    fallback, and Multilingual plus all three timestamp/long-form capabilities were asserted
    for every package the worker loaded. A non-Whisper engine was therefore published as
    Whisper, and a model that cannot produce word timestamps was advertised as able to.
    """

    def manager_with(self, manifests, **kwargs) -> SubtitleManager:
        manager = SubtitleManager(self.data, self.library, manifests, 'b' * 64,
                                  voice_app_dir=VOICE_APP, **kwargs)
        self.addCleanup(manager.close)
        factory, _ = _fake_factory([{'start': 0.0, 'end': 1.0, 'text': 'hello'}])
        manager.engine.model_factory = factory
        return manager

    def non_whisper_package(self) -> dict:
        """An English-only model with no word timings and no long form."""
        manifest = json.loads(json.dumps(self.manifests[0]))
        manifest.update({'package_id': 'parakeet-tdt-0-6b',
                         'model_id': 'nvidia/parakeet-tdt-0.6b-v2', 'revision': 'f' * 40})
        manifest.pop('default', None)
        manifest['subtitle'] = {'family': 'parakeet', 'languages': ['en'],
                                'segment_timestamps': True, 'word_timestamps': False,
                                'long_form': False}
        model_packages.validate_manifest(manifest)
        return manifest

    def test_every_published_capability_is_the_one_its_manifest_declares(self) -> None:
        manager = self.build_manager()
        entries = {entry['package_id']: entry for entry in manager.catalog()}
        for manifest in self.manifests:
            block = manifest['subtitle']
            entry = entries[manifest['package_id']]
            self.assertEqual(entry['family'], block['family'])
            self.assertEqual(entry['languages'], block['languages'])
            self.assertEqual(entry['supports_segment_timestamps'], block['segment_timestamps'])
            self.assertEqual(entry['supports_word_timestamps'], block['word_timestamps'])
            self.assertEqual(entry['supports_long_form'], block['long_form'])

    def test_a_non_whisper_package_is_published_as_what_it_is(self) -> None:
        parakeet = self.non_whisper_package()
        manager = self.manager_with([*self.manifests, parakeet])
        entry = {item['package_id']: item for item in manager.catalog()}['parakeet-tdt-0-6b']
        self.assertEqual(entry['family'], 'parakeet')
        self.assertEqual(entry['languages'], ['en'])
        self.assertTrue(entry['supports_segment_timestamps'])
        self.assertFalse(entry['supports_word_timestamps'],
                         'a model with no word timings must not be advertised as able to')
        self.assertFalse(entry['supports_long_form'])

    def test_the_availability_payload_restates_nothing_of_its_own(self) -> None:
        """The second place the old code asserted True; it must mirror the catalog exactly."""
        manager = self.manager_with([*self.manifests, self.non_whisper_package()])
        catalog = {entry['package_id']: entry for entry in manager.catalog()}
        for model in manager.availability()['models']:
            entry = catalog[model['package_id']]
            self.assertEqual(model['family'], entry['family'])
            self.assertEqual(model['languages'], entry['languages'])
            self.assertEqual(model['supports_segment_timestamps'],
                             entry['supports_segment_timestamps'])
            self.assertEqual(model['supports_word_timestamps'],
                             entry['supports_word_timestamps'])
            self.assertEqual(model['supports_long_form'], entry['supports_long_form'])

    def test_a_manifest_without_the_block_fails_loudly_instead_of_defaulting(self) -> None:
        broken = json.loads(json.dumps(self.manifests[0]))
        broken.pop('subtitle')
        with self.assertRaises(engine_module.ModelPackageError) as caught:
            SubtitleManager(self.data, self.library, [*self.manifests, broken], 'b' * 64,
                            voice_app_dir=VOICE_APP)
        self.assertIn('whisper-turbo', str(caught.exception))
        self.assertIn('subtitle', str(caught.exception))

    def test_an_incomplete_block_fails_loudly_naming_the_field(self) -> None:
        broken = json.loads(json.dumps(self.manifests[0]))
        broken['subtitle'].pop('word_timestamps')
        with self.assertRaises(engine_module.ModelPackageError) as caught:
            SubtitleManager(self.data, self.library, [*self.manifests, broken], 'b' * 64,
                            voice_app_dir=VOICE_APP)
        self.assertIn('word_timestamps', str(caught.exception))

    def test_a_declared_default_is_used_and_the_rule_is_published(self) -> None:
        manager = self.build_manager()
        self.assertEqual(manager.default_package(), 'whisper-turbo')
        self.assertEqual(manager.engine.default_rule, 'declared')
        for entry in manager.catalog():
            self.assertEqual(entry['default_rule'], 'declared')
            self.assertEqual(entry['recommended'], entry['package_id'] == 'whisper-turbo')

    def test_without_a_declared_default_the_rule_is_stated_not_silent(self) -> None:
        """The old hardcode: 'whisper-turbo' if present, else alphabetical-first, unannounced.

        Retiring whisper-turbo silently changed the default, and nothing said which rule had
        been applied. With no declaration the worker still picks, and says how.
        """
        undeclared = []
        for manifest in self.manifests:
            value = json.loads(json.dumps(manifest))
            value.pop('default', None)
            undeclared.append(value)
        manager = self.manager_with(undeclared)
        self.assertEqual(manager.default_package(), 'whisper-base')
        self.assertEqual(manager.engine.default_rule, 'first-package-id')
        for entry in manager.catalog():
            self.assertEqual(entry['default_rule'], 'first-package-id')

    def test_a_worker_with_no_manifest_has_no_default_and_says_so(self) -> None:
        """An empty catalog is not a crash; it is a state with a name.

        The packaged worker refuses an empty catalog before it starts
        (`load_subtitle_manifests` names the manifest directory and the engine it looked for).
        An engine built by something else - the cancellation and credential tests build one - must
        still be able to say *why* it has no default instead of failing on construction.
        """
        from components.subtitle.app.backend.manager import validate_request
        manager = self.manager_with([])
        self.assertIsNone(manager.default_package())
        self.assertEqual(manager.engine.default_rule, 'no-catalog')
        self.assertEqual(manager.catalog(), [])
        with self.assertRaises(ValueError) as caught:
            validate_request({'formats': ['srt']}, packages=[], default_package=None)
        self.assertIn('no packaged subtitle model is available', str(caught.exception))
        self.assertIn('model-manifests', str(caught.exception))

    def test_two_declared_defaults_are_refused_by_name(self) -> None:
        first, second = (json.loads(json.dumps(value)) for value in self.manifests[:2])
        second['default'] = True
        with self.assertRaises(engine_module.ModelPackageError) as caught:
            self.manager_with([first, second, self.manifests[2]])
        message = str(caught.exception)
        self.assertIn('whisper-base', message)
        self.assertIn('whisper-turbo', message)


class LaneContractTests(SubtitleFixture):
    """The transcription lane publishes what a scheduler must send and gets back.

    `docs/lane-execution-contract.md`: the contract comes from the worker that owns the API, so
    a gateway needs no table of its own. Two rules are tested here rather than described: no
    parameter is listed that the route does not honour, and no number is published that the
    worker does not enforce.
    """

    def transcribe_form_fields(self) -> set[str]:
        """The form fields `POST /api/transcribe` really declares, read from the route itself."""
        from fastapi.routing import APIRoute
        client = TestClient(create_app(self.build_manager(), TOKEN))
        for route in client.app.routes:
            if isinstance(route, APIRoute) and route.path == '/api/transcribe':
                return {field.alias or field.name for field in route.dependant.body_params}
        self.fail('the worker serves no POST /api/transcribe')

    def test_every_row_publishes_the_lane_contract(self) -> None:
        manager = self.build_manager()
        for entry in manager.catalog():
            contract = entry['lane_contract']
            self.assertEqual(contract['schema'], 'studiohub.lane-contract.v1')
            self.assertEqual(contract['modality'], 'transcription')
            self.assertEqual(contract['operation'], 'audio.transcription')
            self.assertEqual(contract['artifact'], {
                'modality': 'transcription', 'media_type': 'application/json',
                'extension': 'json', 'per_item': 1})
            self.assertEqual(contract['states'], {
                'accepted': ['queued'], 'active': ['running', 'cancel_requested'],
                'terminal': ['done', 'error', 'cancelled'], 'uncertain': 'uncertain',
                'uncertain_is_terminal': False, 'discard_supported': True})
            self.assertEqual(contract['cancel']['request'], 'DELETE /api/hub/jobs/{batch_id}')
            self.assertTrue(contract['cancel']['supported'])
            self.assertIn('not evidence that nothing was written',
                          contract['cancel']['semantics'])
            # The Hub owns the only dispatch deadline for every lane and adds it when it echoes
            # the contract; a worker restating it would be another copy of a Hub number.
            self.assertNotIn('deadline_seconds', contract)

    def test_the_listed_parameters_are_the_route_that_is_served(self) -> None:
        from components.subtitle.app.backend import manager as subtitle_manager
        manager = self.build_manager()
        contract = manager.catalog()[0]['lane_contract']
        listed = {parameter['name'] for parameter in contract['parameters']}
        self.assertEqual(listed, self.transcribe_form_fields(),
                         'the contract must list exactly what POST /api/transcribe accepts')
        parameters = {parameter['name']: parameter for parameter in contract['parameters']}
        self.assertTrue(parameters['file']['required'])
        self.assertEqual(parameters['file']['max_bytes'], subtitle_manager.MAX_INPUT_BYTES)
        self.assertEqual(set(parameters['file']['enum']),
                         set(subtitle_manager.ALLOWED_EXTENSIONS))
        self.assertIs(parameters['word_timestamps']['default'], False)
        expected = {manifest['package_id'] for manifest in self.manifests}
        expected |= {manifest['model_id'] for manifest in self.manifests}
        self.assertEqual(set(parameters['model']['enum']), expected,
                         'every packaged model is offerable, by either spelling')
        self.assertEqual(parameters['model']['default'], 'whisper-turbo')

    def test_runtime_seconds_is_published_only_with_its_measurement(self) -> None:
        manager = self.build_manager()
        for entry in manager.catalog():
            self.assertNotIn('runtime_seconds', entry['lane_contract'],
                             'an estimator is not a measurement')
        measured = json.loads(json.dumps(self.manifests[0]))
        measured['subtitle']['runtime_seconds'] = {
            'measured': [{'seconds': 12.5, 'duration_seconds': 30.0, 'word_timestamps': False},
                         {'seconds': 13.75, 'duration_seconds': 30.0, 'word_timestamps': True}],
            'measured_on': '32 GiB M2 Max, MLX fp16', 'evidence': 'measured',
            'source': 'qualification/evidence/example.json'}
        model_packages.validate_manifest(measured)
        manager = self.manager_with_extra([measured])
        contract = {entry['package_id']: entry['lane_contract']
                    for entry in manager.catalog()}['whisper-turbo']
        rows = contract['runtime_seconds']['measured']
        self.assertEqual([row['seconds'] for row in rows], [12.5, 13.75],
                         'one row per configuration that was run, so a scheduler can match one')
        self.assertEqual(contract['runtime_seconds']['measured_on'], '32 GiB M2 Max, MLX fp16')
        self.assertEqual(contract['runtime_seconds']['evidence'], 'measured')
        self.assertEqual(contract['runtime_seconds']['source'],
                         'qualification/evidence/example.json')

    def manager_with_extra(self, manifests) -> SubtitleManager:
        manager = SubtitleManager(self.data, self.library, manifests, 'b' * 64,
                                  voice_app_dir=VOICE_APP)
        self.addCleanup(manager.close)
        factory, _ = _fake_factory([{'start': 0.0, 'end': 1.0, 'text': 'hello'}])
        manager.engine.model_factory = factory
        return manager

    def test_the_shipped_whisper_turbo_manifest_publishes_its_measured_runtime(self) -> None:
        """The one shipped measurement, cited so a reader can check it rather than trust it."""
        manifest = json.loads((PROJECT_ROOT / 'model-manifests' /
                               'whisper-turbo.json').read_text(encoding='utf-8'))
        runtime = manifest['subtitle']['runtime_seconds']
        self.assertEqual(runtime['evidence'], 'measured')
        self.assertIn('M2 Max', runtime['measured_on'])
        rows = runtime['measured']
        # One row per configuration that was run: a cold worker and a warm one cost different
        # amounts, and a scheduler picks the row matching what it is about to ask for.
        self.assertEqual({row['seconds'] for row in rows}, {9.66, 2.004, 3.013})
        for row in rows:
            self.assertEqual(row['duration_seconds'], 5.166)
            self.assertTrue(row['scene'])
            self.assertTrue(row['source'], 'every row names where its number came from')
        self.assertIn('r07-2026-09-21', rows[0]['source'],
                      'the cold row traces to the stored receipt')


class ModelDefaultsTests(SubtitleFixture):
    """Per-model operator defaults: stored per model, validated, applied, published.

    The owner's rule (2026-09-25): *"it has to be per model because each model is weight
    differently. we can't have a blanket default meant for one model but applied to all."* Each
    model's row offers exactly the parameters this lane publishes - the same list
    ``lane_contract`` publishes and ``POST /api/transcribe`` declares as its form fields - the
    worker stores the operator's choice per model, refuses a value its own lane would refuse,
    applies it only to a field the request omitted, and publishes the resolved value as the
    parameter's ``default`` so a scheduler reads what an omitted field will receive.
    """

    def setUp(self) -> None:
        super().setUp()
        factory, self.holder = _fake_factory([{'start': 0.0, 'end': 0.5, 'text': 'hello world'}])
        self.manager = self.build_manager(factory=factory)
        self.client = TestClient(create_app(self.manager, TOKEN))
        self.headers = {'X-Hub-Token': TOKEN}
        self.repo = self.manifests[0]['model_id']

    def defaults_row(self, repo: str | None = None) -> dict:
        payload = self.client.get('/api/model-defaults', headers=self.headers)
        self.assertEqual(payload.status_code, 200, payload.text)
        return {row['repo']: row for row in payload.json()['models']}[repo or self.repo]

    def parameter(self, name: str) -> dict:
        return next(p for p in self.manager.engine.lane_parameters() if p['name'] == name)

    def published(self, name: str, repo: str | None = None):
        """What ``GET /api/catalog`` advertises as one parameter's ``default``."""
        catalog = self.client.get('/api/catalog', headers=self.headers).json()
        entry = next(m for m in catalog['models'] if m['repo'] == (repo or self.repo))
        return next(p for p in entry['lane_contract']['parameters']
                    if p['name'] == name).get('default', 'absent')

    def store(self, values: dict, repo: str | None = None):
        return self.client.put('/api/model-defaults', headers=self.headers,
                               json={'repo': repo or self.repo, 'values': values})

    def transcribe(self, data: dict | None = None, *, model: str = 'whisper-turbo'):
        """One upload through the Hub's synchronous contract, with only the fields given."""
        body = dict(data or {})
        if model:
            body.setdefault('model', model)
        return self.client.post(
            '/api/transcribe', headers=self.headers, data=body,
            files={'file': ('clip.wav', io.BytesIO(self.audio_bytes), 'audio/wav')})

    def last_call(self) -> dict:
        self.assertTrue(self.holder['model'].calls, 'the engine was never asked to decode')
        return self.holder['model'].calls[-1]

    def test_get_before_anything_is_set_is_the_lanes_own_defaults(self) -> None:
        payload = self.client.get('/api/model-defaults', headers=self.headers).json()
        rows = {row['repo']: row for row in payload['models']}
        self.assertEqual(set(rows), {manifest['model_id'] for manifest in self.manifests})
        offered = [p['name'] for p in model_defaults.editable(
            self.manager.engine.lane_parameters())]
        self.assertNotIn('file', offered, 'a required upload is not defaultable')
        for repo, row in rows.items():
            with self.subTest(model=repo):
                self.assertEqual([p['name'] for p in row['parameters']], offered)
                self.assertEqual(row['defaults'], row['stated'])
                self.assertEqual(row['chosen'], {})
                self.assertEqual(row['not_used'], {})
                self.assertEqual(row['defaults'],
                                 model_defaults.stated(row['parameters']))
        self.assertIs(rows[self.repo]['defaults']['word_timestamps'], False)

    def test_put_stores_get_reflects_and_delete_returns_the_model_to_the_lane(self) -> None:
        saved = self.store({'word_timestamps': True, 'language': 'en'})
        self.assertEqual(saved.status_code, 200, saved.text)
        row = self.defaults_row()
        self.assertEqual(row['chosen'], {'word_timestamps': True, 'language': 'en'})
        self.assertIs(row['defaults']['word_timestamps'], True)
        self.assertEqual(model_defaults.load(self.data),
                         {self.repo: {'word_timestamps': True, 'language': 'en'}})

        deleted = self.client.request('DELETE', '/api/model-defaults', headers=self.headers,
                                      json={'repo': self.repo})
        self.assertEqual(deleted.status_code, 200, deleted.text)
        row = self.defaults_row()
        self.assertEqual(row['chosen'], {})
        self.assertEqual(row['defaults'], row['stated'])

        # The query spelling reaches the same route, so a caller with no body has one answer.
        self.store({'language': 'en'})
        self.assertEqual(self.client.request('DELETE', '/api/model-defaults',
                                             headers=self.headers,
                                             params={'repo': self.repo}).status_code, 200)
        self.assertEqual(self.defaults_row()['chosen'], {})
        self.assertEqual(model_defaults.load(self.data), {})

    def test_a_default_belongs_to_one_model_alone(self) -> None:
        """The owner's whole point: setting one model must not touch another."""
        other = self.manifests[1]['model_id']
        self.store({'word_timestamps': True})
        self.assertIs(self.defaults_row()['defaults']['word_timestamps'], True)
        untouched = self.defaults_row(other)
        self.assertEqual(untouched['chosen'], {})
        self.assertEqual(untouched['defaults'], untouched['stated'])

    def test_either_spelling_addresses_the_same_model(self) -> None:
        """The route's own `model` field takes a package id or a repository; so does this."""
        self.assertEqual(self.store({'language': 'en'}, repo='whisper-turbo').status_code, 200)
        self.assertEqual(self.defaults_row()['defaults']['language'], 'en')
        self.assertEqual(model_defaults.load(self.data), {self.repo: {'language': 'en'}})

    def test_an_unknown_repo_is_a_named_refusal_not_a_silent_no_op(self) -> None:
        for response in (
            self.store({}, repo='parakeet-tdt-0-6b'),
            self.client.request('DELETE', '/api/model-defaults', headers=self.headers,
                                json={'repo': 'parakeet-tdt-0-6b'}),
        ):
            self.assertEqual(response.status_code, 404, response.text)
            self.assertIn('parakeet-tdt-0-6b', str(response.json()['detail']))
        self.assertEqual(self.client.request('DELETE', '/api/model-defaults',
                                             headers=self.headers).status_code, 400)
        self.assertEqual(model_defaults.load(self.data), {})

    def test_a_value_outside_the_models_own_bound_is_refused_by_name_and_not_stored(self) -> None:
        """Driven against the REAL lane parameters, so a bound change moves this test."""
        limit = self.parameter('activity_id')['max_characters']
        response = self.store({'activity_id': 'x' * (limit + 1)})
        self.assertEqual(response.status_code, 422, response.text)
        detail = response.json()['detail']
        self.assertIn('activity_id', detail)
        self.assertIn(str(limit), detail['activity_id'])
        # Not stored, and the model is still on the lane's own value.
        self.assertEqual(model_defaults.load(self.data), {})
        self.assertEqual(self.defaults_row()['chosen'], {})

        # The control that proves this is a bound and not a blanket refusal: the lane's own
        # limit is accepted, and GET shows it.
        accepted = self.store({'activity_id': 'x' * limit})
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertEqual(self.defaults_row()['defaults']['activity_id'], 'x' * limit)

    def test_what_the_route_does_not_publish_is_not_defaultable(self) -> None:
        """`formats` and `file` are request fields; the lane publishes one and not the other."""
        for values, expected in (
            ({'file': 'clip.wav'}, 'publishes no such settable parameter'),
            ({'formats': ['srt']}, 'publishes no such settable parameter'),
            ({'word_timestamps': 'yes'}, 'expected boolean'),
            ({'model': 'not-a-packaged-model'}, 'is not one of'),
        ):
            with self.subTest(values=values):
                response = self.store(values)
                self.assertEqual(response.status_code, 422, response.text)
                name = next(iter(values))
                self.assertIn(name, response.json()['detail'])
                self.assertIn(expected, response.json()['detail'][name])
        self.assertEqual(model_defaults.load(self.data), {})

    def test_the_routes_own_validator_refuses_what_it_would_refuse_at_request_time(self) -> None:
        """A value this lane cannot publish a bound for is still checked against the lane.

        The language hint's shape lives in this app's request validator, not in the lane contract,
        so `model_defaults` cannot express it as a bound. A sentence typed into the language field
        must be refused at the door: stored, it would turn every later request that omitted
        language into a 422.
        """
        refused = self.store({'language': 'not a language!'})
        self.assertEqual(refused.status_code, 422, refused.text)
        self.assertIn('language', refused.json()['detail'])
        self.assertIn('BCP-47', refused.json()['detail']['language'])
        self.assertEqual(model_defaults.load(self.data), {})
        # And a value the route accepts stores cleanly.
        self.assertEqual(self.store({'language': 'en'}).status_code, 200)

    def test_a_values_object_that_is_not_one_is_refused_by_name(self) -> None:
        response = self.client.put('/api/model-defaults', headers=self.headers,
                                   json={'repo': self.repo, 'values': ['en']})
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn('values', response.json()['detail'])
        self.assertEqual(model_defaults.load(self.data), {})

    def test_a_request_that_omits_it_gets_the_stored_default_the_catalog_publishes(self) -> None:
        self.assertEqual(self.store({'word_timestamps': True}).status_code, 200)
        response = self.transcribe()
        self.assertEqual(response.status_code, 200, response.text)
        # Read both sides: the number the request ran with is the number the catalog advertises.
        self.assertIs(self.published('word_timestamps'), True)
        self.assertIs(self.last_call()['word_timestamps'], self.published('word_timestamps'))

    def test_a_request_that_supplies_it_is_unaffected_by_a_stored_default(self) -> None:
        self.assertEqual(self.store({'word_timestamps': True}).status_code, 200)
        response = self.transcribe({'word_timestamps': 'false'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIs(self.last_call()['word_timestamps'], False,
                      'a stored default overrode what the caller sent')
        # And the catalog still publishes the stored default: it is the answer for an omitted
        # field, not a claim about what this request did.
        self.assertIs(self.published('word_timestamps'), True)

    def test_the_stored_language_is_published_and_used(self) -> None:
        self.assertEqual(self.store({'language': 'en'}).status_code, 200)
        self.assertEqual(self.published('language'), 'en')
        self.assertEqual(self.transcribe().status_code, 200)
        self.assertEqual(self.last_call()['language'], self.published('language'))

    def test_route_catalog_generation_defaults_and_the_engine_agree(self) -> None:
        """One value read at four places must be the same number.

        The job's own validated parameters, the lane contract's published ``default``, the row's
        ``generation_defaults`` (what the app pre-fills a form from) and the keyword the engine is
        called with - if any of the four can disagree, the Models tab is showing an operator a
        value the run does not use.
        """
        self.assertEqual(self.store({'language': 'en'}).status_code, 200)
        response = self.transcribe()
        self.assertEqual(response.status_code, 200, response.text)
        job = self.manager.get(response.json()['studio_task_id'])
        routed = job['params']['language']

        entry = next(m for m in
                     self.client.get('/api/catalog', headers=self.headers).json()['models']
                     if m['repo'] == self.repo)
        published = next(p for p in entry['lane_contract']['parameters']
                         if p['name'] == 'language')['default']
        generation_defaults = entry['generation_defaults']['language']

        self.assertEqual(routed, published)
        self.assertEqual(generation_defaults, published)
        self.assertEqual(self.last_call()['language'], routed)

    def test_generation_defaults_and_the_lane_contract_are_one_answer(self) -> None:
        """Both spellings of "the value in force" on a row come from the one resolution."""
        for entry in self.client.get('/api/catalog', headers=self.headers).json()['models']:
            with self.subTest(model=entry['package_id']):
                published = {p['name']: p['default']
                             for p in entry['lane_contract']['parameters'] if 'default' in p}
                self.assertEqual(entry['generation_defaults'], published)

    def test_the_published_map_moves_with_a_stored_default(self) -> None:
        before = {entry['package_id']: dict(entry['generation_defaults'])
                  for entry in self.client.get('/api/catalog', headers=self.headers).json()['models']}
        self.assertEqual(self.store({'word_timestamps': True}).status_code, 200)
        after = {entry['package_id']: dict(entry['generation_defaults'])
                 for entry in self.client.get('/api/catalog', headers=self.headers).json()['models']}
        self.assertIs(after['whisper-turbo']['word_timestamps'], True)
        self.assertIs(before['whisper-turbo']['word_timestamps'], False)
        # One model's choice does not move another model's map.
        self.assertEqual(after['whisper-base'], before['whisper-base'])

    def test_with_nothing_stored_an_omitted_field_stays_exactly_as_the_lane_had_it(self) -> None:
        """A stored default is the only thing that changes an omitted field."""
        self.assertEqual(self.transcribe().status_code, 200)
        call = self.last_call()
        self.assertIs(call['word_timestamps'], False)
        self.assertIsNone(call['language'])

    def test_the_studio_route_applies_the_stored_default_too(self) -> None:
        self.assertEqual(self.store({'language': 'en'}).status_code, 200)
        response = self.client.post(
            '/api/generate/transcribe', headers=self.headers, data={'model': 'whisper-turbo'},
            files={'file': ('clip.wav', io.BytesIO(self.audio_bytes), 'audio/wav')})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.wait(self.manager, response.json()['job']['id'])['state'], 'done')
        self.assertEqual(self.last_call()['language'], 'en')

    def test_the_capability_and_weight_format_publication_does_not_move(self) -> None:
        """Only the resolved ``default`` changes; what the package *is* does not."""
        before = {m['package_id']: m for m in
                  self.client.get('/api/catalog', headers=self.headers).json()['models']}
        self.assertEqual(self.store({'word_timestamps': True}).status_code, 200)
        after = {m['package_id']: m for m in
                 self.client.get('/api/catalog', headers=self.headers).json()['models']}
        self.assertEqual(set(after), set(before))
        for package_id, entry in after.items():
            with self.subTest(package=package_id):
                for field in ('family', 'languages', 'supports_segment_timestamps',
                              'supports_word_timestamps', 'supports_long_form',
                              'capabilities', 'engine', 'model_revision'):
                    self.assertEqual(entry[field], before[package_id][field], field)
                self.assertEqual(entry['readiness']['files'],
                                 before[package_id]['readiness']['files'])
                self.assertEqual([p['name'] for p in entry['lane_contract']['parameters']],
                                 [p['name'] for p in
                                  before[package_id]['lane_contract']['parameters']])
        # The one field that does move is the resolved default, and it moves for one model.
        chosen = next(p for p in after['whisper-turbo']['lane_contract']['parameters']
                      if p['name'] == 'word_timestamps')
        self.assertIs(chosen['default'], True)
        untouched = next(p for p in after['whisper-base']['lane_contract']['parameters']
                         if p['name'] == 'word_timestamps')
        self.assertIs(untouched['default'], False)

    def test_the_memory_policy_contract_answers_beside_the_defaults_routes(self) -> None:
        """The two surfaces are neighbours in one state directory and neither renames the other."""
        from unittest.mock import patch

        from components.subtitle.app.backend import memory_policy

        settings = self.data / 'memory_policy.json'
        self.assertNotEqual(settings.name, model_defaults.SETTINGS_NAME)
        with patch.object(memory_policy, 'settings_file', lambda: settings):
            policy = self.client.get('/api/memory-policy', headers=self.headers)
            self.assertEqual(policy.status_code, 200, policy.text)
            for field in ('mode', 'resolved_mode', 'idle_seconds', 'options', 'loaded_models',
                          'active_work', 'busy'):
                self.assertIn(field, policy.json(), field)
            saved = self.client.put('/api/memory-policy', headers=self.headers,
                                    json={'mode': 'memory_saver'})
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertEqual(saved.json()['mode'], 'memory_saver')
            self.assertEqual(json.loads(settings.read_text(encoding='utf-8')),
                             {'mode': 'memory_saver'})
        # The defaults surface is its own file in the same state directory, and neither write
        # touched the other's.
        self.store({'word_timestamps': True})
        self.assertEqual(model_defaults.load(self.data), {self.repo: {'word_timestamps': True}})
        self.assertTrue((self.data / model_defaults.SETTINGS_NAME).is_file())
        self.assertTrue(settings.is_file())
        self.assertEqual(json.loads(settings.read_text(encoding='utf-8')),
                         {'mode': 'memory_saver'})


class WeightFormatPublicationTests(SubtitleFixture):
    """The weight format is *stated* on the row the app reads, not inferred from a file list.

    The app read ``readiness.files.weight_files`` off the catalog row and took the extension
    itself. That is an interpretation standing in for a fact: it answers nothing for a package
    this runtime cannot load (its ``weight_files`` is empty), it describes the converted view
    rather than the package once a view exists, and it changes silently when a receipt changes.
    The row now carries the format the package's own inventory declares.
    """

    def setUp(self) -> None:
        super().setUp()
        self.manager = self.build_manager()
        self.client = TestClient(create_app(self.manager, TOKEN))
        self.headers = {'X-Hub-Token': TOKEN}
        self.catalog = {entry['package_id']: entry
                        for entry in self.client.get('/api/catalog',
                                                     headers=self.headers).json()['models']}
        self.availability = {model['package_id']: model
                             for model in self.client.get(
                                 '/api/transcribe/availability',
                                 headers=self.headers).json()['models']}

    def declared_weights(self, manifest: dict) -> list[str]:
        """Every extension the package's own inventory carries, in manifest order."""
        return [Path(entry['path']).suffix.lstrip('.') for entry in manifest['files']
                if Path(entry['path']).suffix]

    def test_the_availability_row_states_the_format_the_package_declares(self) -> None:
        """The row the app reads carries it, and every projection of one package agrees."""
        for manifest in self.manifests:
            package_id = manifest['package_id']
            with self.subTest(package=package_id):
                stated = self.availability[package_id]['weight_format']
                self.assertIsNotNone(stated)
                self.assertIn(stated, self.declared_weights(manifest),
                              'the format must be one the package itself declares')
                self.assertEqual(stated, self.catalog[package_id]['weight_format'])
                self.assertEqual(stated,
                                 self.catalog[package_id]['readiness']['files']['weight_format'])
        # The route is the one the Hub and the app read, not just the in-process call.
        served = {model['package_id']: model.get('weight_format')
                  for model in self.client.get('/api/transcribe/availability',
                                               headers=self.headers).json()['models']}
        self.assertEqual(served, {package_id: model['weight_format']
                                  for package_id, model in self.availability.items()})

    def test_a_package_this_runtime_cannot_load_still_states_its_format(self) -> None:
        """The case the old join could not answer: no loadable weights, a format all the same."""
        not_runnable = [manifest for manifest in self.manifests
                        if not self.catalog[manifest['package_id']]
                        ['readiness']['files']['runtime_supported']]
        self.assertTrue(not_runnable, 'the fixture must carry a package this runtime cannot load')
        for manifest in not_runnable:
            package_id = manifest['package_id']
            with self.subTest(package=package_id):
                entry = self.catalog[package_id]
                self.assertEqual(entry['readiness']['files']['weight_files'], [],
                                 'the old join had nothing to read for this package')
                self.assertNotIn(entry['weight_format'],
                                 engine_module.SUPPORTED_WEIGHT_SUFFIXES,
                                 'this package is the one this runtime cannot open')
                self.assertIn(entry['weight_format'],
                              engine_module.CONVERTIBLE_WEIGHT_SUFFIXES)
                self.assertEqual(self.availability[package_id]['weight_format'],
                                 entry['weight_format'])

    def test_a_runnable_package_states_the_format_this_runtime_opens(self) -> None:
        for manifest in self.manifests:
            package_id = manifest['package_id']
            files = self.catalog[package_id]['readiness']['files']
            if not files['runtime_supported']:
                continue
            with self.subTest(package=package_id):
                self.assertEqual(self.catalog[package_id]['weight_format'],
                                 Path(files['weight_files'][0]).suffix.lstrip('.'))

    def test_a_manifest_stating_no_recognised_weight_names_none(self) -> None:
        """`None` is "this package declares no weight file I know", not a guessed format."""
        self.assertIsNone(engine_module.weight_format({'files': [
            {'path': 'model/config.json'}, {'path': 'README.md'}]}))


class CatalogDiscoveryTests(SubtitleFixture):
    """A1 — the worker's catalog is every manifest that *declares* the Subtitle engine.

    One ``model-manifests`` directory ships in the bundle and carries every engine's packages
    (``packaging/stage_voice.py`` copies the whole tree), so a package's *file name* is not a
    contract. The worker read ``whisper-*.json``, which made its whole catalog "packages whose
    id starts with whisper-": the next subtitle package — ``parakeet-tdt-0-6b``,
    ``nemotron-asr-0-6b`` — shipped, was published by the Hub, and never reached this worker's
    ``/api/catalog`` or the picker, and a job for it was refused with a message about Whisper.

    These tests go through the packaged entry point (``from_environment``) so the discovery
    rule itself is what is under test, not a manager built from an already-chosen list.
    """

    @staticmethod
    def subtitle_manifests_in(directory: Path) -> list[dict]:
        """Every manifest in ``directory`` that declares this worker's engine."""
        return [manifest for manifest in
                (json.loads(path.read_text(encoding='utf-8'))
                 for path in sorted(directory.glob('*.json')))
                if manifest.get('engine') == 'subtitle-mlx']

    def manifest_directory(self, extra: list[dict]) -> Path:
        directory = self.root / 'model-manifests'
        directory.mkdir(exist_ok=True)
        for manifest in [*self.manifests, *extra]:
            (directory / f'{manifest["package_id"]}.json').write_text(
                json.dumps(manifest), encoding='utf-8')
        return directory

    def package_named(self, package_id: str, model_id: str, **declared) -> dict:
        """A subtitle package whose id has nothing to do with Whisper.

        Its capabilities are its own: an English-only model that returns segment timings and
        cannot return word timings or transcribe long form. The worker has to publish that, not
        the Whisper-shaped default it used to assert for every package it loaded.
        """
        manifest = json.loads(json.dumps(self.manifests[0]))
        manifest.update({'package_id': package_id, 'model_id': model_id,
                         'revision': 'f' * 40})
        manifest.pop('default', None)
        manifest['subtitle'] = declared.pop('subtitle', {
            'family': 'parakeet', 'languages': ['en'], 'segment_timestamps': True,
            'word_timestamps': False, 'long_form': False})
        manifest.update(declared)
        model_packages.validate_manifest(manifest)
        return manifest

    def worker_over(self, directory: Path) -> tuple[TestClient, SubtitleManager]:
        """The packaged entry point over one directory, as the bundle builds it."""
        from unittest.mock import patch

        from components.subtitle.app.backend import main as subtitle_main

        release = self.root / 'release-manifest.json'
        release.write_text(json.dumps({
            'schema': 'studiohub.standalone-release', 'schema_version': 1, 'version': 'fixture',
            'components': {'subtitle': {'runtime_sha256': 'b' * 64}}}), encoding='utf-8')
        previous = dict(os.environ)
        os.environ.update({
            'STUDIOHUB_SUBTITLE_MANIFESTS': str(directory),
            'STUDIOHUB_MODELS_DIR': str(self.library),
            'STUDIOHUB_VOICE_APP_DIR': str(VOICE_APP),
            'STUDIOHUB_RELEASE_MANIFEST': str(release),
            'STUDIOHUB_FLEET_TOKEN': TOKEN,
            'STUDIOHUB_SUBTITLE_DATA_DIR': str(self.data),
        })
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(previous)))
        captured: dict = {}
        build_app = subtitle_main.create_app

        def capture(manager, token, **kwargs):
            captured['manager'] = manager
            return build_app(manager, token, **kwargs)

        with patch.object(subtitle_main, 'create_app', capture):
            app = subtitle_main.from_environment()
        manager = captured.get('manager')
        if manager is not None:
            self.addCleanup(manager.close)
        return TestClient(app), manager

    def published(self, client: TestClient) -> dict[str, dict]:
        response = client.get('/api/catalog', headers={'X-Studio-Token': TOKEN})
        self.assertEqual(response.status_code, 200, response.text)
        return {entry['package_id']: entry for entry in response.json()['models']}

    def test_a_subtitle_package_named_anything_is_published(self) -> None:
        """The fixture the file-name glob could never see."""
        parakeet = self.package_named('parakeet-tdt-0-6b', 'nvidia/parakeet-tdt-0.6b-v2')
        image = json.loads((PROJECT_ROOT / 'model-manifests' /
                            'flux2-klein-4b-mlx-4bit.json').read_text(encoding='utf-8'))
        client, manager = self.worker_over(self.manifest_directory([parakeet, image]))
        published = self.published(client)
        self.assertIn('parakeet-tdt-0-6b', published,
                      'a subtitle package is discovered by its declared engine, not its file name')
        self.assertEqual(published['parakeet-tdt-0-6b']['repo'], 'nvidia/parakeet-tdt-0.6b-v2')
        self.assertEqual(published['parakeet-tdt-0-6b']['engine'], 'subtitle-mlx')
        # Another engine's manifest in the same directory is simply not this worker's catalog —
        # it must neither appear nor stop the worker from starting.
        self.assertEqual(set(published), {manifest['package_id']
                                          for manifest in [*self.manifests, parakeet]})
        # And the job contract accepts it, by repository or by package id. Before the fix the
        # request was refused as "choose one of the packaged Whisper models" — a message about
        # a model the caller had not asked for.
        from components.subtitle.app.backend.manager import validate_request

        for spelling in ('nvidia/parakeet-tdt-0.6b-v2', 'parakeet-tdt-0-6b'):
            request = validate_request({'model': spelling, 'formats': ['srt']},
                                       packages=sorted(published),
                                       default_package='whisper-turbo',
                                       aliases=manager.model_aliases())
            self.assertEqual(request['model'], 'parakeet-tdt-0-6b')

    def test_every_shipped_manifest_declaring_the_subtitle_engine_is_published(self) -> None:
        """The invariant: ship a subtitle manifest and it is published without editing code."""
        directory = PROJECT_ROOT / 'model-manifests'
        declared = {manifest['package_id'] for manifest in self.subtitle_manifests_in(directory)}
        self.assertTrue(declared, 'the shipped catalog must declare the subtitle engine somewhere')
        client, _ = self.worker_over(directory)
        published = self.published(client)
        self.assertEqual(set(published), declared)
        # A non-whisper file name is what the next package will look like: the test above proves
        # that case, and this one fails the moment a new subtitle manifest ships unseen.
        for entry in published.values():
            self.assertEqual(entry['engine'], 'subtitle-mlx')

    def test_a_sibling_engines_unreadable_file_does_not_stop_the_worker(self) -> None:
        """The shared directory belongs to every worker; a bad sibling is not this worker's."""
        directory = self.manifest_directory([])
        (directory / 'broken.json').write_text('{not json', encoding='utf-8')
        client, _ = self.worker_over(directory)
        published = self.published(client)
        self.assertEqual(set(published), {manifest['package_id'] for manifest in self.manifests})

    def test_an_invalid_subtitle_manifest_is_fatal_and_names_its_file(self) -> None:
        """A declared subtitle package that cannot be read must not vanish silently."""
        from components.subtitle.app.backend.main import load_subtitle_manifests

        broken = self.package_named('parakeet-tdt-0-6b', 'nvidia/parakeet-tdt-0.6b-v2')
        broken['revision'] = 'not-a-revision'
        with self.assertRaisesRegex(ValueError, r'parakeet-tdt-0-6b\.json'):
            load_subtitle_manifests(self.manifest_directory([broken]))


class DecodePathTests(SubtitleFixture):
    """The engine decodes through the pinned call the Voice source adopted."""

    def test_decode_uses_the_pinned_options_and_normalizes_output(self) -> None:
        factory, holder = _fake_factory([
            {'start': 0.0, 'end': 0.5, 'text': 'hello world'},
            {'start': 0.6, 'end': 99.0, 'text': 'past the media end'},
        ])
        manager = self.build_manager(factory=factory)
        job = self.submit(manager, word_timestamps=True)
        value = self.wait(manager, job['id'])
        self.assertEqual(value['state'], 'done', value.get('error'))
        call = holder['model'].calls[0]
        self.assertFalse(call['condition_on_previous_text'])
        self.assertTrue(call['return_timestamps'])
        self.assertTrue(call['word_timestamps'])
        self.assertIsNone(call['language'])
        # The Voice normalizer clamps the final cue to the real media duration.
        srt = Path(value['artifacts']['srt']['path']).read_text()
        self.assertNotIn('00:01:39', srt)
        self.assertLessEqual(value['media_duration_seconds'], 1.3)

    def test_language_is_forwarded(self) -> None:
        factory, holder = _fake_factory([{'start': 0.0, 'end': 0.5, 'text': 'hi'}])
        manager = self.build_manager(factory=factory)
        job = self.submit(manager, language='en')
        self.wait(manager, job['id'])
        self.assertEqual(holder['model'].calls[0]['language'], 'en')

    def test_missing_input_file_fails_the_job(self) -> None:
        manager = self.build_manager()
        source = manager.store_input('test', 'clip.wav', self.audio_bytes)
        Path(source['path']).unlink()
        job = manager.submit({'model': 'whisper-turbo', 'formats': ['srt']}, source)
        value = self.wait(manager, job['id'])
        self.assertEqual(value['state'], 'error')
        self.assertEqual(value['error_code'], 'INPUT_NOT_FOUND')


class LifecycleTests(SubtitleFixture):
    """ST-06.2 — independent jobs, cancellation, history and restart behavior."""

    def test_failed_final_receipt_discards_every_published_artifact(self) -> None:
        from unittest.mock import patch

        manager = self.manager = self.build_manager()
        original_persist = manager._persist
        failed = False

        def fail_done_once():
            nonlocal failed
            if not failed and any(job['state'] == 'done' for job in manager.jobs.values()):
                failed = True
                raise OSError('disk full at final receipt')
            original_persist()

        with patch.object(manager, '_persist', side_effect=fail_done_once):
            job = self.submit(manager)
            value = self.wait(manager, job['id'])

        self.assertTrue(failed)
        self.assertEqual(value['state'], 'error')
        self.assertEqual(value['error_code'], 'JOB_PERSISTENCE_FAILED')
        self.assertEqual(value['artifacts'], {})
        self.assertFalse(list(manager.artifacts.iterdir()))

        restarted = SubtitleManager(self.data, self.library, self.manifests, 'b' * 64,
                                    voice_app_dir=VOICE_APP)
        self.assertEqual(restarted.get(job['id'])['error_code'], 'JOB_PERSISTENCE_FAILED')

    def test_failure_partway_through_multi_format_publish_discards_every_final(self) -> None:
        from unittest.mock import patch
        from components.subtitle.app.backend import manager as manager_module

        manager = self.manager = self.build_manager()
        original_hash = manager_module._sha256_file
        artifact_hashes = 0

        def fail_second_artifact(path):
            nonlocal artifact_hashes
            if path.parent == manager.artifacts:
                artifact_hashes += 1
                if artifact_hashes == 2:
                    raise OSError('disk full during multi-format publish')
            return original_hash(path)

        with patch.object(manager_module, '_sha256_file', side_effect=fail_second_artifact):
            job = self.submit(manager)
            value = self.wait(manager, job['id'])

        self.assertEqual(artifact_hashes, 2)
        self.assertEqual(value['state'], 'error')
        self.assertEqual(value['error_code'], 'JOB_PERSISTENCE_FAILED')
        self.assertEqual(value['artifacts'], {})
        self.assertFalse(list(manager.artifacts.iterdir()))

    def test_failed_cleanup_keeps_the_completion_receipts_uncertain(self) -> None:
        from unittest.mock import patch

        manager = self.manager = self.build_manager()
        original_persist = manager._persist
        failed = False

        def fail_done_once():
            nonlocal failed
            if not failed and any(job['state'] == 'done' for job in manager.jobs.values()):
                failed = True
                raise OSError('disk full at final receipt')
            original_persist()

        with (patch.object(manager, '_persist', side_effect=fail_done_once),
              patch.object(manager, '_discard_published', return_value=False)):
            job = self.submit(manager)
            value = self.wait(manager, job['id'])

        self.assertEqual(value['state'], 'error')
        self.assertEqual(value['error_code'], 'JOB_PERSISTENCE_UNCERTAIN')
        self.assertEqual(sorted(value['artifacts']), ['srt', 'text', 'vtt'])
        for receipt in value['artifacts'].values():
            self.assertTrue(Path(receipt['path']).is_file())

        restarted = SubtitleManager(self.data, self.library, self.manifests, 'b' * 64,
                                    voice_app_dir=VOICE_APP)
        restored = restarted.get(job['id'])
        self.assertEqual(restored['error_code'], 'JOB_PERSISTENCE_UNCERTAIN')
        self.assertEqual(restored['artifacts'], value['artifacts'])

    def test_partial_publish_cleanup_failure_keeps_recovery_identity(self) -> None:
        from unittest.mock import patch
        from components.subtitle.app.backend import manager as manager_module

        manager = self.manager = self.build_manager()
        original_hash = manager_module._sha256_file
        artifact_hashes = 0

        def fail_second_artifact(path):
            nonlocal artifact_hashes
            if path.parent == manager.artifacts:
                artifact_hashes += 1
                if artifact_hashes == 2:
                    raise OSError('disk full after second rename')
            return original_hash(path)

        with (patch.object(manager_module, '_sha256_file', side_effect=fail_second_artifact),
              patch.object(manager, '_discard_published', return_value=False)):
            job = self.submit(manager, formats=['srt', 'vtt'])
            value = self.wait(manager, job['id'])

        self.assertEqual(value['error_code'], 'JOB_PERSISTENCE_UNCERTAIN')
        self.assertEqual(sorted(value['artifacts']), ['srt'])
        self.assertTrue(Path(value['artifacts']['srt']['path']).is_file())
        self.assertEqual(len(value['unverified_artifact_paths']), 1)
        self.assertTrue(Path(value['unverified_artifact_paths'][0]).is_file())

        restarted = SubtitleManager(self.data, self.library, self.manifests, 'b' * 64,
                                    voice_app_dir=VOICE_APP)
        restored = restarted.get(job['id'])
        self.assertEqual(restored['artifacts'], value['artifacts'])
        self.assertEqual(restored['unverified_artifact_paths'],
                         value['unverified_artifact_paths'])

    def test_cancel_stops_the_job_and_publishes_nothing(self) -> None:
        manager = self.build_manager()
        job = self.submit(manager)
        manager.cancel(job['id'])
        value = self.wait(manager, job['id'])
        self.assertEqual(value['state'], 'cancelled')
        self.assertEqual(value['artifacts'], {})
        self.assertFalse(list((self.data / 'artifacts').glob('*')))

    def test_cancel_is_idempotent_and_unknown_ids_are_none(self) -> None:
        manager = self.build_manager()
        job = self.submit(manager)
        manager.cancel(job['id'])
        again = manager.cancel(job['id'])
        self.assertIn(again['state'], ('running', 'cancelled'))
        self.assertEqual(self.wait(manager, job['id'])['state'], 'cancelled')
        self.assertIsNone(manager.cancel('missing-job'))

    def test_history_is_separate_and_survives_restart(self) -> None:
        manager = self.build_manager()
        first = self.submit(manager)
        done = self.wait(manager, first['id'])
        self.assertEqual(done['state'], 'done')
        self.assertEqual(len(manager.list()), 1)

        # A second process opening the same state directory finds its own jobs.
        restarted = SubtitleManager(self.data, self.library, self.manifests, 'b' * 64,
                                    voice_app_dir=VOICE_APP)
        restored = restarted.get(first['id'])
        self.assertEqual(restored['state'], 'done')
        self.assertTrue(restarted._final_matches(restored))
        self.assertEqual(len(restarted.list()), 1)

    def test_restart_marks_in_flight_jobs_uncertain(self) -> None:
        manager = self.build_manager()
        job = self.submit(manager)
        # Simulate a crash after the queued receipt was written.
        state = json.loads((self.data / 'jobs.json').read_text())
        state[job['id']]['state'] = 'running'
        (self.data / 'jobs.json').write_text(json.dumps(state))
        restarted = SubtitleManager(self.data, self.library, self.manifests, 'b' * 64,
                                    voice_app_dir=VOICE_APP)
        value = restarted.get(job['id'])
        self.assertEqual(value['state'], 'error')
        self.assertEqual(value['error_code'], 'WORKER_RESTART_UNCERTAIN')

    def test_artifacts_without_a_receipt_are_uncertain_not_won(self) -> None:
        manager = self.build_manager()
        job = self.submit(manager)
        self.wait(manager, job['id'])
        state = json.loads((self.data / 'jobs.json').read_text())
        state[job['id']]['state'] = 'running'
        (self.data / 'jobs.json').write_text(json.dumps(state))
        restarted = SubtitleManager(self.data, self.library, self.manifests, 'b' * 64,
                                    voice_app_dir=VOICE_APP)
        value = restarted.get(job['id'])
        self.assertEqual(value['error_code'], 'JOB_PERSISTENCE_UNCERTAIN')

    def test_ack_is_idempotent_and_requires_real_bytes(self) -> None:
        manager = self.build_manager()
        job = self.submit(manager)
        value = self.wait(manager, job['id'])
        receipt = manager.ack(job['id'])
        self.assertTrue(receipt['ok'])
        self.assertEqual(receipt['sha256'], value['sha256'])
        self.assertEqual(manager.ack(job['id'])['sha256'], receipt['sha256'])
        self.assertTrue(manager.get(job['id'])['acknowledged'])

        Path(value['artifacts']['srt']['path']).write_text('tampered\n')
        self.assertIsNone(manager.ack(job['id']))
        self.assertIsNone(manager.artifact_path(job['id'], 'srt'))

    def test_ack_rejects_unfinished_jobs(self) -> None:
        manager = self.manager = self.build_manager()
        job = self.submit(manager)
        self.assertIsNone(manager.ack(job['id']))
        self.assertIsNone(manager.artifact_path(job['id'], 'srt'))
        self.assertIsNone(manager.artifact_path(job['id'], 'pdf'))


class OwnerTokenTests(SubtitleFixture):
    """The private worker refuses anything without the owning Hub credential."""

    def test_create_app_requires_a_token(self) -> None:
        manager = self.build_manager()
        with self.assertRaises(ValueError):
            create_app(manager, '')

    def test_every_endpoint_rejects_a_foreign_or_missing_token(self) -> None:
        manager = self.build_manager()
        client = TestClient(create_app(manager, TOKEN))
        for headers in ({}, {'X-Hub-Token': 'wrong'}, {'Authorization': 'Bearer nope'}):
            response = client.get('/api/health', headers=headers)
            self.assertEqual(response.status_code, 401, headers)
        self.assertEqual(client.get('/api/health', headers={'X-Hub-Token': TOKEN}).status_code, 200)

    def test_a_refused_credential_says_what_was_required_and_what_arrived(self) -> None:
        """A bare 401 across a fleet is unattributable.

        Gateway Studio measured one on `/studio/voice/api/transcribe/availability` and could not
        tell whether the controller's credential, the node's Hub, or the worker behind it had
        refused. This worker cannot accept a controller's credential by design - the owning Hub
        re-signs every proxied request with the worker's own token - so the refusal states which
        token it wanted and which headers it saw, and says where the token comes from.
        """
        manager = self.build_manager()
        client = TestClient(create_app(manager, TOKEN))
        response = client.get('/api/transcribe/availability',
                              headers={'Authorization': 'Bearer a-controller-token'})
        self.assertEqual(response.status_code, 401)
        payload = response.json()
        self.assertIn('Hub token', payload['detail'])
        self.assertIn('Authorization: Bearer', payload['detail'])
        self.assertEqual(payload['required'], 'hub-token')
        self.assertEqual(payload['offered'], ['Authorization: Bearer'])
        missing = client.get('/api/transcribe/availability').json()
        self.assertIn('no credential', missing['detail'])
        self.assertEqual(missing['offered'], [])


class SubtitleApiTests(SubtitleFixture):
    """ST-06.1/2 — the public Subtitle endpoints on a private loopback app."""

    def setUp(self) -> None:
        super().setUp()
        self.manager = self.build_manager()
        self.manager.engine.offline = True
        self.client = TestClient(create_app(self.manager, TOKEN))
        self.headers = {'X-Hub-Token': TOKEN}

    def _upload(self, url: str, data: dict, headers: dict | None = None):
        body = dict(data)
        body.setdefault('files', None)
        return self.client.post(
            url, headers=headers or self.headers, data=body,
            files={'file': ('clip.wav', io.BytesIO(self.audio_bytes), 'audio/wav')})

    def test_health_reports_busy_state_and_runtime_identity(self) -> None:
        payload = self.client.get('/api/health', headers=self.headers).json()
        self.assertTrue(payload['ok'])
        self.assertFalse(payload['transcription']['busy'])
        self.assertEqual(payload['transcription']['runtime_revision'], 'b' * 64)

    def test_health_reports_the_release_or_says_it_cannot_know(self) -> None:
        """A version field must not carry a non-version.

        `/api/health` answered `"standalone"` and the Hub republishes that verbatim as the
        worker's `studio_version`, so three of four workers reported a word where a version
        belongs. The value now comes from `components/app_version.py`: the staged release when
        there is one, the checkout's own version for a development run, and `unknown` when it has
        neither — never a word that reads as data.
        """
        from components import app_version
        from components.subtitle.app.backend import main as subtitle_main
        manager = self.build_manager()
        self.addCleanup(manager.close)
        payload = TestClient(create_app(manager, TOKEN)).get(
            '/api/health', headers={'X-Hub-Token': TOKEN}).json()
        self.assertEqual(set(app_version.version_fields()) - {'app_version'},
                         {'app_commit', 'development_build'})
        self.assertIn('app_commit', payload)
        self.assertIn('development_build', payload)
        self.assertRegex(payload['app_version'], r'^(\d+\.\d+\.\d+|unknown)$')
        self.assertNotEqual(payload['app_version'], 'standalone')

        release = self.root / 'release-manifest.json'
        release.write_text(json.dumps({
            'schema': 'studiohub.standalone-release', 'schema_version': 1, 'version': '0.7.3',
            'development_build': False, 'implementation_commit': 'c' * 40,
            'components': {'subtitle': {'runtime_sha256': 'b' * 64}}}), encoding='utf-8')
        previous = os.environ.get(app_version.ENVIRONMENT_VARIABLE)
        os.environ[app_version.ENVIRONMENT_VARIABLE] = str(release)
        self.addCleanup(self.restore_release_manifest, previous)
        self.assertEqual(subtitle_main.read_app_version(), '0.7.3')
        client = TestClient(create_app(self.build_manager(), TOKEN))
        staged = client.get('/api/health', headers={'X-Hub-Token': TOKEN}).json()
        self.assertEqual(staged['app_version'], '0.7.3')
        self.assertEqual(staged['app_commit'], 'c' * 40)
        self.assertFalse(staged['development_build'])

    def test_a_release_manifest_that_is_not_one_is_never_reported_as_a_version(self) -> None:
        from components import app_version
        from components.subtitle.app.backend import main as subtitle_main
        broken = self.root / 'broken-release.json'
        broken.write_text(json.dumps({'schema': 'something-else', 'version': '1.0.0'}),
                          encoding='utf-8')
        previous = os.environ.get(app_version.ENVIRONMENT_VARIABLE)
        os.environ[app_version.ENVIRONMENT_VARIABLE] = str(broken)
        self.addCleanup(self.restore_release_manifest, previous)
        # A manifest that is not one is ignored, so the checkout's own version (a development
        # run) or `unknown` is reported - both of which are versions or the honest word.
        self.assertRegex(subtitle_main.read_app_version(), r'^(\d+\.\d+\.\d+|unknown)$')

    def restore_release_manifest(self, previous) -> None:
        from components import app_version
        if previous is None:
            os.environ.pop(app_version.ENVIRONMENT_VARIABLE, None)
        else:
            os.environ[app_version.ENVIRONMENT_VARIABLE] = previous

    def test_catalog_and_availability_expose_the_same_models(self) -> None:
        catalog = self.client.get('/api/catalog', headers=self.headers).json()
        self.assertEqual(catalog['default_model'], 'whisper-turbo')
        self.assertEqual(len(catalog['models']), 3)
        availability = self.client.get('/api/transcribe/availability',
                                       headers=self.headers).json()
        self.assertEqual(availability['default_model'], 'whisper-turbo')
        self.assertTrue(availability['available'])
        self.assertEqual(len(availability['models']), 3)
        repos = {model['repo'] for model in availability['models']}
        self.assertIn('mlx-community/whisper-large-v3-turbo', repos)

    def test_hub_transcription_contract_returns_srt_and_identity(self) -> None:
        response = self._upload('/api/transcribe',
                                {'model': 'whisper-turbo', 'language': 'en',
                                 'word_timestamps': 'false', 'activity_id': 'stt-hub-1'})
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertIn('00:00:00,000 -->', payload['srt'])
        self.assertTrue(payload['vtt'].startswith('WEBVTT'))
        self.assertEqual(payload['text'], 'hello world second cue')
        self.assertEqual(payload['studio_task_id'], payload['task_id'])
        self.assertEqual(payload['model_revision'], TURBO_REVISION)
        self.assertEqual(payload['package_id'], 'whisper-turbo')
        job = self.manager.get(payload['job_id'])
        self.assertEqual(job['activity_id'], 'stt-hub-1')
        self.assertEqual(job['state'], 'done')

    def test_hub_transcription_refuses_a_non_runnable_model(self) -> None:
        response = self._upload('/api/transcribe', {'model': 'whisper-base'})
        self.assertEqual(response.status_code, 409)
        self.assertIn('safetensors', response.text)

    def test_hub_transcription_rejects_a_foreign_extension(self) -> None:
        response = self.client.post(
            '/api/transcribe', headers=self.headers, data={'model': 'whisper-turbo'},
            files={'file': ('notes.txt', io.BytesIO(b'hello'), 'text/plain')})
        self.assertEqual(response.status_code, 422)

    def test_studio_submit_status_cancel_and_artifact(self) -> None:
        response = self._upload('/api/generate/transcribe',
                                {'model': 'whisper-turbo', 'formats': 'srt,vtt'})
        self.assertEqual(response.status_code, 200, response.text)
        job_id = response.json()['job']['id']
        value = self.wait(self.manager, job_id)
        self.assertEqual(value['state'], 'done')
        self.assertNotIn('input', response.json()['job'])

        detail = self.client.get(f'/api/generate/transcribe/jobs/{job_id}',
                                 headers=self.headers).json()['job']
        self.assertEqual(detail['state'], 'done')
        self.assertGreater(detail['artifacts']['srt']['bytes'], 0)
        self.assertEqual(len(detail['artifacts']['srt']['sha256']), 64)

        listing = self.client.get('/api/generate/transcribe/jobs',
                                  headers=self.headers).json()['jobs']
        self.assertEqual([item['id'] for item in listing], [job_id])

        artifact = self.client.get(
            f'/api/generate/transcribe/jobs/{job_id}/artifact?format=vtt',
            headers=self.headers)
        self.assertEqual(artifact.status_code, 200)
        self.assertTrue(artifact.headers['content-type'].startswith('text/vtt'))
        self.assertTrue(artifact.text.startswith('WEBVTT'))
        self.assertEqual(artifact.headers['x-content-sha256'],
                         value['artifacts']['vtt']['sha256'])
        self.assertEqual(self.client.get(
            f'/api/generate/transcribe/jobs/{job_id}/artifact?format=pdf',
            headers=self.headers).status_code, 404)

        ack = self.client.post(f'/api/generate/transcribe/jobs/{job_id}/ack',
                               headers=self.headers)
        self.assertEqual(ack.status_code, 200)
        self.assertTrue(ack.json()['ok'])
        self.assertEqual(self.client.post(
            f'/api/generate/transcribe/jobs/{job_id}/ack',
            headers=self.headers).json()['sha256'], ack.json()['sha256'])

        cancel = self.client.delete(f'/api/generate/transcribe/jobs/{job_id}',
                                    headers=self.headers)
        self.assertEqual(cancel.status_code, 200)
        self.assertEqual(cancel.json()['job']['state'], 'done')

    def test_studio_submit_requires_a_file(self) -> None:
        response = self.client.post('/api/generate/transcribe', headers=self.headers,
                                    data={'model': 'whisper-turbo'})
        self.assertEqual(response.status_code, 422)

    def test_unknown_job_returns_404(self) -> None:
        self.assertEqual(self.client.get('/api/generate/transcribe/jobs/nope',
                                         headers=self.headers).status_code, 404)
        self.assertEqual(self.client.delete('/api/generate/transcribe/jobs/nope',
                                            headers=self.headers).status_code, 404)
        self.assertEqual(self.client.get(
            '/api/generate/transcribe/jobs/nope/artifact', headers=self.headers).status_code, 404)

    def test_uploads_never_overwrite_each_other(self) -> None:
        first = self._upload('/api/generate/transcribe', {'model': 'whisper-turbo'})
        second = self._upload('/api/generate/transcribe', {'model': 'whisper-turbo'})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        first_job = first.json()['job']['id']
        second_job = second.json()['job']['id']
        self.assertNotEqual(first_job, second_job)
        first_value = self.manager.get(first_job)
        second_value = self.manager.get(second_job)
        self.assertNotEqual(first_value['input']['sha256'] + first_job,
                            second_value['input']['sha256'] + second_job)
        stored = sorted(path.name for path in (self.data / 'inputs').iterdir())
        self.assertEqual(len(stored), 2)
        self.assertNotEqual(stored[0], stored[1])
        for job_id in (first_job, second_job):
            value = self.manager.get(job_id)
            self.assertTrue(Path(value['input']['original_path']).name.startswith('original'))


@unittest.skipUnless(REAL_LIBRARY, 'set STUDIOHUB_TEST_MODEL_LIBRARY to run real Whisper')
class SubtitleRealModelTests(unittest.TestCase):
    """Real decoding against the verified packages. Skipped without a library."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.library = Path(REAL_LIBRARY)
        cls.temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.root = Path(cls.temporary.name)
        cls.manifests = []
        for name in ('whisper-turbo.json', 'whisper-base.json', 'whisper-small.json'):
            path = PROJECT_ROOT / 'model-manifests' / name
            if path.is_file():
                cls.manifests.append(json.loads(path.read_text()))
        cls.audio = cls.root / 'tone.wav'
        _write_tone(cls.audio, seconds=1.0)
        cls.manager = SubtitleManager(cls.root / 'state', cls.library, cls.manifests,
                                      'c' * 64, voice_app_dir=VOICE_APP)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.manager.engine.unload('test-teardown')
        cls.temporary.cleanup()

    def _run(self, **overrides) -> dict:
        source = self.manager.store_input('real', 'tone.wav', self.audio.read_bytes())
        request = {'model': 'whisper-turbo', 'formats': ['srt', 'vtt', 'text'],
                   **overrides}
        job = self.manager.submit(request, source)
        return self._wait(job['id'])

    def test_turbo_transcribes_and_exports_from_the_verified_package(self) -> None:
        readiness = self.manager.readiness('whisper-turbo')
        self.assertTrue(readiness['ready'], readiness['reason'])
        value = self._run()
        self.assertEqual(value['state'], 'done', value.get('error'))
        self.assertEqual(value['package_id'], 'whisper-turbo')
        self.assertEqual(value['model_revision'], TURBO_REVISION)
        self.assertIsNotNone(value['media_duration_seconds'])
        self.assertGreater(value['media_duration_seconds'], 0)
        srt = Path(value['artifacts']['srt']['path']).read_text()
        self.assertTrue(srt.strip())
        self.assertRegex(srt, r'^\d+\n\d{2}:\d{2}:\d{2},\d{3} --> ')
        self.assertTrue(Path(value['artifacts']['vtt']['path']).read_text().startswith('WEBVTT'))
        self.assertIsNotNone(self.manager.ack(value['id']))

    def test_base_and_small_are_not_claimed_as_runnable(self) -> None:
        for package_id in ('whisper-base', 'whisper-small'):
            readiness = self.manager.readiness(package_id)
            self.assertFalse(readiness['ready'], package_id)
            self.assertEqual(readiness['files']['state'], 'unsupported_weight_format')

    def test_cancel_during_real_decoding_publishes_no_artifact(self) -> None:
        source = self.manager.store_input('real', 'tone.wav', self.audio.read_bytes())
        job = self.manager.submit(
            {'model': 'whisper-turbo', 'formats': ['srt', 'vtt', 'text']}, source)
        # Wait until decoding actually started, then cancel mid-flight.
        deadline = time.time() + 120
        while time.time() < deadline:
            value = self.manager.get(job['id'])
            if value['state'] == 'running' and value.get('progress', 0) >= 0.1:
                break
            time.sleep(0.05)
        self.manager.cancel(job['id'])
        value = self._wait(job['id'])
        self.assertEqual(value['state'], 'cancelled', value.get('error'))
        self.assertEqual(value['artifacts'], {})
        self.assertFalse((self.manager.artifacts / f"{job['id']}.srt").exists())

    def test_verified_package_tree_is_never_modified_by_decoding(self) -> None:
        """Loading must not disturb the pinned package the ST-01 receipt covers.

        Regression: attaching the tokenizer straight from the package folder
        bumps every file's ctime, so ST-01's preflight reports
        "File changed; verify again" after each real transcription. The engine
        stages the verified companion tokenizer into Subtitle's own state.
        """
        location = self.manager.engine.package('whisper-turbo')

        def metadata() -> dict:
            return {str(path.relative_to(location.folder)):
                    (path.stat().st_ino, path.stat().st_mtime_ns,
                     path.stat().st_ctime_ns, path.stat().st_size)
                    for path in sorted(location.folder.rglob('*')) if path.is_file()}

        before = metadata()
        self._run()
        self.assertEqual(before, metadata())
        self.assertEqual(self.manager.readiness('whisper-turbo')['files']['state'],
                         'files_verified')
        # The tokenizer really was staged privately, not read in place.
        processor = self.manager.data / 'processor' / 'whisper-turbo' / location.revision
        self.assertTrue((processor / 'tokenizer.json').is_file())

    def _wait(self, job_id: str, timeout: float = 600) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            value = self.manager.get(job_id)
            if value['state'] in ('done', 'error', 'cancelled'):
                return value
            time.sleep(0.1)
        self.fail('real Whisper job did not finish within 600s')


class SubtitleModelIdentifierTests(unittest.TestCase):
    """The Hub's transcription contract identifies a model by repository.

    The worker keys its catalogue by package id. A packaged Subtitle job was answered with
    ``422 choose one of the packaged Whisper models`` because only the package id was accepted,
    so both spellings are pinned here.
    """

    aliases = {'mlx-community/whisper-large-v3-turbo': 'whisper-turbo'}

    def test_model_repository_resolves_to_its_package(self) -> None:
        from components.subtitle.app.backend import manager as subtitle_manager

        request = subtitle_manager.validate_request(
            {'model': 'mlx-community/whisper-large-v3-turbo', 'language': 'en',
             'word_timestamps': False, 'formats': ['srt']},
            packages=['whisper-turbo'], default_package='whisper-turbo',
            aliases=self.aliases)
        self.assertEqual(request['model'], 'whisper-turbo')

    def test_package_id_still_resolves_to_itself(self) -> None:
        from components.subtitle.app.backend import manager as subtitle_manager

        request = subtitle_manager.validate_request(
            {'model': 'whisper-turbo', 'formats': ['srt']},
            packages=['whisper-turbo'], default_package='whisper-turbo',
            aliases=self.aliases)
        self.assertEqual(request['model'], 'whisper-turbo')

    def test_an_unpackaged_model_is_still_refused(self) -> None:
        from components.subtitle.app.backend import manager as subtitle_manager

        with self.assertRaisesRegex(ValueError, 'no packaged subtitle model') as caught:
            subtitle_manager.validate_request(
                {'model': 'someone/unknown-whisper', 'formats': ['srt']},
                packages=['whisper-turbo'], default_package='whisper-turbo',
                aliases=self.aliases)
        # The refusal names the entry that was asked for and the ones that exist, so a missing
        # package is diagnosable without reading this worker's source.
        self.assertIn('someone/unknown-whisper', str(caught.exception))
        self.assertIn('whisper-turbo', str(caught.exception))

    def test_engine_publishes_repository_aliases(self) -> None:
        """The alias map must be derived from the real manifests, not hard-coded."""
        import inspect

        from components.subtitle.app.backend import manager as subtitle_manager

        source = inspect.getsource(subtitle_manager.SubtitleManager.model_aliases)
        self.assertIn('model_id', source)
        self.assertIn('manifests', source)


class SubtitleMemoryPolicyTests(SubtitleFixture):
    """The Subtitle worker must not hold a Whisper model resident forever.

    Voice has an idle-release policy; Subtitle had ``engine.unload()`` with nothing calling
    it, so a model could stay in memory for the process lifetime — the leak that matters most
    on an 8 GB machine.

    The endpoint *names* and the status *fields* are part of the contract with the Hub's
    fleet controller, not an implementation detail: a Studio that answers on a different path
    or publishes a different vocabulary is invisible to the machine-wide release pass, or
    worse, is counted as released while it still holds its model.
    """

    # The names every Studio in the fleet publishes, and the fields the Hub reads back.
    FLEET_PATHS = {'get': '/api/memory-policy', 'put': '/api/memory-policy',
                   'release': '/api/memory/release'}
    FLEET_FIELDS = {'mode', 'resolved_mode', 'default_mode', 'idle_seconds', 'machine_mode',
                    'options', 'loaded_models',
                    'model_idle_seconds', 'active_work', 'busy', 'next_release_at',
                    'last_release_at', 'last_release_reason', 'last_release_details',
                    'last_error', 'release_count', 'counters_since'}

    def setUp(self) -> None:
        super().setUp()
        self.manager = self.build_manager()
        self.manager.engine.offline = True
        self.client = TestClient(create_app(self.manager, TOKEN))
        self.headers = {'X-Hub-Token': TOKEN}

    def test_modes_match_the_voice_worker(self) -> None:
        from components import memory_modes
        from components.subtitle.app.backend import memory_policy

        self.assertIs(memory_policy.MODES, memory_modes.MODES)
        self.assertEqual(list(memory_policy.MODES),
                         ['auto', 'performance', 'balanced', 'memory_saver', 'immediate'])
        self.assertIsNone(memory_policy.MODES['performance']['idle_seconds'])
        self.assertEqual(memory_policy.MODES['immediate']['idle_seconds'], 0)
        self.assertEqual(memory_policy.DEFAULT_MODE, 'auto')

    def test_auto_resolves_to_this_machines_mode(self) -> None:
        """The rule that used to live in `default_mode`, now Auto's answer.

        `default_mode()` is the *selection* (`auto`); what auto means here is
        `machine_mode()`/`resolved_mode()`, and a machine that cannot measure its memory
        resolves to `immediate` — it must not hold a model it cannot prove fits.
        """
        from components.subtitle.app.backend import memory_policy

        original = memory_policy.machine_bytes
        try:
            memory_policy.machine_bytes = lambda: 8 * 1024**3
            self.assertEqual(memory_policy.default_mode(), 'auto')
            self.assertEqual(memory_policy.machine_mode(), 'immediate')
            self.assertEqual(memory_policy.resolved_mode('auto'), 'immediate')
            self.assertEqual(memory_policy.idle_seconds('auto'), 0)
            memory_policy.machine_bytes = lambda: 32 * 1024**3
            self.assertEqual(memory_policy.machine_mode(), 'balanced')
            self.assertEqual(memory_policy.resolved_mode('auto'), 'balanced')
            self.assertEqual(memory_policy.idle_seconds('auto'), 600)
            memory_policy.machine_bytes = lambda: 0
            self.assertEqual(memory_policy.machine_mode(), 'immediate')
        finally:
            memory_policy.machine_bytes = original

    def test_an_operator_choice_beats_the_machine_default(self) -> None:
        import tempfile
        from pathlib import Path
        from components.subtitle.app.backend import memory_policy

        with tempfile.TemporaryDirectory() as temporary:
            original = memory_policy.settings_file
            memory_policy.settings_file = lambda: Path(temporary) / 'memory_policy.json'
            try:
                self.assertIn(memory_policy.save('memory_saver')['mode'], {'memory_saver'})
                self.assertEqual(memory_policy.status()['idle_seconds'], 120)
                with self.assertRaises(ValueError):
                    memory_policy.save('bogus')
            finally:
                memory_policy.settings_file = original

    def test_active_work_is_never_released(self) -> None:
        from unittest.mock import patch
        from components.subtitle.app.backend import memory_policy

        with patch.object(memory_policy, '_active', return_value=True):
            with self.assertRaisesRegex(RuntimeError, 'active'):
                memory_policy.release_now('idle')

    def test_worker_publishes_the_fleet_paths_and_field_shape(self) -> None:
        """The Hub reaches every Studio by these exact names; a rename silently breaks it."""
        policy = self.client.get(self.FLEET_PATHS['get'], headers=self.headers)
        self.assertEqual(policy.status_code, 200)
        payload = policy.json()
        self.assertTrue(self.FLEET_FIELDS <= set(payload),
                        f'missing fleet fields: {sorted(self.FLEET_FIELDS - set(payload))}')
        self.assertEqual([row['mode'] for row in payload['options']],
                         ['auto', 'performance', 'balanced', 'memory_saver', 'immediate'])
        self.assertIsNotNone(payload['idle_seconds'],
                             'auto must publish the resolved threshold, never None')
        self.assertIn(payload['resolved_mode'], {'balanced', 'immediate'})
        self.assertEqual(payload['resolved_mode'], payload['machine_mode'])

        saved = self.client.put(self.FLEET_PATHS['put'], headers=self.headers,
                                json={'mode': 'memory_saver'})
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()['mode'], 'memory_saver')
        self.assertEqual(saved.json()['idle_seconds'], 120)

        rejected = self.client.put(self.FLEET_PATHS['put'], headers=self.headers,
                                   json={'mode': 'overnight'})
        self.assertEqual(rejected.status_code, 422)

        # The old `/api/memory/policy` spelling must not linger as a second contract.
        self.assertEqual(self.client.get('/api/memory/policy', headers=self.headers).status_code,
                         404)

    def test_release_reports_residency_the_hub_can_read_back(self) -> None:
        """`loaded_models` must show what is resident, not just that a release was asked for.

        The Hub counts a release as successful only when the response no longer lists a
        loaded model, so a Studio that reported residency as a private boolean would let a
        failed unload pass as a release.
        """
        package = self.manager.engine.load('whisper-turbo')
        self.assertIsNotNone(package)
        resident = self.client.get(self.FLEET_PATHS['get'], headers=self.headers).json()
        self.assertEqual(resident['loaded_models'], [['whisper-turbo']])
        self.assertTrue(resident['resident'])

        released = self.client.post(self.FLEET_PATHS['release'], headers=self.headers)
        self.assertEqual(released.status_code, 200)
        body = released.json()
        self.assertTrue(body['released'])
        self.assertEqual(body['loaded_models'], [])
        self.assertIsNone(self.manager.engine.loaded_package)
        self.assertEqual(body['last_release_reason'], 'manual')

        idle = self.client.post(self.FLEET_PATHS['release'], headers=self.headers)
        self.assertEqual(idle.status_code, 200)
        self.assertFalse(idle.json()['released'])

    def test_release_refuses_while_transcription_is_running(self) -> None:
        from unittest.mock import patch
        from components.subtitle.app.backend import memory_policy

        with patch.object(memory_policy, '_active', return_value=True):
            response = self.client.post(self.FLEET_PATHS['release'], headers=self.headers)
        self.assertEqual(response.status_code, 409)
        self.assertIn('active', response.json()['detail'])

    def test_the_idle_timer_starts_from_the_job_records(self) -> None:
        """Activity is derived from job history so no endpoint can forget to report it."""
        from components.subtitle.app.backend import memory_policy

        # Bind the manager directly rather than starting the idle thread: the thread holds
        # this manager forever in module state, which outlives the test's temporary library.
        original = memory_policy._MANAGER
        memory_policy._MANAGER = self.manager
        self.addCleanup(setattr, memory_policy, '_MANAGER', original)
        self.manager.engine.load('whisper-turbo')
        self.assertTrue(memory_policy.status()['loaded_models'])
        job = self.submit(self.manager)
        finished = self.wait(self.manager, job['id'])
        self.assertEqual(finished['state'], 'done')
        newest = memory_policy._latest_job_time()
        self.assertIsNotNone(newest)
        self.assertLessEqual(abs(time.time() - newest), 30.0)
        self.assertEqual(memory_policy._idle_since(), newest)


if __name__ == '__main__':
    unittest.main()


class SnapshotViewReuseTests(unittest.TestCase):
    """A cached model view must never be reused once its links dangle.

    A view is built from absolute symlinks into the model library, so it is only valid while that
    library stays where it was. Every link dangles after the library moves, while the marker file
    still says the view is finished - so it is never rebuilt, and mlx-audio reports

        Config not found at .../snapshot-views/whisper-base-1e3e249fb8d01c

    for a model whose `config.json` is sitting in the library. That is exactly what happened when
    the library moved on 2026-09-18: `whisper-base` and `whisper-small` failed while
    `whisper-turbo` kept working, because turbo needs no view at all.

    The second defect was in the check that was supposed to catch it: `Path.is_file()` follows a
    symlink, but on macOS it answers **true** for a broken one, so `(view / 'config.json').is_file()`
    passed on a file that did not exist.
    """

    def setUp(self):
        import sys
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self._old_data = os.environ.get("STUDIOHUB_DATA_DIR")
        os.environ["STUDIOHUB_DATA_DIR"] = str(self.root / "state")
        sys.path.insert(0, str(VOICE_APP))

    def tearDown(self):
        import sys
        if self._old_data is None:
            os.environ.pop("STUDIOHUB_DATA_DIR", None)
        else:
            os.environ["STUDIOHUB_DATA_DIR"] = self._old_data
        if str(VOICE_APP) in sys.path:
            sys.path.remove(str(VOICE_APP))
        self.temporary.cleanup()

    def _cache(self):
        from backend import cache
        return cache

    def test_an_empty_view_is_not_a_finished_view(self):
        """What actually went wrong: a view that exists but holds nothing.

        An earlier version of this test asserted that `is_file()` is true for a broken symlink. It
        is not - measured on Python 3.12, `is_file()` is false while `is_symlink()` is true - so the
        engine's `(shared[1] / 'config.json').is_file()` check was not the defect it looked like.
        The real shape was a view directory that existed, carried no usable entries, and was
        returned anyway because only the marker file was consulted.
        """
        cache = self._cache()
        view = self.root / "empty-view"
        view.mkdir()
        (view / ".studiohub-view").write_text("pkg@rev\n")
        # No entries at all: present on disk, useless to mlx-audio.
        self.assertFalse(cache._view_is_sound(view),
                         "a view with nothing usable in it must be rebuilt, not reused")
        self.assertFalse(view.exists())

    def test_a_view_with_dangling_links_is_rejected_and_removed(self):
        cache = self._cache()
        view = self.root / "view"
        view.mkdir()
        (view / ".studiohub-view").write_text("pkg@rev\n")
        os.symlink(self.root / "missing-config.json", view / "config.json")

        self.assertFalse(cache._view_is_sound(view),
                         "a view whose links dangle must not be reused")
        self.assertFalse(view.exists(), "and it must be removed so it can be rebuilt")

    def test_a_view_can_be_rebuilt_after_its_library_moves(self):
        """The whole point: a second attempt must succeed, not stay broken."""
        cache = self._cache()
        library = self.root / "library"
        (library / "model").mkdir(parents=True)
        (library / "model" / "config.json").write_text("{}")
        # A real npz: the builder converts npz-only packages, so a fake one would abort the build
        # for a reason unrelated to what this test is about.
        import numpy as np
        np.savez(library / "model" / "weights.npz", weight=np.zeros(4, dtype="float32"))

        package, revision = "a-package", "a" * 40
        first = cache._merged_snapshot_view(package, revision, library)
        self.assertIsNotNone(first, "the view should build once")
        self.assertTrue((first / "config.json").exists())

        # Move the library, exactly as tidying it up would.
        moved = self.root / "library-moved"
        library.rename(moved)
        self.assertFalse((first / "config.json").exists(), "the cached view now dangles")

        second = cache._merged_snapshot_view(package, revision, moved)
        self.assertIsNotNone(second, "the view must be rebuilt against the new location")
        self.assertEqual(second, first, "rebuilt in place")
        self.assertTrue((second / "config.json").exists(),
                        "and its config must resolve again")
