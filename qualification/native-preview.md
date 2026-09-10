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
