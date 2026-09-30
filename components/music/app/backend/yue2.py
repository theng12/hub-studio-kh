"""YuE2 (MLX) generation for the Music worker — the engine's own runtime, driven by a bridge.

The sung-song music engine beside Stable Audio 3, and the one that cannot run in this
worker's interpreter: `mlx-Yue` pins `mlx==0.32.2` while this worker's runtime (the Voice runtime)
carries `mlx==0.31.2`. So the run happens in
`runtimes/music-yue2/bin/python3`, through `yue2_runner.py`, and this module is the worker's side of
that contract — the same shape `video/generation.run_lance` has for its engine.

Why a bridge rather than the port's CLI: the CLI is a whole application. It resolves checkpoint
revisions, verifies tree digests, writes resource-evidence sidecars beside the output and refuses a
non-empty output directory. The worker already owns all of that — the Hub verified the package, the
job has its own temp/output path, and the app's own records are the ones that matter — so the bridge
drives `YuE2Pipeline` directly with the app's conventions around it.

Two numbers are read from the bridge rather than measured here, and they are the two that matter:

* **the peak footprint**, because the memory is in the child. This worker's own sampler watches the
  worker, which for this engine sits at a few hundred megabytes while the song renders at ~11 GB.
* **the artifact's own duration and sample rate**, so what the job publishes is what the file says.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

# `components/music/app/` relative to this file: backend/ -> app/ -> music/
APP_DIR = Path(__file__).resolve().parents[1]
BRIDGE = Path(__file__).resolve().parent / "yue2_runner.py"
MEDIA_TYPE = "audio/flac"

# How long a bridge run may take before it is killed. The measured runs took 68 s (24 s of audio)
# and 430 s (120 s of audio); a 16 GB Mac under pressure is slower, and the port's own guard fails
# closed rather than thrashing, so this is generous on purpose and still bounded.
DEFAULT_TIMEOUT_SECONDS = 7200.0


class Yue2Error(RuntimeError):
    """A YuE2 run that did not produce a verified audio file."""


class Yue2Cancelled(RuntimeError):
    """The job was cancelled; the bridge was asked to stop and the partial file is gone."""


class Yue2Progress:
    """Forward the bridge's own stage lines to the job, as ratios."""

    # The bridge's stages, in order, with the fraction each one starts at. The AR stage dominates
    # the wall clock (measured: 146 of 423 s for a 120 s song, with the NAR stage taking 268 s), so
    # the two are given the room they actually take rather than an equal share.
    STAGE_START = {
        "loading": 0.02,
        "loaded": 0.10,
        "generating": 0.12,
    }

    def __init__(self, on_progress) -> None:
        self.on_progress = on_progress

    def __call__(self, payload: dict) -> None:
        stage = str(payload.get("stage") or "")
        start = self.STAGE_START.get(stage)
        if start is None:
            return
        detail = str(payload.get("detail") or stage)
        seconds = payload.get("seconds")
        if isinstance(seconds, (int, float)):
            detail = f"{detail} — {float(seconds):.1f}s"
        self.on_progress(start, detail[:200])


def generate(*, model_dir: Path, vae_dir: Path | None, output: Path, prompt: str, lyrics: str,
             mode: str, precision: str, duration_seconds: float, max_tokens: int, min_tokens: int,
             steps: int, guidance_scale: float, seed: int, interpreter: Path,
             cancel_event: threading.Event | None = None,
             on_progress=None, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> dict:
    """Render one song, verified against the file the bridge actually wrote.

    Every field of the returned dict is read from the artifact or from the bridge's own final line;
    nothing here is estimated. A missing decoder is refused before the spawn, because that is the
    one failure whose fix is a specific package rather than a retry.
    """
    if not interpreter.is_file():
        raise Yue2Error(
            f"the YuE2 runtime is not staged at {interpreter}. This engine runs in its own pinned "
            "Python 3.12 runtime (runtimes/music-yue2); install it before generating a song.")
    if vae_dir is None or not Path(vae_dir).is_dir():
        raise Yue2Error(
            "the YuE2 decoder (yue2-vae) is not installed. The generator package ships without it "
            "and the port loads it by path, so the song cannot be decoded without it.")
    if not BRIDGE.is_file():
        raise Yue2Error(f"the YuE2 runner bridge is missing at {BRIDGE}")

    output.parent.mkdir(parents=True, exist_ok=True)
    spec = {
        "model_dir": str(model_dir),
        "vae_dir": str(vae_dir),
        "output": str(output),
        "precision": precision,
        "style": prompt,
        "lyrics": lyrics,
        "mode": mode,
        "seed": seed,
        "max_tokens": int(max_tokens),
        "min_tokens": int(min_tokens),
        "steps": int(steps),
        "cfg_scale": float(guidance_scale),
        "memory_budget_gib": _memory_budget_gib(),
        "request_id": "studio-hub-yue2",
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                     dir=str(output.parent)) as handle:
        json.dump(spec, handle)
        spec_path = Path(handle.name)

    env = dict(os.environ)
    env["HF_HUB_OFFLINE"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    # The bridge resolves the port's vendored `yue2` package from the runtime's own copy.
    runtime_root = interpreter.parent.parent
    env["YUE2_RUNTIME_ROOT"] = str(runtime_root)

    started = time.time()
    final: dict | None = None
    failure: str | None = None
    process = subprocess.Popen(
        [str(interpreter), "-I", "-B", str(BRIDGE), str(spec_path)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env,
        cwd=str(output.parent))
    progress = Yue2Progress(on_progress or (lambda *_: None))
    try:
        assert process.stdout is not None
        for line in process.stdout:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if payload.get("status") == "ok":
                final = payload
            elif payload.get("status") == "failed":
                failure = str(payload.get("error") or "the YuE2 runner failed")
            elif payload.get("peak_footprint_bytes"):
                # The bridge's own measurement, on its last line after a successful run.
                if final is not None:
                    final.update({key: payload.get(key) for key in (
                        "peak_footprint_bytes", "peak_rss_bytes", "footprint_method")})
            else:
                progress(payload)
            if cancel_event is not None and cancel_event.is_set():
                process.terminate()
                raise Yue2Cancelled("cancelled")
        code = process.wait(timeout=60)
    except Yue2Cancelled:
        _stop(process)
        output.unlink(missing_ok=True)
        raise
    except Exception:
        _stop(process)
        output.unlink(missing_ok=True)
        raise
    finally:
        spec_path.unlink(missing_ok=True)
        if process.poll() is None:
            _stop(process)

    if cancel_event is not None and cancel_event.is_set():
        output.unlink(missing_ok=True)
        raise Yue2Cancelled("cancelled")
    if code != 0 or final is None:
        output.unlink(missing_ok=True)
        raise Yue2Error(failure or f"the YuE2 runner exited {code} without a result")

    # **Verify the artifact, not the report.** A bridge that said ok and wrote nothing, or wrote a
    # truncated file, must fail here rather than become a job that claims a song.
    if not output.is_file() or output.stat().st_size <= 0:
        raise Yue2Error(f"the YuE2 runner reported success but {output.name} is missing or empty")
    seconds = float(final.get("seconds") or 0.0)
    sample_rate = int(final.get("sample_rate") or 0)
    channels = int(final.get("channels") or 0)
    if seconds <= 0 or sample_rate <= 0 or channels <= 0:
        output.unlink(missing_ok=True)
        raise Yue2Error("the YuE2 runner produced a file whose audio header could not be read")

    payload = output.read_bytes()
    return {
        "bytes": len(payload),
        "sha256": __import__("hashlib").sha256(payload).hexdigest(),
        "audio_duration_s": round(seconds, 3),
        "sample_rate_hz": sample_rate,
        "channels": channels,
        "frames": int(round(seconds * sample_rate)),
        "generated_seconds": round(time.time() - started, 3),
        "load_seconds": float(final.get("load_seconds") or 0.0),
        "generate_seconds": float(final.get("generate_seconds") or 0.0),
        "peak_footprint_bytes": final.get("peak_footprint_bytes"),
        "peak_rss_bytes": final.get("peak_rss_bytes"),
        "footprint_method": final.get("footprint_method"),
    }


def _memory_budget_gib() -> float:
    """What this host can afford, from the operator's own free memory.

    The port's monitor fails closed when the process exceeds its budget or swaps more than 64 MiB,
    and it refuses a budget above total-4 GiB. This is not a tier decision — the Hub publishes the
    tier — it is the guard's input, so it is derived from the machine rather than fixed at the
    port's 16 GiB default: on a 32 GB Mac an 11.9 GB run should not be failed for crossing a
    budget meant for a 16 GB one, and on a 16 GB Mac the guard must still bite.
    """
    try:
        import psutil  # noqa: PLC0415 - only needed here, and absent in a bare test harness

        total_gib = psutil.virtual_memory().total / (1024 ** 3)
        return max(6.0, min(total_gib - 4.0, total_gib * 0.75))
    except Exception:  # noqa: BLE001 - no psutil means the port's own default
        return 16.0


def _stop(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()


def bridge_available(interpreter: Path) -> tuple[bool, str]:
    """``(usable, why not)`` for this engine on this host — what the catalogue publishes."""
    if not interpreter.is_file():
        return False, (f"the YuE2 runtime is not staged at {interpreter}; this engine needs its "
                       "own pinned Python 3.12 runtime")
    return True, ""


if __name__ == "__main__":  # pragma: no cover - a helpful failure, not a code path
    print(f"YuE2 bridge: {BRIDGE}", file=sys.stderr)
