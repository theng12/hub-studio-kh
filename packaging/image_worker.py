"""Private bundle-relative Image entry point; run with immutable Python -I -B."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

RESOURCES = Path(__file__).resolve().parent
sys.path.insert(0, str(RESOURCES))
from voice_worker import read_private_token, run_server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--models-dir', type=Path, required=True)
    parser.add_argument('--ready-file', type=Path, required=True)
    parser.add_argument('--hub-token-file', type=Path, required=True)
    parser.add_argument('--parent-pid', type=int)
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error('Port must be between 0 and 65535')
    resources = RESOURCES
    bundle = resources.parent.parent
    data = args.data_dir.expanduser().resolve()
    models = args.models_dir.expanduser().resolve()
    ready = args.ready_file.expanduser().absolute()
    release_path = resources / 'release-manifest.json'
    source = resources / 'components' / 'image' / 'app'
    runtime = resources / 'runtimes' / 'image'
    manifest_path = resources / 'model-manifests' / 'flux2-klein-4b-mlx-4bit.json'
    if not models.is_dir():
        parser.error('Selected model library is unavailable; reconnect its volume')
    if any(path == bundle or path.is_relative_to(bundle) for path in (data, models)):
        parser.error('Image state and selected models must be outside the app bundle')
    if ready.is_symlink() or not ready.resolve().is_relative_to(data):
        parser.error('Ready file must stay inside external app data')
    if not (release_path.is_file() and (source / 'backend/main.py').is_file()
            and manifest_path.is_file() and (runtime / 'bin/python3').is_file()):
        parser.error('Incomplete Image bundle: release, source, model catalog or runtime is missing')
    try:
        release = json.loads(release_path.read_text())
        image = release['components']['image']
        identity = image['runtime_sha256']
        if (release['schema'] != 'studiohub.standalone-release'
                or release['schema_version'] != 1 or not isinstance(identity, str)
                or len(identity) != 64 or any(ch not in '0123456789abcdef' for ch in identity)):
            raise ValueError('Release manifest does not identify an exact Image runtime')
    except (OSError, KeyError, TypeError, ValueError) as exc:
        parser.error(f'Invalid Image release identity: {exc}')
    if args.parent_pid is not None:
        if args.parent_pid <= 1 or args.parent_pid != os.getppid():
            parser.error('Parent PID must identify the worker actual owner')
        if os.getpgrp() != os.getpid():
            os.setsid()
        pgid = os.getpgrp()
        if pgid != os.getpid():
            parser.error('Private Image process group must be isolated')
    else:
        pgid = None
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    image_data = data / 'image'
    if image_data.is_symlink():
        parser.error('Image data cannot be a symlink')
    image_data.mkdir(exist_ok=True, mode=0o700)
    if image_data.stat().st_uid != os.geteuid() or image_data.stat().st_mode & 0o077:
        parser.error('Image state must be owner-only')
    try:
        token = read_private_token(args.hub_token_file, data)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    for key in list(os.environ):
        if key.startswith(('PYTHON', 'HF_', 'HUGGINGFACE_', 'STUDIOHUB_',
                           'KH_STUDIO_', 'PINOKIO_', 'DYLD_')):
            os.environ.pop(key, None)
    os.environ.update({
        'STUDIOHUB_IMAGE_DATA_DIR': str(image_data),
        'STUDIOHUB_MODELS_DIR': str(models),
        'STUDIOHUB_IMAGE_MANIFEST': str(manifest_path),
        'STUDIOHUB_RELEASE_MANIFEST': str(release_path),
        'STUDIOHUB_FLEET_TOKEN': token,
        'HF_HUB_DISABLE_TELEMETRY': '1', 'PYTHONNOUSERSITE': '1',
        'PYTHONDONTWRITEBYTECODE': '1',
        'HF_HOME': str(image_data / 'cache'),
        'XDG_CACHE_HOME': str(image_data / 'cache'),
        'MPLCONFIGDIR': str(image_data / 'cache' / 'matplotlib'),
        'PATH': str(runtime / 'bin') + ':/usr/bin:/bin:/usr/sbin:/sbin',
    })
    # Only when the owner asked for it. This used to be forced on, so switching Offline mode off
    # in the app changed nothing here - the setting looked like it worked and did not.
    if args.offline:
        os.environ['HF_HUB_OFFLINE'] = '1'
        os.environ['TRANSFORMERS_OFFLINE'] = '1'
    sys.dont_write_bytecode = True
    if args.check:
        print(json.dumps({'component': 'image', 'version': release['version'],
                          'source': str(source), 'runtime_sha256': identity,
                          'data': str(image_data), 'models': str(models),
                          'offline': bool(args.offline), 'managed_updates': False}))
        return
    os.chdir(image_data)
    run_server('components.image.app.backend.main:from_environment', args.port,
               ready, str(release['version']), args.parent_pid, pgid, factory=True)


if __name__ == '__main__':
    main()
