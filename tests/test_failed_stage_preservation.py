"""A failed fresh app stage remains recoverable instead of being removed."""
from pathlib import Path
import importlib.util
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class FailedStagePreservation(unittest.TestCase):
    def test_failed_bundle_is_moved_to_a_unique_archive(self):
        spec = importlib.util.spec_from_file_location('failed_stage_source', ROOT / 'packaging/stage_voice.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertTrue(callable(getattr(module, 'archive_failed_stage', None)),
                        'failed staged bundles need a recoverable archive operation')
        with tempfile.TemporaryDirectory(prefix='stage-preservation-private-') as name:
            project = Path(name)
            failed = project / '.build/Hub Studio KH.app'
            failed.mkdir(parents=True)
            (failed / 'diagnostic').write_text('missing-module failure')
            with patch.object(module, 'ROOT', project):
                saved = module.archive_failed_stage(failed)
            self.assertFalse(failed.exists())
            self.assertEqual((saved / 'diagnostic').read_text(), 'missing-module failure')
            self.assertTrue((project / '_trash') in saved.parents)


if __name__ == '__main__':
    unittest.main()
