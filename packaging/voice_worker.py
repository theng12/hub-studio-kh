"""Bundle-relative Voice entry point; Python runs this with -I -B."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description="Standalone Voice packaging proof")
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--models-dir', type=Path)
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--offline', action='store_true', help='Use imported Hugging Face models without downloads')
    parser.add_argument('--check', action='store_true', help='Validate paths without starting services')
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error('Port must be between 0 and 65535')

    resources = Path(__file__).resolve().parent
    bundle = resources.parent.parent
    default_state = Path.home() / 'Library' / 'Application Support' / 'StudioHub Standalone'
    data = (args.data_dir or default_state).expanduser().resolve()
    models = (args.models_dir or (data / 'models')).expanduser().resolve()
    # Selected external volumes must already exist. Never reconstruct a missing mount.
    if args.models_dir and not models.is_dir():
        parser.error('Selected model library is unavailable; mount the volume or choose another library')
    for path in (data, models):
        if path == bundle or bundle in path.parents:
            parser.error('Node state and models must be outside the app bundle')
    source = resources / 'voice' / 'app'
    manifest = resources / 'release-manifest.json'
    if not source.is_dir() or not manifest.is_file():
        parser.error('Incomplete Voice bundle: source or release manifest is missing')
    try:
        release = json.loads(manifest.read_text())
        if release['schema'] != 'studiohub.standalone-release' or release['schema_version'] != 1:
            raise ValueError('unsupported release schema')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.error(f'Invalid release manifest: {exc}')

    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    models.mkdir(parents=True, exist_ok=True)
    # The app owns these settings. Inherited credentials/cache/runtime variables
    # can belong to a different Studio installation or the developer's shell.
    for key in list(os.environ):
        if key.startswith(('PYTHON', 'HF_', 'HUGGINGFACE_', 'STUDIOHUB_', 'KH_STUDIO_', 'PINOKIO_', 'DYLD_')):
            os.environ.pop(key, None)
    runtime = resources / 'runtimes' / 'voice'
    os.environ.update({
        'STUDIOHUB_DATA_DIR': str(data),
        'STUDIOHUB_MODELS_DIR': str(models),
        'STUDIOHUB_TOOLS_DIR': str(runtime / 'bin'),
        'STUDIOHUB_RELEASE_MANIFEST': str(manifest),
        'HF_HOME': str(models),
        'HF_HUB_CACHE': str(models / 'hub'),
        'HF_HUB_DISABLE_TELEMETRY': '1',
        'PYTHONNOUSERSITE': '1',
        'PYTHONDONTWRITEBYTECODE': '1',
        'XDG_CACHE_HOME': str(data / 'cache'),
        'NUMBA_CACHE_DIR': str(data / 'cache' / 'numba'),
        'TORCH_HOME': str(data / 'cache' / 'torch'),
        'MPLCONFIGDIR': str(data / 'cache' / 'matplotlib'),
        'PATH': str(runtime / 'bin') + ':/usr/bin:/bin:/usr/sbin:/sbin',
    })
    if args.offline:
        os.environ.update({'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1'})
    sys.dont_write_bytecode = True
    if args.check:
        print(json.dumps({'source_dir': str(source), 'data_dir': str(data),
                          'models_dir': str(models), 'managed_updates': False,
                          'version': release.get('version'), 'offline': args.offline}))
        return
    sys.path.insert(0, str(source))
    os.chdir(data)
    import uvicorn
    config = uvicorn.Config('backend.main:app', host='127.0.0.1', port=args.port, log_level='info')
    # Bind once and keep the socket, so reported ephemeral ports are exact and
    # another process cannot take the port between selection and server startup.
    with config.bind_socket() as sock:
        os.environ['STUDIOHUB_BIND_HOST'] = '127.0.0.1'
        os.environ['STUDIOHUB_BIND_PORT'] = str(sock.getsockname()[1])
        uvicorn.Server(config).run(sockets=[sock])


if __name__ == '__main__':
    main()
