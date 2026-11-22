"""Stage the local Voice proof. Build tool only; never shipped to fleet nodes."""
from __future__ import annotations

import argparse
from collections.abc import Callable
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Literal

ROOT = Path(__file__).resolve().parents[1]
RenderAuthorityMode = Literal['tool-only', 'descriptor-only', 'sealed-candidate']
RENDER_AUTHORITY_MODES: tuple[RenderAuthorityMode, ...] = (
    'tool-only', 'descriptor-only', 'sealed-candidate')

# The Image runtime is the proven mflux 0.23 base plus pinned mlx-serve 26.10.1 native subtree.
# The native engine is qualified on 24 GiB (packaged M4 run, qualification/evidence/qwen-image-21-20261008-24gb);
# the low-q4 package on 16 GiB (packaged M4 run, qualification/evidence/qwen-image-21-20261008-16gb).
# The Image runtime this build is qualified against, pinned by content. It includes **mflux 0.23.0**
# (mlx / mlx-metal 0.32.4, pillow 12.3.0, urllib3 2.8.0) - the engine that carries
# `Flux2KleinEdit` and `QwenImage21Edit`, measured for the Reference-to-Image lane in
# `qualification/evidence/image-edit-2026-10-26.jsonl`, and the engine the three text-to-image
# models were re-measured on in `qualification/evidence/image-runtime-0.23-2026-10-26.jsonl`.
# It replaced the mflux 0.20.0 tree in 0.6.202, when Reference-to-Image shipped; that tree is
# still named below so a build handed the old one says which it is.
#
# Nothing pinned this before, and the release loop in `docs/tasks/HANDOFF.md` named
# `.build/st03-image-runtime-worker` - which is the **superseded 0.17.5** runtime preserved for the
# ST-03 receipt. Following that line shipped 0.17.5 in every release from 0.6.63 to 0.6.67, after
# 0.6.59-0.6.62 had shipped 0.20.0: five releases publishing Image numbers - Z-Image's 166 s and
# 236 s, its peaks, its `measured_on` - for an engine the app did not contain. It was found by the
# owner's 8 GB Z-Image run, whose harness checks the engine version and refused; the refusal's own
# advice ("install the kit's app, it carries mflux 0.20.0") was false, because the kit carried
# 0.17.5 too.
#
# A version string would not have caught it - both trees contain an `mflux` package and both accept
# the same commands. The content digest is what distinguishes them, and refusing here is what makes
# the mistake impossible rather than merely documented.
# 0.6.222 appends psutil 7.2.2 without changing engine files; native audit, isolated smoke
# and the live admission reader passed. See qualification/evidence/image-runtime-psutil-20261106.json.
# 0.6.246 rebuilt the exact same lock (`cd8cd947...`), CPython (`3.12.14`), and `mlx-serve 26.10.1`
# after the cached Nov 6 tree was retired (`smoke_imports.py` also includes `multipart` since `f3b8c3e1`).
#
# **Re-qualified 2026-11-21.** The previous pin was a *build-path* identity: each `RECORD` was
# written before the shebang rewrite, so the same packages built in two checkouts had two digests
# and no second path could ever match. The recipe now rewrites every RECORD after the rewrite and
# leaves out build junk (`runtime_packaging.py normalize-records` / `check-build-junk`); this value is
# what it produces, proven at two checkout paths (one identical digest) and by a real generation
# (512x512 FLUX.2 Klein picture, sha256 c80795c570c0a6b8f9d8f951fb6958135352a615c8344127ebc74468b61e14ba,
# evidence in Hub Lead 004's receipt). The previous value stays in git history.
IMAGE_RUNTIME_SHA256 ='a08b58527f2946b211648708fd7f8b9912ada00f98ade82a1dc62514cf580158'
# Kept so the refusal can name what it was handed and where that tree came from.
IMAGE_RUNTIME_SUPERSEDED = {
    '810a725d20ef9bbc942d53bfea39e56327964d519d3cb4f33a5eea13a4a08178':
        'mflux 0.23.0 + mlx 0.32.4 + psutil 7.2.2 built 2026-11-06 before smoke_imports multipart pin',
    'c5ac478f85d5687e0bd6df715eec5d55a3a59e36aae4f14da352f45ea546923a':
        'mflux 0.20.0 + mlx 0.32.2, shipped through 0.6.201',
    'c3acf7661fb9e214c2273f01a61d9ac810250847274959a30ebc2e80c646491f':
        'base mflux 0.20 runtime before Qwen native subtree (through 0.6.149)',
    '7b9b9ff831caa254b928c9d5d05c86df776fbc0fc459a4725cc969c898cba243':
        '.build/st03-image-runtime-worker (mflux 0.17.5, preserved for the ST-03 receipt)',
}
# The Video runtime this build is qualified against, pinned by content the same way the Image
# runtime is. It is **Python 3.11 + ltx-2-mlx at 3b2e77fd73af5f08961f4436ae979c0579d5deed + MLX
# 0.32.2**, because that is the interpreter `qualification/ltx-video-measure.py` measured the
# first successful LTX-2.3 image-to-video run on (298.5 s, 704x448, 97 frames, 4.0 s with audio,
# peak process footprint 16.38 GB). `ltx_pipelines_mlx` needs 3.11 while the base runtime is
# 3.12, which is why Video carries its own tree.
#
# Nothing pinned this before because the lane is new. The rule it inherits is the one the Image
# runtime's pin exists for: a version string would not distinguish a tree that can load the pack
# from one that cannot — the pack's mixed 2-8 bit loader lives in the pack, but the pipeline and
# MLX must be the ones the measurement ran — so the content digest is the identity.
#
# **The tree carries the muxer too, and that is why the pin moved on 2026-09-26.** The video pack
# shells out to `ffmpeg -c:v libx264` for its MP4, and the app's other ffmpeg (the Voice runtime's,
# built minimal) has no H.264 encoder at all while still answering `-h encoder=libx264` with exit
# 0. `packaging/video-runtime/build.sh` now fetches the same pinned ffmpeg 8.1 (osxexperts.net,
# arm64) that Story Studio ships, verifies its sha256, and refuses to build a runtime that cannot
# answer for `libx264`.
VIDEO_RUNTIME_SHA256 = '27304a7a42a17cec3ee9c6acd22a9133794b8bdd331f2e18fa134db513cc2ad7'
# **The local video pack is retired (`components/retired_models.py`), and so is shipping this
# runtime.** The Video worker exists only to run that one model (`hub_worker._video_spec` and
# `video_worker.py` both pin its manifest by name), and a retired manifest is never staged, so a bundle that carried the worker
# would stop the whole Hub at start-up ("Incomplete Video worker"). While the package is on the retired
# list `stage()` therefore drops `--video-runtime` and the release has no `video` component: no
# `runtimes/video` (517 MB), no `video_worker.py`, no `components/video`. Taking the two ids off the
# retired list (and passing the flag again) is all it takes to ship Video again.
VIDEO_MODEL_PACKAGE = 'ltx-2-3-22b-ram-12gb-mlx'

# The Music lane's second engine. YuE2 needs its own interpreter because `mlx-Yue` pins
# `mlx==0.32.2` while the Voice runtime the Music worker uses carries `mlx==0.31.2`; the worker
# spawns the bridge with this tree, resolved as the sibling of its own.
#
# Moved at 0.6.248: the same packages and the same measured runs, except that CPython's generated
# sysconfigdata and `lib/libpython3.12.dylib`'s install name no longer record the build user's home
# path (packaging/yue2-runtime/neutralize_prefix.py).
#
# **Re-qualified 2026-11-21** with the Image pin and for the same reason: the old value carried the
# build directory through RECORD's self-entry and installer-written hashes. Proven at two checkout
# paths (one identical digest) and by a real generation (81.36 s, 48 kHz stereo, RMS 0.161, sha256
# f44dbc4a5c1e4b45fdff77487c031dfe9dfb14006c299083067e9b2b19dff147, evidence in Hub Lead 004's
# receipt). Earlier values stay in git history.
YUE2_RUNTIME_SHA256 ='9a47de1869bf569aa9f491f4ee524f5de23c68750d7604159917e1df63f60837'

# What a Hub release ships, declared here so a build cannot quietly narrow it.
#
# 0.6.199 shipped a 592 MB DMG (0.6.198 was 1.35 GB) whose Resources/runtimes held only `voice`:
# the helper driving the stager passed `--hub` and the two KH Updater flags, never
# `--image-runtime` or `--yue2-runtime`. Each stager whose flag was absent returned None, the
# manifest simply listed fewer components, and the build exited 0 - so `runtimes/image` (1.3 GB),
# `runtimes/music-yue2` (698 MB), `image_worker.py` and `yue2_runner.py` were missing from an
# artifact that installed and then could not run Image or sung-song Music. A successful build is
# not evidence that the release contains what the product line ships.
#
# This table is that declaration. `stage()` refuses a Hub bundle that lacks any of these, and the
# refusal names what is missing and the flags that stage it. A release that deliberately ships
# fewer has to edit this table - the change is then in the diff, not in a forgotten flag.
#
# What it cannot catch: a runtime present under the right name but built from the wrong contents
# (the Image and YuE2 content pins in their stagers do that), a missing model package (models live
# outside the app), a staged file altered after this check (the release manifest and a mount
# comparison do that), or a DMG step that drops something staging had (prove the mounted image).
HELPER_BUNDLE = Path('helper') / 'Hub Studio KH Helper.app'
HUB_RELEASE_ARTIFACTS: dict[str, tuple[str, ...]] = {
    'helper': ('Contents/MacOS/Hub Studio KH Helper', 'Contents/Info.plist'),
    'runtimes': ('voice', 'image', 'music-yue2'),
    'workers': (
        'hub_worker.py', 'voice_worker.py', 'subtitle_worker.py', 'chat_worker.py',
        'embeddings_worker.py', 'vision_worker.py', 'music_worker.py', 'yue2_runner.py',
        'image_worker.py',
    ),
}


def release_notes_text(changelog: str, version: str) -> str:
    """Project the plain-language release history from the one authored changelog."""
    entries = re.split(r'(?=^## \d+\.\d+\.\d+[^\n]*$)', changelog, flags=re.MULTILINE)[1:]
    notes = []
    for entry in entries:
        public = entry.split('\n### Technical notes\n', 1)[0].strip()
        if len(public.splitlines()) > 1:
            notes.append(public)
    if not any(re.match(rf'## {re.escape(version)}(?:\s|$)', note) for note in notes):
        raise ValueError(f'CHANGELOG.md has no plain-language changes for {version}')
    return "# What's New\n\n" + '\n\n'.join(notes) + '\n'


def missing_release_artifacts(resources: Path) -> list[str]:
    """Artifacts a Hub release declares (``HUB_RELEASE_ARTIFACTS``) that staging did not produce."""
    missing = [
        f'runtimes/{name}'
        for name in HUB_RELEASE_ARTIFACTS['runtimes']
        if not (resources / 'runtimes' / name / 'bin' / 'python3').is_file()]
    missing += [
        name for name in HUB_RELEASE_ARTIFACTS['workers']
        if not (resources / name).is_file()]
    missing += [
        f'helper/{name}' for name in HUB_RELEASE_ARTIFACTS['helper']
        if not (resources / HELPER_BUNDLE / name).is_file()]
    return missing


def tree_digest(root: Path, exclude: set[Path] | None = None) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob('*')):
        # An excluded path may name a directory: it and everything below it are left out.
        if exclude:
            relative = path.relative_to(root)
            if relative in exclude or any(parent in exclude for parent in relative.parents):
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


_RUNTIME_COPY_EXCLUDES = ('.DS_Store', '._*', '__pycache__', '*.pyc', '*.pyo')
_ignore_runtime_metadata = shutil.ignore_patterns(*_RUNTIME_COPY_EXCLUDES)


def runtime_input_digest(root: Path) -> str:
    # Check against the same content the bundle receives, without editing the input tree.
    return tree_digest(root, exclude={path.relative_to(root)
                                     for pattern in _RUNTIME_COPY_EXCLUDES for path in root.rglob(pattern)})


def _thin_signature_spans(stream, offset: int, length: int) -> list[tuple[int, int]]:
    import struct
    if length < 32:
        return []
    stream.seek(offset)
    head = stream.read(min(length, 65536))
    magic = head[:4]
    if magic == b'\xcf\xfa\xed\xfe':
        endian, header = '<', 32
    elif magic == b'\xfe\xed\xfa\xcf':
        endian, header = '>', 32
    elif magic == b'\xce\xfa\xed\xfe':
        endian, header = '<', 28
    elif magic == b'\xfe\xed\xfa\xce':
        endian, header = '>', 28
    else:
        return []
    ncmds = struct.unpack_from(endian + 'I', head, 16)[0]
    pos = header
    for _ in range(ncmds):
        if pos + 8 > len(head):
            return []
        cmd, size = struct.unpack_from(endian + 'II', head, pos)
        if cmd == 0x1D and pos + 16 <= len(head):
            dataoff, datasize = struct.unpack_from(endian + 'II', head, pos + 8)
            if datasize and 0 <= dataoff and offset + dataoff + datasize <= offset + length:
                return [(offset + dataoff, offset + dataoff + datasize)]
        if size < 8:
            return []
        pos += size
    return []


def _signature_spans(stream, size: int) -> list[tuple[int, int]]:
    """Byte spans of embedded LC_CODE_SIGNATURE blobs; empty for non-Mach-O files."""
    import struct
    stream.seek(0)
    prefix = stream.read(8)
    if len(prefix) < 8:
        return []
    magic = prefix[:4]
    fat64 = (b'\xca\xfe\xba\xbf', b'\xbf\xba\xfe\xca')
    fat32 = (b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca')
    if magic in fat32 or magic in fat64:
        endian = '>' if magic[:2] == b'\xca\xfe' else '<'
        step = 32 if magic in fat64 else 20
        nfat = struct.unpack_from(endian + 'I', prefix, 4)[0]
        table = stream.read(nfat * step)
        spans = []
        for i in range(nfat):
            rec = table[i * step:(i + 1) * step]
            if len(rec) < 16:
                return []
            if step == 32:
                off, length = struct.unpack_from(endian + 'QQ', rec, 8)
            else:
                off, length = struct.unpack_from(endian + 'II', rec, 8)
            if off + length > size:
                return []
            spans.extend(_thin_signature_spans(stream, off, length))
        return spans
    return _thin_signature_spans(stream, 0, size)


def payload_digest(root: Path, exclude: set[Path] | None = None) -> str:
    """tree_digest's mixing, except embedded LC_CODE_SIGNATURE blobs hash as zero bytes.

    codesign seals release-manifest.json into the principal executable's CodeDirectory, so
    re-signing after that file changes rewrites the signature bytes themselves; masking them
    for this digest only is what lets a recorded payload_sha256 equal the final
    recomputation. The signature bytes stay covered by codesign --deep --strict and by the
    unmasked .tree-sha256 beside the app.
    """
    digest = hashlib.sha256()
    for path in sorted(root.rglob('*')):
        if exclude:
            relative = path.relative_to(root)
            if relative in exclude or any(parent in exclude for parent in relative.parents):
                continue
        if path.is_dir() and not path.is_symlink():
            continue
        digest.update(path.relative_to(root).as_posix().encode() + b'\0')
        if path.is_symlink():
            digest.update(b'link\0' + os.readlink(path).encode() + b'\0')
            continue
        digest.update(b'file\0')
        size = path.stat().st_size
        with path.open('rb') as stream:
            spans = sorted(_signature_spans(stream, size))
            pos = 0
            for start, end in spans:
                if start < pos or end <= start:
                    continue
                stream.seek(pos)
                while pos < start:
                    chunk = stream.read(min(1024 * 1024, start - pos))
                    if not chunk:
                        break
                    digest.update(chunk)
                    pos += len(chunk)
                if pos != start:
                    break
                digest.update(b'\0' * (end - start))
                pos = end
            stream.seek(pos)
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
        digest.update(b'\0')
    return digest.hexdigest()


SIGNING_IDENTITY = 'KH Studio Local Signing'
_MACHO_MAGICS = {
    b'\xfe\xed\xfa\xce', b'\xce\xfa\xed\xfe', b'\xfe\xed\xfa\xcf', b'\xcf\xfa\xed\xfe',
    b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca', b'\xca\xfe\xba\xbf', b'\xbf\xba\xfe\xca',
}
_SIGNABLE_BUNDLES = {'.app', '.appex', '.bundle', '.framework', '.xpc'}
# codesign reports a copied executable's Authority for an unsealed bundle; these are the
# platform signatures of Apple's own tools, never an app identity to preserve.
_APPLE_PLATFORM_IDENTITIES = {'macOS Software Signing', 'Software Signing'}


def _is_macho(path: Path) -> bool:
    with path.open('rb') as stream:
        return stream.read(4) in _MACHO_MAGICS


def _is_script(path: Path) -> bool:
    """codesign signs scripts too; --deep verification treats Contents/MacOS ones as code."""
    if not os.access(path, os.X_OK):
        return False
    with path.open('rb') as stream:
        return stream.read(2) == b'#!'


def _existing_real_identity(app: Path) -> str | None:
    """An app already sealed with a real identity keeps it (owner 2026-11-08).

    Only the outer bundle's own seal counts. An unsigned bundle inherits the Authority of its
    main executable, so the fixture's copied system Mach-O would otherwise look signed.
    """
    if not (app / 'Contents/_CodeSignature/CodeResources').is_file():
        return None
    probe = subprocess.run(
        ['/usr/bin/codesign', '-d', '--verbose=4', str(app)],
        capture_output=True, text=True)
    if probe.returncode:
        return None
    for line in probe.stderr.splitlines():
        if line.startswith('Authority='):
            authority = line.split('=', 1)[1].strip()
            if (authority and authority != 'adhoc'
                    and authority not in _APPLE_PLATFORM_IDENTITIES):
                return authority
    return None


def _signing_identity(app: Path) -> str:
    identity = _existing_real_identity(app) or SIGNING_IDENTITY
    if identity != SIGNING_IDENTITY:
        return identity
    listed = subprocess.run(
        ['/usr/bin/security', 'find-identity', '-p', 'codesigning'],
        capture_output=True, text=True)
    if SIGNING_IDENTITY not in listed.stdout:
        raise RuntimeError(
            f'The shared signing identity {SIGNING_IDENTITY!r} is missing from this Mac\'s login '
            'signing identity. Packaging refuses to fall back to ad-hoc signing; create or import '
            'the signing identity, then stage again.')
    return identity


def _bundle_main_executable(bundle: Path) -> Path | None:
    for relative in ('Contents/Info.plist', 'Resources/Info.plist', 'Info.plist'):
        plist = bundle / relative
        if not plist.is_file():
            continue
        try:
            with plist.open('rb') as stream:
                name = plistlib.load(stream).get('CFBundleExecutable')
        except Exception:
            return None
        if not name:
            return None
        for candidate in (bundle / 'Contents/MacOS' / name,
                          bundle / 'Versions/Current' / name, bundle / name):
            if candidate.is_file():
                return candidate.resolve()
        return None
    return None


def _signable_items(app: Path) -> list[Path]:
    """Nested code deepest first; a bundle's main executable last among its siblings.

    codesign treats a main executable as its enclosing bundle, so signing it before an unsigned
    sibling fails. The bundle's own sign seals the main last.
    """
    bundles = [app]
    items = []
    for path in app.rglob('*'):
        if path.is_symlink():
            continue
        if path.is_dir():
            if path.suffix in _SIGNABLE_BUNDLES and (
                    path.suffix == '.framework' or any(
                        (path / relative).is_file()
                        for relative in ('Info.plist', 'Contents/Info.plist', 'Resources/Info.plist'))):
                bundles.append(path)
                items.append(path)
        elif path.is_file():
            if _is_macho(path) or (path.parent.name == 'MacOS' and _is_script(path)):
                items.append(path)
    mains = {main for main in (_bundle_main_executable(bundle) for bundle in bundles) if main}
    return sorted(items, key=lambda item: (
        -len(item.parts), item.resolve() in mains, item.as_posix()))


def sign_app(app: Path, *, nested: bool = True) -> None:
    """Sign inside-out, or reseal only the outer app after finalizing metadata."""
    app = Path(app).resolve()
    identity = _signing_identity(app)
    items = _signable_items(app) if nested else []
    for item in [*items, app]:
        restore_modes = []
        if item.is_file() and _is_macho(item):
            for path in (item, item.parent):
                if not path.resolve().is_relative_to(app):
                    continue
                info = path.stat()
                if info.st_uid == os.getuid() and not info.st_mode & stat.S_IWUSR:
                    restore_modes.append((path, stat.S_IMODE(info.st_mode)))
        try:
            for path, mode in restore_modes:
                path.chmod(mode | stat.S_IWUSR)
            result = subprocess.run(
                ['/usr/bin/codesign', '--force', '--sign', identity, '--timestamp=none', str(item)],
                capture_output=True, text=True)
            if result.returncode:
                raise RuntimeError(f'codesign failed for {item}: {result.stderr.strip()}')
        finally:
            for path, mode in reversed(restore_modes):
                path.chmod(mode)
    subprocess.run(
        ['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)],
        check=True, capture_output=True, text=True,
    )


def write_file_manifest(app: Path, destination: Path | None = None) -> dict:
    """Write sidecar ``file-manifest/`` directory for a staged app; returns its index.

    The format lives in the Hub's own ``file_manifest`` module - the same code the Hub uses to read
    it - loaded by path because `components.hub.backend` is not importable from here.
    """
    if destination is None:
        destination = app.with_name(app.name + '.file-manifest')
    if destination == app / 'Contents/Resources/file-manifest':
        raise ValueError(
            'Writing file-manifest inside the app bundle breaks code signing seals. '
            'Use external sidecar destination instead.'
        )
    import importlib.util
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location(
        'studiohub_file_manifest',
        Path(__file__).resolve().parents[1] / 'components/hub/backend/file_manifest.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.write(app, destination=destination)


def strip_runtime_tests(runtime: Path) -> dict:
    """Remove test-only files from a runtime copied into the bundle; returns what went.

    The implementation is `packaging/voice-runtime/runtime_packaging.py` - the same code the build
    uses, loaded by path because `packaging` here is not a package. A Voice runtime is a whole
    Python installation, so every wheel's `tests/` directory is in it, and 0.6.200's DMG also
    carried pytest 9.1.1 with pluggy and iniconfig: someone installed them into
    `.build/voice-runtime` after it was built, and this step copied the tree wholesale. Nothing in
    the product runs any of it. `.build` trees are reused between releases, so a build-time sweep
    alone cannot clean one that was already built - this is the one point a runtime becomes an app
    bundle, and it refuses a tree it could not clean.
    """
    import importlib.util
    path = Path(__file__).resolve().parent / 'voice-runtime' / 'runtime_packaging.py'
    spec = importlib.util.spec_from_file_location('studiohub_voice_runtime_packaging', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.strip_test_only(runtime)


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


def copy_model_manifests(destination: Path) -> None:
    """Ship the model manifests, minus any retired model (`components/retired_models.py`)."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    # Not `packaging.stage_render_tools`: the runtime's PyPI `packaging` shadows this directory.
    if str(ROOT / 'packaging') not in sys.path:
        sys.path.insert(0, str(ROOT / 'packaging'))
    from components import retired_models
    copy_tracked_tree(ROOT / 'model-manifests', destination, suffix='.json',
                      skip=lambda path: retired_models.manifest_retired(
                          json.loads(path.read_text()), path.stem))


def copy_tracked_tree(source: Path, destination: Path, suffix: str | None = None, skip=None) -> None:
    names = subprocess.check_output(['git', '-C', str(ROOT), 'ls-files', '-z', '--',
        source.relative_to(ROOT).as_posix()]).decode().split('\0')
    for name in filter(None, names):
        path = ROOT / name
        if suffix and path.suffix != suffix:
            continue
        if skip and skip(path):
            continue
        if path.is_symlink():
            raise ValueError(f'Shipped resource cannot be a symlink: {path}')
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)


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
    copy_model_manifests(resources / 'model-manifests')
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


# Chat imports the Voice cache policy by package path while Voice itself runs from
# ``Resources/voice``. Stage the same canonical file at the one additional package path; copying
# the whole Voice backend would duplicate unrelated implementation and widen the shipped surface.
CHAT_SHARED_MODULES = (Path('components/voice/app/backend/mlx_memory.py'),)


def stage_chat_shared_modules(resources: Path) -> None:
    """Copy Chat's small, explicit cross-component dependency allowlist."""
    for relative in CHAT_SHARED_MODULES:
        source = ROOT / relative
        if not source.is_file():
            raise ValueError(f'Chat shared helper is missing: {relative.as_posix()}')
        try:
            tracked = subprocess.check_output(
                ['git', '-C', str(ROOT), 'ls-files', '--error-unmatch', '--',
                 relative.as_posix()], stderr=subprocess.DEVNULL).strip()
        except subprocess.CalledProcessError as exc:
            raise ValueError(
                f'Chat shared helper must be tracked before staging: {relative.as_posix()}') from exc
        if not tracked:
            raise ValueError(
                f'Chat shared helper must be tracked before staging: {relative.as_posix()}')
        target = resources / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def stage_developer_gate_assets(resources: Path) -> None:
    """Ship the frozen gate contract, commands fixture and official-tools manifest at the paths
    their loaders resolve from `Resources/` (`parents[2]` of the staged Hub modules).

    `stop-proof.json` is the lead-verified immutable receipt copy. Until that artifact exists
    it is not staged, and the loader stays unsupported; nothing is synthesized here.
    """
    for relative in (
            'contracts/developer-gate-v1.schema.json',
            'contracts/developer-gate-v1-wire.txt',
            'packaging/developer-gates/library-portable.commands.json',
            'packaging/developer-gates/library-portable.gate.json',
            'packaging/developer-gates/library-portable.registry.json',
            'packaging/developer-gates/library-portable.dependency-manifest.json',
            'packaging/developer-gates/official-tools.json',
            'packaging/developer-gates/stop-proof.json'):
        source = ROOT / relative
        if relative.endswith('stop-proof.json') and not source.is_file():
            continue
        target = resources / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def _stage_hub(context: dict) -> dict | None:
    if not context['include_hub']:
        return None
    resources = context['resources']
    hub = resources / 'components/hub'
    copy_tracked_tree(ROOT / 'components/hub', hub)
    stage_developer_gate_assets(resources)
    # `Resources/components/*.py` — the shared modules — is already staged by `stage()`, for
    # every bundle, so there is one place that decides what the workers can import.
    copy_model_manifests(resources / 'model-manifests')
    copy_tracked_tree(ROOT / 'model-presets', resources / 'model-presets', suffix='.json')
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
    stage_chat_shared_modules(resources)
    shutil.copyfile(ROOT / 'packaging/chat_worker.py', resources / 'chat_worker.py')
    return {
        'source_sha256': tree_digest(chat_source), 'runtime': 'voice',
        'runtime_sha256': context['runtime_hash'],
    }


def _stage_embeddings(context: dict) -> dict | None:
    """The Embeddings Studio: its own process and state root on the shared Voice runtime (mlx and
    tokenizers). It imports Chat's memory policy, so it is staged after Chat."""
    if not context['include_hub']:
        return None
    resources = context['resources']
    source = resources / 'components/embeddings'
    copy_tracked_tree(ROOT / 'components/embeddings', source)
    shutil.copyfile(ROOT / 'packaging/embeddings_worker.py', resources / 'embeddings_worker.py')
    return {
        'source_sha256': tree_digest(source), 'runtime': 'voice',
        'runtime_sha256': context['runtime_hash'],
    }


def _stage_vision(context: dict) -> dict | None:
    """The Vision Studio: its own process and state root on the shared Voice runtime (mlx, numpy,
    Pillow, tokenizers). It imports Chat's memory policy, so it is staged after Chat."""
    if not context['include_hub']:
        return None
    resources = context['resources']
    source = resources / 'components/vision'
    copy_tracked_tree(ROOT / 'components/vision', source)
    shutil.copyfile(ROOT / 'packaging/vision_worker.py', resources / 'vision_worker.py')
    return {
        'source_sha256': tree_digest(source), 'runtime': 'voice',
        'runtime_sha256': context['runtime_hash'],
    }


def _stage_music(context: dict) -> dict | None:
    if not context['include_hub']:
        return None
    resources = context['resources']
    music_source = resources / 'components/music'
    copy_tracked_tree(ROOT / 'components/music', music_source)
    shutil.copyfile(ROOT / 'packaging/music_worker.py', resources / 'music_worker.py')
    # Stable Audio 3 runs in the shared Voice runtime and YuE2 in its own (`runtimes/music-yue2`),
    # so Music carries no runtime of its own: its identity is the base runtime's.
    #
    # The YuE2 tree is staged HERE, with the studio that runs it. It used to be staged inside
    # `_stage_video` (after that function's "no video runtime" early return), so a build handed
    # `--yue2-runtime` without `--video-runtime` - which is every build once Video is retired -
    # silently shipped Music with no sung-song engine. Its record moved from `components.video`
    # to `components.music`; nothing else read it.
    yue2_note = {}
    if context.get('yue2_runtime') is not None:
        yue2_found = runtime_input_digest(context['yue2_runtime'])
        yue2_reason = context.get('unqualified_yue2_runtime')
        if yue2_found != YUE2_RUNTIME_SHA256 and not yue2_reason:
            raise SystemExit(
                'The YuE2 runtime is not the one this release was qualified against.\n'
                f'  required: {YUE2_RUNTIME_SHA256}\n'
                f'  handed:   {yue2_found}\n'
                f'            {context["yue2_runtime"]}\n'
                '  rebuild it with: bash packaging/yue2-runtime/build.sh\n'
                '            Python 3.12 + mlx-Yue 9253ed133343406947bde7b67d43c7a63fb39d99 + '
                'MLX 0.32.2,\n'
                '  pass --yue2-runtime .build/yue2-runtime, or update this pin with the '
                'qualification that moved it.')
        shutil.copytree(context['yue2_runtime'], resources / 'runtimes/music-yue2',
                        symlinks=True, ignore=_ignore_runtime_metadata)
        # The bridge runs *inside* that runtime, so it is staged beside it rather than inside the
        # worker's own tree: `generation.yue2_runtime_python()` finds the interpreter, and the
        # bridge is spawned from the worker's component directory.
        bridge_source = ROOT / 'components' / 'music' / 'app' / 'backend' / 'yue2_runner.py'
        if bridge_source.is_file():
            shutil.copyfile(bridge_source, resources / 'yue2_runner.py')
        yue2_note = {'yue2_runtime_sha256': yue2_found,
                     'yue2_runtime_qualified': yue2_found == YUE2_RUNTIME_SHA256}
    return {
        'source_sha256': tree_digest(music_source), 'runtime': 'voice',
        'runtime_sha256': context['runtime_hash'], **yue2_note,
    }


def _stage_image(context: dict) -> dict | None:
    if context['image_runtime'] is None:
        return None
    found = runtime_input_digest(context['image_runtime'])
    unqualified_reason = context.get('unqualified_image_runtime')
    if found != IMAGE_RUNTIME_SHA256 and not unqualified_reason:
        known = IMAGE_RUNTIME_SUPERSEDED.get(found)
        raise SystemExit(
            'REFUSING TO STAGE: the Image runtime handed to this build is not the one the models '
            'were qualified on.\n'
            f'  handed:   {found}\n'
            f'            {context["image_runtime"]}'
            + (f'\n            -> {known}' if known else '') +
            '\n'
            f'  required: {IMAGE_RUNTIME_SHA256}\n'
            '            mflux 0.23.0, the engine the Reference-to-Image measurements ran on and '
            'the one every text-to-image model was re-measured on '
            '(qualification/evidence/image-runtime-0.23-2026-10-26.jsonl)\n'
            '  pass --image-runtime .build/image-runtime (the composite Qwen runtime), or update this pin with the '
            'qualification that moved it.'
        )
    resources = context['resources']
    image_source = resources / 'components/image'
    copy_tracked_tree(ROOT / 'components/image', image_source)
    shutil.copytree(context['image_runtime'], resources / 'runtimes/image',
                    symlinks=True, ignore=_ignore_runtime_metadata)
    shutil.copyfile(ROOT / 'packaging/image_worker.py', resources / 'image_worker.py')
    provenance = context['image_provenance']
    matched = {'runtime_qualified': True} if found == IMAGE_RUNTIME_SHA256 else {
        # A bundle may carry an unqualified runtime - a staging fixture, or a deliberate trial -
        # but it has to say so. The same rule as `development_build`: the artefact prints its own
        # status rather than being trusted to be the qualified one.
        'runtime_qualified': False,
        'runtime_note': unqualified_reason,
        'runtime_sha256_found': found,
        'runtime_sha256_qualified': IMAGE_RUNTIME_SHA256,
    }
    return {
        **matched,
        'upstream_commit': provenance['commit'],
        'upstream_version': provenance['version'],
        'source_sha256': tree_digest(image_source), 'runtime': 'image',
        'runtime_sha256': tree_digest(resources / 'runtimes/image'),
    }


def _stage_video(context: dict) -> dict | None:
    """The Video Studio: its own Python 3.11 runtime, pinned by the same content rule as Image.

    Returns None when this build does not include a video runtime, so a Voice-only or Image-only
    proof build keeps working; a build that *does* pass one gets the pin checked before anything
    is copied. **Since LTX-2.3 was retired `stage()` never passes one** (`VIDEO_MODEL_PACKAGE`), so
    today this returns None and the release has no `video` component.
    """
    if context['video_runtime'] is None:
        return None
    found = tree_digest(context['video_runtime'])
    unqualified_reason = context.get('unqualified_video_runtime')
    if found != VIDEO_RUNTIME_SHA256 and not unqualified_reason:
        raise SystemExit(
            'REFUSING TO STAGE: the Video runtime handed to this build is not the one the video '
            'model was qualified on.\n'
            f'  handed:   {found}\n'
            f'            {context["video_runtime"]}\n'
            f'  required: {VIDEO_RUNTIME_SHA256}\n'
            '            Python 3.11 + ltx-2-mlx 3b2e77fd73af5f08961f4436ae979c0579d5deed + '
            'MLX 0.32.2, the interpreter qualification/ltx-video-measure.py measured the first '
            'successful run on\n'
            '  pass --video-runtime .build/video-runtime, or update this pin with the '
            'qualification that moved it.'
        )
    resources = context['resources']
    video_source = resources / 'components/video'
    copy_tracked_tree(ROOT / 'components/video', video_source)
    shutil.copytree(context['video_runtime'], resources / 'runtimes/video', symlinks=True)
    shutil.copyfile(ROOT / 'packaging/video_worker.py', resources / 'video_worker.py')

    matched = {'runtime_qualified': True}
    if found != VIDEO_RUNTIME_SHA256:
        matched = {
            'runtime_qualified': False,
            'runtime_note': unqualified_reason,
            'runtime_sha256_found': found,
            'runtime_sha256_qualified': VIDEO_RUNTIME_SHA256,
        }
    return {
        **matched,
        # BRIDGE (revert to 'video' only once NO Mac still runs < 0.6.121): every Mac already installed
        # (0.6.119/0.6.120) verifies a staged update with ITS OWN `runtimePaths`, which has no
        # `video`, so it refuses any release that says 'video' here and can never update itself.
        # Nothing reads this label but that check (`_video_spec` reads `runtime_sha256`, and the
        # worker file is checked separately), so 'voice' lets those apps take this release; the
        # 0.6.121+ app knows `video`. Pinned by tests/test_installer_knows_shipped_runtimes.py.
        'runtime': 'voice',
        'runtime_sha256': tree_digest(resources / 'runtimes/video'),
        'source_sha256': tree_digest(video_source),
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
def _stage_khu_agent_component(context: dict) -> None:
    """The KH Updater agent kit is not a top-level manifest component yet.

    It is staged at the end of `stage()` (it needs the verified kit and the owner's publisher
    descriptor, not a studio source tree) and recorded inside `components['hub']`: the installer
    already on every Mac is 0.6.198's, whose `runtimePaths` has no `khu-agent`, and
    `verifyDeclaredComponents` refuses a manifest key it does not know **before copying a byte**
    (0.6.199 was refused exactly that way). Registered here, and in the Hub's
    `COMPONENT_WORKERS`, so both tables know the name; a later release may list it as a component
    once every installed installer can read it.
    """
    return None


COMPONENT_STAGERS: dict[str, Callable[[dict], dict | None]] = {
    'voice': _stage_voice,
    'hub': _stage_hub,
    'subtitle': _stage_subtitle,
    'chat': _stage_chat,
    'embeddings': _stage_embeddings,
    'vision': _stage_vision,
    'music': _stage_music,
    'image': _stage_image,
    'video': _stage_video,
    'khu-agent': _stage_khu_agent_component,
}


def unregistered_components(root: Path) -> list[str]:
    """Studio source trees under components/ that this build would silently not ship."""
    components = root / 'components'
    if not components.is_dir():
        return []
    return sorted(
        path.name for path in components.iterdir()
        if (path / 'app/backend/main.py').is_file() and path.name not in COMPONENT_STAGERS)


def staged_import_gaps(resources: Path) -> list[str]:
    """Python modules a staged worker imports that are not in the staged tree.

    `copy_tracked_tree` copies what `git ls-files` lists, not what is on disk. A new module in a
    component is therefore **invisible to staging until it is added to the index**, and the bundle
    builds successfully with a `main.py` importing something that is not in it - so the worker dies
    at import, the Hub refuses to start, and the failure appears only when the app is launched (or
    worse, on a drive). That happened for real: `components/music/app/backend/memory_policy.py`
    was written, staged and would have shipped missing had its author not warned in prose.

    Prose is not a guard. This walks every staged `.py` and reports the imports that resolve to
    nothing in the tree: relative imports inside a component, and `components.<...>` imports of the
    shared modules the workers add `Resources/` to the path for. Third-party imports are ignored -
    only the two shapes this build controls are checked, because a false alarm here would be a
    blocked release.
    """
    import ast

    # Only the code this stager writes: the component trees, the Voice worker, and the worker entry
    # points at the top level. `runtimes/` and `engines/` are vendored trees with tens of thousands
    # of third-party files - walking them is slow, finds imports this build does not control, and
    # one of them is not even UTF-8.
    roots = [path for path in (resources / 'components', resources / 'voice') if path.is_dir()]
    sources = [path for root in roots for path in root.rglob('*.py')]
    sources += sorted(resources.glob('*.py'))

    staged = {path.relative_to(resources).as_posix() for path in sources}
    # The roots themselves count: `components` is a namespace package, so `from components import
    # app_version` resolves against the directory even though it has no `__init__.py`.
    directories = {root.relative_to(resources).as_posix() for root in roots}
    directories |= {path.relative_to(resources).as_posix()
                    for root in roots for path in root.rglob('*') if path.is_dir()}
    problems: list[str] = []

    def resolves(parts: list[str]) -> bool:
        """A module path exists as a file, as a package, or as a namespace package directory."""
        joined = '/'.join(parts)
        return (f'{joined}.py' in staged or f'{joined}/__init__.py' in staged
                or joined in directories)

    for path in sources:
        relative = path.relative_to(resources)
        package = list(relative.parts[:-1])
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        except (SyntaxError, UnicodeDecodeError) as error:
            problems.append(f'{relative}: cannot be read as Python ({error})')
            continue
        for node in ast.walk(tree):
            targets: list[list[str]] = []
            if isinstance(node, ast.ImportFrom):
                if node.level:
                    base = package[:len(package) - (node.level - 1)] if node.level > 1 else package
                    if node.module:
                        # `from .memory_policy import status` - the module is the dependency.
                        targets.append(base + node.module.split('.'))
                    else:
                        # `from . import memory_policy` - the names are the submodules.
                        targets.extend(base + [alias.name] for alias in node.names)
                elif node.module == 'components':
                    # `from components import app_version` - `components` is a namespace of
                    # modules, so here the names really are modules. This is the shape the shared
                    # modules are imported with, and the one that broke an image bundle fixture.
                    targets.append(['components'])
                    targets.extend(['components', alias.name] for alias in node.names)
                elif node.module and node.module.startswith('components.'):
                    # Deeper, the names are symbols (`from components.chat...llm_engine import
                    # ChatEngine`), so only the module itself has to exist.
                    targets.append(node.module.split('.'))
            elif isinstance(node, ast.Import):
                # `import components.model_packages` - always a module path.
                for alias in node.names:
                    if alias.name == 'components' or alias.name.startswith('components.'):
                        targets.append(alias.name.split('.'))
            for target in targets:
                if target and not resolves(target):
                    problems.append(
                        f'{relative}: imports {"/".join(target)}, which is not in the bundle - '
                        f'if it is a new file, `git add` it before staging')
    return sorted(set(problems))


def archive_failed_stage(output: Path) -> Path:
    """Keep a failed fresh bundle and its diagnostics under a unique project archive."""
    archive = ROOT / '_trash' / 'failed-stages'
    # An emptied Desktop trash leaves `_trash` dangling; recreate its target rather than fail (2026-11-16).
    archive.resolve().mkdir(parents=True, exist_ok=True)
    destination = Path(tempfile.mkdtemp(prefix='stage-', dir=archive)) / output.name
    shutil.move(str(output), str(destination))
    return destination


def stage_hub_render_authority(
    resources: Path, render_authority: RenderAuthorityMode, render_pins: tuple,
) -> None:
    """Stage the pinned render tools and every closed authority descriptor into a Hub bundle."""
    from stage_render_tools import (
        CUE_OVERLAY_AUTHORITY_PINS,
        LATE_LABEL_AUTHORITY_PINS,
        PRODUCTION_AUTHORITY_PINS,
        SUBTITLE_AUTHORITY_PINS,
        _stage_render_authority_into,
        _stage_render_tools_into,
    )
    scratch = Path(tempfile.mkdtemp(
        prefix='.studiohub-render-tools-', dir=resources.parent))
    scratch.chmod(0o700)
    try:
        _stage_render_tools_into(resources, scratch, render_pins)
        _stage_render_authority_into(
            resources, scratch, render_authority, PRODUCTION_AUTHORITY_PINS)
        if render_authority == 'tool-only':
            return
        _stage_render_authority_into(
            resources, scratch, 'descriptor-only', LATE_LABEL_AUTHORITY_PINS,
            late_labels=True)
        # None until the v4 canonical triple is frozen: no subtitle directory then, so the
        # installed bundle reports the capability as absent rather than unverified.
        if SUBTITLE_AUTHORITY_PINS is not None:
            _stage_render_authority_into(
                resources, scratch, 'descriptor-only', SUBTITLE_AUTHORITY_PINS,
                subtitles=True)
        _stage_render_authority_into(
            resources, scratch, 'descriptor-only', CUE_OVERLAY_AUTHORITY_PINS,
            cue_overlays=True)
    finally:
        # The manifest and descriptors were hard-linked out of scratch; keeping it would leave a
        # dot-directory in the output folder whose links hold the same ~264 MB alive.
        shutil.rmtree(scratch, ignore_errors=True)


def stage_hub_helper(resources: Path, native_helper: Path) -> None:
    """Stage the background helper as `Hub Studio KH Helper.app` beside the runtimes.

    Every user-visible background name is `<App Name> Helper` (owner, 2026-11-11), so Activity
    Monitor, App Background Activity and notifications show "Hub Studio KH Helper" instead of
    `python3`. The launchd labels and bundle identifiers stay as they are: they are internal.
    """
    bundle = resources / HELPER_BUNDLE
    macos = bundle / 'Contents' / 'MacOS'
    helper_resources = bundle / 'Contents' / 'Resources'
    macos.mkdir(parents=True)
    helper_resources.mkdir(parents=True)
    executable = macos / 'Hub Studio KH Helper'
    shutil.copyfile(native_helper, executable)
    executable.chmod(0o755)
    shutil.copyfile(ROOT / 'assets' / 'app-icon.icns', helper_resources / 'app-icon.icns')
    plist = {
        'CFBundleExecutable': 'Hub Studio KH Helper',
        'CFBundleName': 'Hub Studio KH Helper',
        'CFBundleDisplayName': 'Hub Studio KH Helper',
        'CFBundleIdentifier': 'local.studiohub.helper',
        'CFBundlePackageType': 'APPL',
        'CFBundleShortVersionString': '1.0',
        'CFBundleVersion': '1',
        'CFBundleIconFile': 'app-icon',
        'LSMinimumSystemVersion': '14.0',
        # No Dock icon and no menu bar: the helper exists only to own the worker process.
        'LSUIElement': True,
    }
    with (bundle / 'Contents' / 'Info.plist').open('wb') as stream:
        plistlib.dump(plist, stream)


def stage(runtime: Path, output: Path, native_shell: Path | None = None, *,
          native_helper: Path | None = None,
          include_hub: bool = False, image_runtime: Path | None = None,
          unqualified_image_runtime: str | None = None,
          video_runtime: Path | None = None, unqualified_video_runtime: str | None = None,
          yue2_runtime: Path | None = None, unqualified_yue2_runtime: str | None = None,
          render_authority: RenderAuthorityMode = 'tool-only',
          render_tools_dir: Path | None = None,
          khu_agent_kit: Path | None = None,
          khu_publisher_json: Path | None = None) -> Path:
    if render_authority not in RENDER_AUTHORITY_MODES:
        raise ValueError(f'unsupported render authority mode: {render_authority}')
    if render_authority != 'tool-only':
        include_hub = True
    output = output.expanduser().absolute()
    if output.exists():
        raise FileExistsError(f'Refusing to replace existing artifact: {output}')
    validate_runtime(runtime)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    # Not `packaging.stage_render_tools`: the runtime's PyPI `packaging` shadows this directory.
    if str(ROOT / 'packaging') not in sys.path:
        sys.path.insert(0, str(ROOT / 'packaging'))
    from components import retired_models
    if video_runtime is not None and retired_models.is_retired(VIDEO_MODEL_PACKAGE):
        print(f'NOTE: {VIDEO_MODEL_PACKAGE} is retired, so --video-runtime is ignored: this release '
              'ships no Video component.', file=sys.stderr)
        video_runtime = None
    if image_runtime is not None:
        image_runtime = image_runtime.expanduser().resolve()
        validate_runtime(image_runtime)
        include_hub = True
    if video_runtime is not None:
        video_runtime = video_runtime.expanduser().resolve()
        validate_runtime(video_runtime)
        include_hub = True
    if yue2_runtime is not None:
        yue2_runtime = yue2_runtime.expanduser().resolve()
        validate_runtime(yue2_runtime)
        include_hub = True
    if native_shell is not None:
        native_shell = native_shell.expanduser().resolve()
        validate_native_shell(native_shell)
        include_hub = True
        if native_helper is None:
            raise ValueError(
                'A Hub build needs --native-helper: .build/HubStudioHelper, the launcher staged '
                'as Hub Studio KH Helper.app. Without it the app falls back to a python3 process '
                'name and the release is missing an artifact HUB_RELEASE_ARTIFACTS declares.')
        native_helper = native_helper.expanduser().resolve()
        validate_native_shell(native_helper)
    provenance = json.loads((ROOT / 'research/voice-adoption.json').read_text())
    hub_provenance = None
    if include_hub:
        if render_tools_dir is None:
            raise ValueError('A Hub build needs --render-tools-dir: the directory holding the pinned '
                             'universal ffmpeg and ffprobe (nothing is looked up by default)')
        from stage_render_tools import production_pins
        render_pins = production_pins(render_tools_dir)  # fail now, not after the long stage
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
    if video_runtime is not None:
        if not (ROOT / 'components/video/app/backend/main.py').is_file():
            raise ValueError('Bundled Video entry point is missing')
        if not subprocess.check_output(['git', '-C', str(ROOT), 'ls-files', '--',
                                        'components/video/app/backend/main.py']).strip():
            raise ValueError('Video source must be added to Git before staging the bundle')
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
    # The menu-bar mark: a template image the native shell loads by name (`menubar-icon.png`, plus
    # `@2x`). Same generator, same rule - a build that lacks them fails here rather than showing the
    # fallback symbol in the owner's menu bar.
    for name in ('menubar-icon.png', 'menubar-icon@2x.png'):
        mark = ROOT / 'assets' / name
        if not mark.is_file():
            raise FileNotFoundError(
                f"the menu-bar icon is missing: {mark.relative_to(ROOT)}. "
                "Run packaging/make_app_icon.py and commit the result.")
        shutil.copyfile(mark, resources / name)
    # Keep the app's existing resource name; CHANGELOG.md is the only authored release text.
    (resources / 'release-notes.md').write_text(
        release_notes_text((ROOT / 'CHANGELOG.md').read_text(encoding='utf-8'), version), encoding='utf-8')
    shutil.copytree(runtime, resources / 'runtimes/voice',
                    symlinks=True, ignore=_ignore_runtime_metadata)
    # Nothing in the product runs a dependency's tests or pytest, and both were in 0.6.200's
    # bundle. Removed here, at the staging step, so a reused `.build/voice-runtime` cannot smuggle
    # them in; the runtime's own `manifest/runtime-files.json` is rewritten to match what ships.
    stripped = strip_runtime_tests(resources / 'runtimes/voice')
    if stripped['removed']:
        print(f"Note: voice runtime ships without {len(stripped['removed'])} test-only files "
              f"({stripped['bytes'] / (1024 * 1024):.1f} MB): wheel tests/, conftest.py and pytest")
    # The shared private-worker lifecycle every wrapper imports; not a component of its own.
    shutil.copyfile(ROOT / 'packaging/voice_worker.py', resources / 'voice_worker.py')
    # The top-level modules under `components/` that workers import by package path. Staged for
    # every bundle, Hub or Voice-alone, because the Voice worker imports one of them too now
    # (its memory policy reads the shared mode table and `auto` rule from
    # `components/memory_modes.py`), and a Voice-only artifact without `Resources/components/`
    # would fail at import rather than at build time.
    (resources / 'components').mkdir(parents=True, exist_ok=True)
    for module in shared_components_modules(ROOT / 'components'):
        shutil.copyfile(module, resources / 'components' / module.name)
    runtime_hash = tree_digest(resources / 'runtimes/voice')
    context = {
        'resources': resources, 'runtime_hash': runtime_hash, 'include_hub': include_hub,
        'image_runtime': image_runtime,
        'unqualified_image_runtime': unqualified_image_runtime,
        'video_runtime': video_runtime,
        'unqualified_video_runtime': unqualified_video_runtime,
        'yue2_runtime': yue2_runtime,
        'unqualified_yue2_runtime': unqualified_yue2_runtime,
        'provenance': provenance,
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
    # A Hub release must carry what `HUB_RELEASE_ARTIFACTS` declares. 0.6.199 was staged with
    # neither `--image-runtime` nor `--yue2-runtime`, so both stagers returned None, the manifest
    # was consistent with the (smaller) bundle, and the build reported success over an app whose
    # Image and sung-song Music could not start. Refuse here, before hashing, so no installer can
    # be built from it.
    if include_hub:
        if native_helper is not None:
            stage_hub_helper(resources, native_helper)
        missing = missing_release_artifacts(resources)
        if missing:
            saved = archive_failed_stage(output)
            raise ValueError(
                'The staged Hub app is missing artifacts this release declares, so an installer '
                'built from it would install a Hub whose runtimes or workers cannot start. '
                'Missing: ' + ', '.join(missing)
                + '. Stage them with --image-runtime and --yue2-runtime (the qualified trees named '
                  'by the release loop in docs/tasks/HANDOFF.md); a deliberate narrower release '
                  'edits HUB_RELEASE_ARTIFACTS in packaging/stage_voice.py. '
                  f'The failed bundle is preserved at {saved}.')
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
        native_executable = contents / 'MacOS/Hub Studio KH'
        shutil.copyfile(native_shell, native_executable)
        native_executable.chmod(0o755)
        plist.update({
            'CFBundleExecutable': 'Hub Studio KH',
            'CFBundleIdentifier': 'local.studiohub.standalone',
            # The app is "Hub Studio KH" everywhere an operator can see it: bundle filename,
            # Finder, Dock, About, window title and this short name (owner, 2026-11-12; it
            # used to ship as `Studio Hub.app` with only the display name changed). The bundle
            # identifier and the Application Support folder stay as they are, because they are
            # invisible and moving them would break update continuity and orphan the data.
            'CFBundleName': 'Hub Studio KH',
            'CFBundleDisplayName': 'Hub Studio KH',
            'NSMicrophoneUsageDescription':
                'Hub Studio KH uses the microphone to record audio for transcription.',
            # macOS gates local-network access per app, separately from App Transport Security,
            # and an app that declares nothing here is refused rather than prompted - which is
            # what a node hit when it tried to reach its controller. Granting the prompt puts
            # this app in System Settings > Privacy & Security > Local Network.
            'NSLocalNetworkUsageDescription':
                'Hub Studio KH talks to your other Macs on this network to send and receive work.',
            # A login launch opens no window, and AppKit may automatically terminate a
            # windowless app after idle (observed 2026-11-15, ~8 minutes). The opt-out counter
            # only has effect when support is enabled, so declare support here and call
            # `disableAutomaticTermination` at launch; declaring support off made the opt-out
            # a no-op (observed 2026-11-15).
            'NSSupportsAutomaticTermination': True,
        })
        plist.pop('LSUIElement')
    with (contents / 'Info.plist').open('wb') as stream:
        plistlib.dump(plist, stream)
    # Before anything is hashed or recorded: a worker that cannot import is not a bundle. This is
    # the check that turns `git add` from a thing someone remembers into a thing the build does.
    gaps = staged_import_gaps(resources)
    if gaps:
        saved = archive_failed_stage(output)
        raise ValueError(
            'The staged bundle is missing modules its own code imports, so a worker would die at '
            f'startup. Failed bundle preserved at {saved}. Add the new files to git and stage again:\n  ' + '\n  '.join(gaps))
    if include_hub:
        stage_hub_render_authority(resources, render_authority, render_pins)
    # The KH Updater agent kit travels inside the Hub. It is staged only when both halves are
    # handed in - the credential-free kit and the owner's publisher descriptor - because a
    # bundle that carried one without the other could not enrol, and the descriptor is the
    # pinned trust anchor. The staging is recorded on the hub component, so a release that
    # shipped without one says so in its own manifest (and one that shipped one says that too).
    staged_kit = None
    if include_hub and khu_agent_kit is not None and khu_publisher_json is not None:
        from stage_khu_agent import finalize_agent_kit, stage_agent_kit
        staged_kit = stage_agent_kit(resources, khu_agent_kit, khu_publisher_json)
        # **Not a top-level component.** A manifest key becomes a runtime the *installed* installer
        # must know (`native/UpdateClient.swift` `verifyDeclaredComponents`), and every Mac in the
        # fleet runs the 0.6.198 installer, whose table predates the agent. 0.6.199 listed
        # `khu-agent` there, and every 0.6.198 Mac refused the release at manifest validation,
        # before downloading anything: "lists component 'khu-agent' with runtime 'khu-agent',
        # which this installer does not know". Adding the name to 0.6.200's own Swift cannot help -
        # the old binary is what validates. The kit therefore rides inside the `hub` component's
        # record, which old installers already accept, and `Resources/khu-agent/` is unchanged.
    elif include_hub:
        print('NOTE: this Hub build ships no KH Updater agent kit; pass --khu-agent-kit '
              'and --khu-publisher-json to include one.', file=sys.stderr)
    # Developer direction 2026-11-07: payload_sha256 is the POST-SIGN payload digest. The
    # bundle is signed inside-out first; the digest is taken over the signed Contents excluding
    # release-manifest.json (it cannot hash itself) and _CodeSignature (the reseal below
    # regenerates it). Nested code is signed once; only the outer app is resealed after
    # the final kit checksums and release manifest are written.
    # codesign seals release-manifest.json into the principal's CodeDirectory, so the re-seal
    # always rewrites those Mach-O bytes; payload_digest zeroes embedded LC_CODE_SIGNATURE
    # blobs for hashing only (codesign --deep --strict and .tree-sha256 still cover them),
    # which is what makes a recorded payload comparable to the final recomputation at all.
    sign_app(output)
    if staged_kit is not None:
        finalize_agent_kit(staged_kit)
        components['hub']['khu_agent'] = {
            'agent_version': staged_kit.agent_version,
            'kit_manifest_sha256': hashlib.sha256(
                staged_kit.manifest_path.read_bytes()).hexdigest(),
        }
        signed_kit_digest = tree_digest(staged_kit.staged_path)
    release['payload_sha256'] = payload_digest(
        contents, exclude={Path('Resources/release-manifest.json'), Path('_CodeSignature')})
    (resources / 'release-manifest.json').write_text(json.dumps(release, indent=2) + '\n')
    sign_app(output, nested=False)
    if staged_kit is not None and tree_digest(staged_kit.staged_path) != signed_kit_digest:
        raise ValueError('outer app reseal changed the finalized agent kit')
    # Stability of the recorded digest across the re-seal is asserted, never assumed: any
    # payload-byte churn outside the signature blobs stops the stage loudly here.
    final_payload = payload_digest(
        contents, exclude={Path('Resources/release-manifest.json'), Path('_CodeSignature')})
    if final_payload != release['payload_sha256']:
        raise ValueError(
            're-sign changed payload bytes: payload_sha256 recorded '
            f'{release["payload_sha256"]} but the sealed bundle now hashes {final_payload}')
    # The per-file manifest that lets a Mac fetch only the files that changed. Written as a
    # sidecar directory outside the app bundle AFTER code signing so the seal covers native
    # binaries and CodeResources without being invalidated.
    write_file_manifest(output)
    output.with_name(output.name + '.tree-sha256').write_text(tree_digest(output) + '\n')
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--native-shell', type=Path)
    parser.add_argument('--native-helper', type=Path,
                        help='.build/HubStudioHelper, staged as Hub Studio KH Helper.app; '
                             'required for a Hub build with a native shell')
    parser.add_argument('--image-runtime', type=Path, help='Include the independently qualified Image runtime and worker')
    # Staging a runtime that is NOT the qualified one is allowed only by saying why. The reason is
    # recorded in the release manifest, so an unqualified bundle announces itself.
    parser.add_argument('--unqualified-image-runtime', metavar='REASON',
                        help='Stage a non-qualified Image runtime anyway, recording this reason')
    parser.add_argument('--video-runtime', type=Path,
                        help='Include the pinned Python 3.11 LTX Video runtime and worker')
    parser.add_argument('--yue2-runtime', type=Path,
                        help='Include the pinned Python 3.12 YuE2 runtime and bridge')
    parser.add_argument('--unqualified-yue2-runtime', metavar='REASON',
                        help='Stage a non-qualified YuE2 runtime anyway, recording this reason')
    parser.add_argument('--unqualified-video-runtime', metavar='REASON',
                        help='Stage a non-qualified Video runtime anyway, recording this reason')
    parser.add_argument('--hub', action='store_true', help='Include the local Hub service (always enabled for native app builds)')
    parser.add_argument('--render-authority', choices=RENDER_AUTHORITY_MODES,
                        default='tool-only')
    parser.add_argument('--render-tools-dir', type=Path,
                        help='Absolute directory holding the pinned universal ffmpeg and ffprobe; '
                             'required for any Hub build (they are verified by size and sha256)')
    parser.add_argument('--khu-agent-kit', type=Path,
                        help='Credential-free agent kit from kh-updater '
                             'packaging/build-agent-kit.sh; staged with --khu-publisher-json')
    parser.add_argument('--khu-publisher-json', type=Path,
                        help="The owner's ~/Library/KHUpdater/publisher.json: the feed and "
                             'enrolment URLs and the pinned public key. Required with --khu-agent-kit')
    args = parser.parse_args()
    print(stage(args.runtime, args.output, native_shell=args.native_shell,
                native_helper=args.native_helper,
                include_hub=args.hub, image_runtime=args.image_runtime,
                unqualified_image_runtime=args.unqualified_image_runtime,
                video_runtime=args.video_runtime,
        yue2_runtime=args.yue2_runtime,
        unqualified_yue2_runtime=args.unqualified_yue2_runtime,
                unqualified_video_runtime=args.unqualified_video_runtime,
                render_authority=args.render_authority,
                render_tools_dir=args.render_tools_dir,
                khu_agent_kit=args.khu_agent_kit,
                khu_publisher_json=args.khu_publisher_json))
