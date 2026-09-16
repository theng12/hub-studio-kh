"""Image's private API, pinned files, finalized PNG and restart contract."""
from __future__ import annotations

from hashlib import sha256
import io
import json
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
from components.image.app.backend.generation import GenerationManager, MODEL_ID, MODEL_REVISION
from components.image.app.backend.generation import validate_request
from components.image.app.backend.main import create_app


ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = json.loads((ROOT / 'model-manifests/flux2-klein-4b-mlx-4bit.json').read_text())
TOKEN = 'imageownercred_' * 3
RUNTIME_ID = 'a' * 64


def tiny_manifest() -> tuple[dict, dict[str, bytes]]:
    manifest = json.loads(json.dumps(ORIGINAL))
    contents = {}
    for entry in manifest['files']:
        value = (entry['path'] + '\n').encode()
        contents[entry['source_path']] = value
        entry['size'] = len(value)
        entry['sha256'] = sha256(value).hexdigest()
    model_packages.validate_manifest(manifest)
    return manifest, contents


def fake_mflux(save_delay: float = 0.0, *, steps: int = 4, guidance_value: float = 1.0):
    names = ('mflux', 'mflux.models', 'mflux.models.flux2',
             'mflux.models.flux2.variants', 'mflux.models.flux2.variants.txt2img',
             'mflux.models.flux2.variants.txt2img.flux2_klein')
    modules = {name: types.ModuleType(name) for name in names}

    class Flux2Klein:
        def __init__(self, *, model_path, quantize, lora_paths, lora_scales):
            assert Path(model_path).is_dir()
            assert (Path(model_path) / 'text_encoder/0.safetensors').is_file()
            assert quantize is None and lora_paths is None and lora_scales is None

        def generate_image(self, *, seed, prompt, num_inference_steps, height, width, guidance):
            assert num_inference_steps == steps and guidance == guidance_value
            assert isinstance(seed, int) and prompt

            class Result:
                def save(self, path, overwrite):
                    assert overwrite
                    if save_delay:
                        time.sleep(save_delay)
                    Image.new('RGB', (width, height), (17, 31, 47)).save(path, format='PNG')
            return Result()

    modules[names[-1]].Flux2Klein = Flux2Klein
    return modules


class ImageStandaloneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
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
        self.manager = GenerationManager(self.data, self.library, self.manifest, RUNTIME_ID)
        self.client = TestClient(create_app(self.manager, TOKEN))
        self.headers = {'X-Studio-Token': TOKEN}

    def request(self, **changes):
        return {'repo': MODEL_ID, 'model_revision': MODEL_REVISION, 'prompt': 'A river at dawn',
                'aspect_ratio': '1:1', 'width': 1024, 'height': 1024,
                'steps': 4, 'guidance': 1.0, 'seed': -1, **changes}

    def wait(self, job_id):
        for _ in range(200):
            job = self.client.get(f'/api/generate/jobs/{job_id}', headers=self.headers).json()['job']
            if job['state'] in ('done', 'error', 'cancelled'):
                return job
            time.sleep(0.02)
        self.fail('Image job did not reach terminal state')

    def test_auth_readiness_and_missing_text_encoder_blocks_dispatch(self):
        self.assertEqual(self.client.get('/api/catalog').status_code, 401)
        self.assertEqual(self.client.get('/api/health').status_code, 401)
        with patch('components.image.app.backend.main._host_eligibility',
                   return_value={'eligible': True, 'macos': '26.6.2'}):
            catalog = self.client.get('/api/catalog', headers=self.headers).json()['models'][0]
            self.assertEqual(catalog['cache']['state'], 'cached')
            self.assertTrue(catalog['execution_ready'])
            self.assertEqual(catalog['generation_defaults']['steps'], 4)
            self.assertEqual(catalog['generation_limits']['max_steps'], 100)
            (self.folder / 'model/text_encoder/0.safetensors').unlink()
            catalog = self.client.get('/api/catalog', headers=self.headers).json()['models'][0]
            self.assertFalse(catalog['execution_ready'])
            self.assertEqual(catalog['cache']['state'], 'invalid')
            response = self.client.post('/api/generate/txt2img', json=self.request(),
                                        headers=self.headers)
            self.assertEqual(response.status_code, 409)
            self.assertIn('MODEL_FILES_UNAVAILABLE', response.json()['detail'])
            self.assertEqual(self.manager.list(), [])

    def test_png_evidence_ack_and_restart_identity(self):
        with (patch.dict(sys.modules, fake_mflux()),
              patch('components.image.app.backend.main._host_eligibility',
                    return_value={'eligible': True, 'macos': '26.6.2'})):
            response = self.client.post('/api/generate/txt2img', json=self.request(),
                                        headers=self.headers)
            self.assertEqual(response.status_code, 200, response.text)
            job_id = response.json()['job']['id']
            job = self.wait(job_id)
            self.assertEqual(job['state'], 'done', job.get('error'))
            self.assertEqual((job['width'], job['height']), (1024, 1024))
            self.assertEqual(job['model_revision'], MODEL_REVISION)
            self.assertEqual(job['runtime_revision'], RUNTIME_ID)
            self.assertGreater(job['duration_seconds'], 0)
            artifact = self.client.get(f'/api/generate/jobs/{job_id}/image', headers=self.headers)
            self.assertEqual(artifact.status_code, 200)
            self.assertEqual(artifact.headers['content-type'], 'image/png')
            self.assertEqual(job['bytes'], len(artifact.content))
            self.assertEqual(job['sha256'], sha256(artifact.content).hexdigest())
            self.assertEqual(artifact.headers['x-content-sha256'], job['sha256'])
            for _ in range(2):
                ack = self.client.post(f'/api/generate/jobs/{job_id}/ack', headers=self.headers)
                self.assertEqual(ack.status_code, 200)
                self.assertTrue(ack.json()['ok'])
            self.assertTrue(self.manager.get(job_id)['acknowledged'])
            restarted = GenerationManager(self.data, self.library, self.manifest, RUNTIME_ID)
            self.assertEqual(restarted.get(job_id)['state'], 'done')
            self.assertEqual(restarted.get(job_id)['sha256'], job['sha256'])
            self.assertTrue(restarted.get(job_id)['acknowledged'])
            path = self.manager.outputs / f'{job_id}.png'
            content = bytearray(path.read_bytes())
            content[-1] ^= 1
            path.write_bytes(content)
            self.assertEqual(self.client.get(f'/api/generate/jobs/{job_id}/image',
                             headers=self.headers).status_code, 409)
            self.assertEqual(GenerationManager(self.data, self.library, self.manifest,
                             RUNTIME_ID).get(job_id)['state'], 'error')

    def test_cancelled_generation_never_publishes_final_png(self):
        with (patch.dict(sys.modules, fake_mflux(save_delay=0.1)),
              patch('components.image.app.backend.main._host_eligibility',
                    return_value={'eligible': True, 'macos': '26.6.2'})):
            job_id = self.client.post('/api/generate/txt2img', json=self.request(),
                                      headers=self.headers).json()['job']['id']
            response = self.client.delete(f'/api/generate/jobs/{job_id}', headers=self.headers)
            self.assertEqual(response.status_code, 200)
            job = self.wait(job_id)
            self.assertEqual(job['state'], 'cancelled')
            self.assertFalse((self.manager.outputs / f'{job_id}.png').exists())

    def test_native_style_dimensions_infer_preset_and_editable_controls_reach_mflux(self):
        with (patch.dict(sys.modules, fake_mflux(steps=6, guidance_value=2.5)),
              patch('components.image.app.backend.main._host_eligibility',
                    return_value={'eligible': True, 'macos': '26.6.2'})):
            native = {'repo': MODEL_ID, 'prompt': 'A river at dawn', 'width': 1024,
                      'height': 1024, 'steps': 6, 'guidance': 2.5, 'seed': 42}
            response = self.client.post('/api/generate/txt2img', json=native,
                                        headers=self.headers)
            self.assertEqual(response.status_code, 200, response.text)
            job = self.wait(response.json()['job']['id'])
            self.assertEqual(job['state'], 'done', job.get('error'))
            self.assertEqual(job['params']['aspect_ratio'], '1:1')
            self.assertEqual((job['steps'], job['resolved_seed']), (6, 42))

    def test_ack_persistence_failure_remains_retryable(self):
        with (patch.dict(sys.modules, fake_mflux()),
              patch('components.image.app.backend.main._host_eligibility',
                    return_value={'eligible': True, 'macos': '26.6.2'})):
            job_id = self.client.post('/api/generate/txt2img', json=self.request(),
                                      headers=self.headers).json()['job']['id']
            self.assertEqual(self.wait(job_id)['state'], 'done')
            with patch.object(self.manager, '_persist', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    self.manager.ack(job_id)
            self.assertFalse(self.manager.get(job_id)['acknowledged'])
            self.assertNotIn('receipt_acked_at', self.manager.get(job_id))
            self.assertTrue(self.manager.ack(job_id)['ok'])
            self.assertTrue(GenerationManager(self.data, self.library, self.manifest,
                            RUNTIME_ID).get(job_id)['acknowledged'])

    def test_failed_final_persist_discards_png_and_failed_submit_is_not_accepted(self):
        with patch.object(self.manager, '_persist', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.manager.submit(self.request())
        self.assertEqual(self.manager.list(), [])
        original_persist = self.manager._persist
        failed = False

        def fail_once_after_publish():
            nonlocal failed
            if not failed and any(job['state'] == 'done' for job in self.manager.jobs.values()):
                failed = True
                raise OSError('disk full at finalization')
            original_persist()

        with (patch.dict(sys.modules, fake_mflux()),
              patch.object(self.manager, '_persist', side_effect=fail_once_after_publish)):
            job_id = self.manager.submit(self.request())['id']
            for _ in range(200):
                job = self.manager.get(job_id)
                if job['state'] in ('done', 'error', 'cancelled'):
                    break
                time.sleep(0.02)
            self.assertEqual(job['state'], 'error')
            self.assertTrue(failed)
            self.assertEqual(job['error_code'], 'JOB_PERSISTENCE_FAILED')
            self.assertFalse((self.manager.outputs / f'{job_id}.png').exists())

    def test_running_receipt_failure_stops_before_inference(self):
        original_persist = self.manager._persist
        failed = False

        def fail_running_once():
            nonlocal failed
            if not failed and any(job['state'] == 'running' for job in self.manager.jobs.values()):
                failed = True
                raise OSError('disk full at running receipt')
            original_persist()

        with patch.object(self.manager, '_persist', side_effect=fail_running_once):
            job_id = self.manager.submit(self.request())['id']
            job = self.wait(job_id)
        self.assertTrue(failed)
        self.assertEqual(job['state'], 'error')
        self.assertEqual(job['error_code'], 'JOB_PERSISTENCE_FAILED')
        self.assertFalse((self.manager.outputs / f'{job_id}.png').exists())
        self.assertEqual(GenerationManager(self.data, self.library, self.manifest,
                         RUNTIME_ID).get(job_id)['error_code'], 'JOB_PERSISTENCE_FAILED')

    def test_restart_with_unreceipted_final_png_marks_uncertain(self):
        job_id = 'a' * 32
        path = self.manager.outputs / f'{job_id}.png'
        Image.new('RGB', (16, 16), (1, 2, 3)).save(path, format='PNG')
        self.manager.jobs[job_id] = {'id': job_id, 'state': 'running',
                                     'created_at': time.time(), 'output_path': None}
        self.manager._persist()
        restarted = GenerationManager(self.data, self.library, self.manifest, RUNTIME_ID)
        self.assertEqual(restarted.get(job_id)['error_code'], 'JOB_PERSISTENCE_UNCERTAIN')
        self.assertTrue(path.exists())

    def test_a_small_mac_is_warned_about_not_refused(self):
        """A model's published RAM floor is a tier, not a wall.

        FLUX.2 Klein is qualified at 16 GB and has run tens of thousands of images
        on the owner's 8 GB Macs. The worker must not refuse those machines: it
        reports the tier, and the Hub's live free-memory guard is what protects the
        Mac at the moment a model loads.
        """
        from components.image.app.backend import main as image_main

        def sysctl(_command, **_kwargs):
            return str(8 * 1024 ** 3)

        with patch.object(image_main.subprocess, 'check_output', sysctl):
            host = image_main._host_eligibility()
        self.assertTrue(host['eligible'], host)
        self.assertIsNone(host['reason'] if 'reason' in host else None)
        self.assertIn('8 GB of unified memory', host['memory_warning'])
        self.assertIn('qualified on 16 GB', host['memory_warning'])
        # The catalog reports the same thing, so the app can show it beside the model.
        with patch.object(image_main.subprocess, 'check_output', sysctl):
            model = self.client.get('/api/catalog', headers=self.headers).json()['models'][0]
        self.assertTrue(model['memory_eligible'])
        self.assertEqual(model['qualified_minimum_memory_gb'], 16)
        self.assertIn('qualified on 16 GB', model['memory_warning'])
        self.assertIsNone(model['readiness']['reason'], model['readiness'])
        self.assertTrue(model['readiness']['ready'], model['readiness'])

    def test_a_mac_at_the_qualified_tier_gets_no_warning(self):
        from components.image.app.backend import main as image_main

        def sysctl(_command, **_kwargs):
            return str(16 * 1024 ** 3)

        with patch.object(image_main.subprocess, 'check_output', sysctl):
            host = image_main._host_eligibility()
        self.assertTrue(host['eligible'])
        self.assertIsNone(host['memory_warning'])

    def test_a_mac_that_cannot_run_at_all_is_still_refused(self):
        """Capability is not memory: this is what the refusals are for."""
        from components.image.app.backend import main as image_main

        with patch.object(image_main.platform, 'system', lambda: 'Linux'):
            self.assertFalse(image_main._host_eligibility()['eligible'])

        def old_macos(_command, **_kwargs):
            return str(16 * 1024 ** 3)

        with patch.object(image_main.subprocess, 'check_output', old_macos), \
             patch.object(image_main.platform, 'mac_ver', lambda: '13.6'):
            host = image_main._host_eligibility()
        self.assertFalse(host['eligible'])
        self.assertIn('macOS 14', host['reason'])

    def test_memory_policy_publishes_the_fleet_contract(self):
        """Image holds no model between jobs, but the Hub still inventories it.

        The Hub's machine-wide memory inventory and its handoff release pass read every
        Studio through `GET/PUT /api/memory-policy` + `POST /api/memory/release`. Without
        these the app reported Image as "update required" and counted it as a failed
        release, even though Image is the one Studio that always frees its model.
        """
        policy = self.client.get('/api/memory-policy', headers=self.headers)
        self.assertEqual(policy.status_code, 200)
        body = policy.json()
        self.assertEqual(body['mode'], 'immediate')
        self.assertTrue(body['fixed'])
        self.assertEqual(body['loaded_models'], [])
        self.assertFalse(body['resident'])
        self.assertFalse(body['busy'])
        self.assertEqual([row['mode'] for row in body['options']], ['immediate'])

        # There is no idle timer to configure, so the honest answer is a refusal.
        rejected = self.client.put('/api/memory-policy', headers=self.headers,
                                   json={'mode': 'balanced'})
        self.assertEqual(rejected.status_code, 422)
        accepted = self.client.put('/api/memory-policy', headers=self.headers,
                                   json={'mode': 'immediate'})
        self.assertEqual(accepted.status_code, 200)

    def test_memory_release_never_reports_a_running_generation_as_freed(self):
        idle = self.client.post('/api/memory/release', headers=self.headers)
        self.assertEqual(idle.status_code, 200)
        self.assertFalse(idle.json()['released'])
        self.assertEqual(idle.json()['loaded_models'], [])

        self.manager.jobs['b' * 32] = {'id': 'b' * 32, 'state': 'running',
                                       'created_at': time.time()}
        active = self.client.get('/api/memory-policy', headers=self.headers).json()
        self.assertEqual(active['loaded_models'], [[MODEL_ID]])
        self.assertTrue(active['busy'])
        refused = self.client.post('/api/memory/release', headers=self.headers)
        self.assertEqual(refused.status_code, 409)

    def test_invalid_controls_and_unqualified_modes(self):
        with self.assertRaises(ValueError):
            validate_request(self.request(guidance=float('inf')))
        with patch('components.image.app.backend.main._host_eligibility',
                   return_value={'eligible': True, 'macos': '26.6.2'}):
            for changes in ({'repo': 'other/model'}, {'resolution': '2K'},
                            {'steps': 101},
                            {'negative_prompt': 'blurry'}, {'width': 2048}):
                response = self.client.post('/api/generate/txt2img', json=self.request(**changes),
                                            headers=self.headers)
                self.assertEqual(response.status_code, 422, changes)


if __name__ == '__main__':
    unittest.main()
