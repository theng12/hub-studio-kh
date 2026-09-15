# ST-06 card handoff (for the coordinator to apply to `docs/tasks/ST-06.md`)

`docs/` is root-owned, so this file is the card update. Facts and evidence are in
[st06.md](st06.md).

## Handoff — filled

- **Owner/session:** Worker agent (DeepSeek Harness session), dispatched for the
  ST-06.TURBO checkpoint.
- **Start date, commit and worktree:** 2026-09-15 · worktree `.worktrees/st-06` ·
  branch `implementation/st-06` · base `implementation/voice-runtime` ·
  start commit `6ab93347db901ba9436c4b2bf876201543e6f09b`.
- **Owned paths and shared-file coordination:** new `components/subtitle/`,
  `packaging/subtitle_worker.py`, `tests/test_subtitle_standalone.py`,
  `tests/test_subtitle_bundle.py`,
  `tests/hub_regression/cases/test_subtitle_dispatch.py`,
  `qualification/st06.md`, `qualification/st06-handoff.md`. No root-owned file was
  edited. Required shared-file changes are listed below and are **not applied**.
- **Current checkpoint/status:** PARTIAL. ST-06.1, .2, .3, .4 (Base/Small
  catalogued only), .5 and .TURBO have implementation evidence; ST-06.6 and
  integration/review do not. Turbo real inference is proven through the packaged
  worker; Base/Small are refused with a concrete reason.
- **Changed files and implementation commit:** see the handoff commit recorded by
  the coordinator; changed paths are the owned paths above plus nothing else.
- **Commands, return codes and result/log paths:** [st06.md](st06.md) "Evidence:
  exact commands and results" — focused suites exit 0 (35 and 4 cases, gated
  real-model cases included), real Turbo through the packaged worker exits 0, Hub
  regression 52 passed, whole project suite 162 cases exit 0, Voice regression
  639 passed / 2 skipped exit 0. Logs under `.build/st06-logs/` and real
  artifacts under `.build/st06-real/`.
- **Model/runtime/app IDs and actual hardware:** model
  `mlx-community/whisper-large-v3-turbo` revision
  `a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb` (manifest digest
  `2f6080e652cb3dd8f52ea61f092d8c7d85a326d81753285f93eb7dacf25d0531`); shared
  bundled runtime `.build/voice-runtime`, Python 3.12.14, `mlx_audio` present;
  export source SHA-256
  `09e8def3c013e5080b5c91927c9b088475f7d65c2206322fbb449c8491e0ff08`. Host: Apple
  M4 Mac mini, 16 GiB, macOS 26.6.2 (25G83). No packaged app artifact exists for
  Subtitle.
- **Independent reviewer and resolved findings:** none — **no independent review
  has been performed.**
- **Remaining limitation/blocker:** (a) Base and Small cannot run on this
  runtime — an **mlx-audio loader limitation**, not a packaging error: the loader
  reads only `*.safetensors`, and upstream publishes only `weights.npz` at the
  pinned Base/Small revisions. They are catalogued and refused (`409`), never
  silently loaded. See the handoff section for the three runnable options and the
  recommendation. (b) Not integrated into the packaged app or native UI; no
  assembled-app receipt. (c) Cancellation cannot interrupt `mlx-audio` decoding
  mid-flight; a cancel during decoding discards the result instead. (d) No
  node-concurrency, physical M1/Sonoma, or word-timestamp/video-input proof.
- **Integrated commit / DONE date:** not integrated; not DONE.
- **Next work:** ST-08; Turbo can support the intermediate pilot **only after**
  the coordinator applies the shared-file changes below and the receipt passes
  independent review. Base/Small need a runtime/model decision first.

---

# Exact shared-file contract changes the coordinator must make

Nothing below has been applied. Each item names the file, the required change,
and why.

## 1. `packaging/hub_worker.py` — own the Subtitle process

The wrapper currently starts only Voice and Image. Required:

1. Extend the child spec list (currently
   `specs = [('voice', runtime, release['runtime_sha256'], data / 'voice/output')]`
   plus the Image append) with a subtitle entry that reuses the **shared** Voice
   runtime and a distinct state root:

   ```python
   specs.append(('subtitle', runtime, subtitle_runtime_id, data / 'subtitle'))
   ```

   `subtitle_runtime_id` must be the exact 64-hex identity recorded in the
   release manifest for `components.subtitle` (see item 3). Using a distinct
   `runtime_sha256` value that is not the shipped tree digest would be a
   fabricated identity; keep them equal.

2. The descriptor writer uses `'modality': child['id']`, so a `subtitle` child
   would publish `modality = "subtitle"`. The transcription dispatcher requires
   `modality == "transcription"` when `STUDIOHUB_PACKAGED == "1"`
   (`components/hub/backend/transcription_jobs.py`, `_eligible_studios`). Change
   the child record to carry an explicit modality and write that:

   ```python
   children.append({'id': 'subtitle', 'modality': 'transcription', ...})
   # and in load_app():
   'id': child['id'], 'modality': child['modality'], ...
   ```

   `id` stays `subtitle` (it is the worker/registry key); only `modality` is
   `transcription`. This matches the convention already used by the packaged Hub
   regression fixture (`tests/hub_regression/cases/conftest.py`, which registers
   `{"id": "subtitle", "modality": "transcription", …}`).

3. Add the Subtitle readiness file to the startup/cleanup path the same way
   Image does (`subtitle-ready.json` under `hub_data`), and include the new child
   in the existing `watch_children` supervision so a dead Subtitle worker stops
   its Hub owner.

4. `packaging/subtitle_worker.py` already accepts exactly the argument set the
   wrapper passes (`--data-dir --models-dir --ready-file --parent-pid
   --hub-token-file --port --offline --check`); no change is needed there. It
   reads `STUDIOHUB_FLEET_TOKEN` from the private token file exactly like the
   Image worker.

## 2. `packaging/stage_voice.py` — ship the tree and record its identity

1. Inside the `include_hub` block (or a new guard), copy the new source and
   worker into the bundle:

   ```python
   copy_tracked_tree(ROOT / 'components/subtitle', resources / 'components/subtitle')
   shutil.copyfile(ROOT / 'packaging/subtitle_worker.py', resources / 'subtitle_worker.py')
   ```

   The Subtitle worker also needs the shared Voice source it imports. The bundle
   already ships `Resources/voice/app/backend` for the Voice service, and
   `subtitle_worker.py` points `STUDIOHUB_VOICE_APP_DIR` at
   `Resources/voice/app`; verify that path is present in the same staging run
   (it is today), because Subtitle imports
   `components/voice/app/backend/transcription.py` from there instead of carrying
   a copy.

2. Record the component identity in the release manifest, using the **same**
   runtime tree digest as Voice:

   ```python
   release['components']['subtitle'] = {
       'source_sha256': tree_digest(resources / 'components/subtitle'),
       'runtime': 'voice',
       'runtime_sha256': release['runtime_sha256'],
   }
   ```

   `packaging/subtitle_worker.py` refuses to start unless
   `components.subtitle.runtime_sha256` is a 64-hex string, and
   `components/subtitle/app/backend/main.py:from_environment` reads the same
   value as the runtime identity it reports to the Hub.

## 3. `components/hub/standalone.py` — Subtitle read routes (native UI)

Add authenticated pass-through reads mirroring the existing `voice_read` helper,
pointed at the worker whose `id == "subtitle"`:

| Route | Worker route |
| --- | --- |
| `GET /studio/subtitle/api/catalog` | `/api/catalog` |
| `GET /studio/subtitle/api/transcribe/availability` | `/api/transcribe/availability` |
| `GET /studio/subtitle/api/generate/transcribe/jobs` | `/api/generate/transcribe/jobs` |
| `GET /studio/subtitle/api/generate/transcribe/jobs/{job_id}` | `/api/generate/transcribe/jobs/{job_id}` |

The existing `voice_read` helper is already generic apart from the worker id; the
smallest change is to parameterize it by worker id and call it twice. Public
Subtitle job **state** must come from these Subtitle routes, not from the Voice
worker, otherwise the separation is only nominal.

## 4. `native/StudioAPI.swift` / `native/StudioViews.swift` — point the workspace at Subtitle

Today `refresh()` reads `studio/voice/api/transcribe/availability` and
`transcribe()` submits to `api/hub/transcription/jobs`. Required:

1. Availability: read `studio/subtitle/api/transcribe/availability`.
2. Submission: keep `api/hub/transcription/jobs` (the common admission lease is
   required by the runtime contract) but ensure the Hub now routes it to the
   Subtitle worker rather than the Voice worker. If the coordinator wants Voice's
   internal STT to remain reachable for quality checks, the routing decision must
   be explicit — see item 6.
3. Export/ack: the existing artifact route and ACK wiring are unchanged; the Hub
   transcription artifact route already serves the SRT the Subtitle worker
   returns.

## 5. Native export acknowledgement (shared with ST-08)

`native/StudioViews.swift:saveAudio` still writes bytes with no verified
destination acknowledgement. ST-03 fixed this only for Image
(`StudioAPI.swift:saveImage`). Subtitle export should use the same pattern so
`/api/generate/transcribe/jobs/{id}/ack` can be sent after a verified save. The
Subtitle worker's ACK is idempotent and refuses to acknowledge unless the SRT
artifact still matches its recorded size and SHA-256.

## 6. Board/registry note the coordinator must decide

Both `voice` (which has its own STT subsystem) and `subtitle` can report
`modality == "transcription"` to the transcription dispatcher. The dispatcher
picks eligible workers by modality, model and availability; it does not prefer a
worker id. Before wiring this, the coordinator should confirm the intended split:

* **Recommended:** public Subtitle jobs are served by the `subtitle` worker, and
  Voice's Whisper quality check stays internal (its own `/api/transcribe` on
  Voice, called inside the parent Voice job), so the Voice worker does not
  advertise `modality == "transcription"` to the packaged public scheduler.
  Preserving both paths would need an explicit routing rule; that is a
  coordinator decision, not a Subtitle-worker change.

## 7. Model catalog decision (Base/Small) — scope as its own checkpoint

Classification: **an mlx-audio loader limitation**, not an ST-01 packaging
mistake. `mlx_audio/stt/models/whisper/whisper.py:665` globs only
`*.safetensors`; upstream `mlx-community/whisper-base-mlx` and
`whisper-small-mlx` publish only `weights.npz` at the pinned revisions, and
`load_model(..., strict=False)` then returns a **randomly initialised** model
with no error. Voice never hit this because its registry only named safetensors
variants. The Subtitle service reports `unsupported_weight_format` and refuses
submissions, so no worker change is needed whichever option is chosen.

Runnable options, none attempted here:

1. **Build-side offline conversion (recommended).** The bundled runtime already
   ships `mlx_audio.convert.convert`, whose weight path uses `mx.load` (npz
   capable) and writes `model.safetensors`. Convert the pinned npz folder, pin
   the new project-managed revision, record the new checksums. No node-runtime
   network dependency.
2. **Pin upstream PyTorch safetensors.** `openai/whisper-base` revision
   `e37978b9…` and `openai/whisper-small` revision `973afd24…` publish
   `model.safetensors` — the same revisions ST-01 already uses for their
   processor companions. Cheap, but must be verified with a real transcription;
   a wrong load here is silent.
3. **Add a narrow `npz` read path** to the vendored Whisper loader. Smallest
   diff, largest blast radius: it edits the audited runtime tree, so the runtime
   audit and hashes must be redone.

Recommendation: a separate checkpoint ("ST-06.BASE-SMALL" or a shared
runtime/model-package task) doing option 1, with option 2 tested first because
it is cheaper. Require a real Base and Small transcription before either is
advertised, and do not relax the refusal gate until then.

---

# Files this work added (for review)

| File | Purpose |
| --- | --- |
| `components/subtitle/app/backend/engine.py` | Verified-package Whisper engine; imports Voice export/normalization at one source path; stages the companion tokenizer privately so the model library stays immutable |
| `components/subtitle/app/backend/manager.py` | Independent job ledger, cancellation, history, receipts, exports |
| `components/subtitle/app/backend/main.py` | Private FastAPI surface (health/catalog/submit/status/cancel/artifact/ack) |
| `components/subtitle/{__init__.py,app/__init__.py,app/backend/__init__.py}` | Package markers |
| `packaging/subtitle_worker.py` | Bundle-relative owned process entry point |
| `tests/test_subtitle_standalone.py` | Contract, catalog, decode, lifecycle, API and real-model tests |
| `tests/test_subtitle_bundle.py` | Real `-I -B` worker process tests, including real Turbo over HTTP |
| `tests/hub_regression/cases/test_subtitle_dispatch.py` | Hub drives the subtitle worker; uncertainty fencing preserved |
| `qualification/st06.md` | Full receipt |
| `qualification/st06-handoff.md` | This card update and the shared-file contract |
