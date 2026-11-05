"""Owner defaults and live memory stops use the same node policy as publication."""
import copy
import threading
import types
import unittest
from unittest.mock import patch

import _image_pressure

from components.image.app.backend import generation as g
from components.image.app.backend.main import edit_lane


class ReferenceLimitTableTests(unittest.TestCase):
    def test_physical_tiers_and_owner_defaults(self):
        for tier, klein_max, qwen_max in ((8, 0, 0), (16, 3, 0), (24, 4, 1), (32, 4, 1), (64, 4, 1)):
            memory = tier * 1024**3
            with self.subTest(tier=tier):
                self.assertEqual(g.reference_tier(memory), str(min(tier, 32)))
                self.assertEqual(g.effective_reference_max(g.FLUX2_KLEIN, memory), klein_max)
                self.assertEqual(g.effective_reference_max(g.QWEN_IMAGE_21_TURBO, memory), qwen_max)
                self.assertTrue(g.effective_reference_policy(g.FLUX2_KLEIN, 'txt2img', memory)['allowed'])
                self.assertEqual(g.effective_reference_policy(g.QWEN_IMAGE_21_TURBO, 'txt2img', memory)['allowed'], tier >= 16)
        self.assertEqual(g.reference_tier(4 * 1024**3), '<8')
        self.assertEqual(g.reference_tier(round(34.36 * 1e9)), '32')

    def test_edit_publication_preserves_explicit_zero_and_provisional_on(self):
        for model in (g.FLUX2_KLEIN, g.QWEN_IMAGE_21_TURBO):
            for tier in (8, 16, 24, 32):
                with self.subTest(model=model.repo, tier=tier):
                    memory = tier * 1024**3
                    contract = g.lane_contract(model, total_memory_bytes=memory)
                    maximum = g.effective_reference_max(model, memory)
                    self.assertEqual(contract['references']['max'], maximum)
                    self.assertEqual(contract['max_reference_images'], maximum)
                    self.assertEqual(contract['supports_reference_images'], maximum > 0)
                    lane = edit_lane(model, {'ready': True}, memory, 'auto')
                    self.assertEqual(lane['available'], maximum > 0)
                    if model == g.QWEN_IMAGE_21_TURBO and tier >= 24:
                        self.assertEqual(contract['effective_reference_policy']['source'], 'provisional, measuring')

    def test_exact_count_cells_are_published_and_enforced(self):
        for row in g.reference_limit_table()['rows']:
            self.assertEqual(set(row['tiers']), {'8', '16', '24', '32'})
            for cell in row['tiers'].values():
                self.assertEqual(cell['swap_allowance_gb'], 0)
                self.assertTrue(cell['source'])
                self.assertTrue(cell['receipt'])
        memory = 16 * 1024**3
        self.assertTrue(g.effective_reference_policy(g.FLUX2_KLEIN, 'edit', memory, 3)['allowed'])
        self.assertFalse(g.effective_reference_policy(g.FLUX2_KLEIN, 'edit', memory, 4)['allowed'])
        table = copy.deepcopy(g.reference_limit_table())
        row = next(r for r in table['rows'] if r['model_id'] == g.FLUX2_KLEIN.repo and r['reference_count'] == 2)
        row['tiers']['16']['allowed'] = False
        with patch.object(g, 'reference_limit_table', return_value=table):
            self.assertFalse(g.effective_reference_policy(g.FLUX2_KLEIN, 'edit', memory, 2)['allowed'])
            self.assertTrue(g.effective_reference_policy(g.FLUX2_KLEIN, 'edit', memory, 1)['allowed'])

    def test_existing_free_floor_is_checked_live(self):
        with patch.object(g, '_reclaimable_available_gb', return_value=0.01), _image_pressure.pressure(1):
            self.assertIn('free before it starts', g._admit_job(g.FLUX2_KLEIN, 'txt2img', 8 * 1024**3))


class ImageMemoryStopTests(unittest.TestCase):
    def manager(self):
        return types.SimpleNamespace(total_memory_bytes=32 * 1024**3,
            jobs={'job': {'id': 'job', 'state': 'running', 'error': None}},
            hard_stops={}, lock=threading.Lock())

    def test_swap_growth_stops_with_baseline_and_reason(self):
        manager = self.manager()
        event = threading.Event()
        with patch.object(g, '_host_swap_used_mb', side_effect=[1000.0, 1001.0]), _image_pressure.pressure(1):
            guard = g._JobMemoryGuard(manager, 'job', g.FLUX2_KLEIN, 'edit')
            guard.check(event)
        self.assertTrue(event.is_set())
        self.assertEqual(manager.jobs['job']['error_code'], 'SWAP_GROWTH_EXCEEDED')
        self.assertIn('baseline 1000.0 MB', manager.jobs['job']['error'])

    def test_critical_pressure_stops_even_with_loose_swap_allowance(self):
        manager = self.manager()
        event = threading.Event()
        with patch.object(g, '_host_swap_used_mb', return_value=1000.0), _image_pressure.pressure(4):
            guard = g._JobMemoryGuard(manager, 'job', g.FLUX2_KLEIN, 'edit')
            guard.allowance_gb = 100.0
            guard.check(event)
        self.assertTrue(event.is_set())
        self.assertEqual(manager.jobs['job']['error_code'], 'MEMORY_PRESSURE_STOPPED')

    def test_warning_pressure_leaves_a_running_job_alone(self):
        # The other half of the owner's 2026-11-05 split, which a level-blind mock could not ask:
        # warning (2) evicts an idle resident model, but never stops work already running.
        manager = self.manager()
        event = threading.Event()
        with patch.object(g, '_host_swap_used_mb', return_value=1000.0), _image_pressure.pressure(2):
            g._JobMemoryGuard(manager, 'job', g.FLUX2_KLEIN, 'edit').check(event)
        self.assertFalse(event.is_set())
        self.assertFalse(manager.hard_stops)

    def test_stop_reason_survives_failure_recording(self):
        manager = self.manager()
        job = manager.jobs['job']
        job['started_at'] = 1.0
        manager.hard_stops['job'] = ('SWAP_GROWTH_EXCEEDED', 'retained host swap reason')
        manager._persist = lambda: None
        manager._discard_inputs = lambda _job: None
        event = threading.Event()
        event.set()
        g.GenerationManager._record_failure(manager, job, event, RuntimeError('cancelled'))
        self.assertEqual(job['state'], 'error')
        self.assertEqual(job['error_code'], 'SWAP_GROWTH_EXCEEDED')
        self.assertEqual(job['error'], 'retained host swap reason')

    def test_existing_swap_without_growth_is_allowed(self):
        manager = self.manager()
        event = threading.Event()
        with patch.object(g, '_host_swap_used_mb', return_value=5000.0), _image_pressure.pressure(1):
            g._JobMemoryGuard(manager, 'job', g.FLUX2_KLEIN, 'edit').check(event)
        self.assertFalse(event.is_set())
        self.assertFalse(manager.hard_stops)


if __name__ == '__main__':
    unittest.main()
