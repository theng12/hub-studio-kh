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
import re
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


class Sa3Cancelled(Sa3Error):
    """The operator cancelled the run; the job must settle as cancelled, not error."""


# The CLI prints these stage headers to stdout whether or not it is a TTY, so the
# worker can show honest progress without patching the vendored script. It carries
# no percentage of its own; these are the boundaries between its real phases.
STAGE_PROGRESS = {
    "[1/5]": (0.10, "T5Gemma text encode"),
    "[2/5]": (0.25, "conditioning"),
    "[3a]": (0.35, "encoding reference"),
    "[3/5]": (0.45, "DiT sampling"),
    "[4/5]": (0.80, "SAME decoder"),
    "[5/5]": (0.95, "writing WAV"),
}
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


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
    """Lay out a temp tree so the CLI finds its code and the package weights.

    The code is **copied**, not symlinked: the CLI resolves its own root with
    ``Path(__file__).resolve()``, so a symlinked script would point back at the
    installed app and its weight lookup (and any auto-download) would land in the
    app bundle instead of this private tree.
    """
    models = root / "models"
    models.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ENGINE_DIR / "scripts", root / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(ENGINE_DIR / "models" / "defs", models / "defs",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(ENGINE_DIR / "models" / "__init__.py", models / "__init__.py")
    # The CLI looks for weights at <root>/models/mlx; point that at the package.
    (models / "mlx").symlink_to(weights, target_is_directory=True)
    return root / "scripts" / "sa3_mlx.py"


def generate(*, prompt: str, seconds: float, weights: Path, output: Path,
             steps: int = DEFAULT_STEPS, seed: int | None = None,
             cfg: float = DEFAULT_CFG,
             cancel_event: threading.Event | None = None,
             on_progress=None,
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
        log: list[str] = []
        reader = threading.Thread(target=_read_output,
                                  args=(process.stdout, log, on_progress),
                                  daemon=True, name="sa3-output")
        reader.start()
        try:
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    _terminate(process)
                    staging.unlink(missing_ok=True)
                    raise Sa3Cancelled("cancelled by the operator")
                try:
                    process.wait(timeout=0.5)
                    break
                except subprocess.TimeoutExpired:
                    if time.time() - started > timeout:
                        _terminate(process)
                        staging.unlink(missing_ok=True)
                        raise Sa3Error("Stable Audio 3 run exceeded its time budget")
            reader.join(timeout=5.0)
        finally:
            if process.stdout:
                process.stdout.close()
    if process.returncode != 0 or not staging.is_file():
        staging.unlink(missing_ok=True)
        tail = [line for line in log if line.strip()][-6:]
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


def _read_output(stream, sink: list[str], on_progress) -> None:
    """Drain the CLI's stdout, keeping a tail and reporting stage progress.

    ``PYTHONUNBUFFERED`` is set for the child, so each stage line arrives as it is
    printed rather than at process exit.
    """
    if stream is None:
        return
    try:
        for raw in stream:
            line = _ANSI.sub("", raw).rstrip()
            sink.append(line)
            if len(sink) > 400:
                del sink[:200]
            if on_progress is None:
                continue
            stripped = line.strip()
            for marker, (ratio, label) in STAGE_PROGRESS.items():
                if stripped.startswith(marker):
                    try:
                        on_progress(ratio, label)
                    except Exception:  # noqa: BLE001 - progress must never kill a run
                        pass
                    break
    except (ValueError, OSError):
        return


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
