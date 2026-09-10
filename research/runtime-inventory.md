# Voice runtime inventory

Date: 2026-09-10. Scope: the milestone-one Qwen MLX generation and Whisper MLX transcription proof on Apple Silicon. This is an input and boundary inventory, not a claim that every Voice catalog engine is qualified.

## Artifact contract

The candidate is built by `packaging/voice-runtime/build.sh` and is consumed through these bundle-relative paths:

| Purpose | Runtime path |
| --- | --- |
| Isolated interpreter | `bin/python3` |
| Python packages | `lib/python3.12/site-packages` |
| Media conversion/probe | `bin/ffmpeg`, `bin/ffprobe` |
| Exact Python resolution | `manifest/requirements.lock.txt` |
| Installed distribution and license metadata | `manifest/python-distributions.json` |
| Native load-command evidence | `manifest/macho-audit.json` |
| Immutable file hashes, modes and symlink targets | `manifest/runtime-files.json` |
| Redistributed notices found in package metadata | `licenses/` |

The app should set `STUDIOHUB_TOOLS_DIR` to the runtime `bin` directory. No Homebrew, Conda, Pinokio runtime, system Python package, or developer tool is a runtime input.

## Locked build inputs

| Input | Selection | Integrity |
| --- | --- | --- |
| CPython | python-build-standalone `3.12.14+20260901`, `aarch64-apple-darwin-install_only` | SHA-256 `3ee3ee547cedfeb7c2b16b2b7156039f7b470bb8f857e226fd3d2eb11db83c76` |
| FFmpeg and FFprobe | FFmpeg source `9.0.1`, built locally with `MACOSX_DEPLOYMENT_TARGET=14.0`, static internal FFmpeg libraries, no autodetected third-party libraries | SHA-256 `cf38e0e28c7e5605942c4a77755349b0145804a397af37eb1fb4c77cb237f635` |
| Open JTalk dictionary | UTF-8 dictionary `1.11`, installed and exercised on the build host | SHA-256 `fe6ba0e43542cef98339abdffd903e062008ea170b04e7e2a35da805902f382a` |
| Python packages | uv hash lock generated from `requirements-pilot.txt` | Every registry artifact is hash checked; VCS package is fixed to a commit |

python-build-standalone documents its install-only distributions as portable, self-contained CPython distributions. uv documents that its managed Python distributions come from that project. Sources: [python-build-standalone running documentation](https://gregoryszorc.com/docs/python-build-standalone/main/running.html), [uv Python versions](https://docs.astral.sh/uv/concepts/python-versions/).

The adopted Voice declaration pins the coupled runtime set to `transformers==5.14.1`, `tokenizers==0.22.2`, `mlx==0.31.2`, `mlx-lm==0.31.3`, and mlx-audio commit `2c9461f5d8315fa8e7013ab2729495b2bb83d384`. The checked-in adopted `requirements-generation.lock.txt` records older Transformer and mlx-audio values, so it was not treated as authoritative for this build. `refresh-lock.sh` resolves the current declarative inputs and emits a new complete hash lock.

MLX and mlx-metal are constrained to their exact `macosx_14_0_arm64` wheels. A plain uv `aarch64-apple-darwin` resolution models macOS 13 and rejects these wheels, so the artifact URLs and hashes are explicit. The native audit still checks the arm64 slice of every installed Mach-O; a wheel filename alone is not accepted as compatibility proof.

## Bounded dependency set

The final pilot lock contains the adopted base server and every non-F5 direct generation dependency. It intentionally excludes `f5-tts` and the dedicated numba/librosa/TorchCodec tree that F5 introduced. TorchCodec 0.16 ships loaders for several FFmpeg ABIs but no corresponding macOS `libav*.dylib` files; the separately bundled static FFmpeg executables cannot satisfy those loader dependencies. The Voice backend has no direct TorchCodec caller. Bundling several shared FFmpeg generations merely to make an unqualified F5 engine load would expand this proof beyond its accepted Qwen/Whisper scope.

The selected pilot versions include:

| Package | Exact version or revision | Role |
| --- | --- | --- |
| fastapi | 0.141.1 | Voice API |
| mlx / mlx-metal | 0.31.2 | Apple Silicon tensor runtime |
| mlx-lm | 0.31.3 | MLX language-model utilities |
| mlx-audio | 0.4.7 code at `2c9461f…` | Qwen TTS and Whisper STT implementations |
| transformers / tokenizers | 5.14.1 / 0.22.2 | processor and tokenizer compatibility pair |
| torch / torchaudio | 2.14.0 / 2.11.0 | retained non-F5 audio helper surface |
| pyopenjtalk | 0.4.1, source-built on this host | Japanese G2P support for retained non-F5 engines |

TorchAudio 2.11 uses PyTorch's stable ABI and its current install documentation supports PyTorch 2.11 and later. The build smoke also runs `torchaudio.functional.resample` on a real tensor. Source: [TorchAudio binary installation and compatibility](https://docs.pytorch.org/audio/main/installation.html).

## Native and external inputs

The native audit recognizes Mach-O files by magic bytes, checks the arm64 slice explicitly (including universal2 binaries), reads `LC_BUILD_VERSION`/`LC_VERSION_MIN_MACOSX`, rejects a minimum above 14.0, rejects bare or non-system absolute dependencies, rejects non-system absolute `LC_RPATH` entries, and verifies loader-relative dependencies resolve to files inside the bundle. Stale upstream wheel CI paths are removed on the build host; every modified Mach-O receives an ad-hoc signature before import testing. The node performs no prefix or load-command rewriting.

The only app-invoked media commands are `ffmpeg` and `ffprobe`. macOS `sysctl` is used for hardware reporting and is an operating-system facility rather than a redistributed dependency. The packaged release path disables the Git/Conda/Pinokio maintenance path; those commands are not runtime dependencies of the standalone worker.

## Models and writable state

No model is included in this runtime. Complete model snapshots are separate, validated inputs under the app's model storage. The pilot separately records a Qwen CustomVoice 0.6B snapshot and a Whisper snapshot; their identity and licenses belong in model qualification evidence.

All mutable state stays outside the runtime:

| State | Standalone location contract |
| --- | --- |
| Settings, storage and memory policy, fleet token | `STUDIOHUB_DATA_DIR/voice` |
| Voice references, metadata, transcripts, embeddings | external Voice data directory |
| Generated output, history and partial files | external Voice data directory |
| Reference-audio cache | external Voice data directory |
| Service logs | external Voice data directory |
| Hugging Face/model snapshots | `STUDIOHUB_MODELS_DIR` |
| Temporary files | caller-provided `TMPDIR` |

`pyopenjtalk` normally fetches its dictionary into site-packages on first G2P use. The builder downloads the exact dictionary, verifies its hash, installs it, and executes G2P before making the runtime read-only. Final smoke tests use `-I -B`, disable user site packages and bytecode, run with a clean minimal `PATH`, and verify the file manifest after execution from two paths, including one containing spaces.

## Qualification boundary

The build host is an Apple Silicon Mac running macOS 26.6.2. Native load-command floors and real relocation can be verified there, but they do not replace a physical clean Sonoma test. Before production use, the exact immutable artifact still needs the plan's Sonoma 14 machine run without Command Line Tools and a repeat on a newer supported macOS release. Only Qwen CustomVoice 0.6B generation and the selected Whisper transcription are pilot engines; other catalog entries remain unqualified or unavailable as their dependency/model proofs require.
