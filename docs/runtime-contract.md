# Runtime, model and local Hub contract

Implemented boundaries: ST-01 model package validation/import/download and pinned runtime inputs;
ST-02 packaged Hub ownership and common admission. Engine qualification and complete native
workflows remain their named later tasks. This document extends the existing Hub contracts rather than defining new job APIs.

## Per-Mac function priority

`GET /api/hub/work-policy` returns `machine_enabled`, the registered local `studios`
(`id`, `title`, canonical `modality`, `enabled`), `priority_functions` (`id`, `title`),
and `priority_order`. The Hub supplies the rankable function IDs; clients do not
maintain a second vocabulary. Subtitle uses `transcription`. Unknown registered
studios retain their enable checkbox even when they are not rankable.

`POST /api/hub/work-policy` accepts exactly `{"priority_order": ["image", "voice", "chat"]}`.
IDs must be unique canonical functions registered on that Mac. An empty array means
**No preference**. Listed functions rank first in order; all unlisted functions share
equal priority below them. Disabled functions retain saved positions, but do not
participate in dispatch. The current job finishes; the highest-ranked eligible
queued function runs next, oldest first within a rank. Other enabled work runs when
higher-ranked eligible work is absent. Continuous higher-ranked demand may delay
lower-ranked work. Existing model readiness, memory, render precedence and pause
rules still apply. A priority save changes neither enablement nor any pause.

Legacy `chat_priority: true` reads as `["chat"]`; false or missing reads as `[]`.
Migration leaves other functions tied. Native Settings offers **No preference** and
**Custom order** with keyboard-accessible move buttons. Choosing Custom from an
empty order deliberately seeds the displayed rankable functions; reading a partial
order preserves its ties. An explicit reorder ranks the enabled functions while
retaining disabled saved entries. The native client uses the save response as
authoritative and rejects stale polls. An older Hub missing the new fields shows
its saved legacy choice with a restart/update requirement instead of accepting
unsupported edits.

Resources and fleet rows publish `priority_order`. The authenticated controller
`GET/POST /api/hub/fleet/nodes/{id}/work-policy` relay targets the chosen Mac,
including the controller itself. Gateway's per-Mac editor uses this same policy;
other Macs keep their own enablement and priorities.

## Files and callers

- [model_packages.py](../components/model_packages.py): standard-library runtime helper.
- [Model catalog](../model-manifests/): 38 exact packages; no moving model revisions.
- [Runtime input records](../research/st01-runtime-contracts.json): engine families,
  lock hashes, source provenance and limits of the import/audit evidence.
- [Model package tests](../tests/test_model_packages.py): filesystem, download and receipt behavior.

ST-02 stages the helper and catalog alongside Hub and supplies the owner-authenticated
model-management API using the existing asynchronous download/run lifecycle. The ST-01 helper does not itself launch services or change existing model caches.

## Model manifest v1

Each package contains:

| Field | Meaning |
| --- | --- |
| schema_version | 1; reject unknown versions |
| package_id | Stable local catalog key, independent of its download host |
| model_id / revision | Publisher model ID and full pinned Git revision |
| engine | The engines the catalog ships:  image-mflux, image-mlx-serve, voice-mlx, voice-vits, subtitle-mlx, music-mlx, music-yue2, chat-mlx, video-ltx, embed-mlx, vision-mlx |
| minimum_macos | Current qualification candidate; not physical model qualification |
| files | Relative destination path, byte size, expected SHA-256 and HTTPS sources |
| files.source_path | Optional primary-file alias for an original publisher folder without the model/ prefix |
| requires_packages | Shared companion packages, resolved against the same trusted catalog. Present **exactly when** the package declares a companion, so an empty list is not a valid shape. `dependencies` was the first spelling and is **refused** by `validate_manifest` — a second field nothing resolves is how a declared companion gets silently ignored |
| subtitle | Present exactly when `engine` is `subtitle-mlx`: this model's own `family`, `languages`, `segment_timestamps`, `word_timestamps` and `long_form`, so a worker publishes no capability it was not told |
| default | Optional; `true` on at most one manifest per engine — the model a worker picks when no model is asked for |
| defaults / qualification | Model-specific defaults/evidence; never permission to bypass device checks |
| source | Exact upstream repositories, revisions, file layout and capture provenance |

Weight digests captured from official LFS metadata are **expected** digests, not claims
that the model bytes were downloaded or verified locally. Smaller companion files are
hashed during capture. The capture command is a build-side tool; target nodes use the
shipped catalog and do not resolve moving upstream revisions.

The content identity hashes schema, package/model/revision/engine/OS requirements,
required companions, file paths/aliases, sizes and checksums. Download mirrors and
editable generation defaults are excluded. Changing a mirror does not invalidate the
expected content; changing a file checksum or runtime family requires reverification.

A digest keys an installed verification receipt, so it is deliberately narrow. Moving a manifest's
companion list from the legacy `dependencies` field to `requires_packages` **does** change that
package's digest, and the one shipped package that carried it (`chatterbox-{4,8}bit`, companion
`s3tokenizerv2`) is re-verified once on the next launch — correct, because the receipt it replaces
was written by code that ignored the companion relationship entirely.

Measured on 2026-09-25, computing `manifest_digest` for every manifest this migration touched,
old against new: **`chatterbox-4bit` and `chatterbox-8bit` changed; `ace-step-1-5-turbo`,
`stable-audio-3-medium`, `fish-audio-s2-pro-8bit`, `fish-audio-s2-pro-bf16`, `s3tokenizerv2` and
`qwen3-5-9b-4bit` are unchanged** — the first five because the key removed from them was never
read, the last because its edit is in `defaults`, which this identity excludes on purpose.

## Library ownership and installation

The selected library must already exist. A disconnected SSD is an error; no replacement
directory is created. Normal imported/downloaded layout is:

~~~text
Selected Model Library/
  package-id/revision/
    model/                         # publisher file hierarchy
    companions/processor/          # where required
  .studiohub/
    installed.json                 # private, atomic verification receipts
    import.lock                    # serializes imports, not generation
    staging/                       # unadvertised incomplete downloads/imports
~~~

An owner-copied complete folder already under the library can register in place without
a second full copy. Its name need not be the package ID. Refresh inspects immediate
library folders and canonical package/revision folders, not arbitrary recursive paths.
Original publisher layouts use only the primary files' explicit source_path aliases;
processor companions keep their separate directory. Unknown layouts remain unregistered.

Importing an external folder stages into the selected library. Select an SSD as the
library first to register its contents in place. External HF snapshot file links may
resolve into that repository's sibling blobs directory; import materializes regular
files so the result does not depend on the source cache. Installed files and their
intermediate directories must not be symlinks. No imported code is executed.

Both installation paths validate every required file and SHA-256. Incomplete data,
missing tokenizers/shards, unreadable files, LFS pointers and unsafe paths cannot publish
a verified receipt. Complete files survive an interrupted download, and a subsequent
explicit retry resumes partial bytes when the HTTPS host supports the required Range
response. A server ignoring Range restarts that file and still verifies the full digest.

Only complete, verified folders enter the atomic index. Failed replacement leaves the
previous receipt intact. An existing invalid revision folder is preserved while repair
publishes a separately named complete folder; no caller deletes models or active readers
as a side effect of repair. Model removal remains the existing explicit guarded action.

## Functions used by ST-02

~~~python
validate_manifest(manifest)
manifest_digest(manifest)
import_folder(manifest, source_folder, selected_library)
download_package(manifest, selected_library, sources=mirror_overrides, catalog=catalog)
scan_library(catalog.values(), selected_library, catalog=catalog)
preflight(manifest, selected_library, catalog=catalog)
verify_installed(manifest, selected_library, catalog=catalog)
~~~

Explicit download includes declared companion packages from the trusted catalog.
Offline import never downloads missing companions. Qwen Base references the shared
Whisper Turbo package for its internal quality check, rather than copying it twice.
Whisper's companion list supports the adopted narrow `WhisperTokenizer` path in
`components/voice/app/backend/transcription.py`; it does not promise the optional
full `WhisperProcessor` fallback. ST-04/ST-06 must pass the verified local companion
folder to that existing tokenizer path, so runtime inference never resolves a moving
upstream repository or fetches missing assets. The standalone Turbo companion folder
has a separate offline tokenizer load/round-trip receipt.
Model downloads do not import/install Python dependencies. The caller owns cancellation
of the download job; interruption leaves staging resumable and never clears model failure
protection. ST-02 must connect its existing run cancellation to that operation.

Preflight checks required files are readable regular files and compares size, device,
inode, modification time and change time with the full-hash verification receipt. A
changed file needs explicit full verification. It rechecks companion receipts as well.
This is an efficient cooperative-filesystem change detector, not an adversarial filesystem
integrity service. The app must protect its private receipt directory and retain the
existing model read/removal safeguards during generation.
Explicit Verify rehashes the selected package's bytes and refreshes its receipt;
companion eligibility remains separately visible. File verification is not an inference
test and must not clear a model's generation failure protection.

| File state | Meaning for the caller |
| --- | --- |
| not_installed | No receipt for this exact content identity |
| invalid | Required files/receipt changed, unavailable or unsafe; show exact reason |
| dependency_missing | A required companion lacks a valid matching receipt |
| files_verified | File preflight passed; still not generation readiness |

File verification deliberately cannot report Ready. The later Hub combines it with:
matching bundled runtime identity, actual OS/chip/RAM eligibility, model-specific
qualification, model pause/protection state, storage space and physical-node admission.
Return truthful unsupported/unknown states instead of mapping absence to a healthy zero.

## Existing Hub semantics to preserve

- Device/site identity is independent of IP and editable display labels. Preserve
  Standalone/Controller/Agent roles and the existing prohibition on customer queue
  submissions to agents. GatewayKH contacts location controllers; their joined nodes do not contact GatewayKH.
  This owner-confirmed client change does not rename inherited wire fields or
  certify the counterpart: test the exact GatewayKH adapter revision under R09.
- Preserve batch ID, item index and clientRequestId/fingerprint replay. Count execution
  attempts/retries separately from requested items; lost responses reconcile original work.
- Preserve submit/status/cancel, Hub-relative artifact proxy, SHA-256/byte evidence and
  verified delivery acknowledgements. Do not expose worker-local paths as customer URLs.
- Native and remote work use the same Hub admission. Workers bind privately. Runtime
  identity uses the manifest runtime_sha256; the release separately records
  payload_sha256 and exact adoption commits. Build-side lock hashes remain in the
  runtime input records, not in the worker descriptor. Never present a fabricated
  Git identity or a files-verified badge as successful model execution.
- Add independent Subtitle worker/registry/ledger entries deliberately. Its public jobs
  and history are separate; Voice's internal Whisper quality check remains part of the
  parent Voice job under the same admission lease.
- Device-owned settings provide defaults/overrides and durable acknowledgements. Output
  registration carries job/device/model/runtime identity, finalized generated_at, bytes,
  checksum and delivery/reader state for ST-07's shared cleanup. Models are outside the
  generated-output quota.

Exact audited endpoints and source references are in the
[Hub inventory](../research/rescope-hub-storage-2026-09-15.md).

## Build/operator CLI

Use the bundled/build-side Python 3.12. These are explicit commands for operators and
tests; later native controls call the same functions without asking users for Terminal.

~~~sh
python3 -m components.model_packages import \
  --manifest model-manifests/whisper-turbo.json \
  --library '/absolute/path/to/Model Library' --source '/absolute/path/to/complete-folder'

python3 -m components.model_packages download \
  --manifest model-manifests/whisper-turbo.json --library '/absolute/path/to/Model Library'

python3 -m components.model_packages scan \
  --manifest model-manifests --library '/absolute/path/to/Model Library'

python3 -m components.model_packages verify \
  --manifest model-manifests/whisper-turbo.json --library '/absolute/path/to/Model Library'
~~~

Download accepts --sources pointing to an owner-selected JSON map of destination file
paths to HTTPS URL lists, allowing GitHub release assets or another valid mirror while
preserving expected file hashes. No repository clone or package-manager action occurs.
The command reads adjacent catalog manifests for companion resolution.

## Acceptance boundary

ST-01 provides pinned inputs, import/download behavior and recorded runtime import/audit
evidence. It does not certify all requested model inference on the new runtimes, make a
complete multi-worker app, or establish physical M1/Sonoma support. Those remain the
named later engine and ST-11 checkpoints, with exact runtime hashes carried forward.


## ST-02 packaged handoff

`packaging/hub_worker.py` is the `StudioHubService` entry point. The native shell
reads its owner-only `hub-ready.json` and sends `X-Hub-Token` for API calls to that
exact loopback URL; its health check uses the accepted Bearer form. The Hub starts Voice privately using the same existing Hub token.
The readiness file contains URL, PID, release version and token; never log its token.
`components/hub/standalone.py` owns the FastAPI lifecycle and the retained schedulers.

The wrapper supplies these paths rather than discovering existing Pinokio installs:

| Input | Value / owner |
| --- | --- |
| STUDIOHUB_DATA_DIR | External app data `/hub` directory for Hub ledger, settings and downloads |
| STUDIOHUB_MODELS_DIR | Existing selected model library; missing volume fails startup |
| STUDIOHUB_WORKERS_FILE | Private `hub/owned-workers.json`, removed on owned service shutdown |
| STUDIOHUB_MODEL_MANIFESTS | Shipped Resources/model-manifests |
| STUDIOHUB_RELEASE_MANIFEST | Shipped release-manifest.json with exact source/runtime identity |

Descriptors list only workers actually running. Required fields are `id` (image,
voice or subtitle), matching `modality` (image, voice or transcription), loopback
host and distinct port, `local: true`, stable `machine`, existing `hub_token` and
`runtime_identity`. A compatible runtime needs a nonempty ID and `compatible: true`.
The same credential supplies `studio_token`. ST-03/ST-06 add owned Image/Subtitle
processes; the current wrapper starts only Voice. ST-10 adds real fleet integration.

### Authenticated API boundaries

- `/api/hub/jobs`: existing native/external submit, list, item status, cancel and
  Hub-relative artifact flow. Replay preserves the original execution identity and
  validates conflicts before treating the request as new work.
- `/api/hub/transcription/jobs`: separate multipart Subtitle lifecycle. A request
  that may already have reached a synchronous worker must remain uncertain after
  cancellation, connection loss or restart. It cannot be automatically resubmitted.
  The persisted physical machine remains occupied in the existing admission check
  until reconciliation; other machines remain eligible.
- `/api/hub/model-packages`: catalog/file/runtime readiness; `/{package_id}/import`,
  `/download` and `/verify` use the ST-01 helper. Download returns a durable run ID;
  `/api/hub/model-downloads/{run_id}` gets or cancels that run; `GET /api/hub/model-downloads`
  lists every run, newest first, whoever started it (app, fleet install, LAN copy). Explicit retries reuse
  validated partial bytes without changing model protection.
- `/api/hub/settings`: GET defaults/effective values, PUT `max_bytes` and
  `retention_days`, POST `/restore-defaults`. ST-07 extends this same owner instead
  of creating another settings service. Current defaults are 50,000,000,000 and 30.
- `/api/hub/controller`, `/api/hub/capabilities`, `/api/hub/workers` and preserved
  statistics/pause routes expose existing identity and admission semantics.

### Storage and receipt handoff to ST-07/ST-08

`components/hub/delivery.py` registers the existing item ACK route. It requires a
done item, successful worker acknowledgement and durable Hub receipt save. Failure
leaves it unacknowledged; sequential repeats return the recorded receipt. Concurrent
or lost-response retries may repeat the downstream call, so the worker ACK contract
must be idempotent. Remote worker routing is qualified by ST-10.

Retained Voice 2.7.2 lacks that worker ACK endpoint. The Hub boundary is fixture-tested;
ST-07 supplies the worker output registration/receipt mechanism. Native export does
not send an ACK until ST-07/ST-08 can verify saved destination bytes/hash. Hub-owned
Voice disables its previous 80 GiB background cleaner using
`STUDIOHUB_OUTPUT_POLICY_OWNER=hub`; packaged Subtitle also leaves its legacy cleanup
timer off. ST-07 supplies shared enforcement. This interim
development build therefore preserves outputs. Existing standalone Voice cleanup
behavior outside Hub ownership is unchanged.

ST-07 must register finalized groups using original generation time, exact paths,
bytes/hash, job/item/attempt and physical device/model/runtime identity. Existing
ledger metadata and artifact evidence are inputs; no output index is claimed here.
Models, originals, active/uncertain and unacknowledged files stay protected. Tests may
exercise deletion only inside temporary fixtures.
