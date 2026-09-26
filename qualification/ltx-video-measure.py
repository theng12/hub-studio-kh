#!/usr/bin/env python3
"""Measure ONE LTX-2.3 video generation on this Mac, and publish nothing it cannot support.

This is the measurement half of `RUN-16GB-LTX-VIDEO.command`. It exists because the only
published memory figure for LTX-2.3 on Apple Silicon is the packager's *"at least 24 GB
unified RAM for safe T2V inference"* — a vendor claim, measured by nobody on a 16 GB Mac,
which is the tier this fleet is actually made of (15 of ~53 machines).

Design rules, each of which is here because this project has been burned by its absence:

* **Record the machine, not an assumption.** Chip, model identifier and unified memory are
  read with ABSOLUTE sysctl paths, never the bare name. A packaged worker has no `/usr/sbin`
  on PATH, and that exact omission made Voice's memory probe answer `"unknown"` fleet-wide.
* **Peak memory is two numbers, never one.** `mx.get_peak_memory()` counts MLX's Metal
  allocations only; on macOS those do NOT appear in the process resident set. The sibling
  project says so outright (`ltx-2-mlx` PR #86). Both are recorded, and neither is presented
  as "the" footprint.
* **Pass only what the pipeline actually accepts.** The call is built by introspecting
  `generate_and_save`'s signature, so a pipeline that has no `image` parameter is *detected*
  rather than throwing, and this script keeps working across runtime versions.
* **Refuse rather than fall back.** `--require-image-to-video` makes a missing image path a
  hard failure. An I2V run that silently became text-to-video would be a measurement of the
  wrong thing wearing the right label.
* **Write everything down.** One JSON record plus the raw log, beside this script, so the
  number can be traced to the run that produced it.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
import zlib
import struct
from pathlib import Path

SYSCTL = ("/usr/sbin/sysctl", "/usr/bin/sysctl")


def _sysctl(name: str) -> str | None:
    """One sysctl reading via an absolute path. Never the bare name — see the module docstring."""
    for exe in SYSCTL:
        if not os.path.exists(exe):
            continue
        try:
            done = subprocess.run([exe, "-n", name], capture_output=True, text=True, timeout=10)
        except Exception:
            continue
        if done.returncode == 0 and done.stdout.strip():
            return done.stdout.strip()
    return None


def machine_facts() -> dict:
    mem = _sysctl("hw.memsize")
    mem_gb = round(int(mem) / 1024**3) if mem and mem.isdigit() else None
    return {
        "chip": _sysctl("machdep.cpu.brand_string") or platform.processor() or "unknown",
        "model": _sysctl("hw.model"),
        "unified_memory_gb": mem_gb,
        "macos": platform.mac_ver()[0],
        "python": platform.python_version(),
        "swap": _sysctl("vm.swapusage"),
    }


def write_test_frame(path: Path, width: int, height: int) -> None:
    """A PNG with no third-party dependency, for when no source scene is supplied.

    Deliberately a plain gradient rather than anything pictorial: the point of this run is
    memory and time, and a busy test image would invite reading quality into it.
    """
    rows = bytearray()
    for y in range(height):
        rows.append(0)  # PNG filter byte: none
        for x in range(width):
            rows += bytes((x * 255 // max(width - 1, 1), y * 255 // max(height - 1, 1), 96))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(bytes(rows), 6))
           + chunk(b"IEND", b""))
    path.write_bytes(png)


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Measure one LTX-2.3 generation on this Mac.")
    p.add_argument("--model-dir", required=True)
    p.add_argument("--gemma-model", default="mlx-community/gemma-3-12b-it-4bit")
    p.add_argument("--prompt", default="A slow, steady camera push toward a quiet harbour at dawn.")
    p.add_argument("--image", default=None, help="Source scene. Required when --require-image-to-video.")
    p.add_argument("--require-image-to-video", action="store_true")
    p.add_argument("--output", required=True)
    p.add_argument("--results", required=True)
    p.add_argument("--height", type=int, default=480)
    p.add_argument("--width", type=int, default=704)
    p.add_argument("--num-frames", type=int, default=97)
    p.add_argument("--frame-rate", type=float, default=24.0)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--pipeline", default="DistilledPipeline")
    p.add_argument("--no-low-memory", dest="low_memory", action="store_false", default=True)
    p.add_argument("--low-ram-streaming", action="store_true", default=False)
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    facts = machine_facts()
    record: dict = {
        "schema": "studiohub.video-measurement.v1",
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "machine": facts,
        "request": {k: v for k, v in vars(args).items() if k != "results"},
        "runtime": {},
        "result": {"status": "started"},
    }
    results = Path(args.results)
    results.parent.mkdir(parents=True, exist_ok=True)

    def save(status: str, **extra) -> None:
        record["result"].update({"status": status, **extra})
        record["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        results.write_text(json.dumps(record, indent=2, default=str))

    print(f"Mac: {facts['chip']} {facts['model']} — {facts['unified_memory_gb']} GB unified, macOS {facts['macos']}")
    print(f"swap at start: {facts['swap']}")

    model_dir = Path(args.model_dir)
    if not model_dir.is_dir():
        print(f"\nREFUSING: no model directory at {model_dir}")
        save("refused", reason="model directory absent", model_dir=str(model_dir))
        return 3

    if args.require_image_to_video and not args.image:
        print("\nREFUSING: --require-image-to-video was set and no --image was given.")
        save("refused", reason="image-to-video required but no source image supplied")
        return 3

    image_path = Path(args.image) if args.image else None
    if image_path and not image_path.is_file():
        print(f"\nREFUSING: source image not found at {image_path}")
        save("refused", reason="source image absent", image=str(image_path))
        return 3
    if image_path is None:
        image_path = results.parent / "test-frame.png"
        write_test_frame(image_path, args.width, args.height)
        record["request"]["image"] = str(image_path)
        print(f"no --image given; wrote a synthetic test frame at {image_path}")

    try:
        import mlx.core as mx  # noqa: F401
        import ltx_pipelines_mlx
    except Exception as exc:  # pragma: no cover - environment dependent
        print(f"\nREFUSING: the LTX runtime is not importable here: {exc}")
        print("Run this from the runtime's own interpreter (see the runner script).")
        save("refused", reason=f"runtime import failed: {exc}")
        return 3

    record["runtime"] = {
        "ltx_pipelines_mlx": getattr(ltx_pipelines_mlx, "__version__", "unversioned"),
        "mlx": getattr(mx, "__version__", "unversioned"),
        "pipelines_available": sorted(
            n for n in dir(ltx_pipelines_mlx) if n.endswith("Pipeline")
        ),
    }

    pipeline_cls = getattr(ltx_pipelines_mlx, args.pipeline, None)
    if pipeline_cls is None:
        print(f"\nREFUSING: this runtime has no {args.pipeline}.")
        print("Available: " + ", ".join(record["runtime"]["pipelines_available"]))
        save("refused", reason=f"{args.pipeline} not exported by this runtime")
        return 3

    import inspect

    ctor = inspect.signature(pipeline_cls.__init__).parameters
    ctor_kwargs: dict = {"model_dir": str(model_dir)}
    for name, value in (("gemma_model_id", args.gemma_model),
                        ("low_memory", args.low_memory),
                        ("low_ram_streaming", args.low_ram_streaming)):
        if name in ctor:
            ctor_kwargs[name] = value
        elif name == "low_ram_streaming":
            print("note: this runtime has no `low_ram_streaming`; the lever is unavailable here")
    record["runtime"]["constructor_kwargs"] = {k: v for k, v in ctor_kwargs.items() if k != "model_dir"}

    call_params = inspect.signature(pipeline_cls.generate_and_save).parameters
    call_kwargs: dict = {
        "prompt": args.prompt,
        "output_path": str(args.output),
        "height": args.height,
        "width": args.width,
        "num_frames": args.num_frames,
        "frame_rate": args.frame_rate,
        "seed": args.seed,
    }
    if "image" in call_params:
        call_kwargs["image"] = str(image_path)
    elif args.require_image_to_video:
        print("\nREFUSING: --require-image-to-video was set but this pipeline takes no `image`.")
        save("refused", reason=f"{args.pipeline}.generate_and_save has no image parameter",
             accepted=sorted(call_params))
        return 3
    else:
        print(f"note: {args.pipeline} takes no `image`; this run is text-to-video")

    call_kwargs = {k: v for k, v in call_kwargs.items() if k in call_params}
    record["request"]["effective_call"] = call_kwargs
    print(f"\npipeline: {args.pipeline}  low_memory={ctor_kwargs.get('low_memory')}  "
          f"low_ram_streaming={ctor_kwargs.get('low_ram_streaming', 'n/a')}")
    print(f"canvas: {args.width}x{args.height}  frames: {args.num_frames} @ {args.frame_rate}fps  "
          f"({args.num_frames / args.frame_rate:.1f}s)")
    print(f"image-to-video: {'yes' if 'image' in call_kwargs else 'NO — text-to-video'}\n")

    mx.reset_peak_memory() if hasattr(mx, "reset_peak_memory") else None
    save("running")

    started = time.time()
    status, failure = "ok", None
    try:
        pipe = pipeline_cls(**ctor_kwargs)
        pipe.generate_and_save(**call_kwargs)
    except Exception as exc:
        status, failure = "failed", f"{type(exc).__name__}: {exc}"
        import traceback
        traceback.print_exc()
    elapsed = time.time() - started

    peak_metal = None
    for getter in ("get_peak_memory", "peak_memory"):
        fn = getattr(mx, getter, None)
        if callable(fn):
            try:
                peak_metal = int(fn())
                break
            except Exception:
                pass

    out = Path(args.output)
    produced = out.is_file()
    save(
        status,
        failure=failure,
        wall_seconds=round(elapsed, 3),
        peak_metal_bytes=peak_metal,
        peak_metal_gb=round(peak_metal / 1024**3, 2) if peak_metal else None,
        output=str(out),
        output_bytes=out.stat().st_size if produced else None,
        swap_at_end=_sysctl("vm.swapusage"),
    )

    print()
    if status == "ok" and produced:
        print(f"DONE in {elapsed:.1f}s — {out} ({out.stat().st_size / 1e6:.1f} MB)")
    else:
        print(f"FAILED after {elapsed:.1f}s — {failure}")
    if peak_metal:
        print(f"peak MLX Metal allocation: {peak_metal / 1024**3:.2f} GB "
              f"(Metal only — this is NOT the process footprint)")
    print(f"swap at end: {record['result'].get('swap_at_end')}")
    print(f"record: {results}")
    return 0 if (status == "ok" and produced) else 4


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
