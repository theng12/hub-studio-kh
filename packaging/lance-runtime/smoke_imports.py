#!/usr/bin/env python3
"""What the Lance runtime must be able to import, checked inside the built tree.

The runtime is pinned by content, and a content pin only means something if the tree it describes
can do the job. This is the cheap half of that check — the expensive half is the measured run
recorded in the manifest's qualification.

Run by `build.sh` with the built interpreter, `-I -B`, before the digest is printed and the tree is
made read-only.
"""
from __future__ import annotations

import importlib
import sys

REQUIRED = (
    # The runner itself and the pipeline the worker drives.
    ("lance_mlx", "the runner"),
    ("lance_mlx.pipeline.t2v", "text-to-video, the lane's operation for this model"),
    # Its two git-pinned dependencies: the pack pins them to exact commits because Qwen2.5-VL's
    # mRoPE churns upstream, and a runtime that imported fine against a different revision would be
    # a different runner wearing the same name.
    ("mlx_vlm", "Qwen2.5-VL, the text/vision tower the pipeline loads"),
    ("mlx_video", "the Wan2.2 VAE path Lance decodes with"),
    ("mlx.core", "MLX itself"),
    # The mux path: the bridge writes frames with imageio and points it at the app's own ffmpeg,
    # so these two must import (the ffmpeg binary itself is not in this runtime — see the bridge).
    ("numpy", "frame arrays"),
    ("imageio", "writing the MP4"),
    ("PIL", "frame handling inside the runner"),
)


def main() -> int:
    failures: list[str] = []
    for module, why in REQUIRED:
        try:
            importlib.import_module(module)
        except Exception as exc:  # noqa: BLE001 - the message is the point
            failures.append(f"  {module}: {exc}  ({why})")
    if failures:
        print("the Lance runtime cannot import what it is for:", file=sys.stderr)
        print("\n".join(failures), file=sys.stderr)
        return 1

    import mlx.core as mx
    import lance_mlx

    print(
        "lance runtime ok — "
        f"lance_mlx {getattr(lance_mlx, '__version__', 'unknown')}, mlx {mx.__version__}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
