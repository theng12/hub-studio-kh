"""The Image worker's per-machine loading-size setting, and what it publishes per model.

``loading_mode`` (``auto`` | ``low`` | ``full``, ``GET/PUT /api/loading-mode``) is the operator's
control over which mflux memory *configuration* the next job loads with. It is not the release
policy: ``/api/memory-policy`` stays fixed, because Image drops its model after every job, and
the two are deliberately separate routes.

Everything here is driven against the real ``memory_plan``/``apply_memory_plan`` and the real
FastAPI app, so the setting is proved where it is enforced rather than in a reimplementation of
it. The invariant under test is not "these two models behave like this" but **every model
publishes exactly the options its own MemoryPolicy supports** - a model added tomorrow is
covered by these tests without editing them.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(PROJECT_ROOT), str(Path(__file__).resolve().parent)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from fastapi.testclient import TestClient  # noqa: E402

from components.image.app.backend import main as image_main  # noqa: E402
from components.image.app.backend import loading_mode  # noqa: E402
from components.image.app.backend.generation import (  # noqa: E402
    DEFAULT_CONFIGURATION, FLUX2_KLEIN, LOW_RAM_CONFIGURATION, MODELS, Z_IMAGE_TURBO,
    GenerationManager, ImageModel, MemoryPolicy, apply_memory_plan, loading_modes, memory_plan)

# The fixtures are the ones the host-threshold suite already installs: tiny real packages in
# the real layout, and the two mflux modules a low-RAM Z-Image job touches.
from test_image_memory_pressure import fake_z_image_stack, install_tiny_packages  # noqa: E402

GiB = 1024 ** 3
TOKEN = 'imageownercred_' * 3
RUNTIME_ID = 'a' * 64


class ImageMemoryModeFixture(unittest.TestCase):
    """A real manager over tiny installed packages, on an injected host size."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.library = root / 'models'
        self.library.mkdir()
        self.data = root / 'data'
        self.data.mkdir()
        self.flux_manifest, self.z_manifest = install_tiny_packages(self.library)

    def manager(self, memory_bytes: int) -> GenerationManager:
        return GenerationManager(self.data, self.library, self.flux_manifest, RUNTIME_ID,
                                 manifests=[self.flux_manifest, self.z_manifest],
                                 total_memory_bytes=memory_bytes)

    def client(self, memory_bytes: int) -> TestClient:
        return TestClient(image_main.create_app(self.manager(memory_bytes), TOKEN))

    def catalog(self, client: TestClient, memory_bytes: int) -> dict:
        """The real catalog, with the host size pinned so it never depends on this Mac."""

        def sysctl(_command, **_kwargs):
            return str(memory_bytes)

        with patch.object(image_main.subprocess, 'check_output', sysctl):
            entries = client.get('/api/catalog',
                                 headers={'X-Studio-Token': TOKEN}).json()['models']
        return {entry['repo']: entry for entry in entries}

    def settings(self) -> Path:
        return self.data / 'loading_mode.json'

    def run_z_image_job(self, client: TestClient) -> dict:
        """One real job through the app, with mflux faked at the module boundary."""
        calls: list = []
        with (patch.dict(sys.modules, fake_z_image_stack(calls)),
              patch.object(image_main, '_host_eligibility',
                           return_value={'eligible': True, 'macos': '26.6.2'})):
            response = client.post('/api/generate/txt2img', headers={'X-Studio-Token': TOKEN},
                                   json={'repo': Z_IMAGE_TURBO.repo, 'prompt': 'a lantern',
                                         'aspect_ratio': '1:1', 'width': 1024, 'height': 1024,
                                         'seed': 7})
            self.assertEqual(response.status_code, 200, response.text)
            job_id = response.json()['job']['id']
            job = {}
            for _ in range(200):
                job = client.get(f'/api/generate/jobs/{job_id}',
                                 headers={'X-Studio-Token': TOKEN}).json()['job']
                if job['state'] in ('done', 'error', 'cancelled'):
                    break
                time.sleep(0.02)
        return job


class PublishedOptionsMatchTheModelsOwnPolicy(ImageMemoryModeFixture):
    """The invariant: options are derived from the policy, never from a model-name list."""

    def test_the_registry_exercises_both_shapes(self):
        """A guard so the invariant below cannot pass by covering only one shape."""
        bounded = [model.repo for model in MODELS.values()
                   if model.memory.low_ram_below_bytes is not None]
        single = [model.repo for model in MODELS.values()
                  if model.memory.low_ram_below_bytes is None]
        self.assertTrue(bounded, 'no registered model declares a bounded configuration')
        self.assertTrue(single, 'no registered model exercises the single-mode case')

    def test_every_registered_model_publishes_exactly_the_options_its_policy_supports(self):
        for model in MODELS.values():
            supported = model.memory.low_ram_below_bytes is not None
            for host in (0, 8 * GiB, 16 * GiB, 32 * GiB, 128 * GiB):
                for selected in loading_mode.MODES:
                    published = loading_modes(model, host, selected)
                    with self.subTest(model=model.repo, host=host, selected=selected):
                        self.assertEqual(published['supported'], supported)
                        self.assertEqual([option['id'] for option in published['options']],
                                         list(loading_mode.MODES) if supported else [])
                        self.assertEqual(published['threshold_bytes'],
                                         model.memory.low_ram_below_bytes)
                        self.assertEqual(published['selected'], selected)
                        for option in published['options']:
                            self.assertEqual(option['label'],
                                             loading_mode.MODES[option['id']]['label'])
                            self.assertTrue(option['detail'])
                        if supported:
                            self.assertIsNone(published['reason'])
                        else:
                            # One loading mode: say so, and offer nothing the lane discards.
                            self.assertTrue(published['reason'])
                            self.assertEqual(published['effective'], 'default')
                            self.assertNotIn(selected, published['options'])

    def test_the_catalog_publishes_that_derivation_for_every_registered_model(self):
        for host in (8 * GiB, 16 * GiB, 32 * GiB):
            entries = self.catalog(self.client(host), host)
            for model in MODELS.values():
                with self.subTest(host=host, model=model.repo):
                    self.assertEqual(entries[model.repo]['loading_modes'],
                                     loading_modes(model, host, 'auto'))


class PlanAndApplyFollowTheSetting(ImageMemoryModeFixture):
    """`auto`, `low` and `full` against the real plan and the real mflux mapping."""

    def test_auto_picks_the_bounded_configuration_on_a_small_host_and_the_default_on_a_large_one(self):
        calls: list = []
        with patch.dict(sys.modules, fake_z_image_stack(calls)):
            small = apply_memory_plan('z-image', memory_plan(Z_IMAGE_TURBO, 16 * GiB, 'auto'))
            large = apply_memory_plan('z-image', memory_plan(Z_IMAGE_TURBO, 32 * GiB, 'auto'))
        # The bounded configuration goes through the CLI's own mapping...
        self.assertEqual(small, 'memory-saver-sentinel')
        self.assertEqual(len(calls), 1)
        applied = calls[0]
        self.assertEqual((applied['low_ram'], applied['mlx_cache_limit_gb'],
                          applied['vae_tiling'], applied['vae_tile_size']),
                         (True, 1.0, True, None))
        # ...and the default plan never reaches it at all.
        self.assertIsNone(large)

    def test_low_and_full_override_the_host(self):
        calls: list = []
        with patch.dict(sys.modules, fake_z_image_stack(calls)):
            forced_low = apply_memory_plan('z-image', memory_plan(Z_IMAGE_TURBO, 128 * GiB, 'low'))
            forced_full = apply_memory_plan('z-image', memory_plan(Z_IMAGE_TURBO, 8 * GiB, 'full'))
        self.assertEqual(forced_low, 'memory-saver-sentinel')
        self.assertIsNone(forced_full)
        self.assertEqual([call['low_ram'] for call in calls], [True])
        self.assertEqual(loading_modes(Z_IMAGE_TURBO, 128 * GiB, 'low')['effective'], 'low-ram')
        self.assertEqual(loading_modes(Z_IMAGE_TURBO, 8 * GiB, 'full')['effective'], 'default')

    def test_low_cannot_invent_a_bounded_configuration_a_model_does_not_declare(self):
        calls: list = []
        with patch.dict(sys.modules, fake_z_image_stack(calls)):
            self.assertIsNone(
                apply_memory_plan('flux', memory_plan(FLUX2_KLEIN, 8 * GiB, 'low')))
        self.assertEqual(calls, [], 'a single-mode model must not reach the mflux mapping')
        published = loading_modes(FLUX2_KLEIN, 8 * GiB, 'low')
        self.assertFalse(published['supported'])
        self.assertEqual(published['effective'], 'default')

    def test_an_unknown_mode_is_refused_by_name_before_it_can_select_a_configuration(self):
        with self.assertRaisesRegex(ValueError, "unknown memory mode 'turbo'"):
            memory_plan(Z_IMAGE_TURBO, 8 * GiB, 'turbo')
        with self.assertRaisesRegex(ValueError, "'turbo'"):
            loading_mode.save(self.data, 'turbo')
        self.assertEqual(loading_mode.current(self.data), 'auto')
        self.assertFalse(self.settings().exists(), 'a refused value must not be written')

    def test_a_persisted_unknown_value_falls_back_to_auto_rather_than_to_a_configuration(self):
        for stored in ('not json at all', '{"mode": "turbo"}', '[1, 2]', '{"mode": 7}', '{}'):
            with self.subTest(stored=stored):
                self.settings().write_text(stored, encoding='utf-8')
                self.assertEqual(loading_mode.current(self.data), 'auto')
                # The fallback is the auto answer, which is not one fixed configuration: on
                # 16 GiB auto chooses the bounded one, on 32 GiB the default.
                self.assertEqual(
                    memory_plan(Z_IMAGE_TURBO, 16 * GiB,
                                loading_mode.current(self.data))['mode'], 'low-ram')
                self.assertEqual(
                    memory_plan(Z_IMAGE_TURBO, 32 * GiB,
                                loading_mode.current(self.data))['mode'], 'default')


class TheRoutePersistsTheChoiceAndPublishesIt(ImageMemoryModeFixture):
    """`GET/PUT /api/loading-mode`, separate from the fixed release policy."""

    def test_get_publishes_the_three_modes_and_this_machines_selection(self):
        client = self.client(16 * GiB)
        response = client.get('/api/loading-mode', headers={'X-Studio-Token': TOKEN})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['mode'], 'auto')
        self.assertEqual(body['default_mode'], 'auto')
        self.assertEqual([option['id'] for option in body['options']], list(loading_mode.MODES))
        self.assertEqual([option['label'] for option in body['options']],
                         [loading_mode.MODES[key]['label'] for key in loading_mode.MODES])
        self.assertEqual(body['machine_bytes'], 16 * GiB)
        rows = {row['repo']: row for row in body['models']}
        self.assertEqual(set(rows), {model.repo for model in MODELS.values()})
        self.assertTrue(rows[Z_IMAGE_TURBO.repo]['supported'])
        self.assertEqual(rows[Z_IMAGE_TURBO.repo]['effective'], 'low-ram')
        self.assertFalse(rows[FLUX2_KLEIN.repo]['supported'])
        self.assertEqual(rows[FLUX2_KLEIN.repo]['effective'], 'default')
        # The release policy stays its own fixed thing on its own route.
        release = client.get('/api/memory-policy', headers={'X-Studio-Token': TOKEN}).json()
        self.assertTrue(release['fixed'])
        self.assertEqual([row['mode'] for row in release['options']], ['immediate'])

    def test_put_persists_the_choice_and_changes_what_the_catalog_publishes(self):
        manager = self.manager(32 * GiB)
        client = TestClient(image_main.create_app(manager, TOKEN))
        before = self.catalog(client, 32 * GiB)[Z_IMAGE_TURBO.repo]['loading_modes']
        self.assertEqual((before['selected'], before['effective']), ('auto', 'default'))

        saved = client.put('/api/loading-mode', headers={'X-Studio-Token': TOKEN},
                           json={'mode': 'low'})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()['mode'], 'low')
        self.assertEqual(json.loads(self.settings().read_text(encoding='utf-8')),
                         {'mode': 'low'})
        forced = self.catalog(client, 32 * GiB)[Z_IMAGE_TURBO.repo]
        self.assertEqual((forced['loading_modes']['selected'],
                          forced['loading_modes']['effective']), ('low', 'low-ram'))
        self.assertEqual(forced['memory_policy']['mode_for_this_host'], 'low-ram')

        restored = client.put('/api/loading-mode', headers={'X-Studio-Token': TOKEN},
                              json={'mode': 'full'})
        self.assertEqual(restored.json()['mode'], 'full')
        self.assertEqual(self.catalog(client, 32 * GiB)[Z_IMAGE_TURBO.repo]
                         ['loading_modes']['effective'], 'default')

    def test_put_refuses_an_unknown_mode_by_name_and_leaves_the_stored_choice_alone(self):
        client = self.client(32 * GiB)
        headers = {'X-Studio-Token': TOKEN}
        client.put('/api/loading-mode', headers=headers, json={'mode': 'low'})
        for body in ({'mode': 'overnight'}, {}, {'mode': None}):
            with self.subTest(body=body):
                rejected = client.put('/api/loading-mode', headers=headers, json=body)
                self.assertEqual(rejected.status_code, 422)
                self.assertIn('auto, low, full', rejected.json()['detail'])
        self.assertIn('overnight', client.put('/api/loading-mode', headers=headers,
                                              json={'mode': 'overnight'}).json()['detail'])
        self.assertEqual(json.loads(self.settings().read_text(encoding='utf-8')),
                         {'mode': 'low'})
        self.assertEqual(client.get('/api/loading-mode', headers=headers).json()['mode'], 'low')

    def test_a_persisted_unknown_value_runs_the_auto_configuration_on_a_real_job(self):
        small = TestClient(image_main.create_app(self.manager(16 * GiB), TOKEN))
        self.settings().write_text(json.dumps({'mode': 'turbo'}), encoding='utf-8')
        auto_job = self.run_z_image_job(small)
        self.assertEqual(auto_job['state'], 'done', auto_job.get('error'))
        # 16 GiB is below the model's own threshold, so auto picks the bounded configuration
        # and the receipt says the setting it read was `auto`, not the stored word.
        self.assertEqual(auto_job['memory_mode'], 'low-ram')
        self.assertEqual(auto_job['loading_mode_setting'], 'auto')

        large = TestClient(image_main.create_app(self.manager(32 * GiB), TOKEN))
        self.settings().write_text(json.dumps({'mode': 'turbo'}), encoding='utf-8')
        default_job = self.run_z_image_job(large)
        self.assertEqual(default_job['state'], 'done', default_job.get('error'))
        # On 32 GiB auto picks the default: together the two hosts prove the fallback is the
        # auto answer rather than the one configuration a fixed fallback would have chosen.
        self.assertEqual(default_job['memory_mode'], 'default')
        self.assertEqual(default_job['loading_mode_setting'], 'auto')

    def test_a_forced_mode_is_recorded_on_the_job_receipt_as_the_reason(self):
        client = TestClient(image_main.create_app(self.manager(32 * GiB), TOKEN))
        client.put('/api/loading-mode', headers={'X-Studio-Token': TOKEN}, json={'mode': 'low'})
        job = self.run_z_image_job(client)
        self.assertEqual(job['state'], 'done', job.get('error'))
        self.assertEqual(job['memory_mode'], 'low-ram')
        self.assertEqual(job['loading_mode_setting'], 'low')


class TheCatalogIsHonestAboutAModelWithNoBoundedMode(ImageMemoryModeFixture):
    def test_a_model_with_no_bounded_mode_says_the_setting_does_not_apply(self):
        manager = self.manager(8 * GiB)
        client = TestClient(image_main.create_app(manager, TOKEN))
        headers = {'X-Studio-Token': TOKEN}
        client.put('/api/loading-mode', headers=headers, json={'mode': 'low'})

        entry = self.catalog(client, 8 * GiB)[FLUX2_KLEIN.repo]
        modes = entry['loading_modes']
        self.assertFalse(modes['supported'])
        self.assertEqual(modes['options'], [], 'no control may be offered for a single-mode model')
        self.assertEqual(modes['selected'], 'low')
        self.assertEqual(modes['effective'], 'default')
        self.assertIsNone(modes['threshold_bytes'])
        reason = modes['reason']
        # The model's own terms, from its own MemoryPolicy, plus the applicability sentence.
        self.assertIn('qualified at 16 GB', reason)
        self.assertIn('thousands of generations', reason)
        self.assertIn('does not change how it loads', reason)
        self.assertIn("The setting is 'low' on this Mac", reason)
        # The machine-wide route carries the same answer from the same derivation.
        route = client.get('/api/loading-mode', headers=headers).json()
        row = {item['repo']: item for item in route['models']}[FLUX2_KLEIN.repo]
        self.assertFalse(row['supported'])
        self.assertEqual(row['effective'], 'default')
        self.assertEqual(row['reason'], reason)

    def test_the_published_numbers_are_the_models_own_stated_measurements(self):
        for model in MODELS.values():
            published = loading_modes(model, 16 * GiB, 'auto')['measured']
            runtime = model.measured_runtime or {}
            rows = runtime.get('measured') or []
            stated_seconds: dict = {}
            for row in rows:
                stated_seconds.setdefault(row.get('memory_mode') or DEFAULT_CONFIGURATION,
                                          row['seconds'])
            peaks = model.qualification.get('measured_peak_gb') or {}
            with self.subTest(model=model.repo):
                for configuration in (DEFAULT_CONFIGURATION, LOW_RAM_CONFIGURATION):
                    self.assertEqual(published[configuration]['seconds'],
                                     stated_seconds.get(configuration))
                    self.assertEqual(published[configuration]['peak_gb'],
                                     peaks.get(configuration))
                self.assertEqual(published['measured_on'], runtime.get('measured_on'))
                self.assertEqual(published['evidence'], runtime.get('evidence'))
                self.assertIn(model.repo, published['source'])

    def test_a_bounded_model_states_the_trade_in_its_own_numbers(self):
        published = loading_modes(Z_IMAGE_TURBO, 16 * GiB, 'auto')
        measured = published['measured']
        details = {option['id']: option['detail'] for option in published['options']}
        self.assertIn(f"about {measured['low_ram']['peak_gb']:.1f} GB peak",
                      details['low'])
        self.assertIn(f"instead of {measured['default']['peak_gb']:.1f} GB", details['low'])
        self.assertIn(f"about {measured['low_ram']['seconds']:.0f} s", details['low'])
        self.assertIn(f"instead of {measured['default']['seconds']:.0f} s", details['low'])
        self.assertIn(measured['measured_on'], details['low'])
        self.assertIn(measured['scene'], details['low'])
        self.assertIn('bounded configuration below 24 GB', details['auto'])

    def test_a_bounded_model_that_states_no_measurements_publishes_no_number(self):
        """The rule, on a model this build does not ship: state nothing rather than estimate."""
        unmeasured = ImageModel(
            repo='example/unmeasured-4bit', revision='0' * 40, package_id='unmeasured-4bit',
            label='Example MLX 4-bit', family='example', model_class='example:Model',
            defaults={'aspect_ratio': '1:1', 'resolution': '1K', 'steps': 4, 'guidance': 1.0,
                      'seed': -1},
            limits={'min_steps': 1, 'max_steps': 4, 'min_guidance': 0.0, 'max_guidance': 1.0,
                    'min_seed': -1, 'max_seed': 1, 'max_prompt': 10},
            memory=MemoryPolicy(low_ram_below_bytes=16_000_000_000),
            qualification={})
        published = loading_modes(unmeasured, 8 * GiB, 'auto')
        self.assertEqual([option['id'] for option in published['options']],
                         list(loading_mode.MODES))
        for configuration in (DEFAULT_CONFIGURATION, LOW_RAM_CONFIGURATION):
            self.assertIsNone(published['measured'][configuration]['peak_gb'])
            self.assertIsNone(published['measured'][configuration]['seconds'])
        self.assertIn('states no measured peak for the default configuration',
                      published['measured']['note'])
        self.assertIn('no measured runtime for the bounded configuration',
                      published['measured']['note'])
        for option in published['options']:
            self.assertIn('no measured', option['detail'])
            if option['id'] != 'auto':
                self.assertNotIn('instead of', option['detail'],
                                 'a comparison must not be invented from a number nobody stated')


if __name__ == '__main__':
    unittest.main()
