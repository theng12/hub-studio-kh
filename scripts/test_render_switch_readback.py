"""A publication failure must never erase verified switch writes or repeat them."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from render_switch_readback import verify_publication


class ReadbackTests(unittest.TestCase):
    def test_success_missing_rows_and_timeouts_keep_writes_separate(self):
        expected = [{'controller_id': 'site', 'machine_id': 'one', 'opted_in': False},
                    {'controller_id': 'site', 'machine_id': 'keep', 'opted_in': True}]
        for case in ('verified', 'missing', 'timeout', 'wrong flag'):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                calls = []
                def call(method, path, *, timeout):
                    calls.append((method, path, timeout))
                    if path.endswith('force=true'):
                        if case == 'timeout':
                            raise TimeoutError('private-token-in-url')
                        return 200, {'generated_at': 123, 'locations': [
                            {'id': 'site', 'reachable': True, 'token': 'private-token'}],
                            'machines': [{'location_id': 'site', 'machine': 'one',
                                          'operations': ['video.final_render']}]}
                    workers = [dict(row) for row in expected]
                    if case == 'missing':
                        workers.pop()
                    if case == 'wrong flag':
                        workers[0]['opted_in'] = True
                    return 200, {'workers': workers}
                out = Path(directory) / 'receipt.json'
                code = verify_publication(SimpleNamespace(call=call), expected,
                                          {'store_verified': True, 'audits_verified': True},
                                          out, timeout_s=20, now=lambda: 1)
                record = json.loads(out.read_text())
                self.assertEqual(code, 0 if case == 'verified' else 2)
                self.assertIs(record['switch_writes_verified'], True)
                self.assertEqual(record['publication_verified'], case == 'verified')
                self.assertEqual(len(calls), 1 if case == 'timeout' else 2)
                self.assertTrue(all(method == 'GET' for method, _, _ in calls))
                self.assertTrue(all(0 < timeout <= 20 for _, _, timeout in calls))
                self.assertNotIn('private-token', out.read_text())
                if case != 'verified':
                    self.assertEqual(record['state'],
                                     'switch writes verified; publication readback pending')
                with self.assertRaises(FileExistsError):
                    verify_publication(SimpleNamespace(call=call), expected,
                                       {'store_verified': True, 'audits_verified': True},
                                       out, timeout_s=20)

    def test_unverified_writes_are_refused_before_any_read(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'verified separately'):
                verify_publication(SimpleNamespace(), [], {}, Path(directory) / 'no.json', timeout_s=20)


if __name__ == '__main__':
    unittest.main()
