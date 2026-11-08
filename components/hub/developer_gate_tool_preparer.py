"""Trusted composition seam between an accepted gate tool pin and bounded preparation.

The constructor receives the exact accepted ``studio.tool-manifest.v1`` object from
packaged authority (never a request field) and the packaged official-tools manifest path.
Each accepted pin must bind the raw SHA-256 of that packaged manifest; the adapter then
calls ``prepare_official_tool`` with a bounded HTTPS stream provider and one owned
``--version`` probe.

No caller command, argv, path or URL comes from the wire.  A probe whose exact process
release is unconfirmed raises; no ``process_release`` is ever claimed on an exception.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, BinaryIO, Callable, Mapping
from urllib.parse import urlsplit

from components.hub import developer_gate_contract as contract
from components.hub import developer_gate_runner as gate_runner
from components.hub import developer_gate_tools as tools_mod

# Only the official publisher hosts and their HTTPS asset redirects.  The GitHub release
# download redirects to a signed asset host; go.dev redirects to dl.google.com.
TRUSTED_DOWNLOAD_HOSTS = frozenset({
    "go.dev",
    "dl.google.com",
    "nodejs.org",
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
})
VERSION_ARGV = {
    "go": ("version",),
    "node": ("--version",),
    # The tool itself is isolated, not only the launcher that execs it.
    "python": ("-I", "-B", "--version"),
}
PROBE_OUTPUT_CAP_BYTES = 64 * 1024
PROBE_SAMPLE_SECONDS = 0.05
CHECKPOINT_RESOURCE_SECONDS = 0.5
_VERSION_TOKEN = re.compile(
    r"(?<![0-9A-Za-z._+-])(?:go|v)?([0-9]+(?:\.[0-9]+){1,3}(?:[-+][0-9A-Za-z.-]+)?)"
)

# One indirection so a test can state the free-space answer without a real disk.
_disk_usage = shutil.disk_usage


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _refuse(message: str, *, code: str = "invalid_request", status: int = 400) -> None:
    raise tools_mod.OfficialToolError(message, status=status, code=code)


class _TrustedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow HTTPS redirects only on the narrow official host set."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _require_trusted_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _require_trusted_url(raw: str) -> None:
    parts = urlsplit(raw)
    try:
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError as exc:
        _refuse(f"Download URL is malformed: {exc}", code="integrity_error")
    if parts.scheme != "https" or host not in TRUSTED_DOWNLOAD_HOSTS:
        _refuse(
            f"Download URL host {host or raw!r} is not a trusted official HTTPS host",
            code="integrity_error")
    if port not in (None, 443) or parts.username or parts.password:
        _refuse("Download URL must use the default HTTPS port and no credentials",
                code="integrity_error")


def _open_trusted_stream(url: str, timeout_seconds: float) -> BinaryIO:
    """Bounded stream from a pinned official URL; no proxy, cookie or auth header."""
    _require_trusted_url(url)
    timeout = float(timeout_seconds)
    if not math.isfinite(timeout) or timeout <= 0:
        _refuse("Download timeout is not a positive finite number", code="bounds", status=413)
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _TrustedRedirectHandler())
    request = urllib.request.Request(url, headers={"User-Agent": "StudioHub-DeveloperGate/1"})
    try:
        return opener.open(request, timeout=timeout)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        _refuse(f"Official download did not open: {exc}", code="download_failed", status=502)


class _BoundedSink:
    def __init__(self) -> None:
        self._data = bytearray()
        self._lock = threading.Lock()
        self.overflow = False
        self.failed = False

    def feed(self, chunk: bytes) -> None:
        with self._lock:
            if self.overflow:
                return
            if len(self._data) + len(chunk) > PROBE_OUTPUT_CAP_BYTES:
                self.overflow = True
                return
            self._data.extend(chunk)

    def text(self) -> str:
        with self._lock:
            return bytes(self._data).decode("utf-8", "replace")


def _drain(pipe: Any, sink: _BoundedSink) -> None:
    try:
        while True:
            chunk = pipe.read(4096)
            if not chunk:
                break
            sink.feed(chunk)
    except Exception:  # noqa: BLE001 - an unreadable probe output is a failure
        sink.failed = True
    finally:
        try:
            pipe.close()
        except Exception:  # noqa: BLE001
            pass


def _parse_version(stdout: str) -> tuple[str, str]:
    match = _VERSION_TOKEN.search(stdout)
    if match is None:
        _refuse("Version probe output has no exact version", code="integrity_error")
    version = match.group(1)
    line = next(
        (item.strip() for item in stdout.splitlines() if match.group(0) in item), version)
    return version, line[:512]


class OfficialToolPreparer:
    """Callable ``tool_preparer`` for ``DeveloperGateService``.

    ``tool_manifest`` is the accepted, packaged manifest object (not request data).
    ``metrics`` is the owned process/resource reader; ``manifest_path`` defaults to the
    packaged official-tools manifest.
    """

    def __init__(self, tool_manifest: Mapping[str, Any], *, metrics: Any,
                 manifest_path: Path | str | None = None) -> None:
        if not isinstance(tool_manifest, Mapping):
            _refuse("Accepted tool manifest must be an object")
        try:
            contract.validate_schema(dict(tool_manifest), "toolManifest")
        except contract.GateContractError as exc:
            raise tools_mod.OfficialToolError(
                f"Accepted tool manifest is not schema-valid: {exc}", code=exc.code) from exc
        self._manifest_path = (
            Path(manifest_path) if manifest_path is not None else tools_mod.DEFAULT_MANIFEST_PATH)
        try:
            raw = self._manifest_path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            self._official = tools_mod.load_official_tools_manifest(self._manifest_path)
            if hashlib.sha256(self._manifest_path.read_bytes()).hexdigest() != digest:
                _refuse("Official tools manifest changed while being read", code="integrity_error")
        except OSError as exc:
            _refuse(f"Official tools manifest is unavailable: {exc}", code="integrity_error")
        self._metrics = metrics
        self._manifest_sha256 = digest
        self._stop_grace = gate_runner.DEFAULT_STOP_GRACE_SECONDS
        self._limits = self._official["limits"]
        self._min_free_bytes = int(self._limits["min_free_bytes"])
        self._pins: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        for pin in tool_manifest["tools"]:
            if pin["manifest_sha256"] != digest:
                _refuse(
                    f"Tool pin {pin['id']!r} does not bind the packaged manifest raw hash",
                    code="integrity_error")
            artifact = next(
                (item for item in self._official["artifacts"] if item["id"] == pin["id"]), None)
            if artifact is None:
                _refuse(f"Tool {pin['id']!r} has no packaged official artifact",
                        code="integrity_error")
            if artifact["version"] != pin["version"]:
                _refuse(
                    f"Tool pin {pin['id']!r} version {pin['version']!r} does not match "
                    f"packaged {artifact['version']!r}", code="integrity_error")
            self._pins[pin["id"]] = (dict(pin), artifact)

    def __call__(
        self,
        tool_id: str,
        *,
        attempt_root: Path | str,
        tools_root: Path | str,
        identity_recorder: Callable[[dict[str, Any]], None],
        stage_allowed: Callable[[], bool],
        preempt_check: Callable[[], bool],
        resource_safe: Callable[[], bool],
        deadline_monotonic: float,
        limits: Mapping[str, Any],
    ) -> dict[str, Any]:
        if tool_id not in self._pins:
            _refuse(f"Tool {tool_id!r} is not in the accepted packaged manifest")
        pin, artifact = self._pins[tool_id]
        accepted = self._accepted_limits(limits)
        attempt_root = Path(attempt_root)
        tools_root = Path(tools_root)
        self._require_manifest_unchanged()
        probe_release: dict[str, Any] = {}
        checkpoint = self._checkpoint(
            tools_root, stage_allowed, preempt_check, resource_safe, float(deadline_monotonic))

        def probe(executable: Path, timeout_seconds: float) -> dict[str, Any]:
            result = self._probe(
                tool_id, executable, timeout_seconds, attempt_root=attempt_root,
                identity_recorder=identity_recorder, stage_allowed=stage_allowed,
                preempt_check=preempt_check, resource_safe=resource_safe,
                deadline=float(deadline_monotonic), accepted=accepted)
            probe_release.update(result.pop("process_release_fact"))
            return result

        prepared = tools_mod.prepare_official_tool(
            tool_id,
            tools_root=tools_root,
            attempt_root=attempt_root,
            checkpoint=checkpoint,
            open_stream=_open_trusted_stream,
            probe=probe,
            manifest_path=self._manifest_path,
            deadline_monotonic=float(deadline_monotonic),
        )
        self._require_manifest_unchanged()
        fact = {
            "id": tool_id,
            "pin": dict(pin),
            "available": True,
            "observed_at": _now_iso(),
            "installed_bytes": int(prepared["footprint"]["allocated_bytes"]),
            "provenance": "owned-toolchain-readback",
            "reason": None,
        }
        try:
            contract.validate_schema(fact, "toolFact")
            contract.validate_schema(dict(probe_release), "processRelease")
        except contract.GateContractError as exc:
            _refuse(f"Prepared tool facts are not schema-valid: {exc}", code="integrity_error")
        return {
            "executable": prepared["paths"]["executable"],
            "actual_toolfacts": fact,
            "process_release": dict(probe_release),
        }

    def _require_manifest_unchanged(self) -> None:
        try:
            digest = hashlib.sha256(self._manifest_path.read_bytes()).hexdigest()
        except OSError as exc:
            _refuse(f"Official tools manifest became unreadable: {exc}", code="integrity_error")
        if digest != self._manifest_sha256:
            _refuse("Official tools manifest changed after acceptance", code="integrity_error")

    @staticmethod
    def _accepted_limits(limits: Mapping[str, Any]) -> dict[str, int]:
        """Bind the probe to the accepted request limits, never a guessed value."""
        if not isinstance(limits, Mapping):
            _refuse("Accepted request limits are required", code="invalid_request")
        accepted: dict[str, int] = {}
        for key in ("runtime_seconds", "workspace_bytes", "max_process_tree_rss_bytes"):
            value = limits.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                _refuse(f"Accepted limit {key} is not a positive integer", code="invalid_request")
            accepted[key] = value
        return accepted

    def _checkpoint(self, tools_root: Path, stage_allowed: Callable[[], bool],
                    preempt_check: Callable[[], bool], resource_safe: Callable[[], bool],
                    deadline: float) -> Callable[[str], None]:
        last_resource = [0.0]

        def checkpoint(stage: str) -> None:
            if not stage_allowed() or preempt_check():
                _refuse(f"Preparation stage {stage!r} is no longer allowed",
                        code="stage_refused", status=409)
            now = time.monotonic()
            if now >= deadline:
                _refuse(f"Preparation deadline exceeded at stage {stage!r}",
                        code="bounds", status=413)
            # Resource/free-floor reads are throttled: extraction checkpoints every member.
            if now - last_resource[0] < CHECKPOINT_RESOURCE_SECONDS:
                return
            last_resource[0] = now
            if not resource_safe():
                _refuse(f"Fresh resources are unsafe at stage {stage!r}",
                        code="resource_unknown", status=507)
            free = int(_disk_usage(tools_root).free)
            if free < self._min_free_bytes:
                _refuse(
                    f"Free space {free} bytes is below the required {self._min_free_bytes}",
                    code="bounds", status=507)

        return checkpoint

    def _probe(self, tool_id: str, executable: Path, timeout_seconds: float, *,
               attempt_root: Path, identity_recorder: Callable[[dict[str, Any]], None],
               stage_allowed: Callable[[], bool], preempt_check: Callable[[], bool],
               resource_safe: Callable[[], bool], deadline: float,
               accepted: Mapping[str, int]) -> dict[str, Any]:
        if tool_id not in VERSION_ARGV:
            _refuse(f"Tool {tool_id!r} has no packaged version probe", code="unsupported")
        remaining = float(timeout_seconds)
        if not math.isfinite(remaining) or remaining <= 0:
            _refuse("Version probe deadline is already expired", code="bounds", status=413)
        # The accepted runtime bound is real even when the preparation deadline is longer.
        probe_deadline = min(deadline, time.monotonic() + remaining,
                             time.monotonic() + accepted["runtime_seconds"])
        if not stage_allowed() or preempt_check():
            _refuse("Version probe was preempted before spawn", code="stage_refused", status=409)
        if not resource_safe():
            _refuse("Fresh resources are unsafe before probe spawn",
                    code="resource_unknown", status=507)
        if time.monotonic() >= probe_deadline:
            _refuse("Version probe deadline expired before spawn", code="bounds", status=413)
        env = gate_runner._build_sanitized_env(attempt_root / "probe-env", {})
        scratch_baseline = gate_runner._measure_dir_bytes(attempt_root)
        argv = [str(executable), *VERSION_ARGV[tool_id]]
        try:
            process = subprocess.Popen(
                [sys.executable, "-I", "-B", "-c", gate_runner._LAUNCHER_SOURCE, *argv],
                cwd=str(attempt_root), env=env, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                start_new_session=True, close_fds=True)
        except OSError as exc:
            _refuse(f"Version probe could not start: {exc}", code="probe_failed", status=500)

        identity: dict[str, Any] | None = None
        known: dict[tuple[int, float], dict[str, Any]] = {}
        drain: threading.Thread | None = None
        sink = _BoundedSink()
        confirmed = False
        last_workspace = [0.0]
        try:
            identity = self._await_identity(process.pid)
            identity_recorder(identity)
            if not stage_allowed() or preempt_check():
                _refuse("Version probe was preempted before authorization",
                        code="stage_refused", status=409)
            if not resource_safe():
                _refuse("Fresh resources are unsafe before probe authorization",
                        code="resource_unknown", status=507)
            if time.monotonic() >= probe_deadline:
                _refuse("Version probe deadline expired before authorization",
                        code="bounds", status=413)
            if int(_disk_usage(attempt_root).free) < self._min_free_bytes:
                _refuse("Free space is below the required floor before probe authorization",
                        code="bounds", status=507)
            if (gate_runner._measure_dir_bytes(attempt_root) - scratch_baseline
                    > accepted["workspace_bytes"]):
                _refuse("Owned probe scratch exceeds the accepted workspace bound",
                        code="bounds", status=413)
            drain = threading.Thread(target=_drain, args=(process.stdout, sink), daemon=True)
            drain.start()
            try:
                process.stdin.write(b"G")
                process.stdin.flush()
            finally:
                try:
                    process.stdin.close()
                except Exception:  # noqa: BLE001
                    pass
            while process.poll() is None:
                now = time.monotonic()
                if now >= probe_deadline:
                    _refuse("Version probe deadline exceeded", code="bounds", status=413)
                if sink.overflow:
                    _refuse("Version probe output exceeded its ceiling", code="bounds", status=413)
                if sink.failed:
                    _refuse("Version probe output became unreadable", code="probe_failed", status=500)
                if not stage_allowed() or preempt_check():
                    _refuse("Version probe was preempted", code="stage_refused", status=409)
                if not resource_safe():
                    _refuse("Version probe resources became unsafe",
                            code="resource_unknown", status=507)
                try:
                    rss, sampled = self._metrics.tree(process.pid)
                except Exception as exc:  # noqa: BLE001 - unreadable is never a pass
                    if process.poll() is not None:
                        break
                    _refuse(f"Owned process tree is unreadable: {exc}",
                            code="probe_failed", status=500)
                if rss > accepted["max_process_tree_rss_bytes"]:
                    _refuse("Version probe process tree exceeds the accepted RSS bound",
                            code="bounds", status=413)
                for item in sampled:
                    pid = int(item.get("pid", -1))
                    create_time = float(item.get("create_time", 0.0))
                    if pid > 1 and create_time > 0:
                        known[(pid, create_time)] = {"pid": pid, "create_time": create_time}
                if now - last_workspace[0] >= CHECKPOINT_RESOURCE_SECONDS:
                    last_workspace[0] = now
                    if (gate_runner._measure_dir_bytes(attempt_root) - scratch_baseline
                            > accepted["workspace_bytes"]):
                        _refuse("Owned probe scratch exceeds the accepted workspace bound",
                                code="bounds", status=413)
                time.sleep(PROBE_SAMPLE_SECONDS)
            proof = gate_runner.stop_exact_identity(
                identity, metrics=self._metrics, clock=time.monotonic,
                grace=self._stop_grace, known_pids=list(known.values()),
                reap=process.poll)
            if not proof.confirmed:
                _refuse(
                    f"Version probe release is unconfirmed: {proof.reason or 'unknown'}",
                    code="probe_release_unknown", status=500)
            confirmed = True
            drain.join(timeout=2.0)
            if drain.is_alive():
                _refuse("Version probe output drain did not finish after release",
                        code="probe_failed", status=500)
            if sink.overflow:
                _refuse("Version probe output exceeded its ceiling", code="bounds", status=413)
            if sink.failed:
                _refuse("Version probe output became unreadable", code="probe_failed", status=500)
            if (gate_runner._measure_dir_bytes(attempt_root) - scratch_baseline
                    > accepted["workspace_bytes"]):
                _refuse("Owned probe scratch exceeds the accepted workspace bound",
                        code="bounds", status=413)
            stdout = sink.text()
            version, version_line = _parse_version(stdout)
            return {
                "exit_code": process.returncode,
                "process_release": True,
                "version": version,
                "version_line": version_line,
                "stdout": stdout[:PROBE_OUTPUT_CAP_BYTES],
                "process_release_fact": {
                    "confirmed": True,
                    "observed_at": proof.confirmed_at or _now_iso(),
                    "owned_processes_remaining": 0,
                    "proof": "owned-process-identities-exited",
                },
            }
        finally:
            if not confirmed:
                if identity is not None:
                    gate_runner.stop_exact_identity(
                        identity, metrics=self._metrics, clock=time.monotonic,
                        grace=self._stop_grace, known_pids=list(known.values()),
                        reap=process.poll)
                else:
                    self._reap_unknown(process)
            if drain is not None:
                drain.join(timeout=2.0)
            if drain is None or not drain.is_alive():
                for pipe in (process.stdin, process.stdout):
                    try:
                        pipe.close()
                    except Exception:  # noqa: BLE001
                        pass

    @staticmethod
    def _reap_unknown(process: subprocess.Popen) -> None:
        """No verified identity: never signal a group; only this owned launcher is reaped."""
        try:
            process.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            process.wait(timeout=1.0)
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            process.kill()
        except Exception:  # noqa: BLE001
            pass
        try:
            process.wait(timeout=1.0)
        except Exception:  # noqa: BLE001
            pass


    def _await_identity(self, pid: int) -> dict[str, Any]:
        try:
            pgid = os.getpgid(pid)
        except OSError as exc:
            _refuse(f"Probe process group is unreadable: {exc}", code="probe_failed", status=500)
        probed = None
        for _ in range(25):
            probed = self._metrics.identity(pid)
            if probed is not None:
                break
            time.sleep(0.02)
        create_time = float(probed["create_time"]) if probed else 0.0
        if pgid != pid or create_time <= 0:
            _refuse("Probe identity could not be verified before authorization",
                    code="probe_failed", status=500)
        return {"pid": pid, "pgid": pgid, "create_time": create_time}
