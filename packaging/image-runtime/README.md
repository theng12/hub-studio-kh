# Image runtime input proof

This directory locks the package surface used by the selected FLUX.2
Klein MLX 4-bit private worker: `mflux`, MLX/Metal, FastAPI/Uvicorn and their
required Python dependencies. It is deliberately separate from `packaging/voice-runtime`:
the existing Image source resolves mflux 0.17.5 / MLX 0.31.2, while Voice also
requires an exact mlx-audio source revision.

ST-01's inference-only lock had SHA-256
`6809919c3ec817d7de27d03cc7f2adfe2f52f283d842ea74098a45696cc07c51`.
ST-03 adds the HTTP worker dependencies while constraining all 66 original
package versions; the resulting lock has SHA-256
`5fd5e79db7f8bec7dfa5c3ebc0fd1495ba7460138b44caf06bb6c0346c186b4f`.
Regenerate it on an Apple-Silicon build host with:

```sh
git show 5358a52:packaging/image-runtime/requirements.lock.txt \
  > .build/st01-image-inference.lock.txt
uv pip compile --python 3.12 --generate-hashes \
  -c .build/st01-image-inference.lock.txt \
  -c packaging/image-runtime/constraints-sonoma.txt \
  -o packaging/image-runtime/requirements.lock.txt \
  packaging/image-runtime/requirements.in
```

The lock is an installation input, not a runtime readiness result. Build a new
immutable runtime on Apple Silicon with `bash packaging/image-runtime/build.sh
.build/image-runtime`. It uses the already verified CPython 3.12.14 archive and
cached exact wheel hashes, audits every arm64 Mach-O install ID/rpath/dependency
against macOS 14.0, signs rewritten wheel libraries locally, records all file
hashes, then imports mflux/MLX from two relocated paths with an empty environment.
It never accesses model weights. Physical original-M1/macOS-14 inference remains
a separate qualification gate.

The build also appends the pinned mlx-serve 26.10.1 release archive with
`append_mlx_serve.py`. The existing Python/mflux tree and its Mach-O audit remain
macOS 14 compatible. The native leaf has a separate, complete audit because its
unchanged upstream Mach-O headers require macOS 26.2; only the Qwen Image 2.1
MLX-Serve 4-bit manifest declares that conditional floor. To upgrade an already
verified runtime without resolving or rebuilding the unchanged Python packages:

```sh
.build/image-runtime/bin/python3 -I -B \
  packaging/image-runtime/append_mlx_serve.py \
  .build/image-runtime \
  .build/downloads/mlx-serve-bin-macos-arm64.tar.gz
```

The helper verifies the existing runtime manifest, the exact archive digest,
version, file inventory, arm64 slices, install IDs, dependencies, rpaths,
signatures and native deployment floors. It refuses archive traversal and every
destination collision. The pre-append manifests and any failed staging tree stay
under `_trash/image-runtime-native/`; the helper never removes them.
