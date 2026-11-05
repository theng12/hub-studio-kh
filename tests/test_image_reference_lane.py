"""Reference-to-image: the Image worker's edit lane (owner-approved 2026-10-26).

FLUX.2 Klein 4B MLX 4-bit takes one or more character/reference images plus a prompt and returns
a scene with those characters in it. The lane is a second model *class* on the same installed
package (`Flux2KleinEdit`), reached by `POST /api/generate/edit`, and it publishes its own count
window, reference pixel ceiling and memory floor.

The failure this file exists to make impossible: a request that carried references being served
as text-to-image. Every layer refuses instead — here, a count outside the published window, a
file that is not a readable image, or a model with no edit lane, and the job record carries
`mode: "edit"` plus `reference_count` so the Gateway can compare what it sent with what was
attached.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
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
from components.image.app.backend import loading_mode
from components.image.app.backend.generation import (FLUX2_KLEIN, MODEL_ID, MODEL_REVISION,
                                                     QWEN_IMAGE_21_TURBO, Z_IMAGE_TURBO,
                                                     GenerationManager, lane_contract,
                                                     lane_parameters,
                                                     lane_policy, loading_modes, memory_plan,
                                                     plan_configuration, reference_count_facts,
                                                     reference_counts, reference_max,
                                                     reference_mode,
                                                     reference_mode_values, validate_request)
from components.image.app.backend.main import create_app, edit_lane
from tests.test_image_standalone import HOST_BYTES, RUNTIME_ID, TOKEN, tiny_manifest


EDIT_MODULE = 'mflux.models.flux2.variants.edit.flux2_klein_edit'


def fake_edit_module(record: dict) -> dict:
    """A stand-in for mflux's `Flux2KleinEdit`, recording the class loaded and the call it got."""
    names = ('mflux', 'mflux.models', 'mflux.models.flux2', 'mflux.models.flux2.variants',
             'mflux.models.flux2.variants.edit', EDIT_MODULE)
    modules = {name: types.ModuleType(name) for name in names}

    class Flux2KleinEdit:
        def __init__(self, *, model_path, quantize, lora_paths, lora_scales):
            record.setdefault('constructed', []).append(str(model_path))
            self._cancel_event = None
            self._step_callback = None

        def set_cancel_event(self, event):
            self._cancel_event = event

        def set_step_callback(self, callback):
            self._step_callback = callback

        def generate_image(self, *, seed, prompt, num_inference_steps, height, width,
                           guidance, image_paths=None):
            record.setdefault('calls', []).append({
                'seed': seed, 'prompt': prompt, 'steps': num_inference_steps,
                'width': width, 'height': height, 'guidance': guidance,
                'image_paths': [str(path) for path in (image_paths or [])]})
            for step in range(num_inference_steps):
                if self._cancel_event is not None and self._cancel_event.is_set():
                    raise RuntimeError('Generation cancelled')
                if self._step_callback is not None:
                    self._step_callback(step + 1, num_inference_steps)

            class Result:
                def save(self, path, overwrite):
                    assert overwrite
                    Image.new('RGB', (width, height), (9, 21, 33)).save(path, format='PNG')
            return Result()

    modules[EDIT_MODULE].Flux2KleinEdit = Flux2KleinEdit
    return modules


def png_bytes(size=(64, 64), colour=(200, 40, 40), fmt='PNG') -> bytes:
    buffer = io.BytesIO()
    Image.new('RGB', size, colour).save(buffer, format=fmt)
    return buffer.getvalue()


class LaneHarness:
    """An installed FLUX.2 Klein package, a manager, and an authorised client.

    A mixin, not a TestCase: the below-floor class reuses this setup without inheriting (and so
    re-running) the tests that assume a Mac which clears the edit floor.
    """

    total_memory_bytes = HOST_BYTES

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for name, value in (('_reclaimable_available_gb', 64.0),
                            ('_host_swap_used_mb', 0.0)):
            mocked = patch('components.image.app.backend.generation.' + name, return_value=value)
            mocked.start()
            self.addCleanup(mocked.stop)
        # The predicate stays real; only the reported level is pinned (see _image_pressure).
        from _image_pressure import pressure
        self.addCleanup(pressure(1).stop)
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
        self.manager = GenerationManager(
            self.data, self.library, self.manifest, RUNTIME_ID,
            manifests=[self.manifest], total_memory_bytes=self.total_memory_bytes)
        self.client = TestClient(create_app(self.manager, TOKEN))
        self.headers = {'X-Studio-Token': TOKEN}
        self.record: dict = {}

    def wait(self, job_id):
        for _ in range(300):
            job = self.client.get(f'/api/generate/jobs/{job_id}', headers=self.headers).json()['job']
            if job['state'] in ('done', 'error', 'cancelled'):
                return job
            time.sleep(0.02)
        self.fail('Image edit job did not reach a terminal state')

    def catalog_for(self, total_memory_bytes):
        """The catalog one Mac of this size publishes: a fresh manager, own data dir."""
        data = Path(self.temp.name) / f'data-{total_memory_bytes}' / 'image'
        data.mkdir(parents=True)
        manager = GenerationManager(data, self.library, self.manifest, RUNTIME_ID,
                                    manifests=[self.manifest],
                                    total_memory_bytes=total_memory_bytes)
        client = TestClient(create_app(manager, TOKEN))
        return client.get('/api/catalog', headers=self.headers).json()

    def post_edit(self, files, **fields):
        body = {'repo': MODEL_ID, 'prompt': 'Two friends at a night market', 'steps': '4',
                'guidance': '1.0', 'aspect_ratio': '1:1', 'seed': '7', **fields}
        return self.client.post(
            '/api/generate/edit',
            data=body,
            files=[('images', (name, content, mime)) for name, content, mime in files],
            headers=self.headers)

@contextmanager
def patched_eligibility():
    """The worker's host gate, satisfied: these cases are about memory, not macOS version."""
    with patch('components.image.app.backend.main._host_eligibility',
               return_value={'eligible': True, 'macos': '26.6.2'}):
        yield


class ReferenceLaneTests(LaneHarness, unittest.TestCase):
    def test_an_edit_job_attaches_every_reference_in_order_and_echoes_the_count(self):
        first, second = png_bytes(colour=(200, 40, 40)), png_bytes(colour=(40, 40, 200))
        with (patch.dict(sys.modules, fake_edit_module(self.record)),
              patch('components.image.app.backend.main._host_eligibility',
                    return_value={'eligible': True, 'macos': '26.6.2'})):
            response = self.post_edit([('one.png', first, 'image/png'),
                                       ('two.png', second, 'image/png')])
            self.assertEqual(response.status_code, 200, response.text)
            job_id = response.json()['job']['id']
            job = self.wait(job_id)
        self.assertEqual(job['state'], 'done', job.get('error'))
        self.assertEqual(job['mode'], 'edit')
        self.assertEqual(job['reference_count'], 2)
        self.assertEqual(job['reference_digests'],
                         [sha256(first).hexdigest(), sha256(second).hexdigest()])
        staged = job['params']['reference_images']
        self.assertEqual([Path(path).suffix for path in staged], ['.png', '.png'])
        # The edit class — not the text-to-image class — was loaded, and it got the references in
        # the order the request named them.
        self.assertEqual(Path(self.record['constructed'][0]).name, 'model')
        self.assertEqual(self.record['calls'][0]['image_paths'], staged)
        self.assertEqual(self.record['calls'][0]['steps'], 4)
        self.assertEqual((self.record['calls'][0]['width'],
                          self.record['calls'][0]['height']), (1024, 1024))
        self.assertEqual(job['reference_count'], len(self.record['calls'][0]['image_paths']))
        # A reference is input: the job's staged directory is gone once it is terminal.
        self.assertFalse((self.manager.inputs / job_id).exists())
        self.assertEqual([path for path in self.manager.inputs.iterdir()], [])

    def test_a_reference_job_without_references_is_refused_with_the_published_window(self):
        with patch('components.image.app.backend.main._host_eligibility',
                   return_value={'eligible': True, 'macos': '26.6.2'}):
            response = self.client.post('/api/generate/edit',
                                        data={'repo': MODEL_ID, 'prompt': 'x'},
                                        headers=self.headers)
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn(f'{FLUX2_KLEIN.reference_limits["min"]}', response.text)
        self.assertIn(f'{FLUX2_KLEIN.reference_limits["max"]}', response.text)
        self.assertEqual(self.manager.list(), [])

    def test_more_references_than_the_model_takes_are_refused_before_a_job_is_queued(self):
        too_many = [('1.png', png_bytes(), 'image/png'),
                    ('2.png', png_bytes(), 'image/png'),
                    ('3.png', png_bytes(), 'image/png'),
                    ('4.png', png_bytes(), 'image/png'),
                    ('5.png', png_bytes(), 'image/png')]
        with patch('components.image.app.backend.main._host_eligibility',
                   return_value={'eligible': True, 'macos': '26.6.2'}):
            response = self.post_edit(too_many)
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn('between 1 and 4 reference images', response.text)
        self.assertIn('this request had 5', response.text)
        self.assertEqual(self.manager.list(), [])
        self.assertEqual(list(self.manager.inputs.iterdir()), [])

    def test_a_reference_that_is_not_an_image_is_refused_before_the_job_is_queued(self):
        with patch('components.image.app.backend.main._host_eligibility',
                   return_value={'eligible': True, 'macos': '26.6.2'}):
            response = self.post_edit([('notes.png', b'not an image at all', 'image/png')])
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn('not a readable PNG, JPEG or WebP image', response.text)
        self.assertEqual(self.manager.list(), [])

    def test_an_unreadable_reference_path_is_refused_before_enqueue(self):
        """A direct caller (not the route) gets the same gate: no job, no queued failure."""
        broken = Path(self.temp.name) / 'broken.png'
        broken.write_bytes(b'\x89PNG\r\n\x1a\n not actually a png')
        with self.assertRaisesRegex(ValueError, 'not a readable PNG, JPEG or WebP image'):
            self.manager.submit({'repo': MODEL_ID, 'prompt': 'x',
                                 'reference_images': [str(broken)]}, mode='edit')
        self.assertEqual(self.manager.list(), [])

    def test_a_model_without_a_reference_lane_refuses_references_in_its_old_words(self):
        image = Path(self.temp.name) / 'ok.png'
        image.write_bytes(png_bytes())
        with self.assertRaisesRegex(ValueError, 'Reference images, LoRAs'):
            validate_request({'repo': Z_IMAGE_TURBO.repo, 'prompt': 'x',
                              'reference_images': [str(image)]})
        self.assertNotIn('reference_images',
                         {item['name'] for item in lane_parameters(Z_IMAGE_TURBO)})

    def test_a_reference_request_is_never_served_as_text_to_image(self):
        """The mode comes from the request, not from a caller's label: references ⇒ edit."""
        image = Path(self.temp.name) / 'ok.png'
        image.write_bytes(png_bytes())
        params = validate_request({'repo': MODEL_ID, 'prompt': 'x',
                                   'reference_images': [str(image)]})
        self.assertEqual(params['reference_images'], [str(image)])
        self.assertNotIn('reference_images',
                         validate_request({'repo': MODEL_ID, 'prompt': 'x'}))
        # An explicit `mode='txt2img'` cannot downgrade a request that carries references, and
        # an explicit `mode='edit'` with none is refused rather than silently rendering text.
        with self.assertRaisesRegex(ValueError, 'between 1 and 4 reference images'):
            validate_request({'repo': MODEL_ID, 'prompt': 'x'}, mode='edit')

    def test_the_catalog_advertises_edit_for_the_configuration_this_mac_runs(self):
        with patch('components.image.app.backend.main._host_eligibility',
                   return_value={'eligible': True, 'macos': '26.6.2'}):
            row = self.client.get('/api/catalog', headers=self.headers).json()['models'][0]
        self.assertIn('edit', row['capabilities'])
        self.assertEqual(row['reference_strength_mode'], 'fixed')
        self.assertEqual(row['supported_reference_strengths'], [])
        self.assertTrue(row['reference_lane']['available'])
        self.assertEqual(row['reference_lane']['mode'], 'default')
        references = row['lane_contract']['references']
        self.assertEqual(references['field'], 'images')
        self.assertEqual(references['mode'], 'default')
        # The floor and the runtime are the *default* configuration's own published numbers.
        self.assertEqual(references['min_total_memory_gb'],
                         reference_mode_values(FLUX2_KLEIN, 'default')['min_total_memory_gb'])
        self.assertEqual(references['runtime_seconds']['evidence'], 'measured')
        self.assertTrue(references['runtime_seconds']['measured'])

    def test_an_edit_job_is_sized_from_its_own_lane_history(self):
        """An edit job gets >= the 900 s floor, and its own median once three have finished.

        The two lanes are mixed in one job store, so a per-lane history is the only way the
        median is the right measurement: an edit job's seconds per step includes the reference
        encode and would size a text-to-image deadline from the wrong figure (and the reverse).
        """
        now = time.time()
        for index in range(3):
            for mode, seconds in (('edit', 2000.0), ('txt2img', 200.0)):
                self.manager.jobs[f'{mode}-{index}'] = {
                    'id': f'{mode}-{index}', 'mode': mode, 'state': 'done', 'steps': 4,
                    'duration_seconds': seconds, 'finished_at': now + index,
                    'params': {'repo': MODEL_ID}}
        edit = self.manager._deadline_s({'repo': MODEL_ID, 'steps': 4}, 'edit')
        text = self.manager._deadline_s({'repo': MODEL_ID, 'steps': 4}, 'txt2img')
        self.assertGreaterEqual(edit, 900)
        self.assertEqual(edit, 120 + 1.5 * 4 * (2000.0 / 4))
        self.assertEqual(text, 900)      # the edit jobs' times never reached this lane
        self.assertLess(text, edit)


class SmallMacLaneTests(LaneHarness, unittest.TestCase):
    """Below the default configuration's floor, the lane runs the bounded one — and says so.

    8 GB and 16 GB Macs qualify through the same bounded configuration text-to-image already
    uses (owner, 2026-10-26), at the cost of speed: the smallest tier gets stage hooks + block
    streaming + the bounded VAE decode, the 16 GB tier stage hooks + the bounded decode. A 4 GB
    Mac has no measured configuration and advertises nothing.
    """

    total_memory_bytes = 8 * 1024 ** 3

    def plan(self, gigabyte):
        return memory_plan(FLUX2_KLEIN, int(gigabyte * 1024 ** 3), lane='edit')

    def test_the_8_gb_tier_runs_the_streamed_bounded_configuration(self):
        plan = self.plan(8)
        self.assertEqual(plan['mode'], 'low-ram')
        self.assertTrue(plan['staged'])
        self.assertTrue(plan['bounded_vae_decode'])
        self.assertTrue(plan['stream'], 'the 8 GB tier must stream the transformer blocks')
        self.assertFalse(plan['lean'])
        self.assertEqual(reference_mode(FLUX2_KLEIN, 8 * 1024 ** 3), 'bounded-streamed')

    def test_the_16_gb_tier_runs_the_staged_bounded_configuration(self):
        plan = self.plan(16)
        self.assertEqual(plan['mode'], 'low-ram')
        self.assertTrue(plan['staged'])
        self.assertTrue(plan['bounded_vae_decode'])
        self.assertFalse(plan['stream'], '16 GB is above the model\'s streaming threshold')
        self.assertEqual(reference_mode(FLUX2_KLEIN, 16 * 1024 ** 3), 'bounded-staged')

    def test_the_edit_lane_reads_its_own_tiers_not_the_text_lanes(self):
        """A reference render costs more than a text render, so its thresholds are its own.

        On this 16 GiB host the text lane's policy picks `lean` while the edit lane's picks
        `bounded-staged`: one policy answering for both lanes is exactly what the owner overruled
        on 2026-10-26.
        """
        self.assertEqual(plan_configuration(memory_plan(FLUX2_KLEIN, 16 * 1024 ** 3, 'auto')),
                         'lean')
        self.assertEqual(plan_configuration(self.plan(16)), 'bounded-staged')
        self.assertIs(lane_policy(FLUX2_KLEIN, 'edit'), FLUX2_KLEIN.reference_memory)
        self.assertIs(lane_policy(FLUX2_KLEIN, 'txt2img'), FLUX2_KLEIN.memory)

    def test_the_loading_mode_setting_applies_to_edit_as_it_does_to_text_to_image(self):
        """Owner, 2026-10-26: big Macs run at full speed and only small ones go slow.

        `low` forces the bounded configuration, `full` forces the default one — for the edit lane
        too. `auto` is the only mode that consults the lane's measured thresholds.
        """
        low = memory_plan(FLUX2_KLEIN, 32 * 1024 ** 3, 'low', lane='edit')
        self.assertTrue(low['low_ram'])
        self.assertEqual(plan_configuration(low), 'bounded-staged')
        full = memory_plan(FLUX2_KLEIN, 8 * 1024 ** 3, 'full', lane='edit')
        self.assertFalse(full['low_ram'])
        self.assertEqual(plan_configuration(full), 'default')
        # ...while text-to-image on the same Mac keeps exactly what the setting asked for.
        self.assertEqual(memory_plan(FLUX2_KLEIN, 8 * 1024 ** 3, 'full')['mode'], 'default')

    def test_a_mac_that_forces_a_configuration_it_cannot_hold_does_not_advertise_edit(self):
        """The setting is honoured, and the capability is the honest consequence of it.

        8 GiB with `full` would run the default configuration, whose measured floor this Mac does
        not clear: the worker refuses the lane (and says why) instead of overriding the operator
        or starting a render it cannot hold.
        """
        refused = edit_lane(FLUX2_KLEIN, {'ready': True}, 8 * 1024 ** 3, 'full')
        self.assertFalse(refused['available'])
        self.assertEqual(refused['mode'], 'default')
        self.assertIn('off on the 8 GiB tier', refused['reason'])

    def test_an_eight_gigabyte_mac_is_refused_with_the_real_minimum(self):
        """Two references need 7.47 GB of working set, so 8 GiB is not offered the lane.

        Measured, not estimated: `bounded-streamed` peaks at 5.69 GB for one reference and
        7.47 GB for two, which leaves no room for macOS on an 8 GiB Mac. The published floor is
        therefore 12 GiB and this Mac says so, rather than being advertised a render that swaps.
        """
        with patch('components.image.app.backend.main._host_eligibility',
                   return_value={'eligible': True, 'macos': '26.6.2'}):
            row = self.client.get('/api/catalog', headers=self.headers).json()['models'][0]
        self.assertNotIn('edit', row['capabilities'])
        self.assertEqual(row['reference_lane']['mode'], 'bounded-streamed')
        self.assertIn('off on the 8 GiB tier', row['reference_lane']['reason'])
        self.assertEqual(reference_mode_values(FLUX2_KLEIN, 'bounded-streamed')
                         ['min_total_memory_gb'], 12.0)

    def test_a_sixteen_gigabyte_mac_advertises_the_staged_configuration(self):
        with patched_eligibility():
            row = self.catalog_for(16 * 1024 ** 3)['models'][0]
        self.assertIn('edit', row['capabilities'])
        self.assertEqual(row['reference_lane']['mode'], 'bounded-staged')
        references = row['lane_contract']['references']
        self.assertEqual(references['mode'], 'bounded-staged')
        self.assertEqual(references['min_total_memory_gb'],
                         reference_mode_values(FLUX2_KLEIN, 'bounded-staged')
                         ['min_total_memory_gb'])

    def test_the_bounded_mode_gets_its_measured_longer_timeout(self):
        floor = reference_mode_values(FLUX2_KLEIN, 'bounded-streamed')['timeout_floor_s']
        self.assertGreaterEqual(floor, 900)
        self.assertGreaterEqual(self.manager._deadline_s({'repo': MODEL_ID, 'steps': 4}, 'edit'),
                                floor)


class BelowEveryMeasuredFloorTests(LaneHarness, unittest.TestCase):
    """A Mac under every measured configuration's floor advertises and takes nothing."""

    total_memory_bytes = 4 * 1024 ** 3

    def test_an_edit_job_is_refused_below_the_measured_memory_floor(self):
        with patch('components.image.app.backend.main._host_eligibility',
                   return_value={'eligible': True, 'macos': '26.6.2'}):
            row = self.client.get('/api/catalog', headers=self.headers).json()['models'][0]
            response = self.post_edit([('one.png', png_bytes(), 'image/png')])
        self.assertNotIn('edit', row['capabilities'])
        self.assertFalse(row['reference_lane']['available'])
        self.assertIn('GB of unified memory', row['reference_lane']['reason'])
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn('GB of unified memory', response.text)
        self.assertEqual(self.manager.list(), [])



class EditTierPublicationTests(unittest.TestCase):
    """The edit lane publishes its own tiers, from its own measurements (owner, 2026-10-26)."""

    def test_the_edit_lanes_modes_come_from_its_own_measured_blocks(self):
        modes = loading_modes(FLUX2_KLEIN, 16 * 1024 ** 3, 'auto', lane='edit')
        self.assertEqual(modes['lane'], 'edit')
        self.assertTrue(modes['supported'])
        self.assertEqual(modes['configuration'], 'bounded-staged')
        self.assertTrue(modes['options'])
        # The peak and the runtime are the bounded configuration's own published figures.
        bounded = reference_mode_values(FLUX2_KLEIN, 'bounded-staged')
        self.assertEqual(modes['measured']['low_ram']['peak_gb'], bounded['peak_gb'])
        self.assertEqual(modes['measured']['low_ram']['seconds'],
                         max(row['seconds'] for row in bounded['measured']))
        # ...and on the same Mac the text lane answers differently, from its own policy.
        self.assertEqual(loading_modes(FLUX2_KLEIN, 16 * 1024 ** 3, 'auto')['configuration'],
                         'lean')

    def test_a_lane_with_no_measured_configuration_states_no_number(self):
        """A block nobody ran publishes `None`, never an estimate dressed as a measurement."""
        modes = loading_modes(FLUX2_KLEIN, 32 * 1024 ** 3, 'auto', lane='edit')
        measured = modes['measured']
        self.assertEqual(measured['configuration'], 'bounded-staged')
        for key in ('peak_gb', 'seconds'):
            value = measured['default'][key]
            self.assertTrue(value is None or value > 0, f'{key} must be a measurement or None')


class QwenTurboEditLaneTests(unittest.TestCase):
    """Qwen Image 2.1 Turbo is the second Reference-to-Image model (owner, 2026-10-26)."""

    def test_the_row_serves_both_lanes_from_one_installed_package(self):
        from components.image.app.backend import qwen_turbo
        from components.image.app.backend.generation import model_class

        self.assertEqual(QWEN_IMAGE_21_TURBO.package_id, 'qwen-image-2-1-turbo-mflux-q4')
        self.assertIs(model_class(QWEN_IMAGE_21_TURBO, 'edit'), qwen_turbo.QwenTurboEdit)
        self.assertIs(model_class(QWEN_IMAGE_21_TURBO), qwen_turbo.QwenTurbo)

    def test_its_edit_lane_is_bounded_where_the_full_configuration_could_not_hold(self):
        for gigabytes in (16, 24, 32):
            plan = memory_plan(QWEN_IMAGE_21_TURBO, gigabytes * 1024 ** 3, 'auto', lane='edit')
            self.assertTrue(plan['low_ram'], gigabytes)
            self.assertTrue(plan['vae_tiling'], 'the bounded Qwen configuration decodes in tiles')
            self.assertFalse(plan['stream'], 'this model has no stage hooks to stream with')
            self.assertEqual(plan_configuration(plan), 'bounded-staged')
        # 32 GiB is the host the unbounded configuration pushed into 9.5 GB of swap, so it takes
        # the bounded one; only a larger tier is offered the default, and that tier has no
        # published floor until a host large enough has been measured.
        large = memory_plan(QWEN_IMAGE_21_TURBO, 64 * 1024 ** 3, 'auto', lane='edit')
        self.assertFalse(large['low_ram'])
        self.assertEqual(plan_configuration(large), 'default')
        self.assertIsNone(reference_mode_values(QWEN_IMAGE_21_TURBO, 'default')
                          ['min_total_memory_gb'])
        provisional = edit_lane(QWEN_IMAGE_21_TURBO, {'ready': True}, 64 * 1024 ** 3, 'auto')
        self.assertTrue(provisional['available'])
        self.assertEqual(lane_contract(QWEN_IMAGE_21_TURBO,
                         total_memory_bytes=64 * 1024 ** 3)['references']['policy_source'],
                         'provisional, measuring')
        # The text lane of the same row keeps its shipped single mode.
        self.assertEqual(plan_configuration(
            memory_plan(QWEN_IMAGE_21_TURBO, 16 * 1024 ** 3, 'auto')), 'default')

    def test_the_wrapper_pins_the_recipe_and_the_lane_it_publishes(self):
        from components.image.app.backend import qwen_lowram, qwen_turbo

        with tempfile.TemporaryDirectory() as folder:
            index = Path(folder) / 'model_index.json'
            index.write_text(json.dumps({'_class_name': 'QwenImage21Pipeline',
                                         'sample_sigmas': [1.0, 0.5, 0.0]}))
            with patch.object(qwen_turbo, '_turbo_nodes', lambda: (1.0, 0.978453, 0.95418)):
                with self.assertRaisesRegex(ValueError, 'Turbo sampling recipe'):
                    qwen_turbo._check_recipe(Path(folder))
                index.write_text(json.dumps({'_class_name': 'QwenImage21Pipeline',
                                             'sample_sigmas': [1.0, 0.978453, 0.95418]}))
                qwen_turbo._check_recipe(Path(folder))
            index.unlink()
            with self.assertRaisesRegex(ValueError, 'no readable model_index.json'):
                qwen_turbo._check_recipe(Path(folder))

        wrapper = object.__new__(qwen_turbo.QwenTurboEdit)
        seen: dict = {}

        def capture(_self, **kwargs):
            seen.update(kwargs)

        with patch.object(qwen_lowram.QwenLowRam, 'generate_image', capture):
            wrapper.generate_image(seed=1, prompt='p', num_inference_steps=8, guidance=1.0,
                                   image_paths=[Path('reference.png')])
            self.assertEqual(seen['strength'], 1.0)
            with self.assertRaisesRegex(ValueError, 'exactly 8'):
                wrapper.generate_image(seed=1, prompt='p', num_inference_steps=6, guidance=1.0)
            with self.assertRaisesRegex(ValueError, 'does not accept mask_image'):
                wrapper.generate_image(seed=1, prompt='p', num_inference_steps=8, guidance=1.0,
                                       mask_image='a-mask')


def model_with_count_rows(base, **rows_by_mode):
    """`base` with extra measured rows appended to named modes — the shape the calibration
    receipt fills in later, built here from stated numbers so a test never needs a live run."""
    modes = {}
    for name, block in base.reference_limits['modes'].items():
        extra = rows_by_mode.get(name)
        modes[name] = ({**block, 'measured': list(block['measured']) + [extra]}
                       if extra else block)
    return replace(base, reference_limits={**base.reference_limits, 'modes': modes})


class EngineOwnedCapTests(unittest.TestCase):
    """`max` is the engine's cap, published whether or not every count has run (owner, 2026-11-05)."""

    def test_klein_publishes_four_and_validates_to_it(self):
        self.assertEqual(FLUX2_KLEIN.reference_limits['max'], 4)
        self.assertEqual(reference_max(FLUX2_KLEIN), 4)
        with tempfile.TemporaryDirectory() as folder:
            paths = []
            for index in range(5):
                path = Path(folder) / f'{index}.png'
                path.write_bytes(png_bytes())
                paths.append(str(path))
            params = validate_request({'repo': FLUX2_KLEIN.repo, 'prompt': 'x',
                                       'reference_images': paths[:4]})
            self.assertEqual(len(params['reference_images']), 4)
            with self.assertRaisesRegex(ValueError, 'between 1 and 4 reference images'):
                validate_request({'repo': FLUX2_KLEIN.repo, 'prompt': 'x',
                                  'reference_images': paths})
        contract = lane_contract(FLUX2_KLEIN)
        parameter = next(item for item in contract['parameters']
                         if item['name'] == 'reference_images')
        self.assertEqual(parameter['max_items'], 4)
        self.assertEqual(contract['references']['max'], 4)

    def test_qwen_publishes_ten_from_mflux_or_the_rows_own_fallback(self):
        # mflux absent → the row's documented fallback; mflux present → MAX_REFERENCES (10 today).
        self.assertEqual(QWEN_IMAGE_21_TURBO.reference_limits['max'], 10)
        self.assertEqual(reference_max(QWEN_IMAGE_21_TURBO), 10)
        reference_contract = lane_contract(QWEN_IMAGE_21_TURBO, total_memory_bytes=32 * 1024 ** 3)['references']
        self.assertEqual(reference_contract['max'], 1)
        self.assertEqual(reference_contract['max_intrinsic'], 10)

    def test_a_future_model_inherits_its_engine_constant_without_an_edit_here(self):
        stub = types.ModuleType('fake_reference_cap_engine')
        stub.MAX_REFERENCES = 7
        row = replace(QWEN_IMAGE_21_TURBO,
                      reference_limits={**QWEN_IMAGE_21_TURBO.reference_limits,
                                        'max': 3,
                                        'max_engine_constant':
                                            'fake_reference_cap_engine:MAX_REFERENCES'})
        with patch.dict(sys.modules, {'fake_reference_cap_engine': stub}):
            self.assertEqual(reference_max(row), 7, 'the engine wins over the row fallback')
        absent = replace(QWEN_IMAGE_21_TURBO,
                         reference_limits={**QWEN_IMAGE_21_TURBO.reference_limits,
                                           'max': 3,
                                           'max_engine_constant':
                                               'no_such_engine_module_xyz:MAX_REFERENCES'})
        self.assertEqual(reference_max(absent), 3, 'no engine → the row number, honestly')


class PublishedCountsTests(unittest.TestCase):
    """The contract's `counts` block publishes each count's recorded row only (owner, 2026-11-05):
    floors and timeouts are null until a row carries them as its own adopted fields, and the mode
    blocks remain the mode-scoped policy the runtime enforces."""

    def test_counts_one_and_two_publish_their_recorded_measurements_only(self):
        references = lane_contract(FLUX2_KLEIN)['references']
        counts = references['counts']
        self.assertEqual(sorted(counts), ['1', '2'])
        self.assertEqual(counts['2']['default']['seconds'], 151.56)
        self.assertEqual(counts['2']['default']['peak_gb'], 12.02)
        # No row carries a floor or timeout, so none is published — deriving one from a peak
        # is exactly what the owner refused.
        for mode in ('default', 'bounded-staged', 'bounded-streamed'):
            self.assertIsNone(counts['2'][mode]['min_total_memory_gb'])
            self.assertIsNone(counts['2'][mode]['min_free_memory_gb'])
            self.assertIsNone(counts['2'][mode]['timeout_floor_s'])
        # The mode-scoped policy is untouched and never relabelled count-specific evidence.
        self.assertEqual(references['min_total_memory_gb'],
                         reference_mode_values(FLUX2_KLEIN, 'default')['min_total_memory_gb'])
        self.assertEqual(references['timeout_floor_s'],
                         reference_mode_values(FLUX2_KLEIN, 'default')['timeout_floor_s'])
        self.assertNotIn('3', counts, 'an unmeasured count is absent, never a row of guesses')

    def test_adopted_row_fields_are_published_verbatim_and_nothing_is_derived(self):
        grown = model_with_count_rows(
            FLUX2_KLEIN,
            **{'bounded-staged': {'references': 3, 'reference_pixels': 1024, 'seconds': 300.0,
                                  'peak_gb': 6.5, 'swap_delta_mb': 0.0,
                                  'min_total_memory_gb': 12.0, 'timeout_floor_s': 950.0}})
        counts = reference_counts(grown)
        adopted = counts['3']['bounded-staged']
        self.assertEqual(adopted['seconds'], 300.0)
        self.assertEqual(adopted['min_total_memory_gb'], 12.0)
        self.assertEqual(adopted['timeout_floor_s'], 950.0)
        # The same count's default configuration never ran: null, not a number borrowed from
        # another count or from the block.
        self.assertIsNone(counts['3']['default'])
        # Counts 1-2 are unchanged by the extra row.
        self.assertEqual(counts['2']['default']['seconds'], 151.56)
        self.assertIsNone(counts['2']['default']['min_total_memory_gb'])

    def test_qwens_counts_derive_nothing_either(self):
        references = lane_contract(QWEN_IMAGE_21_TURBO,
                                   total_memory_bytes=16 * 1024 ** 3)['references']
        self.assertEqual(references['max'], 0)
        self.assertEqual(references['max_intrinsic'], 10)
        counts = references['counts']
        self.assertEqual(counts['2']['bounded-staged']['seconds'], 182.96)
        self.assertIsNone(counts['2']['bounded-staged']['min_total_memory_gb'])
        self.assertIsNone(counts['2']['default']['timeout_floor_s'])
        # The mode-scoped policy still publishes the bounded block's own measured floor.
        self.assertEqual(references['min_total_memory_gb'],
                         reference_mode_values(QWEN_IMAGE_21_TURBO,
                                               'bounded-staged')['min_total_memory_gb'])

class PerCountDeadlineHarness(unittest.TestCase):
    """A manager on a 16 GiB Mac: per-count timeout activation is deferred, so the deadline
    keeps the tier's mode-scoped published floor (owner, 2026-11-05)."""

    total_memory_bytes = 16 * 1024 ** 3

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for name, value in (('_reclaimable_available_gb', 64.0),
                            ('_host_swap_used_mb', 0.0)):
            mocked = patch('components.image.app.backend.generation.' + name, return_value=value)
            mocked.start()
            self.addCleanup(mocked.stop)
        # The predicate stays real; only the reported level is pinned (see _image_pressure).
        from _image_pressure import pressure
        self.addCleanup(pressure(1).stop)
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
        self.manager = GenerationManager(
            self.data, self.library, self.manifest, RUNTIME_ID,
            manifests=[self.manifest], total_memory_bytes=self.total_memory_bytes)

    def test_an_unmeasured_count_keeps_the_tiers_published_floor(self):
        # No count-specific timeout is wired in: three references take the same 900 s mode
        # floor two references take, from the bounded-staged block this Mac's tier selects.
        self.assertEqual(self.manager._deadline_s(
            {'repo': MODEL_ID, 'steps': 4,
             'reference_images': ['a.png', 'b.png', 'c.png']}, 'edit'), 900.0)
        self.assertEqual(self.manager._deadline_s(
            {'repo': MODEL_ID, 'steps': 4,
             'reference_images': ['a.png', 'b.png']}, 'edit'), 900.0)

    def test_no_row_carries_a_timeout_and_none_is_invented(self):
        self.assertIsNone(reference_count_facts(FLUX2_KLEIN, 'bounded-staged', 2)
                          ['timeout_floor_s'])
        self.assertIsNone(reference_count_facts(FLUX2_KLEIN, 'bounded-staged', 3))
        self.assertEqual(reference_mode_values(FLUX2_KLEIN, 'bounded-staged')
                         ['timeout_floor_s'], 900.0)
