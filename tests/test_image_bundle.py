"""The Image wrapper loads shared process code under actual Python -I isolation."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
import unittest

import httpx


ROOT = Path(__file__).resolve().parents[1]
BUILD_PYTHON = ROOT / '.build/voice-runtime/bin/python3'
IMAGE_RUNTIME = ROOT / '.build/st03-image-runtime-worker'


class ImageBundleTests(unittest.TestCase):
    def test_isolated_wrapper_check_uses_private_external_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            resources = root / 'Bundle.app/Contents/Resources'
            resources.mkdir(parents=True)
            for name in ('image_worker.py', 'voice_worker.py'):
                shutil.copyfile(ROOT / 'packaging' / name, resources / name)
            source = resources / 'components/image/app/backend'
            source.mkdir(parents=True)
            (source / 'main.py').write_text('# tracked source fixture\n')
            catalog = resources / 'model-manifests'
            catalog.mkdir()
            shutil.copyfile(ROOT / 'model-manifests/flux2-klein-4b-mlx-4bit.json',
                            catalog / 'flux2-klein-4b-mlx-4bit.json')
            runtime = resources / 'runtimes/image/bin'
            runtime.mkdir(parents=True)
            (runtime / 'python3').symlink_to(BUILD_PYTHON)
            (resources / 'release-manifest.json').write_text(json.dumps({
                'schema': 'studiohub.standalone-release', 'schema_version': 1,
                'version': 'fixture',
                'components': {'image': {'runtime_sha256': 'a'*64}},
            }))
            data = root / 'External State'
            models = root / 'External Models'
            data.mkdir()
            models.mkdir()
            token = data / 'hub' / '.hub_token'
            token.parent.mkdir()
            token.write_text('image_owner_token_' * 3)
            token.chmod(0o600)
            command = [str(BUILD_PYTHON), '-I', '-B', str(resources / 'image_worker.py'),
                       '--data-dir', str(data), '--models-dir', str(models),
                       '--ready-file', str(data / 'image-ready.json'),
                       '--hub-token-file', str(token), '--check']
            result = subprocess.run(command, capture_output=True, text=True,
                                    env={'PATH': '/usr/bin:/bin', 'LANG': 'en_US.UTF-8'},
                                    timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload['runtime_sha256'], 'a'*64)
            # Not offline unless asked: this used to report offline unconditionally while the
            # worker forced it on, so turning the setting off in the app changed nothing here.
            self.assertFalse(payload['offline'], payload)
            offline = subprocess.run(command + ['--offline'], capture_output=True, text=True,
                                     env={'PATH': '/usr/bin:/bin', 'LANG': 'en_US.UTF-8'},
                                     timeout=20)
            self.assertEqual(offline.returncode, 0, offline.stderr)
            self.assertTrue(json.loads(offline.stdout)['offline'],
                            'asking for offline must actually be offline')
            self.assertEqual(payload['models'], str(models.resolve()))
            self.assertFalse((data / 'image-ready.json').exists())
            token.chmod(0o644)
            denied = subprocess.run(command, capture_output=True, text=True,
                                    env={'PATH': '/usr/bin:/bin'}, timeout=20)
            self.assertNotEqual(denied.returncode, 0)
            self.assertIn('owner-only', denied.stderr)

    @unittest.skipUnless((IMAGE_RUNTIME / 'bin/python3').is_file(),
                         'Build the pinned Image runtime for the process smoke')
    def test_isolated_private_server_requires_token_and_model_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            resources = root / 'Bundle.app/Contents/Resources'
            resources.mkdir(parents=True)
            for name in ('image_worker.py', 'voice_worker.py'):
                shutil.copyfile(ROOT / 'packaging' / name, resources / name)
            shutil.copytree(ROOT / 'components/image', resources / 'components/image',
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            # Every shared top-level module, discovered the way the stager discovers them, so a
            # new one cannot be missing here while the real bundle has it - or the reverse.
            (resources / 'components').mkdir(exist_ok=True)
            for shared in sorted((ROOT / 'components').glob('*.py')):
                shutil.copyfile(shared, resources / 'components' / shared.name)
            catalog = resources / 'model-manifests'
            catalog.mkdir()
            shutil.copyfile(ROOT / 'model-manifests/flux2-klein-4b-mlx-4bit.json',
                            catalog / 'flux2-klein-4b-mlx-4bit.json')
            (resources / 'runtimes').mkdir()
            (resources / 'runtimes/image').symlink_to(IMAGE_RUNTIME)
            (resources / 'release-manifest.json').write_text(json.dumps({
                'schema': 'studiohub.standalone-release', 'schema_version': 1,
                'version': '0.0.0', 'development_build': True,
                'implementation_commit': 'a' * 40,
                'components': {'image': {'runtime_sha256': 'a'*64}},
            }))
            data = root / 'External State'
            models = root / 'External Models'
            data.mkdir()
            models.mkdir()
            token = data / 'hub' / '.hub_token'
            token.parent.mkdir()
            token.write_text('image_owner_token_' * 3)
            token.chmod(0o600)
            ready = data / 'image-ready.json'
            command = [str(IMAGE_RUNTIME / 'bin/python3'), '-I', '-B',
                       str(resources / 'image_worker.py'), '--data-dir', str(data),
                       '--models-dir', str(models), '--ready-file', str(ready),
                       '--hub-token-file', str(token), '--parent-pid', str(os.getpid())]
            log = root / 'image-worker.log'
            with log.open('w+') as output:
                process = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT,
                    cwd=root, env={'PATH': '/usr/bin:/bin', 'LANG': 'en_US.UTF-8'})
                try:
                    for _ in range(300):
                        if ready.is_file() or process.poll() is not None:
                            break
                        time.sleep(0.1)
                    self.assertTrue(ready.is_file(), log.read_text())
                    value = json.loads(ready.read_text())
                    self.assertEqual(value['pid'], process.pid)
                    with httpx.Client() as client:
                        self.assertEqual(client.get(value['url']+'/api/health').status_code, 401)
                        headers = {'X-Studio-Token': token.read_text()}
                        health = client.get(value['url']+'/api/health', headers=headers)
                        self.assertEqual(health.status_code, 200, health.text)
                        self.assertTrue(health.json()['ok'])
                        # The release the bundle was staged with, read from its own manifest. A
                        # worker that answers a non-version here makes every Mac look like the
                        # same build to whoever is reading `workers[].studio_version`.
                        self.assertEqual(health.json()['app_version'], '0.0.0')
                        self.assertTrue(health.json()['development_build'])
                        catalog = client.get(value['url']+'/api/catalog', headers=headers)
                        self.assertEqual(catalog.status_code, 200, catalog.text)
                        model = catalog.json()['models'][0]
                        self.assertEqual(model['cache']['state'], 'not_installed')
                        self.assertFalse(model['execution_ready'])
                        # The lane contract a scheduler reads instead of this worker's source.
                        contract = model['lane_contract']
                        self.assertEqual(contract['schema'], 'studiohub.lane-contract.v1')
                        self.assertEqual(contract['operation'], 'image.text_to_image')
                        self.assertEqual(contract['artifact']['media_type'], 'image/png')
                        self.assertFalse(contract['states']['uncertain_is_terminal'])
                        self.assertTrue(contract['states']['discard_supported'])
                        parameters = {item['name']: item for item in contract['parameters']}
                        self.assertEqual(parameters['prompt']['max_characters'],
                                         model['max_prompt_characters'])
                        self.assertEqual(parameters['steps']['maximum'],
                                         model['generation_limits']['max_steps'])
                        # A parameter the worker refuses must not be offered as a contract.
                        for refused in ('negative_prompt', 'reference_images', 'lora_names',
                                        'quantize'):
                            self.assertNotIn(refused, parameters)
                        refused = client.post(value['url']+'/api/generate/txt2img',
                            json={'repo': model['repo'], 'prompt': 'offline fixture'}, headers=headers)
                        self.assertEqual(refused.status_code, 409)
                finally:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=5)
                self.assertFalse(ready.exists(), log.read_text())


if __name__ == '__main__':
    unittest.main()

class ImageLaneContractTests(unittest.TestCase):
    """The contract a scheduler reads instead of this worker's source.

    Shape: `docs/lane-execution-contract.md`. The properties asserted are the ones that make it
    worth publishing — built from each model's own limits, offering only what the validator
    accepts, and publishing a runtime only where one was measured.
    """

    @classmethod
    def setUpClass(cls):
        from components.hub.backend.capabilities import OPERATION_BY_MODALITY
        from components.image.app.backend import generation
        cls.generation = generation
        cls.operation = OPERATION_BY_MODALITY['image']

    def test_every_registered_model_publishes_a_contract_from_its_own_limits(self):
        models = list(self.generation.MODELS.values())
        self.assertGreaterEqual(len(models), 2, 'the image registry lost a model')
        for model in models:
            with self.subTest(repo=model.repo):
                contract = self.generation.lane_contract(model)
                self.assertEqual(contract['schema'], 'studiohub.lane-contract.v1')
                self.assertEqual(contract['modality'], 'image')
                self.assertEqual(contract['operation'], self.operation)
                parameters = {item['name']: item for item in contract['parameters']}
                self.assertEqual(parameters['prompt']['max_characters'],
                                 model.limits['max_prompt'])
                self.assertEqual(parameters['steps']['minimum'], model.limits['min_steps'])
                self.assertEqual(parameters['steps']['maximum'], model.limits['max_steps'])
                self.assertEqual(parameters['steps']['default'], model.defaults['steps'])
                self.assertEqual(parameters['guidance']['minimum'], model.limits['min_guidance'])
                self.assertEqual(parameters['guidance']['maximum'], model.limits['max_guidance'])
                self.assertEqual(parameters['aspect_ratio']['default'],
                                 model.defaults['aspect_ratio'])
                self.assertEqual(sorted(parameters['aspect_ratio']['enum']),
                                 sorted(self.generation.size_presets(model)))
                # The two facts a scheduler gets wrong by default.
                self.assertNotIn(contract['states']['uncertain'], contract['states']['terminal'])
                self.assertFalse(contract['states']['uncertain_is_terminal'])
                self.assertTrue(contract['states']['discard_supported'])
                self.assertTrue(contract['cancel']['semantics'])

    def test_the_contract_offers_nothing_the_validator_refuses(self):
        """A parameter the lane rejects must not be advertised as part of its surface."""
        model = self.generation.FLUX2_KLEIN
        offered = {item['name'] for item in self.generation.lane_contract(model)['parameters']}
        for refused in ('reference_images', 'image_path', 'lora_names', 'lora_paths',
                        'negative_prompt', 'quantize'):
            with self.subTest(parameter=refused):
                with self.assertRaises(ValueError):
                    self.generation.validate_request(
                        {'repo': model.repo, 'prompt': 'x', refused: ['anything']})
                self.assertNotIn(refused, offered)
        # width/height are derived from the aspect ratio and refused unless they match it.
        self.assertNotIn('width', offered)
        self.assertNotIn('height', offered)

    def test_a_published_runtime_says_where_it_was_measured(self):
        for model in self.generation.MODELS.values():
            with self.subTest(repo=model.repo):
                runtime = self.generation.lane_contract(model).get('runtime_seconds')
                if runtime is None:
                    self.assertIsNone(model.measured_runtime)
                    continue
                self.assertEqual(runtime['evidence'], 'measured')
                self.assertTrue(runtime.get('measured_on'))
                self.assertIsInstance(runtime['measured'], list)
                self.assertTrue(runtime['measured'])
                for row in runtime['measured']:
                    self.assertGreater(row['seconds'], 0)

    def test_an_ignored_control_is_published_as_ignored(self):
        """Z-Image-Turbo runs without CFG; the contract must not imply guidance does anything."""
        note = next(item.get('note') for item
                    in self.generation.lane_contract(self.generation.Z_IMAGE_TURBO)['parameters']
                    if item['name'] == 'guidance')
        self.assertIn('ignored', note)
