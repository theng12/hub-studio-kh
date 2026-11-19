"""Image 0.6.253: the catalogue publishes renderable geometry beside the offered sizes."""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(PROJECT_ROOT), str(Path(__file__).resolve().parent)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from fastapi.testclient import TestClient  # noqa: E402

from components.image.app.backend import main as image_main  # noqa: E402
from components.image.app.backend.generation import (  # noqa: E402
    FLUX2_KLEIN, QWEN_IMAGE_21_LOW_RAM, size_presets)
from components.image.app.backend.main import create_app  # noqa: E402

TOKEN = 'imageownercred_' * 3
HOST = {'eligible': True, 'macos': '26.6.2', 'unified_memory_bytes': 32 * 1024 ** 3}


class CatalogManager:
    """Only the manager surface a catalog read touches; no files or engine needed."""

    def __init__(self, data: Path):
        self.data = data
        self.total_memory_bytes = HOST['unified_memory_bytes']
        self.runtime_sha256 = 'a' * 64

    def preflight(self, repo):
        return {'state': 'files_verified'}

    def production_admission(self, repo):
        return {'allowed': True, 'production_allowed': True,
                'qualification_allowed': True, 'reason': None}

    def packages_without_a_runtime_model(self):
        return []


class RenderableSizesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=PROJECT_ROOT)
        self.addCleanup(self.temporary.cleanup)
        host = patch.object(image_main, '_host_facts', return_value=dict(HOST))
        host.start()
        self.addCleanup(host.stop)
        manager = CatalogManager(Path(self.temporary.name))
        client = TestClient(create_app(manager, TOKEN))
        response = client.get('/api/catalog', headers={'X-Studio-Token': TOKEN})
        self.assertEqual(response.status_code, 200, response.text)
        self.entries = {entry['repo']: entry for entry in response.json()['models']}

    def test_multiple_is_the_models_own_engine_bound_and_bounds_are_not_invented(self):
        for model, expected_multiple in ((FLUX2_KLEIN, 1), (QWEN_IMAGE_21_LOW_RAM, 32)):
            constraints = self.entries[model.repo]['render_constraints']
            self.assertEqual(model.dimension_multiple or 1, expected_multiple, model.repo)
            self.assertEqual(constraints['multiple'], model.dimension_multiple or 1)
            # ImageModel.limits carries no canvas bounds; null is the honest answer.
            self.assertIsNone(constraints['min'])
            self.assertIsNone(constraints['max'])

    def test_sizes_are_the_offered_preset_pixels_and_image_sizes_is_untouched(self):
        for model in (FLUX2_KLEIN, QWEN_IMAGE_21_LOW_RAM):
            entry = self.entries[model.repo]
            presets = size_presets(model)
            self.assertEqual(entry['render_constraints']['sizes'],
                             [{'width': width, 'height': height}
                              for width, height in presets.values()])
            self.assertEqual(entry['image_sizes'],
                             [{'id': ratio, 'label': ratio, 'width': width, 'height': height}
                              for ratio, (width, height) in presets.items()])


if __name__ == '__main__':
    unittest.main()
