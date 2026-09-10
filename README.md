# Studio Hub Standalone

Independent application project for **new Apple Silicon fleet nodes**. Existing Pinokio installations retain their current flow.

**Status: the Voice engine proof passed 665 tests and real Qwen speech/Whisper transcription after relocation. Native app preview implementation is in progress.** See [qualification evidence](qualification/voice-runtime.md) for remaining Sonoma and fleet requirements.

The first implementation packages the adopted Voice backend, its web interface, an isolated Python/MLX runtime for the Qwen/Whisper pilot, and FFmpeg/FFprobe into a relocatable `.app` structure. The original artifact is a command-line Voice worker proof. The native preview adds a window and six functional sections: Audio, Transcription, Image, Music, Video, Chat. Audio and Transcription reuse Voice; the other four engines and bundled Hub/GenStudio integration remain pending. macOS 14 Sonoma is the compatibility target, subject to clean-machine qualification.

## Build the native preview

```sh
bash native/build.sh
python3 -B packaging/stage_voice.py --runtime .build/voice-runtime --native-shell .build/StudioHub --output 'dist/Studio Hub Preview.app'
```

Open the app in Finder, choose a model library (or use its default folder), then use Audio or Transcription. The other four sections accurately report that their engines are not installed in this preview. Closing the window keeps the engine running; use the menu-bar icon to reopen it. Explicit Quit explains interruption before stopping the engine. Start at login is opt-in through macOS service management; login/reboot behavior still requires packaged-app qualification.

Use an existing complete Hugging Face cache by choosing its parent folder containing `hub`. For an empty library, use the existing Studio Models flow to download models. A polished standalone model import wizard remains future work. Keep the selected drive connected. To change the model folder or offline mode, stop the engine first.

This preview keeps browser-only presets and model-filter preferences for the current app session. Node settings, model folder, generated outputs and voice files remain on disk. Durable browser preferences will be integrated with Hub before the full app is considered ready.

The native executable also accepts `--data-dir`, `--models-dir` and `--offline` for isolated local testing. The build uses Swift/AppKit/SwiftUI/WebKit from the developer tools; target nodes need none of those build tools installed.

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
