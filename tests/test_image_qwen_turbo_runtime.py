"""The Qwen Image 2.1 Turbo adapters against mflux 0.23.0's own Turbo support.

mflux 0.23.0 carries what the 0.20.0-era adapter had to supply: `Qwen21TurboScheduler` holds the
checkpoint's eight sigma nodes and `ModelConfig.qwen_image_21_turbo()` is what selects them, and
the checkpoint's weights are normalized by mflux's loader. So this file pins the parts the *row*
still owns — the right mflux class and model config for each lane, the recipe check that runs
before a model is constructed, the constraints the row publishes (eight steps, guidance 1.0, no
negative prompt, fixed reference strength), and the memory configuration each lane installs.

The old assertions here (weight renaming, the hand-written scheduler, the checkpoint layout
validator) were removed with the runtime bump: mflux owns those now, which is what the real runs
in `qualification/evidence/` exercised.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from components.image.app.backend import generation, qwen_lowram, qwen_turbo


SIGMAS = [1.0, 0.978453, 0.95418, 0.926626, 0.89508, 0.845148, 0.704534, 0.414568]


def fake_mflux(*, fail_load=False):
    """The smallest mflux 0.23.0 surface the adapters touch, recording what they did with it."""
    names = (
        "mlx", "mlx.core", "mflux", "mflux.callbacks",
        "mflux.callbacks.instances", "mflux.callbacks.instances.memory_saver",
        "mflux.models", "mflux.models.common", "mflux.models.common.config",
        "mflux.models.common.config.model_config", "mflux.models.common.vae",
        "mflux.models.common.vae.tiling_config", "mflux.models.qwen21",
        "mflux.models.qwen21.model", "mflux.models.qwen21.model.qwen21_scheduler",
        "mflux.models.qwen21.variants", "mflux.models.qwen21.variants.txt2img",
        "mflux.models.qwen21.variants.txt2img.qwen_image_21",
        "mflux.models.qwen21.variants.edit",
        "mflux.models.qwen21.variants.edit.qwen_image_21_edit",
        "mflux.models.qwen21.weights", "mflux.models.qwen21.weights.qwen21_weight_definition",
    )
    modules = {name: types.ModuleType(name) for name in names}
    observed: dict = {"constructors": [], "generate_calls": [], "cache_calls": []}

    class Component:
        def __init__(self, name, skip):
            self.name = name
            self.skip_quantization = skip

    class WeightDefinition:
        @staticmethod
        def get_components():
            return [Component("text_encoder", True), Component("transformer", False)]

    class Registry:
        def __init__(self):
            self.registered = []

        def register(self, callback):
            self.registered.append(callback)

    class TurboScheduler:
        SIGMA_NODES = tuple(SIGMAS)

    class TilingConfig:
        pass

    class MemorySaver:
        def __init__(self, **kwargs):
            observed['memory_saver'] = kwargs

    def make_model(class_name):
        class Model:
            def __init__(self, *, quantize=None, model_path=None, model_config=None, **_rest):
                if fail_load:
                    raise RuntimeError("fixture load failed")
                observed['constructors'].append({
                    'class': class_name, 'quantize': quantize, 'model_path': model_path,
                    'model_config': model_config,
                    'encoder_skip': WeightDefinition.get_components()[0].skip_quantization,
                })
                self.callbacks = Registry()
                self.prompt_cache = {}
                self.text_encoder = object()
                self.transformer = object()
                self.vae = object()
                self.tiling_config = None

            def generate_image(self, **kwargs):
                observed['generate_calls'].append(dict(kwargs))
                return 'image'

        return Model

    class TurboConfig:
        model_name = 'qwen-image-2.1-turbo'

    def turbo_config():
        observed['model_config_requested'] = True
        return TurboConfig()

    modules["mflux.models.qwen21.weights.qwen21_weight_definition"].Qwen21WeightDefinition = WeightDefinition
    modules["mflux.models.qwen21.variants.txt2img.qwen_image_21"].QwenImage21 = make_model('QwenImage21')
    modules["mflux.models.qwen21.variants.edit.qwen_image_21_edit"].QwenImage21Edit = make_model('QwenImage21Edit')
    modules["mflux.models.qwen21.model.qwen21_scheduler"].Qwen21TurboScheduler = TurboScheduler
    modules["mflux.models.common.config.model_config"].ModelConfig = types.SimpleNamespace(
        qwen_image_21_turbo=staticmethod(turbo_config))
    modules["mflux.models.common.vae.tiling_config"].TilingConfig = TilingConfig
    modules["mflux.callbacks.instances.memory_saver"].MemorySaver = MemorySaver

    def set_cache_limit(value):
        observed['cache_calls'].append(value)
        return 1

    modules["mlx.core"].set_cache_limit = set_cache_limit
    modules["mlx.core"].get_peak_memory = lambda: 0
    modules["mlx.core"].clear_cache = lambda: None
    modules["mlx.core"].reset_peak_memory = lambda: None
    modules["mlx.core"].eval = lambda *_args: None
    return modules, observed


def write_checkpoint(root: Path, *, sigmas=SIGMAS, class_name='QwenImage21Pipeline') -> None:
    (root / 'model').mkdir(parents=True, exist_ok=True)
    (root / 'model' / 'model_index.json').write_text(json.dumps({
        '_class_name': class_name,
        'scheduler': ['diffusers', 'FlowMatchEulerDiscreteScheduler'],
        'sample_sigmas': list(sigmas),
    }), encoding='utf-8')


class TurboAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)

    @contextmanager
    def loaded(self, wrapper, *, fail_load=False, **checkpoint):
        """Construct `wrapper` against the fake mflux stack, with the patch still in force."""
        modules, observed = fake_mflux(fail_load=fail_load)
        root = Path(self.temporary.name)
        write_checkpoint(root, **checkpoint)
        with patch.dict(sys.modules, modules):
            yield wrapper(model_path=str(root / 'model')), observed

    def test_each_lane_loads_its_own_mflux_class_with_the_turbo_model_config(self):
        """The lane chooses the class; mflux's Turbo config selects the eight-node schedule."""
        with self.loaded(qwen_turbo.QwenTurbo) as (_instance, text_seen):
            self.assertEqual([entry['class'] for entry in text_seen['constructors']],
                             ['QwenImage21'])
            self.assertTrue(text_seen.get('model_config_requested'))
            entry = text_seen['constructors'][0]
            self.assertEqual(entry['model_config'].model_name, 'qwen-image-2.1-turbo')
            self.assertIsNone(entry['quantize'], 'the packaged q4 weights are used as they are')
        with self.loaded(qwen_turbo.QwenTurboEdit) as (_instance, edit_seen):
            self.assertEqual([entry['class'] for entry in edit_seen['constructors']],
                             ['QwenImage21Edit'])
            self.assertTrue(edit_seen.get('model_config_requested'))

    def test_the_text_encoders_pack_weights_are_used_and_the_global_patch_is_restored(self):
        """`skip_quantization` is flipped only for the load, then the class is put back.

        The class read here is the one the adapter patched: mflux's own weight definition, which
        the fake stack provides. (The gate environment has a real, older mflux installed, so the
        import must happen inside the patch.)
        """
        with self.loaded(qwen_turbo.QwenTurbo) as (_instance, observed):
            definition = sys.modules[
                'mflux.models.qwen21.weights.qwen21_weight_definition'].Qwen21WeightDefinition
            self.assertFalse(observed['constructors'][0]['encoder_skip'])
            self.assertTrue(definition.get_components()[0].skip_quantization)

    def test_a_load_failure_leaves_the_weight_definition_patch_restored(self):
        modules, _observed = fake_mflux(fail_load=True)
        root = Path(self.temporary.name)
        write_checkpoint(root)
        with patch.dict(sys.modules, modules):
            with self.assertRaises(RuntimeError):
                qwen_turbo.QwenTurbo(model_path=str(root / 'model'))
            definition = modules[
                'mflux.models.qwen21.weights.qwen21_weight_definition'].Qwen21WeightDefinition
            self.assertTrue(definition.get_components()[0].skip_quantization)

    def test_a_checkpoint_that_is_not_the_turbo_recipe_is_refused_before_construction(self):
        modules, observed = fake_mflux()
        root = Path(self.temporary.name)
        write_checkpoint(root, sigmas=[1.0, 0.5, 0.0])
        with patch.dict(sys.modules, modules):
            with self.assertRaisesRegex(ValueError, 'Turbo sampling recipe'):
                qwen_turbo.QwenTurbo(model_path=str(root / 'model'))
        write_checkpoint(root, class_name='SomeOtherPipeline')
        with patch.dict(sys.modules, modules):
            with self.assertRaisesRegex(ValueError, 'not a Qwen-Image-2.1 pipeline'):
                qwen_turbo.QwenTurbo(model_path=str(root / 'model'))
        with tempfile.TemporaryDirectory() as empty, patch.dict(sys.modules, modules):
            with self.assertRaisesRegex(ValueError, 'no readable model_index.json'):
                qwen_turbo.QwenTurbo(model_path=empty)
        self.assertEqual(observed['constructors'], [], 'nothing was constructed')

    def test_invalid_steps_guidance_and_negative_prompt_are_rejected_before_generation(self):
        with self.loaded(qwen_turbo.QwenTurbo) as (instance, observed):
            with self.assertRaisesRegex(ValueError, 'exactly 8'):
                instance.generate_image(seed=1, prompt='p', num_inference_steps=7, guidance=1.0)
            with self.assertRaisesRegex(ValueError, 'guidance=1.0'):
                instance.generate_image(seed=1, prompt='p', num_inference_steps=8, guidance=2.0)
            with self.assertRaisesRegex(ValueError, 'negative prompt'):
                instance.generate_image(seed=1, prompt='p', num_inference_steps=8, guidance=1.0,
                                        negative_prompt='fog')
            self.assertEqual(observed['generate_calls'], [], 'mflux was never asked to generate')

    def test_the_edit_lane_publishes_a_fixed_strength_and_no_optional_inputs(self):
        with self.loaded(qwen_turbo.QwenTurboEdit) as (instance, observed):
            instance.generate_image(seed=1, prompt='p', num_inference_steps=8, guidance=1.0,
                                    image_paths=['a.png'])
            self.assertEqual(observed['generate_calls'][0]['strength'], 1.0)
            with self.assertRaisesRegex(ValueError, 'does not accept mask_image'):
                instance.generate_image(seed=1, prompt='p', num_inference_steps=8, guidance=1.0,
                                        image_paths=['a.png'], mask_image='m')

    def test_the_text_lane_ships_its_configuration_and_the_edit_lane_leaves_it_to_the_plan(self):
        """The row's text lane has one measured mode; the edit lane's tiers come from its plan."""
        with self.loaded(qwen_turbo.QwenTurbo) as (text, text_seen):
            self.assertEqual(type(text._model.tiling_config).__name__, 'TilingConfig')
            self.assertIsNotNone(text_seen.get('memory_saver'),
                                 'the shipped text configuration evicts and tiles')
        with self.loaded(qwen_turbo.QwenTurboEdit) as (edit, edit_seen):
            self.assertNotIn('memory_saver', edit_seen,
                             'a plan-managed lane installs nothing until apply_memory_plan runs')
            self.assertIs(edit.memory_target, edit._model,
                          'the plan must reach the mflux model, not the wrapper')

    def test_the_cache_limit_is_zeroed_for_the_run_and_restored_after(self):
        with self.loaded(qwen_turbo.QwenTurbo) as (instance, observed):
            instance.generate_image(seed=1, prompt='p', num_inference_steps=8, guidance=1.0)
            # Zero for the run, then the limit the process had before it (the fake starts at 1).
            self.assertEqual(observed['cache_calls'], [0.0, 1])


if __name__ == '__main__':
    unittest.main()
