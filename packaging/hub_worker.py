"""Local Hub entry point. The Hub process owns its private studio children."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit


RESOURCES = Path(__file__).resolve().parent
sys.path.insert(0, str(RESOURCES))
from voice_worker import read_private_token, run_server


def write_workers(path, workers):
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix='.workers-')
    try:
        with os.fdopen(descriptor, 'w') as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(workers, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def stop_child(process):
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        # voice_worker isolates its own group; do not signal any unrelated group.
        try:
            if os.getpgid(process.pid) == process.pid:
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            pass
        process.wait(timeout=5)


def await_child(process, ready_file, timeout=120, *, label="Studio"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f'Private {label} worker exited during startup; inspect the service log')
        try:
            value = json.loads(ready_file.read_text())
            url = urlsplit(value['url'])
            if (value['pid'] != process.pid or url.scheme != 'http' or url.hostname != '127.0.0.1'
                    or not url.port or url.username or url.password or url.path not in ('', '/')
                    or url.query or url.fragment):
                raise ValueError('Invalid private worker startup identity')
            return value, url.port
        except FileNotFoundError:
            time.sleep(0.05)
    raise RuntimeError(f'Private {label} worker startup timed out; inspect the service log')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--models-dir', type=Path, required=True)
    parser.add_argument('--ready-file', type=Path, required=True)
    parser.add_argument('--parent-pid', type=int)
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    data, models = args.data_dir.expanduser().resolve(), args.models_dir.expanduser().resolve()
    ready = args.ready_file.expanduser().absolute()
    bundle = RESOURCES.parent.parent
    source = RESOURCES / 'components/hub'
    release_file = RESOURCES / 'release-manifest.json'
    runtime = RESOURCES / 'runtimes/voice'
    if not 0 <= args.port <= 65535:
        parser.error('Port must be between 0 and 65535')
    if not models.is_dir():
        parser.error('Selected model library is unavailable; reconnect its volume')
    if any(p == bundle or p.is_relative_to(bundle) for p in (data, models)):
        parser.error('App state and models must be outside the application bundle')
    if ready.is_symlink() or not ready.resolve().is_relative_to(data):
        parser.error('Readiness must stay inside this app data directory')
    if not (source / 'standalone.py').is_file() or not release_file.is_file() or not (runtime / 'bin/python3').is_file():
        parser.error('Incomplete Hub application bundle')
    release = json.loads(release_file.read_text())
    if (release.get('schema') != 'studiohub.standalone-release'
            or release.get('schema_version') != 1 or 'hub' not in release.get('components', {})):
        parser.error('Release manifest does not identify the bundled Hub')
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    hub_data = data / 'hub'
    if hub_data.is_symlink():
        parser.error('Hub state cannot be a directory symlink')
    hub_data.mkdir(exist_ok=True, mode=0o700)
    if hub_data.stat().st_uid != os.geteuid() or hub_data.stat().st_mode & 0o077:
        parser.error('Hub state must be an owner-only directory')
    for key in list(os.environ):
        if key.startswith(('PYTHON', 'HF_', 'HUGGINGFACE_', 'STUDIOHUB_', 'KH_STUDIO_', 'PINOKIO_', 'DYLD_')):
            os.environ.pop(key, None)
    workers_file = hub_data / 'owned-workers.json'
    os.environ.update({
        'STUDIOHUB_PACKAGED': '1', 'STUDIOHUB_REQUIRE_AUTH': '1', 'STUDIOHUB_DATA_DIR': str(hub_data),
        'STUDIOHUB_MODELS_DIR': str(models), 'STUDIOHUB_RELEASE_MANIFEST': str(release_file),
        'STUDIOHUB_WORKERS_FILE': str(workers_file),
        'STUDIOHUB_MODEL_MANIFESTS': str(RESOURCES / 'model-manifests'),
        'PYTHONNOUSERSITE': '1', 'PYTHONDONTWRITEBYTECODE': '1',
        'PATH': str(runtime / 'bin') + ':/usr/bin:/bin:/usr/sbin:/sbin',
    })
    sys.dont_write_bytecode = True
    os.chdir(hub_data)
    if args.check:
        print(json.dumps({'component': 'hub', 'version': release['version'],
                          'state': str(hub_data), 'models': str(models), 'managed_updates': False}))
        return
    pgid = None
    if args.parent_pid is not None:
        if args.parent_pid <= 1 or args.parent_pid != os.getppid():
            parser.error('Parent PID must identify the actual owning app process')
        if os.getpgrp() != os.getpid():
            os.setsid()
        pgid = os.getpgrp()
        if pgid != os.getpid():
            parser.error('Hub process group is not isolated')
    ready.unlink(missing_ok=True)
    token_file = hub_data / '.hub_token'
    if token_file.exists() or token_file.is_symlink():
        read_private_token(token_file, data)
    from components.hub.backend.auth import load_token
    load_token()
    token = read_private_token(token_file, data)
    specs = [('voice', 'voice', runtime, release['runtime_sha256'], data / 'voice/output')]
    if 'image' in release.get('components', {}):
        image_runtime = RESOURCES / 'runtimes/image'
        image_id = release['components']['image'].get('runtime_sha256')
        if (not (image_runtime / 'bin/python3').is_file()
                or not (RESOURCES / 'image_worker.py').is_file()
                or not isinstance(image_id, str) or len(image_id) != 64
                or any(ch not in '0123456789abcdef' for ch in image_id)):
            parser.error('Incomplete Image runtime or release identity')
        specs.append(('image', 'image', image_runtime, image_id, data / 'image/outputs'))
    if 'subtitle' in release.get('components', {}):
        # Subtitle reuses the shared Voice runtime bytes but is its own process and state root.
        # It advertises modality 'transcription' so the packaged public scheduler routes public
        # Subtitle jobs here; the Voice worker keeps its Whisper quality check internal.
        subtitle_id = release['components']['subtitle'].get('runtime_sha256')
        if (not (RESOURCES / 'subtitle_worker.py').is_file()
                or not (RESOURCES / 'components/subtitle/app/backend/main.py').is_file()
                or not isinstance(subtitle_id, str) or len(subtitle_id) != 64
                or any(ch not in '0123456789abcdef' for ch in subtitle_id)):
            parser.error('Incomplete Subtitle worker or release identity')
        specs.append(('subtitle', 'transcription', runtime, subtitle_id, data / 'subtitle'))
    children = []
    child_watch_stop = threading.Event()

    def watch_children():
        while not child_watch_stop.wait(0.2):
            for child in children:
                if child['process'].poll() is not None:
                    print(f"Private {child['id'].title()} worker exited unexpectedly; stopping its Hub owner.",
                          file=sys.stderr, flush=True)
                    os.kill(os.getpid(), signal.SIGTERM)
                    return

    def load_app():
        # The shared server starts its parent watchdog before these startup waits.
        workers = []
        for child in children:
            _, port = await_child(child['process'], child['ready'], label=child['id'].title())
            workers.append({
                'id': child['id'], 'modality': child['modality'], 'title': f"{child['id'].title()} Studio",
                'host': '127.0.0.1', 'port': port, 'machine': 'local', 'local': True,
                'hub_token': token, 'studio_token': token, 'pid': child['process'].pid,
                'runtime_identity': {'id': child['runtime_id'], 'compatible': True},
                'managed_output_roots': [str(child['outputs'])],
            })
        write_workers(workers_file, workers)
        threading.Thread(target=watch_children, daemon=True,
                         name='studiohub-child-monitor').start()
        from components.hub.standalone import app
        return app

    def cleanup():
        child_watch_stop.set()
        ready.unlink(missing_ok=True)
        for child in reversed(children):
            stop_child(child['process'])
            child['ready'].unlink(missing_ok=True)
        workers_file.unlink(missing_ok=True)

    try:
        for worker_id, modality, worker_runtime, runtime_id, outputs in specs:
            child_ready = hub_data / f'{worker_id}-ready.json'
            child_ready.unlink(missing_ok=True)
            command = [str(worker_runtime / 'bin/python3'), '-I', '-B', str(RESOURCES / f'{worker_id}_worker.py'),
                '--data-dir', str(data), '--models-dir', str(models), '--ready-file', str(child_ready),
                '--parent-pid', str(os.getpid()), '--hub-token-file', str(token_file)]
            if args.offline:
                command.append('--offline')
            child = subprocess.Popen(command, cwd=data,
                env={'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'LANG': 'en_US.UTF-8'})
            children.append({'id': worker_id, 'modality': modality, 'process': child, 'ready': child_ready,
                             'runtime_id': runtime_id, 'outputs': outputs})
        run_server(load_app, args.port, ready, release['version'], args.parent_pid, pgid,
                   token=token, factory=True, shutdown_callback=cleanup)
    finally:
        cleanup()



if __name__ == '__main__':
    main()
