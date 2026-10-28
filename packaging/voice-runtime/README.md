# Voice runtime builder

This directory builds the bounded Apple Silicon Voice runtime used by the Qwen MLX generation and Whisper MLX transcription proof. It produces a read-only, relocatable CPython 3.12 runtime with bundled FFmpeg and FFprobe, exact dependency hashes, native load-command evidence, copied license files and an immutable file manifest.

From the repository root on Apple Silicon macOS:

```sh
packaging/voice-runtime/build.sh
```

The default output is `.build/voice-runtime`; an alternate output must remain inside the repository:

```sh
packaging/voice-runtime/build.sh .build/another-voice-runtime
```

The build needs `uv`, Xcode Command Line Tools (including `clang`, `lipo`, `otool`, `install_name_tool` and `codesign`) and network access. Nodes need none of them. Source-only wheels such as pyopenjtalk and FFmpeg are built only on the build host. The builder refuses to overwrite an existing output.

Refresh the Python hash lock after an intentional input change with:

```sh
packaging/voice-runtime/refresh-lock.sh
```

That command uses `.build/voice-runtime-base/bin/python3` as its resolver interpreter, and resolves
every package to its latest release. When the input change is a single added dependency, keep the
versions already locked and resolve only the newcomer:

```sh
packaging/voice-runtime/refresh-lock.sh --keep-locked-pins
```

A plain refresh of a lock that is weeks old also moves every unrelated package, so the addition
arrives mixed with upstream churn nobody asked for. The first `--keep-locked-pins` run writes the
new packages without citing the previous lock beside them; a second run adds that citation line
and is byte-stable, which is the state this lock is committed in. Review `requirements-pilot.txt` before changing scope: F5-TTS is deliberately absent because its TorchCodec dependency requires several shared FFmpeg ABIs that the bounded pilot does not ship.

## Test-only content is removed

A wheel ships its own `tests/` directory, so a Python runtime is full of code no product path runs. The build therefore ends with `runtime_packaging.py strip-tests`, which removes wheel test trees, `conftest.py`/`test_*.py` files and any test framework (pytest, pluggy, iniconfig, …) and rewrites `manifest/runtime-files.json` to match. A tree it cannot clean is refused, not shipped. `packaging/stage_voice.py` runs the same sweep when it copies a runtime into an app bundle, because `.build/voice-runtime` is reused between releases: 0.6.200 shipped pytest 9.1.1 and ~3,200 test files that somebody had installed into the built tree afterwards.

The sweep keeps a `tests` directory or `test_`-named module that the remaining sources still import — `mpmath.__init__` imports `mpmath.tests`, and torch's Dynamo imports `torch._inductor.test_operators` — so a rule based on the name alone would break the runtime.
