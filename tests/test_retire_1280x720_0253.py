"""Release 0.6.253: FLUX.2 Klein retires 1280x720 without moving any other family."""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from components import model_packages
from components.image.app.backend.generation import (
    FLUX2_KLEIN, MODEL_ID, MODELS, SIZE_PRESETS, Z_IMAGE_TURBO,
    GenerationManager, lane_parameters, size_presets, validate_request)
from components.image.app.backend.main import create_app
from tests.test_image_standalone import (HOST_BYTES, RUNTIME_ID, TOKEN, fake_mflux,
                                         tiny_manifest)


ROOT = Path(__file__).resolve().parents[1]
REFUSAL = ('The 1280x720 (16:9) size has been retired for FLUX.2 Klein; '
           'choose 1024x576 (16:9 small) or 1344x768 (7:4) instead.')


class PresetTests(unittest.TestCase):
    def test_only_the_retired_ratio_leaves_the_flux_offer(self):
        flux = size_presets(FLUX2_KLEIN)
        self.assertNotIn('16:9', flux)
        self.assertEqual(flux, {key: pixels for key, pixels in SIZE_PRESETS.items()
                                if key != '16:9'})
        self.assertEqual(len(flux), len(SIZE_PRESETS) - 1)
        self.assertEqual(flux['7:4'], (1344, 768))
        enum = next(p['enum'] for p in lane_parameters(FLUX2_KLEIN)
                    if p['name'] == 'aspect_ratio')
        self.assertEqual(set(enum), set(flux))

    def test_fresh_default_and_other_families(self):
        params = validate_request({'repo': MODEL_ID, 'prompt': 'river'})
        self.assertEqual(params['aspect_ratio'], '16:9 small')
        self.assertEqual((params['width'], params['height']), (1024, 576))
        self.assertEqual(size_presets(Z_IMAGE_TURBO), SIZE_PRESETS)
        self.assertEqual(Z_IMAGE_TURBO.defaults['aspect_ratio'], '16:9')
        z_params = validate_request({'repo': Z_IMAGE_TURBO.repo, 'prompt': 'river'})
        self.assertEqual((z_params['width'], z_params['height']), (1280, 720))
        for model in MODELS.values():
            if model not in (FLUX2_KLEIN, Z_IMAGE_TURBO):
                self.assertIn('16:9', size_presets(model), model.repo)

    def test_unknown_ratio_keeps_the_generic_refusal(self):
        with self.assertRaises(ValueError) as refused:
            validate_request({'repo': MODEL_ID, 'prompt': 'river', 'aspect_ratio': '9:10'})
        self.assertEqual(str(refused.exception), 'Choose a qualified 1K aspect ratio')

    def test_stored_1280x720_params_revalidate_as_a_custom_canvas(self):
        stored = {'repo': MODEL_ID, 'prompt': 'river', 'aspect_ratio': '16:9',
                  'width': 1280, 'height': 720, 'resolution': '1K',
                  'steps': 4, 'guidance': 1.0, 'seed': -1}
        replayed = validate_request(stored)
        self.assertEqual((replayed['width'], replayed['height']), (1280, 720))
        self.assertEqual(replayed['aspect_ratio'], 'custom')


class CallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
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
        eligibility = patch('components.image.app.backend.main._host_eligibility',
                            return_value={'eligible': True, 'macos': '26.6.2'})
        eligibility.start()
        self.addCleanup(eligibility.stop)
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

    def wait(self, job_id):
        for _ in range(300):
            job = self.client.get(f'/api/generate/jobs/{job_id}',
                                  headers=self.headers).json()['job']
            if job['state'] in ('done', 'error', 'cancelled'):
                return job
            time.sleep(0.02)
        self.fail('Image job did not reach a terminal state')

    def test_retired_ratio_refusal_reaches_the_caller_as_a_sentence(self):
        with patch.dict(sys.modules, fake_mflux()):
            response = self.client.post('/api/generate/txt2img',
                json={'repo': MODEL_ID, 'prompt': 'river', 'aspect_ratio': '16:9'},
                headers=self.headers)
        self.assertEqual(response.status_code, 422, response.text)
        sentence = response.json()['detail']
        self.assertEqual(sentence, REFUSAL)
        self.assertIn('retired', sentence)
        self.assertIn('1024x576', sentence)
        self.assertIn('1344x768', sentence)

    def test_catalog_offer_and_default_move_with_the_row(self):
        models = self.client.get('/api/catalog', headers=self.headers).json()['models']
        flux = next(model for model in models if model['repo'] == MODEL_ID)
        self.assertNotIn('16:9', {size['id'] for size in flux['image_sizes']})
        self.assertEqual((flux['generation_defaults']['width'],
                          flux['generation_defaults']['height']), (1024, 576))

    def test_replayed_1280x720_job_still_renders(self):
        with patch.dict(sys.modules, fake_mflux()):
            response = self.client.post('/api/generate/txt2img',
                json={'repo': MODEL_ID, 'prompt': 'river', 'aspect_ratio': '16:9',
                      'width': 1280, 'height': 720, 'steps': 4, 'guidance': 1.0},
                headers=self.headers)
            self.assertEqual(response.status_code, 200, response.text)
            job = self.wait(response.json()['job']['id'])
        self.assertEqual(job['state'], 'done', job.get('error'))
        self.assertEqual((job['render_width'], job['render_height']), (1280, 720))


if __name__ == '__main__':
    unittest.main()
