"""The app release archive is preserved on replacement and failed staging."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class RecoverableDmgFiles(unittest.TestCase):
    def test_two_same_name_replacements_keep_separate_archive_items(self):
        with tempfile.TemporaryDirectory(prefix='qwen-dmg-private-') as name:
            root = Path(name)
            self.assertTrue((ROOT / 'packaging/make_dmg.py').is_file(), 'tracked recoverable DMG builder is missing')
            spec = importlib.util.spec_from_file_location('safe_dmg', ROOT / 'packaging/make_dmg.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            original = root / 'dist/Studio Hub 0.6.150.dmg'
            original.parent.mkdir()
            original.write_bytes(b'first')
            first = module.preserve(original, root, 'dist')
            original.write_bytes(b'second')
            second = module.preserve(original, root, 'dist')
            self.assertNotEqual(first, second)
            self.assertEqual(first.read_bytes(), b'first')
            self.assertEqual(second.read_bytes(), b'second')

    def test_an_archive_cannot_be_moved_through_a_parent_alias(self):
        with tempfile.TemporaryDirectory(prefix='qwen-dmg-private-') as name:
            root = Path(name)
            self.assertTrue((ROOT / 'packaging/make_dmg.py').is_file(), 'tracked recoverable DMG builder is missing')
            spec = importlib.util.spec_from_file_location('safe_dmg', ROOT / 'packaging/make_dmg.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            archive = root / '_trash'
            archive.mkdir()
            saved = archive / 'saved.dmg'
            saved.write_bytes(b'owner archive')
            (root / 'alias').symlink_to(archive, target_is_directory=True)
            with self.assertRaises(ValueError):
                module.preserve(root / 'alias/saved.dmg', root, 'dist')
            self.assertEqual(saved.read_bytes(), b'owner archive')


if __name__ == '__main__':
    unittest.main()
