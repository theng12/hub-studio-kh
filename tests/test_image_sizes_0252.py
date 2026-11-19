"""Image 0.6.252 sizes: one 7:4 preset, an honest custom-size door, and a recorded crop."""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from components import model_packages
from components.image.app.backend.generation import (
    MODEL_ID, MODELS, QWEN_IMAGE_21_LOW_RAM, QWEN_MFLUX_SIZE_PRESETS, QWEN_SIZE_PRESETS,
    SIZE_PRESETS, GenerationManager, centred_16x9_crop, size_presets, validate_request)
from components.image.app.backend.main import create_app
from tests.test_image_standalone import HOST_BYTES, RUNTIME_ID, TOKEN, tiny_manifest


MODEL_PATH = 'mflux.models.flux2.variants.txt2img.flux2_klein'


def fake_flux(saves: list[dict]) -> dict:
    """A stand-in that records the engine canvas and writes each row's y value as its colour."""
    names = ('mflux', 'mflux.models', 'mflux.models.flux2', 'mflux.models.flux2.variants',
             'mflux.models.flux2.variants.txt2img', MODEL_PATH)
    modules = {name: types.ModuleType(name) for name in names}

    class Flux2Klein:
        def __init__(self, *, model_path, quantize, lora_paths, lora_scales):
            self._cancel_event = None
            self._step_callback = None

        def set_cancel_event(self, event):
            self._cancel_event = event

        def set_step_callback(self, callback):
            self._step_callback = callback

        def generate_image(self, *, seed, prompt, num_inference_steps, height, width, guidance):
            saves.append({'width': width, 'height': height})

            class Result:
                def save(self, path, overwrite):
                    assert overwrite
                    rows = b''.join(bytes((17, y // 256, y % 256)) * width
                                    for y in range(height))
                    Image.frombytes('RGB', (width, height), rows).save(path, format='PNG')
            return Result()

    modules[MODEL_PATH].Flux2Klein = Flux2Klein
    return modules


class SizePresetTests(unittest.TestCase):
    def test_7x4_is_published_by_every_family_with_its_own_pixels(self):
        for table in (SIZE_PRESETS, QWEN_SIZE_PRESETS, QWEN_MFLUX_SIZE_PRESETS):
            self.assertEqual(table['7:4'], (1344, 768))
        for model in MODELS.values():
            self.assertEqual(size_presets(model)['7:4'], (1344, 768), model.repo)
        self.assertEqual(SIZE_PRESETS['16:9'], (1280, 720))

    def test_both_sizes_the_owner_named_are_published_by_every_image_model(self):
        """Owner, 2026-11-19: "add both for both models". Both = 1024x576 and 1344x768, and both
        models = every image model family, not only the two rows that share `SIZE_PRESETS`."""
        for model in MODELS.values():
            presets = size_presets(model)
            self.assertIn((1024, 576), presets.values(), model.repo)
            self.assertEqual(presets['7:4'], (1344, 768), model.repo)
            keys = list(presets)
            self.assertEqual(len(keys), len(set(keys)), model.repo)

    def test_the_existing_small_defaults_are_unchanged(self):
        self.assertEqual(SIZE_PRESETS['16:9 small'], (1024, 576))
        self.assertEqual(QWEN_MFLUX_SIZE_PRESETS['16:9'], (1024, 576))
        flux = validate_request({'repo': MODEL_ID, 'prompt': 'river',
                                 'aspect_ratio': '16:9 small'})
        qwen = validate_request({'repo': QWEN_IMAGE_21_LOW_RAM.repo, 'prompt': 'river',
                                 'aspect_ratio': '16:9'})
        self.assertEqual((flux['width'], flux['height']), (1024, 576))
        self.assertEqual((qwen['width'], qwen['height']), (1024, 576))

    def test_a_custom_dimension_outside_the_engine_rule_is_refused_by_value(self):
        raw = {'repo': MODEL_ID, 'prompt': 'river', 'width': 1344, 'height': 769}
        with self.assertRaises(ValueError) as refused:
            validate_request(raw)
        self.assertEqual(str(refused.exception),
                         'Height 769 must be a positive multiple of 32')

    def test_preset_dimensions_still_validate_and_resume(self):
        params = validate_request({'repo': MODEL_ID, 'prompt': 'river', 'aspect_ratio': '7:4'})
        self.assertEqual((params['width'], params['height']), (1344, 768))
        resumed = validate_request(params)
        self.assertEqual((resumed['width'], resumed['height']), (1344, 768))
        custom = validate_request({'repo': MODEL_ID, 'prompt': 'river',
                                   'width': 2048, 'height': 1152})
        self.assertEqual(custom['aspect_ratio'], 'custom')
        self.assertEqual((validate_request(custom)['width'],
                          validate_request(custom)['height']), (2048, 1152))

    def test_crop_geometry_is_integer_and_centred(self):
        self.assertIsNone(centred_16x9_crop((2048, 1152)))
        self.assertEqual(centred_16x9_crop((1344, 768)), {
            'source': [1344, 768], 'output': [1344, 756], 'offset': [0, 6]})
        self.assertEqual(centred_16x9_crop((2048, 1024)), {
            'source': [2048, 1024], 'output': [1808, 1017], 'offset': [120, 3]})


class CropJobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for name, value in (('_reclaimable_available_gb', 64.0),
                            ('_host_swap_used_mb', 0.0)):
            mocked = patch('components.image.app.backend.generation.' + name,
                           return_value=value)
            mocked.start()
            self.addCleanup(mocked.stop)
        pressure = patch('components.image.app.backend.generation._under_memory_pressure',
                         return_value=False)
        pressure.start()
        self.addCleanup(pressure.stop)
        root = Path(self.temp.name)
        self.library = root / 'models'
        self.library.mkdir()
        self.data = root / 'data' / 'image'
        self.data.mkdir(parents=True)
        self.manifest, contents = tiny_manifest()
        source = root / 'source'
        for relative, content in contents.items():
            target = source / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        self.folder = model_packages.import_folder(self.manifest, source, self.library)
        self.manager = GenerationManager(self.data, self.library, self.manifest, RUNTIME_ID,
                                         manifests=[self.manifest],
                                         total_memory_bytes=HOST_BYTES)
        self.client = TestClient(create_app(self.manager, TOKEN))
        self.headers = {'X-Studio-Token': TOKEN}
        self.saves: list[dict] = []

    def submit(self, **fields):
        raw = {'repo': MODEL_ID, 'prompt': 'A river at dawn', 'steps': 4, 'seed': 7,
               **fields}
        with (patch.dict(sys.modules, fake_flux(self.saves)),
              patch('components.image.app.backend.main._host_eligibility',
                    return_value={'eligible': True, 'macos': '26.6.2'})):
            response = self.client.post('/api/generate/txt2img', json=raw,
                                        headers=self.headers)
            self.assertEqual(response.status_code, 200, response.text)
            job_id = response.json()['job']['id']
            for _ in range(300):
                job = self.client.get(f'/api/generate/jobs/{job_id}',
                                      headers=self.headers).json()['job']
                if job['state'] in ('done', 'error', 'cancelled'):
                    return job
                time.sleep(0.02)
        self.fail('Image job did not reach a terminal state')

    def test_7x4_run_records_the_centre_crop_and_keeps_source_pixels(self):
        job = self.submit(aspect_ratio='7:4')
        self.assertEqual(job['state'], 'done', job.get('error'))
        self.assertEqual(self.saves, [{'width': 1344, 'height': 768}])
        self.assertEqual((job['render_width'], job['render_height']), (1344, 768))
        self.assertEqual(job['crop'], {'source': [1344, 768], 'output': [1344, 756],
                                       'offset': [0, 6]})
        self.assertEqual((job['width'], job['height']), (1344, 756))
        with Image.open(Path(job['output_path'])) as delivered:
            self.assertEqual(delivered.size, (1344, 756))
            # Row 0 of the crop is source row 6; pixel bytes were never resampled.
            self.assertEqual(delivered.getpixel((640, 0)), (17, 0, 6))
            self.assertEqual(delivered.getpixel((640, 1)), (17, 0, 7))

    def test_existing_presets_keep_their_published_pixels(self):
        job = self.submit(aspect_ratio='1:1')
        self.assertEqual(job['state'], 'done', job.get('error'))
        self.assertIsNone(job['crop'])
        self.assertEqual((job['width'], job['height']), (1024, 1024))

    def test_exact_16x9_custom_pixels_are_rendered_with_no_crop(self):
        job = self.submit(width=2048, height=1152)
        self.assertEqual(job['state'], 'done', job.get('error'))
        self.assertEqual(self.saves, [{'width': 2048, 'height': 1152}])
        self.assertIsNone(job['crop'])
        self.assertEqual((job['render_width'], job['render_height']), (2048, 1152))
        self.assertEqual((job['width'], job['height']), (2048, 1152))
        with Image.open(Path(job['output_path'])) as delivered:
            self.assertEqual(delivered.size, (2048, 1152))


class PublishedListsTest(unittest.TestCase):
    """The Gateway intersects the row's `image_sizes` with the aspect-ratio enum.

    A size in only one list is silently never published (confirmed live on Gateway 0.2.289), so
    both are asserted from the functions that build them, for both models.
    """

    def test_both_published_lists_carry_the_new_key(self):
        from components.image.app.backend import generation
        for model in (generation.FLUX2_KLEIN, generation.QWEN_IMAGE_21):
            row_sizes = set(generation.size_presets(model))          # the row's `image_sizes`
            enum = next(p['enum'] for p in generation.lane_parameters(model)
                        if p['name'] == 'aspect_ratio')              # `parameters.aspect_ratio.enum`
            with self.subTest(repo=model.repo):
                self.assertIn('7:4', row_sizes)
                self.assertIn('7:4', enum)
                self.assertEqual(generation.size_presets(model)['7:4'], (1344, 768))
                self.assertEqual(set(enum), row_sizes)


if __name__ == '__main__':
    unittest.main()
