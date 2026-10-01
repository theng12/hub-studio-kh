"""Bundle-relative Voice entry point; Python runs this with -I -B."""
from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import re
import signal
import stat
import sys
import tempfile
import threading


PARENT_POLL_SECONDS = 0.1
PARENT_KILL_GRACE_SECONDS = 2.0


def read_private_token(path: Path, data: Path) -> str:
    if path.is_symlink() or not path.resolve().is_relative_to(data.resolve()):
        raise ValueError('Hub token must be a private file inside this app data directory')
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'r') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_mode & 0o077 or info.st_size > 257):
            raise ValueError('Hub token file must be owner-only')
        token = stream.read(257).strip()
        if not re.fullmatch(r'[A-Za-z0-9_-]{24,256}', token):
            raise ValueError('Invalid Hub token')
        return token


def single_engine_manifest(catalog: Path, engine: str, *, component: str) -> tuple[Path, dict]:
    """The one catalog manifest a deliberately single-model packaged worker serves.

    A packaged studio worker may be built around exactly one model - one engine, one manifest,
    one entry in its own ``/v1/models`` - and that is a real contract, not an accident (see
    ``components/chat/app/backend/main.py``). It is only safe while it is enforced: the Hub
    publishes every manifest the bundle carries and offers it in the picker, so a second
    manifest for this engine would be routed here and this worker would silently serve the
    other model. The worker therefore reads the shipped catalog to learn which model it serves
    and refuses, by name, a bundle that carries none or more than one.
    """
    matches = []
    for path in sorted(catalog.glob('*.json')):
        try:
            value = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            raise ValueError(f'Model manifest {path.name} cannot be read: {exc}') from exc
        if isinstance(value, dict) and value.get('engine') == engine:
            matches.append((path, value))
    if not matches:
        raise ValueError(
            f'this bundle carries no {engine} manifest in {catalog.name}/, so the packaged '
            f'{component} worker has no model to serve')
    if len(matches) > 1:
        names = ', '.join(path.name for path, _ in matches)
        raise ValueError(
            f'the packaged {component} worker serves exactly one model, but this bundle carries '
            f'{len(matches)} {engine} manifests: {names}. A second model needs its own worker; '
            f'see components/{component.lower()}/app/backend/main.py')
    return matches[0]


def write_ready_file(path: Path, url: str, version: str, *, token: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f'.{path.name}.', suffix='.tmp')
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, 'w') as stream:
            value = {'url': url, 'pid': os.getpid(), 'version': version}
            if token is not None:
                value['token'] = token
            json.dump(value, stream)
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
    parser.add_argument('--hub-token-file', type=Path, help='Require the owning Hub token, including loopback requests')
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
        # The Voice cache resolver answers from the verified package library, which needs the
        # packaged manifests to map a repository to its package id and revision.
        'STUDIOHUB_MODEL_MANIFESTS': str(resources / 'model-manifests'),
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
    if args.hub_token_file is not None:
        try:
            token = read_private_token(args.hub_token_file, data)
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
        os.environ.update({'STUDIOHUB_FLEET_TOKEN': token, 'STUDIOHUB_REQUIRE_AUTH': '1',
                           'STUDIOHUB_OUTPUT_POLICY_OWNER': 'hub'})
    sys.dont_write_bytecode = True
    if args.check:
        print(json.dumps({'source_dir': str(source), 'data_dir': str(data),
                          'models_dir': str(models), 'managed_updates': False,
                          'version': release.get('version'), 'offline': args.offline}))
        return
    sys.path.insert(0, str(source))
    # `components/` sits beside this script in the bundle, and the shared modules the workers
    # import by package path live there (`components/memory_modes.py` is the memory-mode table
    # and the `auto` rule every Studio answers from; `components/app_version.py` and
    # `components/model_packages.py` are the same shape). Appended rather than prepended, so
    # the Voice tree and the runtime keep precedence and nothing in the bundle's top level can
    # shadow a third-party module.
    sys.path.append(str(resources))
    os.chdir(data)
    run_server('backend.main:app', args.port, ready_file, str(release.get('version', '')),
               args.parent_pid, worker_pgid)


LOOPBACK = '127.0.0.1'


def bind_hosts(value: object) -> list[str]:
    """The addresses to listen on: always loopback, plus whatever was asked for.

    Loopback is never dropped. The owner app talks to its own Hub over it, so losing it would
    mean a Mac whose own services it cannot reach - and no fleet feature is worth that. A
    wildcard request supersedes the rest because it already covers them.
    """
    raw = str(value or '').strip()
    requested = [entry.strip() for entry in raw.split(',') if entry.strip()]
    if any(entry in ('0.0.0.0', '::') for entry in requested):
        return ['0.0.0.0']
    hosts = [LOOPBACK]
    for entry in requested:
        if entry != LOOPBACK and entry not in hosts:
            hosts.append(entry)
    return hosts


class QuietReadAccessLog(logging.Filter):
    """Drop uvicorn's access line for a successful GET; keep every other line.

    The Hub polls each worker's health, activity, catalog and job status every few seconds, and
    its own app polls a dozen read routes. Those answered 200 lines were ~95% of the service log
    (about 70 lines a second on a controller, ~20 MB an hour) and carry no timestamp, so they
    cannot even be used to time anything. Errors, POSTs and other verbs still log.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) == 5:
            method, status = args[1], args[4]
            if method == 'GET' and isinstance(status, int) and status < 400:
                return False
        return True


LOG_ROTATE_BYTES = 16 * 1024 * 1024        # one log file at most this big, plus one rotated copy: 32 MB in all
LOG_CHECK_SECONDS = 60.0


def rotate_log_by_size(path: Path, limit: int = LOG_ROTATE_BYTES) -> bool:
    """Keep the service log bounded while the workers are running.

    The app opens `hub-worker.log` once and hands the descriptor to the Hub and every worker as
    stdout/stderr (append mode), so the file cannot be renamed away from under them - the app's own
    rotation only happens when it spawns the Hub, and a Mac that stays up for weeks wrote 4-14 GB a
    month. So it is copy-truncated: the live content moves to `<log>.1` (replacing the older copy) and
    the live file is emptied in place; appending writers carry on at the new end. A few lines written
    between the copy and the truncate are lost, which is the price of not needing a restart.
    """
    import fcntl
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size < limit:
            return False
        with open(path, 'rb') as live:
            try:
                fcntl.flock(live, fcntl.LOCK_EX | fcntl.LOCK_NB)   # one worker rotates; the others skip
            except OSError:
                return False
            if os.fstat(live.fileno()).st_size < limit:            # someone else just did it
                return False
            previous = path.with_name(path.name + '.1')
            temporary = path.with_name(path.name + '.1.rotating')
            with open(temporary, 'wb') as copy:
                os.fchmod(copy.fileno(), 0o600)
                while chunk := live.read(1024 * 1024):
                    copy.write(chunk)
            os.replace(temporary, previous)
            os.truncate(path, 0)
        return True
    except OSError:
        return False


def _log_path_of_stderr() -> Path | None:
    """The file stderr is writing to, when it is a regular file (macOS F_GETPATH); None for a terminal or pipe."""
    import fcntl
    try:
        if not stat.S_ISREG(os.fstat(2).st_mode):
            return None
        raw = fcntl.fcntl(2, getattr(fcntl, 'F_GETPATH', 50), b'\0' * 1024)
        return Path(raw.split(b'\0', 1)[0].decode())
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def start_log_rotation(stop: threading.Event, path: Path | None = None,
                       limit: int = LOG_ROTATE_BYTES, every: float = LOG_CHECK_SECONDS) -> threading.Thread | None:
    """Check the service log's size once a minute for as long as this process runs."""
    target = path or _log_path_of_stderr()
    if target is None:
        return None
    def loop() -> None:
        while not stop.wait(every):
            rotate_log_by_size(target, limit)
    thread = threading.Thread(target=loop, daemon=True, name='studiohub-log-rotation')
    thread.start()
    return thread


def run_server(app, port, ready_file, version, parent_pid, worker_pgid, *, token=None, factory=False,
               shutdown_callback=None, host='127.0.0.1'):
    """Shared owned-process lifecycle for the private Voice worker and local Hub.

    ``host`` defaults to loopback: the Hub is reachable from this Mac only unless the operator
    explicitly asks for more. Binding beyond loopback is what makes a Mac a fleet node, and it
    is also the point at which the Hub stops being private, so it is never inferred.
    """
    monitor_stop = threading.Event()
    start_log_rotation(monitor_stop)
    parent_lost = threading.Event()
    readiness_lock = threading.Lock()
    parent_monitor = None
    if worker_pgid is not None:
        parent_monitor = threading.Thread(target=monitor_parent,
            args=(parent_pid, worker_pgid, ready_file, monitor_stop,
                  parent_lost, readiness_lock), daemon=True,
            name='studiohub-parent-monitor')
        parent_monitor.start()
    try:
        if ready_file is not None:
            ready_file.unlink(missing_ok=True)
        import socket as socket_module
        import uvicorn
        options = {'factory': True} if factory else {}
        hosts = bind_hosts(host)
        config = uvicorn.Config(app, host=hosts[0], port=port, log_level='info', **options)
        # After Config: it applies uvicorn's logging configuration in its constructor.
        logging.getLogger('uvicorn.access').addFilter(QuietReadAccessLog())
        # Bind every socket up front and keep them, so the reported port is exact and nothing
        # else can take it between selection and startup. Binding all of them before serving
        # also means a failure to bind one address stops the start rather than leaving a Hub
        # that is quietly reachable on fewer networks than the operator asked for.
        socks = []
        for entry in hosts:
            sock = socket_module.socket(socket_module.AF_INET, socket_module.SOCK_STREAM)
            sock.setsockopt(socket_module.SOL_SOCKET, socket_module.SO_REUSEADDR, 1)
            sock.bind((entry, port))
            sock.listen(2048)
            sock.set_inheritable(True)
            socks.append(sock)
            if port == 0:
                port = sock.getsockname()[1]
        os.environ['STUDIOHUB_BIND_HOST'] = ','.join(hosts)
        os.environ['STUDIOHUB_BIND_PORT'] = str(port)
        # The owner app always reaches its own Hub on loopback, which is always bound.
        client_host = LOOPBACK

        class ReadyServer(uvicorn.Server):
            async def startup(self, sockets=None):
                await super().startup(sockets=sockets)
                with readiness_lock:
                    if (ready_file is not None and not self.should_exit
                            and not parent_lost.is_set()):
                        write_ready_file(ready_file,
                            f'http://{client_host}:{port}', version, token=token)

            async def shutdown(self, sockets=None):
                try:
                    await super().shutdown(sockets=sockets)
                finally:
                    # Uvicorn re-raises SIGTERM after shutdown, before outer
                    # Python finally blocks. Dispose owned children here.
                    if shutdown_callback is not None:
                        shutdown_callback()
                    with readiness_lock:
                        if ready_file is not None:
                            ready_file.unlink(missing_ok=True)
                    if parent_monitor is not None and not parent_lost.is_set():
                        monitor_stop.set()

        ReadyServer(config).run(sockets=socks)
    finally:
        for sock in locals().get('socks', []) or []:
            try:
                sock.close()
            except OSError:
                pass
        if ready_file is not None:
            ready_file.unlink(missing_ok=True)
        if parent_monitor is not None:
            if not parent_lost.is_set():
                monitor_stop.set()
            parent_monitor.join(PARENT_KILL_GRACE_SECONDS + 1)


if __name__ == '__main__':
    main()
