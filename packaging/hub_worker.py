"""Local Hub entry point. The Hub process owns its private studio children."""
from __future__ import annotations

import argparse
from collections.abc import Callable
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


def _group_exists(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def stop_child(process, pgid=None):
    """Stop a private worker and every process in its verified isolated group."""
    owned_group = pgid if pgid == process.pid and pgid > 1 else None
    if owned_group is not None:
        try:
            os.killpg(owned_group, signal.SIGTERM)
        except ProcessLookupError:
            pass
    elif process.poll() is None:
        process.terminate()
    if process.poll() is None:
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            if owned_group is not None:
                try:
                    os.killpg(owned_group, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            process.wait(timeout=5)
    if owned_group is not None:
        deadline = time.monotonic() + 2
        while _group_exists(owned_group) and time.monotonic() < deadline:
            time.sleep(0.02)
        if _group_exists(owned_group):
            try:
                os.killpg(owned_group, signal.SIGKILL)
            except ProcessLookupError:
                pass


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


def _voice_spec(release: dict, data: Path, runtime: Path, resources: Path) -> dict:
    return {
        'id': 'voice', 'modality': 'voice', 'python': runtime / 'bin/python3',
        'runtime_id': release['runtime_sha256'], 'outputs': data / 'voice/output',
        'extra_args': [],
    }


def _image_spec(release: dict, data: Path, runtime: Path, resources: Path) -> dict:
    image_runtime = resources / 'runtimes/image'
    image_id = release['components']['image'].get('runtime_sha256')
    if (not (image_runtime / 'bin/python3').is_file()
            or not (resources / 'image_worker.py').is_file()
            or not _runtime_id(image_id)):
        raise ValueError('Incomplete Image runtime or release identity')
    return {
        'id': 'image', 'modality': 'image', 'python': image_runtime / 'bin/python3',
        'runtime_id': image_id, 'outputs': data / 'image/outputs', 'extra_args': [],
    }


def _subtitle_spec(release: dict, data: Path, runtime: Path, resources: Path) -> dict:
    subtitle_id = release['components']['subtitle'].get('runtime_sha256')
    if (not (resources / 'subtitle_worker.py').is_file()
            or not (resources / 'components/subtitle/app/backend/main.py').is_file()
            or not _runtime_id(subtitle_id)):
        raise ValueError('Incomplete Subtitle worker or release identity')
    return {
        'id': 'subtitle', 'modality': 'transcription',
        'python': runtime / 'bin/python3', 'runtime_id': subtitle_id,
        'outputs': data / 'subtitle', 'extra_args': [],
    }


def _music_spec(release: dict, data: Path, runtime: Path, resources: Path) -> dict:
    music_id = release['components']['music'].get('runtime_sha256')
    if (not (resources / 'music_worker.py').is_file()
            or not (resources / 'components/music/app/backend/main.py').is_file()
            or not _runtime_id(music_id)):
        raise ValueError('Incomplete Music worker or release identity')
    return {
        'id': 'music', 'modality': 'music', 'python': runtime / 'bin/python3',
        'runtime_id': music_id, 'outputs': data / 'music/outputs', 'extra_args': [],
    }


def _video_spec(release: dict, data: Path, runtime: Path, resources: Path) -> dict:
    """The Video worker runs on its own Python 3.11 runtime, not the shared base.

    `ltx_pipelines_mlx` needs 3.11 while the base runtime is 3.12, so the staged tree is
    `runtimes/video` and the digest beside it is the pinned identity — the same shape as Image.
    """
    video_runtime = resources / 'runtimes/video'
    video_id = release['components']['video'].get('runtime_sha256')
    if (not (video_runtime / 'bin/python3').is_file()
            or not (resources / 'video_worker.py').is_file()
            or not (resources / 'components/video/app/backend/main.py').is_file()
            or not (resources / 'model-manifests/ltx-2-3-22b-ram-12gb-mlx.json').is_file()
            or not _runtime_id(video_id)):
        raise ValueError('Incomplete Video worker or release identity')
    return {
        'id': 'video', 'modality': 'video', 'python': video_runtime / 'bin/python3',
        'runtime_id': video_id, 'outputs': data / 'video/outputs', 'extra_args': [],
    }


def _chat_spec(release: dict, data: Path, runtime: Path, resources: Path) -> dict:
    # The chat worker reuses the base runtime (mlx-lm lives there), so its staged identity
    # is expected to equal the base runtime digest unless a dedicated runtimes/chat is added.
    chat_id = release['components']['chat'].get('runtime_sha256')
    if (not (resources / 'chat_worker.py').is_file()
            or not (resources / 'components/chat/app/backend/main.py').is_file()
            or not (resources / 'components/chat/app/backend/model_manager.py').is_file()
            or not (resources / 'components/chat/app/backend/llm_engine.py').is_file()
            or not (resources / 'components/model_packages.py').is_file()
            or not _runtime_id(chat_id)):
        raise ValueError('Incomplete Chat worker or release identity')
    try:
        from components.chat.app.backend.model_manager import (read_chat_manifests,
                                                               select_default_manifest)
        manifests = read_chat_manifests(resources / 'model-manifests')
        select_default_manifest(manifests)
    except (ImportError, OSError, ValueError) as exc:
        raise ValueError(str(exc)) from exc
    return {
        'id': 'chat', 'modality': 'chat', 'python': runtime / 'bin/python3',
        'runtime_id': chat_id, 'outputs': data / 'chat/outputs', 'extra_args': [],
    }


def _embeddings_spec(release: dict, data: Path, runtime: Path, resources: Path) -> dict:
    # Reuses the base runtime (mlx and tokenizers live there) and Chat's memory-policy module.
    embeddings_id = release['components']['embeddings'].get('runtime_sha256')
    if (not (resources / 'embeddings_worker.py').is_file()
            or not (resources / 'components/embeddings/app/backend/main.py').is_file()
            or not _runtime_id(embeddings_id)):
        raise ValueError('Incomplete Embeddings worker or release identity')
    return {
        'id': 'embeddings', 'modality': 'embeddings', 'python': runtime / 'bin/python3',
        'runtime_id': embeddings_id, 'outputs': data / 'embeddings/outputs', 'extra_args': [],
    }


def _vision_spec(release: dict, data: Path, runtime: Path, resources: Path) -> dict:
    # Reuses the base runtime (mlx, numpy, Pillow, tokenizers) and Chat's memory-policy module.
    vision_id = release['components']['vision'].get('runtime_sha256')
    if (not (resources / 'vision_worker.py').is_file()
            or not (resources / 'components/vision/app/backend/main.py').is_file()
            or not _runtime_id(vision_id)):
        raise ValueError('Incomplete Vision worker or release identity')
    return {
        'id': 'vision', 'modality': 'vision', 'python': runtime / 'bin/python3',
        'runtime_id': vision_id, 'outputs': data / 'vision/outputs', 'extra_args': [],
    }


# One entry per key the release manifest's `components` map may carry, and the only place a
# studio is registered with the Hub. The Hub spawns exactly what the manifest lists, in the
# order the manifest lists it; a component with no private child process of its own (the Hub
# is this process) maps to None. A new studio is an entry here *and* an entry in
# `COMPONENT_STAGERS` in packaging/stage_voice.py - a key this table does not know is refused
# by name rather than spawning nothing and routing jobs to a worker that does not exist.
COMPONENT_WORKERS: dict[str, Callable[[dict, Path, Path, Path], dict] | None] = {
    'hub': None,
    'voice': _voice_spec,
    'image': _image_spec,
    'subtitle': _subtitle_spec,
    'music': _music_spec,
    'chat': _chat_spec,
    'video': _video_spec,
    'embeddings': _embeddings_spec,
    'vision': _vision_spec,
    # The KH Updater agent runs in this process (`khu_agent.loop`), not as a private child. It is
    # not a top-level manifest component yet: the installer already on every Mac is 0.6.198's and
    # refuses a component name its `runtimePaths` does not know, so the kit rides inside the `hub`
    # record. Registered here (and in `stage_voice.COMPONENT_STAGERS`) so a later release can list
    # it as a component once every installed installer can read it.
    'khu-agent': None,
}


def worker_specs(release: dict, data: Path, runtime: Path, resources: Path) -> list[dict]:
    specs = []
    for component in release.get('components', {}):
        if component not in COMPONENT_WORKERS:
            raise ValueError(
                f"Release manifest lists component {component!r}, which this Hub has no private "
                f"worker for. Add '{component}' to COMPONENT_WORKERS in packaging/hub_worker.py "
                f"and to COMPONENT_STAGERS in packaging/stage_voice.py before shipping it.")
        factory = COMPONENT_WORKERS[component]
        if factory is None:
            continue
        spec = factory(release, data, runtime, resources)
        if spec['id'] != component:
            raise ValueError(
                f"Component {component!r} produced a spec for {spec['id']!r}; fix "
                f"COMPONENT_WORKERS in packaging/hub_worker.py")
        specs.append(spec)
    return specs


def worker_command(spec: dict, resources: Path, data: Path, models: Path,
                   token_file: Path, ready_file: Path, parent_pid: int,
                   *, offline: bool, port: int = 0) -> list[str]:
    command = [
        str(spec['python']), '-I', '-B', str(resources / f"{spec['id']}_worker.py"),
        '--data-dir', str(data), '--models-dir', str(models),
        '--ready-file', str(ready_file), '--parent-pid', str(parent_pid),
        '--hub-token-file', str(token_file), *spec.get('extra_args', []),
    ]
    if port:
        command += ['--port', str(port)]
    if offline:
        command.append('--offline')
    return command


# A private worker that exits after startup is restarted on its own port, so the Hub's worker
# descriptor stays valid and only that studio's lane is down (the monitor sees it unreachable)
# while every other studio and its in-flight jobs keep running. Consecutive failures back off
# along this list (the last value repeats); a worker that stayed up for RESTART_STABLE_SECONDS
# starts the list over.
RESTART_BACKOFF_SECONDS = (1, 2, 5, 10, 30, 60)
RESTART_STABLE_SECONDS = 300


def restart_delay(failures: int) -> int:
    return RESTART_BACKOFF_SECONDS[min(failures, len(RESTART_BACKOFF_SECONDS) - 1)]


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
    components = release.get('components', {})
    # `worker_specs` spawns exactly the components the manifest lists, so the two every Hub
    # release is built on must be present: the Hub itself and the Voice runtime it shares.
    if (release.get('schema') != 'studiohub.standalone-release'
            or release.get('schema_version') != 1
            or 'hub' not in components or 'voice' not in components):
        parser.error('Release manifest does not identify the bundled Hub and Voice components')
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
        # Why the app last stopped, written by the app itself (`StopRecord`); published as `last_stop`.
        'STUDIOHUB_LAST_STOP_FILE': str(data / 'last-stop.json'),
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
    # Held while a worker is spawned or the descriptor rewritten, so `cleanup` never misses a
    # worker the monitor is starting at the moment the Hub shuts down.
    children_lock = threading.Lock()

    def spawn(child, port=0):
        child['ready'].unlink(missing_ok=True)
        command = worker_command(
            child['spec'], RESOURCES, data, models, token_file, child['ready'],
            os.getpid(), offline=args.offline, port=port)
        child['process'] = subprocess.Popen(command, cwd=data,
            env={'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'LANG': 'en_US.UTF-8'})
        child['pgid'] = child['process'].pid
        child['started'] = time.monotonic()

    def descriptor(child):
        return {
            'id': child['id'], 'modality': child['modality'],
            'title': f"{child['id'].title()} Studio",
            'host': '127.0.0.1', 'port': child['port'], 'machine': 'local', 'local': True,
            'hub_token': token, 'studio_token': token, 'pid': child['process'].pid,
            'runtime_identity': {'id': child['runtime_id'], 'compatible': True},
            'managed_output_roots': [str(child['outputs'])],
        }

    def restart(child):
        """Bring one exited worker back on its own port; the Hub and other studios stay up."""
        label = child['id'].title()
        if time.monotonic() - child['started'] >= RESTART_STABLE_SECONDS:
            child['failures'] = 0
        delay = restart_delay(child['failures'])
        child['failures'] += 1
        print(f"Private {label} worker exited (status {child['process'].returncode}); restarting "
              f"it in {delay} s. The other studios keep running.", file=sys.stderr, flush=True)
        stop_child(child['process'], child.get('pgid'))
        child['pgid'] = None
        if child_watch_stop.wait(delay):
            return
        with children_lock:
            if child_watch_stop.is_set():
                return
            spawn(child, child['port'])
        try:
            await_child(child['process'], child['ready'], label=label)
            if os.getpgid(child['process'].pid) != child['process'].pid:
                raise RuntimeError(f'Private {label} worker process group is not isolated')
        except (RuntimeError, ValueError) as exc:
            print(f"{exc}; the Hub will try again.", file=sys.stderr, flush=True)
            stop_child(child['process'], child.get('pgid'))
            child['pgid'] = None
            return
        with children_lock:
            if not child_watch_stop.is_set():
                write_workers(workers_file, [descriptor(item) for item in children])
        print(f"Private {label} worker restarted.", file=sys.stderr, flush=True)

    def watch_children():
        while not child_watch_stop.wait(0.2):
            for child in children:
                if child['process'].poll() is not None and not child_watch_stop.is_set():
                    restart(child)

    def load_app():
        # The shared server starts its parent watchdog before these startup waits. A worker that
        # fails here, before the Hub is ready, is still fatal: nothing has been published yet.
        for child in children:
            _, child['port'] = await_child(child['process'], child['ready'], label=child['id'].title())
            if os.getpgid(child['process'].pid) != child['process'].pid:
                raise RuntimeError(
                    f'Private {child["id"].title()} worker process group is not isolated')
        write_workers(workers_file, [descriptor(child) for child in children])
        threading.Thread(target=watch_children, daemon=True,
                         name='studiohub-child-monitor').start()
        from components.hub.standalone import app
        return app

    def cleanup():
        with children_lock:
            child_watch_stop.set()
        ready.unlink(missing_ok=True)
        for child in reversed(children):
            stop_child(child['process'], child.get('pgid'))
            child['pgid'] = None
            child['ready'].unlink(missing_ok=True)
        workers_file.unlink(missing_ok=True)

    try:
        for spec in specs:
            child = {
                'id': spec['id'], 'modality': spec['modality'], 'spec': spec,
                'ready': hub_data / f"{spec['id']}-ready.json", 'runtime_id': spec['runtime_id'],
                'outputs': spec['outputs'], 'failures': 0,
            }
            spawn(child)
            children.append(child)
        # The Hub publishes what it was told to listen on, so "is this Mac reachable, and on
        # what" is a fact an operator or a gateway can read rather than infer from a failed
        # connection. The shell decides the list (`--listen-auto` recomputes it from this Mac's
        # own addresses); this process only reports the answer it was launched with, which is why
        # it is passed through the environment rather than re-derived here.
        os.environ['STUDIOHUB_BOUND_HOST'] = _bind_host(args.host)
        os.environ['STUDIOHUB_BOUND_PORT'] = str(args.port)
        run_server(load_app, args.port, ready, release['version'], args.parent_pid, pgid,
                   token=token, factory=True, shutdown_callback=cleanup,
                   shutdown_started=child_watch_stop.set, host=_bind_host(args.host))
    finally:
        cleanup()



if __name__ == '__main__':
    main()
