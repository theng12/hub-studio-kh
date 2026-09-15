# ST-01 runtime input contracts

This receipt records reproducible runtime inputs for the first Studio Hub
slice. It deliberately separates a locked/importable runtime from model
readiness: the ST-01 importer owns model-package file verification, while
Hub admission and a real generation receipt are outside this receipt.

The machine-readable companion is
[`st01-runtime-contracts.json`](st01-runtime-contracts.json). Its checksums
cover the lock files, including all registry-wheel hashes. The entries were
observed from clean, read-only sibling checkouts on 2026-09-15:

| Component | Observed commit | Version | Official remote |
| --- | --- | --- | --- |
| Hub | `06e893e1643bffbcbd162fae122e5a4d0f29e283` | 2.22.0 | `theng12/studiohub-mac` |
| Image | `36f88a0bc0abc07e3d7a8cf04424ab5336501827` | 1.32.2 | `theng12/imagestudio-mac` |
| Voice | `72e26b795cd3d0a6f76961f8137e4dfd4af59e9b` | 2.8.1 | `theng12/voicestudio-mac` |

These exact checkouts are newer than the older source inventory. They are
evidence only; no source tree, virtual environment, credentials, job data, or
model cache was copied from them.

## Runtime split

| Engine | Locked input | Selected import surface | Status |
| --- | --- | --- | --- |
| `voice-mlx` | Voice hash lock, SHA-256 `93766f4…0ef66` | `mlx_audio`, MLX, Torch/TorchAudio, Transformers | Existing immutable artifact can be read/tested; source lock identifies its input only. |
| `subtitle-mlx` | The exact Voice lock | MLX + `mlx_audio` Whisper surface | Reuse remains conditional on later independent worker/lifecycle proof. |
| `image-mflux` | New Image hash lock, SHA-256 `680991…07c51` | `mflux` 0.17.5, MLX 0.31.2, Flux2Klein import | Built-side isolated import proof, separate from Voice. |

The Image pin comes from the current Image source's
`app/requirements-generation.lock.txt`: `mflux==0.17.5`, `mlx==0.31.2`, and
`mlx-metal==0.31.2`. Its lock retains mflux's resolved dependencies, including
Torch, rather than claiming they are removable without an import/reachability
proof. mflux's installed distribution declares an MIT license; its full
redistribution notice, and notices for every resolved distribution, must be
copied into a final immutable Image runtime during the future bundle build.

The exploratory `.build/st01-image-runtime` proof used CPython 3.12.13 on the
Apple-Silicon macOS 26.6.2 build host. Contract evidence instead uses
`.build/st01-image-runtime-31214`, a new environment created with the existing
read-only bundled Voice CPython 3.12.14 interpreter. `uv pip sync` accepted the hash lock and
the model-free smoke import completed for mflux 0.17.5, MLX/Metal 0.31.2,
Pillow 12.3.0, NumPy 2.5.3, Safetensors 0.8.0 and Transformers 5.17.0. The
installed `mlx/core.cpython-312-darwin.so` has an arm64 slice and `minos 14.0`;
its recorded dependencies are its bundled `@rpath/libmlx.dylib`, Apple Metal,
Foundation, QuartzCore and Accelerate frameworks, and `/usr/lib` C++/System
libraries. The environment has 69 distributions because its venv includes
`pip`; the lock itself resolves 68 shipped packages. This is a direct wheel
observation. The completed native audit below covers every Mach-O file in this
build-side environment, which is still not a relocatable Image bundle.

The complete distribution metadata is preserved at
[`packaging/image-runtime/manifest/python-distributions.json`](../packaging/image-runtime/manifest/python-distributions.json),
with 179 copied declared license files in `packaging/image-runtime/licenses/`.
The inventory has SHA-256 `6e77d5def83d8033af64b42f3bb387b603209b089a00b4761d87e74f13191868`.
The existing `runtime_packaging.py audit` examined all 182 Image-environment
Mach-O files: no binary lacks arm64 and none has a macOS floor above 14.0 (80
are at 11.0, one at 12.0, 69 at 13.0, and 32 at 14.0). It rejected the virtual
environment for packaging: 112 external install IDs, two external rpaths, and
an unresolved `libmlx.dylib` dependency on `@rpath/libjaccl.dylib` remain. This
separates the wheel-floor result from relocation work required for a final
Image artifact. The complete failed-audit receipt is retained at
[`packaging/image-runtime/manifest/macho-audit.json`](../packaging/image-runtime/manifest/macho-audit.json),
so integration can distinguish those relocation defects from platform-floor
failures.

## mlx-audio reconciliation

The current Voice runtime lock and its read-only built artifact agree on
`mlx-audio` 0.4.7 at commit
`2c9461f5d8315fa8e7013ab2729495b2bb83d384`, with MLX 0.31.2 and
Transformers 5.14.1. Earlier audit evidence for Qwen 0.6B Base names a
different `d28d68…` mlx-audio revision; that object is not present in the
current Image checkout and is not substituted here. The two lines remain
separate historical/runtime facts. A new Base 0.6B run is required before
asserting it works with the `2c9461…` lock.

A no-weight, offline import against the read-only Voice runtime imported
Qwen3-TTS Base, VoxCPM2, OmniVoice, Whisper, and the TTS/STT loaders. The
selected Base package is pinned at
`mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit@e7dd0585652209fa0d7783659aad4e8a324de11c`.
Its recognized `config.json` model type is `qwen3_tts`; current Voice passes a
local snapshot `Path` to `mlx_audio.tts.utils.load_model`, whose 0.4.7 mapping
loads `mlx_audio.tts.models.qwen3_tts.qwen3_tts`. Its package initializer
exports that module's `Model`. The selected model inventory also includes its
`speech_tokenizer/` assets. This was an import-only proof: no model weights were
downloaded and it is not a READY or generation claim.

## Reference workflows

Each clean reference checkout declares `pytest.ini` with `pythonpath = app` and
`testpaths = app/tests`, so its declared source check is `python -m pytest`
from the Hub, Image, or Voice checkout. The projects are Pinokio launchers
rather than independently built binaries: Hub declares `install.js` then
`start.js`; Image and Voice declare `install.js`, optional
`install_generation.js`, then `start.js`. These workflows are recorded only;
ST-01 did not run a live reference launcher or hosted compute.

## macOS and hardware boundary

Both runtime contracts set an explicit Apple Silicon arm64/macOS 14.0 target.
The MLX and mlx-metal wheels are pinned by full PyPI URL and SHA-256 to their
`macosx_14_0_arm64` artifacts. This controls the selected wheel floor; it does
not prove an M1 will generate on the oldest supported macOS 14 point release.
The existing Voice runtime's native audit is a read-only historical artifact.
After relocation, the final packaged Image artifact needs a fresh Mach-O audit;
then each selected model needs physical original-M1/macOS-14 inference evidence.
