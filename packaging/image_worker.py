"""Private bundle-relative Image entry point; run with immutable Python -I -B."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys

RESOURCES = Path(__file__).resolve().parent
sys.path.insert(0, str(RESOURCES))
from voice_worker import read_private_token, run_server


def qualification_package(resources: Path, data: Path, models: Path,
                          parent_pid: int | None, package_id: str | None) -> str | None:
    """Validate the launch-only permission for one unqualified package.

    The backend never accepts this permission over HTTP. The packaged launcher grants it only
    to a parent-owned worker whose private state and copy-on-write library are siblings below the
    qualification runner's ``_qualification-private`` root.
    """
    if package_id is None:
        return None
    if parent_pid is None:
        raise ValueError('Qualification requires an owning parent process')
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]*', package_id):
        raise ValueError('Qualification package id is invalid')
    private = data.parent
    if models.parent != private or private.parent.name != '_qualification-private':
        raise ValueError('Qualification state and library must share one private qualification root')
    if not (models / package_id).is_dir():
        raise ValueError(f'Qualification package {package_id} is absent from the private library')
    manifest_path = resources / 'model-manifests' / f'{package_id}.json'
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, ValueError) as exc:
        raise ValueError(f'Qualification manifest is unavailable: {exc}') from exc
    if manifest.get('package_id') != package_id:
        raise ValueError('Qualification manifest package id does not match')
    if manifest.get('production_allowed') is not False:
        raise ValueError(f'Qualification package {package_id} is already allowed for production')
    return package_id


_NODE_ID = re.compile(r'[a-z0-9][a-z0-9._-]{0,99}')


def parse_destination_node_id(value: str | None) -> str | None:
    """The launcher's narrow internal identity argument, validated once.

    Same shape as the control plane's own ID_PATTERN; absent stays absent (unknown identity),
    never an empty string that could read as a real node.
    """
    if value is None:
        return None
    value = value.strip()
    if not _NODE_ID.fullmatch(value):
        raise ValueError('destination node id is not a valid fleet identity')
    return value


def worker_environment(*, image_data: Path, models: Path, manifest_path: Path,
                       release_path: Path, token: str, runtime: Path,
                       destination_node_id: str | None = None) -> dict[str, str]:
    """The deliberate, sanitised environment the Image runtime gets."""
    environment = {
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
    }
    if destination_node_id is not None:
        environment['STUDIOHUB_DESTINATION_NODE_ID'] = destination_node_id
    return environment


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--models-dir', type=Path, required=True)
    parser.add_argument('--ready-file', type=Path, required=True)
    parser.add_argument('--hub-token-file', type=Path, required=True)
    parser.add_argument('--parent-pid', type=int)
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--qualification-package')
    parser.add_argument('--destination-node-id')
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
    try:
        qualification = qualification_package(
            resources, data, models, args.parent_pid, args.qualification_package)
    except ValueError as exc:
        parser.error(str(exc))
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
    try:
        destination = parse_destination_node_id(args.destination_node_id)
    except ValueError as exc:
        parser.error(str(exc))
    os.environ.update(worker_environment(
        image_data=image_data, models=models, manifest_path=manifest_path,
        release_path=release_path, token=token, runtime=runtime,
        destination_node_id=destination))
    if qualification is not None:
        os.environ['STUDIOHUB_IMAGE_QUALIFICATION_PACKAGE'] = qualification
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
                          'offline': bool(args.offline),
                          'qualification_package': qualification,
                          'managed_updates': False}))
        return
    os.chdir(image_data)
    run_server('components.image.app.backend.main:from_environment', args.port,
               ready, str(release['version']), args.parent_pid, pgid, factory=True)


if __name__ == '__main__':
    main()
