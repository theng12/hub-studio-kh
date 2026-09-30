"""ST-07 storage cleanup uses only disposable fixture outputs."""
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]

PROOF = r'''
import asyncio, hashlib, importlib, os, sqlite3, tempfile
from pathlib import Path
from unittest.mock import patch

root = Path(tempfile.mkdtemp(prefix='output-policy-')).resolve()
os.environ['STUDIOHUB_DATA_DIR'] = str(root / 'hub')
from components.hub.backend import registry, ledger
importlib.reload(registry); importlib.reload(ledger)
from components.hub import output_storage, standalone_config
importlib.reload(output_storage); importlib.reload(standalone_config)
hub = root / 'hub'; hub.mkdir(exist_ok=True)
image, voice, exports = root/'image', root/'voice', root/'exports'
for folder in (image, voice, exports): folder.mkdir()
def add(folder, name, data, *, studio, job, generated, batch, index, roots=None, machine='device-a'):
    path = folder/name; path.write_bytes(data)
    row = output_storage.register_finalized_output(
        machine=machine, studio=studio, job_id=job, managed_output_roots=roots or [str(folder)],
        output_path=str(path), generated_at=generated, batch_id=batch, item_index=index,
        bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    assert row, (studio, job)
    return path

# A settings preview has no storage side effect before the Hub has initialized its index.
assert not ledger.DB_FILE.exists()
output_storage.preview(machine='device-a', max_bytes=1, retention_days=1)
assert not ledger.DB_FILE.exists(), 'preview initialized a database'

# Exact age boundary is retained; immutable generated_at, not copied-file mtime, drives age.
boundary = add(image, 'boundary.png', b'a', studio='image', job='boundary', generated=86400, batch='b', index=0)
old = add(voice, 'old.wav', b'bb', studio='voice', job='old', generated=86399.9, batch='b', index=1)
for index in (0, 1): output_storage.mark_receipt_acked(batch_id='b', item_index=index)
result = output_storage.enforce(machine='device-a', max_bytes=99, retention_days=1, now=172800)
assert boundary.exists() and not old.exists() and result['freed_bytes'] == 2, result

# One physical hard-linked output is never charged twice across studios.
shared = add(image, 'shared.png', b'ccc', studio='image', job='shared-i', generated=1, batch='c', index=0)
linked = voice/'shared.wav'; os.link(shared, linked)
assert output_storage.register_finalized_output(machine='device-a', studio='voice', job_id='shared-v',
    managed_output_roots=[str(voice)], output_path=str(linked), generated_at=1, batch_id='c', item_index=1,
    bytes=3, sha256=hashlib.sha256(b'ccc').hexdigest())
for index in (0, 1): output_storage.mark_receipt_acked(batch_id='c', item_index=index)
snap = output_storage.preview(machine='device-a', max_bytes=99, retention_days=30, now=2)
assert snap['used_bytes'] == 4, snap  # boundary (1) + one shared inode (3)
assert snap['free_bytes'] is not None
output_storage.enforce(machine='device-a', max_bytes=4, retention_days=30, now=2)
assert shared.exists() and linked.exists(), 'exact byte boundary was treated as over-limit'

# A ctime change from unlinking one hard link must not make an otherwise matching
# acknowledged link uncertain. Shared physical bytes are freed only after its last link.
links = root/'links'; links.mkdir()
first = add(links, 'first.bin', b'link', studio='image', job='first', generated=1, batch='links', index=0, machine='links')
second = links/'second.bin'; os.link(first, second)
assert output_storage.register_finalized_output(machine='links', studio='voice', job_id='second',
    managed_output_roots=[str(links)], output_path=str(second), generated_at=1, batch_id='links', item_index=1,
    bytes=4, sha256=hashlib.sha256(b'link').hexdigest())
for index in (0, 1): output_storage.mark_receipt_acked(batch_id='links', item_index=index)
result = output_storage.enforce(machine='links', max_bytes=0, retention_days=1, now=172800)
assert not first.exists() and not second.exists() and result['freed_bytes'] == 4, result

# An active reader's hard link is retained while an unleased duplicate is safely removed.
held = add(links, 'held.bin', b'hold', studio='image', job='held', generated=1, batch='held', index=0, machine='held-links')
protected_link = links/'protected.bin'; os.link(held, protected_link)
assert output_storage.register_finalized_output(machine='held-links', studio='voice', job_id='protected',
    managed_output_roots=[str(links)], output_path=str(protected_link), generated_at=1, batch_id='held', item_index=1,
    bytes=4, sha256=hashlib.sha256(b'hold').hexdigest())
for index in (0, 1): output_storage.mark_receipt_acked(batch_id='held', item_index=index)
release_held = output_storage.acquire_reader(batch_id='held', item_index=1)
result = output_storage.enforce(machine='held-links', max_bytes=0, retention_days=1, now=172800)
assert not held.exists() and protected_link.exists() and result['freed_bytes'] == 0, result
release_held()
result = output_storage.enforce(machine='held-links', max_bytes=0, retention_days=1, now=172800)
assert not protected_link.exists() and result['freed_bytes'] == 4, result

# Unacknowledged files and leases remain protected and block admission.
protected = add(voice, 'protected.wav', b'P'*10, studio='voice', job='protected', generated=1, batch='p', index=0)
blocked = output_storage.admission(machine='device-a', max_bytes=1, retention_days=1, enabled=True)
assert blocked and blocked['code'] == 'OUTPUT_STORAGE_BLOCKED', blocked

# Free disk admission uses the trusted managed root even before it has an index row.
empty_root = root/'empty-output-root'; empty_root.mkdir()
with patch.object(output_storage.shutil, 'disk_usage', return_value=type('Disk', (), {'free': 99})()):
    free_blocked = output_storage.admission(machine='free-space', max_bytes=99, retention_days=1, enabled=True,
        managed_output_roots=[str(empty_root)], minimum_free_bytes=100)
    assert free_blocked and free_blocked['code'] == 'OUTPUT_STORAGE_BLOCKED', free_blocked
    assert output_storage.admission(machine='free-space', max_bytes=99, retention_days=1, enabled=True,
        managed_output_roots=[str(empty_root)], minimum_free_bytes=0) is None
fresh_parent = root/'fresh-parent'; fresh_parent.mkdir()
fresh_leaf = fresh_parent/'output'
with patch.object(output_storage.shutil, 'disk_usage', return_value=type('Disk', (), {'free': 99})()):
    assert output_storage.admission(machine='fresh-output', max_bytes=99, retention_days=1, enabled=True,
        managed_output_roots=[str(fresh_leaf)], minimum_free_bytes=100)
assert not fresh_leaf.exists(), 'space admission recreated a fresh output leaf'
output_storage.mark_receipt_acked(batch_id='p', item_index=0)
release = output_storage.acquire_reader(batch_id='p', item_index=0)
second_reader = output_storage.acquire_reader(batch_id='p', item_index=0)
output_storage.enforce(machine='device-a', max_bytes=1, retention_days=1, now=172800)
assert protected.exists(), 'reader lease was ignored'
release()
release()  # a repeated response-close callback must not steal another reader lease
output_storage.enforce(machine='device-a', max_bytes=1, retention_days=1, now=172800)
assert protected.exists(), 'second reader lease was stolen by duplicate release'
second_reader()
output_storage.enforce(machine='device-a', max_bytes=1, retention_days=1, now=172800)
assert not protected.exists(), 'released output was not reclaimable'
transfer = add(image, 'transfer.png', b'T', studio='image', job='transfer', generated=1, batch='transfer', index=0)
output_storage.mark_receipt_acked(batch_id='transfer', item_index=0)
release_transfer = output_storage.acquire_transfer(batch_id='transfer', item_index=0)
output_storage.enforce(machine='device-a', max_bytes=0, retention_days=1, now=172800)
assert transfer.exists(), 'in-flight receipt transfer was reclaimed'
release_transfer()
output_storage.enforce(machine='device-a', max_bytes=0, retention_days=1, now=172800)
assert not transfer.exists(), 'completed transfer did not release output'

# Leases are process-local. A Hub restart drops only stale counters and retries retention.
stale = add(image, 'stale.png', b'S', studio='image', job='stale', generated=1, batch='stale', index=0, machine='restart')
output_storage.mark_receipt_acked(batch_id='stale', item_index=0)
output_storage.acquire_reader(batch_id='stale', item_index=0)
importlib.reload(output_storage)
output_storage.recover_process_leases()
output_storage.enforce(machine='restart', max_bytes=0, retention_days=1, now=172800)
assert not stale.exists(), 'fresh Hub process retained a dead reader lease'

# A replacement after registration becomes uncertain rather than being unlinked.
replaced = add(image, 'replace.png', b'old', studio='image', job='replace', generated=1, batch='replace', index=0)
output_storage.mark_receipt_acked(batch_id='replace', item_index=0)
replacement = image/'replacement.tmp'; replacement.write_bytes(b'new')
replacement.replace(replaced)
output_storage.enforce(machine='device-a', max_bytes=0, retention_days=1, now=172800)
assert replaced.read_bytes() == b'new', 'replacement was deleted without identity recheck'

# Derivatives may have distinct byte counts and checksums.
main = image/'main.png'; thumb = image/'thumb.png'; main.write_bytes(b'main'); thumb.write_bytes(b'x')
assert output_storage.register_finalized_output(machine='device-a', studio='image', job_id='derivatives',
    managed_output_roots=[str(image)], output_path=str(main), generated_at=1, batch_id='d', item_index=0,
    bytes=4, sha256=hashlib.sha256(b'main').hexdigest(), derivatives=[{
        'path': str(thumb), 'bytes': 1, 'sha256': hashlib.sha256(b'x').hexdigest()}])
output_storage.mark_receipt_acked(batch_id='d', item_index=0)
output_storage.enforce(machine='device-a', max_bytes=0, retention_days=1, now=172800)
assert not main.exists() and not thumb.exists(), 'unequal derivative group was not reclaimable'

# Preview reports the index but does not reconcile or alter it.
readonly = add(image, 'readonly.png', b'R', studio='image', job='readonly', generated=1, batch='readonly', index=0)
output_storage.mark_receipt_acked(batch_id='readonly', item_index=0)
os.utime(readonly, (2, 2))
output_storage.preview(machine='device-a', max_bytes=0, retention_days=1, now=172800)
with ledger._conn() as conn:
    state = conn.execute("SELECT state FROM output_groups WHERE batch_id='readonly'").fetchone()['state']
assert state == 'finalized', 'read-only preview reconciled storage state'
with patch.object(ledger, '_conn', side_effect=sqlite3.OperationalError('database is locked')):
    try: output_storage.preview(machine='device-a', max_bytes=0, retention_days=1)
    except sqlite3.OperationalError: pass
    else: raise AssertionError('locked output index was reported as empty')

# An output-index failure after a worker completed is recorded conservatively;
# it does not put the already-finished inference back through dispatch.
from components.hub.backend import broker
completed = {'id': 'completed-index-failure', 'modality': 'image', 'model': 'fixture/image',
             'created_at': 1, 'cancelled': False, 'items': []}
completed_item = {'index': 0, 'state': 'running', 'seed': None, 'prompt': 'fixture', 'tries': 1}
completed['items'] = [completed_item]
completed_job = {'id': 'image-job', 'output_path': str(image/'index-failure.png'),
                 'output_url': '/api/generate/jobs/image-job/image', 'bytes': 1,
                 'sha256': hashlib.sha256(b'x').hexdigest(), 'media_type': 'image/png',
                 'generated_at': 1, 'finished_at': 1}
storage_calls = []
with patch.object(output_storage, 'register_finalized_output', side_effect=lambda **_: storage_calls.append(1) or (_ for _ in ()).throw(OSError('index unavailable'))):
    asyncio.run(broker._record_worker_success(None, completed, completed_item,
        {'id': 'image', 'machine': 'device-a', 'host': '127.0.0.1', 'port': 1}, completed_job, {}, 0))
    asyncio.run(broker._record_worker_success(None, completed, completed_item,
        {'id': 'image', 'machine': 'device-a', 'host': '127.0.0.1', 'port': 1}, completed_job, {}, 0))
assert completed_item['state'] == 'done' and 'OSError: index unavailable' in completed_item['output_storage_error']
assert storage_calls == [1], 'completed worker result re-entered output registration/dispatch'

# Active and uncertain recovery records are never reclaimed, even with a receipt.
active = add(image, 'active.png', b'A', studio='image', job='active', generated=1, batch='state', index=0)
uncertain = add(voice, 'uncertain.wav', b'U', studio='voice', job='uncertain', generated=1, batch='state', index=1)
for index in (0, 1): output_storage.mark_receipt_acked(batch_id='state', item_index=index)
with ledger._conn() as conn:
    conn.execute("UPDATE output_groups SET state='active' WHERE batch_id='state' AND item_index=0")
    conn.execute("UPDATE output_groups SET state='uncertain' WHERE batch_id='state' AND item_index=1")
output_storage.enforce(machine='device-a', max_bytes=0, retention_days=1, now=172800)
assert active.exists() and uncertain.exists(), 'active or uncertain work was reclaimed'
with ledger._conn() as conn:
    conn.execute("UPDATE output_groups SET state='finalized' WHERE batch_id='state'")
output_storage.enforce(machine='device-a', max_bytes=0, retention_days=1, now=172800)
assert not active.exists() and not uncertain.exists(), 'terminal work remained permanently protected'

# A changed managed root keeps the original indexed root; exports and symlinks never enter it.
historic = add(image, 'historic.png', b'H', studio='image', job='historic', generated=1, batch='h', index=0)
output_storage.mark_receipt_acked(batch_id='h', item_index=0)
external = exports/'copy.png'; external.write_bytes(b'H')
assert output_storage.register_finalized_output(machine='device-a', studio='image', job_id='external',
    managed_output_roots=[str(image)], output_path=str(external), generated_at=1) is None
escape = image/'escape.png'; escape.symlink_to(external)
assert output_storage.register_finalized_output(machine='device-a', studio='image', job_id='escape',
    managed_output_roots=[str(image)], output_path=str(escape), generated_at=1) is None
output_storage.enforce(machine='device-a', max_bytes=1, retention_days=1, now=172800)
assert not historic.exists() and external.exists(), 'historic root or explicit export handling failed'

# A mounted managed root can recover only when every original identity and hash returns.
removable = root/'removable'; removable.mkdir()
reconnected = add(removable, 'reconnected.png', b'R', studio='image', job='reconnected', generated=1, batch='reconnected', index=0, machine='reconnect')
output_storage.mark_receipt_acked(batch_id='reconnected', item_index=0)
offline = root/'offline-root'; removable.rename(offline)
output_storage.enforce(machine='reconnect', max_bytes=0, retention_days=1, now=172800)
with ledger._conn() as conn:
    assert conn.execute("SELECT state FROM output_groups WHERE batch_id='reconnected'").fetchone()['state'] == 'uncertain'
offline.rename(removable)
output_storage.enforce(machine='reconnect', max_bytes=0, retention_days=1, now=172800)
assert not reconnected.exists(), 'matching remounted output remained permanently uncertain'

# Restart reconciles a group interrupted after one of its derivative files is removed.
partial = image/'partial.png'; derivative = image/'partial-preview.png'
partial.write_bytes(b'12'); derivative.write_bytes(b'12')
assert output_storage.register_finalized_output(machine='device-a', studio='image', job_id='partial',
    managed_output_roots=[str(image)], output_path=str(partial), derivatives=[str(derivative)],
    generated_at=1, batch_id='q', item_index=0, bytes=2, sha256=hashlib.sha256(b'12').hexdigest())
output_storage.mark_receipt_acked(batch_id='q', item_index=0)
original, calls = os.rename, {'count': 0}
def interrupted(src, dst):
    calls['count'] += 1
    if calls['count'] == 1: return original(src, dst)
    raise OSError('fixture interruption')
with patch.object(os, 'rename', interrupted):
    output_storage.enforce(machine='device-a', max_bytes=0, retention_days=1, now=172800)
importlib.reload(output_storage)
output_storage.enforce(machine='device-a', max_bytes=0, retention_days=1, now=172800)
assert not partial.exists() and not derivative.exists(), 'restart reconciliation did not finish partial cleanup'

# A permission error is retained safely and becomes reclaimable once re-permitted.
locked = add(voice, 'locked.wav', b'LOCK', studio='voice', job='locked', generated=1, batch='r', index=0)
output_storage.mark_receipt_acked(batch_id='r', item_index=0)
voice.chmod(0o500)
output_storage.enforce(machine='device-a', max_bytes=0, retention_days=1, now=172800)
assert locked.exists(), 'permission failure was treated as deletion'
voice.chmod(0o700)
output_storage.enforce(machine='device-a', max_bytes=0, retention_days=1, now=172800)
assert not locked.exists(), 're-permission did not allow a later safe cleanup'

# Preview validates without persistence; save is the durable owner acknowledgement.
assert not (hub/'device_settings.json').exists()
preview = standalone_config.preview_device_settings(max_bytes=7, retention_days=2, cleanup_enabled=False, cleanup_interval_seconds=300)
assert preview['max_bytes'] == 7 and not (hub/'device_settings.json').exists()
standalone_config.save_device_settings(**preview)
assert standalone_config.load_device_settings()['effective'] == preview
'''


class OutputPolicyTests(unittest.TestCase):
    def test_shared_cleanup_fixture_contract(self):
        env = dict(os.environ)
        env['PYTHONPATH'] = str(ROOT)
        result = subprocess.run([sys.executable, '-B', '-c', PROOF], env=env,
                                text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
