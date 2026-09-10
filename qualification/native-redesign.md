# Native local-creation redesign — 0.3.0

This preview replaces the embedded legacy web workspace with native SwiftUI/AppKit pages. Audio and Transcription are separate workspaces. Overview reports local jobs and model readiness; Models, Jobs and Settings are shared. The six creation destinations remain visible, with Image, Music, Video and Chat accurately marked as engines not installed. API exposure and external Studio Hub/GenStudio control are optional secondary features; the current API listens only on this Mac and controller integration is pending.

## Observed local results

- Native Audio generated job `c56f0f41d938` using the imported Qwen 0.6B CustomVoice 8-bit model. The native Save panel exported its 7.6-second WAV.
- Native Transcription selected that WAV, used bundled MLX Whisper large-v3 turbo, displayed the expected sentence and exported SRT subtitles. [Exact result and WAV identity](redesign-gui.json).
- Audio draft, model and speaker choices, source recording and latest transcript survived restart. Transcription offers Text/SRT/VTT; word-level review is not implemented.
- Visual and accessibility checks covered Overview, Audio, Transcription, Models, Jobs, Settings and Connections. Audio, Transcription and Models were inspected at default and minimum window sizes. The installed-only model filter correctly showed the two imported pilot models. Explicit Quit stopped the owned app and worker.
- These generation/export actions used a disposable development harness sharing the immutable 0.2 runtime resources; final artifact checks are recorded separately below.

## Checks

- Optimized arm64 native build and native boundary/API fixture checks passed. Build SDK is macOS 26.5 because the installed 27 SDK lacks the required SwiftUI State macro plugin; deployment target remains macOS 14.
- Full adopted backend/frontend/standalone/packaging suite: **676 passed**, five upstream warnings, 37.89 seconds. Log `.build/redesign-regression-verified.log`.
- Separate packaging unittest discovery: **28 passed**, 8.462 seconds.
- Reconnect regression tests prevent stale generation publication, duplicate polling, uploads sent to a replacement worker and false token-save success. Private-log tests verify append, 0600 permissions and rejection of symlinks. Existing parent-loss, instance ownership, runtime isolation and backend model/deletion safeguards remain.
- No real token save/test, model removal/download, login registration, publication, deployment or fleet enrollment was performed. No Pinokio installation or finalized runtime was changed.

## Remaining work

Image, Chat, Music and Video engines and generation forms; remaining Audio model-specific controls; optional authenticated controller integration; clean physical Sonoma and external-SSD qualification; login/reboot and distribution signing. Only the exact Qwen/Whisper pilot models have end-to-end evidence here. Jobs retains backend Audio history and the latest transcript; a full transcription history is future work. This is a local preview, not a qualified fleet release.

## Final artifact

- Artifact: `dist/Studio Hub.app`, **0.3.0**, approximately **1.7 GB allocated**, built from clean implementation commit `9efb6f0142bb64cdb8294107a4a95784d9306f60`. [Release manifest](redesign-release-manifest.json).
- App tree SHA-256: `7634717fc7b5dd66681e807d15e4a069b1d3be6e4558b4679091159e95b4d914`.
- Final packaged native window launched its owned worker with the matching 0.3.0 release, restored Audio and Transcription state, and retained the Script label and generation controls at minimum size. Explicit Quit removed its worker and readiness file. [Launch evidence](redesign-launch.json).
- Relocation smoke passed two starts, exact release/API identity, imported model hash validation, real Qwen speech and MLX Whisper transcription, and unchanged bundle bytes. [Smoke report](redesign-smoke.json), [transcription](redesign-transcription.json). First startup 8.312 s; relocated startup 1.986 s; model generation 16.2 s; generation plus transcription 29.54 s. Shared-host observations, not fleet performance guarantees.
- Shutdown still reports upstream resource_tracker cleanup of 13 semaphore objects, as in 0.2.0. All task-owned GUI, worker and model subprocesses exited; long-running lifecycle qualification remains outstanding.
- Disposable development and relocated app copies were removed after verification; final 0.3.0 and historical 0.2.0/0.1.0 artifacts remain. No publishing, deployment or enrollment occurred.
