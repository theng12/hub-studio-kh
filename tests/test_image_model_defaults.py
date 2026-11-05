"""Image's per-model generation defaults, and the axis they are deliberately not.

The surface it covers is ``model_defaults`` on the Image worker: ``GET/PUT/DELETE
/api/model-defaults``, the value in force applied to a request that **omits** a parameter, and
that same value published in the catalog — both in the lane contract's per-parameter ``default``
and in ``generation_defaults``, which is what the app pre-fills its form from. The other axis —
which mflux memory *configuration* the next job loads with — is ``loading_mode``, and it is
tested in ``tests/test_image_loading_mode.py``. They are different questions ("what do we ask the
model for" vs "how do we load it"), which is why the two are pinned separately here as well.

Everything is driven against the real managers, the real FastAPI app and the real lane contract,
so a bound that changes in the worker moves these tests with it rather than leaving a literal
behind. Each group carries its own **negative control** — the same call in the direction that must
succeed — so a green suite cannot mean the route simply refuses everything.
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

from components import model_defaults  # noqa: E402
from components.image.app.backend import main as image_main  # noqa: E402
from components.image.app.backend import loading_mode  # noqa: E402
from components.image.app.backend.generation import (  # noqa: E402
    MODELS, SIZE_PRESETS, Z_IMAGE_TURBO, GenerationManager, lane_contract, lane_parameters,
    validate_request)

# The same tiny real packages and the same fake mflux stack the loading-mode suite uses, so a
# job here runs the worker's real submit -> validate -> infer path.
from test_image_memory_pressure import fake_z_image_stack, install_tiny_packages  # noqa: E402

GiB = 1024 ** 3
TOKEN = 'imageownercred_' * 3
RUNTIME_ID = 'a' * 64
HOST_BYTES = 32 * GiB


class ImageModelDefaultsFixture(unittest.TestCase):
    """A real manager over tiny installed packages, with this Mac's size pinned."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.library = root / 'models'
        self.library.mkdir()
        self.data = root / 'data'
        self.data.mkdir()
        self.flux_manifest, self.z_manifest = install_tiny_packages(self.library)
        self.manager = self.manager_for(HOST_BYTES)
        self.client = TestClient(image_main.create_app(self.manager, TOKEN))
        self.headers = {'X-Studio-Token': TOKEN}

    def manager_for(self, memory_bytes: int) -> GenerationManager:
        return GenerationManager(self.data, self.library, self.flux_manifest, RUNTIME_ID,
                                 manifests=[self.flux_manifest, self.z_manifest],
                                 total_memory_bytes=memory_bytes)

    def catalog(self, client: TestClient | None = None, memory_bytes: int = HOST_BYTES) -> dict:
        """The real catalog, with the host size pinned so it never depends on this Mac."""

        def sysctl(_command, **_kwargs):
            return str(memory_bytes)

        with patch.object(image_main.subprocess, 'check_output', sysctl):
            entries = (client or self.client).get('/api/catalog',
                                                  headers=self.headers).json()['models']
        return {entry['repo']: entry for entry in entries}

    def published_parameters(self, repo: str) -> dict:
        """One model's published parameters, from the lane contract itself."""
        return {parameter['name']: parameter
                for parameter in lane_contract(MODELS[repo])['parameters']}

    def model_defaults_route(self, client: TestClient | None = None) -> dict:
        response = (client or self.client).get('/api/model-defaults', headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def rows(self, client: TestClient | None = None) -> dict:
        return {row['repo']: row for row in self.model_defaults_route(client)['models']}

    def lane_stated_defaults(self, repo: str) -> dict:
        """What the lane itself states for this model, read from the published parameters."""
        return {name: parameter['default']
                for name, parameter in self.published_parameters(repo).items()
                if 'default' in parameter}

    def put(self, repo: str, values: dict, client: TestClient | None = None):
        return (client or self.client).put('/api/model-defaults', headers=self.headers,
                                           json={'repo': repo, 'values': values})

    def delete(self, repo: str, *, query: bool = False, client: TestClient | None = None):
        client = client or self.client
        if query:
            return client.request('DELETE', f'/api/model-defaults?repo={repo}',
                                  headers=self.headers)
        return client.request('DELETE', '/api/model-defaults', headers=self.headers,
                              json={'repo': repo})

    def settings_file(self) -> Path:
        return model_defaults.settings_file(self.data)

    def run_z_image_job(self, request: dict, client: TestClient | None = None) -> tuple[dict, list]:
        """One real job through the app, with mflux faked at the module boundary."""
        calls: list = []
        with (patch.dict(sys.modules, fake_z_image_stack(calls)),
              patch.object(image_main, '_host_eligibility',
                           return_value={'eligible': True, 'macos': '26.6.2'})):
            response = (client or self.client).post('/api/generate/txt2img',
                                                    headers=self.headers, json=request)
            self.assertEqual(response.status_code, 200, response.text)
            job_id = response.json()['job']['id']
            job = {}
            for _ in range(200):
                job = (client or self.client).get(
                    f'/api/generate/jobs/{job_id}', headers=self.headers).json()['job']
                if job['state'] in ('done', 'error', 'cancelled'):
                    break
                time.sleep(0.02)
        return job, calls

    def z_request(self, **changes) -> dict:
        """A Z-Image request that supplies geometry and a seed but **no** steps."""
        return {'repo': Z_IMAGE_TURBO.repo, 'prompt': 'a lantern', 'aspect_ratio': '1:1',
                'width': 1024, 'height': 1024, 'seed': 7, **changes}


class TheRouteRoundTripsTests(ImageModelDefaultsFixture):
    """GET before anything is set is the lane's own; PUT stores; GET reflects; DELETE restores."""

    def test_get_before_anything_is_set_is_exactly_what_the_lane_states(self) -> None:
        payload = self.model_defaults_route()
        self.assertEqual(payload['settings_file'], model_defaults.SETTINGS_NAME)
        self.assertEqual({row['repo'] for row in payload['models']}, set(MODELS))
        for row in payload['models']:
            stated = self.lane_stated_defaults(row['repo'])
            with self.subTest(repo=row['repo']):
                # Non-vacuous: the lane publishes something for this model to default.
                self.assertTrue(stated, row['repo'])
                self.assertEqual(row['stated'], stated)
                self.assertEqual(row['defaults'], stated,
                                 'nothing has been chosen on this machine yet')
                self.assertEqual(row['chosen'], {})
                self.assertEqual(row['not_used'], {})
                # The offered surface is the shared module's own rule over the lane's published
                # parameters (`required` parameters and one-option enums have nothing to choose),
                # so a control cannot be rendered for a parameter this worker would refuse.
                self.assertEqual(
                    row['parameters'],
                    model_defaults.editable(lane_parameters(MODELS[row['repo']])))
                self.assertEqual(
                    [parameter['name'] for parameter in row['parameters']],
                    [parameter['name'] for parameter in model_defaults.editable(
                        lane_contract(MODELS[row['repo']])['parameters'])])

    def test_put_stores_it_and_get_reflects_it_for_that_model_only(self) -> None:
        stored = self.put(Z_IMAGE_TURBO.repo, {'steps': 12})
        self.assertEqual(stored.status_code, 200, stored.text)
        rows = {row['repo']: row for row in stored.json()['models']}
        self.assertEqual(rows[Z_IMAGE_TURBO.repo]['chosen'], {'steps': 12})
        self.assertEqual(rows[Z_IMAGE_TURBO.repo]['defaults']['steps'], 12)
        self.assertEqual(rows[Z_IMAGE_TURBO.repo]['stated']['steps'],
                         self.lane_stated_defaults(Z_IMAGE_TURBO.repo)['steps'])
        for other in MODELS:
            if other != Z_IMAGE_TURBO.repo:
                self.assertEqual(rows[other]['chosen'], {},
                                 'a default belongs to one model, never to the lane')

        # A fresh GET, and the file itself, say the same thing.
        self.assertEqual(self.rows()[Z_IMAGE_TURBO.repo]['defaults']['steps'], 12)
        self.assertEqual(json.loads(self.settings_file().read_text(encoding='utf-8')),
                         {Z_IMAGE_TURBO.repo: {'steps': 12}})

    def test_the_catalog_publishes_the_same_number_the_route_holds(self) -> None:
        self.assertEqual(self.put(Z_IMAGE_TURBO.repo, {'steps': 12}).status_code, 200)
        published = {parameter['name']: parameter
                     for parameter in self.catalog()[Z_IMAGE_TURBO.repo]
                     ['lane_contract']['parameters']}
        self.assertEqual(published['steps']['default'],
                         self.rows()[Z_IMAGE_TURBO.repo]['defaults']['steps'],
                         'the catalog and the setting must be one answer, not two')
        self.assertEqual(published['steps']['default'], 12)
        # The bounds are still the lane's, unchanged by the stored value.
        self.assertEqual(published['steps']['minimum'],
                         self.published_parameters(Z_IMAGE_TURBO.repo)['steps']['minimum'])
        self.assertEqual(published['steps']['maximum'],
                         self.published_parameters(Z_IMAGE_TURBO.repo)['steps']['maximum'])

    def test_delete_with_a_body_or_a_query_returns_the_model_to_the_lanes_own(self) -> None:
        for use_query in (False, True):
            with self.subTest(query=use_query):
                self.assertEqual(self.put(Z_IMAGE_TURBO.repo, {'steps': 12}).status_code, 200)
                cleared = self.delete(Z_IMAGE_TURBO.repo, query=use_query)
                self.assertEqual(cleared.status_code, 200, cleared.text)
                row = {item['repo']: item for item in cleared.json()['models']}[Z_IMAGE_TURBO.repo]
                self.assertEqual(row['defaults'], self.lane_stated_defaults(Z_IMAGE_TURBO.repo))
                self.assertEqual(self.rows()[Z_IMAGE_TURBO.repo]['chosen'], {})
                stored = (json.loads(self.settings_file().read_text(encoding='utf-8'))
                          if self.settings_file().exists() else {})
                self.assertEqual(stored, {},
                                 'deleting the only chosen value leaves nothing behind')

    def test_every_offered_control_default_is_a_value_the_worker_accepts(self) -> None:
        """The contract cannot advertise a number ``model_defaults`` would refuse.

        Both come from one ``resolved()`` call, so this is the invariant made checkable: every
        control the editor would render, at its published default, is fed straight back through
        the route and must be accepted.
        """
        self.assertEqual(self.put(Z_IMAGE_TURBO.repo, {'steps': 12, 'guidance': 2.5}).status_code,
                         200)
        for row in self.model_defaults_route()['models']:
            settable = {parameter['name']: row['defaults'][parameter['name']]
                        for parameter in row['parameters']}
            with self.subTest(repo=row['repo']):
                accepted = self.put(row['repo'], settable)
                self.assertEqual(accepted.status_code, 200, accepted.text)

    def test_the_routes_require_the_owner_credential(self) -> None:
        for call in (lambda: self.client.get('/api/model-defaults'),
                     lambda: self.client.put('/api/model-defaults', json={'repo': 'x'}),
                     lambda: self.client.request('DELETE', '/api/model-defaults')):
            self.assertEqual(call().status_code, 401)


class AParameterWithNoControlIsStillAppliedTests(ImageModelDefaultsFixture):
    """`resolution` publishes a one-option enum: nothing to choose, and a job still carries it.

    The editor offers ``model_defaults.editable`` — no required parameters, no one-option enums.
    The job needs the *resolved* map, which is every parameter the lane defaults. Building the
    request from the settable subset would silently drop ``resolution``, and with it the preset
    geometry ``aspect_ratio`` derives.
    """

    def test_the_lane_publishes_no_control_for_it_and_the_route_will_not_store_one(self) -> None:
        row = self.rows()[Z_IMAGE_TURBO.repo]
        offered = [parameter['name'] for parameter in row['parameters']]
        self.assertNotIn('resolution', offered)
        self.assertIn('resolution', row['defaults'],
                      'no control is not the same as no value: the job still sends it')
        refused = self.put(Z_IMAGE_TURBO.repo, {'resolution': '1K'})
        self.assertEqual(refused.status_code, 422)
        self.assertIn('resolution', refused.json()['detail'])
        self.assertIn('publishes no such settable parameter',
                      refused.json()['detail']['resolution'])
        # The control is real for a parameter with more than one option.
        self.assertIn('aspect_ratio', offered)

    def test_a_job_that_omits_it_still_carries_it_and_the_geometry_it_derives(self) -> None:
        # A stored aspect ratio drives the width and height of a request that names neither.
        self.assertEqual(self.put(Z_IMAGE_TURBO.repo, {'aspect_ratio': '9:16'}).status_code, 200)
        applied = validate_request({'repo': Z_IMAGE_TURBO.repo, 'prompt': 'a lantern'},
                                   data_dir=self.data)
        published = {parameter['name']: parameter
                     for parameter in self.catalog()[Z_IMAGE_TURBO.repo]
                     ['lane_contract']['parameters']}
        self.assertEqual(applied['aspect_ratio'], published['aspect_ratio']['default'])
        self.assertEqual((applied['width'], applied['height']), SIZE_PRESETS['9:16'])
        self.assertEqual(applied['resolution'],
                         self.rows()[Z_IMAGE_TURBO.repo]['defaults']['resolution'])
        # And the whole path: the job record carries both, from the resolved map.
        job, calls = self.run_z_image_job({'repo': Z_IMAGE_TURBO.repo, 'prompt': 'a lantern'})
        self.assertEqual(job['state'], 'done', job.get('error'))
        self.assertEqual(job['params']['resolution'], applied['resolution'])
        self.assertEqual((job['params']['width'], job['params']['height']), SIZE_PRESETS['9:16'])
        generated = [call for call in calls if call['event'] == 'generate']
        self.assertEqual([(call['width'], call['height']) for call in generated],
                         [SIZE_PRESETS['9:16']])


class AValueOutsideTheLaneIsRefusedByNameTests(ImageModelDefaultsFixture):
    """The bound is the lane's own published one, so a bound change moves this test."""

    def test_a_bound_crossing_is_refused_by_name_while_the_bound_itself_is_accepted(self) -> None:
        for repo in MODELS:
            parameter = self.published_parameters(repo)['steps']
            minimum, maximum = parameter['minimum'], parameter['maximum']
            with self.subTest(repo=repo):
                # Control: the edge of the published range is exactly what the lane accepts, so a
                # refusal below is caused by the bound and not by a route that refuses everything.
                self.assertEqual(self.put(repo, {'steps': maximum}).status_code, 200)
                self.assertEqual(self.rows()[repo]['defaults']['steps'], maximum)
                self.assertEqual(self.delete(repo).status_code, 200)
                for value, reason, bound in ((maximum + 1, 'above the lane', maximum),
                                             (minimum - 1, 'below the lane', minimum)):
                    refused = self.put(repo, {'steps': value})
                    self.assertEqual(refused.status_code, 422, refused.text)
                    detail = refused.json()['detail']
                    self.assertIn('steps', detail)
                    self.assertIn(reason, detail['steps'])
                    self.assertIn(str(bound), detail['steps'],
                                  'the refusal names the bound the worker enforces')
                    # Not stored: the model stays on the lane's own default for that parameter.
                    self.assertEqual(self.rows()[repo]['defaults']['steps'],
                                     self.lane_stated_defaults(repo)['steps'])

    def test_an_enum_outside_the_published_options_is_refused(self) -> None:
        published = self.published_parameters(Z_IMAGE_TURBO.repo)['aspect_ratio']['enum']
        outsider = next(candidate for candidate in ('7:3', '11:7', '0:0')
                        if candidate not in published)
        refused = self.put(Z_IMAGE_TURBO.repo, {'aspect_ratio': outsider})
        self.assertEqual(refused.status_code, 422)
        self.assertIn('aspect_ratio', refused.json()['detail'])
        self.assertIn('is not one of', refused.json()['detail']['aspect_ratio'])
        self.assertEqual(self.rows()[Z_IMAGE_TURBO.repo]['defaults']['aspect_ratio'],
                         self.lane_stated_defaults(Z_IMAGE_TURBO.repo)['aspect_ratio'])

    def test_a_parameter_the_lane_does_not_publish_has_nothing_to_default(self) -> None:
        refused = self.put(Z_IMAGE_TURBO.repo, {'cfg_scale': 3.5})
        self.assertEqual(refused.status_code, 422)
        self.assertIn('cfg_scale', refused.json()['detail'])
        self.assertIn('publishes no such settable parameter',
                      refused.json()['detail']['cfg_scale'])

    def test_a_refused_save_leaves_the_values_already_stored_alone(self) -> None:
        self.assertEqual(self.put(Z_IMAGE_TURBO.repo, {'steps': 12}).status_code, 200)
        refused = self.put(Z_IMAGE_TURBO.repo,
                           {'steps': self.published_parameters(Z_IMAGE_TURBO.repo)
                            ['steps']['maximum'] + 1})
        self.assertEqual(refused.status_code, 422)
        self.assertEqual(self.rows()[Z_IMAGE_TURBO.repo]['defaults']['steps'], 12,
                         'a refused save must not clear or replace what was already chosen')

    def test_an_unknown_repo_is_a_named_refusal_not_a_silent_no_op(self) -> None:
        for response in (self.put('someone/not-registered', {'steps': 4}),
                         self.delete('someone/not-registered')):
            with self.subTest(status=response.status_code):
                self.assertEqual(response.status_code, 422)
                self.assertIn('not-registered', response.json()['detail'])
                self.assertIn(Z_IMAGE_TURBO.repo, response.json()['detail'],
                              'the refusal names the models this worker does serve')
        self.assertFalse(self.settings_file().exists())


class WhatAppliesToARequestThatOmitsItTests(ImageModelDefaultsFixture):
    """The stored default is the answer only where the request has none."""

    def test_a_parameter_the_request_supplies_is_untouched_by_a_stored_default(self) -> None:
        self.assertEqual(self.put(Z_IMAGE_TURBO.repo, {'steps': 12}).status_code, 200)
        # The validator, on the worker's own state directory...
        self.assertEqual(validate_request(self.z_request(steps=5), data_dir=self.data)['steps'], 5)
        # ...and the whole path, with the value mflux actually received.
        job, calls = self.run_z_image_job(self.z_request(steps=5))
        self.assertEqual(job['state'], 'done', job.get('error'))
        self.assertEqual(job['params']['steps'], 5)
        generated = [call for call in calls if call['event'] == 'generate']
        self.assertEqual([call['steps'] for call in generated], [5],
                         'the stored default must not override an explicit request')

    def test_a_parameter_the_request_omits_gets_the_stored_default(self) -> None:
        self.assertEqual(self.put(Z_IMAGE_TURBO.repo, {'steps': 12}).status_code, 200)
        job, calls = self.run_z_image_job(self.z_request())
        self.assertEqual(job['state'], 'done', job.get('error'))
        self.assertEqual(job['params']['steps'], 12)
        generated = [call for call in calls if call['event'] == 'generate']
        self.assertEqual([call['steps'] for call in generated], [12])

    def test_what_is_applied_is_what_the_catalog_publishes_as_that_default(self) -> None:
        """Read both and compare: one number in the setting, the catalog and the job."""
        self.assertEqual(self.put(Z_IMAGE_TURBO.repo, {'steps': 12, 'guidance': 2.5}).status_code,
                         200)
        published = {parameter['name']: parameter
                     for parameter in self.catalog()[Z_IMAGE_TURBO.repo]
                     ['lane_contract']['parameters']}
        applied = validate_request(self.z_request(), data_dir=self.data)
        for name in ('steps', 'guidance'):
            with self.subTest(parameter=name):
                self.assertEqual(published[name]['default'], applied[name])
                self.assertEqual(published[name]['default'],
                                 self.rows()[Z_IMAGE_TURBO.repo]['defaults'][name])
        job, _calls = self.run_z_image_job(self.z_request())
        self.assertEqual(job['state'], 'done', job.get('error'))
        self.assertEqual(job['params']['steps'], published['steps']['default'])
        self.assertEqual(job['params']['guidance'], published['guidance']['default'])

    def test_generation_defaults_and_the_lane_contract_are_one_answer(self) -> None:
        """The catalog says the same number twice, so both halves must move together.

        ``generation_defaults`` is what the app pre-fills its form from and sends with every
        request; the lane contract's per-parameter ``default`` is what a scheduler reads. Two
        answers to one question is how a stored default reaches one of them and not the other.
        """
        stored = {'steps': 12, 'guidance': 2.5, 'aspect_ratio': '9:16', 'seed': 7}
        self.assertEqual(self.put(Z_IMAGE_TURBO.repo, stored).status_code, 200)
        entry = self.catalog(memory_bytes=16 * GiB)[Z_IMAGE_TURBO.repo]
        defaults, published = entry['generation_defaults'], {
            parameter['name']: parameter
            for parameter in entry['lane_contract']['parameters']}
        for name, value in stored.items():
            with self.subTest(parameter=name):
                self.assertEqual(defaults[name], value)
                self.assertEqual(published[name]['default'], value,
                                 'the two published halves must agree with each other')
        # The derived fields follow the parameter they are derived from.
        self.assertEqual((defaults['width'], defaults['height']), SIZE_PRESETS['9:16'])
        self.assertEqual([size['aspect_ratio'] for size in entry['sizes'] if size['default']],
                         ['9:16'],
                         'the size table marks the ratio in force, not the one the model shipped '
                         'with')
        # ...and the engine receives those numbers for a job that omits them.
        job, calls = self.run_z_image_job({'repo': Z_IMAGE_TURBO.repo, 'prompt': 'a lantern'})
        self.assertEqual(job['state'], 'done', job.get('error'))
        for name in ('steps', 'guidance', 'aspect_ratio'):
            with self.subTest(parameter=name, where='job'):
                self.assertEqual(job['params'][name], defaults[name])
        self.assertEqual((job['params']['width'], job['params']['height']),
                         (defaults['width'], defaults['height']))
        generated = [call for call in calls if call['event'] == 'generate'][0]
        self.assertEqual(generated['steps'], defaults['steps'])
        self.assertEqual(generated['guidance'], defaults['guidance'])
        self.assertEqual((generated['width'], generated['height']),
                         (defaults['width'], defaults['height']))

    def test_a_model_nobody_has_chosen_for_still_generates_its_own_defaults(self) -> None:
        """The control for the tests above: with nothing stored, nothing moves."""
        entry = self.catalog()[Z_IMAGE_TURBO.repo]
        self.assertEqual(entry['generation_defaults']['steps'],
                         self.lane_stated_defaults(Z_IMAGE_TURBO.repo)['steps'])
        self.assertEqual({size['aspect_ratio'] for size in entry['sizes'] if size['default']},
                         {Z_IMAGE_TURBO.defaults['aspect_ratio']})
        job, _calls = self.run_z_image_job(self.z_request())
        self.assertEqual(job['state'], 'done', job.get('error'))
        self.assertEqual(job['params']['steps'],
                         self.lane_stated_defaults(Z_IMAGE_TURBO.repo)['steps'])

    def test_a_stored_default_the_request_contradicts_is_refused_like_any_request(self) -> None:
        """An explicit value outside the lane's bound is still refused, stored default or not."""
        self.assertEqual(self.put(Z_IMAGE_TURBO.repo, {'steps': 12}).status_code, 200)
        with self.assertRaises(ValueError):
            validate_request(self.z_request(steps=101), data_dir=self.data)


class TheTwoAxesStaySeparateTests(ImageModelDefaultsFixture):
    """`model_defaults` is what to ask for; `loading_mode` is how to load it."""

    def test_the_loading_mode_routes_and_the_release_policy_are_unchanged(self) -> None:
        modes = self.client.get('/api/loading-mode', headers=self.headers)
        self.assertEqual(modes.status_code, 200)
        self.assertEqual([option['id'] for option in modes.json()['options']],
                         list(loading_mode.MODES))
        self.assertEqual(modes.json()['mode'], loading_mode.DEFAULT_MODE)
        self.assertEqual(self.client.put('/api/loading-mode', headers=self.headers,
                                         json={'mode': 'low'}).status_code, 200)
        policy = self.client.get('/api/memory-policy', headers=self.headers)
        self.assertEqual(policy.status_code, 200)
        self.assertFalse(policy.json()['fixed'])
        self.assertEqual([row['mode'] for row in policy.json()['options']],
                         ['auto', 'performance', 'balanced', 'memory_saver', 'immediate'])
        self.assertEqual(self.client.put('/api/memory-policy', headers=self.headers,
                                         json={'mode': 'overnight'}).status_code, 422)

    def test_setting_a_model_default_does_not_change_which_configuration_loads(self) -> None:
        before = self.catalog()[Z_IMAGE_TURBO.repo]['loading_modes']
        self.assertEqual(self.put(Z_IMAGE_TURBO.repo, {'steps': 12}).status_code, 200)
        after = self.catalog()[Z_IMAGE_TURBO.repo]['loading_modes']
        self.assertEqual(after, before,
                         'a generation default is not a memory configuration, and setting one '
                         'must not change what loads')
        self.assertEqual(loading_mode.current(self.data), loading_mode.DEFAULT_MODE)
        self.assertFalse(loading_mode.settings_file(self.data).exists(),
                         'the two settings live in two files, and choosing one writes only its own')

    def test_choosing_a_loading_mode_does_not_change_a_model_default(self) -> None:
        stated = self.lane_stated_defaults(Z_IMAGE_TURBO.repo)
        self.assertEqual(self.client.put('/api/loading-mode', headers=self.headers,
                                         json={'mode': 'low'}).status_code, 200)
        row = self.rows()[Z_IMAGE_TURBO.repo]
        self.assertEqual(row['defaults'], stated)
        self.assertEqual(row['chosen'], {})
        self.assertFalse(self.settings_file().exists())

    def test_the_two_routes_answer_different_questions(self) -> None:
        defaults = self.model_defaults_route()
        modes = self.client.get('/api/loading-mode', headers=self.headers).json()
        self.assertIn('settings_file', defaults)
        self.assertNotIn('mode', defaults)
        self.assertNotIn('settings_file', modes)
        self.assertEqual(defaults['settings_file'], model_defaults.SETTINGS_NAME)
        self.assertEqual(loading_mode.settings_file(self.data).name,
                         loading_mode.SETTINGS_NAME)


class TheIgnoredMarkerIsAFieldTests(ImageModelDefaultsFixture):
    """A parameter the lane accepts and then ignores says so twice: a field and a sentence.

    Z-Image-Turbo runs without classifier-free guidance, so it accepts ``guidance`` and drops it.
    The app has to explain that control rather than pretend it works — and it must match a
    *field*, not a sentence, because a consumer keyed to prose stops matching the moment the prose
    is reworded. The two are published together so they cannot drift apart.
    """

    def test_every_marked_parameter_says_so_and_every_one_that_says_so_is_marked(self) -> None:
        for model in MODELS.values():
            parameters = lane_parameters(model)
            with self.subTest(repo=model.repo):
                for parameter in parameters:
                    marked = parameter.get('ignored') is True
                    prose = 'ignored' in (parameter.get('note') or '')
                    self.assertEqual(
                        marked, prose,
                        f'{parameter["name"]}: the field a consumer matches and the note a person '
                        'reads must say the same thing')
                guidance = next(parameter for parameter in parameters
                                if parameter['name'] == 'guidance')
                self.assertEqual(guidance.get('ignored', False), model.guidance_ignored,
                                 "the row's flag and the parameter's field are one fact")
        # A guard, so the loop above cannot pass by covering only one shape.
        ignored = [model.repo for model in MODELS.values() if model.guidance_ignored]
        self.assertTrue(ignored, 'no registered model exercises the ignored case')
        self.assertLess(len(ignored), len(MODELS),
                        'no registered model exercises the not-ignored case')

    def test_a_parameter_with_no_ignored_key_is_not_ignored(self) -> None:
        for model in MODELS.values():
            unmarked = [parameter for parameter in lane_parameters(model)
                        if not parameter.get('ignored')]
            with self.subTest(repo=model.repo):
                self.assertTrue(unmarked)
                for parameter in unmarked:
                    self.assertNotIn('ignored', parameter,
                                     'the field is published only when true: absent is not true')
        # The control: it really is published for the parameter this model ignores.
        ignored = {parameter['name']: parameter
                   for parameter in lane_parameters(Z_IMAGE_TURBO)
                   if parameter.get('ignored')}
        self.assertEqual(sorted(ignored), ['guidance'])
        self.assertIs(ignored['guidance']['ignored'], True)
        self.assertIn('ignored', ignored['guidance']['note'])

    def test_the_catalog_and_the_editor_publish_it_on_the_parameters_own_row(self) -> None:
        for repo, entry in self.catalog().items():
            published = {parameter['name']: parameter
                         for parameter in entry['lane_contract']['parameters']}
            with self.subTest(repo=repo):
                self.assertEqual(published['guidance'].get('ignored', False),
                                 entry['guidance_ignored'],
                                 "the row's flag and the parameter's field are one fact")
                for name, parameter in published.items():
                    self.assertEqual(parameter.get('ignored', False),
                                     'ignored' in (parameter.get('note') or ''), name)
        # And it reaches the surface the Models tab renders controls from.
        for repo, row in self.rows().items():
            parameters = {parameter['name']: parameter for parameter in row['parameters']}
            with self.subTest(repo=repo, where='editor'):
                self.assertEqual(parameters['guidance'].get('ignored', False),
                                 MODELS[repo].guidance_ignored)



def setUpModule() -> None:
    # Jobs here reach the start door and the memory guard: report normal pressure so no run
    # depends on this Mac's live host state (a Mac mid-render can be critical).
    global _pressure
    from _image_pressure import pressure
    _pressure = pressure(1)


def tearDownModule() -> None:
    _pressure.stop()


if __name__ == '__main__':
    unittest.main()
