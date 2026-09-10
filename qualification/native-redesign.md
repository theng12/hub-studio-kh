# Native local-creation redesign — 0.3.0

This preview replaces the embedded legacy web workspace with native SwiftUI/AppKit pages. Audio and Transcription are separate workspaces. Overview reports local jobs and model readiness; Models, Jobs and Settings are shared. The six creation destinations remain visible, with Image, Music, Video and Chat accurately marked as engines not installed. API exposure and external Studio Hub/GenStudio control are optional secondary features; the current API listens only on this Mac and controller integration is pending.

## Observed local results

- Native Audio generated job `c56f0f41d938` using the imported Qwen 0.6B CustomVoice 8-bit model. The native Save panel exported its 7.6-second WAV.
- Native Transcription selected that WAV, used bundled MLX Whisper large-v3 turbo, displayed the expected sentence and exported SRT subtitles. [Exact result and WAV identity](redesign-gui.json).
- Audio draft, model and speaker choices, source recording and latest transcript survived restart. Transcription offers Text/SRT/VTT; word-level review is not implemented.
- Visual and accessibility checks covered Overview, Audio, Transcription, Models, Jobs, Settings and Connections. Audio, Transcription and Models were inspected at default and minimum window sizes. The installed-only model filter correctly showed the two imported pilot models. Explicit Quit stopped the owned app and worker.
- These generation/export actions used a disposable development harness sharing the immutable 0.2 runtime resources; final artifact checks are recorded separately below.

## Checks

- Optimized arm64 native build and native boundary/API fixture checks passed. Build SDK is macOS26.5 because the installed27SDK lacks the required SwiftUI State macro plugin; deployment target remains macOS14.
- Full adopted backend/frontend/standalone/packaging suite: **676 passed**, five upstream warnings,37.89seconds. Log `.build/redesign-regression-verified.log`.
- Separate packaging unittest discovery: **28 passed**,8.462seconds.
- Reconnect regression tests prevent stale generation publication, duplicate polling, uploads sent to a replacement worker and false token-save success. Private-log tests verify append,0600permissions and rejection of symlinks. Existing parent-loss, instance ownership, runtime isolation and backend model/deletion safeguards remain.
- No real token save/test, model removal/download, login registration, publication, deployment or fleet enrollment was performed. No Pinokio installation or finalized runtime was changed.

## Remaining work

Image, Chat, Music and Video engines and generation forms; remaining Audio model-specific controls; optional authenticated controller integration; clean physical Sonoma and external-SSD qualification; login/reboot and distribution signing. Only the exact Qwen/Whisper pilot models have end-to-end evidence here. Jobs retains backend Audio history and the latest transcript; a full transcription history is future work. This is a local preview, not a qualified fleet release.

## Final artifact

Final clean-build identity, launch and relocation checks will be appended after staging.
