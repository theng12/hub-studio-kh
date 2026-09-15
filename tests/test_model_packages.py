"""Portable model installation must never publish partial or changed bytes."""
import copy
from contextlib import contextmanager, redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import stat
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('model_packages', ROOT / 'components/model_packages.py')
packages = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(packages)


def manifest(files=None, package_id='test-model', revision='a' * 40):
    files = files or {'weights.safetensors': b'actual weights', 'tokenizer/config.json': b'{}'}
    return {
        'schema_version': 1, 'package_id': package_id, 'model_id': 'owner/model',
        'revision': revision, 'engine': 'voice-mlx', 'minimum_macos': '14.0',
        'requires_packages': [],
        'files': [{'path': name, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                   'urls': ['https://example.test/' + name]} for name, data in files.items()],
    }


class Response(io.BytesIO):
    status = 200
    headers = {}


class ModelPackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / 'source'
        self.library = self.root / 'library'
        self.source.mkdir()
        self.library.mkdir()
        self.files = {'weights.safetensors': b'actual weights', 'tokenizer/config.json': b'{}'}
        self.manifest = manifest(self.files)
        for name, data in self.files.items():
            p = self.source / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)

    def test_copy_import_and_offline_preflight(self):
        installed = packages.import_folder(self.manifest, self.source, self.library)
        self.assertEqual(installed, self.library / 'test-model' / ('a' * 40))
        self.assertEqual(packages.preflight(self.manifest, self.library)['state'], 'files_verified')
        self.assertEqual((installed / 'weights.safetensors').read_bytes(), b'actual weights')
        self.assertTrue((self.source / 'weights.safetensors').exists())

    def test_missing_companion_does_not_publish_install(self):
        (self.source / 'tokenizer/config.json').unlink()
        with self.assertRaisesRegex(packages.PackageError, 'tokenizer/config.json'):
            packages.import_folder(self.manifest, self.source, self.library)
        self.assertEqual(packages.preflight(self.manifest, self.library)['state'], 'not_installed')

    def test_invalid_or_traversing_manifest_rejected(self):
        for name in ['../escape', '/absolute', 'x/../../escape', 'x\\escape', '.studiohub/index', 'x//file']:
            with self.subTest(name=name):
                value = copy.deepcopy(self.manifest)
                value['files'][0]['path'] = name
                with self.assertRaises(packages.PackageError):
                    packages.validate_manifest(value)
        value = copy.deepcopy(self.manifest)
        value['files'].append(value['files'][0])
        with self.assertRaises(packages.PackageError):
            packages.validate_manifest(value)

    def test_in_library_registration_never_makes_second_copy(self):
        copied = self.library / 'my copied model'
        self.source.rename(copied)
        inode = (copied / 'weights.safetensors').stat().st_ino
        self.assertEqual(packages.import_folder(self.manifest, copied, self.library), copied)
        self.assertEqual((copied / 'weights.safetensors').stat().st_ino, inode)
        self.assertFalse((self.library / 'test-model').exists())

    def test_refresh_recognizes_copied_folder_without_manifest_file(self):
        copied = self.library / 'pasted'
        self.source.rename(copied)
        report = packages.scan_library([self.manifest], self.library)
        self.assertEqual(report[0]['state'], 'files_verified')
        self.assertEqual(packages.preflight(self.manifest, self.library)['state'], 'files_verified')

    def test_failed_new_revision_preserves_previous_install(self):
        packages.import_folder(self.manifest, self.source, self.library)
        newer = copy.deepcopy(self.manifest)
        newer['revision'] = 'b' * 40
        (self.source / 'weights.safetensors').write_bytes(b'bad')
        with self.assertRaises(packages.PackageError):
            packages.import_folder(newer, self.source, self.library)
        self.assertEqual(packages.preflight(self.manifest, self.library)['state'], 'files_verified')

    def test_preflight_rejects_changed_bytes_even_when_size_is_unchanged(self):
        installed = packages.import_folder(self.manifest, self.source, self.library)
        (installed / 'weights.safetensors').write_bytes(b'wrong! weights')
        report = packages.preflight(self.manifest, self.library)
        self.assertEqual(report['state'], 'invalid')
        self.assertIn('weights.safetensors', str(report))

    def test_preflight_rejects_unreadable_file_and_directory_symlink(self):
        installed = packages.import_folder(self.manifest, self.source, self.library)
        weights = installed / 'weights.safetensors'
        weights.chmod(0)
        self.assertEqual(packages.preflight(self.manifest, self.library)['state'], 'invalid')
        weights.chmod(0o600)
        packages.import_folder(self.manifest, installed, self.library)
        (installed / 'tokenizer').rename(installed / 'real-tokenizer')
        (installed / 'tokenizer').symlink_to(installed / 'real-tokenizer', target_is_directory=True)
        self.assertEqual(packages.preflight(self.manifest, self.library)['state'], 'invalid')

    def test_source_cache_links_are_materialized_and_relocatable(self):
        cache = self.root / 'models--owner--model'
        source = cache / 'snapshots' / ('a' * 40)
        source.mkdir(parents=True)
        blobs = cache / 'blobs'
        blobs.mkdir()
        for name, data in self.files.items():
            blob = blobs / hashlib.sha256(data).hexdigest()
            blob.write_bytes(data)
            p = source / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.symlink_to(os.path.relpath(blob, p.parent))
        installed = packages.import_folder(self.manifest, source, self.library)
        self.assertFalse((installed / 'weights.safetensors').is_symlink())
        cache.rename(self.root / 'cache-no-longer-present')
        self.assertEqual(packages.preflight(self.manifest, self.library)['state'], 'files_verified')

    def test_source_link_to_unrelated_file_and_state_symlink_are_rejected(self):
        (self.source / 'weights.safetensors').unlink()
        outside = self.root / 'unrelated'
        outside.write_bytes(self.files['weights.safetensors'])
        (self.source / 'weights.safetensors').symlink_to(outside)
        with self.assertRaises(packages.PackageError):
            packages.import_folder(self.manifest, self.source, self.library)
        (self.library / '.studiohub').symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(packages.PackageError):
            packages.preflight(self.manifest, self.library)

    def test_missing_library_is_not_silently_recreated(self):
        with self.assertRaises(packages.PackageError):
            packages.import_folder(self.manifest, self.source, self.root / 'missing-volume')
        self.assertFalse((self.root / 'missing-volume').exists())

    def test_lfs_pointer_is_rejected_even_if_manifest_hash_matches(self):
        data = b'version https://git-lfs.github.com/spec/v1\noid sha256:deadbeef\n'
        (self.source / 'weights.safetensors').write_bytes(data)
        value = manifest({'weights.safetensors': data})
        with self.assertRaisesRegex(packages.PackageError, 'LFS'):
            packages.import_folder(value, self.source, self.library)

    def test_download_and_folder_import_have_identical_identity(self):
        def download(request, **kwargs):
            name = request.full_url.split('example.test/')[1]
            return Response(self.files[name])
        with mock.patch.object(packages, '_open_download', side_effect=download):
            downloaded = packages.download_package(self.manifest, self.library)
        report = packages.preflight(self.manifest, self.library)
        self.assertEqual(report['state'], 'files_verified')
        self.assertEqual(report['manifest_sha256'], packages.manifest_digest(self.manifest))
        self.assertEqual((downloaded / 'weights.safetensors').read_bytes(), self.files['weights.safetensors'])

    def test_download_rejects_oversized_or_wrong_hash_bytes(self):
        with mock.patch.object(packages, '_open_download', return_value=Response(b'x' * 100)):
            with self.assertRaises(packages.PackageError):
                packages.download_package(self.manifest, self.library)
        self.assertEqual(packages.preflight(self.manifest, self.library)['state'], 'not_installed')

    def test_interrupted_download_resumes_and_does_not_repeat_completed_files(self):
        value = manifest({'weights.safetensors': b'abcdef'})
        class Interrupted(Response):
            def read(self, size=-1):
                if self.tell() == 0:
                    return super().read(3)
                raise OSError('Connection lost')
        with mock.patch.object(packages, '_open_download', return_value=Interrupted(b'abcdef')):
            with self.assertRaises(packages.PackageError):
                packages.download_package(value, self.library)
        self.assertEqual(packages.preflight(value, self.library)['state'], 'not_installed')
        def resume(request, **kwargs):
            self.assertEqual(request.get_header('Range'), 'bytes=3-')
            response = Response(b'def')
            response.status = 206
            response.headers = {'Content-Range': 'bytes 3-5/6'}
            return response
        with mock.patch.object(packages, '_open_download', side_effect=resume) as opened:
            packages.download_package(value, self.library)
            packages.download_package(value, self.library)
            self.assertEqual(opened.call_count, 1)

    def test_zero_byte_download_without_partial_file(self):
        value = manifest({'empty.bin': b''})
        with mock.patch.object(packages, '_open_download', return_value=Response(b'')):
            installed = packages.download_package(value, self.library)
        self.assertEqual((installed / 'empty.bin').read_bytes(), b'')
        self.assertEqual(packages.preflight(value, self.library)['state'], 'files_verified')

    def test_resume_restarts_when_server_returns_full_response(self):
        value = manifest({'weights.safetensors': b'abcdef'})
        staged = packages._stage(value, self.library)
        (staged / 'weights.safetensors.part').write_bytes(b'abc')
        def full_response(request, **kwargs):
            self.assertEqual(request.get_header('Range'), 'bytes=3-')
            return Response(b'abcdef')
        with mock.patch.object(packages, '_open_download', side_effect=full_response):
            packages.download_package(value, self.library)
        self.assertEqual(packages.preflight(value, self.library)['state'], 'files_verified')

    def test_invalid_partial_content_range_is_rejected_and_partial_removed(self):
        value = manifest({'weights.safetensors': b'abcdef'})
        staged = packages._stage(value, self.library)
        partial = staged / 'weights.safetensors.part'
        partial.write_bytes(b'abc')
        response = Response(b'def')
        response.status = 206
        response.headers = {'Content-Range': 'bytes 4-6/7'}
        with mock.patch.object(packages, '_open_download', return_value=response):
            with self.assertRaises(packages.PackageError):
                packages.download_package(value, self.library)
        self.assertFalse(partial.exists())

    def test_unsafe_url_and_redirect_are_rejected(self):
        for url in ['file:///etc/passwd', 'http://example.test/model', 'https://secret@example.test/model']:
            value = copy.deepcopy(self.manifest)
            value['files'][0]['urls'] = [url]
            with self.assertRaises(packages.PackageError):
                packages.validate_manifest(value)
        with self.assertRaises(packages.PackageError):
            packages._SecureRedirect().redirect_request(None, None, 302, '', {}, 'file:///etc/passwd')

    def test_safe_https_redirect_is_followed(self):
        request = packages.urllib.request.Request('https://example.test/model')
        redirected = packages._SecureRedirect().redirect_request(
            request, None, 302, '', {}, 'https://cdn.example.test/model')
        self.assertEqual(redirected.full_url, 'https://cdn.example.test/model')

    def test_source_alias_imports_raw_publisher_layout(self):
        value = copy.deepcopy(self.manifest)
        for entry in value['files']:
            entry['source_path'] = entry['path']
            entry['path'] = 'model/' + entry['path']
        target = packages.import_folder(value, self.source, self.library)
        self.assertEqual((target / 'model/weights.safetensors').read_bytes(), b'actual weights')

    def test_incompatible_manifest_does_not_reuse_verified_receipt(self):
        packages.import_folder(self.manifest, self.source, self.library)
        other = copy.deepcopy(self.manifest)
        other['files'][0]['sha256'] = 'f' * 64
        self.assertEqual(packages.preflight(other, self.library)['state'], 'not_installed')

    def test_corrupt_index_does_not_discard_existing_files(self):
        installed = packages.import_folder(self.manifest, self.source, self.library)
        (self.library / '.studiohub/installed.json').write_text('not json')
        with self.assertRaises(packages.PackageError):
            packages.preflight(self.manifest, self.library)
        self.assertEqual((installed / 'weights.safetensors').read_bytes(), b'actual weights')

    def test_companion_download_uses_explicit_catalog_and_is_cycle_safe(self):
        main = copy.deepcopy(self.manifest)
        main['requires_packages'] = ['whisper-turbo']
        companion = manifest(self.files, package_id='whisper-turbo')
        catalog = {'whisper-turbo': companion}
        def download(request, **kwargs):
            return Response(self.files[request.full_url.split('example.test/')[1]])
        with mock.patch.object(packages, '_open_download', side_effect=download):
            packages.download_package(main, self.library, catalog=catalog)
        self.assertEqual(packages.preflight(main, self.library, catalog=catalog)['state'], 'files_verified')
        companion['requires_packages'] = ['test-model']
        with self.assertRaises(packages.PackageError):
            packages.download_package(main, self.library, catalog={**catalog, 'test-model': main})

    def test_directory_swap_cannot_redirect_writes_outside_library(self):
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'config.json').write_bytes(b'preserve owner file')
        original = packages._directory
        swapped = False
        @contextmanager
        def raced(root, relative='', create=False):
            nonlocal swapped
            with original(root, relative, create=create) as descriptor:
                if relative.endswith('/tokenizer') and not swapped:
                    swapped = True
                    folder = root / relative
                    folder.rename(folder.with_name('original-tokenizer'))
                    folder.symlink_to(outside, target_is_directory=True)
                yield descriptor
        def download(request, **kwargs):
            return Response(self.files[request.full_url.split('example.test/')[1]])
        with mock.patch.object(packages, '_directory', side_effect=raced), \
                mock.patch.object(packages, '_open_download', side_effect=download):
            with self.assertRaises(packages.PackageError):
                packages.download_package(self.manifest, self.library)
        self.assertTrue(swapped)
        self.assertEqual((outside / 'config.json').read_bytes(), b'preserve owner file')
        self.assertEqual(sorted(p.name for p in outside.iterdir()), ['config.json'])

    def test_publication_syncs_directory_and_recovers_failed_index_replace(self):
        original_sync = os.fsync
        synced_directories = []
        def synced(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                synced_directories.append(fd)
            return original_sync(fd)
        with mock.patch.object(packages.os, 'fsync', side_effect=synced):
            packages.import_folder(self.manifest, self.source, self.library)
        self.assertTrue(synced_directories)
        newer = copy.deepcopy(self.manifest)
        newer['revision'] = 'b' * 40
        original_replace = os.replace
        def fail_index(src, dst, **kwargs):
            if Path(dst).name == 'installed.json':
                raise OSError('Simulated publication failure')
            return original_replace(src, dst, **kwargs)
        with mock.patch.object(packages.os, 'replace', side_effect=fail_index):
            with self.assertRaises(OSError):
                packages.import_folder(newer, self.source, self.library)
        self.assertEqual(packages.preflight(self.manifest, self.library)['state'], 'files_verified')
        self.assertEqual(packages.preflight(newer, self.library)['state'], 'not_installed')
        packages.import_folder(newer, self.source, self.library)
        self.assertEqual(packages.preflight(newer, self.library)['state'], 'files_verified')

    def test_failed_new_revision_download_preserves_previous_receipt(self):
        packages.import_folder(self.manifest, self.source, self.library)
        newer = copy.deepcopy(self.manifest)
        newer['revision'] = 'b' * 40
        with mock.patch.object(packages, '_open_download', return_value=Response(b'x' * 100)):
            with self.assertRaises(packages.PackageError):
                packages.download_package(newer, self.library)
        self.assertEqual(packages.preflight(self.manifest, self.library)['state'], 'files_verified')
        self.assertEqual(packages.preflight(newer, self.library)['state'], 'not_installed')

    def test_preflight_is_explicit_cached_receipt_check_not_full_rehash(self):
        packages.import_folder(self.manifest, self.source, self.library)
        with mock.patch.object(packages, '_check_stream', side_effect=AssertionError('Unexpected full rehash')):
            report = packages.preflight(self.manifest, self.library)
        self.assertEqual(report['verification'], 'cached_receipt')

    def test_cli_verify_full_rehashes_indexed_install(self):
        manifest_path = self.root / 'manifest.json'
        manifest_path.write_text(json.dumps(self.manifest))
        packages.import_folder(self.manifest, self.source, self.library)
        with mock.patch.object(packages, '_check_stream', wraps=packages._check_stream) as checked:
            with mock.patch.object(sys, 'argv', [
                    'model_packages', 'verify', '--manifest', str(manifest_path),
                    '--library', str(self.library)]):
                with redirect_stdout(io.StringIO()) as output:
                    packages.main()
        self.assertEqual(checked.call_count, len(self.manifest['files']))
        self.assertEqual(json.loads(output.getvalue())['verification'], 'full_rehash')

    def test_full_verify_refreshes_receipt_after_valid_file_touch(self):
        installed = packages.import_folder(self.manifest, self.source, self.library)
        (installed / 'weights.safetensors').write_bytes(self.files['weights.safetensors'])
        self.assertEqual(packages.preflight(self.manifest, self.library)['state'], 'invalid')
        report = packages.verify_installed(self.manifest, self.library)
        self.assertEqual(report['state'], 'files_verified')
        self.assertEqual(packages.preflight(self.manifest, self.library)['state'], 'files_verified')

    def test_full_verify_rejects_installed_ancestor_symlink(self):
        packages.import_folder(self.manifest, self.source, self.library)
        package_parent = self.library / 'test-model'
        package_parent.rename(self.library / 'real-test-model')
        package_parent.symlink_to(self.library / 'real-test-model', target_is_directory=True)
        report = packages.verify_installed(self.manifest, self.library)
        self.assertEqual(report['state'], 'invalid')

    def test_changed_mirror_does_not_change_expected_content_identity(self):
        other = copy.deepcopy(self.manifest)
        other['files'][0]['urls'] = ['https://github.com/owner/models/releases/download/v1/weights']
        self.assertEqual(packages.manifest_digest(other), packages.manifest_digest(self.manifest))

    def test_companion_package_required_and_checked_again_before_use(self):
        packages.import_folder(self.manifest, self.source, self.library)
        dependent = copy.deepcopy(self.manifest)
        dependent['requires_packages'] = ['whisper-turbo']
        packages.import_folder(dependent, self.source, self.library)
        self.assertEqual(packages.preflight(dependent, self.library)['state'], 'dependency_missing')
        companion = manifest(self.files, package_id='whisper-turbo')
        packages.import_folder(companion, self.source, self.library)
        catalog = {'whisper-turbo': companion}
        self.assertEqual(packages.preflight(dependent, self.library, catalog=catalog)['state'], 'files_verified')


if __name__ == '__main__':
    unittest.main()
