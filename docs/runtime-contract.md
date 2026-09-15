# ST-01 runtime and model contract

Implemented boundary: model package validation/import/download and pinned runtime inputs.
Hub ownership, native model controls and generation admission are implemented by later
tasks. This document extends the existing Hub contracts rather than defining new job APIs.

## Files and callers

- [model_packages.py](../components/model_packages.py): standard-library runtime helper.
- [Model catalog](../model-manifests/): ten exact packages; no moving model revisions.
- [Runtime input records](../research/st01-runtime-contracts.json): engine families,
  lock hashes, source provenance and limits of the import/audit evidence.
- [Model package tests](../tests/test_model_packages.py): filesystem, download and receipt behavior.

ST-02 stages the helper and catalog alongside Hub and supplies the owner-authenticated
model-management API using the existing asynchronous download/run lifecycle. This phase
does not add native UI, start a Hub, change an existing model cache or deploy a node.

## Model manifest v1

Each package contains:

| Field | Meaning |
| --- | --- |
| schema_version | 1; reject unknown versions |
| package_id | Stable local catalog key, independent of its download host |
| model_id / revision | Publisher model ID and full pinned Git revision |
| engine | image-mflux, voice-mlx or subtitle-mlx |
| minimum_macos | Current qualification candidate; not physical model qualification |
| files | Relative destination path, byte size, expected SHA-256 and HTTPS sources |
| files.source_path | Optional primary-file alias for an original publisher folder without the model/ prefix |
| requires_packages | Shared companion packages, resolved against the same trusted catalog |
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
  submissions to agents. GenStudio contacts location controllers; their joined nodes do not contact GenStudio.
- Preserve batch ID, item index and clientRequestId/fingerprint replay. Count execution
  attempts/retries separately from requested items; lost responses reconcile original work.
- Preserve submit/status/cancel, Hub-relative artifact proxy, SHA-256/byte evidence and
  verified delivery acknowledgements. Do not expose worker-local paths as customer URLs.
- Native and remote work use the same Hub admission. Workers bind privately. Runtime
  identity records the package lock hash and staged artifact hash, never a fabricated
  Git identity or a files-verified badge presented as successful model execution.
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
