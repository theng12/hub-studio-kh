"""Portable model installation must never publish partial or changed bytes."""
import copy
from contextlib import contextmanager, redirect_stdout
import hashlib
import importlib.util
import io
import json
import shutil
import os
import subprocess
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


def companion_manifest(package_id, files, revision='a' * 40):
    """A companion shaped the way every shipped manifest is: files under their layout.

    `source.layout` names the publisher repository, its immutable revision and the destination
    the tree was installed to; `source_path` names the file inside that repository. Both are what
    the companion mirror reads to place a companion's bytes where its engine looks for them, so a
    fixture that omits them is a fixture for a manifest that cannot be placed at all.
    """
    value = manifest(files, package_id=package_id, revision=revision)
    for entry in value['files']:
        entry['source_path'] = entry['path']
        entry['path'] = 'model/' + entry['path']
    value['source'] = {
        'provider': 'huggingface', 'captured_at': '2026-09-25',
        'layout': {'model': {'destination': 'model', 'repo': 'owner/model', 'revision': revision}}}
    return value


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
        """A same-size rewrite is caught, and the cache has to be older than the rewrite.

        The stamp records whole seconds on purpose - nothing finer survives the copy the kit
        ships - so a rewrite in the very same second as the verification is indistinguishable
        from it. Moving the recorded time back models the real case: a package verified, then a
        file replaced later.
        """
        installed = packages.import_folder(self.manifest, self.source, self.library)
        receipt = self.library / '.studiohub' / 'installed.json'
        index = json.loads(receipt.read_text())
        recorded = index['test-model']['files']['weights.safetensors']
        recorded['mtime_s'] -= 5
        receipt.write_text(json.dumps(index, sort_keys=True))
        (installed / 'weights.safetensors').write_bytes(b'wrong! weights')
        report = packages.preflight(self.manifest, self.library)
        self.assertEqual(report['state'], 'invalid')
        self.assertIn('weights.safetensors', str(report))

    def test_a_verified_library_stays_verified_after_being_copied(self):
        """A copied library must not read as damaged.

        The receipt used to record `st_ino` and `st_dev`, which describe one filesystem. The kit
        ships a verified library onto an SSD and from there onto each Mac, so every package in a
        copied library answered "File changed; verify again" on the machine that was not the
        build machine - which is exactly what the owner hit, and it made a correct 29 GB copy
        look broken.
        """
        installed = packages.import_folder(self.manifest, self.source, self.library)
        self.assertEqual(packages.preflight(self.manifest, self.library)['state'], 'files_verified')

        copy_root = self.root / 'copied-library'
        shutil.copytree(self.library, copy_root, symlinks=True)
        copy_root.chmod(0o700)
        (copy_root / '.studiohub').chmod(0o700)
        self.assertEqual(packages.preflight(self.manifest, copy_root)['state'], 'files_verified',
                         'a copy of a verified library must verify without a rehash')

        # And the file's real content is still what decides: a rewrite is still caught.
        (copy_root / installed.parent.name / installed.name / 'weights.safetensors').write_bytes(b'x' * 13)
        report = packages.preflight(self.manifest, copy_root)
        self.assertEqual(report['state'], 'invalid',
                         'a genuinely changed file must still be refused')

    def test_a_receipt_from_an_older_build_repairs_itself(self):
        """A stale cache must repair, not accuse.

        Changing which fields the receipt records - twice, in one session - turned every installed
        model into `invalid` on the owner's machines: the comparison raised KeyError on the missing
        field and that was reported as a damaged package. A receipt written in another shape is a
        cache miss, so it is rehashed once and rewritten.
        """
        installed = packages.import_folder(self.manifest, self.source, self.library)
        receipt = self.library / '.studiohub' / 'installed.json'
        index = json.loads(receipt.read_text())
        # An older shape: the fields this build writes, minus one, plus one it no longer does.
        for saved in index['test-model']['files'].values():
            saved.pop('ctime_s', None)
            saved['st_mtime_ns'] = 123456789
        receipt.write_text(json.dumps(index, sort_keys=True))

        report = packages.preflight(self.manifest, self.library)
        self.assertEqual(report['state'], 'files_verified', report)
        # And the receipt was rewritten in the current shape, so the next check is cheap again.
        rewritten = json.loads(receipt.read_text())['test-model']['files']['weights.safetensors']
        self.assertEqual({k for k in rewritten if k != 'relative'}, set(packages._STAMP_FIELDS),
                         'the repaired receipt must be written in this build\'s shape')
        self.assertEqual(packages.preflight(self.manifest, self.library)['state'], 'files_verified')

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
        companion = companion_manifest('whisper-turbo', self.files)
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
        # The same bytes written again: the cache notices the later write and refuses, and the
        # explicit Verify is what clears it - which is the point of having both.
        receipt = self.library / '.studiohub' / 'installed.json'
        index = json.loads(receipt.read_text())
        recorded = index['test-model']['files']['weights.safetensors']
        recorded['mtime_s'] -= 5
        receipt.write_text(json.dumps(index, sort_keys=True))
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
        companion = companion_manifest('whisper-turbo', self.files)
        packages.import_folder(companion, self.source, self.library, catalog={'whisper-turbo': companion})
        catalog = {'whisper-turbo': companion}
        self.assertEqual(packages.preflight(dependent, self.library, catalog=catalog)['state'], 'files_verified')


class VerifyInstalledTests(unittest.TestCase):
    """Verify Files must verify the files that are there, not only a receipt that already agrees.

    Measured on the shipped 0.6.62 bundle: `mlx-community/Qwen3.5-9B-4bit` (4-bit) had all
    5,977,071,067 bytes at the conventional ``<library>/<package_id>/<revision>/model`` path and
    **no receipt**, and `verify_installed` answered `not_installed` in 0.0 s - so the instruction
    "press Verify Files" could not do anything. The same early return left the
    `chatterbox-{4,8}bit` manifests unrepairable after their digest moved under their receipts.
    Each case below is one of the three answers that button has to give.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / 'source'
        self.library = self.root / 'library'
        self.source.mkdir()
        self.library.mkdir()
        self.files = {'weights.safetensors': b'actual weights',
                      'tokenizer/config.json': b'{"vocab": 1}'}
        self.manifest = manifest(self.files)
        for name, data in self.files.items():
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        # The layout the kits, the copy script and the owner's chat model use: bytes at the
        # conventional folder, with no receipt anywhere.
        self.folder = self.library / 'test-model' / ('a' * 40)
        for name, data in self.files.items():
            path = self.folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

    def receipt(self):
        return packages._index(self.library).get('test-model')

    def test_bytes_at_the_conventional_path_are_hashed_and_recorded(self):
        self.assertEqual(packages.preflight(self.manifest, self.library)['state'], 'not_installed')
        with mock.patch.object(packages, '_check_stream', wraps=packages._check_stream) as checked:
            report = packages.verify_installed(self.manifest, self.library)
        self.assertEqual(report['state'], 'files_verified', report)
        self.assertEqual(report['verification'], 'full_rehash')
        self.assertEqual(report['folder'], str(self.folder))
        self.assertEqual(checked.call_count, len(self.manifest['files']),
                         'every file must be hashed, not merely stat-ed')
        record = self.receipt()
        self.assertEqual(record['manifest_sha256'], packages.manifest_digest(self.manifest))
        self.assertEqual(record['folder'], 'test-model/' + 'a' * 40)
        self.assertEqual(packages.preflight(self.manifest, self.library)['state'], 'files_verified')

    def test_bytes_that_do_not_match_are_refused_by_name_and_nothing_is_recorded(self):
        # A same-size rewrite and a truncation: the two failures a copied library actually has,
        # each named with what was measured rather than "incomplete or invalid".
        for written, expected in ((b'wrong weights!', 'hashes to'),
                                  (b'short', 'is 5 bytes, not the 14 bytes')):
            with self.subTest(written=written):
                (self.folder / 'weights.safetensors').write_bytes(written)
                report = packages.verify_installed(self.manifest, self.library)
                self.assertEqual(report['state'], 'invalid', report)
                self.assertIn('test-model', report['reason'])
                self.assertIn('weights.safetensors', report['reason'])
                self.assertIn('model-manifests/test-model.json', report['reason'])
                self.assertIn(expected, report['reason'])
                self.assertIsNone(self.receipt(), 'a failed verify must not write a receipt')
                self.assertEqual((self.folder / 'weights.safetensors').read_bytes(), written,
                                 'verification never rewrites a model file')

    def test_a_receipt_the_manifest_moved_under_is_rewritten(self):
        packages.import_folder(self.manifest, self.source, self.library)
        changed = copy.deepcopy(self.manifest)
        changed['minimum_macos'] = '15.0'          # A catalog change that moves the digest.
        self.assertNotEqual(packages.manifest_digest(changed),
                            packages.manifest_digest(self.manifest))
        self.assertEqual(packages.preflight(changed, self.library)['state'], 'not_installed')
        report = packages.verify_installed(changed, self.library)
        self.assertEqual(report['state'], 'files_verified', report)
        self.assertEqual(self.receipt()['manifest_sha256'], packages.manifest_digest(changed))
        self.assertEqual(packages.preflight(changed, self.library)['state'], 'files_verified')
        self.assertEqual((self.folder / 'weights.safetensors').read_bytes(), b'actual weights')

    def test_a_receipt_the_manifest_moved_under_still_refuses_changed_bytes(self):
        packages.import_folder(self.manifest, self.source, self.library)
        changed = copy.deepcopy(self.manifest)
        changed['files'][0]['sha256'] = 'f' * 64   # The manifest now describes other bytes.
        report = packages.verify_installed(changed, self.library)
        self.assertEqual(report['state'], 'invalid', report)
        self.assertIn('weights.safetensors', report['reason'])
        self.assertIn('model-manifests/test-model.json', report['reason'])
        # The receipt that is left is the one that was true before; nothing was rewritten to
        # describe bytes that are not there, so the package cannot read as verified under it.
        self.assertEqual(self.receipt()['manifest_sha256'],
                         packages.manifest_digest(self.manifest))
        self.assertEqual(packages.preflight(changed, self.library)['state'], 'not_installed')

    def test_nothing_on_disk_stays_not_installed_with_the_paths_it_looked_for(self):
        shutil.rmtree(self.folder)
        report = packages.verify_installed(self.manifest, self.library)
        self.assertEqual(report['state'], 'not_installed', report)
        self.assertIn('test-model', report['reason'])
        self.assertIn('<library>/test-model/' + 'a' * 40, report['reason'])
        self.assertIn('model-manifests/test-model.json', report['reason'])

    def test_a_receipt_whose_folder_is_gone_is_not_installed_rather_than_damaged(self):
        packages.import_folder(self.manifest, self.source, self.library)
        shutil.rmtree(self.library / 'test-model')
        report = packages.verify_installed(self.manifest, self.library)
        self.assertEqual(report['state'], 'not_installed', report)
        self.assertIn('test-model', report['reason'])

    def test_a_receipt_for_another_revision_does_not_hide_the_revisions_own_folder(self):
        packages.import_folder(self.manifest, self.source, self.library)
        newer = copy.deepcopy(self.manifest)
        newer['revision'] = 'b' * 40
        destination = self.library / 'test-model' / ('b' * 40)
        for name, data in self.files.items():
            path = destination / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.assertEqual(packages.preflight(newer, self.library)['state'], 'not_installed')
        report = packages.verify_installed(newer, self.library)
        self.assertEqual(report['state'], 'files_verified', report)
        self.assertEqual(report['folder'], str(destination),
                         'the bytes for the manifest revision must win over an older receipt')
        self.assertEqual(self.receipt()['revision'], 'b' * 40)

    def test_a_folder_named_by_its_receipt_is_still_found_when_its_bytes_match_a_new_manifest(self):
        """`import_folder` keeps the name of a folder already inside the library.

        The receipt is then the only record of where the package lives, so a manifest change must
        still be repaired from that folder rather than answered with `not_installed`.
        """
        copied = self.library / 'my copied model'
        self.folder.rename(copied)
        packages.import_folder(self.manifest, copied, self.library)
        self.assertEqual(self.receipt()['folder'], 'my copied model')
        changed = copy.deepcopy(self.manifest)
        changed['minimum_macos'] = '15.0'
        report = packages.verify_installed(changed, self.library)
        self.assertEqual(report['state'], 'files_verified', report)
        self.assertEqual(report['folder'], str(copied))


class ShippedManifestMigrationTests(unittest.TestCase):
    """The `chatterbox-{4,8}bit` repair, driven by the shipped manifests themselves.

    Their receipts were written while their companion list was spelled `dependencies`, a field
    `manifest_digest` did not read; the current manifests declare it in `requires_packages`, so
    the digest moved under a receipt nothing rewrote - and Verify Files answered `not_installed`
    without hashing a byte. The installed copies are on the kit SSDs (`/Volumes/KH-STUDIO-*`),
    not on this machine, so the bytes here are fixtures. Everything that makes the receipt stale
    is the shipped value: package id, revision, engine, the companion relationship, every file
    path and the publisher layout - and the digest premise is asserted against the real manifest.
    """

    SHIPPED = ('chatterbox-4bit', 'chatterbox-8bit')
    COMPANION = 's3tokenizerv2'

    def real(self, package_id):
        return json.loads((ROOT / 'model-manifests' / f'{package_id}.json').read_text())

    def fixture(self, real, prefix):
        """The shipped manifest with its file list replaced by bytes this machine can hold."""
        trimmed = copy.deepcopy(real)
        trimmed['files'] = []
        for entry in real['files']:
            data = (prefix + entry['path']).encode()
            replacement = {k: entry[k] for k in ('path', 'source_path') if k in entry}
            trimmed['files'].append({
                **replacement, 'size': len(data),
                'sha256': hashlib.sha256(data).hexdigest(),
                'urls': ['https://example.test/' + entry['path']]})
        return trimmed

    def install(self, manifest, library, prefix):
        """Lay a fixture package on disk and register it, as the kit copy would have."""
        source = library.parent / ('source-' + manifest['package_id'])
        files = {}
        for entry in manifest['files']:
            data = (prefix + entry['path']).encode()
            files[entry['path']] = data
            path = source / entry['path']
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        packages.import_folder(manifest, source, library)
        return files

    def test_the_shipped_receipts_were_invalidated_by_the_companion_field(self):
        for package_id in self.SHIPPED:
            with self.subTest(package=package_id):
                real = self.real(package_id)
                before = copy.deepcopy(real)
                # What the digest saw while the companions were in the legacy field: not at all.
                before['requires_packages'] = []
                self.assertTrue(packages.required_packages(real))
                self.assertNotEqual(
                    packages.manifest_digest(real), packages.manifest_digest(before),
                    f'{package_id}: the companion list is not part of the manifest digest, so the '
                    f'receipt could not have gone stale when the field moved')

    def test_a_stale_receipt_is_repaired_and_the_companion_reaches_the_engine_cache(self):
        for package_id in self.SHIPPED:
            with self.subTest(package=package_id):
                with tempfile.TemporaryDirectory() as tmp:
                    library = Path(tmp).resolve() / 'library'
                    library.mkdir()
                    real = self.real(package_id)
                    manifest = self.fixture(real, '')
                    companion_real = self.real(self.COMPANION)
                    companion = self.fixture(companion_real, 'tokenizer ')
                    companion_files = self.install(companion, library, 'tokenizer ')
                    # The receipt the owner's library holds for this package: the digest the
                    # legacy-spelled manifest produced, which the current manifest no longer
                    # matches.
                    legacy = copy.deepcopy(manifest)
                    legacy['requires_packages'] = []
                    self.install(legacy, library, '')
                    catalog = {package_id: manifest, self.COMPANION: companion}
                    self.assertEqual(
                        packages.preflight(manifest, library, catalog=catalog)['state'],
                        'not_installed',
                        'the dead end as measured: bytes on disk, receipt the manifest moved under')
                    report = packages.verify_installed(manifest, library, catalog=catalog)
                    self.assertEqual(report['state'], 'files_verified', report)
                    self.assertEqual(report['verification'], 'full_rehash')
                    record = packages._index(library)[package_id]
                    self.assertEqual(record['manifest_sha256'],
                                     packages.manifest_digest(manifest))
                    self.assertEqual(packages.preflight(manifest, library,
                                                        catalog=catalog)['state'],
                                     'files_verified')
                    self.assertEqual(report['companion_cache']['state'], 'ready', report)
                    plan, problems = packages._companion_plan(library, manifest, catalog)
                    self.assertEqual(problems, [])
                    self.assertTrue(plan, 'the companion has no files to place')
                    for target, item in plan.items():
                        placed = library / target
                        self.assertTrue(placed.is_file(), target)
                        self.assertEqual(placed.read_bytes(),
                                         companion_files[item['entry']['path']])
                    repo = companion_real['source']['layout']['model']['repo']
                    revision = companion_real['source']['layout']['model']['revision']
                    self.assertIn(f'hub/models--{repo.replace("/", "--")}/snapshots/{revision}',
                                  ' '.join(plan))


class CompanionCacheTests(unittest.TestCase):
    """A companion must reach the path the engine loads it from, not only the library.

    The controlled experiment: the companion is installed and verified, the library is complete,
    and the engine's own lookup still finds nothing - because the engine asks the Hugging Face
    cache, which no install path used to write. Every assertion below is about that path.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / 'source'
        self.library = self.root / 'library'
        self.source.mkdir()
        self.library.mkdir()
        self.files = {'weights.safetensors': b'companion weights',
                      'tokenizer/config.json': b'{"n_bins": 4000}'}
        for name, data in self.files.items():
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.companion = companion_manifest('whisper-turbo', self.files)
        self.host = copy.deepcopy(manifest(self.files))
        self.host['requires_packages'] = ['whisper-turbo']
        self.catalog = {'whisper-turbo': self.companion, self.host['package_id']: self.host}
        packages.import_folder(self.companion, self.source, self.library,
                               catalog=self.catalog)

    def targets(self):
        plan, problems = packages._companion_plan(self.library, self.host, self.catalog)
        self.assertEqual(problems, [])
        return plan

    def test_an_installed_companion_is_mirrored_where_the_engine_reads_it(self):
        report = packages.materialize_companions(self.host, self.library, catalog=self.catalog)
        self.assertEqual(report['state'], 'ready', report.get('reason'))
        installed = self.library / 'whisper-turbo' / ('a' * 40) / 'model'
        for target, item in sorted(self.targets().items()):
            path = self.library / target
            self.assertTrue(path.is_file(), target)
            self.assertEqual(path.read_bytes(), (self.library / item['source']).read_bytes())
            # A hard link, not a copy: same inode, so the mirror costs no bytes.
            self.assertEqual(path.stat().st_ino, (self.library / item['source']).stat().st_ino)
            self.assertEqual(path.stat().st_dev, (self.library / item['source']).stat().st_dev)
        # An engine that names no revision resolves through refs/<default>; one that names the
        # commit reads snapshots/<revision> directly. Both must resolve with no network.
        ref = (self.library / 'hub' / 'models--owner--model' / 'refs' / 'main')
        self.assertEqual(ref.read_text().strip(), 'a' * 40)
        self.assertTrue(installed.is_dir())

    def test_a_second_run_writes_nothing_and_leaves_the_mirror_alone(self):
        packages.materialize_companions(self.host, self.library, catalog=self.catalog)
        before = {target: (self.library / target).stat().st_ino for target in self.targets()}
        stamps = {target: (self.library / target).stat().st_mtime_ns for target in self.targets()}
        report = packages.materialize_companions(self.host, self.library, catalog=self.catalog)
        self.assertEqual(report['state'], 'ready', report.get('reason'))
        self.assertEqual(report['written'], {'linked': 0, 'copied': 0, 'present': 2})
        after = {target: (self.library / target).stat().st_ino for target in self.targets()}
        self.assertEqual(before, after, 'an idempotent run must not replace what is already right')
        self.assertEqual(stamps, {target: (self.library / target).stat().st_mtime_ns
                                  for target in self.targets()})

    def test_the_engines_own_lookup_finds_the_companion_with_the_network_off(self):
        """The exact call the engine makes, in the environment the worker is launched with.

        `hf_hub_download` is what mlx-audio's `download_if_necessary` calls, and `HF_HUB_CACHE` /
        `HF_HUB_OFFLINE` are what `packaging/voice_worker.py` sets. A subprocess is used because
        huggingface_hub reads those constants at import time, which is also why the check is a
        fresh interpreter rather than a patched one.
        """
        try:
            import huggingface_hub  # noqa: F401
        except ImportError:  # pragma: no cover - the packaged runtimes always carry it
            self.skipTest('huggingface_hub is unavailable in this interpreter')
        packages.materialize_companions(self.host, self.library, catalog=self.catalog)
        script = (
            'import hashlib, json, sys\n'
            'from huggingface_hub import hf_hub_download\n'
            'out = {}\n'
            'for label, revision in (("pinned", "a" * 40), ("unpinned", None)):\n'
            '    path = hf_hub_download(repo_id="owner/model",\n'
            '                           filename=sys.argv[1], revision=revision)\n'
            '    out[label] = [path, hashlib.sha256(open(path, "rb").read()).hexdigest()]\n'
            'print(json.dumps(out))\n')
        environment = dict(os.environ)
        environment.update({'HF_HOME': str(self.library), 'HF_HUB_CACHE': str(self.library / 'hub'),
                            'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
                            'PYTHONPATH': os.pathsep.join(sys.path)})
        for filename, data in sorted(self.files.items()):
            completed = subprocess.run([sys.executable, '-c', script, filename],
                                       capture_output=True, text=True, env=environment)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            found = json.loads(completed.stdout)
            for label, (path, digest) in found.items():
                self.assertEqual(digest, hashlib.sha256(data).hexdigest(), (filename, label))
                self.assertTrue(Path(path).is_file(), (filename, label, path))

    def test_a_receiptless_host_still_ends_with_its_companion_in_the_engine_cache(self):
        """The newly registered case has to run the companion half too.

        A package whose bytes are on disk with no receipt verifies and registers; an explicit
        verify is also where the operator asks for the whole thing to be true, so the companion
        must still be placed where the engine reads it.
        """
        folder = self.library / self.host['package_id'] / ('a' * 40)
        for name, data in self.files.items():
            path = folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        # The companion was verified against a library whose engine cache was never written:
        # there is no `hub` folder at all.
        self.assertFalse((self.library / 'hub').exists())
        report = packages.verify_installed(self.host, self.library, catalog=self.catalog)
        self.assertEqual(report['state'], 'files_verified', report)
        self.assertEqual(report['companion_cache']['state'], 'ready', report)
        for target in self.targets():
            self.assertTrue((self.library / target).is_file(), target)

    def test_an_unwritable_library_degrades_by_name_instead_of_reporting_success(self):
        # Installed first without the catalog, which is the shape a receipt copied from a Mac
        # that never wrote the engine cache has: verified files, nothing at the engine's path.
        packages.import_folder(self.host, self.source, self.library)
        hub = self.library / 'hub'
        hub.mkdir(mode=0o500)
        self.addCleanup(lambda: hub.chmod(0o700))
        report = packages.verify_installed(self.host, self.library, catalog=self.catalog)
        self.assertEqual(report['state'], packages.COMPANION_CACHE_STATE, report)
        self.assertIn('whisper-turbo', report['reason'])
        self.assertIn('model/weights.safetensors', report['reason'])
        self.assertEqual(report['companion_cache']['state'], 'degraded')
        # The package's own bytes are installed and verified; what is not true is the engine's
        # access to them, and that is what the state and the reason say.
        self.assertEqual(report['verification'], 'full_rehash')

    def test_a_companion_whose_manifest_does_not_say_where_files_publish_is_named(self):
        companion = copy.deepcopy(self.companion)
        companion.pop('source')
        report = packages.materialize_companions(
            self.host, self.library, catalog={'whisper-turbo': companion})
        self.assertEqual(report['state'], 'degraded')
        self.assertIn('source.layout', report['reason'])
        self.assertIn('whisper-turbo', report['reason'])


class SubtitleManifestContractTests(unittest.TestCase):
    """The `subtitle` block is validated centrally, so no consumer has to guess."""

    def subtitle_manifest(self, **overrides):
        value = manifest()
        value['engine'] = 'subtitle-mlx'
        value['subtitle'] = {'family': 'whisper', 'languages': ['multilingual'],
                             'segment_timestamps': True, 'word_timestamps': True,
                             'long_form': True}
        value.update(overrides)
        return value

    def test_a_subtitle_manifest_without_the_block_is_refused_by_name_and_field(self):
        value = self.subtitle_manifest()
        value.pop('subtitle')
        with self.assertRaises(packages.PackageError) as caught:
            packages.validate_manifest(value)
        self.assertIn('test-model', str(caught.exception))
        self.assertIn('subtitle', str(caught.exception))

    def test_a_malformed_subtitle_block_is_refused_by_name_and_field(self):
        for field, broken in (('family', ''), ('languages', 'multilingual'),
                              ('segment_timestamps', 'yes'), ('word_timestamps', None),
                              ('long_form', 1)):
            with self.subTest(field=field):
                value = self.subtitle_manifest()
                value['subtitle'] = {**value['subtitle'], field: broken}
                with self.assertRaises(packages.PackageError) as caught:
                    packages.validate_manifest(value)
                self.assertIn(field, str(caught.exception))
        value = self.subtitle_manifest()
        value.pop('subtitle')
        value['subtitle'] = {'family': 'whisper', 'languages': ['multilingual'],
                             'segment_timestamps': True, 'word_timestamps': True}
        with self.assertRaises(packages.PackageError) as caught:
            packages.validate_manifest(value)
        self.assertIn('long_form', str(caught.exception))

    def test_a_runtime_measurement_must_carry_its_evidence(self):
        value = self.subtitle_manifest()
        value['subtitle']['runtime_seconds'] = {'measured': [{'seconds': 9.0, 'scene': 'x'}]}
        with self.assertRaises(packages.PackageError) as caught:
            packages.validate_manifest(value)
        self.assertIn('evidence', str(caught.exception))
        # `measured` is a list of rows, each carrying seconds and what produced them.
        for broken in ({'seconds': 9.0, 'scene': 'x'},           # not a list
                       [],                                        # nothing measured
                       [{'scene': 'x'}],                          # no seconds
                       [{'seconds': 0}],                          # not a positive cost
                       [{'seconds': 9.0}]):                       # names no configuration
            with self.subTest(broken=broken):
                value = self.subtitle_manifest()
                value['subtitle']['runtime_seconds'] = {
                    'evidence': 'measured', 'measured': broken,
                    'measured_on': 'a host'}
                with self.assertRaises(packages.PackageError) as caught:
                    packages.validate_manifest(value)
                self.assertIn('measured', str(caught.exception))
        value['subtitle']['runtime_seconds'] = {
            'evidence': 'measured',
            'measured': [{'seconds': 9.66, 'duration_seconds': 5.166, 'word_timestamps': False}],
            'measured_on': '32 GiB M2 Max, MLX fp16',
            'source': 'qualification/evidence/example.json'}
        packages.validate_manifest(value)

    def test_another_engine_is_not_forced_to_carry_the_block(self):
        value = manifest()
        value.pop('subtitle', None)
        packages.validate_manifest(value)
        self.assertNotIn('subtitle', value)

    def test_every_shipped_subtitle_manifest_declares_its_capabilities(self):
        manifests = [json.loads(path.read_text())
                     for path in sorted((ROOT / 'model-manifests').glob('*.json'))]
        subtitle = [value for value in manifests if value.get('engine') == 'subtitle-mlx']
        self.assertTrue(subtitle, 'the shipped catalog declares subtitle packages')
        for value in subtitle:
            with self.subTest(package=value.get('package_id')):
                packages.validate_manifest(value)
                self.assertTrue({'family', 'languages', 'long_form', 'segment_timestamps',
                                 'word_timestamps'} <= set(value['subtitle']))
        self.assertLessEqual(
            sum(1 for value in subtitle if value.get('default') is True), 1,
            'at most one shipped subtitle manifest may declare itself the default')

    def test_every_shipped_companion_can_be_placed_where_its_engine_reads_it(self):
        """The invariant that catches the next companion package.

        A companion whose manifest does not say which publisher repository each file came from -
        destination, repo and revision in `source.layout` - cannot be mirrored, so its engine
        would fetch it over the network at generation time. This walks the real catalog: add a
        companion package without that data and this fails, naming the file to fix.
        """
        manifests = {value['package_id']: value
                     for value in (json.loads(path.read_text())
                                   for path in sorted((ROOT / 'model-manifests').glob('*.json')))}
        for package_id, value in sorted(manifests.items()):
            for dependency in packages.required_packages(value):
                with self.subTest(package=package_id, companion=dependency):
                    companion = manifests.get(dependency)
                    self.assertIsNotNone(companion, f'{dependency} has no shipped manifest')
                    for entry in companion['files']:
                        repo, revision, publish_path = packages.publisher_location(companion, entry)
                        self.assertRegex(repo, r'^[^/]+/.+$')
                        self.assertRegex(revision, r'^[0-9a-f]{40}$')
                        self.assertFalse(publish_path.startswith('/'))

    def test_the_unknown_engine_refusal_names_the_engine_and_both_files(self):
        value = manifest()
        value['engine'] = 'chat-mlx-2'
        with self.assertRaises(packages.PackageError) as caught:
            packages.validate_manifest(value)
        message = str(caught.exception)
        self.assertIn('chat-mlx-2', message)
        self.assertIn('_ENGINE_ROUTE', message)
        self.assertIn('components/hub/standalone.py', message)
        self.assertIn('components/model_packages.py', message)

    def test_the_engine_allowlist_and_the_hubs_worker_map_are_one_vocabulary(self):
        """Two files carry the engine vocabulary; this is what stops them drifting.

        When they last disagreed, `chat-mlx` reached `/api/hub/model-packages` with no worker and
        the route returned 500 - which the native refresh read as "Hub unreachable" and answered
        by blanking every model picker on every Mac.
        """
        from components.hub import standalone
        self.assertEqual(set(packages.ENGINES), set(standalone._ENGINE_ROUTE))


if __name__ == '__main__':
    unittest.main()
