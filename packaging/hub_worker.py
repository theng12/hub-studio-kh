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


def _bind_host(value: object) -> str:
    """Accept only an address this Hub may actually bind.

    Loopback and the wildcard are the two safe answers; a specific address lets an operator
    scope the Hub to one interface, such as a Tailscale address. Anything else - a hostname,
    a URL, a range - is refused rather than guessed at, because binding is a security
    decision and a typo should not silently widen it.
    """
    import ipaddress
    entries = [entry.strip() for entry in str(value or '').split(',') if entry.strip()]
    if not entries:
        return '127.0.0.1'
    for host in entries:
        if host in ('0.0.0.0', '::'):
            return '0.0.0.0'
        try:
            address = ipaddress.ip_address(host)
        except ValueError as exc:
            raise ValueError(f'--host must be IP addresses, not {host!r}') from exc
        if address.version != 4:
            raise ValueError('--host must be IPv4 addresses')
    # Loopback is prepended by the server; return the requested scope unchanged.
    return ','.join(entries)


def _runtime_id(value) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(ch in '0123456789abcdef' for ch in value))


def worker_specs(release: dict, data: Path, runtime: Path, resources: Path) -> list[dict]:
    specs = [{
        'id': 'voice', 'modality': 'voice', 'python': runtime / 'bin/python3',
        'runtime_id': release['runtime_sha256'], 'outputs': data / 'voice/output',
        'extra_args': [],
    }]
    if 'image' in release.get('components', {}):
        image_runtime = resources / 'runtimes/image'
        image_id = release['components']['image'].get('runtime_sha256')
        if (not (image_runtime / 'bin/python3').is_file()
                or not (resources / 'image_worker.py').is_file()
                or not _runtime_id(image_id)):
            raise ValueError('Incomplete Image runtime or release identity')
        specs.append({
            'id': 'image', 'modality': 'image', 'python': image_runtime / 'bin/python3',
            'runtime_id': image_id, 'outputs': data / 'image/outputs', 'extra_args': [],
        })
    if 'subtitle' in release.get('components', {}):
        subtitle_id = release['components']['subtitle'].get('runtime_sha256')
        if (not (resources / 'subtitle_worker.py').is_file()
                or not (resources / 'components/subtitle/app/backend/main.py').is_file()
                or not _runtime_id(subtitle_id)):
            raise ValueError('Incomplete Subtitle worker or release identity')
        specs.append({
            'id': 'subtitle', 'modality': 'transcription',
            'python': runtime / 'bin/python3', 'runtime_id': subtitle_id,
            'outputs': data / 'subtitle', 'extra_args': [],
        })
    if 'music' in release.get('components', {}):
        music_id = release['components']['music'].get('runtime_sha256')
        engine = resources / 'engines/ace-step-1-5'
        if (not (resources / 'music_worker.py').is_file()
                or not (resources / 'components/music/app/backend/main.py').is_file()
                or not (resources / 'runtimes/music/manifest/music-runtime.json').is_file()
                or not (engine / 'studiohub-engine.json').is_file()
                or not _runtime_id(music_id)):
            raise ValueError('Incomplete Music worker or release identity')
        specs.append({
            'id': 'music', 'modality': 'music', 'python': runtime / 'bin/python3',
            'runtime_id': music_id, 'outputs': data / 'music/outputs',
            'extra_args': ['--engine-root', str(engine)],
        })
    if 'chat' in release.get('components', {}):
        # The chat worker reuses the base runtime (mlx-lm lives there), so its staged identity
        # is expected to equal the base runtime digest unless a dedicated runtimes/chat is added.
        chat_id = release['components']['chat'].get('runtime_sha256')
        if (not (resources / 'chat_worker.py').is_file()
                or not (resources / 'components/chat/app/backend/main.py').is_file()
                or not (resources / 'model-manifests/qwen3-5-9b-4bit.json').is_file()
                or not _runtime_id(chat_id)):
            raise ValueError('Incomplete Chat worker or release identity')
        specs.append({
            'id': 'chat', 'modality': 'chat', 'python': runtime / 'bin/python3',
            'runtime_id': chat_id, 'outputs': data / 'chat/outputs', 'extra_args': [],
        })
    return specs


def worker_command(spec: dict, resources: Path, data: Path, models: Path,
                   token_file: Path, ready_file: Path, parent_pid: int,
                   *, offline: bool) -> list[str]:
    command = [
        str(spec['python']), '-I', '-B', str(resources / f"{spec['id']}_worker.py"),
        '--data-dir', str(data), '--models-dir', str(models),
        '--ready-file', str(ready_file), '--parent-pid', str(parent_pid),
        '--hub-token-file', str(token_file), *spec.get('extra_args', []),
    ]
    if offline:
        command.append('--offline')
    return command


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--models-dir', type=Path, required=True)
    parser.add_argument('--ready-file', type=Path, required=True)
    parser.add_argument('--parent-pid', type=int)
    parser.add_argument('--port', type=int, default=0)
    # Loopback unless asked otherwise: this is the switch that decides whether the Hub is a
    # private local service or something another Mac can reach. A comma-separated list lets a
    # Mac serve its LAN and its Tailscale address at once; loopback is always included.
    parser.add_argument('--host', default='127.0.0.1')
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
    try:
        specs = worker_specs(release, data, runtime, RESOURCES)
    except ValueError as exc:
        parser.error(str(exc))
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
                'id': child['id'], 'modality': child['modality'],
                'title': f"{child['id'].title()} Studio",
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
        for spec in specs:
            worker_id = spec['id']
            child_ready = hub_data / f'{worker_id}-ready.json'
            child_ready.unlink(missing_ok=True)
            command = worker_command(
                spec, RESOURCES, data, models, token_file, child_ready,
                os.getpid(), offline=args.offline)
            child = subprocess.Popen(command, cwd=data,
                env={'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'LANG': 'en_US.UTF-8'})
            children.append({
                'id': worker_id, 'modality': spec['modality'], 'process': child,
                'ready': child_ready, 'runtime_id': spec['runtime_id'],
                'outputs': spec['outputs'],
            })
        run_server(load_app, args.port, ready, release['version'], args.parent_pid, pgid,
                   token=token, factory=True, shutdown_callback=cleanup, host=_bind_host(args.host))
    finally:
        cleanup()



if __name__ == '__main__':
    main()
