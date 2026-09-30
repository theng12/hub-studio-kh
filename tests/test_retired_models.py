"""One retired-models list: adding an id to it removes the model from every place it is offered."""
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]

PROOF = r'''
import asyncio, json, os, shutil, sys, tempfile
from pathlib import Path

ROOT = Path(os.environ['PROJECT_ROOT'])
tmp = Path(tempfile.mkdtemp(prefix='retired-models-')).resolve()
os.environ['STUDIOHUB_DATA_DIR'] = str(tmp / 'data')
manifests = tmp / 'manifests'
shutil.copytree(ROOT / 'model-manifests', manifests, ignore=shutil.ignore_patterns('*.md'))
# An OLD manifest for the seed model, as one left on a drive would be.
old = json.loads((manifests / 'whisper-base.json').read_text())
old.update(package_id='ace-step-1-5-turbo', model_id='ACE-Step/Ace-Step1.5')
(manifests / 'ace-step-1-5-turbo.json').write_text(json.dumps(old))
os.environ['STUDIOHUB_MODEL_MANIFESTS'] = str(manifests)

from components import retired_models
from components.hub import standalone
from components.hub.backend import model_ids
from components.hub.backend.monitor import StudioMonitor
sys.path.insert(0, str(ROOT / 'packaging'))
import release_kit, stage_voice

def offered():
    """Every place a model is listed, as {place: set of package ids}."""
    app = tmp / 'app' / 'Studio Hub.app'
    resources = app / 'Contents' / 'Resources'
    shutil.rmtree(app.parent, ignore_errors=True)
    resources.mkdir(parents=True)
    shutil.copytree(manifests, resources / 'model-manifests')
    (resources / 'release-manifest.json').write_text('{}')
    library = tmp / 'library'; library.mkdir(exist_ok=True)
    staged = tmp / 'staged'; shutil.rmtree(staged, ignore_errors=True)
    stage_voice.copy_model_manifests(staged)
    return {
        'catalog': set(standalone._model_catalog()),
        'identity': set(model_ids.load_index()),
        'kit listing': {m['package_id'] for m in release_kit.build(app, library)['models']},
        'staged into kits': {p.stem for p in staged.glob('*.json')},
    }

# The seed: ACE-Step stays out even though an old manifest for it is sitting in the catalog folder.
assert 'ace-step-1-5-turbo' in retired_models.RETIRED_MODELS
for place, ids in offered().items():
    assert 'ace-step-1-5-turbo' not in ids and 'ACE-Step/Ace-Step1.5'.lower() not in ids, (place, ids)
    assert ids, place
assert standalone._retired_refusal('ace-step-1-5-turbo')['code'] == 'MODEL_RETIRED'
assert standalone._retired_refusal('ACE-Step/Ace-Step1.5')['code'] == 'MODEL_RETIRED'
assert standalone._retired_refusal('whisper-base') is None

# One line in the list removes a live model from the catalog, the identity index, the kit listing
# and staging, and refuses it by name...
before = offered()
assert all('whisper-base' in ids for ids in before.values()), before
retired_models.RETIRED_MODELS.append('whisper-base')
after = offered()
gone = {'whisper-base', json.loads((manifests / 'whisper-base.json').read_text())['model_id'].lower()}
for place, ids in after.items():
    assert ids == before[place] - gone and 'whisper-base' not in ids, (place, ids ^ before[place])
assert standalone._retired_refusal('whisper-base')['code'] == 'MODEL_RETIRED'
# ...and from the list published to the gateway (the aggregate catalog every route reads).
class Monitor:
    registry = [{'id': 'chat', 'modality': 'chat', 'machine': 'local'}]
    def catalog_observation(self, *_a, **_k): return {'observed_at': 0, 'age_seconds': 0, 'stale': False, 'last_error': None}
retired_models.RETIRED_MODELS.append('org/retired-row')
rows = [{'repo': 'org/kept', 'cache': {'state': 'cached'}}, {'repo': 'org/retired-row', 'cache': {'state': 'cached'}},
        {'repo': 'org/other', 'model_id': 'ORG/RETIRED-ROW', 'cache': {'state': 'cached'}}]
published = StudioMonitor._assemble_aggregate_catalog(Monitor(), [{'models': rows}], [])
assert [row['repo'] for row in published['models']] == ['org/kept'], published['models']

# Taking the id back out of the list brings the model back: the list alone decides.
retired_models.RETIRED_MODELS.remove('whisper-base')
assert offered() == before
'''


class RetiredModelsTests(unittest.TestCase):
    def test_one_list_retires_a_model_everywhere_it_is_offered(self):
        env = dict(os.environ)
        env['PROJECT_ROOT'] = str(ROOT)
        env['PYTHONPATH'] = os.pathsep.join(filter(None, [str(ROOT), env.get('PYTHONPATH')]))
        result = subprocess.run([sys.executable, '-B', '-c', PROOF], env=env,
                                text=True, capture_output=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
