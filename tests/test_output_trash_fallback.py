"""An output root on another volume is not silently skipped: its expired outputs go to a Trash Folder inside it."""
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]

PROOF = r'''
import errno, hashlib, importlib, os, tempfile, time
from pathlib import Path
from unittest.mock import patch

root = Path(tempfile.mkdtemp(prefix='output-fallback-')).resolve()
os.environ['STUDIOHUB_DATA_DIR'] = str(root / 'hub')
from components.hub.backend import registry, ledger
importlib.reload(registry); importlib.reload(ledger)
from components.hub import output_storage
importlib.reload(output_storage)
# The Trash Folder is measured in physical (block) bytes; here a file's physical size is its byte count.
from components.hub import disk_governor
disk_governor._fs_private_size = lambda path: os.lstat(path).st_size
out = root / 'ssd-outputs'; out.mkdir()
hub_trash = ledger.DATA_DIR / 'trash'
real_rename = os.rename

def other_volume(src, dst, *a, **k):
    """The Hub's data folder is on another volume than `out`."""
    if str(dst).startswith(str(hub_trash)):
        raise OSError(errno.EXDEV, 'Cross-device link')
    return real_rename(src, dst, *a, **k)

def add(name, data, batch):
    path = out / name; path.write_bytes(data)
    assert output_storage.register_finalized_output(
        machine='m', studio='image', job_id=name, managed_output_roots=[str(out)],
        output_path=str(path), generated_at=1, batch_id=batch, item_index=0,
        bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    output_storage.mark_receipt_acked(batch_id=batch, item_index=0)
    return path

# 1. Cross-volume: not skipped - moved into the root's own Trash Folder, counted, still freed.
old = add('old.png', b'12345', 'b1')
with patch.object(os, 'rename', other_volume):
    result = output_storage.enforce(machine='m', max_bytes=99, retention_days=1)
assert not old.exists(), 'a cross-volume output was silently left in live storage'
day = time.strftime('%Y-%m-%d', time.gmtime())
moved = list((out / '.studiohub-trash' / day / 'b1').glob('*-old.png'))
assert len(moved) == 1 and moved[0].read_bytes() == b'12345', moved
assert result['freed_bytes'] == 5 and result['used_bytes'] == 0, result
assert result['trash_fallback_moves'] == 1 and result['trash_move_failures'] == 0, result
assert output_storage.trash_bytes() == 5, 'the fallback Trash Folder is not counted'

# 2. The fallback Trash Folder is purged like the other (and only dated folders in it).
stale = out / '.studiohub-trash' / '2000-01-01'; stale.mkdir()
(stale / 'f').write_bytes(b'x')
assert output_storage.purge_trash() == 1 and not stale.exists() and moved[0].exists()

# 3. A move that fails both ways is counted, logged and retried - never silent.
stuck = add('stuck.png', b'abc', 'b2')
with patch.object(os, 'rename', side_effect=OSError('fixture: disk says no')):
    result = output_storage.enforce(machine='m', max_bytes=99, retention_days=1)
assert stuck.exists() and result['deleted'] == 0, result
assert result['trash_move_failures'] == 1 and 'stuck.png' in result['trash_last_failure'], result
result = output_storage.enforce(machine='m', max_bytes=99, retention_days=1)
assert not stuck.exists(), 'a failed move was not retried'
print('ok')
'''


class OutputTrashFallbackTests(unittest.TestCase):
    def test_a_cross_volume_root_falls_back_and_failures_are_counted(self):
        env = dict(os.environ)
        env['PYTHONPATH'] = str(ROOT)
        result = subprocess.run([sys.executable, '-B', '-c', PROOF], env=env,
                                text=True, capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('ok', result.stdout)


if __name__ == '__main__':
    unittest.main()
