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

That command uses `.build/voice-runtime-base/bin/python3` as its resolver interpreter. Review `requirements-pilot.txt` before changing scope: F5-TTS is deliberately absent because its TorchCodec dependency requires several shared FFmpeg ABIs that the bounded pilot does not ship.
