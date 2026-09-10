# Standalone Studio app: design and feasibility plan

Date: 2026-09-10. Status: version 0.3.0 native redesign in progress. The local Voice proof exists; native Audio job `c56f0f41d938` completed and was saved, while Whisper transcription verification is underway. Clean-machine and physical Sonoma qualification remain pending. See `qualification/voice-runtime.md` for measured historical results and remaining acceptance checks.

## Decision

Develop one independent `Studio Hub.app` for new Apple Silicon nodes. Present Audio, Transcription, Image, Music, Video, and Chat in one native application window. Local creation is the primary product. Central app management coordinates models, jobs and settings; retain isolated engine processes and dependencies. Optional API exposure and Studio Hub/GenStudio control are secondary, with no remote controller required for local jobs. Leave all installed Pinokio nodes and their maintenance flow intact. Adopt the standalone app only after a real clean-machine pilot passes.

The owner confirmed six functional sections: Audio, Transcription, Image, Music, Video, Chat. Audio initially means speech generation/voice workflows; Transcription means speech-to-text and subtitles. Audio and Transcription have separate workspaces, selections and result state; they may share Voice runtime dependencies internally. Image, Music, Video generation, and Chat reuse their existing sibling engines where qualified. This supersedes the earlier three-component-only scope; it does not require six independent runtimes or concurrent model loading. The working folder and app name remain provisional.

The minimum compatibility target is **macOS 14 Sonoma**, with testing on newer supported macOS releases as well. MLX documents macOS 14+ Apple Silicon support, and MLX 0.31.2 has macOS 14 arm64 wheels. This does not certify every engine, native dependency, or model on Sonoma. The exact oldest supported release must be determined by the assembled app and physical-machine tests. Do not lower a binary's deployment metadata to disguise a newer OS requirement. [MLX installation](https://ml-explore.github.io/mlx/build/html/install.html), [MLX wheel metadata](https://pypi.org/pypi/mlx/0.31.2/json), [Metal wheel metadata](https://pypi.org/pypi/mlx-metal/0.31.2/json).

## Boundaries

| Area | Proposed first release |
|---|---|
| Users | People creating assets locally on new Macs; optional fleet use; no automatic migration of installed nodes |
| Hardware | Apple Silicon; each model still has its own memory requirements |
| Functions | Audio, Transcription, Image, Music, Video, Chat; one Hub and isolated workers as needed |
| Installation | Copy an app containing tested runtimes and native tools; no Pinokio, Homebrew, system Python, or developer toolchain required on the node |
| Models | Import or download complete, validated model packages separately |
| GenStudio | Secondary, optional authenticated control, qualified separately from local creation |
| Updates | App replacement for the pilot; no Git pull or package installation on nodes |
| Excluded | Intel, Windows/Linux, old-node migration, model conversion promises, public App Store submission |

An external SSD is useful for large model libraries and offline transfers. It does not solve incompatible native binaries or insufficient unified memory. Package size and RAM recommendations will come from measurements, not estimates presented as requirements.

## Architecture

```mermaid
flowchart LR
  U[Native creative app] --> L[Local model and job management]
  L --> I[Image engine]
  L --> A[Audio workspace / voice runtime]
  L --> T[Transcription workspace / STT runtime]
  L --> C[Chat engine]
  L --> M[Music engine]
  L --> V[Video engine]
  E[Optional Studio Hub / GenStudio] -. authenticated control .-> L
  L --> D[External settings, models and outputs]
```

The owner now requires a redesigned native SwiftUI/AppKit application instead of the legacy embedded workspaces. Reuse the tested Python APIs and workers behind new independent creation views and centralized management. Reuse Hub scheduling components when needed for local capacity coordination, without requiring a remote Hub or GenStudio connection. Separate environments prevent incompatible dependency sets from being forced into one Python environment. Reuse existing resource limits; separate processes do not create additional physical GPU capacity.

The owner currently has no paid Apple Developer Program membership. Continue local development and internal testing without making membership a build prerequisite. Xcode and local Mac development are available without paid enrollment; Developer ID and notarization require membership. A transferred, unnotarized internal build may need the normal per-app Open Anyway approval in macOS Privacy & Security, subject to machine policy. Verify this on a test Mac; do not disable Gatekeeper globally or claim frictionless fleet distribution. Paid membership and notarization remain a later distribution decision. [Apple membership comparison](https://developer.apple.com/support/compare-memberships/) · [Apple app-opening guidance](https://support.apple.com/en-ca/102445).

Proposed bundle layout:

```text
Studio Hub.app/Contents/
  MacOS/StudioHub
  Resources/
    hub/                 # adopted tracked source and built UI
    image/
    voice/               # Audio + Transcription
    chat/
    music/
    video/
    runtimes/            # isolated relocatable runtimes
    tools/               # required native executables, e.g. FFmpeg
    release-manifest.json
    licenses/
```

The exact runtime packaging method is a feasibility result. First test a relocatable Python environment prepared from locked dependencies, including wheels built on the build machine for source-only packages such as pyopenjtalk. Do not copy an active conda environment as though it were portable. If relocation requires modifying signed executable files at first launch, reject that method for the final app and evaluate a frozen worker executable. Record only the successful method as the production path. [Conda relocation constraints](https://conda.github.io/conda-pack/), [PyInstaller platform constraints](https://pyinstaller.org/en/stable/usage.html).

All executable code and libraries in the shipped bundle are immutable. Store settings, node identity, credentials, queues, outputs and logs in per-user application support locations. Let the owner choose the model library, including an external SSD. A missing volume must produce a clear unavailable state; never silently create a replacement cache or redownload the library elsewhere. Model import validates configuration, tokenizers, companion files, revisions and checksums as required by that model, not just one weights file.

Keep local engines on loopback and make local creation the primary path. Optional API, Studio Hub and GenStudio exposure may use the existing authenticated enrollment/network model, but it is not required for local jobs. Preserve authentication, idempotency, cancellation semantics, output verification, capacity accounting and owner-controlled model exposure. A packaged runtime must be freshly qualified; prior runtime/model audits are reference evidence, not automatic approval of a changed artifact.

Window close may leave the owned local worker running with a visible status/menu item. Explicit Quit must drain or clearly handle active jobs without silently replaying them. Login startup is optional and uses native macOS service management after the foreground workflow works; login/reboot behavior remains unqualified. The pilot requires a logged-in user; boot-before-login operation is separate scope. [Apple service management](https://developer.apple.com/documentation/servicemanagement/smappservice).

## Reuse and required adaptations

Source baselines are pinned in `research/source-checkouts.json`: Hub 2.21.3, Image 1.32.1, Voice 2.7.2. Adopt source into the new project's lifecycle at explicit commits. Record local changes so later upstream fixes can be selectively incorporated without linking production to the old working directories.

| Existing assumption | Standalone adaptation |
|---|---|
| `conda_env/bin/python`, Pinokio home and `pterm` process control | Bundle-relative interpreter and app-owned process lifecycle |
| Settings, voice files and generated outputs beside source | Explicit writable state and model paths outside the signed bundle |
| Sibling repository discovery | Explicit locations of bundled components; stable node identity outside bundle |
| Git-based updater and commit inspection | Truthful packaged release identity with component commits and runtime artifact hashes |
| Existing model qualification | Rerun relevant checks against the new runtime and record exact evidence |
| Engine dependency trees | Keep necessary isolation; deduplicate only after proving compatibility |

Graphify located Voice's updater/storage dependencies; current source confirmed the assumptions in `auto_update.py`, `settings.py`, `voices.py`, `generation.py`, `cache.py` and `main.py`. The graph covers Voice only, and some recorded locations are stale. A separate source-based Hub contract inventory accompanies this plan.

GenStudio generation endpoints may remain compatible while maintenance endpoints differ. Do not fake a `.git` checkout, report a packaged app as Git-managed, bypass exact-runtime checks, or route legacy nodes to an app updater. The first integration proof must establish whether the current controller accepts the packaged identity. If it needs a contract change outside this project, record the smallest additive change and its separate scope before implementation there. The current release validator fixes three upstream repository identities; a locally adapted standalone component cannot truthfully use its upstream commit as its complete runtime identity. Record the standalone implementation revision and artifact hash as well as upstream provenance. See the [source-linked Hub inventory](../research/hub-contract-inventory.md).

The application's standalone packaging is separate from Hub's internal `standalone` role. A site connected to GenStudio needs the appropriate controller setup; other nodes join it as agents. Preserve these existing roles and enrollment flows rather than making every new machine a controller. The pilot's node/site assignment is selected before enrollment.

## Milestones and acceptance evidence

### 1. Prove a relocatable Voice runtime

Deliver a disposable local bundle and `qualification/voice-runtime.md`, without changing any running node. Use Voice first because it includes the native dependency failure that motivated this project.

1. Inventory pinned Python and native requirements, dynamic libraries, external commands, model files, writable locations and redistribution licenses. Save `research/runtime-inventory.md` and `research/licenses.md`.
2. Adopt tracked Voice source from the recorded commit into `components/voice`; exclude credentials, environments, caches and outputs. Record the source identity and adoption delta.
3. Build an arm64 runtime from pinned inputs targeting Sonoma. Select macOS 14-compatible MLX/Metal wheels explicitly; audit every bundled Mach-O binary and its linked libraries. Produce `packaging/voice-runtime/` scripts and a dependency/hash manifest. Do not resolve packages opportunistically on a node.
4. Adapt only the paths and process launch assumptions needed to run this worker from the bundle. Add focused tests for external writable paths and bundle-relative executable resolution; retain existing generation checks.
5. Run the worker from two different paths, including one containing spaces, using a fresh account with no inherited development PATH, Pinokio, Homebrew or user Python packages. Verify the bundle remains unchanged after launch and a generated job.
6. Import a complete Qwen 0.6B model package from the existing catalog at an exact recorded revision. Generate and validate audio with network unavailable after import. Run transcription with the existing Whisper implementation and record its exact model and revision. These are pilot models, not authorization to expose them commercially.
7. Repeat on a clean physical Apple Silicon Sonoma machine without Xcode/Command Line Tools. Capture OS, hardware, memory, startup duration, job duration, peak memory, output checksums, imports and native dependency evidence. Repeat the same artifact on a newer OS.

Pass: successful audio and transcription, no node-side dependency installation/source builds, no development-path dependencies, no writes inside the bundle, no unexpected network dependency after complete model import, and evidence on Sonoma and a newer OS. Metal's normal runtime kernel compilation is not the prohibited dependency build.

Fail: identify the exact blocking package or behavior. Try one narrowly justified packaging correction. If Sonoma cannot support the selected engine stack without a major rewrite, bring back the measured choice: raise the OS floor or reduce the supported engine/model set. Do not build a universal compatibility framework.

### 2. Add local Image and shared app management

Adopt Image tracked source and needed local Hub scheduling components; build isolated runtimes and integrate the native Image workspace. Replace standalone-only discovery, lifecycle and data paths. Create a truthful release manifest and adapt runtime reporting. Preserve model exposure controls and physical capacity limits.

Use the existing Image candidate `AITRADER/FLUX2-klein-4B-mlx-4bit`, revision `7fd24828501390b67a92c8b66d2fc5a707d0ba1a`, for the initial 1K text-to-image check. Requalify it on the packaged runtime; the existing README's qualification is not proof for this app. Test larger workloads only after this baseline passes.

Pass evidence: authenticated health/catalog discovery; fresh test-node enrollment; exact runtime/model identities; one image, one speech and one transcription request through the GenStudio integration boundary; status and output retrieval; invalid authentication rejection; cancellation and duplicate-request handling; unavailable model/runtime behavior; appropriate shared capacity reporting. Run against a local test harness first. A live GenStudio/node pilot needs explicit enrollment/deployment authority and test credentials provided through an appropriate secure mechanism.

Exercise a mixed-fleet fixture containing a legacy Pinokio node and a packaged node. Generation routing may see both; maintenance targets only the correct installation type. If current GenStudio or Hub cannot represent this safely, report that blocker before changing another repository.

### 3. Redesign the native workspaces and central model setup (current)

Replace the legacy embedded UI with independent native Audio and Transcription workspaces. Build one app sidebar with Overview, the six function pages, central Models and Jobs, app Settings and optional Connections. Preserve per-workspace drafts/selections and completed outputs. Keep installation and global settings out of creation workspaces. Support model download, location selection and readable failure logs. Preserve existing task semantics. The current native proof records Audio job `c56f0f41d938` completed and saved; Whisper transcription verification is still underway. Test Quit, startup failure, occupied ports, duplicate launch, process crashes, disk full, missing SSD, interrupted model import and restart recovery. Never stop an unrelated Pinokio service to claim a port.

Pass: a fresh user can copy the app, open it, select/import models and complete a local job without a terminal. Moving/replacing the app retains node state and model location. A failed startup remains diagnosable.

### 4. Add Chat, Music and Video and extend packaged Hub routing

Use the source-linked inventory in `research/function-scope-inventory.md` to adopt tracked sources at exact revisions. Current Hub production routing tracks Image and Voice only; six visible functions are not proof of six working fleet operations. Extend the adopted Hub additively with each packaged engine, preserving registration, admission, shared memory limits, authentication, leases and release identity. Any required changes to a separate GenStudio repository need their own scoped integration work.

Implement text Chat first using its existing MLX path, then Music using its current Torch/MPS path, then a measured Video candidate. Prefer MLX where implemented and qualified; do not promise an MLX-only app or rewrite Music engines to enforce that preference. Video here means generation from Video Studio; separate Render Studio adoption is not implied. Models and runtime environments stay isolated as required, and are loaded only when needed.

For each function require a real local input/output job from the relocated bundle, complete imported model assets, cancellation, missing-model handling, memory release, safe shutdown and authenticated Hub contract tests. A navigation item alone is not an implemented function. Publish only measured supported model/hardware combinations. Video may require more memory or narrower output settings than Audio/Chat.

### 5. Qualify optional controller integration, distribution and a controlled pilot

Measure bundle size and hardware requirements. Build a signed, notarized distribution artifact when signing access and submission authority are available. Sign all executable components appropriately and test the downloaded/quarantined artifact on clean machines; do not require globally disabling Gatekeeper. [Apple direct distribution](https://developer.apple.com/documentation/xcode/distributing-your-app-for-beta-testing-and-releases).

Start with manual app replacement while idle; retain the prior app artifact and avoid irreversible state migrations so rollback remains testable. No custom automatic updater is needed for the first pilot. Do not advertise `managed_exact_commit` support before the full behavior is implemented and verified. If GenStudio's existing admission flow requires that capability, manual update support alone is insufficient for fleet adoption: complete the packaged release integration first or report the blocked pilot. Later fleet-managed updates must use the maintenance contract with an explicit packaged release implementation.

Pilot on new nodes only. Require completed generation/transcription/image tests, restart recovery, signed artifact launch, external model storage behavior, authenticated GenStudio operation, and a successful app replacement/rollback preserving identity and data. Observe a full agreed workload cycle before recommending adoption. No old-node conversion is part of acceptance.

## Owner input needed later

The owner approved proceeding with the native app and six functional sections. Continue local implementation with one app, Apple Silicon, Sonoma qualification target, separate model storage, menu-bar operation and optional login startup. Deliver native Audio and Transcription with central management first; Image, Chat, Music and Video remain pending engine integration and qualification. API exposure and authenticated external control follow as secondary capabilities. Hardware qualification and external enrollment remain separate acceptance conditions.

- **Before clean-machine qualification:** identify one available Sonoma Apple Silicon test Mac and a newer-OS test Mac; confirm their memory. Do not downgrade or erase a machine to manufacture a test environment without authorization.
- **Before real fleet integration:** choose a disposable new pilot node/site and authorize enrollment/testing. A mixed-fleet compatibility fixture is read-only/local preparation.
- **Before distribution:** select the final app name/bundle identifier and signing identity; authorize any notarization submission. No secrets belong in this plan.
- **Before adopting the app:** review the qualification evidence and decide whether the supported models/hardware meet the fleet's needs.

## Completion definition for this planning task

The separate folder, source evidence, architectural boundaries, staged implementation work and concrete pass/fail checks exist and agree with the owner's direction. The local Voice runtime and native shell are in progress; Audio job `c56f0f41d938` completed and was saved, while Whisper transcription verification remains underway. This does not establish support for every model, the remaining Image/Chat/Music/Video engines, physical Sonoma compatibility, signing, login-registration behavior or optional controller integration. The owner authorized milestone 1 implementation on 2026-09-10 and subsequently approved the native shell and six-function scope. Sonoma and external readiness claims remain gated by physical qualification and separately scoped integration evidence.
