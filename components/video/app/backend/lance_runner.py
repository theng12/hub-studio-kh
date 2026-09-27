#!/usr/bin/env python3
"""Run one Lance text-to-video job — executed **by the Lance runtime's own interpreter**.

Why a separate process rather than a function in the worker: `lance-mlx` declares
``requires-python >= 3.12`` while the worker's runtime is Python 3.11 (LTX's constraint), so the
two engines cannot share an interpreter in either direction. The worker therefore spawns this
script with ``runtimes/video-lance/bin/python3`` and reads its stdout, exactly as it already
spawns ffmpeg.

Why not the runner's own CLI: ``src/lance_mlx/__main__.py`` is a **stub** at the pinned commit
("Phase 0/1 incomplete — pipeline modules are stubs"). The library API is the only surface that
does the work, so this bridge is that API with the app's conventions around it.

Contract with the worker:

* ``argv[1]`` is a path to a JSON spec (written by ``generation.run_lance``).
* stdout carries **one JSON object per line**: ``{"stage": …}`` progress notes while it works, and
  a final ``{"status": "ok", …}`` or ``{"status": "failed", "error": …}``.
* exit status is 0 only when the MP4 exists and the last line says ok.

Two environment facts are load-bearing:

* ``HF_HUB_OFFLINE=1`` — the pipeline calls ``AutoProcessor.from_pretrained``, and the processor is
  a **local companion package** on a fleet node. If it is missing, this must fail here with that
  message rather than silently reaching for huggingface.co on a machine that may be offline.
* ``IMAGEIO_FFMPEG_EXE`` — imageio would otherwise download its *own* ffmpeg at first use. The app
  already ships a video-capable one (the Video runtime's), and the worker passes its path in the
  spec so the clip is written by the same muxer every other artifact in this app uses.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path


class FootprintSampler:
    """Peak OS-committed footprint of **this** process, sampled while it works.

    Why the runner measures itself: the worker's own sampler (`generation.FootprintSampler`)
    watches the worker's process, and for this engine the memory is not there — the bridge is a
    child. Measured 2026-09-27 on the first lane run: the worker recorded **57 MB** for a job
    whose real peak was ~8 GB, which would have been published in the job record and the receipt
    as the run's cost. `phys_footprint_peak` is the field to read (mx.get_peak_memory()
    under-reports by ~2x and excludes everything outside MLX's own allocator).
    """

    def __init__(self, interval: float = 0.5) -> None:
        self.interval = interval
        self.peak_bytes = 0
        self.peak_rss_bytes = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self) -> "FootprintSampler":
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _run(self) -> None:
        pid = str(os.getpid())
        units = {"B": 1, "KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3}
        while not self._stop.is_set():
            try:
                text = subprocess.run(["/usr/bin/footprint", "-p", pid],
                                      capture_output=True, text=True, timeout=60).stdout
                match = re.search(r"phys_footprint_peak:\s*([\d.]+)\s*(KB|MB|GB|B)", text)
                if match:
                    self.peak_bytes = max(self.peak_bytes,
                                          int(float(match.group(1)) * units[match.group(2)]))
            except Exception:  # noqa: BLE001 - a sampler must never fail the run
                pass
            try:
                rss = subprocess.run(["ps", "-o", "rss=", "-p", pid],
                                     capture_output=True, text=True, timeout=10).stdout.strip()
                if rss:
                    self.peak_rss_bytes = max(self.peak_rss_bytes, int(rss) * 1024)
            except Exception:  # noqa: BLE001
                pass
            self._stop.wait(self.interval)


def emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def fail(message: str) -> int:
    emit({"status": "failed", "error": message})
    return 1


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        return fail("usage: lance_runner.py <spec.json>")
    spec = json.loads(Path(argv[1]).read_text())

    weights = Path(spec["weights_dir"])
    processor = Path(spec["processor_dir"])
    output = Path(spec["output"])
    if not weights.is_dir():
        return fail(f"the Lance weights are not installed at {weights}")
    if not processor.is_dir():
        # The most likely fleet failure, and the one with a specific fix — say it here.
        return fail(
            f"the Qwen2.5-VL processor is not installed at {processor}. The Lance pack ships only "
            f"the tokenizer; the processor is a required companion (lance-3b-processor-qwen2-5-vl).")
    if not spec.get("ffmpeg"):
        return fail("no ffmpeg path in the spec; the muxer must be the app's own")

    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["IMAGEIO_FFMPEG_EXE"] = str(spec["ffmpeg"])
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

    emit({"stage": "loading", "weights": str(weights)})
    from lance_mlx.pipeline.t2v import TextToVideoPipeline

    started = time.time()
    mode = spec.get("memory_mode") or "auto"
    try:
        pipeline = TextToVideoPipeline.from_pretrained(
            lance_weights_dir=str(weights),
            vae_safetensors=str(weights / "vae.safetensors"),
            hf_processor_repo=str(processor),
            memory_mode=mode,
        )
    except Exception as exc:  # noqa: BLE001 - the message is what the operator needs
        return fail(f"could not load the Lance pipeline ({mode}): {exc}")
    load_seconds = round(time.time() - started, 1)
    emit({"stage": "generating", "load_seconds": load_seconds})

    import numpy as np

    started = time.time()
    sampler = FootprintSampler()
    sampler.__enter__()
    try:
        frames = pipeline.generate(
            spec["prompt"],
            num_frames=int(spec["num_frames"]),
            height=int(spec["height"]),
            width=int(spec["width"]),
            num_steps=int(spec["num_steps"]),
            cfg_scale=float(spec["cfg_scale"]),
            seed=int(spec["seed"]),
            memory_mode=mode,
            lossless_decode=bool(spec.get("lossless_decode", True)),
        )
    except Exception as exc:  # noqa: BLE001
        sampler.__exit__()
        return fail(f"the Lance pipeline failed: {exc}")
    generate_seconds = round(time.time() - started, 1)
    array = np.asarray(frames)
    if array.ndim != 4 or array.shape[0] < 1:
        return fail(f"the Lance pipeline returned frames of shape {array.shape}, not (T, H, W, 3)")

    emit({"stage": "muxing", "frames": int(array.shape[0]),
          "height": int(array.shape[1]), "width": int(array.shape[2])})
    if array.dtype != np.uint8:
        array = np.clip(array, 0, 255).astype(np.uint8)
    output.parent.mkdir(parents=True, exist_ok=True)
    import imageio

    fps = int(spec.get("fps", 12))
    try:
        with imageio.get_writer(str(output), fps=fps, codec="libx264", quality=8,
                                macro_block_size=None) as writer:
            for frame in array:
                writer.append_data(frame)
    except Exception as exc:  # noqa: BLE001
        return fail(f"could not write the MP4: {exc}")

    sampler.__exit__()
    emit({
        "status": "ok",
        "frames": int(array.shape[0]),
        # Measured here, by this process, because this is where the memory is.
        "peak_footprint_bytes": sampler.peak_bytes,
        "peak_rss_bytes": sampler.peak_rss_bytes,
        "footprint_method": "sampled ri_phys_footprint at 2 Hz in the runner process",
        "width": int(array.shape[2]),
        "height": int(array.shape[1]),
        "fps": fps,
        "load_seconds": load_seconds,
        "generate_seconds": generate_seconds,
        "memory_mode": mode,
        "lossless_decode": bool(spec.get("lossless_decode", True)),
        "bytes": output.stat().st_size if output.exists() else 0,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
