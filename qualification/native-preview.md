# Native Audio and Transcription preview — 0.2.0

This local preview adds a SwiftUI/AppKit window around the adopted Voice interface. Its six sections are Audio, Transcription, Image, Music, Video and Chat. Only Audio and Transcription currently have a bundled engine. Hub management, Image, Chat, Music and Video integration remain pending.

## Development window evidence

The development GUI generated a 6.16-second, 24 kHz WAV using the imported Qwen 0.6B CustomVoice 8-bit model. Its native save dialog wrote the file, the native upload dialog selected it for Transcription, and MLX Whisper returned: “This is the Native Studio Hub preview. Audio and transcription are working together.”

The WAV contains 147,840 frames / 295,724 bytes; SHA-256 `da4e3b3b5771033811b4b3bafb648ec1826b1a2a4b58fb61e8679fce7dac001c`. Generation took 29.81 seconds on the shared development host. The editor retained its draft while switching functions and closing/reopening the window. The menu status reflected work and readiness. Explicit Quit stopped both the window process and its worker.

This UI run used a disposable development bundle. The final artifact's identity and independent relocation/model test are recorded separately below. Local GUI evidence is in `.build/native-gui-evidence.json`, `.build/native-gui-generation.json` and `.build/native-preview-audio.wav`.

Independent review covered worker readiness, process ownership, parent-loss cleanup, scoped forced shutdown and embedded navigation. A real Uvicorn shutdown test caught a stale readiness file after SIGTERM; readiness cleanup now runs before Uvicorn re-raises that signal. A real subprocess regression checks that the isolated native executor exits after its supervisor dies and leaves an unrelated process alive.

## Remaining qualification

- Clean physical Apple Silicon Sonoma machine; the local host is macOS 26.6.2 (25G83), 16 GiB. The native executable targets macOS 14.0; that is a compatibility target, not a Sonoma result.
- Login/reboot behavior, microphone permission/capture and opt-in autoplay. Login registration was not enabled during tests.
- Complete model download/import UX and missing-drive recovery on an actual external SSD. Browser-only presets and model-filter settings currently last for the app session; generated files, voice files and native settings persist externally.
- Hub/Image integration, then Chat/Music/Video packaging and truthful Hub routing. Current Music uses Torch/MPS; MLX is used where the engine implements it.
- GenStudio authentication, mixed-fleet routing, memory admission and real new-node qualification. Existing Pinokio installations retain their own flow.
- Physical network isolation, model/license redistribution review, and distribution packaging. No Developer ID/notarization submission, deployment or fleet enrollment occurred.

See [accepted plan](../docs/standalone-plan.md), [function inventory](../research/function-scope-inventory.md), and the historical [0.1.0 worker proof](voice-runtime.md). This preview is not a qualified fleet release.

## Final local build evidence

- Artifact: `dist/Studio Hub Preview.app`, version **0.2.0**, approximately **1.7 GB allocated**. Built from clean commit `5b4ddf4215508a3be6d90bd0614f5a51b9ffe9c6`.
- App tree SHA-256: `81d3e3717b5359c3c7206eed54d522b6ab82d04b35827d33d76425c9cc020d75`. [Release manifest](native-release-manifest.json).
- Combined adopted backend, frontend and packaging suite: **676 passed**, five upstream warnings, 41.43 seconds (`.build/native-final-regression-complete.log`).
- Native boundary checks passed and optimized arm64 build succeeded (`.build/native-build.log`); Swift 6.4 / SDK27.0, Mach-O deployment target14.0. Native executable occupies 544 KiB allocated and links system frameworks.
- Focused packaging/lifecycle suite: **15 passed**, including real Uvicorn shutdown cleanup.
- Final native binary launched its real bundled worker; health reported version0.2.0 and the implementation commit above. Terminating this task-owned GUI process caused the owned worker to exit and remove readiness. [Launch evidence](native-launch.json). UI automation separately opened a first-run instance without arguments; it remained unconfigured and was stopped. No login registration occurred.
- The earlier full-window generation/transcription/save/upload result is [recorded separately](native-gui.json), without conflating the development app and final artifact.

Final relocation smoke passed: two worker launches, exact release/API identity, validated imported model hashes, real Qwen speech and Whisper transcription, and unchanged bundle digest after both launches. [Smoke report](native-smoke.json), [transcription](native-transcription.json). First startup: 11.75 seconds; relocated startup: 2.907 seconds. Generation including model load: 28.638 seconds; speech plus transcription: 50.124 seconds. Audio: 241964 bytes, SHA-256 `36b937640719dc84fd2385ae0c1224eecec77302b6f15068d460ac068e9e597b`. These are shared-host observations, not fleet performance estimates.

Shutdown logged Python resource_tracker cleanup of 13 semaphore objects; no task-owned worker/native processes remained afterwards. This warning remains in the retained smoke log for follow-up during long-run lifecycle qualification.

Final cleanup found native children from older, pre-fix 0.1.0 smoke runs and the development GUI run. Exact executable paths, parent IDs and test-state working directories were verified before stopping them; the historical report is corrected. No child from the final 0.2.0 smoke remained. Removed two disposable app copies (about 3.4 GB allocated), retaining the final app, historical proof, logs, audio and JSON evidence. The delivered app digest was rechecked after its native launch and matched the recorded value.
