# Studio Hub Standalone

Independent native macOS application project for local creation on Apple Silicon. Existing Pinokio installations and their maintenance flow remain untouched.

**Version 0.3.0 status:** the native SwiftUI/AppKit redesign is implemented; final artifact verification is recorded in [redesign evidence](qualification/native-redesign.md). It presents six functions—Audio, Transcription, Image, Music, Video and Chat—with separate Audio and Transcription workspaces and shared Overview, Models, Jobs and Settings management. Connections to an API, Studio Hub or GenStudio are optional; local asset creation is the primary flow.

Audio currently uses the adopted Voice runtime and Qwen speech model. Native job `c56f0f41d938` completed and was saved. The separate Transcription workspace imported that WAV, returned the expected text and exported SRT subtitles. Image, Chat, Music and Video engine integration, plus optional controller integration, remain pending. A navigation item is not treated as an implemented engine.

## Native build

Build the local artifact on an Apple Silicon Mac:

```sh
bash native/build.sh
python3 -B packaging/stage_voice.py --runtime .build/voice-runtime --native-shell .build/StudioHub --output 'dist/Studio Hub.app'
```

`native/build.sh` selects the installed macOS 26.5 SDK because the installed SDK 27 toolchain is missing the SwiftUI macros plugin needed by this source. The deployment target remains macOS 14. Local execution was tested on macOS 26; physical Sonoma compatibility remains unqualified. The target app needs no Swift build tools, Python, Pinokio or Homebrew on the user machine.

Open `dist/Studio Hub.app`, choose a model library, and use Audio or Transcription. Models, jobs, settings and outputs are managed from the central sidebar. A missing model volume stays unavailable with an actionable error; the app does not silently create a replacement cache or redownload elsewhere. Closing the window leaves the owned worker available from the menu bar. Explicit Quit handles active work before stopping that worker.

Transcription accepts an audio or video file and exports Text, SRT or VTT. The word-timestamp control is not exposed because a word-level result viewer is not implemented.

## Local state and optional connections

Model libraries may live in Application Support or on an external SSD. The selected location is retained outside the app bundle. The Connections page is for optional API or external Studio Hub/GenStudio control; it is not required to create local assets.

The app has no signing, notarization, login-registration or physical Sonoma test evidence yet. Login-item status is read from macOS service management, but login/reboot behavior remains unqualified.

## Historical Voice proof

The following command builds the earlier command-line Voice proof and is retained as historical packaging evidence:

```sh
bash packaging/voice-runtime/build.sh
python3 -B packaging/stage_voice.py --runtime .build/voice-runtime --output 'dist/Studio Hub Voice Proof.app'
'dist/Studio Hub Voice Proof.app/Contents/MacOS/StudioHubVoice' --models-dir '/absolute/path/to/Model Library'
```

The Voice proof uses a complete Hugging Face cache containing model snapshots, configurations, tokenizers and companion assets. The model library is separate from the application. Use `--offline` after all selected models are imported; a missing selected directory is rejected. The pilot excludes F5-TTS and does not qualify every catalog engine.

The historical 0.2.0 evidence recorded 676 passing regression tests after relocation. That count is prior evidence and is not a claim about a current 0.3.0 rerun.

## Project records

- [Accepted design and milestones](docs/standalone-plan.md)
- [Native shell design](DESIGN.md)
- [Native workspaces and management UI](native/StudioViews.swift)
- [Native lifecycle and menu-bar shell](native/StudioHub.swift)
- [Native build script](native/build.sh)
- [API contract inventory](research/redesign-api-contracts.md)
- [Runtime and native dependency inventory](research/runtime-inventory.md)
- [Redistribution inventory](research/licenses.md)
- [Voice qualification evidence](qualification/voice-runtime.md)
- [Upstream source identities](research/source-checkouts.json)

The adopted Voice source remains under `components/voice`; sibling Pinokio repositories are source evidence only. Use planning-with-files with `PWF_PLAN_ROOT=/build/Projects/studiohub-standalone` and `PLAN_ID=2026-09-10-standalone-app-feasibility` when continuing the project.
