"""Bounded Library portable recipe runner for developer gates.

Executes ONLY the 6 packaged Library portable commands from the exact immutable
fixture. Never allows caller-chosen argv/env/URL. Enforces single-process execution,
nice 15, GOMAXPROCS=1, GOTOOLCHAIN=local, sanitized environment allowlists,
production preemption checks, exact-process tree stops, and bounded redacted logs.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

try:
    from .developer_image_runner import (
        Metrics,
        MetricUnavailable,
        ReleaseProof,
        _BoundedLogSink,
        _drain_child_log,
        stop_exact_identity,
    )
except ImportError:
    from components.hub.developer_image_runner import (
        Metrics,
        MetricUnavailable,
        ReleaseProof,
        _BoundedLogSink,
        _drain_child_log,
        stop_exact_identity,
    )

RUNNER_ID = "studio.app-gate-runner.v1"
RUNNER_REVISION = "studio.app-gate-runner.v1+2026-11-08.1"
SCOPE = "portable-mocked-checks-only"
EXPECTED_COMMAND_FIXTURE_SHA256 = (
    "d0b8658d783c7680a96ad0c12526d3ab2ce2ae12c87edb733be20f25cbb3d9df"
)
EXPECTED_SOURCE_COMMIT = "d9c950547cb119c6aa57c54f9e2014a678593692"
EXPECTED_SOURCE_SHA256 = (
    "103a4d5d882726c035f12e5d0b21587d6d8f2da74c51f4583c25e5c41cf6a8b2"
)
COMMANDS_FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "packaging"
    / "developer-gates"
    / "library-portable.commands.json"
)

DEFAULT_LOG_CAP_BYTES = 4 * 1024 * 1024
DEFAULT_STOP_GRACE_SECONDS = 5.0
DEFAULT_SAMPLE_INTERVAL_SECONDS = 0.1
# Owner floor on the workspace volume; a request may raise it, never lower or disable it.
MINIMUM_FREE_BYTES = 10 * 1024**3
# Frozen contract maximum for integer byte limits.
MAX_SAFE_INTEGER = 9007199254740991


def _default_free_bytes(path: Path) -> int:
    """Actual filesystem free bytes on the volume holding ``path`` (not workspace bytes)."""
    return shutil.disk_usage(path).free


def _sample_free_bytes(sampler: Callable[[Path], int], path: Path) -> int:
    """Read one free-space sample, failing closed on any unknown or invalid value."""
    try:
        value = sampler(path)
    except Exception as exc:  # noqa: BLE001 - an unreadable floor is never enforced
        raise GateRunnerError(
            f"filesystem free-space sample unavailable: {exc}", code="metric_unavailable") from exc
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise GateRunnerError(
            f"invalid filesystem free-space sample: {value!r}", code="metric_unavailable")
    return value


def _required_free_bytes(limits: Mapping[str, Any]) -> int:
    """The live floor: the 10 GiB owner minimum or the request's higher value."""
    raw = limits.get("minimum_free_bytes", 0)
    if (isinstance(raw, bool) or not isinstance(raw, int)
            or raw < 0 or raw > MAX_SAFE_INTEGER):
        raise GateRunnerError(
            "minimum_free_bytes must be an integer between 0 and 9007199254740991",
            code="invalid_request")
    return max(MINIMUM_FREE_BYTES, raw)


class GateRunnerError(Exception):
    """Raised when runner configuration or pre-execution check fails."""

    def __init__(self, message: str, *, code: str = "internal_error", detail: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.detail = detail or message


@dataclass(frozen=True)
class GateRunResult:
    """Outputs produced by the runner for downstream result archive construction."""

    outcome: str
    exit_code: int | None
    termination_signal: int | None
    reason: dict[str, str] | None
    checks: list[dict[str, Any]]
    check_logs: dict[str, bytes]
    process_release: dict[str, Any]
    started_at: str | None
    finished_at: str
    scope: str
    peak_workspace_bytes: int | None
    before: dict[str, Any] | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "exit_code": self.exit_code,
            "termination_signal": self.termination_signal,
            "reason": self.reason,
            "checks": self.checks,
            "process_release": self.process_release,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "scope": self.scope,
            "peak_workspace_bytes": self.peak_workspace_bytes,
            "before": self.before,
        }


def _utcnow() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _measure_dir_bytes(target: Path) -> int:
    total = 0
    try:
        for entry in os.scandir(target):
            if entry.is_symlink():
                continue
            if entry.is_file():
                total += entry.stat().st_size
            elif entry.is_dir():
                total += _measure_dir_bytes(Path(entry.path))
    except (OSError, ValueError):
        pass
    return total


# Stdlib-only launcher: blocks until the parent writes the single authorization byte G,
# then lowers priority, detaches stdin and execs the fixed trusted argv. EOF before G exits
# without executing the tool. argv comes only from the verified fixture; the pipe never
# carries a command.
_LAUNCHER_SOURCE = (
    "import os,sys\n"
    "if sys.stdin.buffer.read(1)!=b'G': sys.exit(1)\n"
    "try: os.nice(15)\n"
    "except OSError: pass\n"
    "_fd=os.open(os.devnull,os.O_RDONLY)\n"
    "os.dup2(_fd,0)\n"
    "os.close(_fd)\n"
    "try: os.execv(sys.argv[1],sys.argv[1:])\n"
    "except OSError: sys.exit(1)\n"
)


def load_commands_fixture(path: Path | None = None) -> dict[str, Any]:
    """Load and verify the exact packaged commands fixture."""
    fixture_file = path or COMMANDS_FIXTURE_PATH
    if not fixture_file.is_file():
        raise GateRunnerError(f"Commands fixture file not found: {fixture_file}")
    raw = fixture_file.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != EXPECTED_COMMAND_FIXTURE_SHA256:
        raise GateRunnerError(
            f"Commands fixture SHA-256 mismatch: {digest} != {EXPECTED_COMMAND_FIXTURE_SHA256}",
            code="integrity_error",
        )
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise GateRunnerError(f"Commands fixture JSON malformed: {exc}", code="integrity_error") from exc
    if not isinstance(data, dict) or "commands" not in data or len(data["commands"]) != 6:
        raise GateRunnerError("Commands fixture does not declare the expected 6 commands", code="integrity_error")
    return data


def validate_trusted_toolchains(
    trusted: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Validate that required toolchains (python, node, go) are explicitly trusted and executable.

    Never searches PATH and never fabricates requested versions.
    """
    required = ("python", "node", "go")
    validated: dict[str, dict[str, Any]] = {}
    for name in required:
        info = trusted.get(name)
        if info is None and name == "python":
            info = trusted.get("python3")
        if info is None:
            raise GateRunnerError(
                f"Required toolchain {name!r} not in trusted executable map",
                code="toolchain_unavailable",
            )
        if isinstance(info, dict):
            available = bool(info.get("available", False))
            exe = info.get("executable") or info.get("path")
            version = str(info.get("version") or "")
        else:
            available = bool(getattr(info, "available", False))
            exe = getattr(info, "executable", None) or getattr(info, "path", None)
            version = str(getattr(info, "version", "") or "")

        if not available:
            raise GateRunnerError(f"Toolchain {name!r} is unavailable", code="toolchain_unavailable")
        if not exe:
            raise GateRunnerError(f"Toolchain {name!r} has no executable path", code="toolchain_unavailable")
        exe_path = Path(exe).resolve()
        if not exe_path.is_file() or not os.access(str(exe_path), os.X_OK):
            raise GateRunnerError(
                f"Toolchain {name!r} path is not an executable file: {exe_path}",
                code="toolchain_unavailable",
            )
        validated[name] = {"path": exe_path, "version": version, "available": True}
    return validated


def _build_command_argv(cmd_spec: dict[str, Any], tools: dict[str, dict[str, Any]]) -> list[str]:
    raw_argv = list(cmd_spec["argv"])
    lead = raw_argv[0]
    if lead in ("python", "python3"):
        tool = tools["python"]["path"]
        rest = [a for a in raw_argv[1:] if a not in ("-I", "-B")]
        return [str(tool), "-I", "-B"] + rest
    if lead == "node":
        tool = tools["node"]["path"]
        return [str(tool)] + raw_argv[1:]
    if lead == "go":
        tool = tools["go"]["path"]
        return [str(tool)] + raw_argv[1:]
    raise GateRunnerError(f"Unsupported tool executable in fixture: {lead!r}", code="unsupported")


def _build_sanitized_env(workspace_dir: Path, cmd_spec: dict[str, Any]) -> dict[str, str]:
    home = workspace_dir / "home"
    tmp = workspace_dir / "tmp"
    cache = workspace_dir / "cache"
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp.mkdir(parents=True, exist_ok=True, mode=0o700)
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)

    clean_env = {
        "HOME": str(home),
        "TMPDIR": str(tmp),
        "XDG_CACHE_HOME": str(cache),
        "GOTOOLCHAIN": "local",
        "GOMAXPROCS": "1",
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "LC_ALL": "C.UTF-8",
        "LANG": "C.UTF-8",
    }
    if "environment" in cmd_spec and isinstance(cmd_spec["environment"], dict):
        for k, v in cmd_spec["environment"].items():
            if k in ("GOOS", "GOARCH", "CGO_ENABLED"):
                clean_env[k] = str(v)
    return clean_env


class DeveloperGateRunner:
    """Bounded Library portable recipe runner."""

    def __init__(
        self,
        *,
        fixture_path: Path | None = None,
        metrics: Metrics | None = None,
        clock: Callable[[], float] = time.monotonic,
        stop_grace_seconds: float = DEFAULT_STOP_GRACE_SECONDS,
        sample_interval_seconds: float = DEFAULT_SAMPLE_INTERVAL_SECONDS,
        disk_free_bytes: Callable[[Path], int] | None = None,
    ) -> None:
        self.fixture = load_commands_fixture(fixture_path)
        self.metrics = metrics or Metrics()
        self.clock = clock
        self.stop_grace_seconds = max(0.1, float(stop_grace_seconds))
        self.sample_interval = max(0.01, float(sample_interval_seconds))
        self.disk_free_bytes = disk_free_bytes or _default_free_bytes

    def run(
        self,
        *,
        workspace_dir: Path | str,
        source_dir: Path | str,
        trusted_toolchains: Mapping[str, Any],
        stage_allowed: Callable[[], bool],
        preempt_check: Callable[[], bool] | None = None,
        identity_recorder: Callable[[dict[str, Any]], None],
        limits: Mapping[str, Any] | None = None,
        secrets: Iterable[bytes] = (),
        before: dict[str, Any] | None = None,
    ) -> GateRunResult:
        """Run the packaged 6 Library portable commands in strict sequence.

        Requires callable stage_allowed and exact owned identity recorder before
        any heavy child process starts.
        """
        if not callable(stage_allowed):
            raise GateRunnerError("stage_allowed check must be callable", code="invalid_request")
        if not callable(identity_recorder):
            raise GateRunnerError("identity_recorder must be callable", code="invalid_request")

        ws_path = Path(workspace_dir).resolve()
        src_path = Path(source_dir).resolve()
        if not ws_path.is_dir():
            raise GateRunnerError(f"Workspace directory does not exist: {ws_path}", code="invalid_request")

        started_at = _utcnow()
        lim = dict(limits or {})
        max_runtime = float(lim.get("runtime_seconds", 3600))
        max_rss = int(lim.get("max_process_tree_rss_bytes", 0)) or None
        max_swap = int(lim.get("max_system_swap_growth_bytes", 0)) or None
        max_workspace = int(lim.get("workspace_bytes", 0)) or None
        max_log = int(lim.get("log_bytes", DEFAULT_LOG_CAP_BYTES))

        # 1. Preflight toolchain verification
        try:
            tools = validate_trusted_toolchains(trusted_toolchains)
        except GateRunnerError as exc:
            return GateRunResult(
                outcome="not_run",
                exit_code=None,
                termination_signal=None,
                reason={"code": exc.code, "detail": exc.detail},
                checks=[],
                check_logs={},
                process_release={
                    "confirmed": True,
                    "observed_at": _utcnow(),
                    "owned_processes_remaining": 0,
                    "proof": "never_started",
                },
                started_at=None,
                finished_at=_utcnow(),
                scope=SCOPE,
                peak_workspace_bytes=_measure_dir_bytes(ws_path),
                before=before,
            )

        # 2. Preflight admission check
        if not stage_allowed() or (preempt_check and preempt_check()):
            return GateRunResult(
                outcome="interrupted",
                exit_code=None,
                termination_signal=None,
                reason={"code": "production_preempted", "detail": "cancelled before any command execution"},
                checks=[],
                check_logs={},
                process_release={
                    "confirmed": True,
                    "observed_at": _utcnow(),
                    "owned_processes_remaining": 0,
                    "proof": "never_started",
                },
                started_at=None,
                finished_at=_utcnow(),
                scope=SCOPE,
                peak_workspace_bytes=_measure_dir_bytes(ws_path),
                before=before,
            )

        # 3. Live free-disk admission on the workspace volume, before any launcher spawn.
        disk_reason: dict[str, str] | None = None
        disk_floor = MINIMUM_FREE_BYTES
        try:
            disk_floor = _required_free_bytes(lim)
            free_bytes = _sample_free_bytes(self.disk_free_bytes, ws_path)
        except GateRunnerError as exc:
            disk_reason = {"code": exc.code, "detail": exc.detail}
        else:
            if free_bytes < disk_floor:
                disk_reason = {
                    "code": "not_enough_disk",
                    "detail": f"filesystem free bytes {free_bytes} below required floor {disk_floor}",
                }
        if disk_reason is not None:
            return GateRunResult(
                outcome="not_run",
                exit_code=None,
                termination_signal=None,
                reason=disk_reason,
                checks=[],
                check_logs={},
                process_release={
                    "confirmed": True,
                    "observed_at": _utcnow(),
                    "owned_processes_remaining": 0,
                    "proof": "never_started",
                },
                started_at=None,
                finished_at=_utcnow(),
                scope=SCOPE,
                peak_workspace_bytes=_measure_dir_bytes(ws_path),
                before=before,
            )

        try:
            swap_baseline = self.metrics.swap_used_bytes()
        except MetricUnavailable as exc:
            # No exact identity is possible without metrics, so no child may be authorized.
            return GateRunResult(
                outcome="not_run",
                exit_code=None,
                termination_signal=None,
                reason={"code": "metrics_unavailable", "detail": f"required process metrics unavailable: {exc}"},
                checks=[],
                check_logs={},
                process_release={
                    "confirmed": True,
                    "observed_at": _utcnow(),
                    "owned_processes_remaining": 0,
                    "proof": "never_started",
                },
                started_at=None,
                finished_at=_utcnow(),
                scope=SCOPE,
                peak_workspace_bytes=_measure_dir_bytes(ws_path),
                before=before,
            )

        checks: list[dict[str, Any]] = []
        check_logs: dict[str, bytes] = {}
        all_release_confirmed = True
        overall_outcome = "passed"
        overall_exit_code: int | None = 0
        overall_signal: int | None = None
        overall_reason: dict[str, str] | None = None
        peak_workspace = _measure_dir_bytes(ws_path)

        cmd_list = self.fixture["commands"]
        total_start = self.clock()

        for idx, cmd_spec in enumerate(cmd_list):
            cid = cmd_spec["id"]
            if overall_outcome != "passed":
                # Previous command was non-terminal or failed; remaining are not_run
                checks.append({
                    "id": cid,
                    "outcome": "not_run",
                    "exit_code": None,
                    "duration_seconds": None,
                    "log_sha256": hashlib.sha256(b"").hexdigest(),
                    "log_bytes": 0,
                    "reason": {"code": "not_run", "detail": "skipped due to prior failure or preemption"},
                })
                check_logs[cid] = b""
                continue

            # Check preemption immediately before starting heavy child
            if not stage_allowed() or (preempt_check and preempt_check()):
                overall_outcome = "interrupted"
                overall_exit_code = None
                overall_reason = {"code": "production_preempted", "detail": "preempted before command launch"}
                checks.append({
                    "id": cid,
                    "outcome": "not_run",
                    "exit_code": None,
                    "duration_seconds": None,
                    "log_sha256": hashlib.sha256(b"").hexdigest(),
                    "log_bytes": 0,
                    "reason": dict(overall_reason),
                })
                check_logs[cid] = b""
                continue

            # Re-check the workspace volume immediately before this launcher spawn.
            try:
                free_bytes = _sample_free_bytes(self.disk_free_bytes, ws_path)
                disk_error = None if free_bytes >= disk_floor else {
                    "code": "not_enough_disk",
                    "detail": f"filesystem free bytes {free_bytes} below required floor {disk_floor}",
                }
            except GateRunnerError as exc:
                disk_error = {"code": exc.code, "detail": exc.detail}
            if disk_error is not None:
                # No launcher or tool ran, so this check keeps a null exit and duration.
                overall_outcome = (
                    "not_run" if all(c["outcome"] == "not_run" for c in checks) else "failed")
                overall_exit_code = None
                overall_reason = disk_error
                checks.append({
                    "id": cid,
                    "outcome": "not_run",
                    "exit_code": None,
                    "duration_seconds": None,
                    "log_sha256": hashlib.sha256(b"").hexdigest(),
                    "log_bytes": 0,
                    "reason": dict(overall_reason),
                })
                check_logs[cid] = b""
                continue

            argv = _build_command_argv(cmd_spec, tools)
            env = _build_sanitized_env(ws_path, cmd_spec)

            # Resolve cwd: extracted-source-root if exists, else source_dir
            extracted = ws_path / "extracted-source-root"
            cwd = extracted if extracted.is_dir() else (src_path if src_path.is_dir() else ws_path)

            sink = _BoundedLogSink(secrets, limit=max_log)
            process = None
            drain_thread = None
            stop_code: str | None = None
            stop_detail: str | None = None
            cmd_start = self.clock()

            try:
                process = subprocess.Popen(
                    [sys.executable, "-I", "-B", "-c", _LAUNCHER_SOURCE, *argv],
                    cwd=str(cwd),
                    env=env,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    close_fds=True,
                )
            except Exception as exc:
                overall_outcome = "failed"
                overall_exit_code = 1
                overall_reason = {"code": "internal_error", "detail": f"failed to spawn {cid}: {exc}"}
                checks.append({
                    "id": cid,
                    "outcome": "failed",
                    "exit_code": 1,
                    "duration_seconds": 0.0,
                    "log_sha256": hashlib.sha256(b"").hexdigest(),
                    "log_bytes": 0,
                    "reason": dict(overall_reason),
                })
                check_logs[cid] = b""
                continue

            # The launcher blocks on stdin until G, so no tool byte runs before the parent
            # verifies pid/pgid/create_time, records it durably and re-checks admission.
            pid = process.pid
            identity: dict[str, Any] | None = None
            auth_error: str | None = None
            auth_code = "release_unknown"
            try:
                pgid = os.getpgid(pid)
                probed = self.metrics.identity(pid)
                for _ in range(25):
                    if probed is not None:
                        break
                    time.sleep(0.02)
                    probed = self.metrics.identity(pid)
                create_time = float(probed["create_time"]) if probed else 0.0
                if pgid != pid or create_time <= 0:
                    auth_error = (
                        f"the exact identity of {cid} could not be verified before authorization; "
                        "the tool was not executed")
                else:
                    identity = {"pid": pid, "pgid": pgid, "create_time": create_time}
                    try:
                        identity_recorder(identity)
                    except Exception as exc:  # noqa: BLE001 - a failed recorder must stop the child
                        auth_code = "identity_unrecorded"
                        auth_error = f"identity recorder failed for {cid}: {exc}"
                    else:
                        # Identity is durable; measure the volume once more before G.
                        try:
                            free_bytes = _sample_free_bytes(self.disk_free_bytes, ws_path)
                        except GateRunnerError as exc:
                            auth_code = exc.code
                            auth_error = exc.detail
                        else:
                            if free_bytes < disk_floor:
                                auth_code = "not_enough_disk"
                                auth_error = (
                                    f"filesystem free bytes {free_bytes} below required floor "
                                    f"{disk_floor} before authorization")
                            elif not stage_allowed() or (preempt_check and preempt_check()):
                                auth_code = "production_preempted"
                                auth_error = "preempted before command authorization"
                            else:
                                drain_thread = threading.Thread(
                                    target=_drain_child_log, args=(process.stdout, sink), daemon=True)
                                drain_thread.start()
                                process.stdin.write(b"G")
                                process.stdin.flush()
            except Exception as exc:  # noqa: BLE001 - any failure before G closes the pipe
                auth_error = f"authorization failed for {cid}: {exc}"
            finally:
                try:
                    process.stdin.close()
                except Exception:
                    pass

            if auth_error is not None:
                if process.poll() is None:
                    try:
                        process.wait(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        try:
                            process.kill()  # exact owned child only; never an unverified group
                        except Exception:
                            pass
                        try:
                            process.wait(timeout=2.0)
                        except Exception:
                            pass
                if drain_thread is not None:
                    drain_thread.join(timeout=5.0)
                else:
                    try:
                        process.stdout.close()
                    except Exception:
                        pass
                if identity is not None:
                    proof = stop_exact_identity(
                        identity, metrics=self.metrics, clock=self.clock,
                        grace=self.stop_grace_seconds)
                    if not proof.confirmed:
                        all_release_confirmed = False
                else:
                    # Unknown identity: no signal is sent and release is never claimed.
                    all_release_confirmed = False
                raw_log = sink.body()
                if auth_code == "production_preempted":
                    overall_outcome = "interrupted"
                    overall_exit_code = None
                else:
                    overall_outcome = "failed"
                    overall_exit_code = 1
                overall_reason = {"code": auth_code, "detail": auth_error}
                checks.append({
                    "id": cid,
                    "outcome": "failed",
                    "exit_code": 1 if overall_exit_code is not None else None,
                    "duration_seconds": round(max(0.0, self.clock() - cmd_start), 6),
                    "log_sha256": hashlib.sha256(raw_log).hexdigest(),
                    "log_bytes": len(raw_log),
                    "reason": dict(overall_reason),
                })
                check_logs[cid] = raw_log
                continue

            known: dict[tuple[int, float], dict] = {}

            # Active monitoring loop
            while True:
                ret = process.poll()
                if ret is not None:
                    break
                now = self.clock()
                if not stage_allowed() or (preempt_check and preempt_check()):
                    stop_code = "production_preempted"
                    stop_detail = "preempted while executing"
                    break
                if (now - total_start) > max_runtime:
                    stop_code = "runtime_budget"
                    stop_detail = f"execution runtime exceeded limit {max_runtime}s"
                    break
                if sink.truncated:
                    stop_code = "log_budget"
                    stop_detail = f"command log exceeded limit {max_log} bytes"
                    break

                try:
                    rss, tree = self.metrics.tree(pid)
                    for item in tree:
                        known[(int(item["pid"]), float(item["create_time"]))] = item
                    if max_rss and rss > max_rss:
                        stop_code = "rss_budget"
                        stop_detail = f"process tree RSS {rss} exceeded limit {max_rss}"
                        break
                    swap_now = self.metrics.swap_used_bytes()
                    if max_swap and (swap_now - swap_baseline) > max_swap:
                        stop_code = "swap_budget"
                        stop_detail = f"swap growth {swap_now - swap_baseline} exceeded limit {max_swap}"
                        break
                except MetricUnavailable:
                    # A required limit must never be silently unenforced.
                    if (max_rss or max_swap) and process.poll() is None:
                        stop_code = "metrics_unavailable"
                        stop_detail = "required process metrics became unavailable"
                        break

                cur_ws = _measure_dir_bytes(ws_path)
                peak_workspace = max(peak_workspace, cur_ws)
                if max_workspace and cur_ws > max_workspace:
                    stop_code = "workspace_budget"
                    stop_detail = f"workspace usage {cur_ws} exceeded limit {max_workspace}"
                    break

                try:
                    free_bytes = _sample_free_bytes(self.disk_free_bytes, ws_path)
                except GateRunnerError as exc:
                    stop_code = exc.code
                    stop_detail = exc.detail
                    break
                if free_bytes < disk_floor:
                    stop_code = "not_enough_disk"
                    stop_detail = f"filesystem free bytes {free_bytes} below required floor {disk_floor}"
                    break

                time.sleep(self.sample_interval)

            # Command finished or stopped: enforce exact identity release
            if stop_code is not None:
                def _stop_proof():
                    return stop_exact_identity(
                        identity,
                        metrics=self.metrics,
                        clock=self.clock,
                        grace=self.stop_grace_seconds,
                        known_pids=list(known.values()),
                    )

                process.poll()  # reap a leader that already exited; a zombie has no live group
                proof = _stop_proof()
                if not proof.confirmed:
                    # The leader may have exited during the stop and become a zombie; reaping
                    # it lets the same exact primitive verify leader, group and descendants.
                    process.poll()
                    proof = _stop_proof()
                process.poll()  # sync Popen state with a child the exact stop already reaped
            else:
                try:
                    process.wait(timeout=1.0)
                except Exception:
                    pass
                proof = stop_exact_identity(
                    identity,
                    metrics=self.metrics,
                    clock=self.clock,
                    grace=self.stop_grace_seconds,
                    known_pids=list(known.values()),
                )

            drain_thread.join(timeout=5.0)
            if drain_thread.is_alive() and stop_code is None:
                # The log never reached EOF; a check cannot pass on an unverified log.
                stop_code = "log_budget"
                stop_detail = "command log drain did not finish"
            if sink.truncated and stop_code is None:
                # The child may exit before the sampling loop sees the overflow; the drained
                # sink is the authority, so a fast exit still fails the check.
                stop_code = "log_budget"
                stop_detail = f"command log exceeded limit {max_log} bytes"
            if stop_code is None:
                # A check is never reported passed without a final free-disk sample.
                try:
                    free_bytes = _sample_free_bytes(self.disk_free_bytes, ws_path)
                except GateRunnerError as exc:
                    stop_code = exc.code
                    stop_detail = exc.detail
                else:
                    if free_bytes < disk_floor:
                        stop_code = "not_enough_disk"
                        stop_detail = (
                            f"filesystem free bytes {free_bytes} below required floor "
                            f"{disk_floor} after completion")
            raw_log = sink.body()
            duration = max(0.0, self.clock() - cmd_start)

            if not proof.confirmed:
                all_release_confirmed = False

            # Evaluate check outcome
            if stop_code == "production_preempted":
                overall_outcome = "interrupted"
                overall_exit_code = None
                overall_reason = {"code": "production_preempted", "detail": stop_detail or "preempted"}
                checks.append({
                    "id": cid,
                    "outcome": "failed",
                    "exit_code": None,
                    "duration_seconds": round(duration, 6),
                    "log_sha256": hashlib.sha256(raw_log).hexdigest(),
                    "log_bytes": len(raw_log),
                    "reason": dict(overall_reason),
                })
            elif stop_code is not None:
                overall_outcome = "failed"
                overall_exit_code = 1
                overall_reason = {"code": stop_code, "detail": stop_detail or "budget violation"}
                checks.append({
                    "id": cid,
                    "outcome": "failed",
                    "exit_code": 1,
                    "duration_seconds": round(duration, 6),
                    "log_sha256": hashlib.sha256(raw_log).hexdigest(),
                    "log_bytes": len(raw_log),
                    "reason": dict(overall_reason),
                })
            elif not proof.confirmed:
                overall_outcome = "failed"
                overall_exit_code = 1
                detail = proof.reason.get("detail", "unconfirmed process release") if proof.reason else "unconfirmed"
                overall_reason = {"code": "release_unknown", "detail": detail}
                checks.append({
                    "id": cid,
                    "outcome": "failed",
                    "exit_code": 1,
                    "duration_seconds": round(duration, 6),
                    "log_sha256": hashlib.sha256(raw_log).hexdigest(),
                    "log_bytes": len(raw_log),
                    "reason": dict(overall_reason),
                })
            elif process.returncode == 0:
                checks.append({
                    "id": cid,
                    "outcome": "passed",
                    "exit_code": 0,
                    "duration_seconds": round(duration, 6),
                    "log_sha256": hashlib.sha256(raw_log).hexdigest(),
                    "log_bytes": len(raw_log),
                    "reason": None,
                })
            else:
                rc = process.returncode
                exit_c = rc if rc >= 0 else None
                sig = -rc if rc < 0 else None
                overall_outcome = "failed"
                overall_exit_code = exit_c if exit_c is not None else 1
                overall_signal = sig
                overall_reason = {"code": "command_failed", "detail": f"command {cid} exited with {rc}"}
                checks.append({
                    "id": cid,
                    "outcome": "failed",
                    "exit_code": exit_c,
                    "duration_seconds": round(duration, 6),
                    "log_sha256": hashlib.sha256(raw_log).hexdigest(),
                    "log_bytes": len(raw_log),
                    "reason": dict(overall_reason),
                })

            check_logs[cid] = raw_log

        # Release confirmation must never false-pass
        if not all_release_confirmed and overall_outcome == "passed":
            overall_outcome = "failed"
            overall_exit_code = 1
            overall_reason = {"code": "release_unknown", "detail": "process release could not be confirmed"}

        process_release = {
            "confirmed": bool(all_release_confirmed),
            "observed_at": _utcnow(),
            "owned_processes_remaining": 0 if all_release_confirmed else 1,
            "proof": "owned-process-identities-exited",
        }

        return GateRunResult(
            outcome=overall_outcome,
            exit_code=overall_exit_code,
            termination_signal=overall_signal,
            reason=overall_reason,
            checks=checks,
            check_logs=check_logs,
            process_release=process_release,
            started_at=started_at,
            finished_at=_utcnow(),
            scope=SCOPE,
            peak_workspace_bytes=peak_workspace,
            before=before,
        )


def run_gate(
    *,
    workspace_dir: Path | str,
    source_dir: Path | str,
    trusted_toolchains: Mapping[str, Any],
    stage_allowed: Callable[[], bool],
    preempt_check: Callable[[], bool] | None = None,
    identity_recorder: Callable[[dict[str, Any]], None],
    limits: Mapping[str, Any] | None = None,
    metrics: Metrics | None = None,
    clock: Callable[[], float] = time.monotonic,
    stop_grace_seconds: float = DEFAULT_STOP_GRACE_SECONDS,
    sample_interval_seconds: float = DEFAULT_SAMPLE_INTERVAL_SECONDS,
    secrets: Iterable[bytes] = (),
    before: dict[str, Any] | None = None,
    fixture_path: Path | None = None,
    disk_free_bytes: Callable[[Path], int] | None = None,
) -> GateRunResult:
    """Convenience functional entrypoint for running the developer gate."""
    runner = DeveloperGateRunner(
        fixture_path=fixture_path,
        metrics=metrics,
        clock=clock,
        stop_grace_seconds=stop_grace_seconds,
        sample_interval_seconds=sample_interval_seconds,
        disk_free_bytes=disk_free_bytes,
    )
    return runner.run(
        workspace_dir=workspace_dir,
        source_dir=source_dir,
        trusted_toolchains=trusted_toolchains,
        stage_allowed=stage_allowed,
        preempt_check=preempt_check,
        identity_recorder=identity_recorder,
        limits=limits,
        secrets=secrets,
        before=before,
    )
