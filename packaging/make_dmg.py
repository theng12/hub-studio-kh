#!/usr/bin/env python3
"""Build an installable DMG, preserving replaced artifacts and staging files."""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile
import importlib.util


ROOT = Path(__file__).resolve().parents[1]


def preserve(path: Path, project: Path, bucket: str) -> Path | None:
    path, project = Path(path).absolute(), Path(project).absolute()
    archive = project / '_trash'
    if not path.exists() and not path.is_symlink():
        return None
    if Path(bucket).name != bucket:
        raise ValueError('archive bucket must be one folder name')
    if path == project or path == archive or archive in path.parents:
        raise ValueError('refusing to move project/archive contents')
    canonical_archive = archive.resolve()
    parent = path.parent.resolve()
    if parent == canonical_archive or canonical_archive in parent.parents:
        raise ValueError('refusing to move an archive through an alias')
    # The owner may empty the Desktop trash at any time, which leaves `_trash` a dangling link: create
    # the folder it points at instead of dying on ENOENT (2026-11-16 - it broke a DMG build).
    archive.resolve().mkdir(parents=True, exist_ok=True)
    destination_root = archive / bucket
    destination_root.mkdir(parents=True, exist_ok=True)
    item = Path(tempfile.mkdtemp(prefix='item-', dir=destination_root))
    try:
        relative = path.relative_to(project)
    except ValueError:
        relative = Path('external') / path.as_posix().lstrip('/')
    destination = item / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(destination))
    return destination


def validate_stage(app: Path) -> None:
    """Verify staged .app code signature, recorded tree digest, and sidecar manifest closure."""
    app = Path(app).resolve()
    if not (app / 'Contents/Info.plist').is_file():
        raise ValueError('source must be a staged .app bundle')
    if (app / 'Contents/Resources/file-manifest').exists():
        raise ValueError(
            'app bundle contains legacy embedded file-manifest; manifest must be outside '
            'sealed bundle as a sidecar directory'
        )

    # 1. Verify recorded tree digest
    tree_file = app.with_name(app.name + '.tree-sha256')
    if not tree_file.is_file():
        raise ValueError(f'missing tree-sha256 file: {tree_file}')
    expected = tree_file.read_text().strip()

    spec = importlib.util.spec_from_file_location('dmg_stage_voice', ROOT / 'packaging/stage_voice.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if module.tree_digest(app) != expected:
        raise ValueError('staged app content differs from its recorded tree digest')

    # 2. Verify app code seal
    verify = subprocess.run(
        ['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)],
        capture_output=True, text=True,
    )
    if verify.returncode != 0:
        raise ValueError(f'staged app code signature verification failed: {verify.stderr.strip()}')

    # 3. Verify sidecar manifest closure
    manifest_dir = app.with_name(app.name + '.file-manifest')
    if not manifest_dir.is_dir():
        manifest_dir = app.parent / 'file-manifest'
    if not manifest_dir.is_dir():
        raise ValueError(f'staged app has no sidecar file-manifest directory: {manifest_dir}')

    fm_spec = importlib.util.spec_from_file_location(
        'studiohub_file_manifest',
        ROOT / 'components/hub/backend/file_manifest.py',
    )
    fm = importlib.util.module_from_spec(fm_spec)
    fm_spec.loader.exec_module(fm)

    try:
        index, groups = fm.load_dir(manifest_dir)
    except Exception as exc:
        raise ValueError(f'invalid sidecar file-manifest: {exc}') from exc

    scanned = fm.scan(app)
    flattened = fm.flatten(groups)
    if scanned != flattened:
        raise ValueError('staged app content differs from its sidecar manifest closure')


def build(app: Path, output: Path) -> None:
    app, output = app.expanduser().resolve(), output.expanduser().absolute()
    if not (app / 'Contents/Info.plist').is_file():
        raise ValueError('source must be a staged .app bundle')
    dist = ROOT / 'dist'
    candidates = dist / '.candidates'
    if output.parent.resolve() not in (dist.resolve(), candidates.resolve()) or output.suffix != '.dmg':
        raise ValueError('DMG output must be in this project dist or dist/.candidates folder')
    if dist.is_symlink() or candidates.is_symlink():
        raise ValueError('DMG staging folders must not be symlinks')
    if output.parent.resolve() == dist.resolve() and (dist / '.release-dist.json').exists():
        raise ValueError('accepted release dist is managed; stage candidates in dist/.candidates')
    validate_stage(app)
    manifest_dir = app.with_name(app.name + '.file-manifest')
    if not manifest_dir.is_dir():
        manifest_dir = app.parent / 'file-manifest'
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() or output.is_symlink():
        print('Previous installer preserved:', preserve(output, ROOT, 'dist'))
    scratch_parent = ROOT / '.build'
    scratch_parent.mkdir(exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix='dmg-stage-', dir=scratch_parent))
    complete = False
    try:
        # The bundle keeps the name it was staged with ("Hub Studio KH.app").
        subprocess.run(['/bin/cp', '-cRp', str(app), str(scratch / app.name)], check=True)
        subprocess.run(['/bin/cp', '-cRp', str(manifest_dir), str(scratch / 'file-manifest')], check=True)
        # hdiutil's image copier and another Mac must read owner-only auxiliary runtime tools.
        # Content/link identity and the manifest's executable flags remain unchanged.
        subprocess.run(['/bin/chmod', '-R', 'a+rX', str(scratch / app.name)], check=True)
        subprocess.run(['/bin/chmod', '-R', 'a+rX', str(scratch / 'file-manifest')], check=True)
        (scratch / 'Applications').symlink_to('/Applications', target_is_directory=True)
        subprocess.run(['diskutil', 'image', 'create', 'from', '--volumeName', 'Hub Studio KH',
                        '--format', 'UDZO', str(scratch), str(output)], check=True)
        subprocess.run(['hdiutil', 'verify', str(output)], check=True)
        complete = True
    finally:
        print('DMG staging preserved:', preserve(scratch, ROOT, 'dmg-scratch'))
        if not complete and output.exists():
            print('Incomplete installer preserved:', preserve(output, ROOT, 'dist'))
    print('Installer:', output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('app', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    build(args.app, args.output)


if __name__ == '__main__':
    main()
