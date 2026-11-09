"""Gate preflight: stub interpreters only, no models, no heavy imports.

These cases prove the failure messages name the missing interpreter, seed module,
pytest overlay and closure entry, without importing soundfile/mlx/misaki or any
runtime. Run with any Python 3.12: `python3 -B tests/test_offline_gate_preflight.py`.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
GATE_SCRIPT = ROOT / 'scripts' / 'hub_offline_gate.py'


def load_gate():
    spec = importlib.util.spec_from_file_location('hub_offline_gate_preflight_test', GATE_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def stub(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding='utf-8')
    path.chmod(0o755)
    return path


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.gate = load_gate()

    @staticmethod
    def fake_venv(root: Path, name: str, base: Path) -> Path:
        """A real venv layout whose python symlinks an existing base binary."""
        venv = root / name
        (venv / 'bin').mkdir(parents=True)
        (venv / 'pyvenv.cfg').write_text(
            f'home = {base.parent}\n'
            'include-system-site-packages = false\n'
            f'version = {platform.python_version()}\n',
            encoding='utf-8')
        (venv / 'bin' / 'python').symlink_to(base)
        return venv

    def probe_prefix(self, python: Path) -> str:
        completed = self.gate._run_probe(python, self.gate.INTERPRETER_PROBE)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)['prefix']

    def test_missing_interpreter_is_named(self):
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / 'bin' / 'python'
            with self.assertRaises(SystemExit) as caught:
                self.gate.check_interpreter(missing, 'gate')
            self.assertIn('gate interpreter missing', str(caught.exception))
            self.assertIn(str(missing), str(caught.exception))

    def test_seed_failure_names_the_seed_set_and_the_module_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            python = stub(
                Path(temporary) / 'python',
                '#!/bin/sh\n'
                'echo "ModuleNotFoundError: No module named \'soundfile\'" >&2\n'
                'exit 1\n')
            with self.assertRaises(SystemExit) as caught:
                self.gate.check_gate_seeds(python)
            message = str(caught.exception)
            self.assertIn('soundfile', message)
            self.assertIn('mlx.core', message)
            self.assertIn("No module named 'soundfile'", message)

    def test_seed_check_covers_the_vision_helper(self):
        self.assertIn('tokenizers', self.gate.GATE_SEEDS)
        self.assertIn('tokenizers', self.gate.GATE_SEED_CHECK)

    def test_gate_lock_tokenizers_matches_the_shipped_voice_runtime_pin(self):
        def pinned(text, name):
            for line in text.splitlines():
                line = line.split('#', 1)[0].strip()
                if line.startswith(name + '=='):
                    return line[len(name) + 2:].split()[0].rstrip('\\')
            return None

        gate_lock = (ROOT / 'packaging/hub-gate/requirements.lock.txt').read_text(encoding='utf-8')
        voice_lock = (ROOT / 'packaging/voice-runtime/requirements.lock.txt').read_text(
            encoding='utf-8')
        self.assertIsNotNone(pinned(gate_lock, 'tokenizers'))
        self.assertEqual(pinned(gate_lock, 'tokenizers'), pinned(voice_lock, 'tokenizers'))

    def test_pytest_overlay_failure_names_pytest_and_the_overlay_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            python = stub(
                Path(temporary) / 'python',
                '#!/bin/sh\necho "No module named \'pytest\'" >&2\nexit 1\n')
            overlay = str(Path(temporary) / '.build' / 'test-deps')
            with self.assertRaises(SystemExit) as caught:
                self.gate.check_pytest(python, {'PYTHONPATH': overlay})
            message = str(caught.exception)
            self.assertIn('pytest', message)
            self.assertIn(overlay, message)

    def test_overlay_prepends_test_deps_and_keeps_the_callers_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = self.gate.test_deps_env(root, {'PYTHONPATH': '/existing'})
            self.assertEqual(
                env['PYTHONPATH'],
                str(root / '.build' / 'test-deps') + os.pathsep + '/existing')

    def test_selection_refuses_a_different_launching_interpreter(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            python = stub(
                root / 'gate-python',
                '#!/usr/bin/env python3\n'
                'import json, sys\n'
                'print(json.dumps({"prefix": "/fake/selected", "executable": "/fake/bin/python"}))\n')
            with mock.patch.dict(os.environ, {'HUB_GATE_PYTHON': str(python)}):
                with self.assertRaises(SystemExit) as caught:
                    self.gate.check_gate_selection(root)
            message = str(caught.exception)
            self.assertIn('/fake/selected', message)
            self.assertIn(sys.prefix, message)

    def test_two_venvs_sharing_one_binary_are_refused_by_prefix(self):
        base = Path(sys.executable).resolve()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = self.fake_venv(root, 'hub-gate', base)
            second = self.fake_venv(root, 'hub-tests', base)
            first_bin, second_bin = first / 'bin' / 'python', second / 'bin' / 'python'
            # The old check compared resolved binaries, which are identical here.
            self.assertEqual(first_bin.resolve(), second_bin.resolve())
            first_prefix, second_prefix = self.probe_prefix(first_bin), self.probe_prefix(second_bin)
            self.assertNotEqual(first_prefix, second_prefix)
            with self.assertRaises(SystemExit) as caught:
                self.gate.refuse_different_environment(first_prefix, second_prefix, second_bin)
            message = str(caught.exception)
            self.assertIn(first_prefix, message)
            self.assertIn(second_prefix, message)
            self.assertIsNone(
                self.gate.refuse_different_environment(first_prefix, first_prefix, first_bin))

    def test_probe_timeout_is_a_loud_preflight_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            python = stub(Path(temporary) / 'python', '#!/bin/sh\nexec sleep 5\n')
            with mock.patch.object(self.gate, 'PROBE_TIMEOUT_SECONDS', 0.2):
                with self.assertRaises(SystemExit) as caught:
                    self.gate.check_pytest(python, {})
            message = str(caught.exception)
            self.assertIn('did not finish within', message)
            self.assertIn('pytest import', message)

    def test_lock_comparison_names_missing_and_mismatched_entries(self):
        required = self.gate.parse_lock_text(
            'soundfile==0.14.0\nmlx==0.31.2\n# comment\nespeakng-loader==0.2.4\n')
        missing, mismatched = self.gate.compare_distributions(
            required, {'soundfile': '0.13.0', 'espeakng_loader': '0.2.4'})
        self.assertEqual(missing, ['mlx==0.31.2'])
        self.assertEqual(mismatched, ['soundfile: required 0.14.0, installed 0.13.0'])

    def test_lock_parse_rejects_a_line_without_a_version(self):
        with self.assertRaises(SystemExit) as caught:
            self.gate.parse_lock_text('soundfile\n')
        self.assertIn('name==version', str(caught.exception))

    def test_native_stage_needs_no_interpreter(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.assertIsNone(self.gate.preflight(Path(temporary), 'native'))


class DiskPreflightTests(unittest.TestCase):
    """The measured gate demand: provenance is read, never invented."""

    def setUp(self):
        self.gate = load_gate()

    @staticmethod
    def telemetry(root: Path, budget=11_990_847_488, floor=10_737_418_240) -> Path:
        path = root / 'telemetry.json'
        path.write_text(json.dumps({'disk': {'budget_bytes': budget,
                                             'minimum_remaining_bytes': floor}}), encoding='utf-8')
        return path

    def test_missing_telemetry_is_a_loud_refusal_not_a_guess(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(SystemExit) as caught:
                self.gate._disk_demand(Path(temporary) / 'absent.json')
            message = str(caught.exception)
            self.assertIn('measured gate disk telemetry is missing', message)
            self.assertIn('absent.json', message)

    def test_unreadable_telemetry_is_a_loud_refusal(self):
        with tempfile.TemporaryDirectory() as temporary:
            broken = Path(temporary) / 'telemetry.json'
            broken.write_text('{"disk": {"budget_bytes": "plenty"}}', encoding='utf-8')
            with self.assertRaises(SystemExit) as caught:
                self.gate._disk_demand(broken)
            self.assertIn('unreadable', str(caught.exception))

    def test_the_measured_demand_is_carried_with_its_provenance(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = self.telemetry(Path(temporary))
            demand = self.gate._disk_demand(path)
            self.assertEqual(demand['demand_bytes'], 11_990_847_488)
            self.assertEqual(demand['floor_bytes'], 10_737_418_240)
            self.assertEqual(demand['provenance'], str(path))

    def test_free_space_below_the_measured_need_refuses_and_names_both_sides(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            demand = self.gate._disk_demand(self.telemetry(root))
            below = SimpleNamespace(free=demand['demand_bytes'] - 1)
            with self.assertRaises(SystemExit) as caught:
                self.gate.check_free_disk(root, demand, usage=lambda path: below)
            message = str(caught.exception)
            self.assertIn(str(demand['demand_bytes']), message)
            self.assertIn(str(demand['floor_bytes']), message)
            self.assertIn('in-run floor', message)

    def test_free_space_at_the_measured_need_passes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            demand = self.gate._disk_demand(self.telemetry(root))
            at_need = SimpleNamespace(free=demand['demand_bytes'])
            result = self.gate.check_free_disk(root, demand, usage=lambda path: at_need)
            self.assertTrue(result['ok'])
            self.assertEqual(result['provenance'], str(self.telemetry(root)))

    def test_a_heavy_preflight_refuses_below_the_need_before_any_other_check(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            telemetry = self.telemetry(root)
            with mock.patch.dict(os.environ, {'HUB_GATE_DISK_TELEMETRY': str(telemetry)}):
                with mock.patch.object(self.gate, 'check_gate_selection'):
                    with mock.patch.object(self.gate, 'check_gate_seeds'):
                        with mock.patch.object(self.gate, 'check_gate_closure'):
                            with mock.patch.object(self.gate, 'check_interpreter'):
                                with mock.patch.object(
                                        self.gate.shutil, 'disk_usage',
                                        lambda path: SimpleNamespace(free=0)):
                                    with self.assertRaises(SystemExit) as caught:
                                        self.gate.preflight(root, 'unittest')
            self.assertIn('below the measured gate need', str(caught.exception))

    def test_a_heavy_preflight_reads_the_measured_need_before_running(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            telemetry = self.telemetry(root)
            with mock.patch.dict(os.environ, {'HUB_GATE_DISK_TELEMETRY': str(telemetry)}):
                with mock.patch.object(self.gate, 'check_gate_selection'):
                    with mock.patch.object(self.gate, 'check_gate_seeds'):
                        with mock.patch.object(self.gate, 'check_gate_closure'):
                            with mock.patch.object(self.gate, 'check_interpreter'):
                                with mock.patch.object(
                                        self.gate.shutil, 'disk_usage',
                                        lambda path: SimpleNamespace(
                                            free=11_990_847_488)):
                                    self.assertIsNone(self.gate.preflight(root, 'unittest'))

    def test_the_native_stage_never_needs_the_disk_demand(self):
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.dict(os.environ, {'HUB_GATE_DISK_TELEMETRY': '/absent'}):
                self.assertIsNone(self.gate.preflight(Path(temporary), 'native'))


if __name__ == '__main__':
    unittest.main()
