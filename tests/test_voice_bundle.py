"""Exercise relocation, external state and the worker's launch boundary."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


class BundleTests(unittest.TestCase):
    def test_worker_validates_missing_selected_model_library_without_creating_it(self):
        with tempfile.TemporaryDirectory() as temp:
            missing = Path(temp) / 'unmounted SSD' / 'models'
            command = [sys.executable, '-I', '-B', str(ROOT / 'packaging/voice_worker.py'),
                       '--models-dir', str(missing), '--check']
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn('Selected model library is unavailable', result.stderr)
            self.assertFalse(missing.exists())

    def test_worker_check_uses_bundle_paths_after_relocation(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            resources = base / 'Moved app with spaces' / 'Contents' / 'Resources'
            resources.mkdir(parents=True)
            shutil.copyfile(ROOT / 'packaging/voice_worker.py', resources / 'voice_worker.py')
            (resources / 'voice' / 'app').mkdir(parents=True)
            (resources / 'runtimes' / 'voice' / 'bin').mkdir(parents=True)
            (resources / 'release-manifest.json').write_text(json.dumps({
                'schema': 'studiohub.standalone-release', 'schema_version': 1,
                'version': '0.1.0', 'implementation_commit': 'a' * 40,
                'components': {'voice': {'upstream_commit': 'b' * 40,
                    'upstream_version': '2.7.2', 'source_sha256': 'c' * 64}},
                'runtime_sha256': 'd' * 64,
            }))
            models = base / 'Model Library'
            models.mkdir()
            state = base / 'Node State'
            result = subprocess.run([sys.executable, '-I', '-B', str(resources / 'voice_worker.py'),
                '--models-dir', str(models), '--data-dir', str(state), '--offline', '--check'],
                capture_output=True, text=True, env={'PATH': '/usr/bin:/bin',
                    'HF_HOME': '/must/not/inherit', 'PYTHONPATH': '/must/not/inherit',
                    'KH_STUDIO_TOKEN': 'must-not-inherit'})
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(result.stdout)
            self.assertEqual(data['models_dir'], str(models.resolve()))
            self.assertEqual(data['data_dir'], str(state.resolve()))
            self.assertEqual(data['source_dir'], str((resources / 'voice' / 'app').resolve()))
            self.assertEqual(data['managed_updates'], False)
            self.assertTrue(data['offline'])
            self.assertNotIn('must-not-inherit', result.stdout)
            self.assertFalse((resources / 'voice' / 'app' / 'output').exists())

    def test_worker_rejects_state_inside_immutable_bundle(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            resources = base / 'Proof.app' / 'Contents' / 'Resources'
            resources.mkdir(parents=True)
            shutil.copyfile(ROOT / 'packaging/voice_worker.py', resources / 'voice_worker.py')
            models = base / 'models'
            models.mkdir()
            state = resources / 'state'
            result = subprocess.run([sys.executable, '-I', '-B', str(resources / 'voice_worker.py'),
                '--models-dir', str(models), '--data-dir', str(state), '--offline', '--check'],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn('outside the app bundle', result.stderr)
            self.assertFalse(state.exists())


class StagingTests(unittest.TestCase):
    def test_staging_refuses_external_runtime_symlinks_and_existing_output(self):
        spec = importlib.util.spec_from_file_location('stage_voice', ROOT / 'packaging/stage_voice.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            runtime = base / 'runtime'
            (runtime / 'bin').mkdir(parents=True)
            (runtime / 'bin/python3').symlink_to(sys.executable)
            with self.assertRaisesRegex(ValueError, 'outside runtime'):
                module.validate_runtime(runtime)
            output = base / 'existing.app'
            output.mkdir()
            with self.assertRaises(FileExistsError):
                module.stage(runtime, output)

    def test_complete_stage_excludes_untracked_state_and_hashes_the_payload(self):
        spec = importlib.util.spec_from_file_location('stage_voice', ROOT / 'packaging/stage_voice.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            repo = base / 'repo'
            repo.mkdir()
            files = {
                'VERSION': '0.1.0',
                'CHANGELOG.md': '# Standalone changes',
                'components/voice/app/backend/main.py': '# source',
                'components/voice/app/frontend/index.html': '<html>Proof</html>',
                'components/voice/model-audits/example.audit.json': '{}',
                'components/voice/VERSION': '2.7.2',
                'components/voice/CHANGELOG.md': '# Upstream',
                'research/voice-adoption.json': json.dumps({'commit': 'b'*40, 'version': '2.7.2'}),
                'packaging/voice_worker.py': '# worker',
            }
            for name, content in files.items():
                path = repo / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
            subprocess.run(['git', 'init', '-q', str(repo)], check=True)
            subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
            subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Fixture',
                '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'Fixture'], check=True)
            (repo/'components/voice/app/frontend/settings.json').write_text('fixture-secret')
            (repo/'components/voice/app/backend/untracked_secret.py').write_text('fixture-secret')
            (repo/'components/voice/model-audits/.fleet_token').write_text('fixture-secret')
            runtime = base / 'runtime'
            (runtime/'bin').mkdir(parents=True)
            (runtime/'bin/python3').write_text('#!/bin/sh\n/usr/bin/env\n')
            (runtime/'bin/python3').chmod(0o755)
            artifact = base/'Proof.app'
            with mock.patch.object(module, 'ROOT', repo):
                module.stage(runtime, artifact)
            resources = artifact/'Contents/Resources'
            launch = subprocess.run([str(artifact/'Contents/MacOS/StudioHubVoice')],
                env={'PATH': '/usr/bin:/bin', 'KH_STUDIO_TOKEN': 'inherited-secret'},
                text=True, capture_output=True)
            self.assertEqual(launch.returncode, 0, launch.stderr)
            self.assertNotIn('inherited-secret', launch.stdout)
            self.assertFalse((resources/'voice/app/frontend/settings.json').exists())
            self.assertFalse((resources/'voice/app/backend/untracked_secret.py').exists())
            self.assertFalse((resources/'voice/model-audits/.fleet_token').exists())
            manifest_path = resources/'release-manifest.json'
            manifest = json.loads(manifest_path.read_text())
            self.assertEqual(manifest['payload_sha256'],
                module.tree_digest(artifact/'Contents', exclude={Path('Resources/release-manifest.json')}))
            self.assertEqual((base/'Proof.app.tree-sha256').read_text().strip(), module.tree_digest(artifact))
            (resources/'voice_worker.py').write_text('# changed worker')
            self.assertNotEqual(manifest['payload_sha256'],
                module.tree_digest(artifact/'Contents', exclude={Path('Resources/release-manifest.json')}))


if __name__ == '__main__':
    unittest.main()
