"""Bundle-relative Voice entry point; Python runs this with -I -B."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import threading


PARENT_POLL_SECONDS = 0.1
PARENT_KILL_GRACE_SECONDS = 2.0


def write_ready_file(path: Path, url: str, version: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f'.{path.name}.', suffix='.tmp')
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, 'w') as stream:
            json.dump({'url': url, 'pid': os.getpid(), 'version': version}, stream)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def signal_own_process_group(pgid: int, signal_number: int) -> bool:
    if pgid != os.getpid() or os.getpgrp() != pgid:
        return False
    try:
        os.killpg(pgid, signal_number)
    except ProcessLookupError:
        return False
    return True


def monitor_parent(parent_pid: int, pgid: int, ready_file: Path | None,
                   stop: threading.Event, parent_lost: threading.Event,
                   readiness_lock: threading.Lock) -> None:
    while not stop.is_set():
        if os.getppid() != parent_pid:
            with readiness_lock:
                parent_lost.set()
                if ready_file is not None:
                    ready_file.unlink(missing_ok=True)
            signal_own_process_group(pgid, signal.SIGTERM)
            if not stop.wait(PARENT_KILL_GRACE_SECONDS):
                signal_own_process_group(pgid, signal.SIGKILL)
            return
        stop.wait(PARENT_POLL_SECONDS)


def main() -> None:
    parser = argparse.ArgumentParser(description="Standalone Voice packaging proof")
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--models-dir', type=Path)
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--ready-file', type=Path)
    parser.add_argument('--parent-pid', type=int)
    parser.add_argument('--offline', action='store_true', help='Use imported Hugging Face models without downloads')
    parser.add_argument('--check', action='store_true', help='Validate paths without starting services')
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error('Port must be between 0 and 65535')
    worker_pgid = None
    if args.parent_pid is not None:
        if args.parent_pid <= 1 or args.parent_pid != os.getppid():
            parser.error('Parent PID must identify the worker\'s actual parent process')
        try:
            if os.getpgrp() != os.getpid():
                os.setsid()
            worker_pgid = os.getpgrp()
        except OSError as exc:
            parser.error(f'Could not isolate worker process group: {exc}')
        if worker_pgid != os.getpid():
            parser.error('Worker process group is not isolated')

    resources = Path(__file__).resolve().parent
    bundle = resources.parent.parent
    default_state = Path.home() / 'Library' / 'Application Support' / 'StudioHub Standalone'
    data = (args.data_dir or default_state).expanduser().resolve()
    models = (args.models_dir or (data / 'models')).expanduser().resolve()
    ready_file = args.ready_file.expanduser().resolve() if args.ready_file else None
    # Selected external volumes must already exist. Never reconstruct a missing mount.
    if args.models_dir and not models.is_dir():
        parser.error('Selected model library is unavailable; mount the volume or choose another library')
    for path in (data, models):
        if path == bundle or bundle in path.parents:
            parser.error('Node state and models must be outside the app bundle')
    if ready_file is not None and data not in ready_file.parents:
        parser.error('Ready file must be inside the external data directory')
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
    monitor_stop = threading.Event()
    parent_lost = threading.Event()
    readiness_lock = threading.Lock()
    parent_monitor = None
    if worker_pgid is not None:
        parent_monitor = threading.Thread(target=monitor_parent,
            args=(args.parent_pid, worker_pgid, ready_file, monitor_stop,
                  parent_lost, readiness_lock), daemon=True,
            name='studiohub-parent-monitor')
        parent_monitor.start()
    try:
        if ready_file is not None:
            ready_file.unlink(missing_ok=True)
        import uvicorn
        config = uvicorn.Config(
            'backend.main:app', host='127.0.0.1', port=args.port, log_level='info')
        # Bind once and keep the socket, so reported ephemeral ports are exact and
        # another process cannot take the port between selection and server startup.
        with config.bind_socket() as sock:
            port = sock.getsockname()[1]
            os.environ['STUDIOHUB_BIND_HOST'] = '127.0.0.1'
            os.environ['STUDIOHUB_BIND_PORT'] = str(port)

            class ReadyServer(uvicorn.Server):
                async def startup(self, sockets=None):
                    await super().startup(sockets=sockets)
                    with readiness_lock:
                        if (ready_file is not None and not self.should_exit
                                and not parent_lost.is_set()):
                            write_ready_file(ready_file,
                                f'http://127.0.0.1:{port}', str(release.get('version', '')))

                async def shutdown(self, sockets=None):
                    try:
                        await super().shutdown(sockets=sockets)
                    finally:
                        with readiness_lock:
                            if ready_file is not None:
                                ready_file.unlink(missing_ok=True)
                        if parent_monitor is not None and not parent_lost.is_set():
                            monitor_stop.set()

            ReadyServer(config).run(sockets=[sock])
    finally:
        if ready_file is not None:
            ready_file.unlink(missing_ok=True)
        if parent_monitor is not None:
            if not parent_lost.is_set():
                monitor_stop.set()
            parent_monitor.join(PARENT_KILL_GRACE_SECONDS + 1)


if __name__ == '__main__':
    main()
