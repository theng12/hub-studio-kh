"""Stage the local Voice proof. Build tool only; never shipped to fleet nodes."""
from __future__ import annotations

import argparse
from collections.abc import Callable
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
MUSIC_ENGINE_REPO = 'ace-step/ACE-Step-1.5'
MUSIC_ENGINE_REVISION = 'ca1e85fe9430179831e6bc6be790c332190a3866'
MUSIC_ENGINE_SOURCE_SHA256 = 'a55b4bf166464a690e967b4a31862e39efc6bd954ef0214ac91030d99e97cb33'
# The audited 177-file overlay, re-pinned on 2026-09-23 when tree_digest stopped including file
# permissions. The tree is unchanged; only the algorithm is, so this was re-derived from the
# same source rather than re-vendored.
MUSIC_OVERLAY_SHA256 = 'f17b81b9bc9feb8e055c6c76ef6ccb78461c991be3727d59c86959d7361d6a67'
MUSIC_OVERLAY_PACKAGES = {
    'diskcache': '5.6.3', 'einops': '0.8.2', 'einx': '0.3.0',
    'frozendict': '2.4.7', 'loguru': '0.7.3', 'toml': '0.10.2',
    'vector-quantize-pytorch': '1.27.20',
}


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
            # Content only, deliberately not the permission bits. The identity is checked at
            # startup by the worker, and a tree whose bytes are identical but whose modes differ
            # - which a copy, an install or a chmod can produce - must not be able to stop the
            # app from starting. It did: a Music overlay that read 0444 in the kit and 0644 once
            # installed made the Music worker refuse to run, so the Hub never came up.
            digest.update(b'file\0')
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


def _music_source_names(source: Path) -> list[str]:
    output = subprocess.check_output([
        'git', '-C', str(source), 'ls-files', '-z', '--',
        'acestep', 'LICENSE', 'pyproject.toml'])
    names = sorted(filter(None, output.decode().split('\0')))
    if not names:
        raise ValueError('ACE-Step source inventory is empty')
    return names


def music_source_digest(source: Path) -> str:
    digest = hashlib.sha256()
    for name in _music_source_names(source):
        path = source / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f'ACE-Step source must contain regular files only: {name}')
        size = path.stat().st_size
        digest.update(name.encode() + b'\0' + str(size).encode() + b'\0')
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
        digest.update(b'\0')
    return digest.hexdigest()


def validate_music_overlay(overlay: Path) -> dict:
    overlay = overlay.resolve()
    manifest = overlay / 'manifest/music-runtime.json'
    try:
        value = json.loads(manifest.read_text())
    except (OSError, ValueError) as exc:
        raise ValueError('Music runtime overlay manifest is unavailable') from exc
    if value != {'schema': 'studiohub.music-runtime-overlay', 'schema_version': 1,
                 'packages': MUSIC_OVERLAY_PACKAGES}:
        raise ValueError('Music runtime overlay identity is invalid')
    for path in overlay.rglob('*'):
        if path.is_symlink():
            raise ValueError(f'Music runtime overlay cannot contain symlinks: {path}')
    if tree_digest(overlay) != MUSIC_OVERLAY_SHA256:
        raise ValueError('Music runtime overlay digest does not match the pinned build')
    return value


def stage_music_assets(engine_source: Path, overlay: Path, resources: Path,
                       base_runtime_sha256: str) -> dict:
    engine_source = engine_source.expanduser().resolve()
    overlay = overlay.expanduser().resolve()
    validate_music_overlay(overlay)
    revision = subprocess.check_output(
        ['git', '-C', str(engine_source), 'rev-parse', 'HEAD'], text=True).strip()
    changed = subprocess.check_output([
        'git', '-C', str(engine_source), 'status', '--porcelain',
        '--untracked-files=all', '--', 'acestep', 'LICENSE', 'pyproject.toml'],
        text=True).strip()
    source_digest = music_source_digest(engine_source)
    if revision != MUSIC_ENGINE_REVISION or changed or source_digest != MUSIC_ENGINE_SOURCE_SHA256:
        raise ValueError('ACE-Step source is not the exact clean pinned revision')
    destination = resources / 'engines/ace-step-1-5'
    for name in _music_source_names(engine_source):
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(engine_source / name, target)
    provenance = {
        'schema': 'studiohub.music-engine-source', 'schema_version': 1,
        'repo': MUSIC_ENGINE_REPO, 'revision': MUSIC_ENGINE_REVISION,
        'source_sha256': MUSIC_ENGINE_SOURCE_SHA256,
    }
    (destination / 'studiohub-engine.json').write_text(json.dumps(provenance, indent=2) + '\n')
    staged_overlay = resources / 'runtimes/music'
    shutil.copytree(overlay, staged_overlay)
    overlay_digest = tree_digest(staged_overlay)
    runtime_digest = hashlib.sha256(
        (base_runtime_sha256 + '\0' + overlay_digest).encode()).hexdigest()
    return {
        'engine_repo': MUSIC_ENGINE_REPO,
        'engine_revision': MUSIC_ENGINE_REVISION,
        'engine_source_sha256': MUSIC_ENGINE_SOURCE_SHA256,
        'runtime': 'voice+music-overlay',
        'base_runtime_sha256': base_runtime_sha256,
        'overlay_sha256': overlay_digest,
        'runtime_sha256': runtime_digest,
    }


def _stage_voice(context: dict) -> dict:
    """The Voice Studio. Always present; every other worker reuses its staged runtime."""
    resources = context['resources']
    source = ROOT / 'components/voice'
    voice = resources / 'voice'
    # Explicit source allowlist; never copy live state, caches, tests or tools.
    copy_tracked_tree(source / 'app/backend', voice / 'app/backend', suffix='.py')
    for directory in ('app/frontend', 'model-audits'):
        copy_tracked_tree(source / directory, voice / directory)
    # The Voice worker resolves its own facts from the shipped manifests: the Qwen clone-quality
    # validator reads the model's `requires_packages`, and the Whisper tokenizer source comes from
    # the model's own manifest. Staging only for the Hub (`_stage_hub`) meant a **voice-only**
    # artifact - which is what the smoke pilot builds - had no manifests at all, so a clone failed
    # `QWEN_VALIDATOR_MODEL_UNDECLARED` after its reference had prepared fine, and the ASR leg
    # could not resolve its processor. The fix the smoke needed, and the difference between a
    # worker that can explain itself and one that cannot.
    copy_tracked_tree(ROOT / 'model-manifests', resources / 'model-manifests', suffix='.json')
    for name in ('VERSION', 'CHANGELOG.md'):
        shutil.copyfile(ROOT / name, voice / name)
    provenance = context['provenance']
    return {
        'upstream_commit': provenance['commit'],
        'upstream_version': provenance['version'],
        'source_sha256': tree_digest(voice),
    }


def shared_components_modules(components: Path) -> list[Path]:
    """The top-level modules under ``components/`` that any worker may import.

    `model_packages` (the package library) and `app_version` (the release identity) are imported by
    workers that do not otherwise share a tree. Discovered by glob rather than listed by name: a
    hand-written list is silently wrong the day someone adds a module to it, and the symptom is a
    worker failing at import in a shipped bundle rather than a build failure here.
    """
    return sorted(path for path in components.glob('*.py') if path.is_file())


def _stage_hub(context: dict) -> dict | None:
    if not context['include_hub']:
        return None
    resources = context['resources']
    hub = resources / 'components/hub'
    copy_tracked_tree(ROOT / 'components/hub', hub)
    (resources / 'components').mkdir(parents=True, exist_ok=True)
    for module in shared_components_modules(ROOT / 'components'):
        shutil.copyfile(module, resources / 'components' / module.name)
    copy_tracked_tree(ROOT / 'model-manifests', resources / 'model-manifests', suffix='.json')
    shutil.copyfile(ROOT / 'packaging/hub_worker.py', resources / 'hub_worker.py')
    provenance = context['hub_provenance']
    return {
        'upstream_commit': provenance['commit'],
        'upstream_version': provenance['version'],
        'source_sha256': tree_digest(hub), 'runtime': 'voice',
    }


def _stage_subtitle(context: dict) -> dict | None:
    """Independent Subtitle worker: its own process and state root, but it reuses the shared
    (already staged) Voice runtime bytes and imports Voice's transcription module from
    Resources/voice/app instead of carrying a second copy."""
    if not context['include_hub']:
        return None
    resources = context['resources']
    subtitle_source = resources / 'components/subtitle'
    copy_tracked_tree(ROOT / 'components/subtitle', subtitle_source)
    shutil.copyfile(ROOT / 'packaging/subtitle_worker.py', resources / 'subtitle_worker.py')
    return {
        'source_sha256': tree_digest(subtitle_source), 'runtime': 'voice',
        'runtime_sha256': context['runtime_hash'],
    }


def _stage_chat(context: dict) -> dict | None:
    """ST-14: the local Chat Studio. Its own process and state root, reusing the shared
    (already staged) Voice runtime bytes, which carry mlx-lm."""
    if not context['include_hub']:
        return None
    resources = context['resources']
    chat_source = resources / 'components/chat'
    copy_tracked_tree(ROOT / 'components/chat', chat_source)
    shutil.copyfile(ROOT / 'packaging/chat_worker.py', resources / 'chat_worker.py')
    return {
        'source_sha256': tree_digest(chat_source), 'runtime': 'voice',
        'runtime_sha256': context['runtime_hash'],
    }


def _stage_music(context: dict) -> dict | None:
    if not (context['include_hub'] and context['music_runtime'] is not None):
        return None
    resources = context['resources']
    music_source = resources / 'components/music'
    copy_tracked_tree(ROOT / 'components/music', music_source)
    shutil.copyfile(ROOT / 'packaging/music_worker.py', resources / 'music_worker.py')
    return {
        'source_sha256': tree_digest(music_source),
        **stage_music_assets(context['music_engine'], context['music_runtime'],
                             resources, context['runtime_hash']),
    }


def _stage_image(context: dict) -> dict | None:
    if context['image_runtime'] is None:
        return None
    resources = context['resources']
    image_source = resources / 'components/image'
    copy_tracked_tree(ROOT / 'components/image', image_source)
    shutil.copytree(context['image_runtime'], resources / 'runtimes/image', symlinks=True)
    shutil.copyfile(ROOT / 'packaging/image_worker.py', resources / 'image_worker.py')
    provenance = context['image_provenance']
    return {
        'upstream_commit': provenance['commit'],
        'upstream_version': provenance['version'],
        'source_sha256': tree_digest(image_source), 'runtime': 'image',
        'runtime_sha256': tree_digest(resources / 'runtimes/image'),
    }


# One entry per studio that can ship, in the order components appear in the staged bundle and
# in the release manifest. A stager copies that studio's tracked source and returns its release
# record, or None when this build does not include it (a Voice-only proof build has no Hub).
#
# This table is the only place a studio is added to a release. `stage()` refuses a
# `components/<studio>/app/backend/main.py` tree with no entry here, so a new studio cannot
# ship nothing while the build succeeds; `tests/test_chat_packaging.py` checks this table
# against the Hub's `COMPONENT_WORKERS` table, so a studio cannot be staged without a worker
# (or spawned without being staged).
COMPONENT_STAGERS: dict[str, Callable[[dict], dict | None]] = {
    'voice': _stage_voice,
    'hub': _stage_hub,
    'subtitle': _stage_subtitle,
    'chat': _stage_chat,
    'music': _stage_music,
    'image': _stage_image,
}


def unregistered_components(root: Path) -> list[str]:
    """Studio source trees under components/ that this build would silently not ship."""
    components = root / 'components'
    if not components.is_dir():
        return []
    return sorted(
        path.name for path in components.iterdir()
        if (path / 'app/backend/main.py').is_file() and path.name not in COMPONENT_STAGERS)


def stage(runtime: Path, output: Path, native_shell: Path | None = None, *,
          include_hub: bool = False, image_runtime: Path | None = None,
          music_runtime: Path | None = None, music_engine: Path | None = None) -> Path:
    output = output.expanduser().absolute()
    if output.exists():
        raise FileExistsError(f'Refusing to replace existing artifact: {output}')
    validate_runtime(runtime)
    if image_runtime is not None:
        image_runtime = image_runtime.expanduser().resolve()
        validate_runtime(image_runtime)
        include_hub = True
    if (music_runtime is None) != (music_engine is None):
        raise ValueError('Music runtime overlay and engine source must be supplied together')
    if music_runtime is not None:
        music_runtime = music_runtime.expanduser().resolve()
        music_engine = music_engine.expanduser().resolve()
        validate_music_overlay(music_runtime)
        include_hub = True
    if native_shell is not None:
        native_shell = native_shell.expanduser().resolve()
        validate_native_shell(native_shell)
        include_hub = True
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
    # A studio whose source tree exists but has no stager would be left out of the bundle and
    # out of the release manifest while the build still succeeded. Refuse it by name instead.
    unregistered = unregistered_components(ROOT)
    if unregistered:
        raise ValueError(
            'these component source trees are not staged by this build: ' + ', '.join(unregistered)
            + '. Add each one to COMPONENT_STAGERS in packaging/stage_voice.py and to '
              'COMPONENT_WORKERS in packaging/hub_worker.py so a new studio cannot ship nothing '
              'while the build succeeds.')
    contents = output / 'Contents'
    resources = contents / 'Resources'
    resources.mkdir(parents=True)
    # The app's icon. Generated by `packaging/make_app_icon.py` and committed, so a build cannot
    # quietly ship the blank page macOS shows for a bundle with no icon. Copied for every build,
    # including the Hub-less Voice proof, because the plist names it unconditionally.
    icon = ROOT / 'assets' / 'app-icon.icns'
    if not icon.is_file():
        raise FileNotFoundError(
            f"the app icon is missing: {icon.relative_to(ROOT)}. "
            "Run packaging/make_app_icon.py and commit the result.")
    shutil.copyfile(icon, resources / 'app-icon.icns')
    # The owner-facing notes go at the top of Resources, where `Bundle.main` finds them for the
    # app's What's New screen. Bundling the shipped file rather than a second copy means the screen
    # cannot describe a build it is not part of.
    shutil.copyfile(ROOT / 'release-notes.md', resources / 'release-notes.md')
    shutil.copytree(runtime, resources / 'runtimes/voice', symlinks=True)
    # The shared private-worker lifecycle every wrapper imports; not a component of its own.
    shutil.copyfile(ROOT / 'packaging/voice_worker.py', resources / 'voice_worker.py')
    runtime_hash = tree_digest(resources / 'runtimes/voice')
    context = {
        'resources': resources, 'runtime_hash': runtime_hash, 'include_hub': include_hub,
        'image_runtime': image_runtime, 'music_runtime': music_runtime,
        'music_engine': music_engine, 'provenance': provenance,
        'hub_provenance': hub_provenance, 'image_provenance': image_provenance,
    }
    # `release['components']` is the data every consumer reads - the Hub spawns exactly these
    # keys and the kit describes exactly these keys - so it is assembled by iterating the
    # component table rather than by an if-ladder here.
    components = {}
    for name, stage_component in COMPONENT_STAGERS.items():
        record = stage_component(context)
        if record is not None:
            components[name] = record
    release = {
        'schema': 'studiohub.standalone-release', 'schema_version': 1,
        'version': version, 'implementation_commit': commit,
        'development_build': dirty, 'qualification': 'unqualified-local-proof',
        'components': components,
        'runtime_sha256': runtime_hash,
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
        # The exact build. The product version is unchanged across releases in a day, so
        # without this an operator cannot tell two copies of one product version apart when
        # checking whether an update actually landed.
        'StudioHubBuild': str(commit)[:7],
        # App Transport Security blocks plain HTTP to anything that is not loopback. The Hub is
        # reached over the LAN and the tailnet with a token, so the app needs the broad
        # exception. It must be declared ALONE: when a granular key is present too, macOS
        # ignores NSAllowsArbitraryLoads in favour of it, and NSAllowsLocalNetworking does not
        # cover the tailnet's carrier-grade NAT range - which is exactly the address a node is
        # given. Shipping both keys produced "requires the use of a secure connection" (code
        # -1022) against a 100.x.y.z controller that was up and answering.
        'NSAppTransportSecurity': {
            'NSAllowsArbitraryLoads': True,
        },
        # Finder, the Dock and the About panel all read the icon from the bundle. Without this key
        # the file is present and ignored, which is how the app shipped with no icon at all.
        'CFBundleIconFile': 'app-icon',
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
            # macOS gates local-network access per app, separately from App Transport Security,
            # and an app that declares nothing here is refused rather than prompted - which is
            # what a node hit when it tried to reach its controller. Granting the prompt puts
            # this app in System Settings > Privacy & Security > Local Network.
            'NSLocalNetworkUsageDescription':
                'Studio Hub talks to your other Macs on this network to send and receive work.',
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
    parser.add_argument('--music-runtime', type=Path,
                        help='Include the pinned Music dependency overlay')
    parser.add_argument('--music-engine', type=Path,
                        help='Include the exact pinned ACE-Step source checkout')
    parser.add_argument('--hub', action='store_true', help='Include the local Hub service (always enabled for native app builds)')
    args = parser.parse_args()
    print(stage(args.runtime, args.output, native_shell=args.native_shell,
                include_hub=args.hub, image_runtime=args.image_runtime,
                music_runtime=args.music_runtime, music_engine=args.music_engine))
