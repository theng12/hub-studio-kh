# Studio Hub

A standalone creative Mac application that generates Audio, Transcription, Image, Music, Video and Chat assets locally. This is the primary product. Exposing APIs and accepting optional Studio Hub/GenStudio control are secondary capabilities. No controller connection, enrollment or fleet setup is required for local creation.

The owner wants six independent workspaces with their own inputs, model selections and results. Audio creates speech/voice assets; Transcription turns recordings into text and subtitles. They may share tested runtime dependencies internally. Shared Models, Jobs and Settings belong to the app, with optional external control under Connections. Overview emphasizes local jobs, engine state and model readiness.

The owner explicitly replaced the preview's legacy UI reuse direction with a redesign: one coherent native macOS navigation and centralized management. SwiftUI/AppKit own the interface and app lifecycle. Preserve local task semantics, safe deletion, cancellation, error recovery and model capability checks. Existing backend APIs and isolated workers can be reused without retaining their old interfaces.

Apple Silicon only; macOS14 is a qualification target. Bundle dependencies, store models and app data externally, support menu-bar operation and optional start at login. Existing Pinokio installations remain untouched. All six functions are the target; unimplemented engines must be described honestly. No paid Apple membership is available; local development continues, distribution qualification remains separate.
