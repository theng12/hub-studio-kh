# Native shell design

## Target after the 2026-09-15 rescope

One SwiftUI/AppKit window, with:

- Hub Overview
- Image Studio
- Voice Studio
- Subtitle Studio
- Models
- Jobs
- Devices (controller mode)
- Connections
- Settings

Each creation workspace owns its inputs, selected model and result state. Shared
Models, Jobs, storage settings and device controls are not duplicated inside every
workspace. Chat, Music and Video leave the first-release navigation; their existing
work is not deleted as part of this planning pass.

Voice Studio must provide the reference-library and transcript workflow needed by
Qwen Base, plus only the controls supported by each selected VoxCPM2/OmniVoice model.
The current preset-speaker CustomVoice form is not the requested Base workflow.
Subtitle Studio owns distinct uploads, jobs, cancellation, text/SRT/VTT output and
history. Image Studio starts with the fleet's qualified FLUX text-to-image contract.

The native client submits through the bundled Hub, using the same admission and
model-readiness rules as externally submitted work. Hub reports effective settings,
eligible models, job state and protected output usage. The UI never treats an online
process or downloaded badge as proof that a model can run.

Settings expose editable defaults and Restore Defaults per section. Storage shows
device-wide usage, configured GB/days, eligible/protected bytes and cleanup preview.
Connections shows a private endpoint and masked token with explicit owner-scoped
Reveal/Copy, plus connection tests. Tokens are never normal diagnostic text.

Models provides Download, Import Folder and Refresh/Verify. Show copied packages as
incomplete until all required weights/companions validate; keep source and library path
editable. Connections uses address/IP, port and token independently of whether its
client is hosted or standalone. No terminal or Git workflow appears in model setup.

## Retained implementation and acceptance

Version 0.3.0 already uses AppKit lifecycle and SwiftUI pages; there is no WKWebView.
Reuse native/StudioHub.swift, native/ShellCore.swift, native/StudioAPI.swift and
native/StudioViews.swift, splitting focused studio views/stores only when needed.

Closing the window retains the app-owned Hub in the menu bar. Explicit Quit safely
handles current jobs before stopping owned children. Login startup defaults on after
setup; the UI reports actual SMAppService registration and approval state and allows
turning it off. One data directory owns one app/service instance.

The existing minimum window is 1040×680. Verify keyboard focus, Voice reference
controls, model/error text, token visibility, scrolling and primary actions at minimum
and default sizes. New engine presence is proven by real output, not navigation.

Current built preview and historical limits: [0.3.0 proof](qualification/native-redesign.md).
Current requirements and architecture: [canonical design](docs/standalone-plan.md).
Execution/ownership: [task board](docs/tasks/BOARD.md).
