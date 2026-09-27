#!/usr/bin/env python3
"""What the YuE2 runtime must be able to import, checked inside the built tree.

The runtime is pinned by content, and a content pin only means something if the tree it describes
can do the job. This is the cheap half of that check — the expensive half is the two measured runs
recorded in the manifest's qualification (a 24 s clip at 10.79 GB peak and a 120 s song at
11.93 GB).

Run by `build.sh` with the built interpreter, `-I -B`, before the digest is printed and the tree is
made read-only.
"""
from __future__ import annotations

import importlib
from pathlib import Path
import sys

REQUIRED = (
    # The port itself: `lyra` is its pipeline package and `yue2` its vendored protocol/storage.
    ("lyra", "the port's pipeline package"),
    ("lyra.pipeline", "YuE2Pipeline, what the bridge drives"),
    ("yue2", "the port's vendored protocol and checkpoint storage"),
    ("yue2.protocol", "the sampling and context constants the pipeline reads"),
    # MLX and the LM layer the autoregressive stage is built on. The port pins `mlx==0.32.2` and
    # `mlx_lm==0.31.3`; a tree carrying another pair would be a different engine wearing the name.
    ("mlx.core", "MLX itself"),
    ("mlx_lm", "the AR stage's model plumbing"),
    # Decoding and the artifact: 48 kHz FLAC written through soundfile, arrays through numpy.
    ("numpy", "latent and audio arrays"),
    ("soundfile", "writing the FLAC"),
    ("psutil", "the port's own memory monitor, which fails closed on swap growth"),
    ("transformers", "the tokenizer path"),
)


def main() -> int:
    # The port's own `vendor/yue` tree has to be importable for `yue2` to resolve; `build.sh` copies
    # it beside the environment and passes it here, because a build that installed the wheel without
    # it would import `lyra` and then fail on its first real call.
    if len(sys.argv) > 1:
        sys.path.insert(0, str(Path(sys.argv[1]) / "vendor" / "yue" / "src"))
        sys.path.insert(0, str(Path(sys.argv[1]) / "src"))

    failures: list[str] = []
    for module, why in REQUIRED:
        try:
            importlib.import_module(module)
        except Exception as exc:  # noqa: BLE001 - the message is the point
            failures.append(f"  {module}: {exc}  ({why})")
    if failures:
        print("the YuE2 runtime cannot import what it is for:", file=sys.stderr)
        print("\n".join(failures), file=sys.stderr)
        return 1

    import mlx.core as mx

    from lyra.pipeline import YuE2Pipeline

    assert YuE2Pipeline is not None
    print(f"yue2 runtime ok — mlx {mx.__version__}, lyra pipeline importable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
