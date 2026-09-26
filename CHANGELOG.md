# Changelog

## 0.6.77 — 2026-09-26 · The text encoder is a dependency, not a video model

**Found on the installed 0.6.76 Hub, in the live catalog.** The Video worker published a catalog row
for *every* `video-ltx` manifest it read, and the LTX pack's text encoder
(`gemma-3-12b-it-4bit`) is one — so the encoder appeared as a pickable video model on this Mac with
`memory_eligible: false`, and the gateway withheld it with *"this Mac does not have the memory the Hub
says this model needs"*: a true sentence answering a question nobody asked. The Hub's own package row
already marks it `type: dependency`; the row was coming from the worker.

`GenerationManager.packages_without_a_runtime_model` now excludes any package another manifest
`requires`, through the one shared reader (`model_packages.required_packages`), so a companion gets no
model row at all — the same shape the Voice catalog has always had, where a tokenizer is not a model.
`tests/test_video_worker.py` pins both halves: a required companion is not published, and a manifest
nothing requires still is.

**One message improved in the same change.** `validate_request` refused an unpublished duration with
*"Duration must be one of [2, 4] seconds"* — naming the accepted set but not the value that was
refused. It names both now, which is the rule every other refusal in this lane already followed.

**Evidence.** `tests/test_video_worker.py` 21 passed / 19 subtests; full Hub gate green.

## 0.6.76 — 2026-09-26 · The Video lane exists

**There was no video lane at all.** `components/` was chat, hub, image, music, subtitle and voice;
the Hub's broker had mapped a `video` modality to `/api/generate/txt2video` and classified it
`durable_async` since it was written, and the deadline floor was already 7,200 s — but nothing
served it, so the modality was a route to a worker that did not exist. It exists now, and the parts
that were already there are used rather than restated.

### The worker — `components/video/`

One model-agnostic registry, two engines, two memory tiers, and the two doors the broker already
speaks:

- **Text-to-video** (`POST /api/generate/txt2video`, JSON) and **image-to-video**
  (`POST /api/generate/video2video`, multipart `file`) — the exact routes `broker.MODALITY` and the
  broker's `img2video` reference path call. A job is **accepted before the result exists** and
  returns its receipt immediately, which is what `RETRY_CLASS_DURABLE_ASYNC` requires: a five-minute
  job must survive a Hub restart without being blindly replayed.
- **The pack's own loader, before the pipeline is constructed.** `ltx_core_mlx.apply_quantization`
  applies ONE bit width to this mixed 2–8-bit pack and dies on a shape error deep in
  `quantized_matmul`; the pack ships the loader that knows, and the job receipt names it
  (`pack_generate.py:_patch_pipeline_quantization`).
- **Block streaming is never enabled.** It fails on this pack on two keys the streaming block does
  not carry; the field exists with one allowed value so a future edit has to be deliberate.
- **The artifact is verified, not assumed.** The MP4's own box structure is read: `ftyp`, a video
  track with non-zero dimensions, a positive duration, and — for a pack whose measured runs carry
  audio — an audio track. The bytes are hashed and served from the worker's own route.
- **Memory is measured the way the pack measures it.** The receipt carries MLX's Metal high-water
  mark, the process RSS, and **`ri_phys_footprint` sampled at 1 Hz** (`libproc`, the same number the
  Lance card calls `ri_phys`); `mx.get_peak_memory()` alone under-reports the process.

### The tier, which is the load-bearing part

A model the machine cannot load must not be offered, and the Hub's total-memory floor is
deliberately never in force (`memory_admission`'s own doctrine). So the **worker publishes the
verdict per host**: below the model's qualified tier it publishes `memory_eligible: false`, which is
what the gateway's `withhold_reason` already reads. The failure becomes a routing decision instead
of a node-side load error.

| Model | Task | Tier | Evidence |
| --- | --- | --- | --- |
| **LTX-2.3 22B MLX mixed 2–8 bit** (`baa-ai/LTX-2.3-22B-RAM-12GB-MLX` rev `d8a9d2f6081c`) | I2V + T2V, with audio | **24 GB** | measured here `[M]` |
| **Lance 3B Video MLX bf16** (`mlx-community/Lance-3B-Video-bf16` rev `b70ba0a53c14`) | T2V | **16 GB** | vendor claim `[V]`; **runner not adopted** |

The LTX measurement, on this 32 GB M2 Max: **704×448, 97 frames, 24 fps, 4.04 s of output with one
video and one audio track, 304.0 s wall through the worker's own job path** (298.5 s standalone),
peak MLX Metal 14,163,847,570 bytes, peak `ri_phys` footprint 15.71 GB sampled at 1 Hz (the
standalone run's externally-measured process footprint was 16.38 GB). That is why the tier is 24 GB
rather than the vendor's 16 GB: MLX had no memory pressure to trim on a 32 GB host, so 16.38 GB is a
floor, not a worst case. Lance is the small tier's model; its manifest and 16 GB tier ship now, and
the worker publishes its row with `execution_ready: false` naming the `lance-mlx` adoption step —
an absent row would be invisible, and a row that claimed it runs would be a lie.

### The runtime is its own tree, and it is pinned by content

`ltx_pipelines_mlx` needs Python 3.11 while the base runtime is 3.12, so Video carries
`runtimes/video`, built by `packaging/video-runtime/build.sh` from a pinned interpreter plus
`requirements.lock.txt` and the `ltx-2-mlx` checkout at
`3b2e77fd73af5f08961f4436ae979c0579d5deed`. `packaging/stage_voice.py` refuses to stage a tree whose
digest is not `VIDEO_RUNTIME_SHA256`, the same rule the Image runtime learned the expensive way. The
tree is made **read-only** after the build, because a plain interpreter run inside it would write
`__pycache__` back and move the digest the stager pins — a used runtime must not become an
unstageable one.

### What else moved

- **Manifests**: `ltx-2-3-22b-ram-12gb-mlx` (11 files, 19.12 GB, `requires_packages`
  `gemma-3-12b-it-4bit`), the text encoder companion (13 files, 8.07 GB) and `lance-3b-video-bf16`
  (10 files, 15.63 GB), all captured from their pinned revisions. The capture tool gained a
  `&limit=100` on its tree URL — the Hub paginates at 50, so a large repository could silently lose
  its weights — which is why every existing manifest's canonical bytes moved in the same pass.
- **The companion is resolved by installed path**, so an offline node never reaches for the network,
  and the receipt records which path the runner used.
- **Hub registration**: `video` is a routed modality and a real worker (`vocabulary.py`),
  `video-ltx`/`video-lance` are engine routes, both video doors are fleet-reachable, and the broker
  validates a video completion's receipt (`output_url`, `media_type: video/mp4`, a positive frame
  count and clip duration) the way it validates Image's and Music's. A finished video item now
  carries its measured length, frame count and audio claim for the operator.
- **The native app**: Video moved out of "Coming later" into Create. The page lists the lane's own
  published options — aspect ratio, size class and duration are read from the model's `lane_contract`,
  never a ladder in the app — renders the below-tier sentence like every other page, files real clip
  rows in Jobs, and shows the measured clip length. Image-to-video stays Story Studio's surface, which
  is the app that owns scene images.
- The page switch lost its `default: unavailableWorkspace` fallback: with every `StudioPage` case
  wired, the compiler now proves what the fallback only reported at runtime.

**Evidence.** Hub gate **1347 passed, 11 skipped, 1038 subtests, exit 0**; `bash native/build.sh`
green (boundary, API-contract and lifecycle checks); the worker-alone run's full receipt is
`qualification/ltx-video-worker-results-20260926-210721/measurement.json`.

## 0.6.75 — 2026-09-26 · The installer can no longer leave a broken app, and the disk stops ratcheting

**The install that filled the disk left `/Applications/Studio Hub.app` with no `Info.plist` and no
`MacOS`, and the rollback is what made it unrecoverable.** Measured: the copy stopped after ~1.4 GB of
a 2.9 GB bundle, `rm -rf "$TARGET"` could not clear the wreckage — the image runtime's
`runtimes/image/lib/tcl9` directories are `dr-xr-xr-x`, and unlinking an entry needs write permission
on its *parent* — and the rollback's `mv "$OLD" "$TARGET"` then put the previous app **inside** the
wreckage instead of over it, because the target still existed. `install_local.sh` exited 1 and every
later check was looking at a directory that was not an app.

Three changes, each proved to fire on that defect by `tests/test_install_local.py`:

- **It refuses to start when the copy would fill the disk**, before anything is moved — and before the
  app is quit, because being told there is no room is not a reason to interrupt the app someone is
  using. The check is the stage's size plus half again against the free space it can see.
- **The rollback cannot nest the previous app.** It `chmod -R u+w` first, then verifies the target is
  actually gone before restoring, and if it is not, it stops and says where the previous app is rather
  than reporting a success it cannot verify.
- **Completeness is counted, not sampled.** The old guard checked one file — `runtimes/voice/bin/python3`
  — which a copy that stopped inside `runtimes/` still satisfies, which is exactly how a bundle with no
  `Info.plist` passed it. It now compares entry counts against the stage.

`STUDIOHUB_INSTALL_TARGET` and `STUDIOHUB_INSTALL_DATA` exist so those two guards can be tested without
touching `/Applications` or the operator's real readiness markers; production passes neither.

**And the reason the disk filled: `.build/`, not `_trash/`.** Every release staged a ~2.9 GB bundle and
built a ~1.3 GB DMG, and the previous DMG moved into `_trash/dist/` — on the same volume, so nothing was
ever reclaimed. The owner cleared `_trash/` by hand and recovered 38 GB; the audit then measured
**`.build/` at 100.9 GB, of which 32 staged bundles held 70.7 GB**. `packaging/prune-build-scratch.sh`
is the step that stops it recurring: it keeps the stage of the release currently in `VERSION` (read
from each stage's own `release-manifest.json`, never from a timestamp — `chmod` updates a directory's
mtime, and a "newest by mtime" pass proposed moving the 0.6.74 stage), keeps the newest of the rest,
moves the remainder to a kit drive, verifies each copy by sha256 manifest on both sides, and only then
removes the local one. It refuses to act at all when no drive has room.

## 0.6.74 — 2026-09-26 · The Voice worker's loops start with the app, not with the import

**Both of the Voice worker's background loops — idle model release and output retention — were
started by `import backend.main`.** Every process that imported that module got them, a test run
included, and two real things followed. Neither was a test-only problem.

- **The idle-release ticker reached into a test's own fakes.** `test_memory_policy.py::test_memory_api_frontend_and_process_title`
  failed with `last_release_details.released is False`. It had been recorded as flake — *"it is a
  timeout, not a failure"* — and it was not a timeout. The 5-second ticker released the managers the
  test had just injected, through the test's own settings file, so the test's own
  `POST /api/memory/release` then reported *correctly* that nothing was loaded. **Measured directly,
  with the test doing nothing at all:** `gen.releases` 0 → 1 after seven seconds, and the same POST
  then answered `released: False`. Every assertion in that file that counts releases was racing the
  ticker, which is why the gate was green most of the time and red sometimes.
- **The retention loop enforced the operator's policy against the real output directory**, 60 seconds
  after import and hourly after that. A test session could delete real generated audio in
  `~/Library/Application Support/StudioHub Standalone/voice/output`. The packaged worker is protected
  from its own loop by `STUDIOHUB_OUTPUT_POLICY_OWNER=hub`, which `voice_worker.py` sets — a test run
  is not, and that variable is not what a reader would check.

**Both loops are now registered on the app's `startup`.** This is the pattern the sibling Music and
Subtitle workers already use, whose comment says it outright: *"The packaged worker calls this once; a
test that builds a second app gets its own clock instead of the first one's."* Voice was the outlier.

**Production still starts them, and that was checked rather than assumed.** `voice_worker.run_server`
builds `uvicorn.Config(app, …)` with no `lifespan` argument, so uvicorn's `auto` mode runs startup
handlers, and the installed worker's own log carries `Application startup complete.` — a line only the
lifespan path writes. `main.py` already registered `_reconcile_auto_update_scheduler` there.

**`tests/test_background_loops_start_with_the_app.py` pins both halves**, because "starts on import"
and "starts at all" are different failures: a fresh interpreter must import the module and find
neither thread, and the startup handler must be registered and wired to the worker's real managers and
output directory. It asserts the wiring without starting the real threads — starting them would leave
a live ticker running for the rest of the suite and make `test_memory_policy.py` racy again, which is
the defect the file exists to prevent.

## 0.6.73 — 2026-09-26 · Chat gets its margin back, and the video runner stops wasting runs

**Chat was the only one of the five workspace pages without an outer margin.** The owner, looking at
it beside the others: *"the place to enter the text and stuff doesn't have a proper margin."* Exactly
so — Image, Audio, Subtitle and Music each wrap their split view in `.padding(28)`; Chat never did, so
its form column met the window's top and left edges while the model picker below it sat correctly
inset. The form column's own `.padding(.trailing, 20)` cannot supply it: that insets the content
*inside* the column, not the column from the window. **`test_audio_layout.py` now pins the outer
margin on all five pages, and it is the assertion that was missing** — every other check in that test
passed while this was wrong.

**Two defects in the LTX video runner, both of which cost a run each.**

- **It preferred a partial copy of the model over a complete one.** `~/StudioHubModels` came before
  the drives in the search order, so it picked a **10 GB unfinished rsync** over the complete
  **17.81 GiB** copy on NOVA. The failure reads as a corrupt model — `[load_safetensors] … invalid
  data offsets … Perhaps an incomplete download or corrupt file?` — when the truth is that the copy
  is unfinished. The drives now come first, and a copy under 90% of the package's known size says so
  **before** the run rather than 23 seconds into it.
- **Block streaming is now opt-in, because it does not work with this pack.** The research had called
  `low_ram_streaming` *"the cheapest route to a real 16 GB lane"*, so the runner passed it always. On
  the baa-ai pack it fails before generating: `ValueError: Received 2 parameters not in model:
  video_to_audio_attn.to_gate_logits.{biases,scales}` — the streaming block does not carry those keys
  and the pack never enables streaming itself. **That hope is now measured rather than assumed.**

**And the measurement itself, which is what the owner asked for** — one image-to-video generation on
this 32 GB M2 Max, through the runner, so the process footprint is captured as well as MLX's own
counter:

| | |
|---|---|
| Wall | **298.5 s** for 704×448, 97 frames (4.0 s of video, with audio) |
| Peak MLX Metal | **13.19 GB** |
| Peak process footprint | **16.38 GB** |
| Maximum resident set | **9.91 GB** |
| Swap at end | 3,852 MB of 5,120 MB |

**The 16 GB claim is not supported by this, and the honest reading is that it is in trouble.** A
16.38 GB peak on a 32 GB machine — where MLX has no pressure to trim — is more than a 16 GB Mac has
in total, before macOS takes its share. That is one run on one machine and it does not *prove* the
16 GB tier fails; it does mean the pack's "12 GB / 16 GB" reach is unproven and the margin is
negative, and that the next run that matters is on the 16 GB hardware rather than another one here.

## 0.6.72 — 2026-09-26 · The limit's provenance is a token, not a sentence

**`model_limits` said where each number came from in a sentence, and a sentence is not something a
consumer can branch on** — especially this consumer, which is two hops away with a gateway in
between that must *relay* rather than interpret. Without a token the gateway had to answer for
itself, and it answered `provider` for every stated value. The live row read:

```
max_output_tokens: 16384
max_output_tokens_source:        "provider"
max_output_tokens_source_detail: "worker default: the checkpoint ships no generation_config.json"
```

**The two fields contradicted each other, and the machine-readable one was wrong.** 16,384 is this
Hub's own `FALLBACK_MAX_OUTPUT_TOKENS` — the number a checkpoint that states nothing is given — and
Story Studio reads the token. Their row says what it cost: *"16,384 currently renders as if the model
stated it."*

**Each limit now carries `source_kind` beside its sentence**, and both are set in the same branch, so
they are one decision and cannot drift apart: `model` (the checkpoint's own file states it) ·
`curated` (this Hub's packaged metadata, not the checkpoint's word) · `worker_default` (this worker's
fallback) · `unknown` (nothing states it, and `value` is `null`). The vocabulary is the consumer's,
because the consumer is the one doing the labelling.

The sentence stays. A token is what a program branches on; the sentence is what a person reads, and
losing either is a different defect from the one this fixes.

**GatewayKH relays the token** rather than matching a word in the sentence — which is what they asked
for, and three of our defects came from matching a name where a fact could have been published. Their
half shipped as **0.2.48**.

**Verified:** the cross-repo half is pinned on their side by `tests/test_hub_limit_provenance.py`
(9 tests, proved to fail 9 of 9 on the defect); here, `tests/test_chat_worker.py` now pins
`source_kind` on both the checkpoint-stated and the worker-default case — the second being the number
that used to leave this Hub mislabelled.

## 0.6.71 — 2026-09-26 · Silence became visible to the operator too

**A stall the Hub could see, nobody watching the app could.** 0.6.70 made a quiet worker *visible* —
`stall.py` notices silence in about two minutes whatever the deadline is, which is the whole reason a
30-minute ceiling is safe rather than merely long — and the Hub has published `stalled_seconds` on
every job row and every fleet machine row since. **The app read none of it.** `HubJobSummary` decoded
two fields, `id` and `modality`, so the number arrived on the wire and was dropped on the floor: a
scheduler could see a stalled job and the person watching the Jobs page could not. That is the SH-3
shape exactly — *a two-ended invariant does not see a hole in the middle* — where both ends are true
and the sentence cannot travel between them.

**All three links are built, and one test pins all three**, because any one of them alone looks like it
works. The **producer**: `broker.batch_summary` states it, and *both* job routes serve it —
`/api/hub/jobs` lists summaries and `/api/hub/jobs/{id}` is `_summary(batch)`, that same function plus
`items`. The **wire**: `HubJobSummary`, `HubJobDetail` and `NativeJob` name `stalledSeconds`, so
`convertFromSnakeCase` actually maps it, and `mapHubJob` passes it through. The **screen**: the Jobs
row draws *"No progress for 4 min 12 s — still held, not cancelled"* under the progress bar.

**It is advice, and the wording is pinned to the rule.** `stall.py` is explicit — *"What it must never
do is kill the job."* A merely-quiet job is one a long deadline exists to protect, so the row says
what is happening and promises nothing about the job ending.
`tests/test_stall_visibility.py` fails if the sentence stops saying the job is still held, and fails
if `stall.py`'s own contract is reworded out from under it.

**Verified:** `pytest tests/` **1344 passed, 11 skipped, 1000 subtests, exit 0** (was 1336/998 — this
file's 8 tests and 2 subtests), and `bash native/build.sh` green with the boundary, API contract and
lifecycle checks all passing. **The guard was proved to fail on the defect:** restoring the original
two-field `HubJobSummary` fails it with *"the Hub publishes it and the app drops it, which is the
defect this file exists for"*.

## 0.6.70 — 2026-09-26 · One deadline became four, and silence became visible

**The Hub's job deadline is no longer one number.** It was a single flat 900 s on every lane. The
owner asked the question that ended that: *"if we raise it too high for, say subtitle and it failed,
could we find out or we have to wait 2 hours to findout?"* A shared ceiling is either too short for
the longest job or so long that a broken short one goes unnoticed. `deadline_seconds` is now derived
per **generator** by one function, `standalone.deadline_floor_seconds(modality)`, which feeds **both**
the value published in every lane contract and the timeout the proxy enforces — so what a scheduler
reads is still what this Hub holds the job to. Video **7,200 s**, chat **1,800 s**, image, voice,
transcription and music **900 s**, and a modality nobody classified gets the 900 s floor because a
short deadline can only cut a job short.

**Chat's output fallback moved with it, because the two are one ceiling.** GatewayKH measured the
pair: 16,384 tokens at 1,800 s is 76/65/51 % on M1/M2/M4 — the same margin 8,192 at 900 s gives
today — while 16,384 at 900 s times out on every chip including the M4. So
`FALLBACK_MAX_OUTPUT_TOKENS` went **4,096 → 16,384** and the chat floor went **900 → 1,800** in the
same build, with the arithmetic recorded beside both. **A model that states its own smaller ceiling
still binds** — a fallback can never raise a lane above the window its own model publishes.

**Video was classified for a lane that does not exist.** It sat at `NON_DURABLE`, reasoned from
*"Video and Render answer a terminal result for the POST itself"* — which describes the **legacy**
Video Studio's synchronous POST, not a lane whose generations run 15 to 45 minutes. Under that class
a node dying at minute 40 of a 45-minute job lost the job outright: no `uncertain` state, no retained
attempt, no re-dispatch, while the identical failure in the Image lane was retained and requeued.
Video is now **`DURABLE_ASYNC`**, which is a requirement on the worker as much as a label.

**A stall is now visible instead of merely long.** `components/hub/backend/stall.py` decides whether a
worker that has gone quiet while holding a job is stalled, from the `activity_received_at` receipt
the monitor already collects — and it is wired into every job row the Hub builds as
`stalled_seconds`. It fires only for active states, never for a job that has not started, never for
a terminal one, and never for a studio with no receipt at all. **It does not kill the job**: killing a
merely-quiet job would lose work the long deadline exists to protect. This is the piece that makes a
30-minute ceiling safe rather than just long.

**Z-Image-Turbo 6B MLX 4-bit is qualified on 8 GB.** The owner ran it on an **Apple M1 Mac mini
(`Macmini9,1`), 8.0 GB, macOS 26.6.2** with **mflux 0.20.0**: low-RAM completed 1024x1024 in 392 s
and 1280x720 in 336 s, 5.70 GB peak MLX and 5.85 GB footprint. The manifest's floor moved **16 → 8**
and its status **unqualified → qualified** — through `capture_model_manifests.py`, its producer,
not by hand. **The engine default also completed on that Mac, and that must not be read as licence to
drop the low-RAM rule:** it finished with an **8.72 GB footprint on an 8.0 GB machine** and 3,443 MB
of peak swap, 2.7x the low-RAM runs. It fit by paging, not by fitting, so
`Z_IMAGE_LOW_RAM_BELOW_BYTES` stays at 24 GB. Note the cost that remains: even low-RAM swaps
1.3-1.9 GB at this tier, at 5.6-6.5 minutes an image.

**Voice's deadline was raised to 1,800 s, because voice is the lane that actually failed.** Story Studio reported a **~17-minute narration part killed at the 900 s wall** — the concrete failure the flat constant caused. 1,800 rather than 1,200: 1,200 is three minutes of headroom over the part that died, and that part ran on one Mac, so the same narration on a slower node among the fleet's 35 eight-GB and 15 sixteen-GB machines would hit the identical wall at eighteen minutes. 1,800 is 1.76x the observed worst case and matches chat, so the two long-form lanes share one number. **Seventeen minutes is an observation of a failure, not a clean measurement, and 1.76x is inference** — the end state is to measure a real voice part and derive it, as Z-Image's 8 GB floor was derived from the owner's run.

**The fleet was wrong by a factor of seven.** It was recorded as 7 machines. The owner's own count is
**~53**: 35 x 8 GB, 15 x 16 GB, 2 x 24 GB, 1 x 32 GB — so **50 of ~53 are 16 GB or below**, which
inverts which tier a recommendation must be sized against. `AGENTS.md` also still called the 32 GB
machine an M1 Max; `research/owner-hardware.md` had corrected that to an **M2 Max Mac Studio
(`Mac14,13`)** on 2026-09-24, but the instructions file had not, and the fleet table guard reads the
research file rather than the instructions file, which is why it survived.

**Fixed on the way, all the same shape — a test or a document pinning a number.** One test asserted
`deadline_seconds == 900.0` literally; it now asserts the join, so it still fails if the Hub stops
publishing its own constant and no longer fails when the constant is deliberately changed. Eight chat
tests pinned `4096` or were hand-tuned to sit just above it — one carried the comment *"8192, not
4096: the request below asks for the worker's default output budget"* — and were restored to what
their fixtures were written to check rather than re-pinned around the new value. `docs/tasks/ST-20.md`
restated "the 900 s" against a line that no longer existed.

## 0.6.69 — 2026-09-26 · Voice's memory verdict never worked, and now it is shown

**SH-3, and it was worse than "no warning".** Voice was the one Studio that published no
below-tier sentence, so the Audio page had nothing to draw. Fixing that exposed the reason it could
not have drawn one anyway: `components/voice/app/backend/system_info.py` called a **bare `sysctl`**,
and `sysctl` lives in `/usr/sbin` — which is not on PATH for a packaged worker launched by the Hub
or from Finder, and is not on PATH in this project's own development shell either.
`_sysctl` swallowed the failure and returned None, so **`detect_memory_gb()` and `detect_chip()`
were both None and every model's `fit` verdict has read "unknown", fleet-wide, for as long as that
has been true.** The probe is looked up by absolute path now, and reports this Mac's 32 GB and its
M2 Max.

This is the defect the chat tier harness carries a warning about in its own header: a bare `sysctl`
that failed silently once made a swap sampler report a peak of 0 MB, which looked like a reading.

**The warning, wired end to end.** `catalog.serialize_model` publishes `memory_warning` under the
same key and in the same place as the other four Studios, derived from the `fit` verdict this worker
already computes — `risky` *is* "this host is below the model's floor", and its `hint` already names
both numbers, so there is no second threshold to disagree with the chip beside it. The native audio
model carries it, and the Audio page draws it with the same triangle the Image, Music, Subtitle and
Chat pages use. **Advice, never a refusal**: the model stays offered and `blockReason` remains the
only field that stops a job.

**Guarded from both ends.** `tests/test_below_tier_warning.py` now requires **five** publishing
workers and a render site for each — the expectation it was written to enforce, finally including
Voice. `components/voice/app/tests/test_system_info.py` pins the probe: it must read this machine,
it must work with `PATH` set to `/nonexistent`, and a host below the floor must be told with both
numbers while a host at the floor is not warned at all. Proved: reverting the probe to the bare
name fails two of the three.

**Also in this release.** SH-17's two consumers: the web picker now discovers every ready-made
roster by the *shape* of its key (`<family>_preset_speakers`) instead of naming one family's, and
the native app no longer decides whether a transcript is required from `family == "qwen3-tts"` —
the catalogue states that itself, and every shipped cloning model was checked before it was removed.

## 0.6.68 — 2026-09-26 · five releases shipped the wrong image engine

**The Image runtime regressed in 0.6.63 and nobody noticed for five releases.** 0.6.59–0.6.62
shipped **mflux 0.20.0**; 0.6.63–0.6.67 shipped **mflux 0.17.5**. Every Image number this product
publishes — Z-Image-Turbo 6B MLX 4-bit's 166 s and 236 s, its peak figures, its
`measured_on: "mflux 0.20.0"` — described an engine the app did not contain.

**How it happened.** The release loop in `docs/tasks/HANDOFF.md` named
`--image-runtime .build/st03-image-runtime-worker`. That directory is the **superseded 0.17.5** tree,
preserved as the receipt for the ST-03 packed-app proof — and it was correct when the line was
written, before mflux 0.20.0 existed as a separate built runtime. The 0.20.0 tree has been sitting at
`.build/image-runtime-mflux020` ever since, and every release since 0.6.63 staged the wrong one by
following a stale instruction faithfully. **That is the failure mode this project keeps recording: a
document that was true when written and was never re-checked against the artefact.**

**How it was found.** The owner ran the 8 GB Z-Image measurement on his 8 GB Mac. The harness checks
the engine version before it measures, refused, and named the mismatch — five releases after the
mistake. Its own advice (*"install the kit's app, it carries mflux 0.20.0"*) was **also false**: the
kit carried 0.17.5 too, so following it would have reinstalled and been refused again.

**The fix is the runtime, not the record.** `.build/image-runtime-mflux020` is byte-for-byte the tree
0.6.62 shipped — verified by content digest, `c3acf766…`, which is the same digest the 0.6.59 handoff
records as replacing 0.17.5's. Staging it restores the engine Z-Image-Turbo was qualified on, so its
published numbers describe the app again.

**And it cannot recur.** `packaging/stage_voice.py` now pins the qualified Image runtime by content
digest (`IMAGE_RUNTIME_SHA256`) and **refuses to stage anything else**, naming both digests, where
the handed tree came from, and the path to use. A version string would not have caught this: both
trees contain an `mflux` package and accept the same commands. This is the same mechanism that
already pins the Music overlay.

**Also in this release.** `RUN-8GB-Z-IMAGE.command` — the 8 GB Z-Image runner had no double-clickable
wrapper, which is the same omission that was fixed for the chat tier runner and not for this one; the
wrapper checks the installed app's mflux version and says so *before* the run refuses. The harness's
false advice is replaced with a command the operator can run to see what their app carries. And
`prepare-kit.sh` now treats the kit's `Technical/` folder as a **managed set**: it writes its nine
files and moves anything else to the volume's `_trash`. The owner found 29 files there, including
three exact duplicates (`8gb-z-image-run.sh` beside `eight-gb-z-image-run.sh`, and
`check-everything.sh` beside `verify-launch.sh`) — leftovers from earlier kit layouts that nothing
wrote and nothing removed.

## 0.6.67 — 2026-09-26 · three published things that were not true

Three open items, each the same shape: something the product published or relied on with nothing
behind it. Each fix is small; each guard was proved by breaking the thing it guards.

**FLUX.2 Klein 4B MLX 4-bit published `79.34 s` for a scene it described exactly — and that number
was in no file in this repository.** The stored run of that same scene — same model, same pinned
revision, 1280x720, 4 steps, guidance 1.0, seed 42 — is **105.943 s**, recorded in
`qualification/st03-image-real-inference.md` and again as `worker_terminal.duration_seconds` in
`qualification/evidence/st03-image-real-inference.jsonl` (job `af4913b2`, 2026-09-15). The host was
wrong too, and the correction is better news: the row claimed a 32 GiB M2 Max, and the record says
**"Captured on Apple M4, 16 GiB RAM, macOS 26.6.2"** — a measurement on the tier this fleet actually
runs. `qualification.packaged_inference` also said `pending` while that completed packaged run sat in
the tree; it now names the run.

`tests/test_image_loading_mode.py::test_every_published_runtime_is_found_in_the_record_it_names`
opens the file each row names and fails if the figure is not in it. Proved: with the row set back to
`79.34`, it fails naming the file; with `105.94`, it passes. **A scene description is not a source.**

**`max_output_duration_s` was enforced by the worker and published by no family.** The shape, the
API field and its validator were all real, and the dispatcher genuinely turned it into Qwen's
audio-token ceiling — but no family named it, so **no lane contract offered a bound that was being
applied**, and a scheduler reading contracts could not discover it. The `qwen3-tts` family declares it
now, and it is the only family that honours it: every other family accepts the field and ignores it,
and a lane must not advertise a key it throws away.
`test_every_parameter_shape_reaches_a_published_contract` is a set difference rather than a list of
names, so nobody has to remember it when the next shape is added. Proved: removing the declaration
fails it, naming exactly `max_output_duration_s`.

**Voice's "the lane decides" exception now has a boundary, in tests.** `_job_parameters` leaves a
request field alone when the caller sent nothing *and* the value in force is the lane's own stated
default — the engine resolves that default itself, and writing it in would record an override nobody
chose. That reasoning holds only while the lane states the value, and the rule lived in a comment.
It is the **only** such exception in the fleet: Image and Music fill an omitted field in
`validate_request`, Subtitle in `with_model_defaults`, Chat in `_resolved_parameters`, all
unconditionally. `TestLaneDecidesBoundary` pins all four cases. Proved: widening the condition to
`if params[name] is None` — which would silently kill every optional parameter's stored default —
fails six tests.

**A note on the guard that did not run.** The first version of that test class was named
`LaneDecidesBoundaryTests`, and pytest only collects classes named `Test*`. The suite reported the
same count as before and looked green. A guard the runner does not collect is worse than no guard,
because it reads as covered; the class is renamed and the reason is in a comment beside it.

## 0.6.66 — 2026-09-25 · the tab was filtering the models that could reach the tab

The owner, on the Audio page: *"i can't see a 'preset model' like the khmer tts if i'm on the clone
which is the default, i need to first switch the model to not selecting anything before i can switch
to voice design or ready-made or sth… i'm not sure what the actual bug here is but it's dodgy to
me."* It was a closed loop between two controls, and it hid every preset voice on his machine.

**The loop.** The voice tabs were exactly the **selected model's** modes
(`voiceModeTabs` read `model.voiceModes`), and the model picker listed only the models that
published **the current tab's** mode (`audioModels(for:)`). So the tab decided which models you
could see, and the selected model decided which tabs existed. With a cloning-capable model selected
there was no Ready-Made tab — and therefore no way to see that a preset model existed, including
Khmer TTS, which was installed. The only escape was to pick some other model that happened to
publish preset, which is exactly the fiddling he described. Repairing his selection was not a fix;
the two controls had to stop reading each other.

**The tabs are now the machine's modes.** `audioModes` is the union of what this Mac's Audio models
publish, taken over models that can actually run here — so a tab is never offered with nothing
behind it, installing a preset model brings the tab with it, and a Mac whose models only clone still
shows one tab. Clicking a tab moves the model to one that publishes it, chosen by
`preferredAudioModel` (best that runs here) rather than "first in the list", which could be a model
this Mac cannot generate with.

**The page no longer opens on a model that cannot run.** `reconcileSelections` accepted `supported`,
which is only *"this Hub ships a package for it"* — every TTS entry in the catalogue answers true.
His page opened on `voxcpm2-4bit`, **whose folder is gone while its install receipt still said it
was installed**, so nothing moved the selection and the page sat disabled with an error under the
model. Availability is now read, on a **transition**: a model that was runnable and stops being so
is moved off, and one already known unavailable is left alone — selecting a model in order to
install it must not be undone by the next poll.

**`[Errno 2] No such file or directory: 'voxcpm2-4bit'` was being shown to the operator, twice.**
`preflight()`'s catch-all in `components/model_packages.py` turned every `OSError` into
`state: invalid` with `str(exception)` as the reason, and the Hub republishes a package's `reason`
verbatim — the app draws it under the script and again under the model. Six of his packages carried
it. Two things were wrong: a package with **nothing on disk is `not_installed`**, not `invalid`
(`verify_installed()` in the same file already said so, with a sentence naming the paths it looked
for), and an errno with a relative filename is not something a person can act on. `preflight` now
separates absent from wrong, shares the one sentence `verify_installed` uses, and never publishes a
raw exception string. **A not-installed package also no longer tells you to "press Verify Files"** —
that cannot conjure files that were never downloaded; it says to download it or copy its folder in.

**SH-16 closed: `preset_speaker` is published by the lane that reads it.** It was accepted and read
by the Voice worker and sent by the app, and published by no family's contract, so a scheduler
reading only the contract could not learn how a speaking voice is chosen. The roster moved to one
home (`catalog.PresetRoster`, carried on the family) and the parameter is published for a model that
offers preset **and** has a roster — with the family's own ids as the enum. A single-voice family
(Khmer) and a cloning-only tier publish **nothing**, per the standing rule that a lane must not
advertise a key it discards. No shipped row qualifies today, because the roster's own model was
pruned in ST-13.B; a CustomVoice tier returning to the catalogue publishes it with no code change.

**And the app half of it, which is the same defect this release is about.**
`HubAudioParams.presetSpeaker` was a non-optional `String`, always encoded — `""` for a
single-voice model, otherwise a name **seeded from the roster**, so an untouched picker looked
untouched while the app made the choice. A voice stored for a model in the Models tab could
therefore never reach the engine. It is optional now and `audioSpeakerForRequest` sends only a voice
the operator actually chose from a roster this model publishes; selecting a model **clears** a voice
from another family rather than seeding a new one, and the picker's first row is the not-chosen
state, naming the stored voice where the producer states one and saying only that the model decides
where it does not. Three tests that pinned the old seeding are replaced by that invariant.

## 0.6.65 — 2026-09-25 · the default is a default, and the page control is the override

The owner's ruling, for every generator: *"we have a default but control on the page is
overwrite… we are only setting defaults for stuff we can. stuff that can be change or altered such
as speed must be given to the user."* A value set in **Models** is what a job uses. A control on a
generator page is the operator's own answer and always wins. And a control **nobody has touched
supplies nothing at all** — which is the part that was missing everywhere it mattered.

**Voice's pace.** The Audio page held `audioSpeed: Double = 1.0` and sent it on all four
generation paths. The Voice worker treats a supplied parameter as the caller's own answer — its
own words, and its own test: *"an explicit `word_timestamps=false` is still the caller's answer and
a stored `true` cannot override it"* — so a `speed` default set in the Models tab could never reach
the engine **from the app**. It now sends none until the operator moves the slider. Nil is encoded
away entirely (`encodeIfPresent`), so the request carries no `speed` key and the worker's own
resolution applies: the operator's stored pace over the pace the lane states.

**Nothing about the pace is fixed in the app any more.** `static let audioSpeedRange =
0.5...2.0` is gone. The bounds are the selected model's lane contract (`minimum`/`maximum`) and the
value shown is its own `default`, which the worker publishes **resolved** — it hands
`model_defaults.resolved`'s map to `lane_contract.for_model` before publishing it. The slider
labels follow the lane too, and a chosen value is pulled into this model's published bounds, which
are the same bounds the worker clamps to. A model that publishes no pace gets **no slider**, rather
than one whose movement changes nothing.

**Subtitle's word timings were worse: there was no control at all.** `wordTimestamps: Bool = false`
was persisted and submitted on every transcription request, and no screen in the app rendered it —
so a `word_timestamps` default set in Models was unreachable *and* the operator had no way to reach
the value either. It is now a toggle, offered only where the model's own runtime claims word
timings, showing the value in force, and the form adds the key only once the operator has answered
it. A model that produces no word timings gets no toggle.

**The weight format is the package's own statement.** The model detail line read
`readiness.files.weight_files` and joined the extensions off the file names, which is an
interpretation standing in for a fact: a sharded package read as the format `index, safetensors`,
and a package with no loadable weights had no answer at all. It now shows the row's own
`weight_format` and names that field as its source; the file receipt stays, as its own line, because
which files the runtime found is real data. The Subtitle worker had already published the stated
value on its availability row — the app was reading the receipt instead.

**A parameter the lane discards is no longer sent.** The Audio page sent `language` on every job,
and **12 of the 13 voice families publish no `language` parameter at all** — the lane omits it
precisely because it throws the key away (the Khmer VITS voices are Khmer by construction). The
field is now offered only where the lane publishes one, with the lane's own accepted codes beside
it, and Chatterbox — the one family that publishes it `required` — is refused clearly when the box
is empty rather than filled in with a code this app invented.

**Three tests were pinning the defect, and are replaced by the invariant.** They asserted
`var speed: Double`, that every audio path passed `speed: audioSpeed`, and that the slider used a
`0.5...2.0` literal declared in the app. All three were green for as long as the pace control
outranked the Models tab, and one of them *required* the hardcoded range — so nobody looked. The
rule now has a name and a test class (`GenerationDefaultOwnershipTests`): a generation control the
app owns must be **optional**, because "the operator has not chosen" is a state the request has to
be able to express, and a non-optional page value is a default the app invented.

**Migration.** Every build before this one wrote `audioSpeed: 1.0` whenever the slider had never
been touched, so a saved `1.0` is read as *not chosen* — otherwise the Models tab's pace would stay
dead on every Mac that has ever run the app. A deliberate `1.0` against a model whose default is
something else is the one value that cannot be told apart, and it costs one movement of the slider.

**Recorded, not fixed:** `preset_speaker` is accepted and read by the Voice worker and published by
**no** family's lane contract (SH-16), so a scheduler reading only the contract cannot learn how a
speaking voice is chosen. The app's behaviour is right — it offers only the roster the model itself
published — but publishing it means deciding what a contract-shaped enum for a per-model roster
looks like.

## 0.6.64 — 2026-09-25 · the fleet can reach itself

Every node on the controller was unreachable. GatewayKH measured five of five answering
`ConnectTimeout`, and found the cause by checking the **installed bundle** rather than the
checkout: our 0.6.63 handoff documented this release's fixes as though the running build had
them, thirteen commits apart. So this one is the fixes, and the rule that stops the next
document from doing the same (`AGENTS.md`, "a document names the build that has the behaviour").

**A Mac now answers on its own networks, and keeps doing it.** First-run behaviour was always
right — every private and Tailscale address the Mac has — and it **froze the moment anything was
saved**. A Mac set up before it joined a tailnet stayed on a LAN address from that day forever,
which is exactly what 1101 and 1102 were advertising to their controller: addresses they left
months ago, while their tailnet addresses went unused. An absent `listenHostAutomatic` now means
automatic: recomputed on every launch as this Mac's current private and Tailscale addresses
**plus** whatever was already saved. **Additive — it can never remove a way in that worked.**

**The setter GatewayKH asked us to name exists and persists.** `--listen-auto` turns automatic on
and writes it down; `--listen-host <list>` pins an exact list and now also survives the next
launch (it used to last exactly until then, so a kit could configure a Mac and then watch it come
up on the old addresses with no way to tell "ignored" from "forgotten"); `--fleet-mode` is the one
switch for a Mac that must be ready after a reboot — listen + start at login + **start the Hub at
login**, the third being the one that mattered, since the app already started at login while the
Hub did not. It deliberately does not touch sleep, and says so.

**The bind is a fact, not an inference.** `/api/health` and `/api/hub/settings` publish what this
Hub is actually listening on: the hosts, their classes (`loopback` / `lan` / `tailnet` / `other`),
the port, and the two questions a caller asks — `loopback_only` and `reachable_off_this_mac`. It
is on `/api/health` rather than only on settings because settings is owner-only and a
**controller** is who needs to see that a node came up loopback-only instead of learning it from
a timeout. `known: false` is a different answer from loopback and carries its reason.

**Every Studio has per-model defaults, and the Models tab is rebuilt around what each lane
publishes.** Four of five lanes are here — chat, image, music, voice and subtitle — with the app
reading `lane_contract` for the first time (it read none of it before, which is why every fact the
owner asked for was already on the wire and not on screen):

- **Models is split by Studio** — one collapsible section per function, derived from the enum, so a
  new Studio gets a section with no edit in the app. Search flattens across them.
- **Each section shows that generator's facts with provenance**: the chat context window and
  where it came from, the max output labelled a worker default, TTS's section bounds and hard cap,
  the image size table with the tier it is qualified at, Music's duration ladder and single-pass
  cap, the subtitle weight format. A value nobody states renders nothing.
- **"Your defaults for this model"** is built only from the lane's own settable parameters, in the
  lane's order, with the bounds it enforces. A refusal's per-parameter reasons land under their own
  fields; a stored value the lane now refuses is shown with its reason rather than dropped.
- **The generator pages seed from the resolved values**, which matters because they send every
  field: a stored default the page cannot see is one the page overwrites.

**Two facts that were prose or a join, now published.** The Image lane says `"ignored": true`
rather than relying on a note's wording, and Subtitle publishes `weight_format` on the row the app
reads instead of leaving it to join file names. Both were found by the worker building the screen,
and both are the same shape — a consumer interpreting where a producer could state.

**And `lane_contract.structured_output`**, published by chat: how the JSON guarantee is kept
(`prompt_validate_repair`, `grammar_constrained: false`, one repair attempt), because "json_object
is supported" does not answer the question a strict-JSON caller has.

**And a Mac switched to a node can generate from its own window again.** It could not: one gate
refused *every* local submission in agent mode with a 409, on the sound rule that a node must not
own a **customer's** job — but the owner sitting at his own Mac is not a customer, and the gate
could not tell him apart from a remote controller. The difference is the credential and nothing
else: the Hub token is this Mac's own, the fleet token is someone else's. The fleet refusal is
pinned by the same test, because narrowing a gate is only safe if what it protected still holds.

### What is not here

Two app-side items are recorded rather than fixed: the Audio page's own persisted speed slider
still shadows a `speed` default set in Models (the page sends every field, so the Models value
never reaches the engine from the app), and the app still joins file names for the subtitle weight
format in one place. Both are in `docs/cross-project-open-items.md`.

## 0.6.63 — 2026-09-25 · Auto, and the controls that stopped lying

Four asks from the owner drove this one, and each turned out to be about the same thing: a
number or a control that was published somewhere other than where it was read.

**The Image low-RAM mode is now a control.** *"there is a real lever for image if you want one…
That could be exposed as Auto / Low memory / Full on the Image page."* mflux is built inside every
job and dropped when the job returns, so Image has exactly one *release* policy — that stays the
sentence it was. The new axis is how the model **loads**: `GET/PUT /api/loading-mode` with
`auto` | `low` | `full`, published per model as `loading_modes` on every catalog row and offered
on the Image page. Z-Image-Turbo 6B MLX 4-bit declares a bounded configuration (below 24 GB of
unified memory) and publishes three options whose sentences carry its own measurements: about
7.1 GB peak instead of 17.7 GB, about 236 s instead of 166 s at 1280×720, 9 steps. FLUX.2 Klein 4B
MLX 4-bit declares none **on purpose** — its own registry comment says adding a knob would be an
unverified change to a verified path — so it publishes `supported: false`, no options at all, and
its own sentence saying the machine-wide setting does not change how it loads. A control that
could do nothing is not offered.

It was called `memory-mode` for one afternoon and renamed before it shipped: the shared
`components/memory_modes.py` is the *release* table, a catalog row would have published
`memory_policy` and `memory_modes` side by side meaning different things, and "loading" is what
the setting actually selects. After a release that rename would have been a published contract.

**Every Studio gained `auto`, and it is the default.** All four workers shipped the same four
modes and all four defaulted to `immediate`. That default was measured — 2026-08-07, 16 of 19
fleet machines below the memory guard's 3.2 GB floor with swap burned, because each of 3–5
Studios on one 8 GB Mac independently believed holding its model was free — and it is the wrong
answer on a 16/24/32 GB Mac, where it buys a full model reload per job to free memory nobody was
short of. `auto` is right on both: **at 16 GiB and above it behaves as `balanced` (10 idle
minutes), below it as `immediate`**; a machine whose memory cannot be measured gets `immediate`,
never `balanced`. The rule is the one Subtitle already shipped, moved into
`components/memory_modes.py` so it exists once — every worker imports that table and asserts
identity, and the Hub derives its label-only table from it, so a sixth mode cannot exist in every
Studio and nowhere in the Hub.

The sentence the operator reads now comes from the worker, which is the only party that knows
what `auto` resolved to on that Mac: `options[].detail` says *"Follows this Mac: releases after 10
idle minutes here"*, and `status()` publishes `resolved_mode`, the resolved `idle_seconds` (never
null for auto) and `machine_mode`. A UI that re-derived it from a threshold could only guess.

Two packaging consequences, both proved by staging a real Voice-only bundle: Voice is the one
worker whose bundle did not put `Resources/` on `sys.path`, so `packaging/voice_worker.py`
appends it, and `stage_voice.py` now stages the shared `components/*.py` for **every** bundle
rather than only Hub ones. The Voice web UI also hardcoded its four radios and a "· default"
badge — Auto would have been invisible and unselectable there — and now renders the published
options.

**Chat became work you can see, and work you can measure.** A Chat-page send left no trace:
`GET /api/hub/jobs` is built from the ledger plus the broker's live queue, and the Chat route is a
synchronous proxy call that created no batch, so a Chat tab over that route could only ever have
been empty. `chat_jobs.record_page_completion` now writes one finished one-item batch through
`ledger.save_batch`, in the same shape every other studio's finished work takes, surviving a
restart; the answer is the item's `text` and the item is the artifact, because the worker streams
a completion and keeps no file. The other chat lane (GenStudio packs) has no producer and no
route on this Hub at all, so it is *declared* unlisted with its reason in
`broker.UNLISTED_JOB_LANES` rather than mapped into a second vocabulary — and
`tests/test_hub_jobs.py` now AST-discovers every backend module owning a job queue and fails,
both ways, if it neither reaches `listed_batches()` nor declares why not.

Tokens per second needed two facts and only one existed. `usage.completion_tokens` was on the
wire; the time was not usable, because the Hub kept only `runtime_s`, measured **around the whole
proxied call** — so the model load on a cold turn is inside it (the ST-14 review measured
6.4–8.8 s loads in its packaged probes; our own first packaged reply took 11.98 s end to end).
A rate derived from that would have understated the model's speed on exactly the turn being
measured, and meant something different on the next one. So the worker states its own generation
window on both paths (`studio_hub.elapsed_seconds`, measured in the engine and started where
`complete()` starts its own, after the model is resident), the Hub records it as `generation_s`
**beside** `runtime_s` rather than replacing it, and the rate is derived only where it is
displayed. A worker that states no time gets `null`, never a number derived from the round trip.

**And the Chat below-tier warning was already there.** The review had recorded *"the app ignores
the warning"*; measured, the worker publishes `memory_warning` derived from the model's own
manifest tier on an 8 GB host and nothing on a 32 GB one, and the Chat page draws it with the
same orange triangle Image uses. Nothing refuses, which is what the owner asked for. It is now a
test, and the test carries an invariant: no worker may publish a `memory_warning` that no page
renders. That invariant immediately found two gaps, recorded rather than papered over — Subtitle
computes a warning and publishes it at `readiness.host.memory_warning`, where nothing reads it
(and it cannot fire on this fleet, whose smallest Mac is exactly Subtitle's tier), and Voice
publishes none at all, only `min_free_memory_gb`, which is a *live-free admission floor* and a
different fact from a qualification tier.

**A published duration now states its seconds everywhere.** The run time was fixed in 0.6.62; the
lines beside it were not. `assetFacts` — the "Result details" line under every job row — still
rendered a clip's own length as `%.1f min`, so a 2 min 5 s clip read **"2.1 min" directly above
"2 min 5 s"**, and `NativeVoice.durationLabel` had the same copy, so a saved 90-second reference
clip read "1.5 min". All of them go through `NativeJob.clock` now. The transport label
("0:07 / 0:30") is a playhead, not a duration report, and keeps the shape every other player uses.

**The Subtitle worker now really does reuse the Voice snapshot-view builder.** The import was
`from components.voice.app.backend import cache`, and the bundle stages Voice at
`Resources/voice`, not under `Resources/components` — so it resolved in the checkout and nowhere
a worker runs. It sat inside a bare `except Exception`, so the local fallback kept the catalog
looking healthy while the one-source reuse the module docstring promises had silently stopped
happening in every shipped build. The fallback cannot convert `weights.npz`, which is what pinned
Whisper base and small ship, so those two were catalog-only **for the wrong reason**. Measured
after the fix, inside the bundle layout against the real library: `view_source=voice-cache` for
both, `weights.npz` converted, `runtime_supported: true` for whisper-small, and a real
transcription returns HTTP 200. Which builder produced a package's folder is published as
`readiness.files.view_source` rather than inferred.

**Two dead imports that could only ever raise.** `broker._monitor` and `cloud_guard` both fell
back to `from .main import monitor`, a module that has not existed since the Hub moved to
`standalone.py`; nothing took the path, which is exactly why it survived — `broker` raised
`ModuleNotFoundError` naming a file nobody could find, and `cloud_guard`'s was swallowed by a
bare `except` and looked exactly like a clean "no cloud model matched". `registry` imported a
module that is not in this project and never was. None is a behaviour change on the path anyone
takes; each now says what is wrong in the words of the thing that is wrong.

`packaging/stage_voice.py` gained `staged_import_gaps()`, which is why that class of bug stops
here: `copy_tracked_tree` copies what `git ls-files` lists, so a new module is invisible to
staging until it is added to the index and the bundle builds happily with a worker that dies at
import. Run against the bundle that shipped as 0.6.62 it reports four — these three, and the
Subtitle one — which is the release it would have blocked.

**A cancelled proxied request no longer pins a Mac for six hours.** Cancellation arrives as
`CancelledError`, a `BaseException`, so it slipped past both the transport handler and the
settlement after it, and the machine reservation survived until `_peer_lease_loop` expired it.
What a disconnect does is now declared per route, once: synchronous routes release, asynchronous
submits retain — and a cancel or ack POST no longer takes a machine lease of its own. Both
directions are pinned by tests that fail when the policy is inverted.

**Chat's worker got five fixes of the same shape** — a state the operator could reach but the
worker could not describe. A cancelled stream wedged the engine lock (Starlette never closes a
sync route generator, so `with self._lock` released only when the abandoned generator was
finalised, on whatever thread dropped it, and an `RLock` refuses a release from another thread;
measured ~0.11 s to the next served request after the fix). A prompt that could not fit the
model's stated context was silently truncated and is now refused before the SSE status line is
committed, naming the measured prompt tokens, the budget, the model's own limit and its source. A
boolean field accepted `"false"` as true. Sampling defaults lived as route literals and now live
beside the lane. `max_tokens` is validated against the model's stated limit instead of being
clamped without a word.

**The native Chat page joined the other four.** It ends with the same "managed centrally" note
and a **Manage Chat Models** row, pinned structurally for all five forms — the Manage row is the
last control, and its filter tag equals the Studio whose picker that page uses, parsed from the
enum rather than copied. The operator's own message appears in the transcript **before** the
request leaves, a failure marks that turn instead of inventing a model turn, a send while one is
in flight appends nothing, and `disconnect()` releases the busy flag. The Jobs page gained a
**Chat** tab with a real row (prompt, published model label including the quantization, state,
generation time, the answer, Copy Answer and Save…) and a **Video** placeholder tab that is
listed, files nothing, offers no controls or dates, and says what it will be.

**The kit's copy step now cleans up after a cut, and carries Z-Image on 8 GB.** The owner asked
for both: *"can you make the script prune out old models that's not supported or rejected? like
delete or move it to trash or sth?"* and *"as for the z-image, can you override it too so it can
run on 8gb machines too?"*

The prune's **safety property is the whole design**, because the obvious rule destroys data.
Measured against the real library, "any folder no shipped manifest names" would have moved two
things to `_trash`: `hub/`, which is the **Hugging Face cache** the Voice worker reads, and
`yue2-3b-mlx/`, the owner's Yue2 candidate, kept on purpose. So a managed package is a folder that
is **both** a key in the app's own `.studiohub/installed.json` **and** really a directory, and it is
pruned only when no manifest names it. The folder is tested second, never first — the index is a
historical receipt log (42 keys, 15 folders), so treating it as a picture of the disk would name
folders that are not there. It is a **move** into `<library>/_trash/<package_id>-<stamp>/`, shown
with its size before anything moves and covered by the same confirmation as the copy, including
when a prune is the only thing to do — which the old "nothing to copy" early return would have
skipped. Two corrections to my own numbers, both measured: the receipts this drops are **18**, not
the 27 I first said (9 of the 27 no-folder receipts are still shipped and correctly kept), and a
test for this script already existed (`tests/test_model_copy_script.py`), so the new file is
additional rather than a replacement. Dry run against the real library: **0 packages would move**,
18 receipts would be forgotten, and the four folders the app does not own are untouched.

Z-Image is carried on 8 GB Macs from a **separate table** from FLUX's. `PROVEN_SMALLER` means "the
owner has run it there"; Z-Image's 8 GB run is still unmeasured, so it goes in a new
`CARRY_BELOW_TIER` meaning "he asked to try it there". Writing it into `PROVEN_SMALLER` would have
made that table state something untrue, and `floor_origin()` names the exact table so a wrong skip
stays traceable. Measured: 8 GB machines copy **18 packages / 36.3 GB** instead of 17 / 30.4 GB;
16, 24 and 32 GB are byte-identical to before. The app still publishes Z-Image's below-tier
warning, so the operator is told what he is trading.

### The 166 s question

The image lane contract published **185.61 s** for Z-Image's default configuration. That number
entered the repo in the 0.6.61 registry commit and **nothing in the tree stores it** — no probe
file, no JSONL, nothing. The only record that does exist,
`qualification/z-image-turbo-2026-09-24.md`, says **166 s**, as does the constant's comment,
`README-8gb-z-image.md` ("166–236 s") and the 0.6.60 notes. Four places against one, and the one
had no artefact behind it — which is the thing this project refuses to publish. Both rows now
state 166 s and 236 s and name the document they came from, and the comment's "27 %" (the real
difference between the two configurations is 42 %) is fixed with them. FLUX's `79.34 s` also
traces to nothing; it is recorded as an open item rather than silently kept.

## 0.6.62 — 2026-09-25 · the Clone tab, and the end of guessing a voice's contract

The owner reported that the Audio page's Clone tab showed no voice picker until he went to the
Voices page and clicked "Use voice". Measured cause: the voice library was read by a task keyed on
**which tab was showing**, and changing tabs **cancelled** the read in flight. The store treated a
cancelled read as a failure, so it wiped the list and displayed *"the voice library is unavailable"*
— an error about the engine, when nothing was wrong with the engine.

**Half of that diagnosis was wrong, and the correction is the interesting part.** I assumed the
cancellation surfaced as a `CancellationError`; it does not. A cancelled `URLSession` read arrives
as `URLError -999` (`NSURLErrorCancelled`), and it was being wrapped into a transport error before
the store ever saw it — so a guard on the wrong error type would have fixed nothing. The library is
now read **as part of connecting**, once per connection, so no SwiftUI page task is on the critical
path at all; a cancelled load changes nothing; a real failure keeps the last known list instead of
destroying it; and the picker says which of *reading / empty / unreadable / no transcripts yet* is
true rather than asserting the most alarming one.

**Cloning is now library-only, by the owner's decision.** *"I don't want to just drop in any voice
and starts cloning no. I want to have a button to 'Add Voices' and bring to the voice tab to add
'voices' to the app. That way it can work across all."* The clip chooser, the reference-clip and
transcript blocks, `Transcribe Clip` and `Prepare Voice` are gone from the Audio page; the
saved-voice picker stays, with `Add Voice…` and `Manage Voices` leading to the Voices page, which
is the one place a voice is added or deleted. The `POST /api/generate/txt2speech/reference` route
remains for Gateway/GenStudio, which transports source bytes through it.

**The voice tabs now say what each model can actually do.** Every Voice model publishes which modes
it offers — `cloning`, `preset`, `design` — derived from its own capability row and its family's
facts, never from its name. The Audio page offers exactly those tabs, so a model that cannot design
a voice no longer offers a Design tab. A model that publishes none stays listed with no tabs and
generation blocked, naming what to update; there is deliberately no fallback, because a guessed
mode is the defect.

**The Hub was keeping a second, wrong copy of that answer.** `capabilities._voice_modes` decided
design support by checking whether the model's repo name contained `"voicedesign"` — the same
name-substring shape as the reference-audio bug fixed in 0.6.61 — and defaulted every other TTS
model to `preset_voice`. Measured against the shipped catalog it was **losing the Design mode** for
VoxCPM2 4-bit / 8-bit / bf16 (MLX) and OmniVoice bf16 (MLX bfloat16), and **inventing** a Preset
mode for Pocket TTS 4-bit (MLX 4-bit). It now reads the producer's field through one mapping table
and fails loudly on a mode it does not know, naming both files to update. Verified on a staged
bundle: all **13** voice rows serve their modes and the Hub maps every one.

**A9 corrected: the reference contract is derived, not demanded.** The first fix for that row
replaced a name-substring guess with "a cloning model must have an audited profile", which traded a
wrong result for **no result** — every un-audited cloning model refused on the upload route. The
contract now comes from what the model publishes: its own `sample_rate_hz`, its own transcript rule
from its published guardrails or its family's parameters, marked `profile_audited: false` with a
source for every value, and a bound nothing publishes recorded as *not published* rather than
defaulted. VoxCPM2 4-bit (MLX) resolves **48 kHz** where the old guess was 24 kHz. A refusal now
means a catalogued cloning model published nothing derivable, and it arrives with the missing field
and both files to fix.

**Two of our own published numbers were deleted rather than kept.** The voice lane's runtimes were a
hand-kept table whose values existed only as prose in comments. They now come from stored
measurement records, each row naming the probe it came from — and where no record existed the number
is **gone**: Pocket TTS 4-bit (MLX 4-bit)'s 0.30 s and VoxCPM2 4-bit (MLX 4-bit)'s 61.9 s / 738.4 s
are recorded as withheld with their reasons. Same rule we hold the models to.

**The smoke pilot could never have worked, and running it is how that was found.**
`smoke_voice.py` waited for uvicorn's `Uvicorn running on …` line, which uvicorn does not print when
the worker supplies its own sockets — so it could never see a worker that had in fact started, and
reported a startup failure that was not one. It waits on the worker's ready file now and reaches
`/api/catalog` in about two seconds. The pilot records a shipped model, **Qwen3-TTS-12Hz-0.6B-Base-8bit
(MLX 8-bit)**, with the tracked reference clip and its transcript, and the parameters were proven
through the shipped bundle: `done`, a 230,444-byte 24 kHz WAV, 24.26 s.

**A voice-only bundle can now resolve its own facts.** `_stage_voice` staged no `model-manifests/`
— only the Hub stage did — so a voice-only artifact, which is what the smoke pilot builds, had none:
a clone failed `QWEN_VALIDATOR_MODEL_UNDECLARED` after its reference had prepared fine, because the
clone-quality validator reads the model's `requires_packages` from its manifest, and the ASR leg
could not resolve its Whisper processor. Proved **inert for a shipped bundle** by controlled
experiment on one tree: staging with and without the added line gives a byte-identical
`payload_sha256`, `runtime_sha256` and every component's `source_sha256`.

**The 8 GB Z-Image-Turbo 6B MLX 4-bit run is now one command.** `qualification/eight-gb-z-image-run.sh`
was rewritten so it can be handed to an 8 GB Mac and run unedited, and
`qualification/README-8gb-z-image.md` is the instruction. Seven harness bugs were found by testing
it, each of which would have produced a *wrong answer* rather than no answer — a watchdog that killed
only the timer and left the model running, a stale image from an earlier run that could read as OK,
a refused run that still created its output folder, a silently truncated results file, a hardcoded
step count, a version comparison that warned about a correct engine, and a verdict that could label
an all-OK run "NOT QUALIFIED". A run stopped by the swap or wall cap is `ABORTED` and yields
INCONCLUSIVE, never "not qualified". **No 8 GB figure is claimed** — that measurement is the owner's
to run.

**Verification.** `tests/` — **944 passed, 11 skipped**; `tests/hub_regression/cases` — **175
passed**; `components/voice/app/tests` — **724 passed, 19 skipped**; `bash native/build.sh` and
`tests/native/pool-probe.sh` green; and a staged bundle was launched to confirm the 13 voice rows
serve `modes` and the Hub maps them.

**Still open:** the 8 GB tier measurement; `channels` and reference min/max duration are published
nowhere, so an un-audited cloning model enforces no length bound (the 25 MB upload cap still applies);
an unexplained shell anomaly where the Audio page's outer `.task` chain never fires in a hosted
window (the fix no longer depends on it, but a future page should not assume it runs); and
`capture_model_manifests.py --write` still withholds two multi-source packages rather than
overwriting them.

## 0.6.61 — 2026-09-25 · no model needs a code change

The owner's rule, recorded in `AGENTS.md` on 2026-09-24: *"my rule is never hardcoded — everything
must be dynamic for all generators and models, because we always have a new model to add."* This
release is the audit that made it true rather than aspirational, and the work of 73 findings.

**The reported symptom was khmer-tts not appearing, and the cause was broader than it looked.**
`native/StudioAPI.swift` decided voice-model support from a hardcoded family allowlist
(`qwen3-tts`, `voxcpm*`, `omnivoice`), and the Audio picker lists only supported models — so four
**shipped** families were invisible: `khmer-tts-mms` (VITS), `khmer-tts-female-onnx` (ONNX),
`chatterbox-mlx` (4-bit and 8-bit) and `fish-audio-mlx` (8-bit and bf16) — **six of thirteen voice
packages**. The catalog, Hub, manifests, library and all four kits offered them correctly; the app
alone refused to list them. A test asserted the broken behaviour, so it was green by construction.

**Everything in the audit's Tier A (18 rows) and Tier B (40 rows) is fixed**, verified against the
tree rather than against the report — `research/hardcoded-model-lists-audit.md` now carries the
disposition. What that means per studio:

- **A new model is now data.** Families, engines and modalities are read from the producer's own
  registry and the manifest; where a list is genuinely unavoidable it fails loudly naming the
  missing entry and the file to update, the way the Hub's engine table already did.
- **Voice**: one wiring table instead of six (`_ENGINE_REQUIREMENTS`, `_WIRED_FAMILIES`,
  `_SECTIONED_FAMILIES`, four behaviour sets and 14 `long_form_policy` branches, nine of them for
  families cut in ST-13.B). The MLX label and the "🍎 MLX only" filter read the family's own
  `engine`, which is the bug that had already shipped once. Every cloning model is governed by its
  published capabilities instead of a name substring, and a Whisper tokenizer source is derived
  from the model's own manifest instead of defaulting to another model's vocabulary.
- **Hub and Chat**: `chat-mlx` is in the modality vocabulary that the fleet join, the worker table
  and the ledger all read. `chat` jobs are governed by the memory gate, appear in fleet activity,
  and the Chat worker publishes a real activity snapshot instead of a permanently empty one.
- **Subtitle**: the worker's catalog is *every manifest declaring its engine*, not the packages
  whose id starts with `whisper-`. Each publishes its own `family`, languages and timestamp
  support from the manifest rather than claiming Multilingual and word timestamps for everything.
  Its default model comes from `"default": true` in the manifest, with the rule published.
- **Native**: the Transcription picker no longer requires an engine whose name contains
  "whisper"; image and music configurations no longer require the shipped engine's exact control
  set (a second engine of either modality decoded to `nil` and vanished); the Chat page uses the
  same model picker as the other four; the four per-modality display lists are derived from
  `StudioFunction`; and the installer verifies every runtime the release manifest declares, not
  just Voice's.
- **Tooling**: the capture tool can reproduce all **24** shipped packages (it could rebuild 17),
  and refuses to overwrite one it cannot; the fleet measurement covers music and chat; the image
  runtime smoke imports every class the Image registry names, so a broken Z-Image-Turbo module
  fails the smoke instead of failing on first use.

**One companion relationship, one spelling.** A manifest declares what it cannot run without in
`requires_packages`; `dependencies` was the first spelling and was read by nothing that resolved
the relationship, so `chatterbox-4bit`/`chatterbox-8bit` (MLX 4-bit and 8-bit) shipped with their
`s3tokenizerv2` tokenizer **never verified or downloaded** — the engine fetched it at generation
time, which is the offline failure the declaration exists to prevent. `validate_manifest` now
refuses the legacy spelling by name, and the whole catalog is migrated. Measured: exactly those two
packages' verification receipts change, and they re-verify once; nothing else is affected.

**A companion package now reaches the path the engine loads from.** This was a real bug with a
measured proof: Pocket TTS 4-bit (MLX 4-bit) loads its tokenizer and preset voices by
`hf_hub_download` through `HF_HUB_CACHE=<library>/hub`, and an installed package lived only at
`<library>/<package_id>/<revision>/model/`. On a Mac whose cache had never seen the repo — a kit
node that copied packages and nothing else — generation failed offline. Installation now mirrors
each companion into the HF cache layout from the manifest's own `source.layout` and
`files[].source_path` (no repo, family or filename list anywhere), hard-linking where it can,
idempotent, and degrading with a named reason on a read-only library. Verified with the network
off: the engine's exact call opens the mirrored `tokenizer.model`, sha256 `d461765a…`, and Pocket
TTS 4-bit generated 3.36 s of audio, byte-identical across runs.

**Published facts now carry their provenance.** Gateway Studio measured this from the consuming
side: with nothing stated it fell back to a curated flat `context_window_tokens = 1,000,000` and
`max_output_tokens = 128,000`, labelled *curated*, which a UI then showed as the model's own. The
shipped **Qwen3.5-9B 4-bit** states `text_config.max_position_embeddings = 262144` in its own
`config.json`; that is now what is published, with its source, and `max_output_tokens: 4096` is
labelled as the worker's default because the checkpoint ships no `generation_config.json`. Where
nothing states a value, nothing is published. The manifest's dead `parameters_source` pointer at a
file neither the package nor the publisher ships is fixed.

**Every lane publishes how to ask it for work.** `docs/lane-execution-contract.md` specifies
`lane_contract`: the canonical operation, the parameters with the bounds that are enforced, the
artifact form, the terminal vocabulary with `uncertain` marked **not terminal**, and what a cancel
does not promise — built from each model's own limits, beside the validator that enforces them.
Image, Music, Voice, Transcription and Chat all publish it, and `/api/hub/model-packages` now
carries `modality` and the contract, so a scheduler needs one read instead of a best-effort second
one joined on model id.

**A version field carries a version.** Four of five workers answered the literal string
`"standalone"` on `/api/health`, which the Hub republished as `workers[].studio_version` — a value
that reads as data, is indistinguishable from a version that failed to load, and makes every Mac
look like the same build. Every worker now reports the release it was staged from, through one
shared reader, and the Hub refuses to republish a value that cannot be a version.

**A message that stops at a colon is worse than no message.** A node detail read
`could not reach the node: ` with nothing after it. Refusals now name the peer, the exception type
and the reason; a fleet-credential refusal names the credential it required and the one it was
offered; and an unknown engine names the engine and both files to register it in.

**The owner-approved production floor moved to the model that has it.** The 3.2 GB live-free floor
for Qwen3-TTS 12Hz 0.6B Base 8-bit (the 8 GB voice-cloning tier, measured cold requirement 3.15 GB)
lived in the Hub, keyed by that exact repo id, because the Voice worker published no free floor. A
model-keyed table in a consumer is the defect this release is about — the next model the owner
proves would silently get nothing until somebody remembered to edit it — so the floor is now on the
model's own catalog row (`min_free_memory_gb`) and the Hub's table is deleted. Two guards replace
it: `memory_admission`'s reason now distinguishes "its worker publishes a 3.20 GB live-free floor
and that is what is enforced" from "nothing was published, the generic 2.0 GB default applies"
(it previously said the generic default applied *while enforcing 3.2*), and a test reads the
shipped Voice row end to end so a row that loses the field fails rather than quietly relaxing.

Also in this release: the two documents a reader is most likely to trust state numbers that a test
now **recomputes** rather than a human maintains (the catalog is **24** packages /
**70,529,384,759** bytes, and six engines ship); `tests/hub_regression`, the adapted-upstream parity
suite, is in the verification command set instead of being hidden behind its own `collect_ignore`;
and advisory commercial-licensing sentences are gone from shipped, user-visible copy, per the
owner's 2026-09-22 rule — a licence is now a name and a source, stated once as a fact.

**Verification.** `tests/` — **935 passed, 11 skipped**; `tests/hub_regression/cases` — **172
passed**; `bash native/build.sh` and `tests/native/pool-probe.sh` green.

**Open, and named rather than guessed:** Pocket TTS 4-bit (MLX 4-bit) ships eight preset voices
(the `pocket-tts-preset-voices` package) and also declares cloning, and a model that offers both
is reachable in only one tab today; the producer field to close it is a per-model `preset_speakers`
roster derived from that package plus a published `modes` list. The audited reference-audio
profiles exist only for the Qwen families, so private-upload reference preparation now refuses by
name for the other cloning models instead of using a guessed copy — closing that needs measured
profile records, not code. And the recorded voice smoke pilot names
`Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit`, which the catalog cut in ST-13.B, so the smoke reports
that role as unexercised until a shipped model's parameters are recorded.

## 0.6.60 — 2026-09-24 · Z-Image-Turbo 4-bit can be tried on an 8 GB Mac

A one-line fix with an outsized effect, and it was mine. When Z-Image-Turbo 4-bit was added it
published a **live-free-memory floor of 8.0 GB**, taken from its measured low-RAM footprint
(7.12-7.24 GB on a 32 GiB M2 Max). That conflated two different things: a *footprint* is not a
*free-memory* requirement.

On an 8 GB Mac a floor of 8 GB can never be met — and the Hub answers an unmet free floor with
**"wait"**, not "skip" (`broker._memory_gate`), so the job would have sat waiting for memory that
was never going to arrive. The one tier with no measurement was the one tier that could not run to
produce it.

- The floor is now **3.0 GB**: a real guard against starting into a nearly-full Mac, a little above
  FLUX's 2.0 because Z-Image's weights are 28 % larger, and satisfiable on an 8 GB Mac.
- Verified against the gate itself: an 8 GB Mac with 3.0-5.0 GB free now returns `run`; with 2.0 GB
  free it correctly returns `wait`.
- The test now asserts the **invariant** rather than the number — the free floor must stay below the
  smallest fleet machine's total RAM, or it silently becomes a tier in disguise and stalls instead
  of refusing. The old test pinned 8.0 and passed while the bug was live.

What decides "this machine class is unqualified" remains the published **tier**
(`min_unified_memory_gb = 16`), reported with a below-tier warning and deliberately never a wall —
per `memory_admission`'s own doctrine that a RAM floor is a qualification tier, not a wall. So an
8 GB Mac is offered Z-Image-Turbo 4-bit, warned that it is below tier, and runs it in low-RAM mode.
The 8 GB measurement is what turns that into a qualified tier or a documented exclusion.

## 0.6.59 — 2026-09-24 · Z-Image-Turbo, and the engine it needed

Image gains a second model. **Z-Image-Turbo** is selectable beside FLUX.2 Klein, and it renders
text FLUX gets wrong — the title "THE GOLDEN LIGHT OF DREAMS" comes out correct where FLUX writes
"THE GOLEN LIGHT OF DREAMS" at 4 steps and worsens at 9.

**The engine moved for it.** mflux 0.17.5 → **0.20.0**, mlx 0.31.2 → **0.32.2** — 0.17.5 pins
`mlx<0.32.0` and 0.20.0 requires `>=0.32.0`, so the two had to move together. The re-lock held
every other package: **75 → 63 packages**, of which 12 are the `twine` subtree that mflux 0.20.0
no longer requires. Nothing unrelated moved, proved by a byte-identical second compile.

**FLUX.2 Klein is re-qualified, not assumed.** Its output pixels do change between engines —
94.2 % differ, SSIM 0.972 — but the distance sits on glyph outlines and sparkle edges with flat
interiors near zero. Visually the same picture: on the typography scene the title is identical in
wording, typeface, size and line breaks, including the pre-existing "GOLEN" typo, which the bump
neither fixed nor worsened. It cannot be attributed to mflux alone: mflux and MLX moved together.

- The Image worker ran exactly one model — repo, revision, class import and defaults were module
  constants. It is now a registry, and the catalog advertises both models with their own steps,
  limits, memory policy and qualification block. FLUX's row is unchanged.
- **Z-Image's memory is chosen from the Mac, not fixed.** `low_ram` is not a constructor
  parameter, so the plan is applied through `CallbackManager._register_memory_saver` — the CLI's
  own mapping — and raises if mflux ever renames it rather than silently not applying. Below the
  tier the default was measured on, the worker takes the configuration with the known bound
  (7.1-7.2 GB measured) and pays the measured time cost.
- **It does not claim the default fails on 16 GB.** That figure is cache-inflated from a 32 GB
  host, and the same reasoning would wrongly rule out FLUX, which ships on 16 GB. The default's
  requirement there is unmeasured and said so.
- **The 8 GB tier is unclaimed** pending the owner's measurement. Two named constants are the
  only knobs to move when it returns.
- Z-Image publishes a live-free-memory floor of 8 GB. The Hub enforces only that floor and
  defaults to 2 GB, which would dispatch onto a Mac with 3 GB free and let it thrash rather than
  refusing. FLUX keeps its existing behaviour deliberately.
- `validate_request` now rejects non-finite guidance — the same NaN-acceptance class the
  independent review found in the chat worker.

Evidence: 34 image tests, native boundary/API/lifecycle suites, and a live catalog read showing
both models `execution_ready`. FLUX was run through both the legacy path and the new registry and
all three outputs are pixel-identical (`16a5535f…`), a value that independently reproduces the
0.20.0 re-qualification record.

## 0.6.58 — 2026-09-24 · hotfix: 0.6.57 left every model picker empty

**0.6.57 shipped a broken build and this fixes it.** Do not install 0.6.57.

The Chat manifest reached the release catalog in 0.6.57, but the Hub's engine→worker map in
`components/hub/standalone.py` was never extended for it. That map was an inline dict lookup, so
`GET /api/hub/model-packages` raised `KeyError: 'chat-mlx'` and answered **HTTP 500** — on every
Mac, because the manifest ships in the catalog whether or not the model is installed. The native
app reads that route with a hard `try` inside `refresh()`, so the 500 did not degrade one list: it
threw the whole refresh, set `isReachable = false`, and left **every model picker empty with the
Hub reported as not answering**.

- The mapping is now the named `_worker_for_engine()`, with `chat-mlx` added, and an unknown
  engine raises a `ValueError` naming the engine and both files to update instead of a bare
  `KeyError` from inside a route.
- `tests/test_model_manifest_catalog.py` gains a guard that walks the **real** catalog and asserts
  every shipped manifest's engine maps to a worker. Every previous test of that route used fixture
  manifests, which is exactly why none of them caught this. Reverting the one-line mapping makes
  the new test fail, which is how it was checked.

Measured before the fix: `/api/hub/model-packages` → HTTP 500 with the real catalog in a live Hub.
After: HTTP 200, 20 packages, all six engines present, the chat package carrying
`worker_id: "chat"`.

## 0.6.57 — 2026-09-24 · a node and its controller can both see the group

Owner report: after connecting nodes, a Mac did not show the pool it belonged to, and the
controller did not show the pool of connected nodes either. Both were true, and both came from
the same gap — **the group was not a fact either end held.** The controller's Fleet overview was
built from whatever was in its dispatch registry at that instant, and a node held a borrowed
credential with no record of whose it was, so it could not name its own group at all.

- **Adopting a Fleet token now records which controller it came from.** `POST
  /api/hub/fleet/adopt` accepts an optional `controller_url`; the app sends the controller's
  literal private address (resolving a name first, since the Hub stores an address it can dial).
  This is the root fix: the address previously lived only in the app's preferences file, so a
  reinstall or a second window left the Hub holding a borrowed token with no idea whose. An
  adoption that does not name a controller **clears** the recorded one — a stale address shows the
  wrong Mac as the head of the group.
- **`parent_controller_url` survives an unrelated save.** It was cleared whenever the role was not
  `"agent"`, so a Mac that had joined but had not yet been switched to Node lost the record on the
  next rename or listen-address change. It now lasts exactly as long as the adopted credential.
  Promotion to controller still mints a fresh token and clears it.
- **The Fleet overview is built from the group, not the dispatch registry.** A freshly joined node
  read as *"this Mac has not reported in yet"* for the four seconds until the peer poll caught up,
  and a node with no studio running was missing entirely. Roster members are now probed directly:
  a Mac that is off reads as off, with a reason, instead of as absent.
- **A node's overview shows the whole pool** — the controller and the Macs beside it — by reading
  the roster back from the controller it joined. An unreachable controller reports the group as
  *unreadable* rather than showing an empty one.
- **UI:** rows carry a Controller/Node badge and their address; the header names the group being
  shown and says when the list is incomplete; a node's "Joined nodes" section names its group
  instead of claiming the Mac is alone, and no longer offers "Send here" — a node does not choose
  where work runs.
- `tests/test_voice_bundle.py` staging fixture gained the Chat component, which it predated: the
  fixture's file list was missing `packaging/chat_worker.py`, so both staging tests failed on a
  `FileNotFoundError` rather than on anything they were written to check.

Evidence: [qualification/fleet-pool-2026-09-24.md](qualification/fleet-pool-2026-09-24.md) —
11/11 new tests driving two live Hubs, 126 passed across the fleet/settings/staging suites, native
boundary + API + lifecycle suites pass, and `tests/native/pool-probe.sh` reads the pool back
through the app's own workspace store from both ends.

## 0.6.56 — 2026-09-24 · 8 GB Macs can generate images again

`7187d8d` (0.6.55) turned a model's *qualification tier* into a hard block, regressing
`f9feca5` ("a RAM floor is a qualification tier, not a wall") three days later. The Image
worker publishes `memory_warning` on any Mac below the 16 GB tier; the new `blockReason`
returned that warning as the model's reason, which set `runtimeReady` to false, and
`usableModel` then refused every submit. FLUX.2 Klein image generation was dead on every
8 GB Mac even though the worker itself would have accepted the job.

- **`blockReason` no longer returns `memoryWarning`.** The tier is still surfaced through
  `NativeModel.memoryWarning` and shown in the Image workspace; only the worker's own hard
  gate (`memoryEligible == false`) blocks. Voice's `memory_eligible` gate is unchanged.
- **Image label restored to `FLUX.2 Klein 4B MLX 4-bit`.** It had been reduced to
  `FLUX.2 Klein`, dropping the bits/weight/quantization the picker is designed to keep;
  `pickerName` still drops only the "recommended" and runtime-marker suffix.
- `tests/test_model_reasons.py` now pins the advisory-not-blocking behaviour instead of the
  regression.

Affected: every 8 GB Mac, Image (and Chat). 16/24/32 GB machines were never affected.
Evidence: native boundary, API and lifecycle suites pass; 14/14 Image tests pass.

## 0.6.55 — 2026-09-24 · two Khmer voices, and a manifest capture that no longer truncates

Voice Studio gains two single-speaker Khmer families (ST-12, owner request). Every other TTS
family is mlx-audio, with F5-TTS the only PyTorch exception; neither new model is MLX, so each got
its own worker and dispatch branch under a new packaging engine, `voice-vits`.

- **`khmer-tts-mms`** — `khmerttsopensource/khmer-tts` @ `c9380a9…`, Meta's MMS Khmer VITS
  fine-tune: 83M parameters at 16 kHz through transformers `VitsModel` on CPU. No new Python
  dependency; `torch` and `transformers` were already pinned.
- **`khmer-tts-onnx`** — `sengtha/khmer-tts-female-v2` @ `c6fde511…`, a Coqui VITS female voice at
  22.05 kHz through ONNX Runtime on CPU (`khmer_tts_v3.onnx`). Adds `onnxruntime>=1.20` to the
  generation requirements; the 998 MB training checkpoint is excluded.
- Both are single-speaker and Khmer-only, and **refuse a reference voice** rather than ignoring it.
  Long scripts use the existing conservative sectioning, so no long-form-policy change was needed.
  Model-cache slots, eviction, memory preflight, readiness and diagnostics all cover the two
  families.
- **Manifest capture no longer silently truncates.** The Hub `/tree` API pages at 50 entries and
  the capture tool read a single page, so the MMS repository captured 2 of the 9 required files
  while the manifest looked complete. The tool now follows the `Link` header and supports a
  per-choice `exclude` filter. The manifest catalog grows to 37 packages (89,369,563,824 bytes).

Verified locally on a 16 GB M4 Mac mini: both voices render real Khmer audio, and a 576-character
script sectioned into 3 and was joined through the spawned native worker (MMS 31.3 s @16 kHz, ONNX
42.4 s @22.05 kHz). Voice suite 716 passed / 2 skipped; root suite 659 passed / 11 skipped / 1
pre-existing order-dependent Subtitle failure.

## 0.6.54 — 2026-09-23 · the app no longer launches a second copy of itself

0.6.53 introduced the crash keep-alive, and with it a real defect, reported by the owner within
the hour.

- **Enabling "Start at login" started a second instance.** The startup reconciliation called
  `launchctl bootstrap`, which loads the job, and `RunAtLoad` then started the job at once -
  while the app doing the enabling was already running. The second copy took the instance lock,
  failed, and showed "Studio Hub could not open". The enabling path now **writes the plist and
  never bootstraps**: `~/Library/LaunchAgents` is read by launchd at login, which is what "start
  at login" means, and there is no launchctl call that loads a `RunAtLoad` job without starting
  it. A job already loaded by the older build is left alone and replaced at the next login.
- **The toggle no longer lies.** Its state came from "is the job loaded", and a correctly
  installed agent is deliberately *not* loaded while the app runs - so the switch would have read
  off. It reads the plist instead.
- **A redundant launch is quiet.** A second instance detects that the lock is specifically
  already held, brings the running app forward, logs one line, and exits **0** - no alert, and
  nothing for `KeepAlive` to relaunch.
- Disabling still takes effect immediately: bootout, then remove the plist.

## 0.6.53 — 2026-09-23 · surviving a crash, a slow controller and a stubborn service

Three ways an unattended Mac was left dead, and one migration so the fix does not break a Mac it
is applied to.

- **An app crash was permanent.** "Start at login" used a login item, which does nothing if the
  app crashes or is quit. It is now a launchd agent that supervises the app's **own executable**
  (never `open`, which exits immediately and would leave launchd tracking the wrong process),
  with `RunAtLoad` and `KeepAlive = {SuccessfulExit: false}` - so it relaunches after a crash and
  leaves a deliberate Quit alone. A login-launch marker keeps "Start services at login" honest,
  because launchd starts the executable directly and the app would otherwise not know how it was
  started.
- **A node that booted before its controller stayed out of the group.** The startup re-join ran
  once. It now retries at 15s, 30s, 60s and then every 5 minutes, one attempt at a time, stopping
  on success, on leaving, on a role change or on disconnect. Three consecutive 401/403 answers say
  so once in the banner and drop to the slow cadence rather than looping in silence.
- **A service that failed to start gave up after ten seconds.** The ladder is now 1, 3, 10, 60,
  120 seconds and then every 5 minutes instead of stopping. "Recovering local services..." becomes
  "Still trying to start the services (last failure: ...)", the failure banner is written once per
  phase, a manual Start Services resets the ladder, and quitting cancels it.
- **Migration.** Every Mac already running 0.6.52 has an old login-item registration. On first
  launch the new build removes it once, records that it did, and reconciles the new agent from the
  existing "Start at login" setting so login startup is not silently lost. An upgrade that cannot
  remove the old entry still runs and says so.

## 0.6.52 — 2026-09-23 · no downgrades, and a download that survives

Two defects found while verifying the first real release push.

- **An older release was offered as an update.** The offer decided on "the advertised build
  differs from mine", with no version order, so a controller holding 0.6.50 while the Mac ran
  0.6.51 offered a rollback - one click from installing an older app over a newer one. The rule
  is now numeric: offered when the advertised version is greater, or the same version with a
  different build (a rebuild is normal and still offered); an OLDER version shows as "Older
  release available" with no install button and the install path refuses it. An unparseable
  version falls back to the build rule rather than silently refusing everything.
- **A part-downloaded artifact was thrown away.** A 1.2 GB release streamed into a fresh temp
  directory each attempt, so a dropped connection or a failed swap meant downloading it all
  again. It now keeps the fragment in a stable location keyed by the release identity, sends
  `Range: bytes=<have>-` and appends, and handles a server that ignores the range by restarting
  the file cleanly instead of corrupting it. The completed artifact stays until an install
  SUCCEEDS, so pressing Install again after a failure does not re-download a gigabyte. Progress
  reads "Resuming from N of X" in the existing banner.

Safety order is unchanged: the controller's countersignature is verified with the pinned key
before the download is trusted, the hash before the swap, and never against a key fetched
alongside the artifact.

## 0.6.51 — 2026-09-23 · the publisher key has a front door

- **Connections now shows and changes the trusted publisher key.** The gateway's Ed25519 key -
  the one that decides which releases this Mac will accept - was installable only by curl. The
  section shows this Mac's own publisher fingerprint (what a joining node pins) and the installed
  gateway fingerprint, or plainly says none is installed and that this Mac cannot accept an app
  release. Pasting a key and pressing Change asks for confirmation first, because it changes who
  may update this Mac and a wrong key means every release is refused.
- **An omitted publisher id is accepted** by `POST /api/hub/release/trust`; the public key is
  still the only required field and is still validated.
- **`GET /api/hub/release` reports `trusted_publisher`** ({publisher_id, fingerprint} or null),
  so a page can say which key is installed. `release` is unchanged.

## 0.6.50 — 2026-09-23 · updating a Mac from its controller

The remote-update design's two halves are now built. Nothing changes for a fleet until
GatewayKH pushes a release: a controller with none answers `{"release": null}`.

- **The controller mirrors a release.** `POST /api/hub/release` accepts a release pushed by
  GatewayKH (Fleet token, because the gateway holds no Hub token - the **signature** is the real
  authorisation), verifies the gateway signature and the artifact hash, stores both, and
  countersigns. `GET /api/hub/release`, `/artifact` (range-capable) and `/identity` serve it.
  `POST /api/hub/release/trust` installs the trusted gateway key and is Hub-token only: that is
  the owner's decision and the actual gate.
- **A Mac offers the update and installs it on request.** Connections shows an "Update
  available" banner (version, build, size, who signed it) with Install and Dismiss, or an
  orange "cannot be verified" banner when there is no pinned key. Installing verifies the
  controller's countersignature with CryptoKit, verifies the artifact hash, swaps with the same
  staged discipline as `install_local.sh`, restarts the services and reports.
- **Trust is a pinned key, not a fetched one.** The controller's key is pinned the first time it
  is read and stored with the membership; a release whose key does not match is refused, a
  different key is never adopted, and a Mac with no pinned key is offered no install button.
  Verifying against a key fetched alongside the artifact would prove nothing.
- **Ed25519 now prefers a vetted library.** `cryptography` is vendored into the runtime and used
  when present; the pure-Python implementation remains a fallback. The Hub refuses rather than
  guesses when it cannot verify.
- **Fixed: the "Restart Services" button did nothing.** `restartServices()` guarded on
  `canConfigure`, which is false whenever the services are running - so the button was inert
  exactly when it was offered, including the restart an update needs.
- Artifact form: **DMG**. An app bundle is refused with a clear message rather than guessed at.

## 0.6.49 — 2026-09-23 · the activity reporter says why, and every studio reports

GatewayKH's addendum, in full.

- **A machine-readable reason beside the sentence.** Each `fleet_activity.machines[]` row now
  carries `limitation_code` (closed vocabulary: `reporter_unavailable`, `partial_read`,
  `stale_snapshot`, `waiting_for_worker`), `limitation_reason`, `limitation_since` (epoch
  seconds, stamped only on a real support transition), `limitation_affects` (the field names
  genuinely missing, derived from the row), and `next_attempt_at` / `eta_seconds` (present but
  null - the monitor holds no exact retry time, and inventing one would be worse than saying
  nothing). The existing `limitation` sentence is unchanged. A machine that reports fully has
  `limitation` and `limitation_code` both null, and a test asserts those two agree.
- **The root cause of "Direct activity partially unavailable", fixed.** Only the Voice worker
  implemented `GET /api/fleet/activity`; image, subtitle and music answered 404, so every
  machine with more than one studio read as partially unavailable and its state was `unknown`.
  Image, Subtitle and Music now publish the same snapshot shape (an empty store returns a
  valid empty snapshot, never 404), and the Hub's validator accepts all four studios and the
  `music` operation. The 404-still-means-unavailable rule is unchanged, so an older worker is
  still handled.
- Honest boundary: `fleet_activity.machines[]` still groups only the `image` and `voice`
  studios, so the new Subtitle and Music reporters are accepted and recorded but do not yet
  add a machine row of their own.

## 0.6.48 — 2026-09-23 · the capacity, worker and model-health contract

GatewayKH, which routes Story Studio's work to Studio Hub locations, measured six gaps and
asked for the Hub side. Five are implemented here, additively.

- **`model_id` on every model row and every batch row** (`model_ids.py`), resolved to the
  manifest's own `model_id` so one string joins a model to a batch. The `repo`/`model` keys
  are unchanged.
- **`workers[].models` and `workers[].supported_operations` are populated.** They were empty
  because the only source was `model_exposure`, an audit-gated approval workflow in which no
  catalogue row currently qualifies. The supply view now comes from the worker catalogue's
  direct evidence; the approval workflow is untouched. `model_supply` and
  `capacity.by_operation` therefore carry real machine counts, and an explicit
  `available`/`runtime_ready`/`memory_eligible` of `false` disqualifies a model - absence
  stays null and is never defaulted to a plausible value.
- **`GET /api/hub/model-health` returns one row per (machine, model)** with `model_id`,
  `machine`, `state`, `reason`, `hint`, `observed_at`, over a closed vocabulary
  (ready, download_required, runtime_missing, unsupported_os, unsupported_chip,
  insufficient_memory, quarantined, unknown). `paused` is kept.
- **`errors` on each batch row**: `[{"item_index", "code", "message"}]`, failed items only.
- **`POST /api/hub/jobs/{batch_id}/items/{index}/retry`** and the transcription equivalent.
  Retry accepts a failed item and returns it to `queued` with its prompt and params intact;
  it refuses a non-failed or uncertain item and a cancelled batch.
- All of it is reachable with the **Fleet** credential, including `model-health`, which was
  Hub-token only.

Known limits, recorded rather than papered over: there is no discard route for transcription,
so an uncertain transcription item cannot yet be released that way, and the native job
detail cache is not invalidated by a retry (it caches only done/cancelled rows).

## 0.6.47 — 2026-09-23 · the sidebar counts models, not workers

- The sidebar said `Image 1 · Voice 1 · Subtitle 1 · Music 1` because it counted **workers**, and
  every Mac runs one worker per studio. Beside a model picker offering eight voice models that
  read as a bug. It now counts the **models this Mac can run**, with the same filter the pickers
  use (`function == studio && supported`, and the chosen voice tab for audio), so the sidebar and
  the picker always agree - `Image 1 · Audio 8 · Subtitle 3 · Music 2`.

## 0.6.46 — 2026-09-23 · the sidebar says what this Mac is and what it offers

- **A mode pill in the sidebar** - Controller, Node or Standalone - with a dot that is green when
  the group it belongs to answers, amber when it does not, grey when it is on its own.
- **The sidebar footer now holds the whole status**: the mode, how many of each generator this
  Mac is running (`Image 1 · Voice 1 · Subtitle 1 · Music 1`), the local-services line, and the
  version and build. The version moved here from the page's lower-right corner so an operator
  has one place to read this Mac's state.
- **The Connections page keeps a standing connection state** beside the join form - a coloured
  dot with "Connected" or "Joined, but not answering" and the controller's own answer - instead
  of only a banner at the top of a page the operator may have scrolled past.
- **The inline join result is coloured** (green or amber) rather than grey, because a success and
  a failure used to read identically.

## 0.6.45 — 2026-09-23 · the ATS keys were cancelling each other out

- **Joining a controller over Tailscale answered `code -1022`** ("...requires the use of a
  secure connection") against a controller that was up and reachable. 0.6.40 added
  `NSAllowsLocalNetworking` **and** `NSAllowsArbitraryLoads`; macOS ignores
  `NSAllowsArbitraryLoads` when a granular key is present, and `NSAllowsLocalNetworking` does
  not cover the tailnet's `100.64/10` range - the address a node is in fact given. The bundle
  now declares `NSAllowsArbitraryLoads` **alone**.
- Worth recording: a standalone URLSession probe cannot be used to test this. Probe bundles
  with no key, with each key and with both all reached a `100.x.y.z` Hub successfully, so the
  probe is not subject to the same policy as a launched app. The owner's screenshot is the
  evidence that the real app was being refused.

## 0.6.44 — 2026-09-23 · a permission change can no longer stop the app starting

- **The Music runtime identity no longer includes file permissions.** `_tree_digest`
  (music_worker) and `tree_digest` (staging) hashed each file's mode along with its bytes, so
  a copy that changed 0444 to 0644 - which an install can do - left the Music worker refusing
  to start with "overlay digest does not match its release identity". The Hub waits for its
  workers, so **the whole Hub failed to start**: no port, nothing joinable, and the app showed
  only "Automatic recovery stopped after repeated service failures". The digest is now
  content-only, and both implementations were checked to agree byte for byte on the same tree.
- This is the failure the owner hit while trying to join a controller; the installed bundle on
  this Mac had writable runtime files while the kit's copy was read-only.

## 0.6.43 — 2026-09-22 · see the token, and test the address on its own

- **The Fleet token is shown, not masked.** The owner asked to see what was pasted so a wrong
  copy is obvious; it is a LAN credential already held in a file on both Macs.
- **Pasted whitespace is removed.** Copying an address or token out of a document brings a
  trailing newline with it, which landed in the field, displayed as the text dropping to a
  second line, and made the value wrong without looking wrong. Neither value can contain
  whitespace, so it is stripped as it arrives.
- **A Test button** asks the controller's `/api/health` - which needs no credential - before
  any token is sent. That separates "can this Mac reach the controller?" from "will it accept
  the token?", which one error message used to mix together.
- **Network failures now carry their code** (-1004 cannot connect, -1009 no network, -1022
  App Transport Security, -1200 TLS, -1001 timed out) so a report can be acted on instead of
  guessed at.

## 0.6.42 — 2026-09-22 · typing the controller's address, simplified

- **The port is optional.** The Hub's port is fixed, so `100.64.0.5` is now a complete
  answer and 47873 is assumed. It used to demand `host:port` and refuse the address alone.
- **A name is accepted**, which is what a tailnet actually gives you:
  `mac.tailnet.ts.net` or `mac-mini.local` now work, with or without a port and with or
  without `http://`. The safety property is unchanged in substance - a name is resolved
  first and refused unless every address it resolves to is private or Tailscale, so this Mac
  still cannot be pointed at an arbitrary host on the internet.
- Error messages now say what to type instead of what was wrong with the old format.

## 0.6.41 — 2026-09-22 · the local-network permission macOS was refusing

- **The real block on joining a controller.** macOS gates **local network** access per app,
  separately from App Transport Security, and an app that declares nothing is refused rather
  than prompted. The bundle now carries `NSLocalNetworkUsageDescription`, so the first attempt
  to reach another Mac raises the system prompt and the app appears under
  **System Settings > Privacy & Security > Local Network**. Reported by the owner as a
  security message that survived the ATS declaration in 0.6.40; measured here, the LAN case
  was not ATS at all.
- Because the app is ad-hoc signed, macOS ties the grant to the current build, so a Mac may
  ask again after an update. If the app is listed but denied there, switch it on and retry.
- The ATS declaration from 0.6.40 is kept: there was none, and a tailnet address is the case
  it covers.

## 0.6.40 — 2026-09-22 · joining a controller works, and says so when it does not

- **A secure-connection refusal is now covered.** macOS reported the join failure
  with the text App Transport Security produces (`...requires the use of a secure
  connection`), so the bundle now declares `NSAllowsLocalNetworking` and
  `NSAllowsArbitraryLoads` - there was no ATS key at all before. Honest limit: this could
  not be reproduced against an RFC1918 LAN address (a plain-HTTP request there succeeds
  with no declaration), and a standalone probe bundle could not validate the declaration
  itself, so the tailnet (`100.64/10`) case is the one this is expected to fix and the
  two-Mac run is the proof. Recorded rather than claimed.
- **A refused join quotes the controller's own reason.** The response body's `detail` is
  now read and shown ("studio modality must be one of [...]") instead of "refused (400)",
  and the 401 text names the **Fleet** token rather than the Hub token.
- **A join whose credential adoption failed no longer reports success.** Registration and
  adoption are two steps. A failed adoption left the controller with this Mac on its
  roster and 401 from it, while the app said "Joined"; a manual join now fails loudly.
- **The result of a join, leave or role change is a banner that stays until dismissed** -
  full sentence, selectable, at the top of Connections and inline in the join form. It was
  a line of small grey print that the owner did not notice until the third attempt.
- **Connections states the group explicitly** ("Joined to <address>" / "On its own"),
  which the role picker alone did not answer.
- Two further "Hub token" references in the app were corrected to the Fleet token.

## 0.6.39 — 2026-09-22 · the controller/node link works; placement follows the fleet

- **A node could not join a controller at all.** The app offers every worker it runs,
  including `music`, and `fleet_nodes.NODE_MODALITIES` still held only
  `image/voice/transcription`, so `POST /api/hub/fleet/nodes` answered 400 ("studio
  modality must be one of ['image','transcription','voice']"). Every current build was
  refused. Music is now part of the node vocabulary, and `TRACKED_MODALITIES` matches.
- **A joined node never became routable.** `StudioMonitor.reload_registry()` had no
  callers, so a controller kept the registry it built at startup: the node read as
  reachable on the Fleet page while no work was ever sent to it until a restart. The
  join and leave routes now reload it.
- **Remote image and music could never reconcile.** `_validate_image_worker_job` and
  `_validate_music_worker_job` compared the worker's own `machine_id` against the item's
  `machine`, which for a remote studio is the *node id* the Hub routes through; the two
  can never be equal, so every remote attempt was fenced `uncertain`. That one comparison
  is skipped for remote workers; revisions and `worker_id` are still checked.
- **Placement follows the fleet** (owner policy). Voice, music and video prefer the
  largest capable machine; image and subtitle prefer the smallest, keeping the
  high-memory Macs free for audio. It is a preference, never a filter - the model's own
  memory floor decides eligibility and every capable worker stays a fallback - and
  unknown telemetry sorts last, not first. Retires the chip-speed image proxy.
- **Reachable on Tailscale out of the gate.** A fresh install listens on this Mac's
  private and Tailscale addresses instead of loopback only, and the reachable-address
  toggles stay editable while the services run: the change is saved and the existing
  notice offers the restart that applies it, instead of the control refusing to move.
- **Each build is identifiable.** The bundle carries `StudioHubBuild` (the commit),
  `/api/version` reports `build`, and the footer and Connections page show
  `0.6.39 · <build>`. Two copies of one product version were previously indistinguishable.
- **The Connections page explains both tokens in plain English** - the Hub token as this
  Mac's own key that never leaves it, the Fleet token as the group's work badge, each
  with what it allows and forbids, and the join form names the token it wants.
- The controller may discard an uncertain job on a node (owner decision); that route is
  verified end to end with the Fleet credential, and a controller must still hold a model
  locally to accept a job for it (owner decision, unchanged).
- Includes the Music studio work, the 8-bit VoxCPM2, the studio UI pass and the P0
  stabilisation that landed after 0.6.38.

## 0.6.38 — 2026-09-20 · clone validation compared spelling; the voice suite joins the gate

- **Numeral canonicalization in `qwen_quality`** (root cause of the "flaky" clone rejections). The
  validator scores Whisper's transcript against the submitted text; Whisper renders "one two three"
  as "123", so `token_error_rate` 0.4286 / `character_error_rate` 0.3793 on a 37-character script
  tripped the `> 0.40 and > 0.25` rule and rejected audio Whisper had transcribed correctly. Real
  fixtures (SHA-verified against the app's stored `observed_sha256`): 37-char "…one two three." was
  REJECTED before and now scores 0.0/0.0; the 49-char variant passes either way; the 372-char prose
  case is unchanged (ter 0.0147, 68/68). Numbers are canonicalized on both sides of the token,
  character and terminal-gate streams; thresholds and the retry policy are untouched, and the
  negative fixtures (unrelated speech, half missing, hallucinated tail, 8x repetition, and unrelated
  speech full of numerals) are all still rejected with wide margins (ter >= 1.0).
- **`_qwen_job_duration_limit` KeyError** (shipped in 0.6.37, recorded here): it read
  `job.params["_resolved_section_max_characters"]`, a key only 0.6B Base builds and the audited 1.7B
  8-bit checkpoint set, so every 1.7B-Base-4bit clone generated, transcribed and then died with
  `KeyError` reported as "Whisper Large could not validate the Qwen output" - 6 of 6 recorded
  attempts. Now `.get()`.
- **`audit_contract_runtime` false critical mismatch**: the derived 4-bit record declares 400/280 and
  the catalogue applies it, but `_measured_section_override` only recognised ids listed in
  `_MEASURED_SECTION_PROVENANCE`, so it compared the claim against the family default 288. New
  `_derived_section_override()` allows a derived record to inherit an anchored audit's numbers only
  when it names that audit, matches its `upstream_model_id`, and claims exactly its maximum and auto
  default - it can inherit evidence, never author a number. `build_report`: 15 records, 0 mismatches.
- **`components/voice/app/tests` is in the release gate.** 40 failures had accumulated invisibly
  because `unittest discover -s tests` never collected that tree. 14 were stale assertions for the
  long-form/section-control/speed changes in 0.6.34-0.6.37 (updated to the new intent and renamed
  where the name had become a lie), 2 were genuine (above), 18 drove the retired Pinokio
  launcher/service whose files exist in no commit (replaced by standalone successors or skipped with
  a named reason; the watchdog tests also had a live `os._exit(75)` hazard, now inert). Final: 722
  passed, 19 skipped, 0 failed. New gate test `tests/test_voice_product_suite.py` runs the developer
  command in a clean child, fails on non-zero exit, on "no tests ran", and if fewer than 100 tests
  pass (verified by pointing it at an empty directory).
- **`docs/music-studio-adoption.md`**: the music-studio adoption plan - ACE-Step 1.5 as the model, why
  the old Pinokio Music Studio's models are unusable (CC-BY-NC MusicGen weights, 30 s), which Hub
  contracts already exist (`MODALITY["music"]`, `music.generation`, the audio priority class), and
  what is missing (worker, catalogue, manifests, launcher, page, storage root). Runtime reuse is the
  simplification: the Voice runtime already carries mlx + torch + diffusers + transformers.

## 0.6.37 — 2026-09-20 · the smoke harness asked every model the same question; the tempo pass was silent

- **`qualification/smoke_new_models.py` built an invalid request for the Qwen tier.** `PRESET_KEY`
  had no qwen3-tts entry, so CustomVoice and VoiceDesign rows were sent an empty speaker and Base
  rows a reference voice with no transcript - and the reported errors were about the missing
  parameter, never the model. It now builds each tier's real request (`preset_speaker` from
  `qwen3_preset_speakers`, `voice_design_prompt` from `qwen3_voice_design_examples`, a library voice
  for Base), routes Bark to `bark_voice_presets`, skips rows with `execution_ready: false` (no
  packaged manifest - F5-TTS) and rows whose capabilities carry no `tts` (the aggregate catalogue also
  serves image rows, which the Hub refused with a 409 that said nothing about a model).
  Result: Qwen 6 of 7 pass, up from 0 of 7.
- **One real failure remains**: `mlx-community/Qwen3-TTS-12Hz-1.7B-Base-4bit` ends in
  `QwenQualityError: Whisper Large could not validate the Qwen output` while its 8-bit sibling
  passes. Recorded rather than papered over.
- **`_apply_parent_output_speed` logs the tempo pass.** The in-process path logged "applied
  pitch-preserving Nx tempo"; the parent path - which every Hub job takes, because generation runs in
  the spawned child with `postprocess_speed=False` - did not, so a working speed control left no
  trace. Verified before the change by measurement: 1.0x gave 12.88 s and 12.00 s, 2.0x gave 6.29 s
  (1.98x).

## 0.6.36 — 2026-09-20 · a done item reported 79%, and the Hub regression suite joins the gate

- **`item["progress"] = 1.0` on the done transition.** `_record_worker_success` published the
  terminal state without touching progress, so the item kept whatever the last poll read. The
  15,058-character Kokoro proof finished with `state: done`, `chunk_total: 6`, a complete 807.1 s
  artifact - and `progress: 0.788` (`0.08 + 5/6 * 0.85`, i.e. after section five). Case:
  `tests/hub_regression/cases/test_terminal_progress.py`.
- **The regression cases were never in the gate.** `tests/hub_regression/test_regression.py` was a
  bare `test_*` function, which `unittest discover` does not collect, so its 54 isolated pytest cases
  (dispatch fencing, execution identity, memory admission, model-failure protection, ledger recovery)
  ran only when someone remembered to invoke pytest by hand. It is now a `TestCase`, asserts that
  pytest actually collected and passed rather than merely exiting 0, and the subprocess timeout is 120 s
  (the suite takes ~1.5 s; 60 s was too tight on a loaded machine). Gate count: 54 cases + 1 harness.
- **Published `source` names the evidence, not the code path.** `policy_for` mapped every non-audit
  origin to "runtime_default"; voxtral's measured 800 came out as "runtime_default" while its origin
  said "measured". Now `measured_fleet` and `engine_input_limit` are published as themselves, so the
  Models page says where the number came from.
- **`qualification/long_script_run.py` counts sections correctly.** Its section counter matched the
  word " section ", but the worker writes section files (`section_001.wav`), so the first 15,058-
  character run reported "sections seen 0" while the log showed six. It counts the paths now.

## 0.6.35 — 2026-09-20 · voxtral-tts measured: 800-character sections, and a 96-second truncation found

- **Measured voxtral-tts through the app's own Hub** with the new
  `qualification/measure_section_budget.py` (transcribe-back word coverage, same voice and seed).
  The per-budget axis could not be exercised - the private `_resolved_section_max_characters` override
  is not a member of the worker's `Txt2SpeechBody` and is dropped by Pydantic, and the public
  `section_max_characters` was refused for every family without a declared section-size control
  (0.6.34 fixed the second half) - so the same evidence was produced by varying the script length
  instead: 1162 chars / 215 words → 100.0% coverage; 1685 → 96.8% (10 words lost); 2463 → 80.3%;
  3205 → 64.1%.
- **The 96-second truncation.** The 2463- and 3205-character renders are both exactly 96.0000 s
  (2,304,000 frames = 1200 audio frames), because the worker passes no `max_tokens` for this family
  and `mlx_audio/tts/generate.py` defaults it to 1200, overriding the model's own 4096. Sections of
  800 characters (~36 s) stay far inside it, so the cap is no longer reachable; the underlying
  wrapper default is recorded in `VOXTRAL_SECTION_MAX_CHARACTERS` rather than changed, because the
  measured evidence for a longer single call does not exist.
- `VOXTRAL_SECTION_MAX_CHARACTERS = 800` with `origin: "measured"` and the full table in the policy
  comment. Tests updated: the measured family keeps its measurement, stays below the verified-perfect
  1162, and its declared control publishes 800.

## 0.6.34 — 2026-09-20 · the paused model, the missing budgets, and the silent speed control

- **Kokoro root cause: a field name, not the engine.** `_record_local_revision_evidence` read
  `job.params["voice"]`; every client (the app, the gateway, the smoke script) sends `preset_speaker`,
  which is the field the app's audio request has always carried. The fence therefore raised "Kokoro
  preset voice evidence is unavailable" before the engine loaded, 3 failures in 5 s tripped the Hub's
  circuit breaker, and every later retest was refused pre-dispatch — which is why the reported cause
  never changed through three releases. Both the picker path and the evidence fence now accept either
  name, with Kokoro's `voice_revision` still versioned by the model snapshot plus preset key.
- **The native child now reports its traceback.** The spawned generation process caught `BaseException`
  and returned `f"{type(exc).__name__}: {exc}"` — the string "SystemExit: 1" was, literally, the whole
  diagnosis. The child now sends the formatted traceback as a separate bounded field (6000 chars) and
  the parent folds it into `NativeRunOutcome.error`, so the job record, the log and the operator see
  the frame that raised. Test: `tests/test_native_executor_reports_cause.py`.
- **Paused models are visible and releasable.** `broker.recheck_model_protection` existed with no
  caller. Added `GET /api/hub/model-health`, `POST /api/hub/model-health/recheck`,
  `broker.model_block_state`, `broker.prune_model_protection` (startup, for models this build no
  longer ships), `paused`/`paused_reason`/`paused_studio`/`paused_failures` on every
  `/api/hub/model-packages` row, native decoding plus the Models-page marker and Recheck button.
  Tests: `tests/test_paused_model_recovery.py`, native fixture `fixture/paused`.
- **Long-form coverage completed.** `_runtime_default` returned None for seven families
  (orpheus, bark, voxtral-tts, marvis, kittentts, spark-tts-mlx, f5-tts), and None meant "one request
  for the whole script". Added `CONSERVATIVE_DEFAULT_SECTION_MAX_CHARACTERS = 280` as a fallback that
  cannot be skipped, explicit policies for orpheus (170) and bark (150) with
  `hard_cap_characters` set, and voxtral-tts at the conservative default pending measurement. Audit
  overrides can tighten a section but can no longer exceed an engine's input limit.
- **Section-size control derived for every sectioning model.** Only Qwen 1.7B Base declared one, so
  every other family's chunking was invisible and unchangeable; `section_size_control_for` now derives
  the control from the same policy the runtime obeys (min 40, max = engine cap or 3000, default =
  family budget). `resolve_section_budget` therefore accepts a caller's override for any family, which
  is also what makes the per-budget quality measurement possible through the public API.
- **Speed delivery is one policy, published per model.** `long_form_policy.speed_policy_for` returns
  `native` (engine applies the multiplier: kokoro-mlx, kittentts) or `tempo` (one pitch-preserving
  FFmpeg pass on the finished artifact). `generation.py` passes `speed` upstream only for native
  families and asks the same policy at both pass sites, replacing the hand-kept exclusion list plus
  `_POSTPROCESSED_SPEED_FAMILIES` membership test — which is how Bark's speed control had no path at
  all, and how Spark-TTS would have raised `KeyError` on any value but its five levels. Clamps now
  come from the shared `SPEED_MINIMUM`/`SPEED_MAXIMUM`. The tempo label table falls back to the
  catalogue label instead of raising "not configured for this family".
- **The app shows the policy.** `long_form_delivery` (section size, join pause, hard cap, source, note,
  speed delivery and range, section-size range) is decoded into `NativeLongFormDelivery` and shown on
  the Models page and in the list, so "how is this model chunked, and how does its speed work?" needs
  no terminal.
- Tests: `tests/test_section_policy.py` (16) pins every family's budget, the engine cliffs and the
  audit ceiling, the speed deliveries, the dispatcher's use of them, and the derived controls.
  `tests/native/api_main.swift` covers the paused row and the recheck request.

## 0.6.33 — 2026-09-19 · the spawned child did not resolve espeak from PATH

- **Narrowed to the child process.** Replaying the exact payload the worker sends
  (`_native_dispatch_payload({"action": "generate", ...})`) under the worker's own conditions -
  `PYTHONNOUSERSITE=1`, the runtime's `bin` first on `PATH`, no Homebrew - **returned a normal result
  with no `SystemExit`**, while a fresh job through the Hub still failed. Generation runs in a
  `multiprocessing` **spawn** child, so a PATH entry is not sufficient there.
- Fix: `ensure_espeak_available()` now also sets `PHONEMIZER_ESPEAK_PATH` to the vendored
  `espeak-ng`, naming the executable outright rather than relying on a search path.
- This is the fourth distinct cause in this sequence and each was real: (1) espeak was shipped as a
  *library* only and `phonemizer` hangs without a command; (2) `misaki` needs spaCy's
  `en_core_web_sm`, without which the job exits silently; (3) that model landed in `~/.local`, which
  the packaged worker ignores (`PYTHONNOUSERSITE=1`), so the job tried to download into the read-only
  bundle; (4) the spawned child needs the executable named, not found.

## 0.6.32 — 2026-09-19 · the spaCy model must be inside the runtime

- **Why Kokoro still failed after 0.6.30/0.6.31.** The packaged worker runs with `PYTHONNOUSERSITE=1`
  (set by the worker launcher), so a spaCy model installed in `~/.local` is **invisible** to it. With
  the model missing, `misaki` tries to download it at job time, and the app bundle is read-only:
  `ERROR: Could not install packages due to an OSError: [Errno 13] Permission denied: .../site-packages/en_core_web_sm`
  → `RuntimeError: SystemExit: 1`. Confirmed by reproducing it: with `PYTHONNOUSERSITE=1`,
  `import en_core_web_sm` raised `ModuleNotFoundError`.
- Fix: `en_core_web_sm` 3.8.0 now lives **inside** the runtime's site-packages
  (`.../voice-runtime/lib/python3.12/site-packages/en_core_web_sm`), so staging carries it into the app.
  The runtime directory had to be made writable first — it is read-only as staged, which is also why
  the user-site install happened in the first place.
- Verified under the worker's exact conditions (`PYTHONNOUSERSITE=1`, runtime `bin` on `PATH`, no
  Homebrew): the model resolves inside the runtime and `misaki.en.G2P(...)` returns
  `həlˈO wˈɜɹld.` Then a Kokoro job was run through the Hub.
- Also corrected this round: `qualification/smoke_new_models.py` reported a dispatched job that failed
  as `queued`, because it read the item's state while the reason lives in the batch's governor note —
  it now reports the note too, so a failure cannot look like a hang.

## 0.6.31 — 2026-09-19 · the spaCy model was the other half of Kokoro's failure

- **Root cause of `SystemExit: 1` found:** `misaki`'s English G2P loads spaCy's `en_core_web_sm`, and
  the runtime shipped no such model. Missing it, the text-preparation library exits - so the job
  loaded the model, resolved the voice, and died with no explanation. `en_core_web_sm` 3.8.0
  (12.8 MB) is now installed in the runtime and therefore staged into the app.
- Verification of the whole chain in the repo runtime, with the runtime's bin on PATH and no
  Homebrew: `espeak-ng` resolves to the vendored binary and `misaki.en.G2P(...)` returns
  `həlˈO wˈɜɹld, ðɪs ɪz ɐ tˈɛst.`
- So Kokoro needed *both* halves fixed: the espeak **command** (0.6.30) and this **model** (0.6.31).
  0.6.29's library-path helper remains redundant - `misaki/espeak.py` already does that itself - and is
  kept only for the PATH wiring and the explicit environment, with a comment saying so.
- F5-TTS stays removed (0.6.30). Evidence for the whole chain: `tests/test_espeak_library_available.py`
  (4 checks) plus a real Kokoro job run through the Hub afterwards.

## 0.6.30 — 2026-09-19 · vendored espeak-ng (Kokoro), F5-TTS dropped

- **Kokoro needs an `espeak-ng` *command*.** Proven, not assumed: with only the bundled espeak
  *library* available, `phonemizer` **hung** for ten minutes rather than erroring, and
  `misaki/espeak.py` already wires that library itself (`EspeakWrapper.set_library(...)`), so 0.6.29's
  helper was redundant. The owner asked for Kokoro, so the command is now vendored:
  `.build/voice-runtime/bin/espeak-ng` plus `libespeak-ng.1.dylib` and `libpcaudio.0.dylib`, with every
  Homebrew path rewritten to `@loader_path/<name>` and both the binary and the libraries
  **ad-hoc signed** (without that, macOS kills the modified arm64 binary with `Killed: 9` — which is
  how the first vendoring attempt failed). Verified under `env -i PATH=/usr/bin:/bin` with Homebrew
  absent: `espeak-ng -q --ipa "hello world"` → `həlˈəʊ wˈɜːld`. Voice data comes from the
  `espeakng_loader` package the app already shipped (same 1.52.0 version).
- `ensure_espeak_available()` now also puts the command's directory on `PATH`, because `phonemizer`
  finds it with `shutil.which`. It looks beside the running interpreter, which is the runtime's bin
  directory in both a checkout and the packaged app; `paths.tools_dir()` is the bundle's tools folder
  and does not exist until the app is staged.
- **F5-TTS removed** on the owner's instruction: `model-manifests/f5-tts.json` deleted and its two
  entries dropped from the catalogue gate. `umt5-base` **stays** — it is LongCat's companion, not
  F5's (F5 declares no dependencies).
- Evidence: `tests/test_espeak_library_available.py` (4 checks: helper finds a real library, an
  operator's own espeak survives, it is wired at import, and the vendored command is findable on PATH
  and runs in a stripped environment) and `tests/test_model_manifest_catalog.py` (the pinned catalogue
  now has 33 entries). Verified afterwards by running a Kokoro job through the Hub.

## 0.6.29 — 2026-09-19 · Kokoro: the bundled espeak library was never used

- **Kokoro died with `SystemExit: 1` on every job.** The worker log shows it resolving the voice
  (`voices/af_heart.safetensors`, language `a`), starting `Creating new KokoroPipeline`, and exiting.
  Cause: espeak's phonemizer defaults to running an `espeak-ng` *command*, and none is installed -
  while the app's runtime already ships `espeakng_loader` with `libespeak-ng.dylib` and 400 files of
  `espeak-ng-data`. Nothing was missing; nothing pointed at what was there.
- New `ensure_espeak_available()` in `generation.py`, run at import: it uses the loader's own
  `get_library_path()`/`get_data_path()`/`make_library_available()` and sets
  `PHONEMIZER_ESPEAK_LIBRARY` and `ESPEAK_DATA_PATH` with `setdefault`, so an operator who has their
  own espeak keeps it. Returns whether a library was found; a missing loader is not an error.
- Evidence: `tests/test_espeak_library_available.py` (3 checks) - the helper finds a real library and
  data directory, an operator's own `PHONEMIZER_ESPEAK_LIBRARY` survives, and it is wired at import so
  a job cannot forget it. Verified by running a Kokoro job through the Hub afterwards.

## 0.6.28 — 2026-09-19 · the dispatch rollback was unreachable

- **The rollback added in 0.6.19 could never execute.** It wrapped the lease-time write in
  `try: _save(batch) / except:`, and 0.6.21's bookkeeping sweep replaced that call with
  `_save_quietly(batch)` — which **never raises**, by design, because a failed bookkeeping write must
  not abort a dispatch pass. So the write failed silently, the item stayed on the "dispatched" path,
  and the job was handed to a worker whose Hub had just failed to record it. Found by finally writing
  the regression test: with the write forced to fail, the item ended `error` (from `_run_item`
  failing on a fixture batch with no input path) with `retry_at=None` - proof the rollback never ran.
- Fix: use `_save_quietly`'s **return value** (`if not _save_quietly(batch):`) so the rollback is
  reachable, and give the item a 30-second `retry_at` backoff so the same pass does not immediately
  retry it - which is what turned a rolled-back dispatch into a job failing for an unrelated reason.
- **Correction to the 0.6.22 entry below:** that release claimed the loop was changed from `continue`
  to `break`. The patch's anchor did not match and the change never landed; the code kept `continue`.
  The backoff above is the real fix for the behaviour that entry described.
- Evidence: `tests/_hub_dispatch_proof.py` - the `_PROOF` program from `tests/test_hub_jobs.py`,
  extracted so a test can sit beside its packaged-Hub fixture - now runs **11 tests**: the original ten
  plus `DispatchPersistenceRollbackTests`, which asserts the Mac is released, the item is `queued`, the
  attempt is given back, `error_code=DISPATCH_PERSIST_FAILED` and `retry_at` is in the future.
  `tests/test_hub_dispatch_proof_gate.py` runs it in its own process (its fixtures set `STUDIOHUB_*` at
  import and would otherwise rebind the data directory for every module loaded after it).

## 0.6.27 — 2026-09-19 · picker families accept the field every client sends

- **`_mlx_kwargs_voice_picker` read only `params["voice"]`**, while the app, the gateway and the kit
  smoke all send `preset_speaker` (the Qwen name). Jobs for kokoro-mlx, orpheus, vibevoice and
  voxtral-tts failed with "needs a preset voice" while carrying a valid voice under the other name -
  which is why three working models looked unable to run at all. The worker now accepts either, at
  the place that knows the difference; the family-native field still wins when both are present.
- Found by the adoption smoke test (`qualification/smoke_new_models.py`): with the correct field,
  orpheus, VibeVoice and Voxtral produced artifacts on the first attempt. Kokoro still fails for an
  unrelated reason - its phonemizer needs `espeak-ng`, which is not on this Mac nor bundled (the app
  ships `ffmpeg` only) - and F5-TTS cannot run because its engine is not packaged at all.
- Evidence: `tests/test_preset_voice_field_alias.py` (4 checks) - the alias is accepted, the native
  field wins, a missing voice still refuses with the actionable message, and an unknown VibeVoice
  voice is still rejected.

## 0.6.26 — 2026-09-19 · package rows state model vs dependency

- **`/api/hub/model-packages` rows gained `type` (`model` | `dependency`) and `dependencies`.** A
  codec (`snac_24khz`), a tokenizer (`S3TokenizerV2`) and a real model all reported the same
  `engine=voice-mlx`, `state=files_verified`, `runtime_state=ready`, so a consumer reading that list
  could not tell them apart - and the gateway published five companions as clone-capable voice
  models to Story Studio as a result. `_dependency_package_ids(catalog)` collects every package that
  appears in another manifest's `dependencies`, and each row now states which it is, so no consumer
  has to infer it from membership of some other list.
- Evidence: `tests/test_hub_jobs.py` + `tests/test_model_packages.py` (42 tests) pass.

## 0.6.25 — 2026-09-19 · manifests for the 14 adopted cache models

- **19 manifests generated from the cache** by `qualification/generate_manifests_from_cache.py`:
  the 11 staged models that fit this 16 GB Mac plus the three giants (echo-tts-base 24 GB,
  fish-audio-s2-pro-8bit 24 GB, -bf16 32 GB), and the five companions those families require
  (`s3tokenizerv2`, `snac-24khz`, `fish-s1-dac-min`, `moss-audio-tokenizer-nano`, `umt5-base`).
  `dependencies` is what `model_packages.preflight` checks, which is why a MOSS-TTS import stayed
  `partial` until its audio tokenizer was registered.
- File selection honours the catalogue's `ignore_patterns`: F5-TTS is 1.35 GB of the 2.70 GB present,
  because the alternate checkpoints stay out. Total 39 GB, all already on disk — no downloads.
- Memory floors come from the catalogue's declared `min_unified_memory_gb` and are written to
  `qualification.minimum_unified_memory_gb`, the same field `copy_models_for_this_mac.py` reads, so
  the drive kit copies the giants only onto a Mac that can run them.
- Note on the import: the flat-folder importer refuses symlinked sources, and HuggingFace snapshots
  are symlink trees into `blobs/` (some nested or broken in that old cache), so all 19 imports were
  refused atomically and the library stayed at 15 packages. The link-based route the gateway used
  (`POST /api/imports`, `mode: "link"`) is the one that handles this layout.

## 0.6.24 — 2026-09-19 · the storage families and the memory verdict reach the app

- **The app now reads `/studio/voice/api/model-storage`** (the payload the worker has always served,
  which no part of the native app rendered) and shows a **Storage cache** line above the model split:
  the family count, how many cannot run on this Mac, and one warning per family naming the declared
  floor and this Mac's memory. It sits above the split with the other notes, so it reads as context
  for what is installed.
- The fetch is `try?`-contained like the rest of the refresh since 0.6.18: a Studio that does not
  answer leaves the list empty and cannot disturb models, jobs or connections.
- Evidence: the native fixture serves a two-family storage payload and the API contract test asserts
  both families decode and that exactly one is flagged, carrying both numbers (`required_gb: 32`,
  `this_mac_gb: 16`). Build and both native gates pass. Never copied from a plan: the verdict itself
  was verified live against the real endpoint in 0.6.23 (Echo-TTS 24 GB, Fish Audio S2 Pro 32 GB).

## 0.6.23 — 2026-09-19 · the storage page reports a memory verdict per family

- **`model_storage.inventory()` now attaches `memory: {required_gb, this_mac_gb, runs_here}` to each
  family**, joining the family's model items to the catalogue's declared `min_unified_memory_gb`. The
  floor belongs to the model, so the family takes the highest among its models; companions contribute
  none, and a family whose entries declare no floor reports `null` rather than a guess.
  Measured on this 16 GB Mac: `fish-audio-s2-pro-bf16` (32 GB), `fish-audio-s2-pro-8bit` (24 GB) and
  `echo-tts-base` (24 GB) are flagged `runs_here: false`.
- **Two bugs caught while landing it, both by the tests being wrong rather than the code:**
  (1) the first test called `inventory()` from the repository, where the cache resolves elsewhere, so
  it exercised **zero groups** and passed without touching the new code; (2) `_tts_models()` returns
  `ModelEntry` dataclasses, so the first implementation's `.get("min_unified_memory_gb")` would have
  raised `AttributeError` on every real request — invisible to that vacuous test. The join is now a
  unit-testable `_family_memory(items, tts_models, this_mac)` that reads dataclasses or mappings, and
  the tests drive it with fakes (6 checks that fail against the broken version), plus a live check
  that skips honestly when the process can see no cache.

## 0.6.22 — 2026-09-19 · a rolled-back dispatch retried the same Studio at once

- After the rollback added in 0.6.19, the loop `continue`d to the next candidate Studio - with a
  single Studio that meant retrying the machine whose write had just failed, in the same pass. The
  trace shows what that produced: the write fails, the Mac is released, the item is requeued, and then
  a second attempt runs the job and fails for an unrelated reason (`'input_path'`), which is exactly
  the "stuck job that fails for the wrong reason" shape this work set out to remove. It `break`s now,
  leaving the batch for the next pass.
- The rollback itself is verified by an instrumented live run of the real dispatch path
  (`/tmp/diag2.py`), which prints: `acquire_external_machine -> True`, `_save called: state=running`,
  `release_external_machine`, i.e. the Mac is given back and the item requeued rather than left
  running with no task. Its automated regression test is not landed yet (see the changelog entry for
  0.6.19): the harness needs the packaged modality, the monitor binding, a peer snapshot, a Studio
  that advertises the model, and the monitor's client, all of which are now written down.

## 0.6.21 — 2026-09-19 · unguarded bookkeeping writes aborted the dispatch pass

- **Five `_save(batch)` calls in the transcription dispatch loop were unwrapped** - the governor-note
  writes and the other bookkeeping the scheduler records as it goes. A raising `_save` there
  propagated out of `dispatch_once`, so a full disk or an unreadable database did not merely lose a
  note: the pass ended before any other job could be dispatched, and the failure appeared as a
  scheduler error rather than as a write failure. New `_save_quietly(batch) -> bool` logs the failure
  and returns False; the five bookkeeping writes use it. The lease-path write keeps its own
  try/except with the rollback from 0.6.19, because that one has state to undo.
- Found by instrumenting the reproduction of the 0.6.19 rollback: the trace showed `dispatch_once`
  raising from line 771 (a governor-note write) rather than reaching the lease at all.
- Evidence: `tests/test_dispatch_bookkeeping_writes.py` (3 checks) - a failing write is reported and
  survived, a good write still reports success, and every bookkeeping write in the loop goes through
  the helper. `tests/test_hub_jobs.py` still passes.

## 0.6.20 — 2026-09-19 · a Mac's health came from one arbitrary Studio

- **`fleet_overview` iterated `monitor.registry`, skipped machines it had already seen, and reported
  the first studio's status as the machine's.** A Mac running Voice and Image with Image down read
  "up" if Voice came first, and read "down" while Voice was healthy if it did not. New module-level
  `machine_health(states)` summarises every Studio on that machine: `up` when all answer, `degraded`
  when some do, `down` only when none do, `unknown` when no status is known yet (a Hub that has not
  run its first poll must not report a Mac as sick), with a sentence naming the Studios that are not
  answering; the row also carries `studios: {total, up}`.
- Evidence: `tests/test_machine_health_summary.py` (6 checks) covers all five outcomes, that a Studio
  still starting does not make the Mac look down, and that the route no longer reads a single
  studio's status.

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
