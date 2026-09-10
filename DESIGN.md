# Native shell design

Version 0.3.0 is a native SwiftUI/AppKit application. The redesign replaces the embedded browser workspace entirely. AppKit owns the window, menu bar and process lifecycle; SwiftUI owns navigation, creation views, management views and first-run/recovery state. There is no WKWebView dependency in the native shell.

The sidebar is ordered as Overview, Audio, Transcription, Image, Music, Video, Chat, Models, Jobs, Connections and Settings. The six function pages are creation workspaces. Overview reports local job, engine and model readiness; Models, Jobs and Settings are shared management pages; Connections contains optional API, Studio Hub and GenStudio configuration. Local creation remains usable without a remote connection.

Audio and Transcription have independent selections, drafts, uploads and result state. Audio exposes the installed and runtime-ready Qwen voice model, speaker selection and generation. Transcription accepts an audio or video file and exports Text, SRT or VTT. Word timestamps are not exposed because the native result viewer does not implement word-level output. Image, Music, Video and Chat remain honest unavailable states until their engines are integrated and qualified.

First launch resolves the model library and reports missing-volume or startup failures directly in the app. Model download, removal and progress live in Models; completed local work lives in Jobs. The app keeps writable state and models outside the bundle. Closing the window keeps the owned worker available from the menu bar. Explicit Quit asks how to handle active work, then stops only the verified owned process group. Login-item status is read from macOS service management; login/reboot behavior is unqualified.

The minimum native window is 1040×680, with scrollable creation and management content. Verify keyboard focus and overflow at the default and minimum sizes. The native build targets macOS 14 but currently selects the macOS 26.5 SDK because installed SDK 27 lacks the SwiftUI macros plugin required by the source. Sonoma compatibility has only been tested on macOS 26; physical Sonoma compatibility remains unqualified.

The current implementation is in [native/StudioViews.swift](native/StudioViews.swift), [native/StudioHub.swift](native/StudioHub.swift) and [native/StudioAPI.swift](native/StudioAPI.swift). The SDK selection and native compilation steps are in [native/build.sh](native/build.sh).
