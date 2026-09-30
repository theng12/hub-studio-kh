#!/usr/bin/env python3
"""Run one YuE2 music job — executed **by the YuE2 runtime's own interpreter**.

Why a separate process rather than a function in the worker: ``mlx-Yue`` pins ``mlx==0.32.2`` while
the Music worker's runtime (the Voice runtime) carries ``mlx==0.31.2``. One
tree cannot hold two MLX versions, so the worker spawns this script with
``runtimes/music-yue2/bin/python3`` and reads its stdout, exactly as the Video worker already does
for its two engines.

Why the library API rather than the port's CLI: the CLI is a full application — it resolves
checkpoint revisions, verifies tree digests, writes resource-evidence sidecars and refuses to
overwrite an output directory. The worker already owns all of that (the Hub verified the package,
the job has its own temp dir), so the bridge drives ``YuE2Pipeline`` directly and keeps the app's
conventions around it.

Contract with the worker:

* ``argv[1]`` is a path to a JSON spec (written by ``music.yue2.run_yue2``).
* stdout carries **one JSON object per line**: ``{"stage": …}`` notes while it works, and a final
  ``{"status": "ok", …}`` or ``{"status": "failed", "error": …}``.
* exit status is 0 only when the audio file exists and the last line says ok.

Two environment facts are load-bearing:

* ``HF_HUB_OFFLINE=1`` — the port loads the VAE and the tokenizer by path, but its tokenizer path
  reaches Hugging Face for anything it cannot find locally. A node may be offline, so a missing
  file must fail here with that sentence rather than at a network timeout.
* The weights and the VAE are **two directories**, both verified by the Hub before the job runs;
  a bridge that guessed one from the other would run a different checkpoint than the one the
  operator installed.
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

    The worker's own sampler watches the worker, and for this engine the memory is not there — the
    bridge is a child. `phys_footprint_peak` is the field to read: MLX's own
    ``mx.get_peak_memory()`` counts only its allocator and under-reports the process by roughly 2×,
    which is the difference between an 8 GB verdict and an honest one.
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
        return fail("usage: yue2_runner.py <spec.json>")
    spec = json.loads(Path(argv[1]).read_text())

    model_dir = Path(spec["model_dir"])
    vae_dir = Path(spec["vae_dir"])
    output = Path(spec["output"])
    precision = str(spec.get("precision") or "8bit")
    if precision not in {"bf16", "8bit", "4bit"}:
        return fail(f"unsupported precision {precision!r}; the port offers bf16, 8bit and 4bit")
    if not model_dir.is_dir():
        return fail(f"the YuE2 generator package is not installed at {model_dir}")
    if not vae_dir.is_dir():
        return fail(
            f"the YuE2 decoder (VAE) package is not installed at {vae_dir}. The generator pack "
            "ships without it; the decoder is a required companion package.")

    # The port imports `yue2` from its own vendored tree, which the runtime keeps beside the
    # environment. `build.sh` names it in the same place, so a runtime that passed its smoke check
    # has it here.
    for candidate in (
        # The stager copies the port's tree beside the runtime's own environment and points this
        # bridge at it through `YUE2_RUNTIME_ROOT`; the app's own layout is the fallback so a
        # development checkout runs the same way.
        Path(os.environ.get("YUE2_RUNTIME_ROOT", "")) / "lib" / "mlx-yue-source",
        Path(__file__).resolve().parent / "mlx-yue-source",
    ):
        if (candidate / "vendor" / "yue" / "src" / "yue2").is_dir():
            sys.path.insert(0, str(candidate / "vendor" / "yue" / "src"))
            sys.path.insert(0, str(candidate / "src"))
            break

    os.environ["HF_HUB_OFFLINE"] = "1"

    try:
        from yue2.protocol import Sampling
    except Exception as exc:  # noqa: BLE001
        return fail(f"the YuE2 runtime cannot import its protocol module: {exc}")

    cot = str(spec.get("mode") or "off")
    if cot not in {"off", "melody", "full"}:
        return fail(f"unsupported mode {cot!r}; this lane offers off, melody and full")
    seed = spec.get("seed")
    seed = 831001 if seed is None or int(seed) < 0 else int(seed)
    request_id = str(spec.get("request_id") or "song")
    # Length comes from the caller as seconds, and the port's budget is tokens. The port's own
    # measured rate is ~25 tokens per second of 48 kHz audio (3000 tokens → 120.0 s on this Mac),
    # so a caller's seconds are converted here and clamped to the port's context (24576 tokens).
    semantic_sampling = Sampling(
        min_tokens=max(1, int(spec.get("min_tokens") or 200)),
        max_tokens=max(1, min(int(spec.get("max_tokens") or 9000), 24576)),
    )

    emit({"stage": "loading", "detail": f"YuE2 {precision} generator + decoder"})
    load_started = time.time()
    try:
        from lyra import YuE2Pipeline  # noqa: PLC0415 - the runtime is the point of the delay
    except Exception as exc:  # noqa: BLE001 - the message is the fix
        return fail(f"the YuE2 runtime cannot import its own pipeline: {exc}")

    try:
        from yue2.protocol import GenerationConfig
    except Exception as exc:  # noqa: BLE001
        return fail(f"the YuE2 runtime cannot import its generation config: {exc}")
    steps = spec.get("steps")
    steps = 32 if steps is None else int(steps)

    try:
        pipeline = YuE2Pipeline.from_pretrained(
            str(model_dir),
            vae=str(vae_dir),
            precision=precision,
            local_files_only=True,
            # The ODE step count is the port's own control (8 is its ultra-fast mode, 32 its
            # standard), passed through the generation config the constructor takes. The worker
            # publishes the same two bounds, so a caller's choice is honoured rather than dropped.
            generation_config=GenerationConfig(ode_steps=steps),
            # The port's own monitor fails closed on swap growth. It defaults to 16 GiB; the worker
            # passes what this host can afford, and the value is recorded with the job so a receipt
            # says which budget the run was made under.
            memory_budget_gib=float(spec.get("memory_budget_gib") or 16),
        )
    except Exception as exc:  # noqa: BLE001
        return fail(f"loading the YuE2 checkpoint failed: {exc}")
    load_seconds = time.time() - load_started
    emit({"stage": "loaded", "seconds": round(load_seconds, 1)})

    emit({"stage": "generating", "detail": spec.get("style", "")[:120]})
    generate_started = time.time()
    try:
        result = pipeline(
            style=str(spec["style"]),
            lyrics=str(spec.get("lyrics") or ""),
            seed=seed,
            # `cot` is the port's own name for "how much planning"; the lane publishes it as `mode`
            # because that is the vocabulary every other music lane in this app uses.
            cot=cot,
            id=request_id,
            # Length is a sampling budget, not a duration: the AR stage emits one codec frame per
            # token, so `max_tokens` is what decides how long the song is. The lane's seconds are
            # what it *publishes*; this is how a caller asks for less.
            semantic_sampling=semantic_sampling,
            # `cfg_scale` is the port's own classifier-free-guidance scale; its protocol validates
            # 0..20. Absent means the port's default for the mode.
            **({"cfg_scale": float(spec["cfg_scale"])} if spec.get("cfg_scale") is not None else {}),
        )
    except Exception as exc:  # noqa: BLE001
        return fail(f"YuE2 generation failed: {exc}")
    generate_seconds = time.time() - generate_started

    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        # `save` writes the audio alone, which is the artifact this lane promises. It accepts
        # `.flac` (24-bit PCM) and `.wav` and refuses anything else — the worker names the path.
        result.save(str(output))
    except Exception as exc:  # noqa: BLE001
        return fail(f"writing the audio failed: {exc}")

    if not output.is_file() or output.stat().st_size <= 0:
        return fail(f"the pipeline reported success but {output} is missing or empty")

    # Duration and sample rate are read back from the file, not from the pipeline's own report: the
    # artifact is what the worker will hand the operator, and a number that disagrees with it is
    # the number that gets published wrongly.
    seconds, sample_rate, channels = probe_audio(output)
    emit({
        "status": "ok",
        "output": str(output),
        "bytes": output.stat().st_size,
        "seconds": seconds,
        "sample_rate": sample_rate,
        "channels": channels,
        "precision": precision,
        "steps": steps,
        "load_seconds": round(load_seconds, 3),
        "generate_seconds": round(generate_seconds, 3),
        "memory_budget_gib": float(spec.get("memory_budget_gib") or 16),
    })
    return 0


def probe_audio(path: Path) -> tuple[float, int, int]:
    """Duration, sample rate and channels, from the file itself.

    `soundfile` reads the container the port writes (48 kHz FLAC); a file it cannot read reports
    zeros rather than a guess, and the caller is expected to treat that as "unmeasured".
    """
    try:
        import soundfile  # noqa: PLC0415

        info = soundfile.info(str(path))
        return float(info.duration), int(info.samplerate), int(info.channels)
    except Exception:  # noqa: BLE001 - an unreadable header is not a failed run
        return 0.0, 0, 0


def run_with_sampler(spec_path: Path) -> int:
    with FootprintSampler() as sampler:
        code = main([sys.argv[0], str(spec_path)])
        if code == 0 and sampler.peak_bytes:
            emit({
                "peak_footprint_bytes": sampler.peak_bytes,
                "peak_rss_bytes": sampler.peak_rss_bytes,
                "footprint_method": "sampled /usr/bin/footprint phys_footprint_peak at 2 Hz "
                                    "in the runner process",
            })
        return code


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(fail("usage: yue2_runner.py <spec.json>"))
    raise SystemExit(run_with_sampler(Path(sys.argv[1])))
