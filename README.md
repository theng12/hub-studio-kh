# Studio Hub Standalone

Independent application project for **new Apple Silicon fleet nodes**. Existing Pinokio installations retain their current flow.

**Status: the local Voice proof passes 665 tests and real Qwen speech/Whisper transcription after relocation.** See [qualification evidence](qualification/voice-runtime.md) for remaining Sonoma and fleet requirements.

The first implementation packages the adopted Voice backend, its web interface, an isolated Python/MLX runtime for the Qwen/Whisper pilot, and FFmpeg/FFprobe into a relocatable `.app` structure. This is a command-line Voice worker proof; the native window, bundled Hub/Image services, and GenStudio fleet integration are later milestones. macOS 14 Sonoma is the compatibility target, subject to clean-machine qualification.

## Build and run the Voice proof

Build on an Apple Silicon Mac with the development tools listed in [the runtime inventory](research/runtime-inventory.md). Target nodes will use the bundled runtime, without installing Python, Pinokio, Homebrew, or build tools.

```sh
bash packaging/voice-runtime/build.sh
python3 -B packaging/stage_voice.py --runtime .build/voice-runtime --output 'dist/Studio Hub Voice Proof.app'
'dist/Studio Hub Voice Proof.app/Contents/MacOS/StudioHubVoice' --models-dir '/absolute/path/to/Model Library'
```

The command prints a loopback URL for the existing Voice web interface. Model Library is a Hugging Face cache root containing complete model snapshots, including configurations, tokenizers and companion assets. It is separate from the application. Use `--offline` once the selected models are fully imported; a missing selected directory is rejected. The pilot excludes F5-TTS; other catalog engines have not been qualified. A user-friendly model import/download flow is not implemented yet.

Settings, credentials, outputs and logs default to `~/Library/Application Support/StudioHub Standalone`; `--data-dir` selects an isolated test location. The launcher selects an available loopback port and strips inherited development and fleet settings. Git-based updates and service installation are unavailable in this proof; app replacement is the planned update mechanism.

## Verification

```sh
python3 -B -m unittest discover -s tests -p 'test_voice_bundle.py'
python3 -B -m unittest discover -s tests -p 'test_runtime_packaging.py'
python3 -B packaging/smoke_voice.py --artifact 'dist/Studio Hub Voice Proof.app' --output .build/voice-smoke
```

The smoke helper starts the worker, checks health/catalog/connectivity and credentials, moves the same app into a path containing spaces, starts it again, and verifies unchanged bundle contents. Add `--models-dir /absolute/path/to/imported-model-library` to exercise the pinned Qwen speech model and Whisper transcription model recorded in [model inputs](qualification/model-inputs.json). It uses Hugging Face offline mode; physical network isolation is a separate acceptance check.

The adopted backend has its own regression suite under `components/voice/app/tests`. Run it with the completed generation runtime and an external development-only pytest installation. Never install test dependencies into a finalized runtime.

## Project records

- [Accepted design and milestones](docs/standalone-plan.md)
- [Active plan](.planning/2026-09-10-standalone-app-feasibility/task_plan.md)
- [Runtime and native dependency inventory](research/runtime-inventory.md)
- [Redistribution inventory](research/licenses.md)
- [Voice qualification evidence](qualification/voice-runtime.md)
- [Upstream source identities](research/source-checkouts.json)

Use planning-with-files with `PWF_PLAN_ROOT=/build/Projects/studiohub-standalone` and `PLAN_ID=2026-09-10-standalone-app-feasibility`. The existing Voice Graphify snapshot is a navigation aid; confirm stale paths against adopted source. Ponytail keeps the implementation focused on the smallest working packaging proof.
