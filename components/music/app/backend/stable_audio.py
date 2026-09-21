"""Stable Audio 3 (MLX) generation for the Music worker.

A second music engine beside ACE-Step. It runs Stability's vendored pure-MLX CLI
(`components/music/engines/stable_audio_3/scripts/sa3_mlx.py`) as a short-lived
subprocess against the weights of the imported `stable-audio-3-medium` package.

The CLI resolves its weights and model modules relative to its own file location,
so this module builds a small temporary working tree that symlinks the vendored
code and points ``models/mlx`` at the verified package weights. Nothing is written
into the app bundle or the model library.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import wave

from . import catalog

# `components/music/engines/stable_audio_3` relative to this file:
#   backend/ -> app/ -> music/
ENGINE_DIR = (Path(__file__).resolve().parents[2] / "engines" / "stable_audio_3")
SA3_SCRIPT = ENGINE_DIR / "scripts" / "sa3_mlx.py"
SA3_WEIGHTS_SUBDIR = ("models", "mlx")
DEFAULT_STEPS = 8
DEFAULT_CFG = 1.0
# The model's absolute maximum, mirrored from the catalogue.
MAX_SECONDS = catalog.SA3_MAX_DURATION_SECONDS


class Sa3Error(RuntimeError):
    """A Stable Audio 3 run that did not produce a verified WAV."""


def runtime_available() -> str | None:
    """Return a reason string when the vendored runtime is unusable, else None."""
    if not SA3_SCRIPT.is_file():
        return f"vendored Stable Audio 3 runtime is missing: {SA3_SCRIPT}"
    try:
        import mlx.core  # noqa: F401
        import sentencepiece  # noqa: F401
    except Exception as exc:  # pragma: no cover - environment dependent
        return f"Stable Audio 3 runtime dependencies are missing: {exc}"
    return None


def weights_dir(package_root: Path) -> Path:
    """The `models/mlx` directory inside an imported package folder."""
    return package_root.joinpath(*SA3_WEIGHTS_SUBDIR)


def _build_work_tree(root: Path, weights: Path) -> Path:
    """Lay out a temp tree so the CLI finds its code and the package weights."""
    models = root / "models"
    models.mkdir(parents=True, exist_ok=True)
    (root / "scripts").symlink_to(ENGINE_DIR / "scripts", target_is_directory=True)
    (models / "__init__.py").symlink_to(ENGINE_DIR / "models" / "__init__.py")
    (models / "defs").symlink_to(ENGINE_DIR / "models" / "defs", target_is_directory=True)
    # The CLI looks for weights at <root>/models/mlx; point that at the package.
    (models / "mlx").symlink_to(weights, target_is_directory=True)
    return root / "scripts" / "sa3_mlx.py"


def generate(*, prompt: str, seconds: float, weights: Path, output: Path,
             steps: int = DEFAULT_STEPS, seed: int | None = None,
             cfg: float = DEFAULT_CFG,
             cancel_event: threading.Event | None = None,
             timeout: float = 1800.0) -> dict:
    """Render one clip and return verified facts about the WAV it wrote.

    Raises :class:`Sa3Error` (or ``CancelledError``-equivalent) without leaving a
    partial output behind.
    """
    if not prompt.strip():
        raise Sa3Error("Prompt is required")
    if not 1 <= seconds <= MAX_SECONDS:
        raise Sa3Error(f"duration must be between 1 and {MAX_SECONDS} seconds")
    reason = runtime_available()
    if reason:
        raise Sa3Error(reason)
    weights = Path(weights)
    if not weights.is_dir():
        raise Sa3Error(f"Stable Audio 3 weights are not installed at {weights}")

    output = Path(output).resolve()
    staging = output.with_suffix(".partial.wav")
    staging.unlink(missing_ok=True)
    started = time.time()
    with tempfile.TemporaryDirectory(prefix="sa3-run-") as temporary:
        tree = Path(temporary)
        script = _build_work_tree(tree, weights)
        command = [sys.executable, str(script), "--prompt", prompt,
                   "--dit", "medium", "--decoder", "same-l",
                   "--seconds", str(int(round(seconds))), "--steps", str(steps),
                   "--cfg", str(cfg), "--out", str(staging)]
        if seed is not None and seed >= 0:
            command += ["--seed", str(seed)]
        environment = dict(os.environ)
        # Never reach the network: the package was verified before we got here.
        environment["HF_HUB_OFFLINE"] = "1"
        environment["PYTHONUNBUFFERED"] = "1"
        process = subprocess.Popen(
            command, cwd=str(tree), env=environment,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            start_new_session=True)
        try:
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    _terminate(process)
                    staging.unlink(missing_ok=True)
                    raise Sa3Error("cancelled")
                try:
                    process.wait(timeout=0.5)
                    break
                except subprocess.TimeoutExpired:
                    if time.time() - started > timeout:
                        _terminate(process)
                        staging.unlink(missing_ok=True)
                        raise Sa3Error("Stable Audio 3 run exceeded its time budget")
            log = process.stdout.read() if process.stdout else ""
        finally:
            if process.stdout:
                process.stdout.close()
    if process.returncode != 0 or not staging.is_file():
        staging.unlink(missing_ok=True)
        tail = (log or "").strip().splitlines()[-6:]
        raise Sa3Error("Stable Audio 3 failed: " + " | ".join(tail))

    with wave.open(str(staging), "rb") as handle:
        frames = handle.getnframes()
        rate = handle.getframerate()
        channels = handle.getnchannels()
        duration = frames / float(rate) if rate else 0.0
    if frames <= 0 or duration < seconds - 1.5 or duration > seconds + 1.5:
        staging.unlink(missing_ok=True)
        raise Sa3Error(
            f"Stable Audio 3 wrote {duration:.2f}s for a {seconds:g}s request")
    digest = hashlib.sha256()
    with staging.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(staging), str(output))
    return {
        "output_path": str(output),
        "bytes": output.stat().st_size,
        "sha256": digest.hexdigest(),
        "audio_duration_s": round(duration, 6),
        "sample_rate_hz": rate,
        "channels": channels,
        "frames": frames,
        "media_type": "audio/wav",
        "engine_seconds": round(time.time() - started, 3),
    }


def _terminate(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, 15)
        else:
            process.terminate()
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            if os.name == "posix":
                os.killpg(process.pid, 9)
            else:
                process.kill()
        except ProcessLookupError:
            pass
