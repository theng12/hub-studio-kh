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
    """Keep the existing unsigned bundle/delta format; verify every staged byte and link."""
    spec = importlib.util.spec_from_file_location('dmg_stage_voice', ROOT / 'packaging/stage_voice.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    expected = app.with_name(app.name + '.tree-sha256').read_text().strip()
    if module.tree_digest(app) != expected:
        raise ValueError('staged app content differs from its recorded tree digest')


def build(app: Path, output: Path) -> None:
    app, output = app.expanduser().resolve(), output.expanduser().absolute()
    if not (app / 'Contents/Info.plist').is_file():
        raise ValueError('source must be a staged .app bundle')
    if output.parent.resolve() != (ROOT / 'dist').resolve() or output.suffix != '.dmg':
        raise ValueError('DMG output must be in this project dist folder')
    validate_stage(app)
    output.parent.mkdir(parents=True, exist_ok=True)
    for old in sorted(output.parent.glob('*.dmg')):
        print('Previous installer preserved:', preserve(old, ROOT, 'dist'))
    scratch_parent = ROOT / '.build'
    scratch_parent.mkdir(exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix='dmg-stage-', dir=scratch_parent))
    complete = False
    try:
        subprocess.run(['/bin/cp', '-cRp', str(app), str(scratch / 'Studio Hub.app')], check=True)
        (scratch / 'Applications').symlink_to('/Applications', target_is_directory=True)
        subprocess.run(['hdiutil', 'create', '-volname', 'Studio Hub', '-srcfolder', str(scratch),
                        '-format', 'UDZO', str(output)], check=True)
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
