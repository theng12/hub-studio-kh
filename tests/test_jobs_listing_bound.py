"""The Jobs listing is bounded, so its cost does not grow with every batch the ledger ever held."""
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]

PROOF = r'''
import importlib, os, tempfile, time
root = tempfile.mkdtemp(prefix='jobs-listing-')
os.environ['STUDIOHUB_DATA_DIR'] = root
from components.hub.backend import registry, ledger, broker
importlib.reload(registry); importlib.reload(ledger); importlib.reload(broker)
ledger.prepare_database()
def batch(i, state='done'):
    return {'id': f'b{i:05d}', 'created_at': 1000.0 + i, 'modality': 'image', 'model': 'm',
            'routing': 'pool', 'cancelled': False, 'label': None, 'shared_params': {},
            'items': [{'index': 0, 'state': state, 'prompt': 'p', 'params': {}, 'seed': None, 'tries': 1}]}
total = broker.LISTED_FINISHED_LIMIT + 60
for i in range(total):
    ledger.save_batch(batch(i))
listed = broker.listed_batches()
assert len(listed) == broker.LISTED_FINISHED_LIMIT, len(listed)
ids = {row['id'] for row in listed}
assert f'b{total - 1:05d}' in ids and 'b00000' not in ids, 'the listing must keep the newest'
# Cleanup still sees every finished batch, and an in-memory batch is always listed.
assert len(ledger.load_finished_batches()) == total
broker.batches['live'] = batch(0, 'running') | {'id': 'live'}
assert 'live' in {row['id'] for row in broker.listed_batches()}
print('ok')
'''


class JobsListingBoundTests(unittest.TestCase):
    def test_listing_is_bounded_but_cleanup_sees_everything(self):
        env = dict(os.environ)
        env['PYTHONPATH'] = str(ROOT)
        result = subprocess.run([sys.executable, '-B', '-c', PROOF], env=env,
                                text=True, capture_output=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('ok', result.stdout)


if __name__ == '__main__':
    unittest.main()
