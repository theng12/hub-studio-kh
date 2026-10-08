"""Bounded native Qwen Image leaf owned by the Image worker."""
from __future__ import annotations

import base64
from functools import lru_cache
from hashlib import sha256
import io
import json
import math
import os
from pathlib import Path
import queue
import secrets
import socket
import subprocess
import sys
from contextlib import contextmanager
from tempfile import mkdtemp
import threading
import time
import urllib.error
import urllib.request
from uuid import uuid4

from PIL import Image


MAX_RESPONSE_BYTES = 32 * 1024 * 1024
MAX_PNG_BYTES = 24 * 1024 * 1024
MAX_PIXELS = 1024 * 1024
REQUIRED_VERSION = "26.10.1"
VERSION_TIMEOUT_S = 10
_SAFE_ENV = ("LANG", "LC_ALL", "PATH", "TZ")
_AUTH_ENV = "STUDIOHUB_MLX_SERVE_LEAF_KEY"
_ACTIVE_LOCK = threading.Lock()
_ACTIVE_PROCESS: subprocess.Popen | None = None


class MlxServeFailure(RuntimeError):
    pass


class MlxServeCancelled(MlxServeFailure):
    pass


def runtime_binary() -> Path:
    return Path(sys.executable).parent.parent / "mlx-serve" / "mlx-serve"


@lru_cache(maxsize=4)
def _version_problem(binary: Path, modified_ns: int, size: int) -> str | None:
    del modified_ns, size
    env = {name: os.environ[name] for name in _SAFE_ENV if os.environ.get(name)}
    env["HOME"] = "/var/empty"
    try:
        checked = subprocess.run([str(binary), "--version"], capture_output=True, text=True,
                                 timeout=VERSION_TIMEOUT_S, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"mlx-serve version could not be verified: {exc}"
    version_line = next((line.strip() for line in checked.stdout.splitlines()
                         if line.strip().startswith("mlx-serve ")), "")
    if checked.returncode or version_line != f"mlx-serve {REQUIRED_VERSION}":
        return (f"Qwen Image requires mlx-serve {REQUIRED_VERSION}; found "
                f"{version_line or 'an unreadable version'}")
    return None


def runtime_problem() -> str | None:
    binary = runtime_binary()
    try:
        stat_result = binary.stat()
    except OSError:
        return f"mlx-serve is missing at {binary}"
    if not binary.is_file():
        return f"mlx-serve is missing at {binary}"
    if not os.access(binary, os.X_OK):
        return f"mlx-serve is not executable at {binary}"
    return _version_problem(binary, stat_result.st_mtime_ns, stat_result.st_size)


def _port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        return int(server.getsockname()[1])


def _request(url: str, key: str, body: dict, timeout: float) -> bytes:
    request = urllib.request.Request(
        url,
        data=json.dumps(body, separators=(",", ":")).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    # Loopback must never be handed to a proxy inherited by the Image worker.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=max(0.1, timeout)) as response:
        length = response.headers.get("Content-Length")
        if length is not None and int(length) > MAX_RESPONSE_BYTES:
            raise MlxServeFailure("mlx-serve response is too large")
        content = response.read(MAX_RESPONSE_BYTES + 1)
    if len(content) > MAX_RESPONSE_BYTES:
        raise MlxServeFailure("mlx-serve response is too large")
    return content


def _request_body(weights: Path, params: dict) -> dict:
    """Validate the direct runner boundary before the native process allocates memory."""
    prompt = params.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4000:
        raise MlxServeFailure("Qwen prompt must contain 1-4000 characters")
    width, height = params.get("width"), params.get("height")
    if (type(width) is not int or type(height) is not int or width <= 0 or height <= 0
            or width * height > MAX_PIXELS):
        raise MlxServeFailure("Qwen request exceeds the one-megapixel limit")
    steps = params.get("steps")
    if type(steps) is not int or not 1 <= steps <= 50:
        raise MlxServeFailure("Qwen steps must be an integer from 1 to 50")
    guidance = params.get("guidance")
    if (isinstance(guidance, bool) or not isinstance(guidance, (int, float))
            or not math.isfinite(guidance) or not 0 <= guidance <= 20):
        raise MlxServeFailure("Qwen guidance must be a finite number from 0 to 20")
    seed = params.get("seed")
    if type(seed) is not int or not 0 <= seed <= 2_147_483_647:
        raise MlxServeFailure("Qwen seed must be a signed 31-bit non-negative integer")
    return {
        "model": str(weights), "prompt": prompt, "size": f"{width}x{height}",
        "steps": steps, "guidance_scale": float(guidance), "seed": seed,
        "n": 1, "response_format": "b64_json",
    }


def _png_from_response(content: bytes, expected: tuple[int, int]) -> bytes:
    try:
        payload = json.loads(content)
        data = payload["data"]
        item = data[0] if isinstance(data, list) and len(data) == 1 else None
        if isinstance(item, dict) and item.get("url"):
            raise MlxServeFailure("mlx-serve artifact URLs are not accepted")
        encoded = item.get("b64_json") if isinstance(item, dict) else None
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise MlxServeFailure("mlx-serve returned malformed JSON") from exc
    if not isinstance(encoded, str) or not encoded:
        raise MlxServeFailure("mlx-serve must return one base64 image")
    if len(encoded) > ((MAX_PNG_BYTES + 2) // 3) * 4 + 4:
        raise MlxServeFailure("mlx-serve PNG is too large")
    try:
        png = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise MlxServeFailure("mlx-serve returned invalid base64") from exc
    if len(png) > MAX_PNG_BYTES or not png.startswith(b"\x89PNG\r\n\x1a\n"):
        raise MlxServeFailure("mlx-serve returned an invalid PNG")
    try:
        with Image.open(io.BytesIO(png)) as image:
            if image.format != "PNG" or image.size != expected:
                raise MlxServeFailure(
                    f"mlx-serve returned dimensions {image.size}, expected {expected}"
                )
            if image.width * image.height > MAX_PIXELS:
                raise MlxServeFailure("mlx-serve PNG exceeds the pixel limit")
            image.verify()
    except MlxServeFailure:
        raise
    except Exception as exc:  # Pillow normalizes parser-specific failures here.
        raise MlxServeFailure("mlx-serve returned an invalid PNG") from exc
    return png


def _stop(process: subprocess.Popen, timeout_s: float = 5) -> None:
    if process.poll() is None:
        try:
            process.terminate()
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=max(0.0, timeout_s))
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except ProcessLookupError:
                pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        process.wait(timeout=5)


def stop_active_leaf(timeout_s: float = 5) -> None:
    """Stop the worker-owned leaf during Image lifespan shutdown."""
    with _ACTIVE_LOCK:
        process = _ACTIVE_PROCESS
    if process is not None:
        _stop(process, timeout_s=timeout_s)


def _archive_generated(path: Path) -> None:
    if not path.exists():
        return
    trash = path.parent / '_trash'
    trash.mkdir(parents=True, exist_ok=True)
    archive = Path(mkdtemp(prefix='qwen-', dir=trash))
    path.rename(archive / path.name)


@contextmanager
def _leaf_state(outputs: Path):
    state = Path(mkdtemp(prefix='.qwen-leaf-', dir=outputs))
    try:
        yield state
    finally:
        _archive_generated(state)


# Slowest qualified tier, measured: the owner's 24 GiB M4 took 500.3 s for 16 steps at 1024x1024
# (qualification/evidence/qwen-image-21-20261008-24gb). Counting all of it as step time is 31.3 s per
# step; x1.1 headroom. At the model's 50-step maximum that is 1,720 s, under the Hub's 1,800 s Image
# deadline (`standalone.MODALITY_DEADLINE_FLOOR_S`).
SECONDS_PER_STEP = 34.4
MIN_TIMEOUT_S = 900.0


def job_timeout_s(steps) -> float:
    """How long one job may take, from its step count; never below the old flat 900 s."""
    return max(MIN_TIMEOUT_S, SECONDS_PER_STEP * int(steps or 0))


def run_image(weights: Path, params: dict, destination: Path, cancel_event,
              timeout_s: float | None = None) -> None:
    """Generate one image with a private, non-resident native leaf."""
    if timeout_s is None:
        timeout_s = job_timeout_s(params.get("steps"))
    problem = runtime_problem()
    if problem:
        raise MlxServeFailure(problem)
    if cancel_event.is_set():
        raise MlxServeCancelled("Generation cancelled")
    if not weights.is_dir():
        raise MlxServeFailure(f"Qwen weights are unavailable at {weights}")
    body = _request_body(weights, params)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    deadline = time.monotonic() + max(0.1, float(timeout_s))
    port = _port()
    key = secrets.token_urlsafe(32)
    binary = runtime_binary()
    command = [
        str(binary), "--model", str(weights), "--serve", "--host", "127.0.0.1",
        "--port", str(port), "--max-resident-models", "1",
        "--prefix-cache-entries", "0", "--timeout", "0",
        "--api-key-env", _AUTH_ENV, "--api-key-strict",
    ]
    with _leaf_state(destination.parent) as state_name:
        state = Path(state_name)
        os.chmod(state, 0o700)
        temporary_dir = state / "tmp"
        temporary_dir.mkdir(mode=0o700)
        env = {name: os.environ[name] for name in _SAFE_ENV if os.environ.get(name)}
        env.update({"HOME": str(state), "TMPDIR": str(temporary_dir), _AUTH_ENV: key})
        log_path = state / "mlx-serve.log"
        with log_path.open("wb") as log:
            try:
                process = subprocess.Popen(
                    command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                    env=env, cwd=state,
                )
            except OSError as exc:
                raise MlxServeFailure(f"mlx-serve could not start: {exc}") from exc
            global _ACTIVE_PROCESS
            with _ACTIVE_LOCK:
                _ACTIVE_PROCESS = process
            try:
                health = f"http://127.0.0.1:{port}/health"
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                while True:
                    if cancel_event.is_set():
                        raise MlxServeCancelled("Generation cancelled")
                    if process.poll() is not None:
                        log.flush()
                        detail = log_path.read_text(errors="replace")[-500:].strip()
                        raise MlxServeFailure(
                            "mlx-serve exited before readiness" + (f": {detail}" if detail else "")
                        )
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise MlxServeFailure("mlx-serve readiness timed out")
                    try:
                        health_request = urllib.request.Request(
                            health, headers={"Authorization": f"Bearer {key}"})
                        with opener.open(health_request,
                                         timeout=min(0.25, remaining)) as response:
                            if response.status == 200:
                                break
                    except (OSError, urllib.error.URLError):
                        time.sleep(min(0.05, max(0.0, remaining)))

                completed: queue.Queue = queue.Queue(maxsize=1)

                def perform() -> None:
                    try:
                        completed.put((True, _request(
                            f"http://127.0.0.1:{port}/v1/images/generations", key, body,
                            max(0.1, deadline - time.monotonic()),
                        )))
                    except Exception as exc:  # carried to the owning thread below
                        completed.put((False, exc))

                request_thread = threading.Thread(target=perform, daemon=True,
                                                  name="qwen-image-request")
                request_thread.start()
                while True:
                    if cancel_event.is_set():
                        raise MlxServeCancelled("Generation cancelled")
                    if process.poll() is not None:
                        raise MlxServeFailure("mlx-serve exited during generation")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise MlxServeFailure("mlx-serve generation timed out")
                    try:
                        ok, value = completed.get(timeout=min(0.1, remaining))
                        break
                    except queue.Empty:
                        continue
                if not ok:
                    if isinstance(value, MlxServeFailure):
                        raise value
                    raise MlxServeFailure(f"mlx-serve request failed: {value}") from value
                png = _png_from_response(value, (params["width"], params["height"]))
                if cancel_event.is_set():
                    raise MlxServeCancelled("Generation cancelled")
                digest = sha256(png).hexdigest()
                staged = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
                try:
                    with staged.open("xb") as output:
                        os.chmod(staged, 0o600)
                        output.write(png)
                        output.flush()
                        os.fsync(output.fileno())
                    if cancel_event.is_set():
                        raise MlxServeCancelled("Generation cancelled")
                    if sha256(staged.read_bytes()).hexdigest() != digest:
                        raise MlxServeFailure("Qwen PNG changed while staging")
                    os.replace(staged, destination)
                finally:
                    _archive_generated(staged)
            finally:
                _stop(process)
                with _ACTIVE_LOCK:
                    if _ACTIVE_PROCESS is process:
                        _ACTIVE_PROCESS = None
