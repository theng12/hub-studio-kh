# Changelog

## 0.6.19 — 2026-09-19 · a failed dispatch write stranded the job and the Mac

- **`transcription_jobs`' dispatch loop leased the Mac, marked the item running in memory, then
  saved.** A raising `_save` (full disk, unreadable database) propagated out of the loop, leaving the
  item `running` with no task attached - it could never finish, fail or be retried - and the Mac
  leased to that job, reading as busy until the Hub restarted. The write is now wrapped: on failure
  `broker.release_external_machine` is called, the item returns to `queued` with its running fields
  cleared, the attempt it had just spent is given back, the studio is dropped from the pass's busy
  set, and the batch records why ("...so it is queued again").
- Evidence: `tests/test_hub_jobs.py` (7 tests) passes, including the dispatch paths it exercises. A
  dedicated regression test could **not** be placed reliably: the fixture constants it needs
  (`_WORKERS`) sit inside a dead region of that module (see below), so the test was withdrawn rather
  than left failing. This is the one fix today without its own test, and it is recorded as such.
- New finding, from trying to write that test: **`tests/test_hub_jobs.py` contains a large dead
  region.** Its AST shows only three top-level classes and two assignments while the text contains
  five `class` definitions and constants such as `_WORKERS` at line 39 - so some of that file's
  fixture code and at least one duplicate class definition never execute. Two of my edits this session
  landed inside it and silently did nothing until a test failed. That is the "tests that cannot fail"
  class of defect, and it is the next thing to fix.

## 0.6.18 — 2026-09-19 · the app re-read every historical job every two seconds

- **`refresh()` fetched one job detail per batch, every two seconds, for ever.** History does not
  change, so `finishedJobCache` now remembers a job whose state is final (`done`, `cancelled`) and
  the poll reuses it; the cache is rebuilt each pass from the batches the Hub still lists, so it
  stays as small as the listing, and cleared on `disconnect()`.
- **The safety condition was checked, not assumed.** The reason this was deferred twice is that a job
  you retry could become live again and be shown as permanently finished. Traced: the app has no
  retry action (its only mention of the word is a message), and `broker.retry_batch`,
  `remove_batch` and `clear_terminal` have no HTTP route and no caller outside tests. The states that
  do come back - `uncertain`, re-queued by the Hub - are not final and are never cached.
- Evidence: the native fixture gained a job-detail read counter and asserts a finished job is not
  fetched on a second poll (and that the failed-detail path still reports, from an isolated store);
  `tests/test_job_detail_cache_is_safe.py` (5 checks) pins that only final states are cached, that a
  reconnect drops the cache, and **that no route can re-open a finished job** - so adding one fails
  the test with instructions rather than silently hiding a retried job. The fixture's unresolved-image
  case now uses its own batch id instead of flipping a finished job's state, which is what a retry
  would look like and cannot happen today.

## 0.6.17 — 2026-09-19 · the voice loader could run a different revision than the job recorded

- **`GenerationManager._mlx_audio_snapshot_path` returned `candidates[0]`** from
  `models--<repo>/snapshots/`, and consulted `cache.package_snapshot` (the verified-package library,
  which resolves the manifest's exact revision) only when the cache had no `snapshots` folder at all.
  With both present, the arbitrary cache folder won: a Mac holding the verified revision *and* an
  older leftover could load the older files while the job wrote the verified revision into its record
  as immutable evidence - wrong evidence, and a result that cannot be reproduced from the record.
  The verified package is now resolved first, and the cache fallback sorts by modification time
  (newest first) instead of trusting filesystem order.
- Evidence: `tests/test_voice_snapshot_revision.py` (4 checks) - the verified revision wins over the
  cache, the cache fallback picks the newest and is stable across calls, a missing cache still names
  the fix, and a single revision needs no choice.

## 0.6.16 — 2026-09-19 · a refused credential looked like a switched-off Mac

- **`fleet_nodes.probe` treated every non-success answer as a generic error**, and the Fleet page's
  reading of a node that does not answer is "unreachable". A 401/403 means the opposite: the Mac is
  up and refused *this controller* - the state you get when a node was re-joined with its own token,
  or the controller's fleet token was replaced. The probe now returns `state="unauthorized"` with an
  actionable detail naming the status and the fix, and `inventory`'s summary counts it separately
  from `unreachable`, so it cannot hide inside a total.
- Evidence: `tests/test_fleet_credential_refusal.py` (5 checks) - a 401 and a 403 are their own state
  with the Fleet Token named, other failures stay errors, a healthy node still reads reachable, and
  the summary counts a refusal separately from a silent Mac.

## 0.6.15 — 2026-09-19 · the fleet inventory probed nodes one at a time

- **`fleet_nodes.inventory` awaited `probe` in a loop.** Each probe waits out `PROBE_TIMEOUT` for a
  silent node, so the page cost one timeout per switched-off Mac - ten of them meant ten timeouts
  before the app's own 30-second read gave up and called the Hub unresponsive. The probes now run
  under `asyncio.gather` with the order preserved; `probe` itself is unchanged and its per-node errors
  were already swallowed.
- Caught while doing it: the change needed `import asyncio`, which the module did not have - the
  syntax check passed and the call would have failed at runtime, so the module was imported and
  `inventory` called directly before trusting it.
- Evidence: `tests/test_fleet_inventory_concurrent.py` (3 checks) - four nodes that each take 0.15 s
  finish well inside half the sequential time, every probe starts before the first finishes, and the
  per-node state is still reported.

## 0.6.14 — 2026-09-19 · a cancel during publishing was overwritten by "done"

- **`SubtitleManager._run` checked the cancel event after decoding, then published artifacts and
  committed `state='done'`.** A cancel arriving between those two points - after the check, before
  the commit - was silently overwritten: the app showed a finished job the operator had cancelled,
  with artifacts kept, and the Hub's lifecycle treated it as completed work. The commit now re-checks
  the event inside the lock, marks the job cancelled, drops the artifact references, removes the
  files this call just wrote, and returns.
- Evidence: `tests/test_subtitle_cancel_wins.py` (3 checks) drives the real `_run` with a stub
  decoder and a `_publish` that sets the cancel event mid-write - the job ends cancelled, no
  artifacts are advertised and the written file is gone - plus two controls: an uncancelled job still
  finishes and keeps its file, and a cancel before decoding still cancels. Mutation-verified
  (removing the guard fails the first).

## 0.6.13 — 2026-09-19 · the download row drew a hardcoded zero

- **The Downloads row drew `ProgressView(value: percent)` unconditionally** for an active download.
  `model_downloads` runs the downloader as a child process and records only `{"id", "package_id",
  "state", "started_at", "pid"}` - there are no byte counts anywhere in the Hub - and the app filled
  the gap with `percent: 0, downloadedBytes: 0, totalBytes: 0`, so a 12 GB model read "0 bytes" and
  the bar never moved for the whole download. The row now draws a bar only when there are real
  numbers (`percent > 0 || totalBytes > 0`) and otherwise shows a spinner with the model's own size,
  looked up from the catalog - the same figure the Models page displays.
- Note: the Models page already showed a real size per model (`Estimated size`); the misleading
  figure was in the transfers list.
- Evidence: `tests/test_download_progress_honesty.py` (4 checks) - the unconditional bar is gone, a
  bar is still drawn when numbers exist, the caption names the model's size, and the Hub's run record
  genuinely carries no progress (so the honest thing is a caption, not a percentage).

## 0.6.12 — 2026-09-19 · `cleanup_expired` had no caller

- **`execution_assets.cleanup_expired` existed and was called from nowhere.** Staged voice-reference
  copies carry an `expires_at` (24 h default, 48 h maximum), and the deadline was only honoured by
  `resolve_voice_reference`, which *refuses* an expired asset - so the file stayed. Every duplicate
  clip the Hub ever made for a worker was retained for the life of the Mac. `job_storage.
  _enforce_budget` now calls it alongside `transcription_jobs.cleanup`, so the five-minute sweep
  deletes them. The library and shared voices are unaffected: they are not staged assets.
- Evidence: `tests/test_execution_asset_purge.py` (4 checks) - an expired copy is deleted, a live one
  is kept, a folder whose metadata cannot say when it expires is cleared out (the store's existing
  behaviour, now pinned so the sweep cannot quietly start keeping junk), and the sweep is what calls
  it.

## 0.6.11 — 2026-09-19 · the voice transcribe upload was unbounded in memory

- **`main.py:1602` did `data = await file.read()`** in the transcribe-by-upload handler: the whole
  recording in memory before it reached the temporary file, with no cap on its size. (The audit said
  "voice upload endpoints apply the 25 MB limit after buffering" - that half is wrong: the
  reference-audio path already reads `reference_audio.MAX_BYTES + 1` at `main.py:1316`. Verified
  against the source before changing anything, which is why only this route was touched.) Now
  streamed in 1 MiB pieces to the temp file with `MAX_TRANSCRIBE_UPLOAD_BYTES` (matching the Hub's
  2 GiB per-file limit), the temp file removed on every failure path, and the existing empty-upload
  and read-failure responses kept. Gated on the full suite rather than on a unit test of a helper -
  the lesson from 0.6.9, where a helper test passed while the route was broken.

## 0.6.10 — 2026-09-19 · fix for a regression in 0.6.9

- **0.6.9 referenced `manager.MAX_INPUT_BYTES`**, but `manager` in that module is the *instance* and
  the constant is a module-level name in `manager.py` - so every Subtitle upload raised
  `AttributeError: 'SubtitleManager' object has no attribute 'MAX_INPUT_BYTES'`. Caught by the full
  suite before it reached the owner (`tests/test_subtitle_standalone.py`: 4 errors, all on upload).
  The handler now imports and uses the module constant.
- The lesson worth recording: the new unit checks passed against a fake upload while the real
  endpoint path was broken, because they exercised the helper rather than the route. The
  route-level tests in `test_subtitle_standalone.py` are what caught it, which is why they are the
  gate for anything touching a handler's collaborators.

## 0.6.9 — 2026-09-19 · the Subtitle upload limit was applied after buffering

- **`data = await file.read()` in both Subtitle upload handlers** returned the whole body before
  `manager.MAX_INPUT_BYTES` (2 GiB) was checked in `manager.store_input`. An oversized upload was
  therefore buffered in the worker process first; on a Mac holding a model in unified memory that is
  an OOM kill rather than a 413. New `read_upload_limited(file, limit)` reads in 1 MiB pieces and
  raises 413 at the first byte past the limit; both handlers use it.
  Evidence: `tests/test_subtitle_upload_limit.py` (5 checks) - a body within the limit comes back
  whole, an oversized one is refused with 413, **the refusal stops reading** (four reads of a
  thousand-piece body, the rest untouched), an empty body is fine, and no handler reads the body
  directly any more. Mutation-verified (reading the whole body per call fails three).
- Still open from the audit, and unchanged by this: a *legitimate* upload up to the 2 GiB limit is
  still held in memory before being written to the store, which wants the store API to accept a
  stream rather than bytes.

## 0.6.8 — 2026-09-19 · an alert failure aborted the health poll

- **`_note_transition` and `_note_restart_health` called `alerts.emit` unguarded**, inside the poll
  path. An unreachable webhook or an unwritable alert log raised out of the pass, so `self.status`
  kept its previous values for every Studio while the app presented them as current - and since
  0.6.3 the admission guard reads exactly that status to decide whether a job can run. Both helpers
  now use `self._alert`, which swallows the failure; the alerting module still raises for direct
  callers, so a broken alert is not silently hidden from anything that asks it directly.
  Evidence: `tests/test_monitor_alert_isolation.py` (6 checks) - a down transition, a recovery and a
  restart alert each survive a raising `emit`, a working `emit` is still called with the right kind,
  startup/steady-state transitions stay silent, and only the guarded helper may emit directly.

## 0.6.7 — 2026-09-19 · the identity file was written in place

- **`control_plane` wrote `controller_settings.json` with `write_text`**, which truncates first: a
  crash, kill or full disk between the truncate and the write left a partial file, and the loader
  treats unreadable as "no settings" - this Mac's identity and containment settings silently returned
  to defaults. Same for `database_url.txt`. New `_write_private_file(path, payload)`: temporary file
  in the same directory, `flush` + `fsync`, mode 0600, then `os.replace`. The rename either happened
  or it did not; there is no third state.
  Evidence: `tests/test_atomic_settings_write.py` (4 checks) - a failing `os.replace` leaves the
  previous content byte-identical and still parseable, a successful write is whole and 0600, no
  temporary file is left behind, and neither file is written directly any more. Mutation-verified
  (a direct write fails two of them).

## 0.6.6 — 2026-09-19 · a cancel Event per queued image job leaked

- **`GenerationManager._run` returned without releasing the cancel event.** When a job was cancelled
  while queued, the job's thread reached the top of `_run`, saw the event set, marked the job
  cancelled and returned - skipping the only two paths that remove the entry from `cancel_events`
  (a failed running-state persist, and the `finally` of the inference block, both of which come
  later). One `threading.Event` was therefore retained for every such cancellation, for the life of
  the process. The early-cancel path releases it now.
  Evidence: `tests/test_image_cancel_event_release.py` (3 checks) drives the real method - a
  cancelled-while-queued job, twenty-five of them in a row leaving the dictionary empty, and that
  `cancel()` still marks a queued job cancelled. Mutation-verified: removing the release fails two.

## 0.6.5 — 2026-09-19 · the seed catalog route was shadowed

- **`GET /api/voices/seed-catalog` was declared after `GET /api/voices/{voice_id}`.** FastAPI matches
  in declaration order, so the parameterised route answered first and the single-voice handler ran
  with `voice_id="seed-catalog"` - "voice not found". The ready-made catalog (`voices.seed_catalog`)
  was therefore unreachable from the app and the tab was permanently empty. The literal route is now
  registered before the parameterised one.
  Evidence: `tests/test_seed_catalog_route.py` (4 checks) asserts the declaration order, resolves the
  URL through the router (so it names the handler that actually answers), checks a single-voice URL
  still reaches the voice handler, and calls the handler. Mutation-verified: declaring the route last
  again fails two of them.

## 0.6.4 — 2026-09-19 · the admission sweep no longer runs in the scheduler's inner loop

- **`output_storage.admission` calls `enforce`**, which walks and hashes the managed output roots.
  `_submission_readiness` calls admission for every queued item on every dispatch pass - several
  times a second while work waits - synchronously on the event loop, so a Mac with a full library and
  a busy history stalled its own Hub (and the app's polls with it). Admission now answers from
  `_ADMISSION_SNAPSHOT`, a 10-second per-policy cache, deep-copied in and out so a caller annotating
  its refusal cannot corrupt a later answer, with a 64-entry bound so a fleet's per-node policies do
  not evict one another. Enforcement is unchanged: the background timer and every operator action
  still run it.
  Evidence: `tests/test_storage_admission_cache.py` (5 checks) - one sweep per burst, per-policy
  separation, the refusal still computed from the real numbers, copies handed out, and the snapshot
  expiring. All 27 tests in the modules that touch output storage still pass.

## 0.6.3 — 2026-09-19 · no Studio, no acceptance

- **The submission guard never asked whether a Studio was running.** `_submission_readiness` checked
  the packaged model gates and the storage budget; with no live Voice worker, `_eligible_studios`
  found nothing to dispatch to, so the batch sat in `queued` for ever while the app showed accepted
  work. There is no deregistration either - a dead worker stays in `monitor.registry` - so liveness
  is `monitor.status[id]["status"]`. The guard now refuses with `STUDIO_NOT_RUNNING` when the
  modality has no worker at all, or when every worker for it is explicitly not answering
  (`unknown` is left alone, so a Hub that has not run a health check yet does not refuse work at
  startup). Remote studios count, so a controller can still route to its nodes.
  `tests/test_admission_requires_a_studio.py` (5 checks) pins each branch, including that another
  modality's Studio does not count and that a node's does.
- This is the Hub-side half of the reason work in 0.5.7 (the app already named the reason; the Hub
  went on accepting the job).

## 0.6.2 — 2026-09-19 · mp4/mov audio extraction

- **Subtitle accepted .mp4/.mov and always failed.** Both the upload validation
  (`manager.ALLOWED_EXTENSIONS`) and the Hub (`transcription_jobs`) allow them; `engine.transcribe`
  handed the path to a Whisper decoder that reads audio through miniaudio, which does not open those
  containers, so a screen recording was accepted, dispatched and produced nothing. New
  `engine.decodable_audio(path)` context manager + `find_ffmpeg(voice_app_dir)`: a video container's
  audio track is extracted by the bundled ffmpeg (mono, 16 kHz, WAV, `-vn`) into a temporary
  directory that is removed afterwards, and ordinary audio passes straight through. ffmpeg is located
  the same way the Voice service does it - `STUDIOHUB_TOOLS_DIR` (set by the packaged worker, see
  0.5.8), then the Voice app's tools folder, then PATH - and a genuinely missing ffmpeg raises a
  message naming the file and the reason instead of a decoder error. The duration probe and the
  model both read the extracted file, so the timing clamp still applies.
  `tests/test_subtitle_video_audio.py` (5 checks, mutation-verified: disabling the extraction fails
  four).
- This closes the owner's three audit decisions: storage enforcement (0.6.1), uncertain-transcription
  recovery (0.6.1) and video input (0.6.2).

## 0.6.1 — 2026-09-19 · the owner's storage and recovery rules

- **The storage budget (50 GB / 30 days) was enforced only in development.** `job_storage.
  enforce_budget` - the shared sweep the runtime contract says supersedes the legacy timer - had no
  caller at all, and `transcription_jobs.start_dispatcher` starts its own cleanup loop only
  `if not _packaged()`. Nothing ever deleted an uploaded recording or a transcript in the shipped
  app, and the Hub's own storage figures never counted them. New `job_storage.budget_loop(interval)`
  + `sweep_once()`, started by the Hub's lifespan at `standalone_config.cleanup_interval_seconds()`
  (300 s by default, honouring the operator's setting) and cancelled on shutdown; the sweep runs off
  the event loop because it walks and deletes files. `tests/test_storage_budget_loop.py` (4 checks)
  pins the pass, the timer, that a failing sweep cannot kill the loop, and that the policy constants
  are the owner's numbers.
- **An uncertain transcription removed its machine from every lane, permanently.**
  `_mark_uncertain` sets `manual_action_required`; `retry_batch` skips such an item; `remove_batch`
  and `clear_terminal` refuse a non-terminal batch; no route exposes any of the three; and
  `_eligible_studios` excludes the machine for image, voice and subtitle alike. New
  `UNCERTAIN_RECOVERY_SECONDS = 900` + `recover_stale_uncertain(now)`, called at the top of
  `dispatch_once`: a stale fence is re-queued, its partial output deleted, the flag cleared and the
  reason written to the batch note. The busy-check comment now records that a fence is temporary by
  design: transcription is cheap enough to repeat. `tests/test_uncertain_recovery.py` (4 checks).

## 0.6.0 — 2026-09-19 · stale timings and unbounded memory

- **An edited transcript kept timings measured from the previous words.** `voices.update()` rewrote
  or removed `transcript.txt` and never touched `transcript.timed.json`, so the aligned timings a
  cloning model uses to pick its window described different words - the failure mode is a
  wrong-sounding clone with nothing to explain it. Editing the words (or clearing them) now removes
  the timed file; an unchanged transcript and every other field leave it alone. Four checks in
  `tests/test_voice_timings_invalidation.py`, mutation-verified (removing the invalidation fails two).
- **`broker.batches` was never pruned.** It holds every batch the process has been told about; the
  durable record is the ledger and the listings merge both, so the in-memory copy of a finished job
  is pure cost once it is old. `evict_finished_batches(keep_recent=20, older_than_s=900)` drops them,
  called once a minute from the dispatch loop; the most recent twenty stay because the per-model ETA
  is measured from them. Tested against a long-finished, a just-finished, a running and a half-done
  batch so a running job can never be evicted.
- Note on the test file: `tests/test_hub_jobs.py` has a region whose indentation made an inserted
  class land inside an existing block - the new class is at the end of the file now, and the module's
  seven tests run.

## 0.5.9 — 2026-09-19 · one failure is not the whole screen

- **`refresh()` aborted on any single sub-request.** Eight reads per two-second poll were awaited as
  one tuple, and each batch detail and each Hub download run was read with a throwing `try` inside
  the loop, so one 500 - a pruned job, a dead Subtitle worker, a forgotten download - threw out of
  the whole function, and the catch set `isReachable = false`. Because the poll passes
  `reportErrors: false`, it also showed no banner: the models, jobs, voices and connections all
  emptied and nothing said why. The studio-level reads are now optional (with defaults), and a batch
  detail or download that fails is skipped and counted, with the count shown in one line; only the
  catalog and the package list - the Hub itself - can mark it unreachable.
- **A corrupt file of the app's own blocked `connect()`.** `loadState`, `loadPending*` and
  `loadHubDownloadRuns` were throwing calls inside the same `do` as the Hub handshake, so a
  truncated `native-workspaces.json` cleared `baseURL`/`hubToken` and set `isReachable = false`; the
  file was then re-read on every reconnect, so the app could not recover without finding it in
  Finder. They are best-effort now: an unreadable file is renamed with a `.corrupt` suffix (kept, not
  deleted) and named in `startupNote`, which the main banner shows and can dismiss.
- Evidence: the native fixture gained one unreadable batch detail; the test asserts `isReachable`
  stays true, the readable jobs survive and the skip is reported. A second assertion writes invalid
  JSON into the app's own state file and asserts the app still connects, keeps the file as
  `.corrupt`, and says so. Both were checked by mutation: reverting the best-effort load fails the
  test with "a damaged workspace file must not stop the app connecting".

## 0.5.8 — 2026-09-19 · first batch from a full audit

A full audit of every component (hub backend, the three workers, the native shell, the kit and the
tests), with each finding verified against the source by a second pass before it was acted on.

- **The automatic fleet re-join never ran.** `StudioHub.start` called `rejoinControllerIfNeeded()`,
  which guards on `controllerIdentity?.settings.fleetCredentialOrigin`, and nothing on the startup
  path had called `refreshConnection()` — so the guard saw nil and returned on every Mac. The test
  passed only because it refreshed the identity by hand first. The launch step is now one function
  (`adoptStartupMembership`) that both the app and the test call, and the test fails without the
  identity fetch (verified by mutation).
- **A removed shared voice could never be re-added.** `upsert` recreated the voice (201) but the
  tombstone survived, and `start_sync` refuses any voice with a tombstone, so `reconcile_once`
  deleted it from the targets again. `upsert` now clears the tombstone for the id it is re-creating.
- **A metadata-only push cleared the transcript and its timings.** The route defaulted absent
  `transcript`/`transcript_segments` to `""`, and `upsert` wrote a full replacement, so a rename
  wiped the words and timings everywhere. Absent is now `None` = keep; `""` still clears.
- **`install_app.command` deleted the installed app before copying the new one**, so a cancelled
  password prompt or a full disk left the Mac with nothing. It copies to a staging folder in the
  target, checks the runtime there, and only then swaps.
- **`packaging/subtitle_worker.py` never set `STUDIOHUB_TOOLS_DIR`**, so `find_executable('ffprobe')`
  searched `Resources/voice/tools`, which the bundle does not contain; the media-duration guard was
  silently off for m4a/aac/webm. The voice worker set it all along.
- **`qualification/studiohub-start.sh` passed `--listen-host`** to a worker that defines `--host`, so
  the runbook's "see the real error" step printed an argparse error instead.
- **`WireEncodingTests` could not fail**: it searched the app's raw source plus quoted strings, and an
  app *comment* naming the Hub's key satisfied it (the Hub's own key is written in backticks, so the
  regex found nothing). A hand-written `CodingKeys` mapping to a wrong key - the exact mistake the
  file exists to prevent - left all three tests green. It now strips comments, forbids the literal in
  the app, and forbids `CodingKeys`; verified by mutation.

Still open from the audit, with the owner for decisions or queued next: subtitle retention (nothing
ever deletes an upload or an artifact, and the packaged Hub's cleanup loop is off), .mp4/.mov
accepted by Subtitle and then refused by the decoder, an uncertain Subtitle item that holds its Mac
out of every lane forever, finished broker batches never evicted, the output-storage sweep hashing on
the event loop, worker liveness missing from readiness, and the app's refresh aborting everything on
one failed sub-request.

## 0.5.7 — 2026-09-19 · a refusal with no reason

- **The gates were sent and discarded.** `/api/hub/model-packages` returns, per model,
  `files_state`, `runtime_state`, `os_state`, `chip_state`, `worker_state`, `minimum_macos`,
  `macos_version`, `dispatch_state` and a `reason` when there is one. `HubModelPackage` decoded
  `package_id`, `model_id`, `state` and `dispatch_state` - so the app could tell that a model was
  unavailable and never which of the five independent gates was shut. The owner reported it as "just
  shows unavailable with no proper reason" and guessed at RAM pressure, which is exactly what a UI
  forces an operator to do when it withholds the reason it already has.
- `HubModelPackage` now decodes every gate, and one `blockReason` turns the first shut gate into a
  sentence: files not verified, runtime unavailable in this build, macOS floor (naming both
  versions), Apple Silicon required, studio not running, or the memory tier when the catalog knows it
  (`memory_warning` / `memory_eligible == false`, checked first because it is the one gate an
  operator cannot fix by clicking).
- Readiness is no longer computed by hand in three places with three different rules: the audio,
  subtitle and image mappers all ask the same function, so a model cannot be "ready" in the picker
  and refused by the Hub.
- It is shown in the generator's model list (a `— not available here` marker), under the selected
  model, in the Generate button's explanation, and on the Models page. Evidence: the native fixture
  now serves a package gated on macOS and asserts the sentence names both versions, that the picker
  marks it, and that a healthy model carries no reason; `tests/test_model_reasons.py` (6 checks)
  covers the decode, every gate's sentence, the precedence of the memory reason, and the three places
  it is shown.

## 0.5.6 — 2026-09-19 · naming a Mac, and the restart nobody knew about

- **The name was never editable.** `registry.set_label` has written `machine_labels.json` since the
  beginning and `machine_name` - what every other Mac and the gateway read - comes from it, but no
  route and no control exposed it, so the only name an operator could have was the hostname. New:
  `POST /api/hub/machine-label` (Hub token only; a fleet credential is refused, checked against a
  live Hub), and a **Name** field with a Rename button in Connections. Empty clears back to the
  hostname. It is a label, never an identity: routing and fleet membership do not read it.
- **The restart was implied, not stated.** Role, network addresses and port are read when the Hub
  starts, so their controls are disabled while it runs - leaving a new operator to discover the
  stop/change/start dance. `StudioController.restartServices()` now stops and starts again in one
  press (`restartAfterStop`, honoured in `workerExited`), a notice appears above those controls while
  a change is waiting, and `hasPendingRestartSetting` compares the port the Hub actually bound with
  the saved one so the notice tells the truth rather than remembering an edit. The role picker also
  gained a line saying what Controller / Node / Standalone mean.
- Evidence: `tests/test_machine_name_and_restart.py` (6 checks) - including a live Hub rename that is
  read back through `/api/hub/controller`, a cleared name falling back to the hostname, and a fleet
  credential being refused. Both halves were rendered before shipping with the new
  `STUDIOHUB_RENDER_RESTART=1` mode, which shows the notice that only appears while services run.

## 0.5.5 — 2026-09-19 · how long a generation took, which the app never said

- **The number existed and was not read.** The Hub writes `runtime_s` on an item when it finishes
  and `run_started` when it starts, and `terminal_result` already carried the runtime - but
  `HubJobItem` parsed neither, and the rows showed only `audio_duration_s`, the length of the media
  produced. The owner read "4.6 s · 219 KB" as the app refusing to state a generation time, which is
  exactly what it was doing.
- `HubJobItem` now decodes `runtime_s` and `elapsed_s`, `NativeJob` carries `generationSeconds` /
  `runningSeconds`, and one `generationSuffix` renders as "· took 52.2 s" / "· running 12 s" (in
  minutes past a minute, and nothing at all when the Hub has not timed it). It is appended to the
  state line of all three job row styles, so the generator pages and the Jobs tab show the same
  thing by construction rather than by two edits.
- The Hub's item payload gained `run_started` and `elapsed_s`, because `runtime_s` is only written on
  completion: a generation that was still running had no duration in the payload at all.
- Evidence: `tests/test_generation_time.py` (4 checks: the payload carries both numbers, the app
  parses both, all three rows render it, and the clip length is not folded into the generation
  time), plus `tests/native/api_main.swift` for the formatting ("52.2 s", "2 min 5 s", nothing when
  untimed, nothing for a zero). Verified against the owner's own history by rendering the Audio page
  and the Jobs tab from the running Hub: five real clones at 62.0 / 79.7 / 43.0 / 52.2 / 45.4 s, a
  failed job at 2.3 s, and an image at 1 min 28 s.

## 0.5.4 — 2026-09-19 · the Generation column was clipping its own controls

- **The layout, not the logic.** `audioWorkspace` put a `TextEditor` with only a `minHeight` beside
  a fixed-width column. An unbounded editor takes every pixel the split will give, so the
  Generation column was squeezed to its floor of 280 pt while its contents - a segmented control
  with three long titles, a picker, wrapping captions - needed about 380, and SwiftUI clips content
  that overflows a frame. The result was a panel whose every control was cut off at the right edge
  and could not be clicked: the owner reported it three times, and each earlier fix changed copy
  rather than the layout.
- Fixed: the editor is bounded (`minHeight: 96, idealHeight: 150, maxHeight: 230`); the column asks
  for `minWidth: 330, idealWidth: 370, maxWidth: 460`; the tabs use short titles with the full name
  kept as an accessibility label and in the line below; captions that must wrap say so; the
  window's floor is now the arithmetic the columns need (`980x620`, was `720x520`, which could not
  hold a split page at all).
- **A way to look at the window.** `screencapture` is refused in this environment ("could not create
  image from display"), so three UI fixes were written blind. `native/render-page.sh` compiles the
  real views with its own entry point, hosts the page in an offscreen window and writes PNGs at the
  sizes that matter. It uses `NSHostingView` + `cacheDisplay` rather than `ImageRenderer`, which
  draws a yellow "unsupported" placeholder for AppKit-backed controls. Verified at 1920x1053,
  1100x700 and 980x620 before shipping.
- Second half, seen only after the first was rendered: with the editor bounded, the column became
  shorter than the split, and a split view **centres** a child shorter than itself - so the Script
  box floated a third of the way down the page with empty space above and below it. The column now
  fills the height and aligns `.topLeading`, and the recent-audio list takes the slack instead of
  stopping at a fixed maximum.
- **One house shape for the workspace pages, the Image page's.** The owner asked for the Image
  generator's left-aligned form on every page. Audio and Subtitle were restructured to match it -
  form on the left (script/voice; file/format/Transcribe/recognition), result on the right (recent
  audio; transcript plus recent subtitles) - and Image was brought onto the identical frame so the
  three cannot drift apart. Subtitle's primary action moved off the foot of a flexible column to
  sit with the file and format it acts on, and its "Result format" label moved above its control
  instead of wrapping in half. The Models page's library note was below a flexible split, so it
  appeared at the very bottom of the window; it now sits above the list. "This Mac is a Not
  reporting" became "This Mac is Not reporting".
- `tests/test_audio_layout.py` pins the arithmetic, the bounded editor, the shared column widths
  across all three workspace pages, the primary action/result placement, the short tab labels, the
  wrapping captions and the top alignment, so a revert fails the suite instead of the owner's
  screen. Both halves were found by rendering the page and looking at it - the second one only
  because the first fix was rendered before being handed over.

## 0.5.3 — 2026-09-19 · shared voices reach a node, measured rather than assumed

- **The Hub's shared-voice door exists.** `POST /api/hub/shared-voices` (create or refresh, then fan
  out), `GET /api/hub/shared-voices` (what is stored and how far each voice has reached),
  `GET /api/hub/shared-voices/{id}` and `/audio`, `POST /{id}/sync`, `DELETE /{id}` (tombstone, then
  remove on every node, awaited). `shared_voices` was complete with nothing calling it, and nothing
  started its reconciler either; both are wired now. `?wait=true` runs one fan-out pass before the
  answer, for a caller reporting per location.
- **The id may be the caller's.** A gateway that names a clip by its own content hash
  (`audio_sha256[:12]`) keeps that id, so the same bytes carry one id on all six controllers; the
  bytes decide identity, so a re-push refreshes that voice (200) rather than adding a copy, and an
  id already holding other audio is a 409. Without a `voice_id` the Hub mints one.
- **Timings are required with a transcript.** `PUT /api/voices/{id}/fleet-sync` takes
  `transcript_segments_json` and answers the count it stored, and the fan-out compares that count
  with what it sent: a node that drops them is `failed`, not `synchronized`. The 1.7B Qwen reference
  path passes the stored segments into `reference_audio.prepare`, which is what makes storing them
  anything other than decorative - it had the same gap for a voice prepared locally from a long clip.
- **Two blockers found by pushing a real voice to a real joined node:**
  - `PUT /studio/voice/api/voices/{id}/fleet-sync` was not on the fleet allowlist, so every install
    on a node answered 403 and nothing could ever reach a second Mac. Added narrowly, beside the
    voice `PATCH` it belongs with.
  - The Connections screen offered the **Hub** token for joining while `fleet_nodes` presents the
    **fleet** token to nodes: a Mac that joined correctly would have answered 401 to the controller
    it had just joined. I first "fixed" this by making a controller's fleet credential its Hub token;
    two tests in the suite caught it and it was the wrong answer - it hands full authority over a
    controller to every node it owns. Reverted: the screen offers the Fleet token, the join text and
    the kit say so, and the live fleet tests adopt the fleet token for the same reason.
- Tests: `tests/test_shared_voices.py` (17, unit) and `FleetSharedVoiceTests` (6, two real Hubs) -
  the timings travelling, a re-push not forking, a transcript without timings refused, removal
  tombstoned across controller and node, a node that drops the timings reported failed, and an
  unknown credential refused. 412 tests, 5 skips.

## 0.5.2 — 2026-09-19 · a restart no longer breaks the group

- **Pin the Hub's port.** `packaging/hub_worker.py` takes `--port` and the app never passed one, so
  the Hub bound an ephemeral port and got a different one on every start. That made both directions
  of a join perishable: a node's stored controller address died when the controller restarted, and
  the controller's stored node address died when the node did, with nothing to repair either.
  `ShellConfiguration.port` (default `47873`, which is `peers.DEFAULT_HUB_PORT` - the number the
  fleet code already assumed when it built a peer's studio URL) is now saved and passed as
  `--port`, so an address written on one Mac still names that Mac tomorrow. Pinning it also makes
  the `DEFAULT_HUB_PORT` assumption true rather than about half true.
- **Fail before starting when the port is held.** `ShellConfiguration.requireFreePort` binds the
  wildcard address before anything is spawned. Without it a held port costs three failed starts and
  ends in "Automatic recovery stopped after repeated service failures" - over a number the operator
  never chose.
- **Re-join on start.** A successful join is remembered in the app preferences, and
  `NativeWorkspaceStore.rejoinControllerIfNeeded()` calls the join route again once the Hub is
  ready. That route is already idempotent ("record one node, or refresh what a rejoining node
  reports about itself"), which is what makes an automatic call safe. It runs only for a Mac whose
  credential origin is `adopted`: a controller's own credential is minted and its place is not
  inside another controller. Promotion to controller forgets the controller it used to follow.
  The re-join authenticates with this Mac's own fleet token, which *is* the controller's after
  adoption, so no second copy of a credential is stored anywhere.
- **Connections shows the port and the address to hand a node**, and says when no network address
  is on yet, which is the state that makes a join impossible rather than merely fail.
- **Leave this group.** `POST /api/hub/fleet/leave` mints a fresh local credential, records the
  origin as `minted`, returns the role to `standalone`, and stays outside the fleet credential's
  allowlist so a controller cannot un-join a node. The app asks the controller to drop the Mac from
  its roster first, while the borrowed token still works, then clears the remembered controller.
  This is the action for a Mac being retired, moved or sold - the group's token is a file on it.
- **Fix: the credential origin was never persisted.** `save_settings` writes an explicit key list
  and `fleet_credential_origin` was not on it, so `fleet_adopt`'s write was dropped and the
  in-memory cache was seeded without the key: every reader was told `minted`. Two consequences, both
  silent - the app's automatic re-join (which fires only for an adopted credential) could never run,
  and promotion containment rested solely on the operator having set the role to Node by hand. The
  live two-Hub test for leaving is what exposed it. `tests/test_fleet_credentials_origin.py` now
  asserts that every key in `defaults()` survives a settings write, so the next key added cannot be
  dropped the same way, and that adoption alone forces a rotation on promotion.
- Tests: the native fixture now serves `fleet_credential_origin` and asserts that a join is
  remembered, that an adopted Mac re-joins on start, that a minted one does not, and that becoming
  a controller forgets the old controller; `tests/native/main.swift` pins the pinned port, the
  `--port` override, legacy preferences, the port bounds and the held-port refusal;
  `tests/test_fleet_nodes.py` pins that a re-join with a new address updates the stored address and
  keeps the original `joined_at`.

## 0.5.1 — 2026-09-19 · a protected model library stopped the app from starting

- Move the model library out of `~/Downloads`. A 34 GB library at `~/Downloads/model-library` made
  every launch hang: the app started all three workers, the Voice worker imported its generation
  store, printed `[gen] loaded 4 jobs from history`, and then blocked forever inside `open()`.
  `subprocess.poll()` stayed `None` for the whole 120 s wait, so the Hub never wrote
  `hub-ready.json` and the app reported only "the local services did not become ready within two
  minutes". No process logged anything: `sample` showed the worker asleep in the kernel in
  `_io_FileIO___init__` -> `open`, and `tccd` logged an access request against
  `kTCCServiceSystemPolicyAllFiles` attributed to this app. macOS privacy protection covers
  `~/Downloads`, and there a refusal arrives as a wait rather than an error. Moving the same
  library to `~/StudioHubModels` brought the Hub up with all five workers in about two seconds, and
  an empty folder outside Downloads did too - which ruled out the library's own contents.
- `ShellConfiguration.preparedLibrary` offers `~/StudioHubModels` first and still recognises
  `~/Downloads/model-library` for a Mac the earlier kit filled in. `protectedFolderWarning` names
  the protected folder, so the setup card and Settings can say why before a start is attempted
  rather than after two minutes of waiting.
- `qualification/copy_models_for_this_mac.py` writes to `~/StudioHubModels` and renames a legacy
  `~/Downloads/model-library` tree instead of copying 34 GB again; when both exist it names the
  leftover rather than deleting it.
- The kit's documents, the one-click steps and the release-kit manifest text follow the new path.
- `tests/test_peer_path_contract.py` looked for `Studio Hub 0.4.0.app` alone, so its live router
  probe skipped silently on the version-free bundle name. It now finds `Studio Hub.app` and the new
  library location and actually runs: 375 tests, 5 skips.

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
- Offer the library the one-click setup filled in. On a fresh Mac the setup screen's only
  prominent button was "Use Default Folder", which points at an empty folder inside the app's data
  directory and invites a second, 29 GB download of models already sitting in Downloads. The app
  now recognises `~/Downloads/model-library` by its install record and offers
  **Use the Models Already on This Mac** as one click, and as the first choice.
- Play a saved voice from a file, not from memory. `AVAudioPlayer(data:)` cannot identify an MP3
  handed to it as bytes and fails with `OSStatus error -4` - measured on this Mac against the same
  files `afinfo` reads without complaint. Seven of the ten saved voices are MP3, which is exactly
  the set that would not play. The clip is now written under the app's own directory with the
  extension the worker reports and removed when playback stops. A finished job's audio is played
  the same way, with its container read from its own bytes rather than assumed.
- Offer the saved voices on the Audio page. The Voice library and the worker's `voice_library_id`
  contract both existed; the page only ever offered a clip file, so a voice the operator could see
  in **Voices**, ready to clone, could not be chosen where they generate. The page now has a Voice
  list, and picking a saved voice skips Prepare Voice entirely because the clip, transcript and
  timings are already stored with it.
- Lift `voice_library_id` from the top level of a job envelope in the Hub, as
  `voice_reference_asset_id` already was. Without it the saved voice was dropped on the way to the
  worker, which then refused with a message about a missing reference the operator had never been
  asked for - the same silent-drop shape as the camelCase envelope bug.
- Organise Audio into three voice tabs: cloning, ready-made voices, and voice design. The page was
  one form whose controls changed with the selected model and never said why, so which models did
  which job was only discoverable by trying. Models now carry a `VoiceMode`, each tab lists the
  models for its job and its own controls, and switching tabs keeps the selected model consistent
  with the tab. A model with no name hint and no clone capability is the preset form, since that is
  the only Qwen mode needing nothing but a speaker name.
- Capture and ship the preset-speaker and voice-design Qwen3-TTS tiers
  (`qwen3-tts-12hz-0-6b-customvoice-8bit`, `qwen3-tts-12hz-1-7b-customvoice-8bit`,
  `qwen3-tts-12hz-1-7b-voicedesign-8bit`). The kit shipped only the cloning tiers, so the Audio page
  could offer cloning and nothing else no matter how it was arranged.
- Point the packaged app at the library the setup step fills in. A development checkout had left a
  second `model-library` beside the source, and the installed app was bound to that older copy, so
  the three newly captured voice tiers read as `not_installed` and the Audio page's model list was
  empty. Measured on the running app: the Hub was serving `--models-dir
  .../studiohub-standalone/model-library` with 12 verified packages instead of the 15 in
  `~/Downloads`. The stale Hub was stopped, the duplicate folder moved to the Trash, and the app's
  own `app-preferences.json` points at `~/Downloads/model-library`; the running app now reports
  `15 of 15 ready`.
- Draw subtitle lists with the subtitle row everywhere. The Subtitle page's "Recent subtitles" panel
  was the last place using the audio row, so it offered Play and Save for a transcript while the
  Jobs page offered "Open Transcript" for the same job.
- Make "Open Transcript" load the transcript. It switched pages and left them empty; it now reads
  the finished batch's own SRT artifact and derives the plain text and WebVTT from those cues, so
  the format picker and Export work on a transcript from an earlier sitting.
- Report `governor_note` only while a batch is still waiting. The note explains why a queued job
  could not be dispatched and is cleared on a successful dispatch, but one written on an attempt
  that was later retried into success stayed on the record: all eleven transcripts in the owner's
  history carried "model is not in the packaged catalog" next to a finished result.
- Rebuild a cached model view when the library it was built from moves. Views are made of absolute
  symlinks, so moving the library leaves every link dangling while a marker file still calls the
  view finished - and it is reused forever. Seen in two states on this Mac: a view whose links all
  dangle, and a view directory with no entries. Both are now detected and rebuilt, in
  `cache._merged_snapshot_view()` and in the Subtitle engine's own view builder. This is why
  `whisper-base` and `whisper-small` failed with "Config not found" while `whisper-turbo` - which
  needs no view - kept working. All three verified transcribing after the fix.
- Expose the speaking pace in the app. The engine has applied pitch-preserving tempo to the
  finished audio all along, and the audited contract has carried `speed` (0.5-2.0) since the start of
  the project, but nothing ever sent it: every clip was generated at 1.0. `HubAudioParams.speed` is
  now sent on all four voice paths and a slider sets it. Measured through the real worker, one
  sentence: 12.29 s at 0.5x, 4.88 s at 1.0x, 2.51 s at 2.0x - monotonic and correct in direction,
  and not exactly linear because the model's own generation length varies between runs.
- Keep a finished artifact readable across a Hub restart. Worker ports are assigned at start, but
  the job item stored the absolute worker URL the Hub built at dispatch time - including the port.
  `job_artifact` requires a stored URL to share the worker's *current* origin, which a stored
  absolute URL can never do again, so it answered 502 "worker artifact reference is invalid" for
  files that were on disk the whole time. Measured 2026-09-18: a Voice job completed at
  `127.0.0.1:65461`, the worker came back on `127.0.0.1:58107`, and every Voice and Image artifact
  in the history became unopenable. The job item now stores the worker-relative path, the asset
  record still gets the absolute URL it fetches from, and `ledger.load_batch` normalises an
  absolute URL to its path on read so batches written before this change keep working without a
  migration. Both old artifacts return 200 after the fix.
- Give the app an icon. The bundle had none - no `.icns` and no `CFBundleIconFile` - so macOS drew
  the blank page placeholder everywhere an icon appears. `packaging/make_app_icon.py` draws the mark
  with CoreGraphics and assembles the `.icns` from one 1024px master, so every size is a true
  downscale rather than separately tuned bitmaps. Staging copies it, the plist names it, and staging
  now refuses to build when it is missing.
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
