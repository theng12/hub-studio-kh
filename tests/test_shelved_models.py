"""One shelved-models list: offered and usable, but not carried by a Mac that is not marked keep-all.

`components/shelved_models.py` (`SHELVED_MODELS`) sits beside the retired list. A shelved model stays in
the catalog; the kit's copy step skips it, the Hub moves an installed one into its Trash Folder and refuses
to fetch it back, and a Mac marked `keep_all_models` does none of that.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from test_copy_models_for_this_mac import Harness

ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = ROOT / "model-manifests"
sys.path.insert(0, str(ROOT))
from components import retired_models, shelved_models  # noqa: E402

SEEDS = {"whisper-base": "mlx-community/whisper-base-mlx", "whisper-small": "mlx-community/whisper-small-mlx",
         "voxcpm2-bf16": "mlx-community/VoxCPM2-bf16", "fish-audio-s2-pro-bf16": "mlx-community/fish-audio-s2-pro-bf16",
         "chatterbox-4bit": "mlx-community/chatterbox-4bit", "chatterbox-8bit": "mlx-community/chatterbox-8bit",
         "s3tokenizerv2": "mlx-community/S3TokenizerV2"}


def manifests():
    return [json.loads(path.read_text()) for path in sorted(MANIFESTS.glob("*.json"))]


class ListTests(unittest.TestCase):
    def test_the_seeds_are_the_ones_the_owner_shelved_under_their_manifest_ids(self):
        by_id = {m["package_id"]: m for m in manifests()}
        for package_id, repo in SEEDS.items():
            self.assertEqual(by_id[package_id]["model_id"], repo)
            self.assertTrue(shelved_models.manifest_shelved(by_id[package_id]), package_id)
            self.assertTrue(shelved_models.is_shelved(repo.upper()), "matching ignores case")

    def test_every_entry_names_a_real_manifest_and_nothing_else_is_shelved(self):
        names = {n.lower() for m in manifests() for n in (m["package_id"], m["model_id"])}
        for entry in shelved_models.SHELVED_MODELS:
            self.assertIn(entry.lower(), names, f"{entry} names no manifest: a typo shelves nothing")
        self.assertEqual({m["package_id"] for m in manifests() if shelved_models.manifest_shelved(m)}, set(SEEDS))

    def test_turbo_is_never_shelved_and_is_the_subtitle_default(self):
        turbo = json.loads((MANIFESTS / "whisper-turbo.json").read_text())
        self.assertFalse(shelved_models.manifest_shelved(turbo))
        self.assertIs(turbo.get("default"), True, "Whisper turbo is the one subtitle manifest that declares default")
        self.assertEqual([m["package_id"] for m in manifests()
                          if m.get("engine") == "subtitle-mlx" and m.get("default") is True], ["whisper-turbo"])

    def test_a_shelved_model_is_not_something_a_kept_model_needs(self):
        needed = {dep for m in manifests() if not shelved_models.manifest_shelved(m)
                  for dep in (m.get("requires_packages") or [])}
        self.assertFalse(needed & set(SEEDS), "a kept model requires a shelved one, which would be moved away")

    def test_shelved_and_retired_are_different_lists(self):
        self.assertFalse(shelved_models.is_shelved(*retired_models.RETIRED_MODELS))
        self.assertFalse(retired_models.is_retired(*shelved_models.SHELVED_MODELS))


class KeepAllSettingTests(unittest.TestCase):
    def test_only_a_literal_true_marks_a_mac_keep_all(self):
        with TemporaryDirectory() as tmp:
            file = Path(tmp) / "device_settings.json"
            self.assertFalse(shelved_models.keep_all_models(file))
            for text, expected in (('{"keep_all_models": true}', True), ('{"keep_all_models": false}', False),
                                   ('{"keep_all_models": "true"}', False), ('{"keep_all_models": 1}', False),
                                   ("{}", False), ("not json", False), ("[]", False)):
                file.write_text(text)
                self.assertIs(shelved_models.keep_all_models(file), expected, text)


class CopyStepTests(unittest.TestCase):
    """The kit's copy step reads the list and the device setting; the SSD keeps the weights."""

    def setUp(self):
        self.addCleanup(sys.modules.pop, "shelved_models", None)   # the script imports it by bare name

    def _kit(self, root: Path) -> Harness:
        kit = Harness(root)
        kit.ship("plain-model", floor=1)
        kit.ship("whisper-base", floor=1)           # a shelved package id, with weights on the SSD
        (kit.kit / kit.APP / "Contents/Resources/components/shelved_models.py").write_text(
            (ROOT / "components" / "shelved_models.py").read_text())
        return kit

    def test_a_shelved_model_is_not_copied_but_stays_on_the_ssd(self):
        with TemporaryDirectory() as tmp:
            kit = self._kit(Path(tmp))
            code, output, _prompt = kit.run()
            self.assertEqual(code, 0, output)
            self.assertTrue((kit.home / "plain-model").is_dir(), output)
            self.assertFalse((kit.home / "whisper-base").exists(), "a shelved model was copied to a Mac that does not keep it")
            self.assertTrue((kit.library / "whisper-base").is_dir(), "the SSD must keep the shelved weights")
            self.assertIn("Shelved, not copied (1): whisper-base", output)

    def test_a_keep_all_mac_copies_it(self):
        with TemporaryDirectory() as tmp:
            kit = self._kit(Path(tmp))
            settings = kit.root / "Library/Application Support/StudioHub Standalone/hub/device_settings.json"
            settings.parent.mkdir(parents=True)
            settings.write_text('{"keep_all_models": true}')
            code, output, _prompt = kit.run()
            self.assertEqual(code, 0, output)
            self.assertTrue((kit.home / "whisper-base").is_dir(), "a keep-all Mac must receive shelved models")
            self.assertNotIn("Shelved, not copied", output)

    def test_one_already_on_a_mac_is_left_for_the_hub_not_pruned_by_the_copy_step(self):
        with TemporaryDirectory() as tmp:
            kit = self._kit(Path(tmp))
            kit.receive("whisper-base")
            kit.record("whisper-base")
            code, output, _prompt = kit.run()
            self.assertEqual(code, 0, output)
            self.assertTrue((kit.home / "whisper-base").is_dir())
            self.assertEqual(kit.trash(), [])


PROOF = r'''
import json, os, shutil, sys, tempfile, time
from pathlib import Path
from unittest import mock

ROOT = Path(os.environ['PROJECT_ROOT'])
tmp = Path(tempfile.mkdtemp(prefix='shelved-models-')).resolve()
os.environ['STUDIOHUB_DATA_DIR'] = str(tmp / 'data')
os.environ['STUDIOHUB_MODELS_DIR'] = str(tmp / 'lib')
os.environ['STUDIOHUB_MODEL_MANIFESTS'] = str(ROOT / 'model-manifests')
(tmp / 'lib').mkdir()

from components import shelved_models
from components.hub import output_storage, standalone, standalone_config
from fastapi import HTTPException

settings = tmp / 'data' / 'device_settings.json'
SHELVED = ['whisper-base', 'whisper-small', 'voxcpm2-bf16', 'fish-audio-s2-pro-bf16',
           'chatterbox-4bit', 'chatterbox-8bit', 's3tokenizerv2']

# The default is NOT keep-all, and it is a device setting that survives "restore defaults".
assert standalone_config.load_device_settings()['defaults']['keep_all_models'] is False
assert standalone_config.load_device_settings()['effective']['keep_all_models'] is False
saved = standalone_config.save_device_settings(max_bytes=10**9, retention_days=3, keep_all_models=True)
assert saved['effective']['keep_all_models'] is True and json.loads(settings.read_text())['keep_all_models'] is True
standalone_config.save_device_settings(max_bytes=10**9, retention_days=4)          # a save that does not name it keeps it
assert standalone_config.load_device_settings()['effective']['keep_all_models'] is True
assert standalone_config.restore_device_defaults()['effective']['keep_all_models'] is True
try:
    standalone_config.preview_device_settings(keep_all_models='yes'); raise SystemExit('a non-boolean was accepted')
except ValueError:
    pass
standalone_config.save_device_settings(max_bytes=10**9, retention_days=3, keep_all_models=False)
assert standalone_config.load_device_settings()['effective']['keep_all_models'] is False

# Shelved models stay in the catalog (offered), whatever the Mac keeps.
catalog = standalone._model_catalog()
for package_id in SHELVED + ['whisper-turbo']:
    assert package_id in catalog and not catalog[package_id].get(standalone._MANIFEST_ERROR), package_id

class Monitor:
    registry = []
def row(package_id):
    return standalone._model_state(catalog[package_id], catalog, Monitor(), {})

# Installed shelved folders (a fleet Mac that had them) and a kept one.
library = tmp / 'lib'
for package_id in SHELVED + ['whisper-turbo']:
    (library / package_id / 'rev').mkdir(parents=True)
    (library / package_id / 'rev' / 'weights.bin').write_text(package_id)

# KEEP-ALL: nothing moves; downloads are allowed.
standalone_config.save_device_settings(max_bytes=10**9, retention_days=3, keep_all_models=True)
assert standalone.shelve_installed_models() == []
assert all((library / p / 'rev' / 'weights.bin').is_file() for p in SHELVED)
standalone._shelved_refusal(catalog['whisper-base'])                      # no refusal on a keep-all Mac

# FLEET MAC (default): shelved folders move into the Trash Folder, the kept one stays.
standalone_config.save_device_settings(max_bytes=10**9, retention_days=3, keep_all_models=False)
moved = standalone.shelve_installed_models()
assert sorted(moved) == sorted(SHELVED), moved
trash = output_storage.trash_root()
assert (library / 'whisper-turbo' / 'rev' / 'weights.bin').is_file(), 'turbo must never be shelved'
today = time.strftime('%Y-%m-%d', time.gmtime())
for package_id in SHELVED:
    assert not (library / package_id).exists(), package_id
    found = list((trash / today / 'shelved-models').glob(package_id + '-*'))
    assert len(found) == 1 and (found[0] / 'rev' / 'weights.bin').read_text() == package_id, (package_id, found)
assert standalone.shelve_installed_models() == [], 'a second start has nothing left to move'

# ...it is the Hub's self-cleaning Trash Folder: the dated folder is purged after 15 days, not before.
assert output_storage.purge_trash(time.time() + 14 * 86400) == 0 and (trash / today).is_dir()
assert output_storage.purge_trash(time.time() + 16 * 86400) == 1 and not (trash / today).exists()

# Once moved, the catalog says "not on this Mac" - a state the app already reads as not installed, never invalid.
for package_id in SHELVED:
    r = row(package_id)
    assert r['shelved'] is True and r['state'] == 'not_installed' and r['files_state'] == 'not_installed', r
    assert r['dispatch_state'] == 'ineligible' and not r.get('manifest_error'), r
    assert r['reason'].startswith('Not on this Mac:') and package_id in r['reason'], r['reason']
assert row('whisper-turbo')['shelved'] is False
# ...and nothing brings it back by itself: download and import are refused on a Mac that does not keep it.
for package_id in SHELVED:
    try:
        standalone._shelved_refusal(catalog[package_id]); raise SystemExit('refusal missing for ' + package_id)
    except HTTPException as refusal:
        assert refusal.status_code == 409 and 'shelved model' in refusal.detail
standalone._shelved_refusal(catalog['whisper-turbo'])
# A keep-all Mac that does not have one just sees an ordinary not-installed row (it may download it).
standalone_config.save_device_settings(max_bytes=10**9, retention_days=3, keep_all_models=True)
assert not row('whisper-base')['reason'].startswith('Not on this Mac:')
# A shelved model whose files ARE here is usable like any other: the row is not rewritten.
from components import model_packages
standalone_config.save_device_settings(max_bytes=10**9, retention_days=3, keep_all_models=False)
with mock.patch.object(model_packages, 'preflight', return_value={'package_id': 'whisper-base', 'state': 'files_verified'}):
    r = row('whisper-base')
assert r['shelved'] is True and r['files_state'] == 'files_verified' and 'reason' not in r, r
# A moved folder that cannot be renamed is skipped, never deleted.
(library / 'whisper-base' / 'rev').mkdir(parents=True)
with mock.patch('os.rename', side_effect=OSError('cross-device')):
    assert standalone.shelve_installed_models() == [] and (library / 'whisper-base' / 'rev').is_dir()
'''


class HubTests(unittest.TestCase):
    def test_the_sweep_runs_when_the_hub_starts(self):
        source = (ROOT / "components/hub/standalone.py").read_text()
        started = source[source.index("if start_background:"):]
        self.assertIn("shelve_installed_models()", started[:started.index("monitor.start()")])

    def test_the_settings_route_saves_the_keep_all_choice(self):
        source = (ROOT / "components/hub/standalone.py").read_text()
        self.assertIn('keep_all_models=value["keep_all_models"]', source)

    def test_the_hub_sheds_shelves_keeps_the_catalog_and_never_fetches_them_back(self):
        env = dict(os.environ)
        env['PROJECT_ROOT'] = str(ROOT)
        env['PYTHONPATH'] = os.pathsep.join(filter(None, [str(ROOT), env.get('PYTHONPATH')]))
        result = subprocess.run([sys.executable, '-B', '-c', PROOF], env=env,
                                text=True, capture_output=True, timeout=180)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
