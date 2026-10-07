"""Private bundle-relative Chat entry point; run with immutable Python -I -B.

Chat reuses the base ``runtimes/voice`` runtime because ``mlx-lm`` is already installed
there for Whisper/MLX-audio. A dedicated ``runtimes/chat`` is preferred automatically if
one is ever staged, so this can move to a slimmer runtime without touching the Hub.

The worker reads every ``chat-mlx`` manifest in the bundle. Their metadata may coexist, while
the backend keeps exactly one model's weights resident and switches under one global session
lock. One eligible default may be explicit; a legacy catalog without a flag falls back only
when exactly one model is production-eligible. Pending manifests remain visible but cannot load.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

RESOURCES = Path(__file__).resolve().parent
sys.path.insert(0, str(RESOURCES))
from voice_worker import read_private_token, run_server


def _runtime(resources: Path) -> Path:
    dedicated = resources / 'runtimes' / 'chat'
    return dedicated if (dedicated / 'bin/python3').is_file() else resources / 'runtimes' / 'voice'


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
    source = resources / 'components' / 'chat' / 'app'
    runtime = _runtime(resources)
    catalog = resources / 'model-manifests'
    if not models.is_dir():
        parser.error('Selected model library is unavailable; reconnect its volume')
    if any(path == bundle or path.is_relative_to(bundle) for path in (data, models)):
        parser.error('Chat state and selected models must be outside the app bundle')
    if ready.is_symlink() or not ready.resolve().is_relative_to(data):
        parser.error('Ready file must stay inside external app data')
    if not (release_path.is_file() and (source / 'backend/main.py').is_file()
            and (runtime / 'bin/python3').is_file()):
        parser.error('Incomplete Chat bundle: release, source or runtime is missing')
    try:
        from components.chat.app.backend.model_manager import (read_chat_manifests,
                                                               select_default_manifest)
        manifests = read_chat_manifests(catalog)
        _default_path, default_manifest = select_default_manifest(manifests)
    except (ImportError, ValueError) as exc:
        parser.error(str(exc))
    try:
        release = json.loads(release_path.read_text())
        chat = release['components']['chat']
        identity = chat['runtime_sha256']
        if (release['schema'] != 'studiohub.standalone-release'
                or release['schema_version'] != 1 or not isinstance(identity, str)
                or len(identity) != 64 or any(ch not in '0123456789abcdef' for ch in identity)):
            raise ValueError('Release manifest does not identify an exact Chat runtime')
    except (OSError, KeyError, TypeError, ValueError) as exc:
        parser.error(f'Invalid Chat release identity: {exc}')
    if args.parent_pid is not None:
        if args.parent_pid <= 1 or args.parent_pid != os.getppid():
            parser.error('Parent PID must identify the worker actual owner')
        if os.getpgrp() != os.getpid():
            os.setsid()
        pgid = os.getpgrp()
        if pgid != os.getpid():
            parser.error('Private Chat process group must be isolated')
    else:
        pgid = None
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    chat_data = data / 'chat'
    if chat_data.is_symlink():
        parser.error('Chat data cannot be a symlink')
    chat_data.mkdir(exist_ok=True, mode=0o700)
    if chat_data.stat().st_uid != os.geteuid() or chat_data.stat().st_mode & 0o077:
        parser.error('Chat state must be owner-only')
    try:
        token = read_private_token(args.hub_token_file, data)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    for key in list(os.environ):
        if key.startswith(('PYTHON', 'HF_', 'HUGGINGFACE_', 'STUDIOHUB_',
                           'KH_STUDIO_', 'PINOKIO_', 'DYLD_')):
            os.environ.pop(key, None)
    os.environ.update({
        'STUDIOHUB_CHAT_DATA_DIR': str(chat_data),
        'STUDIOHUB_MODELS_DIR': str(models),
        'STUDIOHUB_MODEL_MANIFESTS': str(catalog),
        'STUDIOHUB_RELEASE_MANIFEST': str(release_path),
        'STUDIOHUB_FLEET_TOKEN': token,
        'HF_HUB_DISABLE_TELEMETRY': '1', 'PYTHONNOUSERSITE': '1',
        'PYTHONDONTWRITEBYTECODE': '1',
        'HF_HOME': str(chat_data / 'cache'),
        'XDG_CACHE_HOME': str(chat_data / 'cache'),
        'PATH': str(runtime / 'bin') + ':/usr/bin:/bin:/usr/sbin:/sbin',
    })
    if args.offline:
        os.environ['HF_HUB_OFFLINE'] = '1'
        os.environ['TRANSFORMERS_OFFLINE'] = '1'
    sys.dont_write_bytecode = True
    if args.check:
        print(json.dumps({'component': 'chat', 'version': release['version'],
                          'source': str(source), 'runtime_sha256': identity,
                          'data': str(chat_data), 'models': str(models),
                          'model_manifests': [path.name for path, _manifest in manifests],
                          'model_ids': [manifest.get('model_id') for _path, manifest in manifests],
                          'default_model_id': default_manifest.get('model_id'),
                          'offline': bool(args.offline), 'managed_updates': False}))
        return
    os.chdir(chat_data)
    run_server('components.chat.app.backend.main:from_environment', args.port,
               ready, str(release['version']), args.parent_pid, pgid, factory=True)


if __name__ == '__main__':
    main()
