"""Output cleanup moves work into a self-cleaning Trash Folder; it never deletes an output."""
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]

PROOF = r'''
import hashlib, importlib, os, tempfile, time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

root = Path(tempfile.mkdtemp(prefix='output-trash-')).resolve()
os.environ['STUDIOHUB_DATA_DIR'] = str(root / 'hub')
from components.hub.backend import registry, ledger
importlib.reload(registry); importlib.reload(ledger)
from components.hub import output_storage, standalone_config
importlib.reload(output_storage); importlib.reload(standalone_config)
out = root / 'out'; out.mkdir()
trash = ledger.DATA_DIR / 'trash'

def add(name, data, *, batch, machine='m', generated=1):
    path = out / name; path.write_bytes(data)
    assert output_storage.register_finalized_output(
        machine=machine, studio='image', job_id=name, managed_output_roots=[str(out)],
        output_path=str(path), generated_at=generated, batch_id=batch, item_index=0,
        bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    output_storage.mark_receipt_acked(batch_id=batch, item_index=0)
    return path

def no_unlink(*_a, **_k): raise AssertionError('cleanup hard-deleted an output')
day = lambda offset: time.strftime('%Y-%m-%d', time.gmtime(time.time() - offset * 86400))

# 1. Prune MOVES: the bytes survive, dated and per batch, and stop counting against max_bytes.
old = add('old.png', b'12345', batch='b1')
with patch.object(os, 'unlink', no_unlink), patch.object(Path, 'unlink', no_unlink):
    result = output_storage.enforce(machine='m', max_bytes=99, retention_days=1)
assert not old.exists(), 'expired output stayed in live storage'
moved = list((trash / day(0) / 'b1').glob('*-old.png'))
assert len(moved) == 1 and moved[0].read_bytes() == b'12345', moved
assert result['freed_bytes'] == 5 and result['used_bytes'] == 0, result   # live outputs only
assert output_storage.trash_bytes() == 5

# 2. A failed move skips: the file stays live and indexed, and nothing falls back to a delete.
stuck = add('stuck.png', b'abc', batch='b2')
with patch.object(os, 'rename', side_effect=OSError('fixture: cross-device')), \
        patch.object(os, 'unlink', no_unlink), patch.object(Path, 'unlink', no_unlink):
    result = output_storage.enforce(machine='m', max_bytes=99, retention_days=1)
assert stuck.exists() and result['deleted'] == 0, result
assert result['used_bytes'] == 3, result
result = output_storage.enforce(machine='m', max_bytes=99, retention_days=1)   # and is retried later
assert not stuck.exists() and result['deleted'] == 1, result

# 3. The purge removes only dated folders older than 15 days, and only dated folders.
for offset in (40, 16, 15, 1):
    (trash / day(offset)).mkdir(parents=True, exist_ok=True)
    (trash / day(offset) / 'f').write_bytes(b'x')
(trash / 'not-a-date').mkdir(); (trash / 'not-a-date' / 'f').write_bytes(b'x')
output_storage.enforce(machine='m', max_bytes=99, retention_days=99)
names = {p.name for p in trash.iterdir()}
assert day(40) not in names and day(16) not in names, names
assert day(15) in names and day(1) in names and day(0) in names and 'not-a-date' in names, names
assert output_storage.TRASH_DAYS == 15

# 4. A full Trash Folder cannot wedge admission: with the floor missed, the oldest day goes first.
disk = 1000
def usage(_root): return SimpleNamespace(free=disk - output_storage.trash_bytes())
for stale in list(trash.iterdir()): 
    import shutil; shutil.rmtree(stale)
for offset, size in ((3, 300), (2, 300), (1, 100)):
    (trash / day(offset)).mkdir(); (trash / day(offset) / 'f').write_bytes(b'x' * size)
roots = [str(out)]
with patch.object(output_storage.shutil, 'disk_usage', usage):
    assert output_storage.trash_bytes() == 700
    verdict = output_storage.admission(machine='wedge', max_bytes=99, retention_days=99, enabled=True,
                                       managed_output_roots=roots, minimum_free_bytes=500)
assert verdict is None, verdict                    # 300 free -> purge oldest (day 3) -> 600 >= 500
names = {p.name for p in trash.iterdir()}
assert day(3) not in names and day(2) in names and day(1) in names, ('oldest-first, and no more than needed', names)
# ...and a disk that is full of something else is still refused, with the Trash Folder emptied for it.
disk = 400
with patch.object(output_storage.shutil, 'disk_usage', usage):
    verdict = output_storage.admission(machine='wedge2', max_bytes=99, retention_days=99, enabled=True,
                                       managed_output_roots=roots, minimum_free_bytes=500)
assert verdict and verdict['code'] == 'OUTPUT_STORAGE_BLOCKED', verdict
assert not list(trash.iterdir()), 'refused before emptying the copies already scheduled for deletion'

# 6. A Mac that already saved the OLD defaults gets the new ones; a value the operator chose is kept.
import json
settings_file = ledger.DATA_DIR / 'device_settings.json'
settings_file.write_text(json.dumps({'max_bytes': 50_000_000_000, 'retention_days': 30,
    'minimum_free_bytes': 1_000_000_000, 'cleanup_enabled': True, 'cleanup_interval_seconds': 300}))
eff = standalone_config.load_device_settings()['effective']
assert (eff['max_bytes'], eff['retention_days'], eff['minimum_free_bytes']) == (20_000_000_000, 3, 5_000_000_000), eff
settings_file.write_text(json.dumps({'max_bytes': 60_000_000_000, 'retention_days': 14,
    'minimum_free_bytes': 2_000_000_000, 'cleanup_enabled': False, 'cleanup_interval_seconds': 600}))
eff = standalone_config.load_device_settings()['effective']
assert (eff['max_bytes'], eff['retention_days'], eff['minimum_free_bytes'], eff['cleanup_enabled']) == (
    60_000_000_000, 14, 2_000_000_000, False), eff
# Saving 30 days on purpose afterwards is a choice and is not migrated away.
standalone_config.save_device_settings(max_bytes=50_000_000_000, retention_days=30, minimum_free_bytes=1_000_000_000)
eff = standalone_config.load_device_settings()['effective']
assert (eff['max_bytes'], eff['retention_days'], eff['minimum_free_bytes']) == (50_000_000_000, 30, 1_000_000_000), eff

# 5. Defaults.
assert (standalone_config.DEFAULT_MAX_BYTES, standalone_config.DEFAULT_RETENTION_DAYS,
        standalone_config.DEFAULT_MINIMUM_FREE_BYTES) == (20_000_000_000, 3, 5_000_000_000)
'''


class OutputTrashTests(unittest.TestCase):
    def test_trash_folder_contract(self):
        env = dict(os.environ)
        env['PYTHONPATH'] = str(ROOT)
        result = subprocess.run([sys.executable, '-B', '-c', PROOF], env=env,
                                text=True, capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
