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
RETIRED = {'ace-step-1-5-turbo': 'ACE-Step/Ace-Step1.5',
           'lance-3b-video-bf16': 'mlx-community/Lance-3B-Video-bf16',
           'lance-3b-processor-qwen2-5-vl': 'Qwen/Qwen2.5-VL-3B-Instruct',
           'ltx-2-3-22b-ram-12gb-mlx': 'baa-ai/LTX-2.3-22B-RAM-12GB-MLX'}   # LTX-2.3 video, 0.6.127
for package_id, model_id in RETIRED.items():
    old.update(package_id=package_id, model_id=model_id)
    (manifests / f'{package_id}.json').write_text(json.dumps(old))
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

# The seeds: ACE-Step, Lance (video, 0.6.120) and LTX-2.3 (video, 0.6.127) stay out even though an old manifest for each is
# sitting in the catalog folder.
for package_id, model_id in RETIRED.items():
    assert package_id in retired_models.RETIRED_MODELS and model_id in retired_models.RETIRED_MODELS
    for place, ids in offered().items():
        assert package_id not in ids and model_id.lower() not in ids, (place, package_id, ids)
        assert ids, place
    assert standalone._retired_refusal(package_id)['code'] == 'MODEL_RETIRED'
    assert standalone._retired_refusal(model_id)['code'] == 'MODEL_RETIRED'
assert standalone._retired_refusal('whisper-base') is None
# LTX's text encoder is NOT retired: the owner may reuse it as a chat model, so it stays offered,
# unrefused and in the kit listing. It is just no longer anyone's companion.
for place, ids in offered().items():
    assert {'gemma-3-12b-it-4bit', 'mlx-community/gemma-3-12b-it-4bit'} & ids, (place, 'gemma gone', sorted(ids))
assert standalone._retired_refusal('gemma-3-12b-it-4bit') is None
assert standalone._retired_refusal('mlx-community/gemma-3-12b-it-4bit') is None

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
