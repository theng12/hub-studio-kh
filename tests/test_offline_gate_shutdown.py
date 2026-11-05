"""The gate must exit without consulting a potentially blocked thread registry."""
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
LOAD_GATE = f'''
import importlib.util
import threading
spec = importlib.util.spec_from_file_location('gate', {str(ROOT / 'scripts/hub_offline_gate.py')!r})
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
'''
HELD_REGISTRY = '''
held = threading.Event()
release = threading.Event()
def hold_registry():
    with threading._active_limbo_lock:
        held.set()
        release.wait()
threading.Thread(target=hold_registry, daemon=True).start()
assert held.wait(1), 'fixture did not acquire registry lock'
print('REGISTRY_LOCK_HELD', flush=True)
'''


class GateShutdownTests(unittest.TestCase):
    def run_child(self, source, timeout=4):
        return subprocess.run([sys.executable, '-I', '-u', '-c', textwrap.dedent(source)],
                              cwd=ROOT, capture_output=True, text=True, timeout=timeout)

    def test_old_thread_diagnostics_block_when_the_registry_is_held(self):
        with self.assertRaises(subprocess.TimeoutExpired) as caught:
            self.run_child(LOAD_GATE + HELD_REGISTRY + 'threading.enumerate()\n', timeout=1)
        self.assertIn(b'REGISTRY_LOCK_HELD', caught.exception.stdout)

    def test_success_exits_even_when_the_registry_is_held(self):
        result = self.run_child(LOAD_GATE + HELD_REGISTRY + 'gate.finish_unittest(True)\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('REGISTRY_LOCK_HELD', result.stdout)

    def test_failure_stays_failed_even_when_the_registry_is_held(self):
        result = self.run_child(LOAD_GATE + HELD_REGISTRY + 'gate.finish_unittest(False)\n')
        self.assertEqual(result.returncode, 1, result.stderr)

    def test_a_leaked_non_daemon_thread_times_out_loudly(self):
        result = self.run_child(LOAD_GATE + '''
event = threading.Event()
threading.Thread(target=event.wait, daemon=False).start()
gate.SHUTDOWN_TIMEOUT_S = 0.2
print('RESULTS_PRINTED', flush=True)
gate.finish_unittest(True)
''')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('RESULTS_PRINTED', result.stdout)
        self.assertIn('Timeout', result.stderr)
        self.assertIn('wait', result.stderr)


if __name__ == '__main__':
    unittest.main()
