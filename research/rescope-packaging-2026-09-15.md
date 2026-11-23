# Standalone packaging rescope inventory

Read-only inspection of standalone baseline `29d0baa7e06117451c31a71860a08680cc08d5a1` on 2026-09-15. This is evidence for rescoping work; it is not a qualification run and it does not replace the model inventory in [rescope-model-inventory-2026-09-15.md](rescope-model-inventory-2026-09-15.md).

## Baseline and preservation

- Repository: `~/Projects/studiohub-standalone`, branch `implementation/voice-runtime`, HEAD `29d0baa`.
- Pre-existing worktree changes were preserved: planning `findings.md`/`progress.md`, the Graphify query stamp, and untracked SSD/Yume research plus the existing model inventory.
- The standalone AGENTS file requires external mutable state and model storage, immutable bundled code, local verification only, no hosted compute/deployment, and honest separation of compatibility targets from qualification. The accepted plan is `docs/standalone-plan.md`; no parallel plan is created.
- Existing Graphify data is under `research/voice-source-graph/graphify-out` and covers Voice only. Its query confirms the relevant storage, model loading, FFmpeg, memory and worker lifecycle symbols; absence was checked against current source rather than inferred from the graph.

## What the current artifact actually contains

`dist/Studio Hub.app` is about 1.7 GB, almost all of it the Voice runtime. Its `Contents` contains the native `StudioHub` shell, `StudioHubVoice`, `Info.plist`, `Resources/voice`, `Resources/voice_worker.py`, `Resources/runtimes/voice`, and one `release-manifest.json`. The staging implementation is explicitly voice-only: [packaging/stage_voice.py](../packaging/stage_voice.py) copies tracked Voice backend/frontend/audits and a single Voice runtime, then optionally adds the native shell.

The embedded 0.3.0 manifest ([release manifest](../dist/Studio%20Hub.app/Contents/Resources/release-manifest.json)) records only a Voice component, Voice upstream commit `154e7390fcee942ca6bbbcd326fcf9027b37404d`, `qualification: unqualified-local-proof`, and no Image or Hub component identity. The app is therefore a native shell around one Voice worker, not yet a one-app package for the requested Image + Voice + Subtitle + Hub set.

`native/StudioHub.swift` starts only `Contents/MacOS/StudioHubVoice`, reads one readiness file, and owns one worker process. It does start automatically after a saved model library is present when the app is launched, but the recorded launch evidence has `start_at_login_enabled: false`; login/reboot behavior remains unqualified. `SMAppService` registration is exposed in Settings, yet there is no recorded login-item qualification.

The six destinations are navigation only. `native/StudioViews.swift` returns `Engine not installed` for Image, Music, Video and Chat. `NativeWorkspaceStore.combineModels` in [native/StudioAPI.swift](../native/StudioAPI.swift) marks only Qwen CustomVoice as supported Audio and marks Whisper engine rows as supported Transcription. The current native fixtures assert exactly that CustomVoice + Whisper Turbo boundary ([tests/native/api_main.swift](../tests/native/api_main.swift)); they do not cover Qwen Base, VoxCPM2, OmniVoice, FLUX.2 Klein, Hub, or controller startup.

## Qualification boundary

The final native redesign receipt ([qualification/native-redesign.md](../qualification/native-redesign.md)) records 676 regression passes, native UI checks, a real Qwen CustomVoice generation, and Whisper Turbo transcription with SRT export. The durable smoke record ([qualification/redesign-smoke.json](../qualification/redesign-smoke.json)) records two relocations, offline Hugging Face mode, immutable bundle bytes, and `generation_tested: true`, but also `network_physically_disabled: false`, `sonoma_qualified: false`, `fresh_os_install: false`, and `managed_exact_commit: false`. This evidence supports the packaging mechanism and lifecycle safeguards on macOS 26 only; it does not qualify the requested models or controller.

`qualification/model-inputs.json` contains only complete imported snapshots for Qwen 0.6B **CustomVoice**, Whisper large-v3 Turbo, and Turbo's OpenAI tokenizer companion. It contains no Qwen Base 0.6B/1.7B, VoxCPM2, OmniVoice, FLUX, or additional Subtitle model package. The exact distinction matters: Base cloning requires reference audio and an exact transcript, while the standalone proof used preset CustomVoice speakers. See the source-backed model details in [rescope-model-inventory-2026-09-15.md](rescope-model-inventory-2026-09-15.md).

## Scope overshoot in the current runtime

`packaging/voice-runtime/requirements-pilot.txt` includes `components/voice/app/requirements.txt` plus Torch, TorchAudio, Diffusers, Accelerate, SentencePiece, Misaki/Fugashi/Jaconv/Mojimoji/pyopenjtalk, MLX, MLX-LM, mlx-audio, Mistral Common, and related packages. The resolved lock is approximately 137 installed distributions, 136 resolved, with 32,784 manifest records according to the existing planning progress record. This is a useful immutable proof runtime, but it is broad for the requested first release:

- Torch/TorchAudio/Diffusers/Accelerate are not needed by the requested MLX FLUX.2 Klein + Qwen Base + VoxCPM2 + OmniVoice + Whisper path; they were retained for non-F5 or legacy catalog coverage.
- Misaki/Fugashi/Jaconv/Mojimoji/pyopenjtalk support Kokoro/Japanese G2P paths outside the requested model set.
- Mistral Common is for Voxtral, which is outside the requested set.
- The upstream generation input still documents Bark, F5-TTS, Chatterbox, Kokoro, Spark and many other families; shipping that dependency surface invites unsupported catalog expansion.

These are removal candidates, not deletions to make blindly. Build a target-only lock from actual imports and selected workers, then prove import, generation, transcription and model-switch behavior before pruning. Keep FFmpeg/FFprobe because the current Voice worker calls them for media handling; the runtime inventory records both as bundled native tools.

The current bundle is correctly model-external: models live under the selected external Hugging Face library and the artifact rejects selected paths inside the app bundle. The next package must preserve that property while adding an explicit model package manifest (repo, revision, complete file list, checksums, license evidence and companion tokenizer files). “Offline” in the existing smoke means already-imported model data plus `HF_HUB_OFFLINE`; it does not demonstrate a fresh machine can install dependencies or import missing model packages without network. Nodes must receive the prebuilt app and separately transferred model packages, with no Node/Python/pip/Conda/Git/dependency resolving on the node.

## Hub/controller implications

There is no Hub source or Hub runtime in the current standalone bundle. The existing [Hub contract inventory](hub-contract-inventory.md) records that sibling Hub currently assumes Pinokio API-tree discovery, `pterm` process control, Git-derived runtime identity, fixed sibling services, and production `TRACKED_MODALITIES = ("image", "voice")`. Its controller/agent enrollment, header-only credentials, capability schema, idempotent jobs and release reconciliation are protective behavior to adapt deliberately. They cannot be represented by adding a Hub commit string to the current Voice manifest.

For the requested one-app shape, Hub should be an app-owned controller/coordination component with explicit bundled component identities and loopback workers. Preserve authentication, stable node/controller identity, capability freshness, model readiness, capacity/lease accounting, cancellation, idempotency, fencing and uncertain-job recovery. Keep connection credentials in external app state; a protected reveal/copy token flow is an owner direction for 2026-09-15. The standalone local path must work without enrollment or a remote controller. The Hub controller path needs its own contract fixtures before any live enrollment.

## Concrete implementation order and ownership boundaries

1. **Lock the exact first-release allowlist.** Root owns the selected FLUX.2 Klein variant, Qwen Base 0.6B/1.7B, VoxCPM2 4/8/bf16, OmniVoice bf16 and roughly three Whisper subtitle choices, each with a pinned model revision, complete package inventory, license record and memory tier. Record editable per-device defaults, including the owner direction of 50 GB maximum completed-output storage and 30-day retention; do not silently change defaults when the catalog grows.
2. **Build the target-only MLX runtime.** A packaging agent owns `packaging/voice-runtime/requirements-pilot.txt`, lock generation, `smoke_imports.py`, runtime audit inputs and focused dependency allowlist tests. Keep isolated runtime directories if the Image and Voice worker contracts or locks do not prove safe sharing. A release must fail if a node needs pip/uv/Conda/Git/Node/Pinokio or writes into the app bundle.
3. **Adopt and narrow Image.** An Image agent owns tracked source adoption under `components/image`, FLUX catalog filtering and a relocatable worker/runtime under `Resources`. The existing Image source has FLUX.2 Klein 4B MLX 4-bit/8-bit and 9B 4-bit rows, but the recorded 4-bit audit is only 1K text-to-image; keep edit/img2img/2K disabled until separately qualified. Add source/model/runtime identity to the release manifest.
4. **Extend Voice and Subtitle contracts.** A Voice agent owns explicit catalog allowlisting and native request controls for Qwen Base, VoxCPM2 and OmniVoice, plus the selected Whisper subtitle models. Preserve the single MLX admission/eviction policy. Add per-model receipts; do not treat the existing CustomVoice receipt as Base evidence.
5. **Replace the single-worker shell coordinator.** A native agent owns `native/StudioHub.swift`, `ShellCore.swift`, `StudioAPI.swift`, `StudioViews.swift` and `native/build.sh`. The coordinator should start the owned Hub and selected workers on app open/login, expose readiness and failures per component, keep menu-bar operation, and stop only its verified process groups. Defaults and connections remain editable in external state, with protected token reveal/copy. Add launch/reboot fixtures only after the foreground flow works.
6. **Adapt Hub/controller in isolation.** A Hub agent owns adopted Hub source and app-owned process/config paths, capability schema mapping, controller/agent role handling and token-safe API transport. No live enrollment or remote fleet mutation belongs in this local package checkpoint.
7. **Stage and qualify one complete app.** Root owns a replacement for `stage_voice.py` (or a clearly generalized stage script), a manifest that names all bundled components/runtimes, external model package references, immutable tree hashes and licenses. Integration checkpoint: clean staged artifact, no developer tools on PATH, offline model import, target smoke, component restart/stop, unchanged bundle digest, and truthful unavailable states for anything outside the allowlist.

## Verification files and commands to preserve/extend

- Existing focused checks: `tests/test_runtime_packaging.py`, `tests/test_voice_bundle.py`, `tests/test_voice_standalone.py`, `tests/test_voice_smoke.py`, `native/build.sh`, and `tests/native/{main.swift,api_main.swift}`. Their current receipts cover the Voice-only boundary and lifecycle; extend them rather than treating the existing fixtures as target coverage.
- Add static tests for target allowlist/catalog-to-worker dispatch, package completeness and checksums, release manifest component identity, runtime dependency allowlist, immutable bundle after startup/model use, external writable state, no inherited credentials, loopback-only binding, process ownership, and editable storage/default/token settings.
- Add live acceptance receipts separately for each model family: exact revision import, model-specific request modes, output validity, subtitle Text/SRT/VTT, memory/swap telemetry, model switching and offline behavior. Do not overwrite the current receipts.
- Add controller contract fixtures for authenticated capability exchange, role/site identity, stale/duplicate/fenced jobs, uncertain response recovery, model readiness/capacity and protected token handling. Keep deployment, signing, physical Sonoma, fleet enrollment and hosted CI outside this checkpoint.

No inference, dependency installation, service startup, or source checkout mutation was performed for this inventory.
