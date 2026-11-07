"""The Hub's INTERNAL reference_policy snapshot, enforced by the Image worker.

Gateway ACK 2026-11-07: an absent field keeps the worker's existing local resolver; a present
field must describe this exact model, mode, destination node, physical tier and reference count,
or the request is refused by name. There is no fallback from a present policy, and the worker's
own engine ceiling may only lower the Hub cap. These tests use fixtures only: no model library,
no Hub, no network, no inference.
"""
from __future__ import annotations

import io
import json
from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from components.image.app.backend import generation
from components.image.app.backend.generation import (FLUX2_KLEIN, QWEN_IMAGE_21_TURBO,
                                                     GenerationManager, reference_max,
                                                     reference_tier, validate_request)
from components.image.app.backend.main import create_app

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'packaging'))
import hub_worker  # noqa: E402
import image_worker  # noqa: E402


MODEL = FLUX2_KLEIN
TOTAL = 32 * 1024 ** 3
NODE = 'node-a'
TOKEN = 'imageownercred_' * 3
RUNTIME = 'a' * 64
REVISION = 'b' * 64
ABSENT = object()


def png_bytes(size=(32, 32), colour=(20, 40, 60)) -> bytes:
    buffer = io.BytesIO()
    Image.new('RGB', size, colour).save(buffer, format='PNG')
    return buffer.getvalue()


def policy(*, count=None, allowed=True, maximum=4, swap=0.0, node=NODE,
           mode='edit', tier=None, revision=REVISION, model=MODEL, **overrides) -> dict:
    value = {
        'model': model.repo,
        'mode': mode,
        'node_id': node,
        'physical_tier': tier or reference_tier(TOTAL),
        'reference_count': count,
        'revision': revision,
        'effective': {'allowed': allowed, 'max_reference_images': maximum,
                      'swap_allowance_gb': swap},
    }
    value.update(overrides)
    return value


class PolicyValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.images = []
        for index in range(6):
            path = self.folder / f'{index}.png'
            path.write_bytes(png_bytes())
            self.images.append(str(path))

    def validate(self, count=1, value=ABSENT, destination=NODE, mode=None):
        raw = {'repo': MODEL.repo, 'prompt': 'x',
               'reference_images': self.images[:count]}
        if value is not ABSENT:
            raw['reference_policy'] = value
        return validate_request(raw, total_memory_bytes=TOTAL,
                                destination_node_id=destination, mode=mode)

    def refused(self, value, pattern, count=1, destination=NODE):
        with self.assertRaisesRegex(ValueError, pattern):
            self.validate(count=count, value=value, destination=destination)

    def test_absent_policy_keeps_the_existing_fallback(self):
        params = self.validate(value=ABSENT)
        self.assertEqual(params['reference_images'], self.images[:1])
        # Absence needs no destination identity; the old resolver still answers.
        self.validate(value=ABSENT, destination=None)
        txt2img = validate_request({'repo': MODEL.repo, 'prompt': 'x'},
                                   total_memory_bytes=TOTAL)
        self.assertEqual(txt2img['repo'], MODEL.repo)

    def test_present_null_or_malformed_refuses(self):
        for value in (None, [], 'x', 1, {}):
            self.refused(value, 'reference_policy')
        missing = policy()
        missing.pop('node_id')
        self.refused(missing, 'missing node_id')
        extra = policy()
        extra['other'] = True
        self.refused(extra, 'unknown other')
        effective = policy()
        effective['effective'].pop('allowed')
        self.refused(effective, 'missing allowed')
        extra_effective = policy()
        extra_effective['effective']['other'] = 1
        self.refused(extra_effective, 'unknown other')

    def test_generic_and_exact_count_identity(self):
        self.validate(value=policy(count=None), count=1)
        self.validate(value=policy(count=1), count=1)
        self.refused(policy(count=2), 'selects 2 reference images', count=1)
        for bad in (0, -1, True, '1', 1.0):
            self.refused(policy(count=bad), 'reference_count')

    def test_wrong_model_mode_node_or_tier_refuses(self):
        self.refused(policy(model=replace(MODEL, repo='other/model')), 'for model')
        self.refused(policy(mode='txt2img'), 'for mode')
        self.refused(policy(node='node-b'), 'for node')
        self.refused(policy(tier='16'), 'physical tier')

    def test_false_zero_and_null_refuse(self):
        self.refused(policy(allowed=False), 'refuses')
        self.refused(policy(allowed=None), 'must be true or false')
        self.refused(policy(maximum=0), 'between 1 and 0')
        self.refused(policy(maximum=None), 'non-negative integer')
        self.refused(policy(maximum=True), 'non-negative integer')
        self.refused(policy(maximum=-1), 'non-negative integer')
        self.refused(policy(swap=-1), 'swap_allowance_gb')
        self.refused(policy(swap='x'), 'swap_allowance_gb')
        self.refused(policy(swap=float('nan')), 'swap_allowance_gb')

    def test_intrinsic_ceiling_only_lowers_the_hub_cap(self):
        self.assertEqual(reference_max(MODEL), 4)
        self.validate(value=policy(maximum=99), count=4)
        self.refused(policy(maximum=99), 'between 1 and 4', count=5)
        self.refused(policy(maximum=2), 'between 1 and 2', count=3)

    def test_missing_destination_identity_refuses_a_present_policy(self):
        self.refused(policy(), 'no destination machine identity', destination=None)

    def test_revision_is_a_64_lower_hex_token(self):
        for bad in ('b' * 63, 'B' * 64, 'z' * 64, 7):
            self.refused(policy(revision=bad), '64 lowercase hex')
        # The state token is the Hub's; any well-formed value is accepted here.
        self.validate(value=policy(revision='c' * 64))


class EndpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.data = root / 'data' / 'image'
        self.data.mkdir(parents=True)
        self.manager = GenerationManager(
            self.data, root / 'library', {}, RUNTIME,
            total_memory_bytes=TOTAL, destination_node_id=NODE)
        self.manager.production_admission = lambda repo: {'allowed': True, 'reason': None}
        self.manager.preflight = lambda repo: {'state': 'files_verified',
                                               'folder': str(root / 'library')}
        self.manager._enqueue = lambda *args, **kwargs: None
        self.client = TestClient(create_app(self.manager, TOKEN))
        self.headers = {'X-Studio-Token': TOKEN}
        for target, value in (
                ('components.image.app.backend.main._host_eligibility',
                 {'eligible': True}),
                ('components.image.app.backend.main.model_host_facts',
                 {'eligible': True}),
                ('components.image.app.backend.generation._reclaimable_available_gb', 64.0),
                ('components.image.app.backend.generation._under_memory_pressure', False)):
            mocked = patch(target, return_value=value)
            mocked.start()
            self.addCleanup(mocked.stop)

    def edit(self, value):
        return self.client.post(
            '/api/generate/edit', headers=self.headers,
            data={'repo': MODEL.repo, 'prompt': 'x',
                  'reference_policy': json.dumps(value)},
            files=[('images', ('a.png', png_bytes(), 'image/png'))])

    def test_multipart_edit_accepts_a_matching_policy(self):
        response = self.edit(policy(count=1))
        self.assertEqual(response.status_code, 200, response.text)
        job = response.json()['job']
        self.assertEqual(job['mode'], 'edit')
        self.assertEqual(job['reference_count'], 1)
        self.assertEqual(job['reference_admission']['max_reference_images'], 4)

    def test_multipart_edit_refuses_a_wrong_node(self):
        response = self.edit(policy(node='node-b'))
        self.assertEqual(response.status_code, 422)
        self.assertIn('for node', response.json()['detail'])

    def test_multipart_edit_refuses_malformed_policy_json(self):
        response = self.client.post(
            '/api/generate/edit', headers=self.headers,
            data={'repo': MODEL.repo, 'prompt': 'x', 'reference_policy': '{'},
            files=[('images', ('a.png', png_bytes(), 'image/png'))])
        self.assertEqual(response.status_code, 422)
        self.assertIn('reference_policy', response.json()['detail'])

    def test_a_present_override_above_the_published_cap_is_admitted(self):
        # Qwen's published 32 GiB cap is 1; a Hub owner override raises it to 2, within the
        # engine's intrinsic 10. The route's local precheck must not reject it before the
        # present policy is read.
        images = [('images', ('a.png', png_bytes(), 'image/png')),
                  ('images', ('b.png', png_bytes(), 'image/png'))]
        response = self.client.post(
            '/api/generate/edit', headers=self.headers,
            data={'repo': QWEN_IMAGE_21_TURBO.repo, 'prompt': 'x',
                  'reference_policy': json.dumps(policy(
                      model=QWEN_IMAGE_21_TURBO, count=2, maximum=2))},
            files=images)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['job']['reference_count'], 2)
        # The same count without a policy keeps the old published precheck.
        response = self.client.post(
            '/api/generate/edit', headers=self.headers,
            data={'repo': QWEN_IMAGE_21_TURBO.repo, 'prompt': 'x'}, files=images)
        self.assertEqual(response.status_code, 422)

    def test_json_submit_accepts_a_txt2img_policy(self):
        response = self.client.post(
            '/api/generate/txt2img', headers=self.headers,
            json={'repo': MODEL.repo, 'prompt': 'x',
                  'reference_policy': policy(mode='txt2img', count=None, maximum=0)})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['job']['mode'], 'txt2img')

    def test_json_submit_refuses_a_wrong_node(self):
        response = self.client.post(
            '/api/generate/txt2img', headers=self.headers,
            json={'repo': MODEL.repo, 'prompt': 'x',
                  'reference_policy': policy(mode='txt2img', count=None, node='node-b')})
        self.assertEqual(response.status_code, 422)
        self.assertIn('for node', response.json()['detail'])

    def test_the_manager_keeps_its_local_machine_alias(self):
        self.assertEqual(self.manager.machine, 'local')
        self.assertEqual(self.manager.destination_node_id, NODE)


class LaunchPropagationTests(unittest.TestCase):
    def test_worker_command_forwards_the_destination_to_image_only(self):
        resources = Path('/bundle/packaging')
        data, models = Path('/data'), Path('/models')
        token, ready = Path('/data/token'), Path('/data/ready')
        image = {'id': 'image', 'python': '/python', 'extra_args': []}
        music = {'id': 'music', 'python': '/python', 'extra_args': []}
        command = hub_worker.worker_command(
            image, resources, data, models, token, ready, 123,
            offline=False, destination_node_id=NODE)
        self.assertIn('--destination-node-id', command)
        self.assertEqual(command[command.index('--destination-node-id') + 1], NODE)
        self.assertNotIn('--destination-node-id', hub_worker.worker_command(
            music, resources, data, models, token, ready, 123,
            offline=False, destination_node_id=NODE))
        self.assertNotIn('--destination-node-id', hub_worker.worker_command(
            image, resources, data, models, token, ready, 123,
            offline=False, destination_node_id=None))

    def test_image_worker_environment_carries_the_identity(self):
        values = dict(image_data=Path('/data/image'), models=Path('/models'),
                      manifest_path=Path('/bundle/manifest.json'),
                      release_path=Path('/bundle/release.json'), token='token',
                      runtime=Path('/bundle/python'))
        self.assertNotIn('STUDIOHUB_DESTINATION_NODE_ID',
                         image_worker.worker_environment(**values))
        environment = image_worker.worker_environment(**values, destination_node_id=NODE)
        self.assertEqual(environment['STUDIOHUB_DESTINATION_NODE_ID'], NODE)

    def test_the_launcher_argument_is_a_narrow_validated_identity(self):
        self.assertIsNone(image_worker.parse_destination_node_id(None))
        self.assertEqual(image_worker.parse_destination_node_id(' node-a '), 'node-a')
        for bad in ('', 'Node A', 'a' * 101, 'node/a'):
            with self.assertRaisesRegex(ValueError, 'fleet identity'):
                image_worker.parse_destination_node_id(bad)


if __name__ == '__main__':
    unittest.main()
