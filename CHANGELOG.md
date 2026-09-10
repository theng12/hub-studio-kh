# Changelog

## 0.3.0 — 2026-09-11

- Redesigned the application as a native SwiftUI/AppKit shell with six visible functions: Audio, Transcription, Image, Music, Video and Chat. The embedded browser workspace is removed.
- Separated Audio and Transcription state and added shared Overview, Models, Jobs and Settings pages. Connections to an API, Studio Hub or GenStudio are optional; local asset creation is primary.
- Recorded native Audio job `c56f0f41d938` as completed and saved. Whisper transcription verification is underway. Image, Chat, Music, Video and optional controller integration remain pending.
- Removed the word-timestamp control because the word-level result viewer is not implemented. Native Transcription supports Text, SRT and VTT results.
- The build selects the macOS 26.5 SDK because installed SDK 27 is missing the SwiftUI macros plugin; the deployment target remains macOS 14. Sonoma compatibility has only been tested on macOS 26, so physical Sonoma compatibility remains unqualified.
- No signing, notarization or login-registration tests are claimed. The planned local artifact is `dist/Studio Hub.app`; no remote artifact is planned.

## 0.2.0 — native Audio and Transcription preview

- Added a SwiftUI/AppKit window with Audio, Transcription, Image, Music, Video and Chat navigation; Audio and Transcription connect to the bundled Voice worker.
- Added model-folder setup, preserved editor state, native upload/save dialogs, loopback-only embedded navigation and menu-bar reopening.
- Added optional login registration, explicit quit/stop handling, scoped worker ownership and startup readiness.
- Image, Music, Video, Chat and bundled Hub/fleet integration remain implementation work. Sonoma, login startup, clean-machine distribution and other models remain unqualified.

## 0.1.0 — local Voice packaging proof

- Added an isolated, pinned Voice source adoption and a bundled Python runtime build.
- Added external node state and model storage, bundle-relative startup, and immutable release metadata.
- Disabled inherited Pinokio maintenance and withheld upstream model qualification for the new runtime.
- Added local relocation and API smoke checks. Sonoma, full fleet integration, signing, and the native app shell remain qualification/implementation work.
