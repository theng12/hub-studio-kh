"""Stage the local Voice proof. Build tool only; never shipped to fleet nodes."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import stat
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def tree_digest(root: Path, exclude: set[Path] | None = None) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob('*')):
        if exclude and path.relative_to(root) in exclude:
            continue
        if path.is_dir() and not path.is_symlink():
            continue
        digest.update(path.relative_to(root).as_posix().encode() + b'\0')
        if path.is_symlink():
            digest.update(b'link\0' + os.readlink(path).encode() + b'\0')
        else:
            digest.update(b'file\0' + str(stat.S_IMODE(path.stat().st_mode)).encode() + b'\0')
            with path.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(chunk)
            digest.update(b'\0')
    return digest.hexdigest()


def validate_runtime(runtime: Path) -> None:
    runtime = runtime.resolve()
    for path in runtime.rglob('*'):
        if path.is_symlink():
            resolved = path.resolve()
            if runtime not in resolved.parents or not resolved.exists():
                raise ValueError(f'Link points outside runtime or is broken: {path}')
            if os.path.isabs(os.readlink(path)):
                raise ValueError(f'Absolute runtime link will not relocate: {path}')
    if not (runtime / 'bin/python3').is_file():
        raise ValueError('Runtime has no bin/python3')


def validate_native_shell(native_shell: Path) -> None:
    if not native_shell.is_file():
        raise FileNotFoundError(f'native shell binary does not exist: {native_shell}')
    if not os.access(native_shell, os.X_OK):
        raise ValueError(f'native shell binary is not executable: {native_shell}')


def copy_tracked_tree(source: Path, destination: Path, suffix: str | None = None) -> None:
    names = subprocess.check_output(['git', '-C', str(ROOT), 'ls-files', '-z', '--',
        source.relative_to(ROOT).as_posix()]).decode().split('\0')
    for name in filter(None, names):
        path = ROOT / name
        if suffix and path.suffix != suffix:
            continue
        if path.is_symlink():
            raise ValueError(f'Shipped resource cannot be a symlink: {path}')
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)


def stage(runtime: Path, output: Path, native_shell: Path | None = None, *, include_hub: bool = False, image_runtime: Path | None = None) -> Path:
    output = output.expanduser().absolute()
    if output.exists():
        raise FileExistsError(f'Refusing to replace existing artifact: {output}')
    validate_runtime(runtime)
    if image_runtime is not None:
        image_runtime = image_runtime.expanduser().resolve()
        validate_runtime(image_runtime)
        include_hub = True
    if native_shell is not None:
        native_shell = native_shell.expanduser().resolve()
        validate_native_shell(native_shell)
        include_hub = True
    source = ROOT / 'components/voice'
    provenance = json.loads((ROOT / 'research/voice-adoption.json').read_text())
    hub_provenance = None
    if include_hub:
        hub_provenance = json.loads((ROOT / 'research/hub-adoption.json').read_text())['source']
        if not (ROOT / 'components/hub/standalone.py').is_file():
            raise ValueError('Bundled Hub entry point is missing')
        if not subprocess.check_output(['git', '-C', str(ROOT), 'ls-files', '--',
                                        'components/hub/standalone.py']).strip():
            raise ValueError('Hub source must be added to Git before staging the bundle')
    image_provenance = None
    if image_runtime is not None:
        image_provenance = json.loads((ROOT / 'research/image-adoption.json').read_text())['source']
        if not (ROOT / 'components/image/app/backend/main.py').is_file():
            raise ValueError('Bundled Image entry point is missing')
        if not subprocess.check_output(['git', '-C', str(ROOT), 'ls-files', '--',
                                        'components/image/app/backend/main.py']).strip():
            raise ValueError('Image source must be added to Git before staging the bundle')
    commit = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip()
    dirty = bool(subprocess.check_output(['git', '-C', str(ROOT), 'status', '--porcelain'], text=True).strip())
    version = (ROOT / 'VERSION').read_text().strip()
    contents = output / 'Contents'
    resources = contents / 'Resources'
    resources.mkdir(parents=True)
    voice = resources / 'voice'
    # Explicit source allowlist; never copy live state, caches, tests or tools.
    copy_tracked_tree(source / 'app/backend', voice / 'app/backend', suffix='.py')
    for directory in ('app/frontend', 'model-audits'):
        copy_tracked_tree(source / directory, voice / directory)
    for name in ('VERSION', 'CHANGELOG.md'):
        shutil.copyfile(ROOT / name, voice / name)
    # The owner-facing notes go at the top of Resources, where `Bundle.main` finds them for the
    # app's What's New screen. Bundling the shipped file rather than a second copy means the screen
    # cannot describe a build it is not part of.
    shutil.copyfile(ROOT / 'release-notes.md', resources / 'release-notes.md')
    shutil.copytree(runtime, resources / 'runtimes/voice', symlinks=True)
    shutil.copyfile(ROOT / 'packaging/voice_worker.py', resources / 'voice_worker.py')
    runtime_hash = tree_digest(resources / 'runtimes/voice')
    release = {
        'schema': 'studiohub.standalone-release', 'schema_version': 1,
        'version': version, 'implementation_commit': commit,
        'development_build': dirty, 'qualification': 'unqualified-local-proof',
        'components': {'voice': {'upstream_commit': provenance['commit'],
                               'upstream_version': provenance['version'],
                               'source_sha256': tree_digest(voice)}},
        'runtime_sha256': runtime_hash,
    }
    if include_hub:
        hub = resources / 'components/hub'
        copy_tracked_tree(ROOT / 'components/hub', hub)
        shutil.copyfile(ROOT / 'components/model_packages.py', resources / 'components/model_packages.py')
        copy_tracked_tree(ROOT / 'model-manifests', resources / 'model-manifests', suffix='.json')
        shutil.copyfile(ROOT / 'packaging/hub_worker.py', resources / 'hub_worker.py')
        release['components']['hub'] = {
            'upstream_commit': hub_provenance['commit'],
            'upstream_version': hub_provenance['version'],
            'source_sha256': tree_digest(hub), 'runtime': 'voice',
        }
        # Independent Subtitle worker: its own process and state root, but it reuses the shared
        # (already staged) Voice runtime bytes and imports Voice's transcription module from
        # Resources/voice/app instead of carrying a second copy.
        subtitle_source = resources / 'components/subtitle'
        copy_tracked_tree(ROOT / 'components/subtitle', subtitle_source)
        shutil.copyfile(ROOT / 'packaging/subtitle_worker.py', resources / 'subtitle_worker.py')
        release['components']['subtitle'] = {
            'source_sha256': tree_digest(subtitle_source), 'runtime': 'voice',
            'runtime_sha256': release['runtime_sha256'],
        }
    if image_runtime is not None:
        image_source = resources / 'components/image'
        copy_tracked_tree(ROOT / 'components/image', image_source)
        shutil.copytree(image_runtime, resources / 'runtimes/image', symlinks=True)
        shutil.copyfile(ROOT / 'packaging/image_worker.py', resources / 'image_worker.py')
        release['components']['image'] = {
            'upstream_commit': image_provenance['commit'],
            'upstream_version': image_provenance['version'],
            'source_sha256': tree_digest(image_source), 'runtime': 'image',
            'runtime_sha256': tree_digest(resources / 'runtimes/image'),
        }
    service_name = 'StudioHubService' if include_hub else 'StudioHubVoice'
    entry_point = 'hub_worker.py' if include_hub else 'voice_worker.py'
    executable = contents / f'MacOS/{service_name}'
    executable.parent.mkdir()
    executable.write_text('#!/bin/sh\nset -eu\n'
        'BUNDLE_BIN=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
        'exec /usr/bin/env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin LANG=en_US.UTF-8 '
        '"$BUNDLE_BIN/../Resources/runtimes/voice/bin/python3" -I -B '
        f'"$BUNDLE_BIN/../Resources/{entry_point}" "$@"\n')
    executable.chmod(0o755)
    # The app's display name carries no version on purpose. It used to be produced as
    # `Studio Hub 0.4.0.app`, which meant every launcher, verify script and instruction file
    # hardcoded a version that a release bump then invalidated. The version lives in
    # `CFBundleShortVersionString` and the release manifest, where it belongs.
    plist = {'CFBundleExecutable': service_name,
        'CFBundleIdentifier': 'local.studiohub.hubproof' if include_hub else 'local.studiohub.voiceproof',
        'CFBundleName': 'Studio Hub Local Proof' if include_hub else 'Studio Hub Voice Proof', 'CFBundlePackageType': 'APPL',
        'CFBundleShortVersionString': version, 'CFBundleVersion': '1',
        'LSMinimumSystemVersion': '14.0', 'LSUIElement': True}
    if native_shell is not None:
        native_executable = contents / 'MacOS/StudioHub'
        shutil.copyfile(native_shell, native_executable)
        native_executable.chmod(0o755)
        plist.update({
            'CFBundleExecutable': 'StudioHub',
            'CFBundleIdentifier': 'local.studiohub.standalone',
            'CFBundleName': 'Studio Hub',
            'NSMicrophoneUsageDescription':
                'Studio Hub uses the microphone to record audio for transcription.',
        })
        plist.pop('LSUIElement')
    with (contents / 'Info.plist').open('wb') as stream:
        plistlib.dump(plist, stream)
    # Hash all shipped code/resources including the worker, shell and plist.
    # The manifest is excluded from its own payload hash to avoid recursion.
    release['payload_sha256'] = tree_digest(contents)
    (resources / 'release-manifest.json').write_text(json.dumps(release, indent=2) + '\n')
    output.with_name(output.name + '.tree-sha256').write_text(tree_digest(output) + '\n')
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--native-shell', type=Path)
    parser.add_argument('--image-runtime', type=Path, help='Include the independently qualified Image runtime and worker')
    parser.add_argument('--hub', action='store_true', help='Include the local Hub service (always enabled for native app builds)')
    args = parser.parse_args()
    print(stage(args.runtime, args.output, native_shell=args.native_shell, include_hub=args.hub, image_runtime=args.image_runtime))
