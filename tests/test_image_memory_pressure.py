"""Which memory configuration the Image worker chooses, on which Mac, and why.

The point of this file is the decision, not a benchmark: Z-Image-Turbo's default footprint was
measured on a 32 GiB machine (17.67 GB at 1280x720) where MLX's buffer cache grows into free
RAM, so that number partly describes the host. Its bounded configuration measures 7.12-7.24 GB
on the same host — a figure that fits the fleet's 16 GB tier with the OS and the app resident.
The worker therefore chooses the bounded configuration from the Mac's actual memory on every
tier below the one where the default was measured, and never claims anything for the 8 GB tier,
which is still being measured.

FLUX.2 Klein 4-bit followed: its default peaked at 12.1 GB of MLX memory on a 32 GB Mac and 9.8 GB on
the 8 GB M1 (about 4 GB of swap per image), nine of them in the VAE decode. It runs staged below
12 GB and lean below 24 GB; 24 GB and up keep the configuration it shipped with
(``tests/test_image_staging.py`` pins the tiers).
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402
import _image_pressure  # noqa: E402
from components.image.app.backend import generation  # noqa: E402

from components import model_packages  # noqa: E402
from components.image.app.backend import main as image_main  # noqa: E402
from components.image.app.backend.generation import (  # noqa: E402
    FLUX2_KLEIN, MODEL_ID, QUALIFIED_MINIMUM_MEMORY_BYTES, Z_IMAGE_TURBO,
    FLUX2_LEAN_BELOW_BYTES, FLUX2_LOW_RAM_BELOW_BYTES, Z_IMAGE_LOW_RAM_BELOW_BYTES,
    GenerationManager, apply_memory_plan, memory_plan,
)

GiB = 1024 ** 3
TOKEN = 'imageownercred_' * 3
RUNTIME_ID = 'a' * 64
TINY_FILES = ('transformer/model.safetensors.index.json', 'tokenizer/tokenizer.json')


def install_tiny_packages(library: Path) -> tuple[dict, dict]:
    """Install a tiny stand-in for each registered package, in the real layout."""
    from hashlib import sha256
    manifests = []
    for model in (FLUX2_KLEIN, Z_IMAGE_TURBO):
        files = [{'path': f'model/{relative}', 'source_path': relative, 'size': 0,
                  'sha256': '0' * 64, 'urls': []} for relative in TINY_FILES]
        manifest = {'schema_version': 1, 'package_id': model.package_id, 'model_id': model.repo,
                    'revision': model.revision, 'engine': 'image-mflux', 'minimum_macos': '14.0',
                    'files': files}
        source = library.parent / f'source-{model.package_id}'
        for entry in manifest['files']:
            value = (entry['path'] + '\n').encode()
            entry['size'] = len(value)
            entry['sha256'] = sha256(value).hexdigest()
            target = source / entry['source_path']
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(value)
        model_packages.validate_manifest(manifest)
        model_packages.import_folder(manifest, source, library)
        manifests.append(manifest)
    return manifests[0], manifests[1]


def fake_z_image_stack(calls: list) -> dict:
    """The two mflux modules a low-RAM Z-Image job touches, and nothing else."""
    names = ('mflux', 'mflux.models', 'mflux.models.z_image', 'mflux.models.z_image.variants',
             'mflux.models.z_image.variants.z_image',
             'mflux.callbacks', 'mflux.callbacks.callback_manager')
    modules = {name: types.ModuleType(name) for name in names}

    class ZImage:
        def __init__(self, *, model_path, quantize, lora_paths, lora_scales):
            calls.append({'event': 'init', 'model_path': model_path})

        def generate_image(self, *, seed, prompt, num_inference_steps, height, width, guidance):
            calls.append({'event': 'generate', 'steps': num_inference_steps, 'width': width,
                          'height': height, 'guidance': guidance})
            from PIL import Image

            class Result:
                def save(self, path, overwrite):
                    Image.new('RGB', (width, height), (9, 9, 9)).save(path, format='PNG')
            return Result()

    # The sentinel stands in for mflux's MemorySaver; only the mapping is under test.
    class CallbackManager:
        @staticmethod
        def _register_memory_saver(args, model):
            calls.append({'event': 'memory-saver', 'model': model,
                          'low_ram': args.low_ram,
                          'mlx_cache_limit_gb': args.mlx_cache_limit_gb,
                          'vae_tiling': args.vae_tiling,
                          'vae_tile_size': args.vae_tile_size})
            return 'memory-saver-sentinel'

    modules['mflux.models.z_image.variants.z_image'].ZImage = ZImage
    modules['mflux.callbacks.callback_manager'].CallbackManager = CallbackManager
    return modules


class MemoryPlanTests(unittest.TestCase):
    """The plan itself: pure policy, no model and no host."""

    def test_flux_keeps_the_configuration_it_shipped_with_from_24gb(self):
        for host in (24 * GiB, 32 * GiB, 128 * GiB):
            plan = memory_plan(FLUX2_KLEIN, host)
            self.assertEqual(plan['mode'], 'default', host)
            self.assertIsNone(plan['mlx_cache_limit_gb'])
            self.assertFalse(plan['vae_tiling'])
            self.assertFalse(plan['staged'])
            self.assertFalse(plan['bounded_vae_decode'])
        # And a default plan applies nothing at all - it must not even reach for mflux.
        with patch.dict(sys.modules, {}):
            self.assertIsNone(apply_memory_plan(object(), memory_plan(FLUX2_KLEIN, 32 * GiB)))
        self.assertNotIn('mflux', sys.modules)

    def test_z_image_takes_the_bounded_configuration_below_the_measured_tier(self):
        for host in (8 * GiB, 16 * GiB):
            plan = memory_plan(Z_IMAGE_TURBO, host)
            self.assertEqual(plan['mode'], 'low-ram', host)
            self.assertTrue(plan['low_ram'])
            # `--low-ram`'s mapping: implicit tiling, and the model's own MLX cache cap (0.5 GB, measured).
            self.assertEqual(plan['mlx_cache_limit_gb'], 0.5)
            self.assertTrue(plan['vae_tiling'])
            # No explicit `--vae-tile-size`: low-RAM's own 512-pixel default is what was measured.
            self.assertIsNone(plan['vae_tile_size'])
        for host in (24 * GiB, 32 * GiB, 128 * GiB):
            plan = memory_plan(Z_IMAGE_TURBO, host)
            self.assertEqual(plan['mode'], 'default', host)
            self.assertIsNone(plan['mlx_cache_limit_gb'])
            self.assertFalse(plan['vae_tiling'])

    def test_the_tier_boundary_is_one_named_constant_between_the_fleet_tiers(self):
        """So the 8 GB numbers can move it without hunting for a magic number."""
        self.assertEqual(Z_IMAGE_TURBO.memory.low_ram_below_bytes, Z_IMAGE_LOW_RAM_BELOW_BYTES)
        self.assertGreater(Z_IMAGE_LOW_RAM_BELOW_BYTES, 16 * GiB)
        self.assertLessEqual(Z_IMAGE_LOW_RAM_BELOW_BYTES, 24 * GiB)
        self.assertEqual(FLUX2_KLEIN.memory.low_ram_below_bytes, FLUX2_LOW_RAM_BELOW_BYTES)
        self.assertLess(FLUX2_LOW_RAM_BELOW_BYTES, 16 * GiB, 'the 16 GB tier is lean, not staged')
        self.assertLessEqual(FLUX2_LEAN_BELOW_BYTES, 24 * GiB)
        # A host whose memory could not be read is not below anything: no knobs, no guessing.
        self.assertEqual(memory_plan(Z_IMAGE_TURBO, 0)['mode'], 'default')

    def test_the_low_ram_plan_is_applied_through_the_mflux_cli_mapping(self):
        calls: list = []
        with patch.dict(sys.modules, fake_z_image_stack(calls)):
            result = apply_memory_plan('model-instance', memory_plan(Z_IMAGE_TURBO, 16 * GiB))
        self.assertEqual(result, 'memory-saver-sentinel')
        self.assertEqual(len(calls), 1)
        applied = calls[0]
        # The four flags the CLI derives from --low-ram, on the instance that was loaded.
        self.assertEqual((applied['low_ram'], applied['mlx_cache_limit_gb'],
                          applied['vae_tiling'], applied['vae_tile_size']),
                         (True, 0.5, True, None))
        self.assertEqual(applied['model'], 'model-instance')


class MemoryCatalogTests(unittest.TestCase):
    """What the operator sees, per model, per machine tier."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.library = root / 'models'
        self.library.mkdir()
        self.data = root / 'data'
        self.data.mkdir()
        self.flux_manifest, self.z_manifest = install_tiny_packages(self.library)

    def manager(self, memory_bytes: int) -> GenerationManager:
        return GenerationManager(self.data, self.library, self.flux_manifest, RUNTIME_ID,
                                 manifests=[self.flux_manifest, self.z_manifest],
                                 total_memory_bytes=memory_bytes)

    def catalog(self, memory_bytes: int) -> dict:
        client = TestClient(image_main.create_app(self.manager(memory_bytes), TOKEN))

        def sysctl(_command, **_kwargs):
            return str(memory_bytes)

        # The catalog reads the host the same way the worker does (hw.memsize), so the tier a
        # test sees does not depend on the machine running the suite.
        with patch.object(image_main.subprocess, 'check_output', sysctl):
            entries = client.get('/api/catalog',
                                 headers={'X-Studio-Token': TOKEN}).json()['models']
        return {entry['repo']: entry for entry in entries}

    def test_a_16gb_mac_is_offered_z_image_in_its_bounded_configuration(self):
        entries = self.catalog(16 * GiB)
        z_image = entries[Z_IMAGE_TURBO.repo]
        self.assertEqual(z_image['memory_policy']['mode_for_this_host'], 'low-ram')
        self.assertEqual(z_image['memory_policy']['low_ram_below_bytes'],
                         Z_IMAGE_LOW_RAM_BELOW_BYTES)
        self.assertEqual(z_image['memory_policy']['measured_peak_gb']['low_ram'], 7.12)
        self.assertTrue(z_image['execution_ready'])
        # 16 GB is the qualified tier, so no under-tier warning there.
        self.assertIsNone(z_image['memory_warning'])
        self.assertEqual(z_image['min_total_memory_gb'], 16.0)

    def test_a_32gb_mac_gets_the_default_and_flux_is_warned_about_nowhere_here(self):
        entries = self.catalog(32 * GiB)
        self.assertEqual(entries[Z_IMAGE_TURBO.repo]['memory_policy']['mode_for_this_host'],
                         'default')
        self.assertIsNone(entries[FLUX2_KLEIN.repo]['memory_warning'])
        self.assertIsNone(entries[Z_IMAGE_TURBO.repo]['memory_warning'])

    def test_the_8gb_tier_is_published_as_unmeasured_not_as_a_floor(self):
        entries = self.catalog(8 * GiB)
        z_image = entries[Z_IMAGE_TURBO.repo]
        # The operator is told what is known, and the worker still runs. Nothing refuses an
        # 8 GB Mac on total memory: the Hub's own floor is display-only unless an operator
        # override names one, and its live guard is free memory. The worker's part is to say
        # the tier is unmeasured rather than to invent a floor for it.
        self.assertTrue(z_image['memory_eligible'])
        self.assertIn('8 GB of unified memory', z_image['memory_warning'])
        self.assertIn('still being measured', z_image['memory_warning'])
        self.assertIn('pending', z_image['qualification']['fleet_8gb'])
        self.assertEqual(z_image['memory_policy']['mode_for_this_host'], 'low-ram')
        # The published tier is the fleet tier the model is qualified at, per model.
        self.assertEqual(z_image['min_total_memory_gb'], 16.0)
        self.assertEqual(z_image['min_unified_memory_gb'], 16.0)
        self.assertEqual(QUALIFIED_MINIMUM_MEMORY_BYTES, 16_000_000_000)
        # FLUX keeps its own, unchanged warning text.
        flux = entries[FLUX2_KLEIN.repo]
        self.assertIn('8 GB of unified memory', flux['memory_warning'])
        self.assertIn('qualified on 16 GB', flux['memory_warning'])
        self.assertNotIn('still being measured', flux['memory_warning'])

    def test_a_low_ram_host_runs_the_job_through_the_bounded_path(self):
        calls: list = []
        manager = self.manager(16 * GiB)
        client = TestClient(image_main.create_app(manager, TOKEN))
        with (patch.dict(sys.modules, fake_z_image_stack(calls)),
              patch.object(image_main, '_host_eligibility',
                           return_value={'eligible': True, 'macos': '26.6.2'})):
            response = client.post('/api/generate/txt2img', headers={'X-Studio-Token': TOKEN},
                                   json={'repo': Z_IMAGE_TURBO.repo, 'prompt': 'a lantern',
                                         'aspect_ratio': '1:1', 'width': 1024, 'height': 1024,
                                         'seed': 7})
            self.assertEqual(response.status_code, 200, response.text)
            job_id = response.json()['job']['id']
            for _ in range(200):
                job = client.get(f'/api/generate/jobs/{job_id}',
                                 headers={'X-Studio-Token': TOKEN}).json()['job']
                if job['state'] in ('done', 'error', 'cancelled'):
                    break
                time.sleep(0.02)
        self.assertEqual(job['state'], 'done', job.get('error'))
        # The mode is recorded on the job and the CLI's memory mapping was the one applied.
        self.assertEqual(job['memory_mode'], 'low-ram')
        events = [call['event'] for call in calls]
        self.assertEqual(events, ['init', 'memory-saver', 'generate'])
        self.assertTrue(calls[1]['low_ram'])
        # The model was loaded from the installed package's single model/ directory.
        self.assertEqual(Path(calls[0]['model_path']).name, 'model')
        # Nothing was downloaded or read outside the library: the job is reproducible offline.
        self.assertEqual(manager.jobs[job_id]['params']['repo'], Z_IMAGE_TURBO.repo)

    def test_the_catalog_floors_are_the_ones_the_hub_governor_enforces(self):
        """The worker's entry is the only source for these, so check them against the Hub.

        `memory_admission.describe` reads the worker's catalog entry. Both floors are enforced
        (`broker._memory_gate`): the live-free floor, and - since the owner's 2026-09-30 decision -
        the total-memory requirement (measured, else manifest, else this catalog row) as a hard
        eligibility floor. This used to assert the total floor was 0.0 because a tier was advice;
        that premise is reversed, and Z-Image-Turbo's requirement is its manifest's measured 8 GB.
        """
        from components.hub.backend import memory_admission

        entries = self.catalog(16 * GiB)
        z_admission = memory_admission.describe(Z_IMAGE_TURBO.repo, entries[Z_IMAGE_TURBO.repo])
        self.assertEqual(z_admission['effective_min_free_memory_gb'], 2.8)
        self.assertEqual(z_admission['catalog_min_free_memory_gb'], 2.8)
        self.assertEqual(z_admission['effective_min_total_memory_gb'], 8.0)
        self.assertEqual(z_admission['min_total_source'], 'manifest')
        # The invariant, not the number, because the number was wrong once in a way that cost a
        # measurement. The floor guards against starting into a nearly-full Mac; it must not
        # become a tier in disguise. A free floor at or above the smallest machine's *total* RAM
        # can never be satisfied there, and the Hub answers an unmet free floor with "wait"
        # rather than "skip" (broker._memory_gate) - so the job waits for memory that is never
        # coming instead of running or failing. It was 8.0, which did exactly that on the 8 GB
        # tier: the one tier with no measurement, and the one that needed to run.
        SMALLEST_FLEET_TOTAL_GB = 8.0
        self.assertLess(z_admission['effective_min_free_memory_gb'], SMALLEST_FLEET_TOTAL_GB)
        # Still a real guard rather than drifting to nothing: above the generic 2.0 default.
        self.assertGreater(z_admission['effective_min_free_memory_gb'], 2.0)

        flux_admission = memory_admission.describe(MODEL_ID, entries[MODEL_ID])
        # Free memory needed before start (not the peak), from the real 8 GB M1+M2 runs 2026-10-02 (owner chose 2.8 for
        # headroom; the runs started cleanly from 3.09 GB): 2.8 for both image models, read from the worker's own row.
        # The Hub keeps no copy of this number (it was removed from `_FLEET_DEFAULTS` 2026-10-19), so an edit to the row
        # is the edit; the operator's override on the Models screen is the only other number.
        self.assertEqual(flux_admission['effective_min_free_memory_gb'], 2.8)
        self.assertEqual(flux_admission['catalog_min_free_memory_gb'], 2.8)
        self.assertEqual(flux_admission['default_min_free_memory_gb'], 2.8)
        self.assertEqual(flux_admission['min_free_source'], 'model_default')
        self.assertEqual(flux_admission['default_min_total_memory_gb'], 8.0)

    def test_the_memory_policy_names_the_model_a_running_job_actually_loaded(self):
        manager = self.manager(32 * GiB)
        client = TestClient(image_main.create_app(manager, TOKEN))
        manager.jobs['c' * 32] = {'id': 'c' * 32, 'state': 'running', 'created_at': time.time(),
                                  'params': {'repo': Z_IMAGE_TURBO.repo}}
        policy = client.get('/api/memory-policy',
                            headers={'X-Studio-Token': TOKEN}).json()
        self.assertEqual(policy['loaded_models'], [[Z_IMAGE_TURBO.repo]])
        self.assertTrue(policy['resident'])



class ReportedPressureRunsTheRealPredicate(unittest.TestCase):
    """Levels 1/2/4 and an unreadable host, through `_under_memory_pressure` itself.

    Nothing here patches the predicate: the fixture states the level the sysctl reports, so the
    function's own comparison, its default level and its unreadable-is-not-pressure rule run.
    """

    def test_every_level_the_owner_rules_distinguish(self):
        cases = ((1, False, False),      # normal: neither warning nor critical
                 (2, True, False),       # warning: the default that governs idle residency
                 (4, True, True),        # critical: what the hard stops ask for
                 (None, False, False))   # unreadable: never pressure a host did not show
        for reported, warning, critical in cases:
            with self.subTest(reported=reported):
                with _image_pressure.pressure(reported):
                    self.assertEqual(generation._under_memory_pressure(), warning)
                    self.assertEqual(generation._under_memory_pressure(level=4), critical)

    def test_admission_refuses_only_at_critical_pressure(self):
        with patch.object(generation, '_reclaimable_available_gb', return_value=64.0):
            for reported in (1, 2, None):
                with self.subTest(reported=reported), _image_pressure.pressure(reported):
                    self.assertIsNone(
                        generation._admit_job(generation.FLUX2_KLEIN, 'txt2img', 32 * GiB))
            with _image_pressure.pressure(4):
                refusal = generation._admit_job(generation.FLUX2_KLEIN, 'txt2img', 32 * GiB)
        self.assertIsNotNone(refusal)
        self.assertIn('critical memory pressure', refusal)


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
