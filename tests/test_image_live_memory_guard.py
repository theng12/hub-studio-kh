"""The live-free floor the Hub authorises, and the swap monitor's honest states.

The Hub's operator override is the authoritative live-free floor for the jobs it dispatches. It
travels as ``memory_floor_gb`` (the request key Voice already uses), is validated once in
``validate_request``, persists in the job's params, and is read by BOTH the queue gate and the
execution gate. A missing swap baseline or live read must never let a job finish green as if the
published allowance had been checked.
"""
from __future__ import annotations

import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _image_pressure  # noqa: E402
from components.image.app.backend import generation as g  # noqa: E402

GiB = 1024 ** 3
KLEIN = g.FLUX2_KLEIN
TINY = {'state': 'files_verified', 'folder': 'folder-x'}


class FloorWiringTests(unittest.TestCase):
    def test_a_txt2img_override_replaces_the_row_floor(self):
        # Row floor is 2.8 GB; the Hub's 1.5 GB override admits at 2.0 GB free, and without it
        # the same host is refused on the row floor.
        with patch.object(g, '_reclaimable_available_gb', return_value=2.0), \
                _image_pressure.pressure(1):
            self.assertIsNone(g._admit_job(KLEIN, 'txt2img', 16 * GiB, 'auto', 1.5))
            self.assertIn('needs 2.8 GB', g._admit_job(KLEIN, 'txt2img', 16 * GiB, 'auto'))

    def test_a_lower_override_cannot_lower_the_edit_floor(self):
        mode_floor = g._reference_floor_gb(KLEIN, 'edit', 16 * GiB, 'auto')
        self.assertIsNotNone(mode_floor)
        with patch.object(g, '_reclaimable_available_gb', return_value=1.0), \
                _image_pressure.pressure(1):
            refusal = g._admit_job(KLEIN, 'edit', 16 * GiB, 'auto', 1.0)
        self.assertIn(f'needs {mode_floor:g} GB', refusal)

    def test_a_higher_override_raises_the_edit_floor(self):
        mode_floor = g._reference_floor_gb(KLEIN, 'edit', 16 * GiB, 'auto')
        higher = mode_floor + 1.0
        with patch.object(g, '_reclaimable_available_gb', return_value=mode_floor + 0.5), \
                _image_pressure.pressure(1):
            refusal = g._admit_job(KLEIN, 'edit', 16 * GiB, 'auto', higher)
        self.assertIn(f'needs {higher:g} GB', refusal)

    def test_the_floor_is_normalized_and_persisted(self):
        params = g.validate_request(
            {'repo': KLEIN.repo, 'prompt': 'x', 'aspect_ratio': '1:1', 'memory_floor_gb': 1.5},
            total_memory_bytes=16 * GiB)
        self.assertEqual(params['memory_floor_gb'], 1.5)
        self.assertNotIn('memory_floor_gb', g.validate_request(
            {'repo': KLEIN.repo, 'prompt': 'x', 'aspect_ratio': '1:1'},
            total_memory_bytes=16 * GiB))

    def test_an_invalid_floor_is_refused(self):
        for bad in (True, False, float('nan'), float('inf'), -0.5, 513, '2'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                g.validate_request({'repo': KLEIN.repo, 'prompt': 'x',
                                    'memory_floor_gb': bad}, total_memory_bytes=16 * GiB)

    def test_an_off_count_cell_names_the_retained_cap(self):
        cell = g.effective_reference_policy(KLEIN, 'edit', 16 * GiB, 4)
        self.assertFalse(cell['allowed'])
        self.assertIn('at most 3 reference images', cell['reason'])
        self.assertNotIn('edit is off', cell['reason'])

    def test_the_refused_parameter_sentence_names_the_effective_cap(self):
        with self.assertRaises(ValueError) as caught:
            g.validate_request({'repo': KLEIN.repo, 'prompt': 'x', 'lora_names': ['x']},
                               total_memory_bytes=16 * GiB)
        self.assertIn('1-3 reference images', str(caught.exception))


class QueueAndExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        (root / 'library').mkdir()
        self.manager = g.GenerationManager(root / 'data', root / 'library', {}, 'a' * 64,
                                           total_memory_bytes=16 * GiB)

    def queue(self) -> dict:
        """A job the Hub admits at 2.0 GB free under its 1.5 GB override (the row asks 2.8)."""
        raw = {'repo': KLEIN.repo, 'prompt': 'x', 'aspect_ratio': '1:1',
               'memory_floor_gb': 1.5}
        with patch.object(g, '_reclaimable_available_gb', return_value=2.0), \
                _image_pressure.pressure(1), \
                patch.object(self.manager, 'preflight', return_value=dict(TINY)), \
                patch.object(self.manager, 'production_admission',
                             return_value={'allowed': True, 'reason': None}), \
                patch.object(self.manager, '_enqueue'):
            return self.manager.submit(raw)

    def test_override_is_accepted_at_the_queue_and_read_at_execution(self):
        job = self.queue()
        self.assertEqual(job['params']['memory_floor_gb'], 1.5)
        # The Mac tightens after queueing: 1.0 GB free is below the override the Hub authorised.
        with patch.object(g, '_reclaimable_available_gb', return_value=1.0), \
                _image_pressure.pressure(1), \
                patch.object(self.manager, 'preflight', return_value=dict(TINY)):
            self.manager._run(job['id'], TINY['folder'])
        terminal = self.manager.jobs[job['id']]
        self.assertEqual(terminal['state'], 'error')
        self.assertIn('needs 1.5 GB', terminal['error'])

    def test_no_swap_baseline_never_runs_green(self):
        job = self.queue()
        with patch.object(g, '_reclaimable_available_gb', return_value=2.0), \
                patch.object(g, '_host_swap_used_mb', return_value=None), \
                _image_pressure.pressure(1), \
                patch.object(self.manager, 'preflight', return_value=dict(TINY)):
            self.manager._run(job['id'], TINY['folder'])
        terminal = self.manager.jobs[job['id']]
        self.assertEqual(terminal['state'], 'error')
        self.assertEqual(terminal['error_code'], 'MEMORY_MONITOR_UNAVAILABLE')
        self.assertIn('cannot be enforced', terminal['error'])


class MonitorHonestyTests(unittest.TestCase):
    def manager(self):
        return types.SimpleNamespace(
            total_memory_bytes=16 * GiB,
            jobs={'job': {'id': 'job', 'state': 'running', 'error': None}},
            hard_stops={}, lock=threading.Lock())

    def test_a_missing_baseline_stops_with_the_monitor_code(self):
        manager = self.manager()
        event = threading.Event()
        with patch.object(g, '_host_swap_used_mb', return_value=None), \
                _image_pressure.pressure(1):
            guard = g._JobMemoryGuard(manager, 'job', KLEIN, 'txt2img')
            self.assertIsNone(guard.baseline_mb)
            guard.check(event)
        self.assertTrue(event.is_set())
        self.assertEqual(manager.jobs['job']['error_code'], 'MEMORY_MONITOR_UNAVAILABLE')
        self.assertIn('cannot be verified', manager.jobs['job']['error'])

    def test_an_unreadable_current_stops_after_a_baseline(self):
        manager = self.manager()
        event = threading.Event()
        with patch.object(g, '_host_swap_used_mb', side_effect=[1000.0, None]), \
                _image_pressure.pressure(1):
            guard = g._JobMemoryGuard(manager, 'job', KLEIN, 'txt2img')
            guard.check(event)
        self.assertEqual(manager.jobs['job']['error_code'], 'MEMORY_MONITOR_UNAVAILABLE')

    def test_a_positive_allowance_stops_only_beyond_it(self):
        manager = self.manager()
        event = threading.Event()
        with patch.object(g, '_host_swap_used_mb', side_effect=[1000.0, 1500.0]), \
                _image_pressure.pressure(1):
            guard = g._JobMemoryGuard(manager, 'job', KLEIN, 'txt2img')
            guard.allowance_gb = 1.0
            guard.check(event)
        self.assertFalse(event.is_set())
        self.assertEqual(manager.hard_stops, {})
        with patch.object(g, '_host_swap_used_mb', side_effect=[1000.0, 2001.0]), \
                _image_pressure.pressure(1):
            guard = g._JobMemoryGuard(manager, 'job', KLEIN, 'txt2img')
            guard.allowance_gb = 1.0
            guard.check(event)
        self.assertTrue(event.is_set())
        self.assertEqual(manager.jobs['job']['error_code'], 'SWAP_GROWTH_EXCEEDED')


class EditFormTransportTests(unittest.TestCase):
    """The Hub's override reaches an edit job through the multipart form it already posts."""

    def setUp(self):
        from test_image_reference_lane import LaneHarness
        harness = type('Harness', (LaneHarness, unittest.TestCase), {
            'total_memory_bytes': 16 * GiB, 'runTest': lambda self: None})
        self.harness = harness(methodName='runTest')
        self.harness.setUp()
        self.addCleanup(self.harness.doCleanups)
        self.manager = self.harness.manager

    def post_edit(self, **fields):
        from test_image_reference_lane import patched_eligibility, png_bytes
        with patched_eligibility():
            return self.harness.post_edit([('one.png', png_bytes(), 'image/png')], **fields)

    def test_the_edit_form_persists_and_enforces_the_override(self):
        with patch.object(self.manager, '_enqueue'):
            response = self.post_edit(memory_floor_gb='10')
        self.assertEqual(response.status_code, 200, response.text)
        params = self.manager.get(response.json()['job']['id'])['params']
        self.assertEqual(params['memory_floor_gb'], 10.0)
        with patch.object(g, '_reclaimable_available_gb', return_value=9.0), \
                _image_pressure.pressure(1):
            self.assertIn('needs 10 GB', g._admit_job(
                KLEIN, 'edit', 16 * GiB, 'auto', params['memory_floor_gb']))
        with patch.object(g, '_reclaimable_available_gb', return_value=10.5), \
                _image_pressure.pressure(1):
            self.assertIsNone(g._admit_job(
                KLEIN, 'edit', 16 * GiB, 'auto', params['memory_floor_gb']))

    def test_a_lower_edit_override_leaves_the_reference_floor(self):
        with patch.object(self.manager, '_enqueue'):
            response = self.post_edit(memory_floor_gb='7')
        self.assertEqual(response.status_code, 200, response.text)
        params = self.manager.get(response.json()['job']['id'])['params']
        self.assertEqual(params['memory_floor_gb'], 7.0)
        with patch.object(g, '_reclaimable_available_gb', return_value=7.5), \
                _image_pressure.pressure(1):
            self.assertIn('needs 8 GB', g._admit_job(
                KLEIN, 'edit', 16 * GiB, 'auto', params['memory_floor_gb']))
        self.assertEqual(g._job_floor_gb(KLEIN, 'edit', 8 * GiB, 'auto', 7.0), 9.5)

    def test_invalid_edit_overrides_are_refused(self):
        for bad in ('true', 'nan', 'inf'):
            with self.subTest(bad=bad):
                self.assertEqual(self.post_edit(memory_floor_gb=bad).status_code, 422)
        self.assertEqual(self.manager.list(), [])


if __name__ == '__main__':
    unittest.main()
