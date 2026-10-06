"""Every image model the catalog can offer has a memory profile, or a documented exemption.

The rules live in ``docs/mlx-memory-discipline.md``. The failure this guards against is the one the 8 GB
runs found: a model added to the Image catalog with no memory configuration runs with MLX's defaults -
every component resident at once, an unbounded buffer cache, and a VAE decode that alone peaked at 9 GB -
and nobody notices until it swaps an 8 GB Mac. A model is therefore not complete until it either declares a
bounded configuration (``MemoryPolicy.low_ram_below_bytes``, with its cache cap and the stage/stream
declarations the worker needs to apply it) or says, in its own words and in the doc, why it has none
(``MemoryPolicy.single_mode_reason``).
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from components.image.app.backend import generation  # noqa: E402

DOC = PROJECT_ROOT / 'docs' / 'mlx-memory-discipline.md'


class ImageMemoryProfileGuard(unittest.TestCase):
    def test_every_image_catalog_engine_model_is_registered_in_the_worker(self):
        """The catalog's image manifests and the worker's registry cannot drift apart silently."""
        manifests = []
        for path in sorted((PROJECT_ROOT / 'model-manifests').glob('*.json')):
            data = json.loads(path.read_text())
            if data.get('engine') in generation.IMAGE_ENGINES:
                manifests.append(data['package_id'])
        self.assertTrue(manifests, 'no Image engine manifest found at all')
        served = {model.package_id for model in generation.MODELS.values()}
        # Retired or unregistered packages are reported by the worker (`packages_without_a_runtime_model`);
        # what this guard needs is that nothing it *does* serve lacks a manifest.
        for package in served:
            self.assertIn(package, manifests, f'{package} is registered but has no image manifest')

    def test_every_registered_model_has_a_memory_profile_or_a_documented_exemption(self):
        doc = DOC.read_text(encoding='utf-8')
        for model in generation.MODELS.values():
            policy = model.memory
            with self.subTest(model=model.repo):
                if policy.low_ram_below_bytes is None:
                    self.assertTrue(
                        policy.single_mode_reason and len(policy.single_mode_reason) > 40,
                        f'{model.package_id} declares no bounded configuration and gives no reason: set '
                        f'MemoryPolicy(low_ram_below_bytes=...) or MemoryPolicy(single_mode_reason=...)')
                    self.assertIn(model.package_id, doc,
                                  f'{model.package_id} is exempt from the memory profile: say why in {DOC.name}')
                    continue
                self.assertIsNotNone(policy.low_ram_cache_limit_gb, 'a bounded configuration caps the MLX cache')
                self.assertGreater(policy.low_ram_cache_limit_gb, 0)
                self.assertLessEqual(policy.low_ram_cache_limit_gb, 2.0)
                # The stage hooks and block streaming need the model's own loader; a profile that names a
                # streamed component without it cannot be applied.
                if policy.stream_below_bytes:
                    self.assertTrue(policy.streamed_components and policy.weight_definition)
                self.assertIn(model.package_id, doc,
                              f'{model.package_id} has a memory profile that {DOC.name} does not describe')

    def test_the_doc_has_the_image_and_voice_sections(self):
        text = DOC.read_text(encoding='utf-8')
        for heading in ('## Image', '## Voice'):
            self.assertIn(heading, text)


if __name__ == '__main__':
    unittest.main()
