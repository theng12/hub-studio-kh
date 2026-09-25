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
from components.image.app.backend.generation import (FLUX2_KLEIN, MODEL_ID, MODEL_REVISION,
                                                     MODELS, Z_IMAGE_TURBO, GenerationManager)
from components.image.app.backend.generation import validate_request
from components.image.app.backend.main import create_app


ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = json.loads((ROOT / 'model-manifests/flux2-klein-4b-mlx-4bit.json').read_text())
TOKEN = 'imageownercred_' * 3
RUNTIME_ID = 'a' * 64
# A fixed host size, so the memory policy a test observes never depends on the machine
# running the suite. 32 GiB keeps Z-Image on its default configuration, as on the 32 GB
# development Mac.
HOST_BYTES = 32 * 1024 ** 3


def tiny_manifest(source: dict | None = None) -> tuple[dict, dict[str, bytes]]:
    manifest = json.loads(json.dumps(source if source is not None else ORIGINAL))
    contents = {}
    for entry in manifest['files']:
        value = (entry['path'] + '\n').encode()
        contents[entry['source_path']] = value
        entry['size'] = len(value)
        entry['sha256'] = sha256(value).hexdigest()
    model_packages.validate_manifest(manifest)
    return manifest, contents


def z_image_manifest() -> dict:
    """The Z-Image package as the manifest job will describe it.

    The pack root is the model directory, so `source_path` is pack-relative and `path`
    carries the one `model/` prefix every installed Image package has.
    """
    files = []
    for relative in ('transformer/model.safetensors.index.json',
                     'tokenizer/tokenizer.json', 'vae/model.safetensors.index.json'):
        value = (relative + '\n').encode()
        files.append({'path': f'model/{relative}', 'source_path': relative,
                      'size': len(value), 'sha256': sha256(value).hexdigest(),
                      'urls': [f'https://huggingface.co/{Z_IMAGE_TURBO.repo}/resolve/'
                               f'{Z_IMAGE_TURBO.revision}/{relative}']})
    return {
        'schema_version': 1, 'package_id': Z_IMAGE_TURBO.package_id,
        'model_id': Z_IMAGE_TURBO.repo, 'revision': Z_IMAGE_TURBO.revision,
        'engine': 'image-mflux', 'minimum_macos': '14.0', 'files': files,
    }


def unregistered_image_manifest() -> dict:
    """An accepted ``image-mflux`` package that no ``ImageModel`` in this build serves."""
    manifest = z_image_manifest()
    manifest.update({'package_id': 'nano-banana-mlx-4bit',
                     'model_id': 'someone/nano-banana-mlx-4bit', 'revision': 'd' * 40})
    return model_packages.validate_manifest(manifest)


def fake_mflux(save_delay: float = 0.0, *, steps: int = 4, guidance_value: float = 1.0,
               z_steps: int | None = None, z_ignores_guidance: bool = True):
    """Stand-in for mflux 0.20.0, with both registered model classes.

    The FLUX fake keeps the original assertions; the Z-Image fake records every construction
    and call on the module so a test can prove which class a job actually loaded.
    """
    names = ('mflux', 'mflux.models', 'mflux.models.flux2',
             'mflux.models.flux2.variants', 'mflux.models.flux2.variants.txt2img',
             'mflux.models.flux2.variants.txt2img.flux2_klein',
             'mflux.models.z_image', 'mflux.models.z_image.variants',
             'mflux.models.z_image.variants.z_image')
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

    calls: list[dict] = []

    class ZImage:
        def __init__(self, *, model_path, quantize, lora_paths, lora_scales):
            # The installed package has exactly one model/ prefix for every publisher.
            assert Path(model_path).name == 'model'
            assert (Path(model_path) / 'transformer/model.safetensors.index.json').is_file()
            assert quantize is None and lora_paths is None and lora_scales is None
            calls.append({'event': 'init', 'model_path': str(model_path)})

        def generate_image(self, *, seed, prompt, num_inference_steps, height, width, guidance):
            if z_steps is not None:
                assert num_inference_steps == z_steps
            if z_ignores_guidance:
                # What mflux does: CFG is disabled, so the value must not reach the schedule.
                assert guidance is not None
            calls.append({'event': 'generate', 'seed': seed, 'steps': num_inference_steps,
                          'guidance': guidance, 'width': width, 'height': height,
                          'prompt': prompt})

            class Result:
                def save(self, path, overwrite):
                    assert overwrite
                    if save_delay:
                        time.sleep(save_delay)
                    Image.new('RGB', (width, height), (47, 31, 17)).save(path, format='PNG')
            return Result()

    modules[names[5]].Flux2Klein = Flux2Klein
    modules[names[-1]].ZImage = ZImage
    modules[names[-1]].calls = calls
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
        self.z_manifest, z_contents = tiny_manifest(z_image_manifest())
        source = root / 'source'
        for relative, content in contents.items():
            target = source / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        self.folder = model_packages.import_folder(self.manifest, source, self.library)
        z_source = root / 'z-source'
        for relative, content in z_contents.items():
            target = z_source / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        self.z_folder = model_packages.import_folder(self.z_manifest, z_source, self.library)
        self.manager = self.manager_for()
        self.client = TestClient(create_app(self.manager, TOKEN))
        self.headers = {'X-Studio-Token': TOKEN}

    def manager_for(self, **kwargs):
        """A manager over both installed packages, with a fixed host size."""
        return GenerationManager(self.data, self.library, self.manifest, RUNTIME_ID,
                                 manifests=[self.manifest, self.z_manifest],
                                 total_memory_bytes=HOST_BYTES, **kwargs)

    def request(self, **changes):
        return {'repo': MODEL_ID, 'model_revision': MODEL_REVISION, 'prompt': 'A river at dawn',
                'aspect_ratio': '1:1', 'width': 1024, 'height': 1024,
                'steps': 4, 'guidance': 1.0, 'seed': -1, **changes}

    def z_request(self, **changes):
        return {'repo': Z_IMAGE_TURBO.repo, 'prompt': 'A river at dawn',
                'aspect_ratio': '16:9', 'width': 1280, 'height': 720, 'seed': 42, **changes}

    def wait(self, job_id, client=None):
        client = client or self.client
        for _ in range(200):
            job = client.get(f'/api/generate/jobs/{job_id}', headers=self.headers).json()['job']
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
            # The file the receipt records is gone, so this is `not_installed` - the state whose
            # instruction is "put the file back or install the package again". Absence and
            # corruption used to share `invalid`, whose reason was `[Errno 2] No such file or
            # directory: 'model/text_encoder/0.safetensors'`. Dispatch stays blocked either way,
            # which is what this test is named for.
            self.assertEqual(catalog['cache']['state'], 'not_installed')
            reason = catalog['readiness']['files']['reason']
            self.assertIn('model/text_encoder/0.safetensors', reason)
            self.assertNotIn('[Errno', reason)
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

    # ── the model registry ─────────────────────────────────────────────────────────────
    # One worker, two models. These pin the dispatch: the catalog lists each registered model
    # with its own numbers, a request is validated against the entry it names, and the job
    # loads that entry's class from that entry's installed package.

    def test_catalog_lists_every_registered_model_with_its_own_numbers(self):
        with patch('components.image.app.backend.main._host_eligibility',
                   return_value={'eligible': True, 'macos': '26.6.2'}):
            entries = self.client.get('/api/catalog', headers=self.headers).json()['models']
        listed = {entry['repo']: entry for entry in entries}
        self.assertEqual(set(listed), set(MODELS))
        flux, z_image = listed[MODEL_ID], listed[Z_IMAGE_TURBO.repo]

        # FLUX.2 Klein is unchanged: same label, family, defaults, limits and qualification.
        self.assertEqual(flux['label'], 'FLUX.2 Klein 4B MLX 4-bit')
        self.assertEqual(flux['family'], 'flux2-klein')
        self.assertEqual(flux['generation_defaults']['steps'], 4)
        self.assertEqual(flux['generation_defaults']['guidance'], 1.0)
        self.assertEqual((flux['generation_defaults']['width'],
                          flux['generation_defaults']['height']), (1280, 720))
        self.assertEqual(flux['generation_limits']['max_steps'], 100)
        self.assertEqual(flux['qualification'],
                         {'historical_source_workload':
                          '1K, four steps, guidance 1.0 on 16 GB Apple Silicon',
                          'packaged_inference': 'pending'})
        self.assertFalse(flux['guidance_ignored'])

        # Z-Image-Turbo is its own entry, with its own nine steps and its own pin.
        self.assertEqual(z_image['label'], 'Z-Image-Turbo 6B MLX 4-bit')
        self.assertEqual(z_image['family'], 'z-image-turbo')
        self.assertEqual(z_image['generation_defaults']['steps'], 9)
        self.assertEqual(z_image['model_revision'], Z_IMAGE_TURBO.revision)
        self.assertTrue(z_image['guidance_ignored'])
        self.assertEqual(z_image['generation_limits']['min_steps'], 2)
        self.assertEqual(z_image['max_prompt_characters'], 4000)
        # Both entries carry the geometry the picker needs, and both are dispatchable here.
        for entry in entries:
            self.assertEqual(len(entry['image_sizes']), 12)
            self.assertEqual(len(entry['sizes']), 12)
            self.assertTrue(entry['execution_ready'], entry['readiness'])
            self.assertEqual(entry['cache']['state'], 'cached')
        # Z-Image's qualification publishes what is measured and what is not.
        self.assertEqual(z_image['qualification']['fleet_8gb'].split(':')[0], 'pending')
        self.assertEqual(z_image['qualification']['measured_peak_gb']['low_ram'], 7.12)
        self.assertNotIn('fleet_8gb', flux['qualification'])

    def test_a_request_is_validated_against_the_entry_it_names(self):
        # Defaults come from the selected model, not from a module constant.
        self.assertEqual(validate_request(self.request())['steps'], 4)
        z_params = validate_request(self.z_request())
        self.assertEqual((z_params['repo'], z_params['steps'], z_params['model_revision']),
                         (Z_IMAGE_TURBO.repo, 9, Z_IMAGE_TURBO.revision))
        self.assertEqual((z_params['width'], z_params['height']), (1280, 720))
        # The pin still only accepts the registered revision for that repo.
        with self.assertRaises(ValueError):
            validate_request(self.z_request(model_revision='0' * 40))
        with self.assertRaises(ValueError):
            validate_request(self.request(repo=Z_IMAGE_TURBO.repo, model_revision=MODEL_REVISION))
        # And the FLUX answer is unchanged, explicitly requested or not.
        explicit = validate_request(self.request(model_revision=MODEL_REVISION))
        self.assertEqual(explicit['steps'], 4)
        self.assertEqual(explicit['guidance'], 1.0)

    def test_z_image_job_loads_its_own_class_and_records_its_own_revision(self):
        modules = fake_mflux(z_steps=9)
        with (patch.dict(sys.modules, modules),
              patch('components.image.app.backend.main._host_eligibility',
                    return_value={'eligible': True, 'macos': '26.6.2'})):
            response = self.client.post('/api/generate/txt2img', json=self.z_request(),
                                        headers=self.headers)
            self.assertEqual(response.status_code, 200, response.text)
            job = self.wait(response.json()['job']['id'])
        self.assertEqual(job['state'], 'done', job.get('error'))
        self.assertEqual(job['model_revision'], Z_IMAGE_TURBO.revision)
        self.assertEqual(job['params']['repo'], Z_IMAGE_TURBO.repo)
        self.assertEqual((job['width'], job['height']), (1280, 720))
        self.assertEqual(job['steps'], 9)
        self.assertEqual(job['resolved_seed'], 42)
        # The loaded class was the registered one, out of the installed package's model/ dir.
        calls = modules['mflux.models.z_image.variants.z_image'].calls
        self.assertEqual([call['event'] for call in calls], ['init', 'generate'])
        self.assertEqual(calls[1]['steps'], 9)
        self.assertEqual(calls[1]['guidance'], 1.0)   # passed through, then ignored by the model
        self.assertTrue(Path(calls[0]['model_path']).is_dir())

    def test_a_model_without_a_manifest_is_unavailable_without_blocking_the_other(self):
        """A build that ships only FLUX must not lose FLUX because Z-Image is registered."""
        manager = GenerationManager(self.data, self.library, self.manifest, RUNTIME_ID,
                                    total_memory_bytes=HOST_BYTES)
        client = TestClient(create_app(manager, TOKEN))
        with (patch.dict(sys.modules, fake_mflux(z_steps=9)),
              patch('components.image.app.backend.main._host_eligibility',
                    return_value={'eligible': True, 'macos': '26.6.2'})):
            entries = {entry['repo']: entry for entry
                       in client.get('/api/catalog', headers=self.headers).json()['models']}
            self.assertEqual(set(entries), set(MODELS))
            self.assertTrue(entries[MODEL_ID]['execution_ready'])
            self.assertFalse(entries[Z_IMAGE_TURBO.repo]['execution_ready'])
            self.assertIn('No model manifest', entries[Z_IMAGE_TURBO.repo]['readiness']['reason'])
            refused = client.post('/api/generate/txt2img', json=self.z_request(),
                                  headers=self.headers)
            self.assertEqual(refused.status_code, 409)
            self.assertIn('MODEL_FILES_UNAVAILABLE', refused.json()['detail'])
            accepted = client.post('/api/generate/txt2img', json=self.request(),
                                   headers=self.headers)
            self.assertEqual(accepted.status_code, 200, accepted.text)
            job = self.wait(accepted.json()['job']['id'], client=client)
            self.assertEqual(job['state'], 'done', job.get('error'))
            self.assertEqual(job['params']['repo'], MODEL_ID)

    def test_a_manifest_that_pins_another_revision_is_refused_for_that_model_only(self):
        drifted = json.loads(json.dumps(self.z_manifest))
        drifted['revision'] = 'c' * 40
        manager = GenerationManager(self.data, self.library, self.manifest, RUNTIME_ID,
                                    manifests=[self.manifest, drifted],
                                    total_memory_bytes=HOST_BYTES)
        files = manager.preflight(Z_IMAGE_TURBO.repo)
        self.assertEqual(files['state'], 'pin_mismatch')
        self.assertIn(Z_IMAGE_TURBO.revision, files['reason'])
        self.assertEqual(manager.preflight(MODEL_ID)['state'], 'files_verified')

    # ── manifests the registry does not serve ──────────────────────────────────────────
    # `load_image_manifests` accepts every image-mflux manifest the bundle ships, while the
    # catalog is enumerated from `MODELS`. The reverse of the case above — a manifest with no
    # registered model — used to be published by the Hub and simply absent from the picker,
    # with no error anywhere. It must be published with its reason instead.

    def test_a_manifest_with_no_runtime_model_is_published_with_its_reason(self):
        orphan = unregistered_image_manifest()
        manager = GenerationManager(self.data, self.library, self.manifest, RUNTIME_ID,
                                    manifests=[self.manifest, self.z_manifest, orphan],
                                    total_memory_bytes=HOST_BYTES)
        client = TestClient(create_app(manager, TOKEN))
        entries = {entry['repo']: entry for entry
                   in client.get('/api/catalog', headers=self.headers).json()['models']}
        self.assertIn(orphan['model_id'], entries,
                      'a shipped image package cannot be silently absent from the catalog')
        row = entries[orphan['model_id']]
        self.assertEqual(row['package_id'], orphan['package_id'])
        self.assertEqual(row['engine'], 'image-mflux')
        self.assertEqual(row['modality'], 'image')
        self.assertFalse(row['execution_ready'])
        self.assertFalse(row['available'])
        self.assertEqual(row['readiness']['state'], 'no_runtime_model')
        # The reason names the manifest file and the registry that must learn about it.
        self.assertIn(f'{orphan["package_id"]}.json', row['readiness']['reason'])
        self.assertIn('MODELS', row['readiness']['reason'])
        self.assertIn('generation.py', row['readiness']['reason'])
        # The reconciliation the catalog row is built from: exactly the unserved manifests.
        self.assertEqual(manager.packages_without_a_runtime_model(), [orphan])
        # The two registered models are untouched by the extra manifest: same set, still ready.
        self.assertEqual(set(entries), set(MODELS) | {orphan['model_id']})
        self.assertTrue(entries[MODEL_ID]['execution_ready'])
        self.assertTrue(entries[Z_IMAGE_TURBO.repo]['execution_ready'])
        # And a job for it is still refused with the registered models named.
        refused = client.post('/api/generate/txt2img',
                              json={'repo': orphan['model_id'], 'prompt': 'a river'},
                              headers=self.headers)
        self.assertEqual(refused.status_code, 422)
        self.assertIn(MODEL_ID, refused.json()['detail'])

    def test_every_shipped_image_manifest_reaches_the_catalog(self):
        """The invariant: ship an image-mflux manifest and the catalog carries it, no edit."""
        from components.image.app.backend.generation import load_image_manifests

        directory = ROOT / 'model-manifests'
        manifests, errors = load_image_manifests(
            directory, directory / 'flux2-klein-4b-mlx-4bit.json')
        self.assertEqual(errors, {})
        declared = {manifest['model_id']: manifest for manifest in manifests.values()}
        self.assertTrue(declared)
        manager = GenerationManager(self.data, self.library, self.manifest, RUNTIME_ID,
                                    manifests=manifests, total_memory_bytes=HOST_BYTES)
        client = TestClient(create_app(manager, TOKEN))
        entries = {entry['repo']: entry for entry
                   in client.get('/api/catalog', headers=self.headers).json()['models']}
        self.assertEqual(set(entries), set(MODELS) | set(declared))
        served = {model.package_id for model in MODELS.values()}
        for manifest in declared.values():
            row = entries[manifest['model_id']]
            self.assertEqual(row['package_id'], manifest['package_id'])
            if manifest['package_id'] not in served:
                # The direction this test exists for: a package with no runtime model is
                # published with its reason, never absent and never unexplained.
                self.assertFalse(row['execution_ready'])
                self.assertEqual(row['readiness']['state'], 'no_runtime_model')
                self.assertIn(f'{manifest["package_id"]}.json', row['readiness']['reason'])


if __name__ == '__main__':
    unittest.main()
