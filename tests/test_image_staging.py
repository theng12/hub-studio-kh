"""Image memory: the per-tier plan, the stage hooks, the bounded VAE decode and model residency.

What these pin, in the order a job meets them:

* ``memory_plan`` chooses, from this Mac's memory, between the staged configuration (8 GB), a lean
  one (16 GB) and the default (24 GB and up, unchanged), per model, and ``loading_mode`` overrides it;
* ``staging.apply`` makes mflux's encode -> denoise -> decode actually run in stages: the encoder is
  freed once the prompt embeddings are *evaluated*, and the transformer is freed when the loop ends
  even though mflux keeps a (possibly ``mx.compile``-d) ``predict`` closure over it;
* ``vae_bounded`` computes the FLUX.2 decoder's GroupNorm and 3x3 convolution in bands and agrees with
  mlx's own to bf16 rounding;
* a model whose plan keeps every component loaded stays resident between jobs, and is released by the
  idle window, the Hub's release, memory pressure, a different configuration or a failed job.
"""
from __future__ import annotations

import gc
from pathlib import Path
import sys
import tempfile
import threading
import time
import types
import unittest
import weakref
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(PROJECT_ROOT), str(Path(__file__).resolve().parent)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import mlx.core as mx  # noqa: E402  (imported up front: a test that patches sys.modules must not be the first to import it)
from mlx import nn  # noqa: E402
from PIL import Image  # noqa: E402

from components.image.app.backend import generation, main as image_main, memory_policy, staging  # noqa: E402
from components.image.app.backend import streaming, vae_bounded  # noqa: E402
from components.image.app.backend.generation import (  # noqa: E402
    FLUX2_KLEIN, Z_IMAGE_TURBO, GenerationManager, apply_memory_plan, memory_plan)

from test_image_memory_pressure import install_tiny_packages  # noqa: E402
import _image_pressure  # noqa: E402

GiB = 1024 ** 3
TOKEN = 'imageownercred_' * 3
RUNTIME_ID = 'a' * 64


class TierPlanTests(unittest.TestCase):
    def test_flux_klein_is_staged_on_8gb_lean_on_16gb_and_unchanged_from_24gb(self):
        small = memory_plan(FLUX2_KLEIN, 8 * GiB)
        self.assertEqual((small['mode'], small['low_ram'], small['staged'], small['lean']),
                         ('low-ram', True, True, False))
        self.assertTrue(small['bounded_vae_decode'])
        self.assertFalse(small['resident_ok'], 'a staged job frees its components as it goes')
        self.assertEqual(small['mlx_cache_limit_gb'], 0.5)
        # The FLUX.2 decoder must not be tiled (per-tile GroupNorm statistics shift the colour);
        # the bounded decode is what replaces tiling for it.
        self.assertFalse(small['vae_tiling'])

        middle = memory_plan(FLUX2_KLEIN, 16 * GiB)
        self.assertEqual((middle['mode'], middle['low_ram'], middle['staged'], middle['lean']),
                         ('default', False, False, True))
        self.assertTrue(middle['bounded_vae_decode'])
        self.assertTrue(middle['resident_ok'])
        self.assertEqual(middle['mlx_cache_limit_gb'], 2.0)

        for host in (24 * GiB, 32 * GiB, 128 * GiB):
            plan = memory_plan(FLUX2_KLEIN, host)
            with self.subTest(host=host):
                self.assertEqual((plan['mode'], plan['staged'], plan['lean'],
                                  plan['bounded_vae_decode']), ('default', False, False, False))
                self.assertIsNone(plan['mlx_cache_limit_gb'])
                self.assertTrue(plan['resident_ok'])

    def test_z_image_is_staged_below_24gb_with_the_tiling_it_always_had(self):
        for host in (8 * GiB, 16 * GiB):
            plan = memory_plan(Z_IMAGE_TURBO, host)
            with self.subTest(host=host):
                self.assertEqual((plan['mode'], plan['staged'], plan['vae_tiling']),
                                 ('low-ram', True, True))
                self.assertFalse(plan['bounded_vae_decode'], 'bounded decode is for the FLUX.2 VAE')
                self.assertFalse(plan['resident_ok'])
        self.assertEqual(memory_plan(Z_IMAGE_TURBO, 32 * GiB)['mode'], 'default')

    def test_the_loading_mode_setting_overrides_the_tier(self):
        self.assertEqual(memory_plan(FLUX2_KLEIN, 8 * GiB, 'full')['mode'], 'default')
        self.assertFalse(memory_plan(FLUX2_KLEIN, 8 * GiB, 'full')['staged'])
        self.assertFalse(memory_plan(FLUX2_KLEIN, 16 * GiB, 'full')['lean'])
        self.assertTrue(memory_plan(FLUX2_KLEIN, 128 * GiB, 'low')['staged'])
        self.assertFalse(memory_plan(FLUX2_KLEIN, 128 * GiB, 'low')['lean'])

    def test_a_staged_plan_goes_through_the_cli_mapping_and_the_hooks(self):
        calls: list = []

        class CallbackManager:
            @staticmethod
            def _register_memory_saver(args, model):
                calls.append(args)
                return 'saver'

        modules = {name: types.ModuleType(name) for name in
                   ('mflux', 'mflux.callbacks', 'mflux.callbacks.callback_manager')}
        modules['mflux.callbacks.callback_manager'].CallbackManager = CallbackManager
        plan = memory_plan(FLUX2_KLEIN, 8 * GiB)
        with patch.dict(sys.modules, modules):
            saver = apply_memory_plan(object(), plan)
        self.assertEqual(saver, 'saver')
        self.assertEqual((calls[0].low_ram, calls[0].mlx_cache_limit_gb, calls[0].vae_tiling),
                         (True, 0.5, False))
        # An object that is not an mflux model gets no hooks, and the plan says so.
        self.assertEqual(plan['hooks'], [])


class _Registry:
    def __init__(self):
        self.after_loop = []

    def register(self, callback):
        if hasattr(callback, 'call_after_loop'):
            self.after_loop.append(callback)


class _Transformer:
    def __call__(self, **_kwargs):
        return mx.ones((2,))


class _TextEncoder:
    pass


class _FakeModel:
    """The three things mflux's generate_image does that the hooks rely on, and nothing else."""

    def __init__(self):
        self.text_encoder = _TextEncoder()
        self.transformer = _Transformer()
        self.callbacks = _Registry()
        self.vae = None

    def _encode_prompt_pair(self, *, prompt, negative_prompt, guidance):
        return mx.zeros((1, 2)), mx.zeros((1,)), None, None

    @staticmethod
    def _predict(transformer):
        # mflux wraps this in mx.compile on most Apple Silicon; either way the closure keeps the
        # transformer alive for as long as the function object lives.
        def predict(**kwargs):
            return transformer(**kwargs)
        return predict


class StageHookTests(unittest.TestCase):
    def test_the_encoder_is_freed_once_the_embeddings_exist_and_the_transformer_after_the_loop(self):
        model = _FakeModel()
        encoder_ref, transformer_ref = weakref.ref(model.text_encoder), weakref.ref(model.transformer)
        events: list[str] = []
        installed = staging.apply(model, on_stage=events.append)
        self.assertEqual(installed, ['encode-then-free', 'free-transformer-after-loop'])

        embeds, *_ = model._encode_prompt_pair(prompt='p', negative_prompt=' ', guidance=1.0)
        gc.collect()
        self.assertIsNone(model.text_encoder)
        self.assertIsNone(encoder_ref(), 'the encoder must be unreferenced, not just detached')
        self.assertEqual(embeds.shape, (1, 2))

        # What generate_image holds across the loop: the predict function over the transformer.
        predict = model._predict(model.transformer)
        self.assertEqual(float(predict(latents=1).sum()), 2.0)
        self.assertIsNotNone(transformer_ref())
        for callback in model.callbacks.after_loop:
            callback.call_after_loop(seed=1, prompt='p', latents=None, config=None)
        gc.collect()
        self.assertIsNone(model.transformer)
        self.assertIsNone(transformer_ref(),
                          'the loop\'s predict function kept the transformer alive after the loop')
        self.assertEqual(events, ['encoded', 'denoised'])
        del predict

    def test_a_model_that_is_not_what_the_hooks_were_written_against_is_left_alone(self):
        self.assertEqual(staging.apply(object()), [])
        bare = types.SimpleNamespace(transformer=object(), text_encoder=object())
        self.assertEqual(staging.apply(bare), [])
        self.assertFalse(staging.supports(bare))

    def test_stages_off_installs_nothing_that_could_break_a_second_job(self):
        model = _FakeModel()
        self.assertEqual(staging.apply(model, stages=False), [])
        model._encode_prompt_pair(prompt='p', negative_prompt=' ', guidance=1.0)
        self.assertIsNotNone(model.text_encoder)
        self.assertEqual(model.callbacks.after_loop, [])


class BoundedVaeTests(unittest.TestCase):
    """The banded GroupNorm and 3x3 conv are the same maths as mlx's, to bf16 rounding."""

    def setUp(self):
        mx.random.seed(3)

    def test_group_norm_with_global_statistics_matches_mlx(self):
        norm = nn.GroupNorm(32, 64, eps=1e-6, pytorch_compatible=True)
        norm.weight = mx.random.normal((64,)) * 0.3 + 1.0
        norm.bias = mx.random.normal((64,)) * 0.1
        x = (mx.random.normal((1, 40, 24, 64)) * 2 + 0.5).astype(mx.bfloat16)
        reference = nn.silu(norm(x.astype(mx.float32)).astype(mx.bfloat16)).astype(mx.float32)
        # A band of two rows, so statistics must come from the whole map, not a band.
        banded = vae_bounded._group_norm_silu(norm, x, band_bytes=2 * 24 * 64 * 4, silu=True)
        self.assertEqual(banded.dtype, mx.bfloat16)
        self.assertLess(float(mx.abs(reference - banded.astype(mx.float32)).max()), 0.05)
        # And a band-local statistic would have been visibly different (negative control).
        local = nn.silu(norm(x[:, :2].astype(mx.float32)).astype(mx.bfloat16)).astype(mx.float32)
        self.assertGreater(float(mx.abs(local - reference[:, :2]).max()), 0.05)

    def test_banded_3x3_conv_matches_the_whole_map_conv_including_the_edges(self):
        conv = nn.Conv2d(16, 8, 3, padding=1)
        conv.weight = conv.weight.astype(mx.bfloat16)
        conv.bias = conv.bias.astype(mx.bfloat16)
        x = mx.random.normal((1, 37, 20, 16)).astype(mx.bfloat16)
        reference = conv(x).astype(mx.float32)
        for band in (1, 3, 7, 50):
            banded = vae_bounded._conv3x3(conv, x, band_bytes=band * 20 * 16 * 2)
            with self.subTest(rows=band):
                self.assertEqual(banded.shape, reference.shape)
                self.assertLess(float(mx.abs(reference - banded.astype(mx.float32)).max()), 0.05)

    def test_supports_is_by_name_and_shape(self):
        self.assertFalse(vae_bounded.supports(object()))
        decoder_shaped = types.SimpleNamespace(decoder=types.SimpleNamespace(up_blocks=[]),
                                               unpack_packed_latents=lambda x: x)
        self.assertFalse(vae_bounded.supports(decoder_shaped), 'not a Flux2VAE, however it is shaped')


class _Block(nn.Module):
    """A block with one big weight, called the way mflux calls its blocks (keywords)."""

    def __init__(self, size: int = 1024):
        super().__init__()
        self.linear = nn.Linear(size, size, bias=True)

    def __call__(self, *, x):
        return mx.tanh(self.linear(x))


class StreamingTests(unittest.TestCase):
    SIZE = 1024   # a 4 MB float32 weight per block; 6 blocks

    def component(self):
        mx.random.seed(11)
        blocks = [_Block(self.SIZE) for _ in range(6)]
        mx.eval([block.parameters() for block in blocks])
        reference = [tree_copy(block.parameters()) for block in blocks]
        return types.SimpleNamespace(layers=blocks), reference

    def test_a_streamed_pass_equals_the_resident_pass_and_releases_each_block(self):
        component, reference = self.component()
        x = mx.random.normal((1, 8, self.SIZE))
        expected = x
        for block in list(component.layers):
            expected = block(x=expected)
        mx.eval(expected)

        loads = {'count': 0}

        def loader():
            loads['count'] += 1
            return {'layers': [tree_copy(tree) for tree in reference]}

        source = streaming.apply(component, ('layers',), loader=loader)
        gc.collect()
        mx.clear_cache()
        resident_before = mx.get_active_memory()
        out = x
        for step in range(3):                       # three passes, like three denoise steps
            out = x
            for block in component.layers:
                out = block(x=out)
            mx.eval(out)
            gc.collect()
            mx.clear_cache()
            # Every block was released at the end of its own call: nothing of the weights is left.
            self.assertLess(mx.get_active_memory() - resident_before, 2 * self.SIZE * self.SIZE * 4,
                            'a streamed pass left its blocks\' weights resident')
        self.assertEqual(loads['count'], 3, 'one lazy re-open per pass, not per block')
        self.assertLess(float(mx.abs(out - expected).max()), 1e-5)
        source.close()
        self.assertIsNone(source.tree)

    def test_a_single_pass_component_needs_no_loader_and_is_freed_block_by_block(self):
        component, _ = self.component()
        x = mx.random.normal((1, 8, self.SIZE))
        gc.collect()
        mx.clear_cache()
        before = mx.get_active_memory()
        self.assertIsNone(streaming.apply(component, ('layers',)))
        out = x
        for block in component.layers:
            out = block(x=out)
        mx.eval(out)
        gc.collect()
        mx.clear_cache()
        self.assertLess(mx.get_active_memory() - before, 3 * self.SIZE * self.SIZE * 4,
                        'the encoder-style pass kept its weights')

    def test_supports_needs_every_named_list(self):
        component, _ = self.component()
        self.assertTrue(streaming.supports(component, ('layers',)))
        self.assertFalse(streaming.supports(component, ('layers', 'single_blocks')))
        self.assertFalse(streaming.supports(None, ('layers',)))
        self.assertFalse(streaming.supports(types.SimpleNamespace(layers=[]), ('layers',)))

    def test_every_registered_model_declares_what_its_streaming_needs(self):
        for model in generation.MODELS.values():
            policy = model.memory
            with self.subTest(model=model.repo):
                if not policy.stream_below_bytes:
                    continue
                self.assertTrue(policy.weight_definition and ':' in policy.weight_definition)
                names = [name for name, _groups, _multi in policy.streamed_components]
                self.assertEqual(sorted(names), ['text_encoder', 'transformer'])
                for _name, groups, multi_pass in policy.streamed_components:
                    self.assertTrue(groups and all(isinstance(group, str) for group in groups))
                self.assertEqual({name: multi for name, _g, multi in policy.streamed_components},
                                 {'text_encoder': False, 'transformer': True},
                                 'the encoder runs once per image, the transformer once per step')

    def test_the_8gb_plan_streams_and_a_16gb_plan_does_not(self):
        for model in generation.MODELS.values():
            expected = (('text_encoder', 'transformer')
                        if model.memory.stream_below_bytes else ())
            self.assertEqual(tuple(name for name, *_ in memory_plan(model, 8 * GiB)['stream']),
                             expected, model.repo)
            self.assertEqual(memory_plan(model, 16 * GiB)['stream'], (), model.repo)
            self.assertEqual(memory_plan(model, 8 * GiB, 'full')['stream'], (), model.repo)

    def test_a_streamed_job_builds_the_uncompiled_denoise_function(self):
        built: list = []

        class AppleSiliconUtil:
            @classmethod
            def is_m1_or_m2(cls):
                return False

        def factory(_transformer):
            built.append(AppleSiliconUtil.is_m1_or_m2())
            return lambda **kw: None

        modules = {name: types.ModuleType(name) for name in ('mflux', 'mflux.utils', 'mflux.utils.apple_silicon')}
        modules['mflux.utils.apple_silicon'].AppleSiliconUtil = AppleSiliconUtil
        with patch.dict(sys.modules, modules):
            staging._uncompiled(factory, object())
            self.assertFalse(AppleSiliconUtil.is_m1_or_m2(), 'the chip check must be put back')
        self.assertEqual(built, [True])


class MemoryProfileEvidenceTests(unittest.TestCase):
    def test_every_published_memory_figure_is_in_the_record_it_names(self):
        """A number is published with its artefact (AGENTS.md: never publish a number the model did not state)."""
        import json
        for model in generation.MODELS.values():
            profile = model.qualification.get('memory_profile')
            with self.subTest(model=model.repo):
                if not model.memory.stream_below_bytes:
                    self.assertIsNone(profile)
                    continue
                self.assertTrue(profile, 'a bounded model publishes the memory it was measured at')
                record = PROJECT_ROOT / profile['source']
                rows = [json.loads(line) for line in record.read_text().splitlines() if line.strip()]
                package = {'flux2-klein-4b-mlx-4bit': 'klein', 'z-image-turbo-mflux-4bit': 'zimage'}[model.package_id]
                for name, config in (('before', 'current'), ('staged_streamed', 'streamed')):
                    mine = [r for r in rows if r['model'] == package and r['config'] == config
                            and r['run'] == 1 and 'cache=' not in r['label']]
                    peaks = {round(r['mlx_peak_gb'], 2) for r in mine}
                    feet = {round(r['footprint_peak_gb'], 2) for r in mine}
                    self.assertIn(round(profile[name]['mlx_peak_gb'], 2), peaks)
                    self.assertTrue(any(abs(profile[name]['footprint_peak_gb'] - f) <= 0.05 for f in feet),
                                    f'{name} footprint {profile[name]["footprint_peak_gb"]} is not in {record.name}')


def tree_copy(tree):
    from mlx.utils import tree_map
    return tree_map(lambda array: mx.array(array), tree)


def _fake_klein_module(counter: dict, *, fail: bool = False):
    names = ('mflux', 'mflux.models', 'mflux.models.flux2', 'mflux.models.flux2.variants',
             'mflux.models.flux2.variants.txt2img', 'mflux.models.flux2.variants.txt2img.flux2_klein')
    modules = {name: types.ModuleType(name) for name in names}

    class Flux2Klein:
        def __init__(self, *, model_path, quantize, lora_paths, lora_scales):
            counter['inits'] = counter.get('inits', 0) + 1
            self.callbacks = types.SimpleNamespace()
            # MLX keeps its default stream per thread: a model built on one thread cannot run on another.
            self.built_on = threading.current_thread()

        def generate_image(self, *, seed, prompt, num_inference_steps, height, width, guidance):
            counter['generates'] = counter.get('generates', 0) + 1
            if self.built_on is not threading.current_thread():
                raise RuntimeError('There is no Stream(cpu, 0) in current thread.')
            if fail:
                raise RuntimeError('boom')

            class Result:
                def save(self, path, overwrite):
                    Image.new('RGB', (width, height), (5, 6, 7)).save(path, format='PNG')
            return Result()

    modules[names[-1]].Flux2Klein = Flux2Klein
    return modules


class ResidentModelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.library = root / 'models'
        self.library.mkdir()
        self.data = root / 'data'
        self.data.mkdir()
        self.flux_manifest, self.z_manifest = install_tiny_packages(self.library)
        self.counter: dict = {}
        # The module global is process-wide: restore the value that was there, not blindly None.
        self.addCleanup(setattr, memory_policy, '_MANAGER', memory_policy._MANAGER)

    def manager(self, memory_bytes: int) -> GenerationManager:
        manager = GenerationManager(self.data, self.library, self.flux_manifest, RUNTIME_ID,
                                    manifests=[self.flux_manifest, self.z_manifest],
                                    total_memory_bytes=memory_bytes)
        memory_policy._MANAGER = manager
        return manager

    def job(self, manager, modules, *, repo=FLUX2_KLEIN.repo) -> dict:
        request = {'repo': repo, 'prompt': 'a lantern', 'aspect_ratio': '1:1', 'width': 1024,
                   'height': 1024, 'seed': 7}
        with (patch.dict(sys.modules, modules),
              patch.object(image_main, '_host_eligibility',
                           return_value={'eligible': True, 'macos': '26.6.2'}),
              _image_pressure.pressure(1)):
            job_id = manager.submit(request)['id']
            for _ in range(300):
                job = manager.get(job_id)
                if job['state'] in ('done', 'error', 'cancelled'):
                    return job
                time.sleep(0.02)
        self.fail('job did not finish')

    def test_a_32gb_mac_keeps_the_model_for_the_next_job(self):
        manager = self.manager(32 * GiB)
        modules = _fake_klein_module(self.counter)
        first = self.job(manager, modules)
        self.assertEqual(first['state'], 'done', first.get('error'))
        self.assertFalse(first['model_reused'])
        self.assertEqual(manager.resident_models(), [FLUX2_KLEIN.repo])
        second = self.job(manager, modules)
        self.assertTrue(second['model_reused'])
        self.assertEqual((self.counter['inits'], self.counter['generates']), (1, 2),
                         'the second image must not reload the model')
        status = memory_policy.status()
        self.assertEqual(status['loaded_models'], [[FLUX2_KLEIN.repo]])
        self.assertTrue(status['resident'])
        self.assertIsNotNone(status['model_idle_seconds'])

    def test_an_8gb_mac_releases_after_every_job(self):
        manager = self.manager(8 * GiB)
        calls: list = []

        class CallbackManager:
            @staticmethod
            def _register_memory_saver(args, model):
                calls.append(args)
                return 'saver'

        modules = _fake_klein_module(self.counter)
        modules['mflux.callbacks'] = types.ModuleType('mflux.callbacks')
        modules['mflux.callbacks.callback_manager'] = types.ModuleType('mflux.callbacks.callback_manager')
        modules['mflux.callbacks.callback_manager'].CallbackManager = CallbackManager
        for _ in range(2):
            job = self.job(manager, modules)
            self.assertEqual(job['state'], 'done', job.get('error'))
            self.assertEqual(job['memory_mode'], 'low-ram')
            self.assertFalse(job['model_reused'])
        self.assertEqual(manager.resident_models(), [])
        self.assertEqual(self.counter['inits'], 2)
        self.assertEqual(len(calls), 2)

    def test_the_immediate_mode_releases_even_where_the_plan_could_keep_the_model(self):
        manager = self.manager(32 * GiB)
        (self.data / 'memory_policy.json').write_text('{"mode": "immediate"}', encoding='utf-8')
        job = self.job(manager, _fake_klein_module(self.counter))
        self.assertEqual(job['state'], 'done', job.get('error'))
        self.assertEqual(manager.resident_models(), [])

    def test_the_policy_of_one_manager_never_decides_another_managers_job(self):
        first = self.manager(32 * GiB)
        (self.data / 'memory_policy.json').write_text('{"mode": "immediate"}', encoding='utf-8')
        other_data = Path(self.temp.name) / 'other'
        other_data.mkdir()
        other = GenerationManager(other_data, self.library, self.flux_manifest, RUNTIME_ID,
                                  manifests=[self.flux_manifest, self.z_manifest],
                                  total_memory_bytes=32 * GiB)
        self.assertFalse(memory_policy.keep_resident(first))
        self.assertTrue(memory_policy.keep_resident(other))

    def test_memory_pressure_reloads_instead_of_reusing(self):
        manager = self.manager(32 * GiB)
        modules = _fake_klein_module(self.counter)
        self.job(manager, modules)
        with (patch.dict(sys.modules, modules),
              patch.object(image_main, '_host_eligibility',
                           return_value={'eligible': True, 'macos': '26.6.2'}),
              # Reported warning (2): eviction is decided by the real predicate; at 4 the
              # job would be refused instead, which the door test below pins.
              _image_pressure.pressure(2)):
            job_id = manager.submit({'repo': FLUX2_KLEIN.repo, 'prompt': 'x', 'aspect_ratio': '1:1',
                                     'width': 1024, 'height': 1024, 'seed': 1})['id']
            for _ in range(300):
                job = manager.get(job_id)
                if job['state'] in ('done', 'error'):
                    break
                time.sleep(0.02)
        self.assertEqual(job['state'], 'done', job.get('error'))
        self.assertFalse(job['model_reused'])
        self.assertEqual(self.counter['inits'], 2)

    def test_critical_pressure_at_the_start_door_stops_before_the_model_loads(self):
        # Admission already passed at level 1; the pressure appears while the job waits for the
        # inference lock, so the live re-read at the door is what must refuse it - and refuse it
        # before the weights are ever loaded.
        manager = self.manager(32 * GiB)
        modules = _fake_klein_module(self.counter)
        with (patch.dict(sys.modules, modules),
              patch.object(image_main, '_host_eligibility',
                           return_value={'eligible': True, 'macos': '26.6.2'}),
              _image_pressure.pressure(1) as reported):
            manager.inference_lock.acquire()
            try:
                job_id = manager.submit({'repo': FLUX2_KLEIN.repo, 'prompt': 'x',
                                         'aspect_ratio': '1:1', 'width': 1024, 'height': 1024,
                                         'seed': 1})['id']
                reported.level = 4
            finally:
                manager.inference_lock.release()
            for _ in range(300):
                job = manager.get(job_id)
                if job['state'] in ('done', 'error'):
                    break
                time.sleep(0.02)
        self.assertEqual(job['state'], 'error', job.get('error'))
        self.assertEqual(job.get('error_code'), 'MEMORY_PRESSURE_STOPPED')
        self.assertIn('before it started', job.get('error') or '')
        self.assertEqual(self.counter.get('inits', 0), 0,
                         'the door must stop the job before anything loads')

    def test_the_hub_release_drops_it_and_never_preempts_a_running_job(self):
        manager = self.manager(32 * GiB)
        self.job(manager, _fake_klein_module(self.counter))
        self.assertEqual(manager.resident_models(), [FLUX2_KLEIN.repo])
        manager.jobs['b' * 32] = {'id': 'b' * 32, 'state': 'running', 'created_at': time.time()}
        with self.assertRaises(RuntimeError):
            memory_policy.release_now('manual')
        self.assertEqual(manager.resident_models(), [FLUX2_KLEIN.repo], 'a refused release frees nothing')
        del manager.jobs['b' * 32]
        released = memory_policy.release_now('manual')
        self.assertTrue(released['released'])
        self.assertEqual(released['models'], [FLUX2_KLEIN.repo])
        self.assertEqual(manager.resident_models(), [])
        self.assertEqual(memory_policy.status()['loaded_models'], [])

    def test_a_failed_job_never_leaves_a_model_behind(self):
        manager = self.manager(32 * GiB)
        job = self.job(manager, _fake_klein_module(self.counter, fail=True))
        self.assertEqual(job['state'], 'error')
        self.assertEqual(manager.resident_models(), [])

    def test_a_different_configuration_drops_the_resident_model_before_loading(self):
        manager = self.manager(32 * GiB)
        modules = _fake_klein_module(self.counter)
        self.job(manager, modules)
        manager.total_memory_bytes = 16 * GiB   # the lean plan: capped cache, bounded decode
        job = self.job(manager, modules)
        self.assertEqual(job['state'], 'done', job.get('error'))
        self.assertFalse(job['model_reused'], 'the resident model was built for a different plan')


if __name__ == '__main__':
    unittest.main()
