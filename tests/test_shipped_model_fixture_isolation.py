"""Shipped-model fixtures honour an explicit library root (the gate's empty temp library).

R7: with the gate's empty ``STUDIOHUB_MODELS_DIR``, ``test_vision_worker`` still resolved
``Path.home()/StudioHubModels``, found the owner's real checkpoints and ran its shipped-model
test - one ERROR at import because ``tokenizers`` is not in the gate interpreter. A fixture that
reads checkpoints must resolve the configured root first; the negative control proves it still
finds the home library when nothing is configured, so this is isolation, not a disabled test.
"""
from __future__ import annotations

import importlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import _model_library

VISION = importlib.import_module('test_vision_worker')
EMBEDDINGS = importlib.import_module('test_embeddings_worker')


def _checkpoints(root: Path) -> None:
    """The shape each fixture's skip test looks for, without any real weights."""
    for manifest in (VISION.DEPTH, VISION.BASE, EMBEDDINGS.MANIFEST):
        folder = root / manifest['package_id'] / manifest['revision'] / 'model'
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'model.safetensors').write_bytes(b'fixture')


class ShippedModelFixtureIsolationTests(unittest.TestCase):
    def test_an_explicit_empty_library_wins_over_the_operators_home(self):
        with tempfile.TemporaryDirectory() as temporary:
            home, empty = Path(temporary) / 'home', Path(temporary) / 'empty-models'
            _checkpoints(home / 'StudioHubModels')
            empty.mkdir()
            with patch.dict(os.environ, {'HOME': str(home), 'STUDIOHUB_MODELS_DIR': str(empty)}):
                self.assertEqual(_model_library.shipped_model_library(), empty)
                vision = importlib.reload(VISION)
                embeddings = importlib.reload(EMBEDDINGS)
                self.assertEqual(vision._LIBRARY, empty)
                for folder in vision._INSTALLED.values():
                    self.assertFalse((folder / 'model.safetensors').is_file(), folder)
                self.assertTrue(embeddings._INSTALLED.is_relative_to(empty))
                self.assertFalse((embeddings._INSTALLED / 'model.safetensors').is_file())

    def test_without_a_configured_root_the_home_library_is_still_found(self):
        # Negative control: the same home shapes ARE found when nothing is configured, so the
        # test above proves the gate root isolates it - not that the fixture never looks.
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary) / 'home'
            _checkpoints(home / 'StudioHubModels')
            with patch.dict(os.environ, {'HOME': str(home), 'STUDIOHUB_MODELS_DIR': ''}):
                self.assertEqual(_model_library.shipped_model_library(),
                                 home / 'StudioHubModels')
                vision = importlib.reload(VISION)
                self.assertTrue(all((folder / 'model.safetensors').is_file()
                                    for folder in vision._INSTALLED.values()))


if __name__ == '__main__':
    unittest.main()
