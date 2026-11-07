"""Retention must survive a reboot and must not re-read the whole output library on every sweep.

macOS renumbers a volume's device id between boots, and the index compared it: every output
generated before the last restart was marked "uncertain" (never moved to the Trash Folder, still
charged to `max_bytes`) although its bytes were untouched - 96 groups / 1.6 GB on this Mac after
a week. And `_reconcile` hashed every indexed file on every sweep.
"""
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]

PROOF = r'''
import hashlib, importlib, os, tempfile
from pathlib import Path
from unittest.mock import patch

root = Path(tempfile.mkdtemp(prefix='output-identity-')).resolve()
os.environ['STUDIOHUB_DATA_DIR'] = str(root / 'hub')
from components.hub.backend import registry, ledger
importlib.reload(registry); importlib.reload(ledger)
from components.hub import output_storage
importlib.reload(output_storage)
out = root / 'out'; out.mkdir()

def add(name, data, *, batch, generated=1):
    path = out / name; path.write_bytes(data)
    assert output_storage.register_finalized_output(
        machine='m', studio='image', job_id=name, managed_output_roots=[str(out)],
        output_path=str(path), generated_at=generated, batch_id=batch, item_index=0,
        bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    output_storage.mark_receipt_acked(batch_id=batch, item_index=0)
    return path

def state(batch):
    with ledger._conn() as conn:
        return conn.execute("SELECT state FROM output_groups WHERE batch_id=?", (batch,)).fetchone()['state']

def renumber_device():
    with ledger._conn() as conn:
        conn.execute("UPDATE output_files SET device=device+7")

# 1. A reboot renumbers the device: the output stays finalized and is still cleaned up on time.
kept = add('kept.png', b'k' * 10, batch='kept', generated=10**10)
expired = add('expired.png', b'e' * 10, batch='expired')
renumber_device()
result = output_storage.enforce(machine='m', max_bytes=10**9, retention_days=1, now=1.5 * 10**5)
assert state('kept') == 'finalized', state('kept')
assert not expired.exists() and result['deleted'] == 1, 'an expired output survived a device renumbering'
assert kept.exists()

# 2. A group already parked "uncertain" by the old comparison (bytes untouched, device differs) heals.
parked = add('parked.png', b'p' * 10, batch='parked')
with ledger._conn() as conn:
    conn.execute("UPDATE output_groups SET state='uncertain', recoverable=0 WHERE batch_id='parked'")
    conn.execute("UPDATE output_files SET device=device+3 WHERE path LIKE '%parked.png'")
output_storage.enforce(machine='m', max_bytes=10**9, retention_days=1, now=1.5 * 10**5)
assert not parked.exists(), 'an output parked uncertain by a device renumbering stayed there'

# 3. Content that really changed still parks the group (and is never moved).
changed = add('changed.png', b'abc', batch='changed')
changed.write_bytes(b'abd')
os.utime(changed, (5, 5))
output_storage.enforce(machine='m', max_bytes=0, retention_days=1, now=1.5 * 10**5)
assert changed.exists() and state('changed') == 'uncertain', 'a modified output was not protected'

# 4. A sweep over unchanged, already-trusted files reads none of them.
steady = [add(f'steady{i}.png', b's' * 100, batch=f'steady{i}', generated=10**10) for i in range(5)]
hashed = []
real = output_storage._sha256
def counting(path):
    hashed.append(path.name); return real(path)
with patch.object(output_storage, '_sha256', counting):
    output_storage.enforce(machine='m', max_bytes=10**9, retention_days=1, now=1.5 * 10**5)
assert [name for name in hashed if name.startswith('steady')] == [], hashed

# 5. Foreign identity rejection: unmanaged foreign path is rejected, and enforce leaves foreign machine output untouched.
foreign_out = root / 'foreign_out'; foreign_out.mkdir()
foreign_file = foreign_out / 'unmanaged.png'; foreign_file.write_bytes(b'outside')
rejected = output_storage.register_finalized_output(
    machine='m', studio='image', job_id='rejected', managed_output_roots=[str(out)],
    output_path=str(foreign_file), generated_at=1, batch_id='rejected', item_index=0,
    bytes=len(b'outside'), sha256=hashlib.sha256(b'outside').hexdigest())
assert rejected is None, 'foreign unmanaged path was not rejected'

foreign_kept = add('foreign_kept.png', b'f' * 20, batch='foreign_batch')
with ledger._conn() as conn:
    conn.execute("UPDATE output_groups SET machine='foreign-host' WHERE batch_id='foreign_batch'")

output_storage.enforce(machine='m', max_bytes=0, retention_days=0, now=1.5 * 10**5)
assert foreign_kept.exists(), 'foreign machine output was deleted by local enforce'
with ledger._conn() as conn:
    foreign_group = conn.execute("SELECT machine, state FROM output_groups WHERE batch_id='foreign_batch'").fetchone()
    assert foreign_group['machine'] == 'foreign-host', 'foreign machine identity was altered'
    assert foreign_group['state'] == 'finalized', 'foreign machine output state was modified'

print('ok')
'''


class OutputReconcileIdentityTests(unittest.TestCase):
    def test_identity_survives_a_reboot_and_the_sweep_does_not_rehash(self):
        env = dict(os.environ)
        env['PYTHONPATH'] = str(ROOT)
        result = subprocess.run([sys.executable, '-B', '-c', PROOF], env=env,
                                text=True, capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('ok', result.stdout)


if __name__ == '__main__':
    unittest.main()
