# Image runtime input proof

This directory locks the minimal package surface used by the selected FLUX.2
Klein MLX 4-bit worker: `mflux`, MLX/Metal, and mflux's required Python
dependencies. It is deliberately separate from `packaging/voice-runtime`:
the existing Image source resolves mflux 0.17.5 / MLX 0.31.2, while Voice also
requires an exact mlx-audio source revision.

`requirements.lock.txt` is generated on an Apple-Silicon build host with:

```sh
uv pip compile --python 3.12 --generate-hashes \
  -c packaging/image-runtime/constraints-sonoma.txt \
  -o packaging/image-runtime/requirements.lock.txt \
  packaging/image-runtime/requirements.in
```

The lock is an installation input, not a runtime readiness result. A future
build can install it into an isolated CPython 3.12 runtime and run
`smoke_imports.py` without accessing model weights. The required MLX wheels
are explicit macOS 14.0 arm64 artifacts; the final bundle still needs native
binary auditing and physical original-M1/macOS-14 inference qualification.
