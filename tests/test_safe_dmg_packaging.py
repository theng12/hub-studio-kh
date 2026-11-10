"""The app release archive is preserved on replacement and failed staging."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import subprocess

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

    def test_build_preserves_other_versions_on_success_and_failure(self):
        for fail in (False, True):
            with self.subTest(fail=fail), tempfile.TemporaryDirectory(prefix='dmg-retention-') as name:
                root = Path(name)
                app = root / 'Studio Hub.app'
                (app / 'Contents').mkdir(parents=True)
                (app / 'Contents/Info.plist').write_text('fixture')
                spec = importlib.util.spec_from_file_location('safe_dmg_retention', ROOT / 'packaging/make_dmg.py')
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                output = root / 'dist/Hub Studio KH-0.6.228.dmg'
                output.parent.mkdir()
                previous = output.with_name('Hub Studio KH-0.6.227.dmg')
                previous.write_bytes(b'verified release')
                output.write_bytes(b'replaced candidate')

                def run(command, **kwargs):
                    if command[:3] == ['diskutil', 'image', 'create']:
                        output.write_bytes(b'new candidate')
                        if fail:
                            raise subprocess.CalledProcessError(1, command)
                    return subprocess.CompletedProcess(command, 0)

                with mock.patch.object(module, 'ROOT', root), \
                        mock.patch.object(module, 'validate_stage'), \
                        mock.patch.object(module.subprocess, 'run', side_effect=run):
                    if fail:
                        with self.assertRaises(subprocess.CalledProcessError):
                            module.build(app, output)
                    else:
                        module.build(app, output)
                        self.assertEqual(output.read_bytes(), b'new candidate')
                self.assertEqual(previous.read_bytes(), b'verified release')
                archived = [p.read_bytes() for p in (root / '_trash/dist').rglob('*.dmg')]
                self.assertIn(b'replaced candidate', archived)
                if fail:
                    self.assertFalse(output.exists())
                    self.assertIn(b'new candidate', archived)

    def test_changed_staged_content_is_rejected_before_packaging(self):
        with tempfile.TemporaryDirectory(prefix='dmg-integrity-private-') as name:
            app = Path(name) / 'Studio Hub.app'
            (app / 'Contents').mkdir(parents=True)
            (app / 'Contents/Info.plist').write_text('staged source')
            spec = importlib.util.spec_from_file_location('safe_dmg_digest', ROOT / 'packaging/make_dmg.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.assertTrue(callable(getattr(module, 'validate_stage', None)))
            stage_spec = importlib.util.spec_from_file_location('dmg_stage_digest', ROOT / 'packaging/stage_voice.py')
            stage = importlib.util.module_from_spec(stage_spec)
            stage_spec.loader.exec_module(stage)
            stage.sign_app(app)
            stage.write_file_manifest(app)
            app.with_name(app.name + '.tree-sha256').write_text(stage.tree_digest(app) + '\n')
            module.validate_stage(app)
            (app / 'Contents/Info.plist').write_text('changed payload')
            with self.assertRaisesRegex(ValueError, 'content'):
                module.validate_stage(app)

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
