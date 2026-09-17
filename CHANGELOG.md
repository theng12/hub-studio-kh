# Changelog

## 0.5.0 — 2026-09-17 · the app actually runs its own features

Owner-facing notes for this release are in `release-notes.md`, which is what the app's **What's
New** screen shows.

- Fix the peer-path modality. The app asked the Hub for `/studio/subtitle/...`; the Hub's fleet
  surface routes `/studio/<modality>/...` and knows only `image`, `voice` and `transcription`, so
  it answered `404 {"detail":"unknown studio"}`. Both the synchronous Transcribe route and the
  transcription availability read were affected; the second meant Whisper models never reached the
  Audio page, so no transcription model could be selected. The Swift fixture had been written
  against the app's own spelling, which is why every gate passed. It now refuses an unrouted studio
  exactly as the Hub does, and `tests/test_peer_path_contract.py` compares the app's paths with the
  Hub's routing table and with a running Hub.
- Fix the Hub job envelope encoding. `sendHubJSON` used a bare `JSONEncoder()` while `sendJSON`
  converted keys to snake_case, so `voiceReferenceAssetId` reached the Hub while it reads
  `voice_reference_asset_id`. The Hub ignores unknown keys, so the reference was discarded and
  every Qwen3-TTS Base clone failed with a message about a missing reference. `clientRequestId` was
  lost the same way, defeating the Hub's replay protection.
- Add the missing reference-audio contract for the 4-bit Qwen3-TTS Base tiers, derived from the
  audited 8-bit sibling of the same size and upstream model by
  `packaging/derive_qwen_base_audits.py`. A quantization changes weight precision, not the audio
  front end; the generated records carry a `derivation` block and claim no independent audit. On an
  8 GB node the 4-bit tier is the only Qwen Base tier that fits.
- Stop discarding a refused voice reference. The Hub now surfaces the worker's own reason, code
  included, instead of running a job with no reference attached.
- Make the model copy incremental and re-runnable: copy what is missing, replace what changed,
  leave identical models untouched, prune the superseded revision, and rewrite the app's receipt so
  an updated model reads as installed.
- Make both kit launchers location-independent; the copy in the kit root had resolved its helper
  one directory above the kit and refused to run.
- Return the engine's timed segments from the synchronous transcribe route instead of `null`, and
  keep them in the app so **Prepare Voice** can send them with the reference. A Qwen3-TTS Base
  reference longer than the model's 15-second maximum needs timestamped segments to select a
  bounded, speech-aligned window; without them the engine refused with
  `REFERENCE_TRANSCRIPT_ALIGNMENT_REQUIRED`. Timings are sent only while they still match the
  transcript, so a hand-edited transcript is never windowed against stale timings.
- Carry the owner's voices in the kit. `2 - Copy the models` now adds the voices the kit holds
  alongside the models, and never overwrites a voice already on a Mac, so a locally corrected
  transcript or replaced clip survives. Ten voices, all with transcripts, arrive ready to clone.
- Ship reference timings with the kit's long voice clips. Seven of the ten are 15.4–22.2 s, and a
  Qwen3-TTS Base reference over the model's 15-second maximum needs timestamped segments to be
  windowed; `qualification/time_kit_voices.py` computes them once at kit-build time with the app's
  own Whisper and the app reads them, so no Mac has to transcribe a voice before using it.
- Add **What's New** to the app, fed by the bundled `release-notes.md`, shown once after an update.

## 0.4.0 — 2026-09-15 · bundled Hub development proof

- Route the native workspace through the bundled authenticated Hub and its private Voice child, reusing the existing Hub scheduler and durable job identities.
- Add external model-package management, editable device output-policy defaults, packaged release identity and independent Subtitle registry support.
- Stop owned workers on app/Hub exit and surface private-worker failure through the Hub owner. Keep model files and device state outside the app.
- Preserve the historical 0.3.0 preview artifact. This development build is not a complete narrowed release: FLUX, Qwen Base controls, independent Subtitle process, cleanup enforcement and fleet integration remain later phases.
- Model-file verification and local process tests do not qualify model inference, original M1/Sonoma, login startup, signing or distribution.

### Included ST-01 foundation

- Pin ten scoped model packages and companion assets to immutable revisions and file hashes.
- Add verified HTTPS download, resumable staging, offline folder import and registration in place; preserve a working revision on failed replacement.
- Record separate Image/Voice runtime inputs, native binary floors and redistribution notices, with Image relocation and physical-device qualification still pending.
- Preserve GenStudio-to-location-controller-to-nodes routing and document the next Hub integration handoff.
- Keep the existing 0.3.0 preview artifact unchanged; the foundation checkpoint itself did not replace that app.

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
