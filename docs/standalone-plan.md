# Studio Hub Standalone — focused first-release design

Owner amendments, 2026-09-21: **GatewayKH**, not GenStudio, is now the external
client. The existing address/token → location-controller → joined-node boundary
remains the starting architecture; GatewayKH compatibility requires testing its
actual adapter contract. Older GenStudio references below describe the inherited
design/protocol, not a second current integration target. Music is the accepted
extension tracked in the implementation plan; Chat and Video remain deferred.
GatewayKH's current remote-location adapter uses the Fleet token for work/results
and an optional Hub/admin token for shared-voice administration. This supersedes
the older blanket external-client Hub-token guidance below, without changing
the existing credential scopes.

Updated: 2026-09-15. This is the canonical design. It supersedes the earlier six-function release plan.
Implementation progress: ST-01 model/runtime contracts and ST-02 local Hub have
current evidence; the 0.4.0 local proof bundles Hub and Voice. See the task board
for remaining engine, storage, native and fleet work. The 0.3.0 Voice preview is preserved.
Execution: [implementation plan](implementation-plan.md) · [task board](tasks/BOARD.md).
Evidence: [current model inventory](../research/rescope-model-inventory-2026-09-15.md), [Hub/storage inventory](../research/rescope-hub-storage-2026-09-15.md), [packaging inventory](../research/rescope-packaging-2026-09-15.md).

## 1. Decision and sufficiency

Build one independent native Apple Silicon app containing a Hub management layer,
Image Studio, Voice Studio and Subtitle Studio. Each device can create locally without
GenStudio. The same app can be configured as a location controller or joined worker.
GenStudio connectivity is a supported first-release option, not a requirement for local work.

This is sufficient for the requested production image, speech and subtitle workflows.
It deliberately removes Chat, Music and Video from the first-release build sequence.
Their existing source, artifacts and personal model stock are preserved; adding those
functions is a later product decision. No legacy Pinokio node is migrated by this project.

The main operational benefit is a tested runtime copied to every new Mac. Do not claim
that old packages are universally unavailable: the observed problem is uncontrolled
dependency resolution and inconsistent installed environments. Packaging addresses that
drift. It does not by itself repair missing weights, qualify a model on every RAM tier,
or guarantee compatibility with every future macOS release.

## 2. Owner-confirmed requirements

- Native standalone app; no Pinokio, Git, pip, uv, Homebrew, Xcode or system Python needed on a target device.
- Image: the fleet's FLUX.2 Klein 4B MLX 4-bit model.
- Voice: Qwen3 TTS 0.6B Base and 1.7B Base; VoxCPM2 4-bit, 8-bit and full; OmniVoice full.
- **Voice cloning is the primary voice-consistency mechanism** (owner clarification, 2026-09-15,
  added after planning). Text-to-speech should generate from a reference clip rather than rely on
  preset speakers, so one voice stays consistent across jobs. Both directions are required:
  - **Local clone** — the owner chooses a reference audio clip (and an optional reference
    transcript) inside the app and uses it for generation.
  - **GenStudio clone** — a reference pushed from GenStudio through the location controller
    (carrying its own source asset identity) is accepted and used the same way, without a local file.
  Both must converge on **one Hub reference-asset contract**, so a clone behaves identically
  whichever direction it arrived from, keeping the existing checksum, size, transcript, expiry and
  privacy rules, and keeping reference files outside generated-output cleanup.
- Subtitle Studio: its own workspace and backend/job lifecycle, with three Whisper options.
- Hub controls jobs, installed models, availability, memory, storage, devices and connections.
- Automatic startup, with editable startup settings.
- Editable per-device generated-output defaults: 50 GB total and 30 days after generation, whichever condition is reached first.
- Operating defaults are editable; settings must show the effective value and allow restoring the default.
- Each generator can gain supported models later without rebuilding its entire interface.
- Protected reveal/copy of the existing Hub token: GenStudio connects to each location controller; its nodes connect only to that controller.
- **Generation history (owner clarification, 2026-09-15, added after planning).** Every
  generation page — Image, Voice and Subtitle — must show its own easy-to-view history of that
  studio's generations, and there must also be **one unified history presented with tabs**, so
  the owner can switch between all work and per-studio work without leaving the page.
- Phased implementation with independently reviewable tasks and durable agent/session handoffs.

## 3. Initial planning baseline (0.3.0)

| Area | Evidence | Decision |
| --- | --- | --- |
| Native SwiftUI/AppKit shell, menu bar, owned-process shutdown, private logs | native/StudioHub.swift, native/ShellCore.swift | Reuse and adapt from one worker to Hub-owned workers |
| Packaged relocatable Python, media tools, hashes, immutable runtime | packaging/voice-runtime/, packaging/stage_voice.py | Keep; narrow runtime dependencies using requested-model evidence |
| Audio generation | 0.3.0 Qwen 0.6B CustomVoice plus saved WAV | Useful plumbing proof; NOT proof of the requested Base models |
| Subtitle output | Whisper Turbo transcription and SRT export | Reuse decoding and format behavior; extract the public Subtitle service |
| Image | Native placeholder; no bundled Image engine | Implement first complete new engine slice |
| Hub | Source inventory only; no Hub service inside the artifact | Implement early, not after all generator pages |
| Startup at login | SMAppService code exists | Verify and finish setup/reboot behavior |
| Shared output quota | Existing sibling/Hub mechanisms have different scopes | One local output index and policy, not three independent 50 GB quotas |
| GenStudio connection | Native placeholder/configuration work | Implement scoped token, packaged identity and fixture qualification |
| Current verification | 676 regression + 28 packaging tests; relocated Qwen/Whisper proof | Historical evidence only; new source/runtime/model combinations need new receipts |

The 0.3.0 artifact is approximately 1.7 GB allocated and packages Voice only.
The native client currently talks directly to that worker. NativeWorkspaceStore
defaults to CustomVoice and must be changed for the requested Base/reference workflow.
The exact source baseline for this planning pass is 29d0baa. Historical qualification
records remain intact; no source or app rebuild occurred to produce this plan.

## 4. Chosen structure

~~~mermaid
flowchart TD
    UI[One native app: Hub, Image, Voice, Subtitle] --> H[Bundled Hub: jobs, readiness, shared device admission]
    G[Optional GenStudio] -->|IP/address and Hub token: controller mode| H
    C[Optional location controller] -->|authenticated worker connection| H
    H --> I[Image worker]
    H --> V[Voice worker]
    H --> S[Subtitle worker]
    I --> O[Device-owned outputs and job evidence]
    V --> O
    S --> O
    H --> R[One output index and cleanup policy]
    R --> O
    I --> M[External validated model library]
    V --> M
    S --> M
~~~

One app does not mean one Python environment. Bundle only the isolated runtime sets
the selected engines actually need. Voice and Subtitle may share identical immutable
runtime files when compatibility is proven, while retaining independent processes,
queues, cancellation and result history. Image has its own qualified runtime if its
dependency constraints differ. Hub owns lifecycle; the native window does not launch
three unrelated unmanaged servers.

Reuse current tested workers and Hub scheduling code at recorded commits. Adopt source
deliberately, replace packaging/path assumptions, and test the resulting contracts.
Do not paste entire installed directories, virtual environments, credentials or live
job databases into the app. Do not redesign working model inference algorithms.

All native generation and external work must pass the same node-owned admission checks.
There must be no direct native-to-worker path that bypasses the Hub's model pause,
physical-memory slot or storage gate. Workers bind to private loopback endpoints.
Only the configured Hub interface is exposed for external control.

Subtitle Studio owns user transcription requests and independent subtitle history.
Voice may retain the existing internal Whisper quality check required by Qwen Base;
that check remains part of the parent voice job under its existing memory lease. It
does not become a separately queued public Subtitle job or wait for a second node slot.
Shared tokenizer/model assets are referenced once in the model library.

### Roles

**Owner-confirmed topology:** preserve the current arrangement. GenStudio connects
once to each location's controller using its address/IP and Hub token. Each controller
manages its joined nodes; nodes do not contact GenStudio directly. No extra location
gateway or new routing layer is required. Standalone local operation remains available.

| Mode | Default behavior | Editable connection setup |
| --- | --- | --- |
| Standalone (initial default) | Local Hub schedules local studios; external listener off | Can later become a controller or join one |
| Controller | Manages this device and explicitly enrolled workers | Site identity, worker enrollment, private listener and existing Hub token for GenStudio |
| Worker (existing Hub agent role) | Executes work accepted from its selected controller | Parent controller address and enrollment |

Preserve the current agent rule: it does not accept customer-style queue submissions.
In joined-worker mode, native creation submits through its parent controller. Already
accepted work survives brief disconnection; new submissions show the connection problem.
An owner may return an idle device to Standalone through the normal safe mode change.
Do not create a second global scheduler or billing/account system: GenStudio retains
global customer jobs, pricing, credits, assets and cross-location routing.

### Locations, nodes and operational data

A location groups explicitly joined node machines. Preserve stable location/site and
physical node identities independently of editable display names and IP addresses.
One node has one active location membership at a time; Standalone remains usable
without a remote location. The location grouping and Controller/Agent/Standalone
roles must survive app restart and be visible in native management and authenticated
API responses. Moving a node between locations uses the existing safe role/enrollment
flow and reconciles accepted jobs before membership changes take effect.

Preserve the current Hub's operational information, mapped to the packaged studios:

| View | Required information |
| --- | --- |
| Location | Member nodes, online/offline/stale status, roles, capabilities and aggregate work |
| Node | Stable identity, location, app/runtime versions, hardware, memory, storage policy/usage, model readiness and last report time |
| Generation | Studio, exact model/variant, location/node attribution, job/item/attempt IDs, current phase/progress when available, timings, result/delivery state and actionable failure reason |
| History/stats | Completed and failed requested items separately from execution attempts/retries, comparable duration/throughput and observation window |

Reuse existing Hub APIs and statistics definitions; do not create a second analytics
pipeline. Count a physical machine once even if it hosts multiple studios. Include
freshness and explicit unsupported/unknown values; unavailable metrics are not zero.
Lost reports must not erase history or turn a model-file failure into healthy readiness.
Keep generation detail behind the existing authorized access, with no secrets in
telemetry. Transport routing remains GenStudio to location controller to joined nodes.

## 5. Bounded catalog: ten initial model choices

The first seven are owner-selected models. The three Whisper choices are recommended
editable defaults drawn from the existing registry; they are not an additional engine family.

| Studio | Initial model IDs | Qualification rule |
| --- | --- | --- |
| Image | AITRADER/FLUX2-klein-4B-mlx-4bit | Start with the current 1K text-to-image contract; four steps and guidance 1.0 are editable within qualified ranges |
| Voice / Qwen | mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit; mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit | Reference-audio/transcript cloning; independent receipt for each model |
| Voice / VoxCPM2 | mlx-community/VoxCPM2-4bit; mlx-community/VoxCPM2-8bit; mlx-community/VoxCPM2-bf16 | Each precision variant gets its own memory, quality and runtime receipt |
| Voice / OmniVoice | mlx-community/OmniVoice-bfloat16 | Full-weight variant only; qualify every exposed generation mode |
| Subtitle / recommended | mlx-community/whisper-base-mlx; mlx-community/whisper-small-mlx; mlx-community/whisper-large-v3-turbo | Base=compact drafts, Small=balanced drafts, Turbo=default; models and companion tokenizers pinned |

Exact known revisions and evidence limits are in the model inventory. ST-01 captures
the remaining five revisions and processor companions from official metadata before
import. A moving main reference is never an installed-model identity.

Do not inherit the complete upstream catalog. Do not expose CustomVoice, VoiceDesign,
additional Omni/Vox precisions or image families merely because their code is present.
Do not delete existing personal model stock (including Khmer TTS) or its records.
No additional model downloads or download-status monitoring is authorized by this
planning task.

Historical RAM evidence is not a universal guarantee. Existing Qwen Base audits exclude
8 GB. Vox tiers need their own packaged measurements; full precision is not automatically
safe or practical on a 16 GB device. FLUX has fleet 8 GB operation evidence but its
referenced formal 1K audit was on 16 GB; repeat the intended 8 GB case on the packaged app.
Publish eligibility from exact model, runtime, workload and hardware receipts.
A smaller machine may install the app without being eligible for every catalog model.

## 6. App release and model package flow

~~~mermaid
flowchart LR
    A[Pinned source and dependency inputs] --> B[Build once on build Mac]
    B --> C[Immutable app with runtime hashes]
    M[Exact model revisions and all companions] --> P[Validated model packages]
    C --> K[Offline install kit: app plus selected packages]
    P --> K
    K --> N[New device imports, verifies and opens app]
    N --> Q[Only ready and eligible models can receive jobs]
~~~

The app contains executable code, runtime libraries and native tools. Model packages
contain complete weight shards, indexes, configurations, tokenizers and required
companions. Keep them separate from the app so replacing a release does not recopy or
delete a large model library. An SSD kit can carry both, giving a new node a complete
offline setup without Git or node-side dependency installation.

### Two equivalent model installation paths

1. **Download in the app:** select a supported model and its configured source. Support
   GitHub-hosted release assets/direct model files as well as the original model host
   (currently Hugging Face for this catalog). Download the complete pinned package,
   including companion assets, then verify it before exposing Ready. A source is
   editable; the downloaded bytes must still match the selected model manifest.
2. **Copy a folder:** copy/paste a complete model folder into the selected model library,
   or choose Import Folder for a folder on an SSD. Rescan on opening Models and through
   an explicit Refresh/Verify action. Recognize supported folder layouts, validate the
   package and register it without redownloading files already present. Register a
   complete folder already in the library in place; do not keep a second full copy.
   Importing from outside the selected library stages/copies into it. To use an SSD
   folder in place, select that SSD library root first; do not silently adopt an
   arbitrary source folder as a second managed library.

Both paths end at the same validation and readiness record. A copy still in progress
stays Incomplete; show exact missing/invalid files and an explicit repair action. Never
dispatch merely because a folder exists. After import, verify the final files again
and publish readiness atomically. A hand-copied folder without a manifest can be matched
to the app's supported catalog manifest; an unknown model requires explicit supported
model registration, not guessing an architecture or executing downloaded code.

A model folder contains all required weight shards, indexes, configuration, tokenizer,
processor, vocabulary and companion model data. Runtime dependencies such as Python,
MLX and media tools stay in the tested app runtime. The manifest identifies the required
engine/runtime; a mismatched app shows the required app release instead of installing
arbitrary dependencies on a node. Portable folder export materializes required cache
links so copying it to another Mac cannot leave references to the original Mac's cache.

GitHub support does not assert that every current upstream weight is hosted there.
Use actual complete published model assets or an owner-provided package source. Generic
source ZIPs may omit actual Git LFS weights; reject pointer-only/incomplete downloads.
GitHub currently limits each release asset to under 2 GiB, so a larger package needs
multiple assets or another configured host. No weights are uploaded or redistributed
by this planning task. Sources: [GitHub release documentation](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases),
[Hugging Face revision and local-folder downloads](https://huggingface.co/docs/huggingface_hub/guides/download).

- Preserve exact source commits, dependency hash locks, wheel/source archives and final runtime manifests on the build side.
- Retain a last-known-good app artifact and its matching model manifests. Do not depend on future availability of a package index to reinstall a node.
- Import into a staging location, validate complete files/checksums, then atomically publish the installed model record.
- Run a cheap required-file readability check before dispatch. A broken symlink, missing shard or absent processor means Not ready and no job submission.
- A failed download/import cannot replace a working model. Resume partial downloads through the existing supported importer; do not duplicate caches.
- Inference never resolves a new dependency, silently updates a model revision or downloads a missing companion.
- App replacement occurs while idle and preserves external settings, tokens, models, outputs and job evidence. Rollback uses a preserved app artifact; no custom automatic updater is needed for the first pilot.
- New-model support for a known engine is a validated catalog/model-package addition with qualification. A new architecture or dependency stack requires a new tested app release. No arbitrary code or pip install on nodes.

## 7. Editable defaults

| Setting | First-release default | Scope and behavior |
| --- | --- | --- |
| Generated-output budget | 50 GB = 50,000,000,000 bytes | Per device, across Image/Voice/Subtitle together |
| Generated-output age | 30 days from successful output finalization | Per device; size or age independently triggers cleanup |
| Automatic cleanup | On | Per device; editable enable/limits with usage preview |
| Start at login | On after first-run setup and normal macOS approval | Editable per device; show actual registered/approval state |
| Start local services | With app startup | Editable enabled studios; load models on demand |
| Model library | App's external Application Support library | User can select another folder or SSD; missing volume stays unavailable |
| Output location | App-managed external output directory | Editable; moving it must retain index and ownership evidence |
| Device mode | Standalone | Editable Standalone/Controller/Worker while safe |
| External Hub listener | Off in Standalone | Explicit private-interface configuration when connecting |
| Default models | FLUX for Image; Qwen0.6 Base if eligible; Turbo for Subtitle | Editable among installed, qualified and eligible choices; no silent fallback |
| Generation parameters | Model-provided defaults | Editable supported fields; unsupported parameters are not invented |
| Device/site labels, parent URL | Current local identity / no parent | Editable display labels; stable IDs are not silently rewritten |

All configurable fields live in the owning backend settings flow and are read by the
native UI, not duplicated as Swift-only constants. Validation explains unsupported
values and preserves the last valid setting. An authenticated owner controller may
edit one device's settings; persist and acknowledge on that device before reporting
success. Do not fan out a per-device change across the whole fleet without an explicit
selection. Restore Defaults resets the selected settings section, not data or credentials.

Editable defaults do not mean editable trust: file-integrity checks, authentication,
active-job protection, verified completion, immutable revisions and safe path boundaries
cannot be disabled by an ordinary preference.

## 8. Storage rules

Use one local Hub-owned output index and reclaimer. Reuse current worker deletion and
receipt mechanisms; do not independently allocate 50 GB to each worker.

1. Index finalized generated images/audio/text/subtitles and app-generated previews with
   job ID, device ID, byte count, immutable generated_at, terminal state, safe relative
   path, checksum and delivery/receipt state. Count physical output files once even when
   UI and Hub both reference them.
2. Delete eligible terminal outputs aged at least the configured days. If managed output
   usage still reaches the configured byte budget, delete the oldest eligible groups
   until usage is below the budget. A job's output group includes derivative files and
   index updates; deleting it leaves an honest expired/deleted history row.
3. Protect queued/running/cancelling/uncertain jobs, active readers, unfinished transfers
   and results awaiting required verified receipt. Generated data needed for a pending
   quality check is not terminal output. Reuse existing acknowledgement behavior.
4. Models, runtime files, user voice-reference libraries, user originals, credentials,
   job evidence and files exported to user-selected locations are not automatic-cleanup
   candidates. Choosing a different app-managed output root keeps generated files indexed,
   counted and reclaimable. Only an explicit exported copy outside the managed root
   becomes user-owned; selecting a folder must not bypass cleanup. Temporary files
   owned by an active job remain protected; abandoned
   terminal job files are reconciled and reclaimed safely.
5. Use generated_at, not filesystem mtime: copying a file or rebooting cannot reset its
   age. Missing SSDs or permission errors never trigger replacement directories.
6. Check space before admission; run cleanup at startup, after finalization/receipt or
   settings changes, and periodically while the app runs. Suggested maintenance cadence
   is five minutes, editable within validated bounds. This is a proposed default, not
   inherited source behavior; admission checks remain authoritative between sweeps.
7. The byte limit is a cleanup/admission threshold, not permission to erase protected
   data. Active/protected outputs can temporarily prevent compliance. Show used,
   eligible and protected bytes, pause new generation with OUTPUT_STORAGE_BLOCKED,
   and explain the needed action. Do not claim an absolute disk ceiling during an
   active unbounded job. Check real disk free space independently of this output quota.
8. Preview reclaimable files/count/bytes when the owner lowers limits. Apply through the
   existing authenticated settings action and display the result, not optimistic success.

This is generated-output housekeeping, not an archive service or a policy change to
GenStudio's final customer asset retention.

## 9. Startup, recovery and connections

On first setup: choose storage, choose mode, import models, set preferences and show
startup approval state. Subsequent login starts the native app and owned Hub quietly
in the menu bar, restoring settings. It must not require Terminal commands.
Closing the window leaves the owned service running; explicit Quit drains or handles
current work through the existing verified shutdown path. Repeated launch owns only
one service set for one node-data directory.

Automatic login startup is distinct from running before macOS login/FileVault unlock.
First release targets a logged-in user, as the existing plan did. Boot-before-login
service operation is separate scope and is not silently promised.

Reuse current SMAppService registration. Physical login, logout, reboot, missing-model
SSD, crashed child, occupied port and upgrade/rollback tests are release checkpoints.
Start-up recovery must reconcile the original job; never duplicate generation because
a status response was lost. A model failure pauses that model and shows Repair/Recheck.
Process health does not clear a model-file failure.

Connections provides:

- Credential-free private controller URL and stable site/node identity; GenStudio uses each location controller endpoint.
- Masked token status with explicit owner-only Reveal and Copy actions.
- Scoped token rotation and clear instructions for reconnecting GenStudio.
- Existing protected Hub-token storage (owner-only files or already-adopted secure
  store); no secret in the app bundle, model manifest, URL, log or ordinary status.
- Preserve existing separate Hub and fleet tokens. GenStudio uses the controller's
  Hub service token; joined-node enrollment and communication retain the existing
  fleet credentials. No new third token type. Rotation must not revoke unrelated
  scope or discard accepted work.
- Authenticated connection test reporting identity, capabilities and release type.
- Truthful packaged app identity: standalone implementation revision, component
  provenance and runtime artifact hashes. Never fake a Git checkout or managed Git
  update support to pass a legacy contract.

The deployment-independent API and existing controller endpoint integration are
required for this release. Preserve current routing and credentials. Any live
enrollment, deployment, secret rotation or changes to a separate production repository
require their established authorization. Planning never changes live credentials.

### Deployment-independent connection contract

The connection setup consists of a reachable address/IP, port and token. Preserve
authenticated HTTP API behavior and the configured secure/private transport; a simple
setup must not require copying databases, shared folders, a GenStudio browser session,
or a hard-coded cloud domain. Tokens travel in the existing authentication header,
never in the address. Do not require an additional account-linking service to connect.

Treat GenStudio as an API client regardless of whether its future deployment is a
hosted app or a standalone app. Preserve current job IDs, idempotent submission/status,
cancellation, result download/verified acknowledgement, capability and status contracts.
Report protocol capabilities and unsupported operations explicitly, reusing the current
contract versioning instead of inventing a generic compatibility framework. A changed
IP updates the connection address without silently changing the location/node identity.

The future standalone GenStudio application itself is deferred. This release provides
the documented API boundary and fixture tests that allow that later client to connect.

## 10. Build order and completion

Build foundation first (ST-01 runtime/model contract; ST-02 Hub). Prove packaged
FLUX end-to-end (ST-03). Then progress Qwen Base, the remaining bounded Voice models
and the independent Subtitle engine against the same interfaces (ST-04/05/06).
Finish shared storage, native controls and automatic startup (ST-07/08/09), then
controller/GenStudio integration and whole-app qualification (ST-10/11). Native pilot
work can start after FLUX/storage; token/connection fixtures can start after Hub and
software lifecycle. Physical login/reboot proof belongs to ST-11 and does not block
independent connection implementation.

Do not wait until the end to test packaging: every engine task must run from a relocated
app on a clean state directory and produce real verified output. The first integrated
pilot is Hub + FLUX + Qwen0.6 Base + Whisper Turbo, with protected storage and startup.
That checkpoint is useful but is NOT completion of the full requested ten-model scope.

Completion requires all selected variants to have explicit support or measured exclusion
per hardware tier, successful clean-machine install with no dependency downloads, real
output/cancellation/recovery checks, 50 GB/30-day policy tests at reduced fixture limits,
startup/rollback proof and authenticated connection fixtures. A physical hardware or
distribution blocker is recorded plainly; no task may mark unrun checks as passed.

## 11. References and current limits

### Old M1 devices and the oldest practical macOS

Owner direction: support the original M1 family with Apple's MLX and keep useful
generation working on the oldest feasible OS, without requiring the newest macOS.

The current engineering baseline is **Apple Silicon M1 or newer, macOS 14 Sonoma**.
Apple's [MLX installation requirements](https://ml-explore.github.io/mlx/build/html/install.html)
currently specify macOS 14.0 or later. The existing app targets arm64/macOS 14.0,
and its runtime pins the macOS-14 variants of MLX and mlx-metal. This supports 14.0
as the first qualification candidate; it does not prove every selected model runs on
14.0. Record the exact minimum working point release and any model-specific exception
from physical tests. M1 chip support, OS support and sufficient RAM are separate checks.

| Device condition | Planned behavior |
| --- | --- |
| M1 on a qualified Sonoma 14.x version | Run supported, RAM-eligible models without requesting the latest macOS |
| M1 on a newer qualified macOS | Use the same compatible app/runtime where verified; newer OS alone does not select a different model |
| M1 on macOS 11/12/13 | Below the documented MLX floor and current app target; do not promise this app or MLX generation before an OS upgrade |
| A particular model needs a newer OS/Metal feature or more RAM | Explain the exact requirement and leave other qualified models available on the current supported OS |

Implementation rules:

- Preserve the macOS 14 baseline across the shell, Hub, workers, libraries and Metal
  shader paths. Audit actual binaries and execute inference on physical M1 hardware;
  a deployment flag, wheel filename or successful launch on the build Mac is insufficient.
- Reuse locked compatible dependencies. Do not raise the minimum because the build Mac
  uses a newer SDK or because a newer package defaults to a newer-OS artifact. Gate
  optional newer UI/engine features appropriately instead of breaking existing workflows.
- Model readiness records the exact app/runtime/model, OS version, chip and memory
  eligibility. An unsupported combination stays unavailable before job submission.
  Do not silently substitute precision, model or a CPU backend to claim compatibility.
- Keep the last qualified app and complete model packages available for older supported
  nodes. Check OS requirements before offering/installing an app replacement; leave a
  working release installed if its replacement is incompatible. Do not add a custom
  updater or a fleet-wide forced upgrade. Previously qualified workflows remain usable
  while a device's OS upgrade is deferred.
- Bundled runtime libraries cannot supply missing operating-system Metal/driver features.
  Supporting pre-Sonoma through a separate legacy engine would need new evidence and
  scope; it is not promised by this plan or by the word modular.

### Working around an OS update

Generation may continue while an OS update is merely downloading and the existing OS
remains operational, subject to normal resource/space checks. It cannot continue through
the installation restart. The app does not install, schedule or suppress macOS updates.
Before an owner-controlled restart, reuse the existing pause/drain behavior: stop new
admission on that node and let active work finish when possible. Keep other eligible
location nodes available; do not claim a mid-generation transfer or checkpoint resume
unless that engine already implements it.

After login, use the existing startup/recovery flow, preserve IDs/settings/models and
reconcile interrupted attempts before accepting new work. Recheck OS/runtime/model
compatibility and readiness without redownloading intact model packages. Unexpected
restart must not invent successful results or duplicate jobs after lost responses.
Physical OS-upgrade/reboot and last-compatible-app tests belong to ST-11.

- [0.3.0 measured proof](../qualification/native-redesign.md).
- [Model and capability evidence](../research/rescope-model-inventory-2026-09-15.md).
- [Current Hub and storage contracts](../research/rescope-hub-storage-2026-09-15.md).
- [Runtime inventory](../research/rescope-packaging-2026-09-15.md).
- [M1/macOS evidence and remaining physical checks](../research/rescope-platform-floor-2026-09-15.md).
- [Apple login service API](https://developer.apple.com/documentation/servicemanagement/smappservice).
- [Apple Developer ID distribution](https://developer.apple.com/developer-id/).
- [MLX installation/platform documentation](https://ml-explore.github.io/mlx/build/html/install.html).

macOS 14 remains a qualification target, not a proven fleet floor. The existing native
build uses the available macOS 26.5 SDK; every bundled native binary must meet the chosen
deployment target. Developer ID/notarization and physical test Macs are later concrete
distribution/qualification conditions. Existing paid-membership availability is not a
reason to stop local work. No hosted GitHub compute is part of this workflow.
