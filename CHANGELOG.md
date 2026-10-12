# Changelog

## 0.6.167 — 2026-10-12 · Staged voice copies last 4 days, so Gateway's 3-day reuse holds

- Owner decision 2026-10-11: a staged voice clip is reused for 3 days instead of being re-sent to every Mac each time. Gateway 0.2.168 asks the Hub to keep it 84 h (72 h reuse + 12 h headroom), but the Hub capped every staged copy at 48 h, so Gateway stopped reusing it after about 36 h.
- `execution_assets.MAX_TTL_SECONDS` 48 h → 96 h. The default (24 h) and the cleanup are unchanged: `cleanup_expired` still deletes each copy when its own `expires_at` passes, from the storage sweep.
- Test: `test_hub_voice_references` stages with an 84 h TTL and checks the copy keeps at least that (it got 48 h before).

## 0.6.166 — 2026-10-11 · Vision Studio: depth maps, subjects and outlines for Story's effects

- Owner decisions 2026-10-11 (effects plan E2–E4): the depth model and Florence-2 are approved, run in our own apps on the fleet and are reached through Gateway KH (0.2.167 `POST /v1/vision/analyze`). New worker `vision` (`components/vision`, `packaging/vision_worker.py`), engine `vision-mlx`, modality `vision`, operation `image.analysis`, registered where 0.6.165 registered Embeddings. It runs on the shared Voice runtime and adds no package; no PyTorch runs in the Hub.
- Models (three manifests, data only): **Depth Anything V2 Small** (`depth-anything/Depth-Anything-V2-Small-hf` @ `5426e4f0`, 99 MB, Apache-2.0; Base/Large are CC-BY-NC and are not shipped), **Florence-2 base ft** (`florence-community/Florence-2-base-ft` @ `0b03b6f1`, 469 MB, MIT, the default) and **Florence-2 large ft** (`florence-community/Florence-2-large-ft` @ `26b734a5`, 1.55 GB, MIT, not installed by default: install its package and name it, no release needed). Safetensors only; no pickle is downloaded.
- `depth.py`: DINOv2-S/14 + DPT in plain MLX, the publisher's preprocessing (keep aspect, short side 518, multiple of 14). Matches transformers' `DepthAnythingForDepthEstimation` within 1e-4 relative on 7 real scene stills, and on random weights offline (`tests/test_vision_worker.py`).
- `florence.py`: DaViT + projector + BART in plain MLX with Hugging Face's beam search (3 beams, no repeated 3-grams, forced BOS/EOS) and the publisher's `<loc_*>` parsing. float32 throughout (float16 drifted ~20% at the image encoder and changed some outlines). Token-identical with transformers' `Florence2ForConditionalGeneration` on 21 of 21 real prompts (base) and 8 of 9 (large; the ninth parses to the same labels and boxes); boxes within half a bin (0.0005), outline IoU 0.98–1.00.
- `POST /v1/vision/analyze` on the worker: `{tasks: [depth|subject|points|segments], image: {b64, mime} | {url}, model?: id | [ids], text?, regions?}` → depth as an 8-bit PNG (255 = nearest, long side ≤ 518) with its p95−p5 range; subject `{box [x,y,w,h], label, score, source: grounding|detect}`; up to 8 points; one 8-bit outline PNG per region with coverage and score, plus the picture's detailed caption. Florence-2's referring segmentation outlines something for any word, present or not ("sky" on an indoor still life covered 86%), so a region is outlined only when that caption (~0.2 s) names it (`REGION_WORDS`); otherwise it is `null`. Coordinates are 0..1. The picture is encoded once for every Florence task; requests run one at a time on one MLX thread. Memory policy is Chat's.
- Peer route `/studio/vision/v1/vision/analyze` and a controller relay to a chosen node, `POST /api/hub/fleet/nodes/{id}/vision/analyze` (the Gateway's "analyse on the Mac that made the picture" rule). Both are admitted like other work (paused Mac, Studio, Lite Mode, every named model installed) and **take no machine lease** (`_PEER_UNLEASED_PATHS`). `ponytail:` no queue; add Chat's bounded wait if analysis ever contends with production.

## 0.6.165 — 2026-10-11 · Embeddings Studio: local text embeddings for Story's meaning check

- Owner decision 2026-10-11 (ideas plan Q1): embeddings run in our own apps, not Ollama. New worker `embeddings` (`components/embeddings`, `packaging/embeddings_worker.py`), engine `embed-mlx`, modality `embeddings`, registered in `vocabulary.WORKER_FOR_MODALITY`, `_ENGINE_ROUTE`, `model_packages.ENGINES`, `hub_worker.COMPONENT_WORKERS` and `stage_voice.COMPONENT_STAGERS`. It runs on the shared Voice runtime and adds no package.
- Model: **BGE small en v1.5 MLX bf16** (`mlx-community/bge-small-en-v1.5-bf16` @ `0e415031`, package `bge-small-en-v1-5-bf16`, 10 files / 67,702,966 bytes, MIT). A BERT encoder written in plain MLX (`encoder.py`); it loads the publisher's safetensors by name, keeps float32 in memory, and pools and normalises as the manifest's `defaults` say (`cls`, normalised, 384 dimensions, 512 tokens). It matches Hugging Face `BertModel` to 5e-7 on the real checkpoint and to 1e-5 on random weights (`tests/test_embeddings_worker.py`, offline).
- Why this model: nomic-embed-text-v1.5 has no MLX build, its own weights are 547 MB fp32 (over the approved ~270 MB) and it needs a custom architecture with remote code. BERT is the architecture the bundled MLX runs most simply, and BGE small v1.5 scores clearly above all-MiniLM-L6-v2 on MTEB.
- `POST /v1/embeddings` on the worker (OpenAI shape: `{model, input}` → `{data:[{embedding, index}], model, usage}`); up to 2,048 inputs, each under 512 tokens (a longer one is refused by index, never cut). `[M]` M2 Max 32 GB: load 0.07 s, 1,000 short texts 0.24–0.31 s, peak MLX 486 MB, max RSS 155 MB. Memory policy is Chat's (`components/embeddings/app/backend/memory_policy.py` re-exports it).
- Peer surface `/studio/embeddings/v1/embeddings` (fleet credential allowed). It is admitted like other work (paused Mac, paused Studio, Lite Mode, model readiness, update drain) but **takes no machine lease**, so it runs beside a narration or image job instead of waiting behind it (`standalone._PEER_UNLEASED_PATHS`). `ponytail:` no queue; add Chat's bounded wait if batches ever contend.
- Downloaded once into this Mac's library (owner-approved). Other Macs get it only when it is installed on them.

## 0.6.164 — 2026-10-09 · An empty or broken narration file is a failed job, not a finished one

- On 10-09 at 08:04 a narration job on studio-mac-1504 (Hub batch `ebcfd92c1a`) produced an empty (0-byte) m4a. The Hub noticed the file could not be checked, but still marked the job finished, following the rule "never retry a completed generation", and handed out the empty file with no size or checksum.
- Now an empty file, or one no player could open, is a failed try and not a finished job. The job then retries and moves to another Mac like any other failure. The error code says which: `EMPTY_OUTPUT` for an empty file, `INVALID_OUTPUT` for one that cannot be opened. That covers an m4a with no `moov` box or cut off partway, a WAV with no audio or cut off, and bytes that are not the type the Mac said it sent (for example an HTML error page).
- A file that plays but whose extra check could not finish still counts as finished, exactly as before. Examples are a size or checksum that differs from what the Mac reported, or a Mac that did not report the duration. It is still marked unchecked and not billed.
- Every finished narration file now comes with its size and SHA-256, including the unchecked ones. The Hub uses the bytes it downloaded. When the download could not run, it uses the size and checksum the Mac reported. Before, only the unchecked files were missing these, so the Hub served them with no `Content-Length` and no `X-Content-SHA256`.
- The m4a encoder now checks the file it writes: it must start with `ftyp`, contain `moov`, and have no box cut off. Before, it only checked the first box, and the length check is skipped when `ffprobe` cannot run. A broken encode now delivers the WAV instead, as an encode error already did.
- When this happens, the Hub log says which job and which Mac it was, and how many bytes the Mac reported.

## 0.6.163 — 2026-10-09 · Qwen thumbnails go to the fastest free Mac

- Owner decision: "Prefer fast Macs for Qwen." Inside a site, a Qwen Image job (any model whose id contains `qwen-image`) now goes to the Mac expected to finish it soonest. It used to go to the smallest Mac, which on sites 1100–1400 often meant an M1 at about 949 s while the M4 would take about 605 s.
- How long a Mac takes is its own median for that model from this Hub's finished jobs. Without that, it is the median for Macs of the same chip and memory. Without that, it is the 10-08 measurement for its chip: M2 Max 228 s, M4 605 s, M2 812 s, M1 949 s, and 949 s for an unknown chip. These are the same rule and numbers Gateway 0.2.156 uses to choose the site.
- A slower free Mac takes a Qwen job only if it would finish before a busy faster Mac would, after that Mac's current job and the Qwen jobs queued ahead of this one. Example: when This Mac is busy and nothing else is queued, an idle M1 leaves the job for it (342 s against 949 s). With three Qwen jobs queued ahead, the M1 takes it.
- While a job waits like this, the job says "Waiting for a faster Mac". The waiting Mac stays free for its other functions.
- Owner settings still win. A Mac with Image switched off gets no Image work, and a Mac that puts another function above Image is never waited for. Other models are placed exactly as before.

## 0.6.162 — 2026-10-09 · Three fixes from the overnight review

- A cloned-voice job no longer fails when the Mac it was sent to is busy with other work. That Mac answers "reserved for other work" before it even looks at the voice reference. The job now stays queued, skips that Mac for a few seconds, and doesn't use up one of its tries. This is how the rest of a job already handled a busy Mac. Overnight this caused 276 failed tries on 252 jobs, mostly at sites 1600 and 1500.
- The Breeze check that the audio says its text no longer fails a correct reading over how a word is written. "Eighteen-eighties" now matches Whisper's "1880s", "the eighties" matches "80s", and "nineteen fifty-two" matches "1952". "Washhouses" matches "wash houses", and the other way round. Joined and split words are only compared after the normal word match, and a wrong sentence still fails. The EP0028 sentence that failed 12 times on 3 Macs now passes.
- When a Mac doesn't have enough free memory to start a job and another Mac can run the same model, the job is handed back after 1 minute instead of 4. When this Mac is the only one that can run it, the job still waits 4 minutes.

## 0.6.161 — 2026-10-08 · Qwen Image 2.1 uses 20 steps by default

- Qwen Image 2.1 now uses 20 steps by default (owner's choice after a thumbnail test; 16 was acceptable, 24 looked best). This covers both packages, the regular one and Low-RAM. The allowed range is still 1–50.
- A Mac where someone saved its own default steps for this model keeps that number. A request that names its own steps also keeps them.
- Each image job's time limit is still based on the steps the job actually runs, so a 20-step job gets a longer limit than a 16-step one.
- The two Qwen manifests were regenerated with the capture tool. Only their `defaults` changed. The files and install identity are the same, so installed copies don't need to be verified again.
- The kit readme no longer lists the retired SSD test runners (`RUN-16GB-FISH`, `RUN-8GB-TTS`).

## 0.6.160 — 2026-10-08 · Every model download shows on the Jobs page, in its own tab, with Cancel

- The app now lists every model download this Mac's Hub is running, not only the ones started from the app. A download started from the Gateway's fleet install (for example Qwen Image 2.1 Low-RAM) used to read "No model downloads" while it ran; it now shows its progress and a Cancel button on the Jobs page, the Models page and the Overview.
- Model downloads have their own tab on the Jobs page, after Chat, so they no longer sit at the bottom of All. The tab shows how many are running, for example "Downloads (1)".
- Cancel stops the download only. The part already downloaded stays in the library's staging folder, and the next install picks up from there.
- The Hub has a new read, `GET /api/hub/model-downloads`, that lists every download on this Mac, newest first. It needs the owner's token; the Fleet token cannot read it.
- The list shows this Mac's downloads only. A controller does not list its nodes' downloads; the Gateway's fleet install page shows those.

## 0.6.159 — 2026-10-08 · Image job time limits follow step count and Mac speed

- Each image job now gets its own time limit: 120 s plus 1.5 × steps × how long a step takes on the Mac running it, between 15 minutes and 2 hours. More steps or a slower Mac get more time; a quick job on a fast Mac is still caught in 15 minutes.
- "How long a step takes" is the Mac's own median from its last 20 finished jobs of that model (3 or more). Without that history it is the stored measurement for that chip (for example Qwen Image 2.1 Low-RAM: 13.3 s on an M2 Max, 33.6 s on an M4), and for a chip nobody has measured, such as an M1, twice the model's slowest measurement.
- The Mac running the job states the limit on the job. The Hub shows it on the batch item, the regular Qwen engine stops at it, and Gateway 0.2.147 waits for it. Gateway still waits at least 30 minutes for every image job, as before.
- Qwen Image 2.1 Low-RAM now has a real 16 GB receipt: the owner's 16 GiB M4 finished four 16-step cases in 463–538 s, peak 6.0 GB, starting with 9.3–10.6 GB free, no swap. The tier, the 8.0 GB start floor and the published notes cite it.
- The model benchmarks page no longer points at the SSD test runners, which were retired today.

## 0.6.158 — 2026-10-08 · Qwen Image 2.1 Low-RAM is the fleet's Qwen; Qwen Image 2.1 runs on 24 GB

- Qwen Image 2.1 Low-RAM is now the fleet's Qwen (16 GB and up); the regular Qwen Image 2.1 is qualified on 24 GB and up and can be installed on request.
- Regular Qwen Image 2.1 (MLX affine 4-bit, native mlx-serve) is qualified on 24 GB from the owner's physical 24 GiB M4 run: four 16-step cases in 440–500 s, peak 9.8 GB, normal memory pressure, no swap. It stays on the SSD and is not copied to Macs by the model copy step; install it from the Gateway when wanted.
- Qwen Image 2.1 Low-RAM (MLX 4-bit) is qualified on 16 GB and up by the owner's decision. Its evidence is the 32 GiB run (peak 5.85 GB); no 16 GB run is recorded. 8 GB is not qualified. The copy step now puts it on every 16 GB and larger Mac.
- A Qwen job starts when the Mac has its measured peak plus 2 GB free: 12.5 GB for regular, 8.0 GB for Low-RAM. Idle models on that Mac are released first. Gemma 4 26B stays 32 GB only.
- Image jobs may now run up to 30 minutes (was 15) before the Hub stops waiting, and a regular Qwen job's own limit grows with its step count, so a 40- or 50-step job on a 24 GB Mac is no longer cut off.
- This is the first fleet release of 0.6.155–0.6.157: Gemma 4 qualification on 32 GiB, per-Mac work choices, and ranked job priorities.

## 0.6.157 — 2026-10-08 · Ranked work priorities on each Mac

- Settings > Work on this Mac replaces Prefer LLM jobs with No preference or Custom order. Move enabled functions up or down using accessible buttons; saved disabled-function ranks are retained. Existing LLM-first settings migrate with other functions tied.
- Finish current work, then choose the highest-ranked eligible queued function; equal ranks use oldest eligible work first. Disabled, unqualified or memory-blocked preferred work does not reserve otherwise usable capacity. Existing render precedence and physical execution leases remain enforced.
- Each Mac publishes its order and supports authenticated Gateway controls through its controller. Changing priority never changes enabled functions or manual pauses.
- Bounded synchronous Chat waiting is independent of rank, including No preference. Cancellation and uncertain-completion recovery retain their existing safeguards.

## 0.6.156 — 2026-10-08 · Per-Mac work choices and LLM priority

- Choose which registered functions this Mac accepts in Settings > Work on this Mac. Existing local, Gateway and fleet pauses stay in place; other Macs keep their choices.
- Prefer LLM jobs finishes current work, then runs eligible queued LLM work before new TTS or image jobs. Other enabled work uses idle time when no LLM is waiting.
- Gateway Chat can wait within the Hub's bounded allowance. Cancelled waiting requests cannot start later, and uncertain completions stay held for recovery instead of being submitted again.
- Preview and save verified JPEG alongside PNG, with the correct export format for older results too. Image preview errors stay on Image with a retry action and clear after a new result or successful retry.

## 0.6.155 — 2026-10-07 · Gemma 4 qualification for the 32 GiB Mac

- Qualify the pinned Gemma 4 26B-A4B QAT MLX 4-bit checkpoint on the measured 32 GiB M2 Max only. The owner accepted the observed 985/800/805-word chapters and Story Studio's verified numeric parsing of Chapter 8/08. The original strict 1,200-word test remains recorded as failed; no lower RAM tier is qualified.
- Use a curated 32,768-token combined input/reserved-output budget on this deployment. Keep the model's stated 262,144-token architecture separate, and the existing 16,384-token output allowance labelled as a worker setting rather than a measured 16k completion.
- Retain exact publisher revisions/checksums and two-run evidence, including the successful 50-scene response under the 4,000-character cap. Qwen3.5-9B stays the default; Nemotron and Qwen3.6 remain pending.
- Chat catalog qualification now reads the selected model's evidence instead of attributing the older Qwen proof to every allowed model. The local installer can preserve the previous app in the project's `_trash` using its archive-path option.


## 0.6.154 — 2026-10-07 · Restart recovery keeps a valid execution until its result is durable

- An abandoned original finalizes on a private candidate. The broker rechecks the competing
  result and cancellation, durably saves the winning batch, then marks the replacement
  superseded and requests its cancellation. Metadata or ledger failure leaves the replacement
  execution intact; a later result cannot overwrite the first finalized winner.
- Shared finalization keeps artifact metadata private across awaits and checks ownership before
  recording an asset. Receipt identity, hash verification and callback reservation remain enforced.
- Controller trust, update notices and App updates use App Update Key display labels. Identifiers,
  endpoints, persisted fields and signature/trust behavior are unchanged.

API key is now called Gateway API Key; the release signing key is now the App Update Key

Focused fault/race regression: five cases failed on the 0.6.152 base, then the 30-case free-work
reroute suite passed after repair. The expanded 34-case acceptance suite and a 28-test replay of
the previously failing paths passed before the 0.6.153 Chat changes were merged. Complete combined
0.6.154 source gates, native build, dark/light fixture review, packaging and installation remain.

## 0.6.153 — 2026-10-07 · Chat model selection and measured processing progress

- Chat reads every immutable `chat-mlx` manifest, preserving the Qwen3.5-9B MLX 4-bit default and safe single-model legacy catalogs. One global session lock owns one resident model; switching releases the old model before loading another, and failure/cancellation returns the session safely.
- Nemotron 3.5 Lightning 30B-A3B MLX 4-bit, Qwen3.6 35B-A3B MLX 4-bit, and Gemma 4 26B-A4B QAT MLX 4-bit have reproducible pinned runtime manifests. All three carry `production_allowed:false`, unqualified status and unknown RAM/tested-context values. No inference, throughput, quality or physical tier claim is made.
- Explicit pending admission is enforced at the Hub, peer and worker boundaries. Legacy manifests retain their established policy. Normal kit model selection skips pending packages and preserves existing stock; a parent cannot pull a pending dependency into ordinary installation.
- Chat reports request-scoped Waiting, Loading, Processing prompt and Generating states, exact MLX prompt/decode statistics and separate repair attempts. Hub history preserves closed, finite telemetry; the native app displays worker-stated rates and labels completion time separately. Unknown counts stay null and actual zero remains zero.
- Native Chat polls an owner-authenticated request-scoped activity route. Preflight errors close activity; queued requests do not claim to be loading. Structured JSON streams buffer until validation and one repair have finished, and invalid output fails before HTTP200. Actual final finish reasons are preserved.
- Staging copies the tracked shared cache helper to Chat's package import path as well as the existing Voice layout; missing/untracked helpers and unresolved imports still refuse the build.
- Chat reuses the configured shared MLX cache limit of 512,000,000 bytes before model loading; the standalone qualification probe uses the same setting. This is configuration, not measured memory eligibility.
- Offline proofs use fake engines, tokenizer responses and synthetic preserved fixtures. Physical inference remains held until the owner closes other work, restarts and explicitly starts one model at a time. Candidate stock/hash receipts and qualification tooling are retained in `_orchestra/audit-20261007/moe-qualification/` outside the checkout.

## 0.6.152 — 2026-10-07 · Updates no longer stall while a release is still arriving

Branch `fix/release-stage-race` off 0.6.151 2e38cbe. Found in the 0.6.151 rollout: a Gateway delta push stores the
manifest (advertising the release) ~110 s before its ~33 blobs. A controller's app that checked inside that window got
`missing_blob` from the stage, fell back to the disk image (404: a manifest-pushed controller holds none), set
`autoUpdateFailedBuild`, and never retried until quit; its nodes, which mirror only what their controller runs, stalled too.
- **Controller stage waits for its blobs** (`release_sync._await_blobs`): while `app_release.missing_blobs()` is non-empty
  (and no disk image is held) the stage reports `phase: downloading` with byte progress, up to `BLOB_WAIT_S` (15 min); still
  missing → `state: "not_ready"` (retryable), never `fallback`. A re-push during the wait ends it as `not_ready` too.
- **Node blob fetch keeps asking** (`release_sync._remote_blob`): a controller that cannot supply a blob (still receiving
  it, or restarting into its own update) is asked again every `BLOB_WAIT_POLL_S` for up to `BLOB_WAIT_S`, then `not_ready`
  instead of the disk-image relay a node cannot use.
- **App** (`StudioAPI.installOfferedRelease`, both roles): `not_ready` is not a failure (no image download, no failure
  memory, the drain ends, the next tick asks again). An automatic failure is remembered by descriptor **signature** (a re-push
  is tried at once) and retried after `autoUpdateFailureRetry` (30 min) instead of never.
- **Fleet report**: the app sends `last_update_error {phase, reason, build, at}` (phase `verifying|staging|downloading|
  installing`, one-line reason) with `POST /api/hub/startup/sync`; `startup_control` keeps it cleaned and publishes it in
  the `startup` object (resources, node rows, roster). Gateway shows it once it adds the key to `fleet._STARTUP_KEYS`.
- Tests: `tests/hub_regression/cases/test_release_delta_routes.py` (controller waits then builds; not-ready timeout;
  node keeps asking then builds; node not-ready), `tests/test_remote_startup.py` (report cleaned; node row carries it),
  `tests/native/api_main.swift` (retry after 30 min and on re-push for node and controller roles; not_ready case; report).

## 0.6.151 — 2026-10-07 · Breeze narration speaks one sentence per section

Branch `feat/breeze-sentence-sections` off 0.6.150 fa79767 (keeps 0.6.150's Qwen Image 2.1 backends and DMG packaging).
In 7 days of fleet traffic Breeze rejected and re-rendered 13.6% of section attempts (about 25% of its machine time), rising
with the sentences in a section (1: 6.2%, 2: 15.3%, 3: 23.2%, 4: 25.4%): Breeze pauses at a sentence break and does not come
back. The owner listened to the A/B (`_orchestra/audit-20261001/breeze-ab/`) and chose one sentence per section.
- **`long_form_policy` / `catalog.Family` gain `max_sentences_per_section`**: Breeze 1, every other family None (unchanged).
  `_sentence_safe_text_chunks(..., max_sentences=)` honours it; the 250-character cap still splits an over-long sentence.
- **Tiny sentences**: with a sentence cap, a sentence under 30 spoken characters (`_SENTENCE_SECTION_MIN_SPOKEN_CHARACTERS`)
  joins a neighbour in its own paragraph (forward when it opens the paragraph), so the 1.5 s paragraph pause stays put. 30 is
  about 6 words, where the text check (max(2 words, 15%) unheard) can still reject a section; 14-day fleet Breeze sections
  of 15-39 characters were 0/19 rejected, and 2.3% of real Breeze sentences are under 30. Other families keep 12.
- Joins are unchanged and text-led: 1.0 s between sentences, 1.5 s at a paragraph, a request's own pauses win. The section
  timeline (Story's script-timed subtitles) gets one row per sentence; a 1,500-character Story request is ~15 sections.
- **Per-section Whisper cost**: with no `language` asked, Whisper auto-detected per section, which runs its encoder twice.
  A section after an accepted one is now read in the language Whisper detected there (`GenerationJob.section_language`,
  in memory only); a re-render detects again. Khmer sections keep their forced `km`.
- Tests: `components/voice/app/tests/test_breeze_sentence_sections.py` (one sentence per section for Breeze only, tiny merge
  in-paragraph and at 30, long-sentence split, pauses at joins, timeline rows, Story-size request, language reuse/retry).

## 0.6.150 — 2026-10-06 · Qwen Image 2.1 packages and physical SSD qualification

- Adds ordinary selectable Qwen Image 2.1 affine 4-bit packages: pinned mlx-serve 26.10.1 native
  backend and separate mflux q4 low-memory backend. RAM admission remains conservative at 32 GB
  while physical 24/16 GB SSD receipts are pending. Only the native package requires macOS 26.2;
  the existing Image models retain macOS 14 support. No language or thumbnail-only restriction.
- The native leaf is private, loopback-only, bounded, cancelled and reaped. Hub restarts clean the
  worker's verified isolated process group even after leader exit. Low-Qwen restores cache settings
  after success/failure and correctly reports ignored guidance when no negative prompt is allowed.
- SSD commands check exact runtime inventory and content digest, clone only the candidate package
  into private state, refuse overlapping workers, and require complete footprint/pressure/swap/
  admission-headroom measurements before a candidate pass. Peak swap growth determines pressure.
- Model updates archive interrupted copies, replaced folders and old revisions rather than deleting
  them. Failed app stages and native diagnostics remain recoverable; the tracked DMG builder
  preserves previous installers and staging.
- Focused regression checks cover lifecycle, cache restoration, request contract, package hashes,
  qualification fail-closed behavior and model-copy recovery. Packaged and release verification
  records are retained under `_orchestra/audit-20261006/qwen-release-0150`.

## 0.6.149 — 2026-10-05 · A Hub restart re-runs the work it interrupted instead of parking it for recovery

Branch `fix/restart-lost-free-work` off 0.6.148 a7fd296. On 2026-10-05 22:14:56 the Terranash-1600 controller's Hub process restarted
(the app itself did not stop, so `last_stop` stayed at its 18:33 update). `restore_batches` fenced its five running Z-Image items
`uncertain` although their nodes had finished all five (22:12:56-22:15:42): the Gateway parked the jobs as "Needs recovery"
(UNKNOWN_OUTCOME) and `busy_machines()` held those five idle Macs until the 30-minute uncertain sweep, while new work queued.
- **Running free work is re-run after a Hub restart, on any capable Mac (owner, 2026-10-05; extends the 2026-10-03 worker/node rule).**
  `broker._rerun_after_restart` requeues every running Image, Music, Video and Voice item of a batch that is not being cancelled -
  this Mac's own worker or a node alike - and no Mac is held for it. Voice takes a fresh client request id, as `_abandon_attempt` does.
  The original is watched once its studio is back in the registry (`_watch_restored_attempts` -> `_watch_abandoned_attempt`, from the
  dispatch loop): a finished original is adopted (the re-run, if one started, is cancelled); one still running is told to stop; one that
  finishes after the re-run is ignored. The interrupted try stays spent: on its last of MAX_TRIES a restart ends the item `error` /
  `HUB_RESTARTED` (published, so the Gateway reads a known failure) instead of a fence.
- **Transcription likewise**: a packaged Hub re-queues a running Subtitle item after a restart (it was fenced `uncertain` with
  `manual_action_required`); on its last try it ends `error` / `HUB_RESTARTED`.
- Unchanged: an item whose batch is being cancelled keeps the cautious fence; cloud work never reaches the Hub (refused at the door).
- Tests: `hub_regression/cases/test_free_work_reroute.py` (restart re-runs every modality on this Mac and on a node and holds no Mac;
  a finished original is adopted with no POST; original-first and re-run-first both settle on the first result; two restarts in a
  row re-run, a third ends `HUB_RESTARTED`; a cancel in flight stays fenced); `test_voice_durability` (the original voice job is adopted
  by its own request id after a restart); the restart expectations in `test_image_uncertainty`, `test_music_durability`,
  `test_hub_jobs`/`_hub_dispatch_proof` (Voice, Subtitle) and `test_startup_recovery` now expect a re-run.

## 0.6.148 — 2026-10-05 · Why a LAN copy fell back is recorded; an update no longer marks finished downloads interrupted

Branch `fix/lan-share-followups` off 0.6.147 85a1344 (findings: `_orchestra/audit-20261001/lan-share/ROLLOUT-20261005.md`, Hub 4 and 5).
- **Copying a model between Macs now records why a copy fell back to the internet.** `model_packages._download_file` swallowed the peer's
  error when it dropped the peer (both Shops Lite iMacs fell back on 2026-10-05 with no reason anywhere). It now prints one line the moment
  it happens - `LAN copy failed, using the internet (internet|mixed): <ErrorType>: <reason>` (share URL masked) - and
  `model_downloads._public` reads it, so a LAN run shows `lan_error` and `source_used: "internet"`/`"mixed"` **while it is still running**
  (a finished run keeps the `source_used` of its closing JSON). Gateway 0.2.126 uses it to relabel the row and count it as the network's
  one internet download. `_source_used` reads the JSON after that line.
- **Finished downloads are no longer marked interrupted by an update.** `model_downloads.shutdown()` marked every tracked run `interrupted`
  with the stop's exit code, including runs whose downloader had already exited 0 (1301/1302 read `interrupted, exit_code 0` with the model
  cached). A child that has already exited is now recorded as it ended (`done`/`error`, with `source_used`); only live ones are stopped.
  `_stop_owned`'s final group SIGKILL also tolerates macOS's EPERM for a group holding only a zombie (it raised out of `shutdown()`,
  leaving later runs unstopped; seen 1 in 6 runs of the lifecycle test once a downloader child had started).
- Tests: `test_model_downloads.py` (a finished run stays `done` through shutdown), `test_model_packages.py`
  (`test_why_a_copy_fell_back_reaches_the_running_run`).

## 0.6.147 — 2026-10-05 · Phonon-2 loads its real weights; a test transcription on one chosen Mac

Branch `fix/phonon2-nzrow` off 0.6.146 a58475d.
- **Phonon-2 (optional English transcription) now loads its real weights correctly; still in testing, Whisper stays the default.**
  `transcription._phonon2_dense` read `<block>.nz_row` as each row's nonzero count; the published `phonon-2-mlx` file stores each row's
  start offset in the hi-bit stream (exclusive prefix sum), so every real block was refused ("trit codes disagree with its nonzero table")
  and no Phonon-2 transcription could start on any Mac. The check now compares `cumsum(counts) - counts` (also right for an empty
  matrix). With it, all 697 tensors match the publisher bit for bit (`_orchestra/audit-20261001/phonon2/RESULTS.md`, G0.2). The test
  fixture `_pack_five_value` now emits the shipped form, and a per-row-count `nz_row` is asserted refused. The Subtitle worker loads
  through the same function (`engine.py` -> `load_phonon2`), so it has no second copy to fix.
- **Admin test transcription on one chosen Mac (model qualification).** `POST /api/hub/fleet/nodes/{id|local}/transcribe` (Fleet token,
  multipart `file`, `model`, `language`, `word_timestamps`): on this Mac it is the peer surface's own
  `/studio/transcription/api/transcribe` (same admission, machine reservation and worker re-signing); for a joined node it is relayed to the
  node's own `.../nodes/local/transcribe` by `fleet_nodes.relay_model` (the 0.6.143 install relay, now able to carry a raw body, its own
  timeout of 960 s and its own "needs 0.6.147" refusal). Audited `transcribe_relay` / `transcribe`. Not in `NODE_ALLOWED`.
- **Subtitle's synchronous `/api/transcribe` keeps each segment's `words`** when `word_timestamps` is on (they were measured and then
  dropped); with it off the segment shape is unchanged. Contract: `_orchestra/audit-20261001/INTERFACES-0147.md` (Gateway 0.2.125).
- Tests: `test_transcription_engines.py`, `tests/hub_regression/cases/test_fleet_model_install.py`, `test_subtitle_standalone.py`.

## 0.6.146 — 2026-10-05 · Macs copy models from each other; Phonon-2 to try; narration timeline; longer narration deadline

Release of `release/0.6.146` (= 0.6.145 76a935b + `--no-ff` merges of `feat/lan-model-share` 5aac7dd, `feat/phonon2-asr` b1abff5 and
`fix/breeze-failures` 6bf5646, then `feat/section-timeline` 8b9b0a6). No textual conflicts.
- **Macs on the same network can copy a model from each other instead of each downloading it from the internet (the Gateway picks a
  nearby Mac; every file is checked).** `POST /api/hub/fleet/nodes/{id|local}/model-packages/{pkg}/share` (Fleet token, relayed like the
  0.6.143 install) returns a 30-minute, renewed-on-use, read-only token for the one verified revision; `GET/HEAD
  /api/hub/model-share/{token}/files/{path}` serves only the manifest's files (Range, octet-stream, no gzip, <= 2 transfers per Mac,
  429 beyond, audited). The download route takes `{"source": share}`: each file is tried from the peer, verified against the pinned
  digests exactly as for the internet, and re-fetched from the internet on a mismatch; a peer that fails is dropped for the run. The run
  records `source_used` (`lan` / `internet` / `mixed`). Contract: `_orchestra/audit-20261001/INTERFACES-0146.md`.
- **Phonon-2, a small fast English transcription model, is available to try next to Whisper.** Package `phonon-2-mlx`
  (selcukkubur/Phonon-2-mlx@71936ba3, 5 files, 189,498,804 bytes, unqualified, 8 GB tier, provisional 1.5 GB free floor);
  `transcription.load_phonon2` unpacks it into mlx-audio's ParakeetTDT with numpy (no new dependency). Voice `phonon2` engine and the
  Subtitle `phonon2` family; non-English requests are refused naming Whisper. NeMo-family word timestamps join SentencePiece pieces into
  words. Whisper large-v3 turbo stays the default; the Lite Mode allowlist is unchanged. Catalog count 29.
- **Narration's text check allows a little more mishearing in short sections (a skipped phrase is still caught).** `tts_quality` lets 15% of a
  section's words go unheard (was 10%; measured good >= 0.938, bad <= 0.849); the dropped-clause rule (missing run > 3) is unchanged.
- **Narration now reports where each piece of text sits in the audio, so subtitles can be timed exactly from the script.**
  `section_quality.timeline = [{section, start_s, end_s, text_start, text_end}]`: speech start/end of each section in the joined file
  (tempo-adjusted) and its character span in the request text; the broker passes it through numbers-only.
- **Long narration pieces get 45 minutes before they're stopped.** Voice job deadline floor 2,700 s (was 1,800 s; slowest successful
  1,500-character Breeze part took 1,747 s on an M1 16 GB). Stall detection is unchanged.
- Tests: `tests/hub_regression/cases/test_lan_model_share.py`, `tests/test_model_packages.py`, `test_transcription_engines.py`,
  `test_model_manifest_catalog.py`, `test_subtitle_standalone.py`, `test_tts_quality.py`, `test_hub_job_deadline.py`,
  `test_section_quality_passthrough.py`, `test_long_form_join_edges.py`. `docs/model-benchmarks.md` regenerated for Phonon-2.

## 0.6.145 — 2026-10-04 · Breeze TTS 2 4-bit runs on 8 GB Macs

Release of `release/0.6.145` (= 0.6.144 d14ad78 + the owner's Breeze decision).
- **Breeze TTS 2 4-bit: 8 GB total-memory floor, 3.2 GB free to start** (were 16 GB / 5.6 GB; owner decision 2026-10-04). Evidence:
  `_orchestra/audit-20261001/breeze-8gb/results/TTS 8GB Run 20261004-230549` - 8 GB M1, Hub 0.6.143, 2/2 S2 clips completed, all sections
  accepted, each clip started with 3.31-3.46 GB free, worker peak 4.69 GB, Mac max used 5.2-5.3 GB, min free 1.19-1.32 GB, pressure warning
  (never critical), swap <= +0.54 GB, RTF 4.1, max stall 0.6 s. One source: `memory_admission._FLEET_DEFAULTS` (measured rung of
  `requirement_gb`), so dispatch, the published tier / `memory_eligible` (`publish_floor`), the fleet install refusal (`_memory_refusal`)
  and `docs/model-benchmarks.md` all read 8 GB. The manifest is unchanged (digest-pinned), as for FLUX.2 Klein.
- **The Voice worker's own preflight takes the same 3.2 GB**: `memory_admission.worker_free_floor_gb` (was `override_free_gb`) returns
  the operator override, else the fleet measurement, and the broker forwards it as `memory_floor_gb`. Without it the worker's size
  estimate (2.9 + 1.25 = 4.15 GB cold) would have refused every job an 8 GB Mac was admitted for. Models with no measurement keep the
  worker's estimate. The Hub's critical-pressure safety net and the INSUFFICIENT_MEMORY hand-back are unchanged.
- Voice catalog row: `min_unified_memory_gb` 8, `min_free_memory_gb` 3.2, note / recommended hardware / use cases from the measurement.
- Kit: `copy_models_for_this_mac.PROVEN_SMALLER["breeze-tts-2-mlx-4bit"] = 8`, so "2 - Copy the models" carries it to 8 GB Macs. Not on
  the Lite Mode allowlist (`components/workstation_models.py`). The 8 GB Breeze test's stale "16 GB only" sentences are updated.
- Tests: `test_breeze_4bit_runs_on_8gb_macs_from_its_measurement` (hub_regression), `test_breeze_is_copied_to_8_gb_macs_on_its_measurement`.

## 0.6.144 — 2026-10-04 · Longer narration pauses; a standing-by Lite Mode Mac is not free capacity

Release of `release/0.6.144` (= 0.6.143 a0f8263 + `--no-ff` merges of `feat/lite-capacity` 9d13187, `feat/kit-breeze-8gb-test` d41f1dc and
`fix/install-model-visible` 38402e5, and the owner's pause decision). No textual conflicts.
- **Narration pauses are longer and more natural: 1 second after each sentence and 1.5 seconds between paragraphs, where the text is joined**
  (owner decision 2026-10-04). `long_form_policy.UNIFORM_SENTENCE_JOIN_PAUSE_SECONDS` / `UNIFORM_PARAGRAPH_JOIN_PAUSE_SECONDS` = 1.00 / 1.50 s (were
  0.60 / 1.00); the mid-sentence soft join stays 0.15 s. They are the one source for the joiner (`_production_join_pauses_s`), the published
  `join_pause_milliseconds` (now 1000), the lane contract's `sentence_pause_sec` / `paragraph_pause_sec` defaults and the section silence check
  (`tts_quality.max_pause_s`: 4.5 s after a sentence, 5.0 s at a paragraph). Request bounds stay 0-3 s; a request that sends its own pause keeps it.
  Tests pinning 0.6 / 1.0 now pin 1.0 / 1.5; README, `docs/model-benchmarks.md` (+ `qualification/model_record.py`) and the catalog notes follow.
- **A shop Mac in use (Lite Mode standing by) no longer counts as free, so work isn't held for it** (`capabilities._model_capability`: a worker with
  `standing_by` - standing by, stepped aside or quiet hours - is not `capacity_eligible`; `build_snapshot` carries `standing_by`). It re-enters the
  eligible totals as soon as it is free. Test: `hub_regression/cases/test_capability_supply.py`.
- **An installed model shows as installed the moment its download says done** (`StudioMonitor.refresh_machine_catalogs`): a status read that
  reports a run `done` re-reads that Mac's worker catalogs first, once per run (`/api/hub/model-downloads/{id}`, the fleet route for this Mac, and a
  relayed node read). Live 2026-10-04: Fish S2 Pro 4-bit finished (`done`, exit 0) but the Fleet still said `cache_state` `absent` until the 60 s
  catalog refresh. Tests: `hub_regression/cases/test_fleet_model_install.py`.
- **Kit: 8 GB Breeze test** (`qualification/RUN-8GB-BREEZE.command`): refuses on a non-8 GB Mac, imports `breeze-tts-2-mlx-4bit` from the kit through
  `import_kit_packages.py`, then runs `eight-gb-tts-run.sh --models breeze` (calibrated 0.6.143 sampling sent explicitly, 2.5x clip limit). A clip the Hub
  kept queued for the whole limit is reported "did not start / NOT MEASURED", not "timed out". Copied by `prepare-kit-0144.sh` (MANAGED list too).

## 0.6.143 — 2026-10-04 · Calibrated voice defaults; faster fleet reads; no tiny sections; a group takes a job any of its Macs can run; install a model from the Gateway

Release of `release/0.6.143` (= 0.6.142 aeb89a5 + `--no-ff` merges of `perf/controller-read` fba7d3c, `feat/kit-lite-models-copy` a36ca55,
`fix/tiny-section` 9231ee5, `fix/0143-small` 8cfc095..3543f56, `fix/machine-counters` 37eb4cd, `feat/node-model-install` fe486fa,
`fix/khmer-gate` 65e49f7, then the Khmer transcribe-back skip and the calibrated sampling defaults). No textual conflicts. Gateway contract for the
model install: `_orchestra/audit-20261001/INTERFACES-0143.md`.
- **Fleet JSON is gzip-compressed** (`standalone.create_app`: Starlette `GZipMiddleware`, level 5, >= 1 KiB). A Gateway's read of one controller was
  ~3.6 MB of JSON over Tailscale at 0.5-1.2 MB/s (4-20 s); it compresses 9-26x. `application/octet-stream` (release images and blobs, read with
  `iter_raw` and resumed with Range) and Starlette's default media exclusions stay identity; a client without `Accept-Encoding` gets identity.
- **Kit: `2 - Copy the Lite Mode models.command`** (`copy-lite-models.command` at the kit root): runs `2 - Copy the models` with
  `STUDIOHUB_WORKSTATION=1`, copying only `components/workstation_models.py`'s models, then says to tick Lite Mode in Settings. It changes neither role
  nor Lite Mode. `Set Up a Shop Mac.command` leaves the kit; `prepare-kit-extras.sh` moves a drive's copy (and the older `Set Up the Shops
  Controller.command`) to `<volume>/_trash/Extras-retired/`. Docs, `release_kit` operator steps and `test_shop_kit` follow.
- **No tiny or unspeakable section reaches a model** (`generation._sentence_safe_text_chunks`): a paragraph with nothing to speak (`***`) is left out
  (its boundary keeps the paragraph pause), a section under 12 spoken characters (CJK counts three) joins its neighbour, and a request with nothing to
  speak is a ValueError up front. Join pauses find sections whitespace-insensitively (`_find_section`). "Produced no audio" for a tiny/unspeakable
  section is a ValueError (the text, not the model); memory is blamed only when free memory was below the model's loaded need.
- **Content-caused pauses are lifted** (`broker._counts_against_model`, one classifier for new failures and stored pauses): on start,
  `_load_model_protection` re-reads `model_protection.json` and lifts a pause whose failure was the text (incl. an old worker's "produced no audio.
  N-character section" with N < 12); engine failures and missing weights stay paused.
- **Published memory tier = admission floor** (`memory_admission.publish_floor`, applied by `monitor._assemble_aggregate_catalog`): VoxCPM2 4-bit
  is memory-eligible on 8 GB Macs (was published 16 GB while dispatch enforced 8 GB); FLUX.2 Klein / Z-Image-Turbo likewise. `qualification/model_record.py`
  reads the same floor; `docs/model-benchmarks.md` regenerated.
- **A group accepts a job when any of its Macs can run it** (`standalone._a_joined_mac_can_run` in `_submission_readiness`): a joined Mac with the model
  cached, runtime-ready, available, not paused, Lite-Mode-allowed and within its RAM floor is enough (group 1500 refused Fish S2 Pro 4-bit with
  `MODEL_NOT_READY` while node 1504 had it). Model-aware dispatch sends it there.
- **Refusals are not failures** (`activity.REFUSAL_CODES`): a Voice MemoryGuard refusal ends typed `INSUFFICIENT_MEMORY` and is neither counted as
  `failed` nor a reason for `needs_attention` (8 GB Macs read 26 done / 192 failed). `GET /api/hub/stats?activity_since=<epoch>` counts from the
  Gateway's "Reset counters" baseline (nothing deleted).
- **Install a model from the Gateway** (`POST /api/hub/fleet/nodes/{node_id}/model-packages/{package_id}/download`,
  `GET /api/hub/fleet/nodes/{node_id}/model-downloads/{run_id}`; controller runs it itself or relays to the node with the Fleet token). Same refusals as
  the Model library download plus "too little memory to run it here"; audited on controller and node. Only these two routes join `FLEET_ALLOWED`.
- **Khmer transcribe-back is skipped** (`fix/khmer-gate` + follow-up): Whisper large-v3 turbo cannot read Khmer (measured: 48 known-good sections score
  syllable overlap median 0.00, max 0.11; broken audio up to 0.42; `_orchestra/audit-20261001/tts-khmer/GATE.md`). `tts_quality.check_section` records a
  Khmer score as `text_check` "advisory" and never rejects on it (`KHMER_MIN_SYLLABLE_OVERLAP` / `KHMER_MAX_MISSING_RUN` removed), and
  `transcribe_back_applies` is False for Khmer as for CJK, so Whisper is not loaded for a Khmer section; silence / length / internal-pause checks still
  enforce. A section that fails every attempt names each attempt's measured value and limit in the job error (`tts_quality.rejection_numbers`, under the
  Hub's 500-character cap); `broker` whitelists `text_check`.
- **Calibrated per-model sampling defaults** (`catalog.SAMPLING_DEFAULTS_BY_REPO`; owner: "use all ★ recommendations"; source
  `_orchestra/audit-20261001/tts-sampling/REPORT.md` + `recommended.json`): Breeze-TTS-2 4-bit B (temperature 0.6, top_p 0.9, top_k 40, repetition
  1.05; Chris's section failures 22 % -> 7 %, spread 13.6 -> 7.0); Fish S2 Pro 4-bit B (0.6 / 0.7 / 30); Qwen3-TTS 1.7B 4-bit D (0.6, 0.9, 40,
  repetition 1.6); Qwen3-TTS 0.6B 8-bit B (0.6, 0.9, 40, 1.5); VoxCPM2 4-bit B (cfg 2.0, 10 steps); OmniVoice bf16 C (position_temperature 0);
  Pocket TTS 4-bit C (temperature 0.3); Khmer MMS B (noise 0.5 / duration noise 0.6). VoxCPM2 8-bit keeps A; models not listed keep their family's
  values. Pinned by `test_sampling_levers.CALIBRATED_0_6_143` (and every calibrated value checked inside its lever's bound).
- Tests: `components/voice/app/tests/test_tiny_sections.py`, `test_priority_mlx_models.py`, `test_khmer_text_handling.py`, `test_tts_quality.py`,
  `test_sampling_levers.py`; `tests/test_activity_counters.py`, `test_shop_kit.py`; `tests/hub_regression/cases/test_group_readiness_any_mac.py`,
  `test_memory_admission.py`, `test_model_failure_protection.py`, `test_fleet_model_install.py`, `test_hub_gap_routes.py`.

## 0.6.142 — 2026-10-04 · Every TTS model's sampling levers are request fields (defaults unchanged); Khmer narration splits, pauses and is checked correctly

Release of `release/0.6.142` (= 0.6.141 + the sampling levers and Khmer fixes below + `feat/lite-mode-every-role` 10e60cb (on `feat/workstation-app-settings` 2e0edf2) + `fix/paused-group-chat-transcription` a2b6284, merged --no-ff). Owner, 2026-10-04: "find a balanced default for each of the models" after the TTS
comparison found the Hub passed Breeze TTS 2 no sampling settings. Audit: `_orchestra/audit-20261001/tts-sampling/LEVERS.md`; calibration plan beside it.
- **One table of sampling levers** (`catalog.SAMPLING_LEVERS`, per family: request field -> engine argument, bound, default; per-checkpoint overrides in
  `catalog.SAMPLING_DEFAULTS_BY_REPO`; `catalog.sampling_kwargs` clamps). New request fields: `qwen_temperature|top_p|top_k|repetition_penalty`,
  `breeze_temperature|top_p|top_k|repetition_penalty|cfg_scale`, `pocket_temperature|lsd_decode_steps|eos_threshold`,
  `omnivoice_class_temperature|position_temperature|layer_penalty_factor|t_shift`, `vits_noise_scale|noise_scale_duration` (Khmer MMS). The existing
  VoxCPM2 (`cfg_value`, `inference_timesteps`, `voxcpm_*`), OmniVoice (`omnivoice_num_steps|guidance_scale`) and Fish (`fish_*`) levers moved into the
  table with their bounds unchanged. Chatterbox is unchanged (its levers were already request fields).
- **Defaults equal what each engine received in 0.6.141** (pinned by `test_sampling_levers.EFFECTIVE_IN_0_6_141`): mlx-audio's `generate_audio`
  passes `temperature=0.7` to every model, so Breeze/Qwen/Pocket sampled at 0.7 (not the 0.9 in their signatures); Qwen's ICL clone floors
  repetition penalty at 1.5; Breeze `cfg_scale` 1.0 = off.
- **Published**: `lane_contract.parameters_for` lists each model's levers with `type`/`minimum`/`maximum`/`default`, so the Models tab can store a
  per-Mac default and a scheduler can read the bounds. An omitted lever is not recorded on the job (`main._job_parameters`), so a request recorded by
  0.6.141 still compares equal on a `client_request_id` replay. Older workers ignore the new fields.
- **Seed**: never passed to an engine (Breeze's own `seed` would reseed every section). Khmer MMS now follows the MLX rule (`_qwen_attempt_seed`, seeded
  once, `_native_continue_rng` sections continue): a fixed seed reseeded every section to one state, and an unseeded job recorded a `resolved_seed` the
  audio never used.
- **Khmer text handling** (`_orchestra/audit-20261001/tts-khmer/REPORT.md` findings 1, 2, 4). `generation._QWEN_SENTENCE_ENDINGS` (the one set the
  section splitter and `_production_join_pauses_s` read) gains ។ U+17D4 / ៕ U+17D5, so joins after a Khmer sentence get the 0.6 s sentence pause, not
  0.15 s. `_split_long_text_unit` prefers a cut after ។/៕ (even with no space after it), then space / zero-width space (U+200B) / `,;:`, and the
  last-resort cut goes through `_cluster_boundary` (never before a combining mark, after a virama-class sign such as the Khmer coeng, or beside
  ZWJ/ZWNJ). Quality gate: `qwen_quality._tokens` adds Khmer orthographic syllables (`_KHMER_SYLLABLE`; `_normalized_text` keeps Khmer signs), so
  `tts_quality.heard_words` counts real units (it reported 0/0/1.0); a Khmer section fails `TTS_SECTION_TEXT_MISMATCH` when syllable overlap
  < `KHMER_MIN_SYLLABLE_OVERLAP` 0.5 or a missing run > `KHMER_MAX_MISSING_RUN` 24 syllables (deliberately loose, unmeasured: `ponytail:` note);
  the English rule is untouched. Whisper transcribe-back reads a Khmer section as `km` (`tts_quality.script_language`) whatever the request's
  `language` says (Story sends the voice's own, e.g. `en`). Tests: `components/voice/app/tests/test_khmer_text_handling.py` (spaced and unspaced
  Khmer fixtures; 8 of its 9 fail on 0.6.141 code).
- **Lite Mode in the app's Settings** (`feat/workstation-app-settings`, 2e0edf2): Settings > Lite Mode reads/saves this Mac's workstation settings
  through its own Hub (`GET/POST /api/hub/workstation`; same labels and policy sentence as the Gateway's Fleet sheet; no restart, `workstation.py`
  re-reads per decision); a hand launch opens the window (only an update's reopen stays quiet). User-facing name "Lite Mode" (kit commands, README,
  checklist, copy-models, refusal sentences); code, API paths, JSON keys, `workstation.json` and error codes keep "workstation". Native tests in
  `tests/native/api_main.swift`, `render_main.swift`.
- **Lite Mode governs every job on every role** (`feat/lite-mode-every-role`, 10e60cb): the scheduler treats `"local"` like any Lite Mode Mac
  (`peers.dispatch_ready` / `standing_by` / `workstation_start_free_gb` read this Mac's `workstation.status()`, `peers.lite_mode_allows`); a controller
  standing by routes its group's work to its nodes, otherwise work waits (`governor_note` "Waiting: Lite Mode - ..."); the pressure-yield loop also
  watches `broker.local_running_jobs()`; a handed-back own job is re-queued here (standalone/node) or handed to the Gateway (controller).
- **"Today: N jobs" counts every job this Mac starts in Lite Mode** (`peers.note_dispatch` at the own-queue dispatch points in `broker`,
  `transcription_jobs`, `chat_jobs`; the peer door already counted). Test: `test_lite_mode_every_role.test_todays_count_includes_this_macs_own_queue`.
- **`Set Up the Shops Controller.command` retired** (owner 2026-10-04: the Shops controller is a regular controller that also generates; set it up
  like any Mac, then tick Lite Mode). Moved to `_trash`; `prepare-kit-extras.sh` ships only `Set Up a Shop Mac.command` and moves a drive's old copy
  to `<volume>/_trash/Extras-retired/`; kit README, NODE-SETUP-CHECKLIST, AGENTS.md, `test_shop_kit`, `test_yam_display_install` follow.
- **Transcription honours a fully paused group** (`fix/paused-group-chat-transcription`, a2b6284): `transcription_jobs` create route answers 503
  `NO_AVAILABLE_MACHINE` (after replay/duplicate checks) via `broker.every_capable_mac_paused`, the dispatcher hands queued items back as
  `error`/`NO_AVAILABLE_MACHINE` without a try, items publish `error_code`, a manual retry clears it. Chat needs nothing (no producer route; the peer
  chat route is synchronous and refuses a paused Mac). Test: `tests/hub_regression/cases/test_paused_group_hands_back.py`.
- Gateway: no change needed — `/v1/audio/jobs` forwards `params` and flattens `controls` into the Hub item's params by name (checked in the sandbox).
- Tests: `components/voice/app/tests/test_sampling_levers.py` (new, 118: defaults pinned per family, every lever clamped at both bounds and published,
  engine argument names checked against the installed mlx-audio / transformers, Breeze end-to-end through `_generate_mlx_audio`, seed once / no engine
  seed, Khmer seed + noise scales); `test_priority_mlx_models.py`, `test_model_defaults.py` follow the move to the table.

## 0.6.141 — 2026-10-04 · A paused group hands its work back; TTS on 8 GB Macs admitted once per job, on one reading

Release of `release/0.6.141` (= 0.6.140, which was never published, + the fixes below). Found by the TTS comparison test of 2026-10-04
(`_orchestra/audit-20261001/tts-comparison/`). Gateway contract: `_orchestra/audit-20261001/INTERFACES-0141.md`.
- **Never strand work in a paused group** (`broker.every_capable_mac_paused`, `_hand_back_queued`, `standalone.submit_jobs`). Pauses were read only
  when choosing a Mac (`_eligible_studios`, `_pool_members`), never at accept, so group 1500 with all five Macs paused accepted 21-25 TTS jobs that sat
  queued ~7,000 s. Now: when at least one Mac that could run the model is known and every such Mac is paused (machine or Studio) or drained,
  `POST /api/hub/jobs` answers **503** `{"error", "code": "NO_AVAILABLE_MACHINE", "detail"}` (replays of an accepted `clientRequestId` excepted), and the
  dispatcher hands every queued item of such a batch back as `error` / `error_code: NO_AVAILABLE_MACHINE`, no try spent, webhooks after the durable write;
  running items finish. Offline/busy/cooling Macs still count as capable. `_pool_members` now shares `_model_rows` / `_paused` / `_can_run` with it.
  Broker lanes only (voice, image, music, video, render); chat and transcription keep their own queues.
- **Voice memory guard admits a job once** (`generation._memory_preflight`, `_dispatch_mlx_long_form` `_memory_admitted`). The guard ran before every
  section with the job's own model (and the section validator) resident, so OmniVoice / VoxCPM2 parts on idle 8 GB Macs failed "2.8 GB free is needed,
  but only 2.0-2.5 GB is available" (the loaded floor 2.75 GB) while the host reading showed 3.0-4.6 GB; 0 successes on 8 GB since 00:25. Later
  sections and quality re-renders of an admitted job are no longer re-measured.
- **A different idle model is freed before refusing** (`_memory_preflight`): a worker holding model A, asked for B, evicts A (the load does anyway)
  and re-reads, instead of refusing B on memory A held. The Hub's admission hand-off (`release_idle_siblings`) now also asks the idle target Studio
  itself to release, for the same reason.
- **The hand-back sentence quotes the decision's numbers** (`broker._insufficient_memory_text`): it parses the refusal's own sentence (Hub gate
  "needs ~X GB, ~Y GB free" or Voice MemoryGuard "X GB free is needed, but only Y GB is available") instead of re-reading policy and a cached host
  snapshot afterwards (116 of 272 refusals said e.g. "needs about 3.1 GB free and has about 4.5 GB free"). The admission-wait expiry now uses the floor
  the gate used (`_with_machine_floor`, a workstation's start floor included).
- **Voice `memory_eligible` is capacity only** (`model_runtime_status`): total memory >= the model's tier, like every other Studio and as
  `capabilities` / `model_health` / the Gateway read it. It used to also need free memory at that instant, so a busy or briefly-full 8 GB Mac published
  OmniVoice as not memory-eligible.
- Tests: `tests/hub_regression/cases/test_paused_group_hands_back.py` (new), `test_capacity_wait_backoff.py` (worker numbers win over a 4.5 GB snapshot),
  `tests/test_memory_policy_contract.py` (target released too), voice `test_priority_mlx_models.py`, `test_native_executor.py`, `test_tts_quality.py`,
  `test_generation_artifact_contract.py`.

## 0.6.140 — 2026-10-03 · Workstation mode: a shop's cashier Mac lends the fleet its idle time and never its user's

Never published on its own; it ships in 0.6.141.

Release of `release/0.6.140` (= 0.6.139 + `feat/workstation-mode` 5e5ad77 (incl. 9e44011), merged `--no-ff`, no textual conflicts; the branch was cut
from 3f59771, so it is checked against 0.6.139's uniform narration and kit Extras: no shared code path, broker edits in separate hunks).
- **Workstation mode** (`components/hub/workstation.py`, owner 2026-10-03 "Shops"): per-Mac settings in `<hub data>/workstation.json` (default off), set
  from the Gateway via the controller relay `POST /api/hub/fleet/nodes/{id}/workstation` -> the node's `/api/hub/workstation` (fleet token); published as
  `workstation` {settings, state off|working|standing_by|yielded|quiet_hours, reason, jobs_today, yields_today} on `/api/hub/resources` and every fleet row.
  When on: worker processes (and children) at `PRIO_DARWIN_BG`; only-when-idle (HID idle >= `idle_minutes`, default 3, or screen locked); step aside
  (input -> the current job finishes, model unloaded, no new work for `resume_after_minutes`, default 2); quiet hours; no desktop alerts.
- **Six-model allowlist** (`components/workstation_models.py`): FLUX.2 Klein 4B 4-bit, Z-Image-Turbo 4-bit, Qwen3-TTS 0.6B Base 4-bit, VoxCPM2 4-bit,
  Whisper large-v3 turbo, Stable Audio 3 Medium, each by package id and repo id; any other model is refused at the peer door
  (`WORKSTATION_MODEL_NOT_ALLOWED`). Checked against 0.6.140's `model-manifests/`: all six ship, none retired or shelved
  (`tests/test_workstation_mode.py` pins it).
- **Memory**: no fixed reserve; a job starts on the model's own free floor or the Mac's `start_free_gb` (`broker._with_machine_floor`). A started job
  finishes; only macOS critical memory pressure for 20 s cancels it and hands it back as **`WORKSTATION_YIELDED`** (receipt kept, worker stopped if it
  does not stop in 2 s).
- **Controller** (`broker.py`, `peers.py`, `capabilities.py`, `fleet_nodes.py`): a standing-by Mac is busy, never failed; a `WORKSTATION_YIELDED` item
  is re-queued on another free Mac of the group or handed straight back to the Gateway, no try spent; stand-by (30 s) / not-allowed (600 s) refusals are waits.
- **Native**: the app reads `workstation.json` - no window / What's New / failure pop-ups, optional hidden menu-bar icon.
- **Kit**: `packaging/kit-extras/Set Up a Shop Mac.command` (workstation mode on, `STUDIOHUB_WORKSTATION=1` copies only the six models, then
  Update this Mac) and `Set Up the Shops Controller.command` (own generation paused via `machine_flags.json`); `prepare-kit-extras.sh` copies both into
  Extras beside the Yam Display installer. `qualification/NODE-SETUP-CHECKLIST.md` "Shops". Tests: `tests/test_workstation_mode.py`, `tests/test_shop_kit.py`,
  `tests/hub_regression/cases/test_workstation_yield.py`, `tests/test_yam_display_install.py`.

## 0.6.139 — 2026-10-03 · One pause rule for every voice, 250-character sections and 1,500-character requests; the kit installs Yam Display

Release of `release/0.6.139` (= 0.6.138 + `feat/kit-yamdisplay` 3f59771 on main + `feat/uniform-narration` 52eb9a7 (incl. 37b0971 paragraph pause), merged
`--no-ff`, no conflicts).
- **One join rule for every voice family** (37b0971, 52eb9a7, `long_form_policy`): `UNIFORM_SENTENCE/PARAGRAPH/SOFT_JOIN_PAUSE_SECONDS` = 0.60 / 1.00 / 0.15 s.
  `production_boundary_pause_seconds` returns the uniform rule; `PRODUCTION_PAUSE_FAMILIES` / `uses_production_pauses` are gone (Breeze's flat 0.6 s included).
  A request's `sentence_pause_sec` / `paragraph_pause_sec` (0-3 s) replace the rule (`requested_boundary_pauses`). The lane contract publishes defaults
  paragraph 1.0 / sentence 0.6 for every model.
- **250-character sections for every TTS model** (`UNIFORM_SECTION_MAX_CHARACTERS`, `policy_for(..., uniform=True)`), join 600 ms published; a finer audit is
  still followed (min), a caller may still choose 40-3000. Audit records are not edited: `audit_contract_runtime` checks a record against the family's
  measured policy (`uniform=False`) and `qwen_17b_assembly_invariants` keeps the audited 300/600/180 ms.
- **`lane_contract.maximum_input_characters` = 1,500 for every TTS model** (`UNIFORM_REQUEST_CHARACTERS`, `evidence: owner_uniform`); a model whose slowest
  Mac was timed and fits less in 900 s publishes the smaller number (`evidence: measured`). Breeze stays 1,500.
- **Quality gate**: the in-section silence allowance follows the uniform (or requested) pause + 3.5 s: sentence 4.1 s, paragraph 4.5 s (was 3.8 / 4.1).
- Catalog wording, voice README, `docs/lane-execution-contract.md`, `docs/model-benchmarks.md` (via `qualification/model_record.py`) updated.
  Write-up: `_orchestra/audit-20261001/uniform-narration.md`. Tests: `tests/test_tts_join_pauses.py` (new), `test_section_policy.py`, `test_breeze_family.py`,
  voice `test_lane_contract.py`, `test_long_form_policy.py` and others.
- **The kit installs Yam Display** (3f59771): `packaging/kit-extras/Install Yam Display.command` (installs when missing or older, never downgrades, never blocks the
  Studio Hub install), run by `1 - Install the app.command` and `Update this Mac.command`; `prepare-kit-extras.sh` copies the DMG and command into a kit's
  Extras with sha256 checks. Tests: `tests/test_yam_display_install.py`.

## 0.6.138 — 2026-10-03 · Every voice section is also checked for a long silence inside it or a dropped clause, and re-made if it has one

Release of `release/0.6.138` (= 0.6.137 + `fix/gate-internal-silence` f5d7946, merged `--no-ff`, no conflicts).
- **Section gate: unnatural silence inside a section** (f5d7946, `tts_quality.py` evidence revision v2, `broker.py`). EP0003 Chapter 9 part 5 passed the
  0.6.137 gate with 35 s of silence (-66 dB) inside a 67 s section, and Whisper heard 69 of 76 words (7 allowed) - the 7 were one clause lost in the silence.
  `audio_metrics` now records `longest_pause_s` (longest unvoiced run between the first and last voiced 20 ms frame, after DC/drift removal, so edge silence
  does not count). `TTS_SECTION_INTERNAL_SILENCE` when it exceeds the longest pause the text calls for (`long_form_policy` paragraph 0.6 s if the text has a
  blank line, else sentence 0.3 s) + 3.5 s, divided by native speed (3.8 s / 4.1 s at speed 1); re-rendered with a new seed like the other codes. No model
  is needed, so 8 GB Macs get this check too. The 3.5 s margin: Breeze 4-bit's own narration pauses reach 3.2-3.4 s; good clips stop at 3.44 s, the next
  is 4.32 s.
- **Transcribe-back: a dropped clause fails** even when the 10% share holds: `TTS_SECTION_TEXT_MISMATCH` also when more than 3 consecutive expected words
  are not heard (`longest_missing_run`). Broker whitelist carries the new code and `longest_pause_s`, `max_pause_s`, `longest_missing_run`.
- Offline on saved audio: 19/19 bad eval sections caught (18 by audio alone, was 7); 8 eval sections labelled good are now rejected, each with a 4.3-18.9 s
  mid-section silence; 4 of 18 accepted Chapter 9 sections (35, 12.3, 7.6, 6.7 s) rejected, 14 kept (`_orchestra/audit-20261001/tts-gate-internal-silence.md`).
  Tests: `test_tts_quality.py` (+3), `tests/test_section_quality_passthrough.py` (+1).

## 0.6.137 — 2026-10-03 · Every voice section is checked before it is joined and re-made if it came out wrong; Breeze takes chapters in pieces it can finish in time

Release of `release/0.6.137` (= 0.6.136 + `fix/whisper-gaps-tts-deadline` c6ea53b + `fix/tts-quality-gate` f115fb5, both merged `--no-ff`, no conflicts;
f115fb5 is built on c6ea53b).
- **TTS size per request from the slowest Mac** (c6ea53b, `catalog.py`, `lane_contract.py`). Each TTS model whose slowest allowed Mac class was timed
  (`catalog.SlowestMacSpeed`) publishes `lane_contract.maximum_input_characters` = floor(900 s / RTF x chars-per-audio-second, to 100) with
  `maximum_input_characters_source`. Breeze TTS 2 4-bit: M1 16 GB, RTF 5.13, 8.58 chars/s -> **1,500** characters (one request <= 15 min on an M1 16 GB,
  2x under the 1,800 s voice deadline). The Gateway scales its deadline by this field and Story splits narration by it. Other TTS models publish nothing.
  The Whisper subtitle gaps were investigated first: Whisper drops nothing; the audio itself was silent, DC drift or babble from failed Breeze sections
  (`_orchestra/audit-20261001/whisper-gaps-tts-deadline.md`). Test: `test_lane_contract.py`.
- **Per-section TTS quality gate** (f115fb5, new `tts_quality.py`, `generation.py`, `broker.py`). Every mlx-audio family (Breeze, VoxCPM2, OmniVoice, Fish,
  Chatterbox, Qwen, Pocket) now runs through the parent section loop, single-section requests included (that one section is moved into place, not joined or
  levelled). Each section gets audio checks (DC/drift removed with a 50 ms moving average, voiced = 20 ms frame > -40 dBFS; `TTS_SECTION_SILENT` below
  chars / (fastest rate x 2.5), `TTS_SECTION_TOO_LONG` above chars / (slowest rate x 0.55) + 2.5 s; Breeze 8.6-11.7 chars/s, others 6-20; CJK counts 3x)
  and a Whisper large-v3 turbo transcribe-back (`TTS_SECTION_TEXT_MISMATCH` when > max(2 words, 10%) are not heard) loaded only when turbo + 2 GB is free;
  a skipped check says why (`unavailable`, `memory`, `script`, `qwen_clone_gate`). A rejected section is re-rendered with a new seed (+7919 per attempt)
  up to 3 times, then the job fails with `TTS_SECTION_*` and "Section N of M ...". Breeze `max_tokens` is now bounded by the section's text (it ran to
  `generate_audio`'s 1,200 = 96 s). Evidence: job field `section_quality {revision, sections: [...]}`, persisted, and copied onto the batch item by
  `broker._record_worker_section_quality` (numbers, booleans and known codes only; cleared on a re-attempt). Measured offline on 131 Dreamlands sections:
  19/19 bad rejected (7 by audio checks alone), 0/112 good rejected; 10/10 new-seed re-renders accepted; Whisper ~4% of Breeze generation time, +1.6 GB
  while a job runs (`_orchestra/audit-20261001/tts-quality-gate.md`). Tests: `test_tts_quality.py`, `tests/test_section_quality_passthrough.py`.

## 0.6.136 — 2026-10-03 · A busy group no longer blocks its controller's update; the app relaunches only after a real crash; free work is re-run when its Mac or worker dies

Release of `release/0.6.136` (= 0.6.135 + `fix/drain-never-starves` e505ad9 + `fix/crash-only-and-reroute` b65a9ad, both merged `--no-ff`, no conflicts;
both touch `broker.py`/`standalone.py` in separate hunks - drain gating in `busy_machines`/`_submission_readiness`, re-runs in `_run_item`'s failure path -
and a re-run is dispatched through the same `busy_machines` gate, so a held drain defers re-runs like any new work).
- **Drain never starves** (e505ad9, `broker.py`, `standalone.py`). A draining controller's nodes keep working while its own job(s) finish; once nothing runs
  on the controller itself (`broker.drain_holds_group`), `busy_machines` names every machine in the group and new submissions get the re-routable
  `MACHINE_BUSY`. What the nodes run finishes, the count reaches zero, the controller installs. Before, a long queue kept some node running one of the
  controller's batches for ever, so the controller - holding the group's one update slot - and every Mac behind it never updated. Test: `test_update_drain.py`.
- **Relaunch only after a real crash** (b65a9ad, `native/ShellCore.swift`, `native/StudioHub.swift`). `StopRecord` settles why the last run ended from a
  `running` heartbeat, crash-signal (`crashed`), SIGTERM (`terminated`), `memory-critical`, `clean-quit`, `updating` and `power-off` markers, backed by
  `StudioHub-*.ips` reports. The agent's standby exits 1 (launchd relaunches) only for a crash; a Quit, Force Quit or memory kill exits 0
  (`AgentStandby.shouldStayStopped`), so the existing plist (KeepAlive SuccessfulExit=false) needs no migration.
- **Why a Mac went offline** (b65a9ad, `fleet_nodes.py`, `standalone.py`): the verdict is written to `<data>/last-stop.json` and published as
  `last_stop {reason, at, label, relaunched}` on `/api/hub/resources`, the fleet overview rows and `/api/hub/fleet/nodes` rows; a controller relays each
  node's, and repeats an unreachable node's last word marked stale. Test: `test_last_stop.py`.
- **Free work is re-run when its Mac or worker dies** (b65a9ad, `broker.py`, `transcription_jobs.py`). An attempt whose worker stopped answering for the
  recovery window, lost the job (404) or restarted under it (`WORKER_RESTART_UNCERTAIN`; Voice `EXECUTION_IDENTITY_LOST`) is re-run in every lane (image,
  music, video, voice; this Mac or a node) instead of being left `uncertain`. The abandoned original is watched for `ABANDONED_WATCH_S`: cancelled when its Mac
  answers, or adopted if it finished first (the re-run is then cancelled). A voice re-run takes a fresh client request id. Transcription lost to a dropped or
  timed-out worker is re-run elsewhere at once. Malformed receipts and a lost local POST receipt stay fenced.
  Tests: `hub_regression/cases/test_free_work_reroute.py`, native StopRecord/AgentStandby checks in `tests/native/api_main.swift`.

## 0.6.135 — 2026-10-03 · One worker exiting no longer stops the Hub; a crash or Force Quit brings the app back; pause a Mac from the Gateway

Release of `release/0.6.135` (= 0.6.134 + `fix/worker-exit-resilience` 214ad11 + `feat/pause-node` 1194f1b, both merged `--no-ff`, no conflicts).
- **A worker that exits is restarted on its own** (214ad11, `packaging/hub_worker.py`). A private worker that exits after start-up is restarted on its own
  port with backoff (1, 2, 5, 10, 30, 60 s; reset after 5 min up); only its lane is down meanwhile and the Hub, the other studios and their in-flight jobs keep
  running. A worker failing before the Hub is ready is still fatal. `run_server` gains a `shutdown_started` hook so no child is restarted once shutdown begins.
- **The app is supervised by its launch agent** (214ad11, `native/ShellCore.swift`, `native/StudioHub.swift`). An app opened by hand or reopened by an update
  was supervised by nothing (the agent is only loaded at login); such a launch now loads/kickstarts the agent. The agent's copy waits on the data-folder lock
  before any AppKit setup and, when the app ends, exits 0 after a deliberate Quit (clean-quit marker) or 1 otherwise, so launchd relaunches after a crash or
  Force Quit and a clean Quit stays stopped. Default data folder only.
- **An unmanaged voice stays on this Mac** (214ad11, `broker.py`): a `voice_library_id` the Hub does not manage is this Mac's own library entry and is now
  routed only to the studio that holds it (was: any studio, so it could go to a node without that voice).
- **Pause or resume a Mac** (1194f1b): Fleet-token `POST /api/hub/fleet/nodes/{id}/pause {"paused": bool}` flips `registry.machine_enabled` (already read by
  every modality's dispatch) for a joined node or the controller itself, persisted in `machine_flags.json`; running jobs finish, the Mac stays joined, and the
  roster row publishes `paused`. Fleet-token only (not in `NODE_ALLOWED`). Tests: `test_hub_process.py`, `tests/native/api_main.swift`,
  `hub_regression/cases/test_unmanaged_voice_routing.py`, `test_node_pause.py`.

## 0.6.134 — 2026-10-03 · Image, music and video jobs finish on other Macs; a busy Mac is a wait; abandoned peer work is cancelled

Release of `release/0.6.134` (= `fix/image-machine-id-reserved`: 0.6.133 + 2e29bea + ec170ac + 59b6ec6).
- **Peer jobs are polled to their own answer** (2e29bea). The controller's dispatch poll and the manual reconcile in `broker.py` called
  `_validate_durable_worker_job` without `remote=`, so a joined node's worker (which reports `machine_id: "local"`) failed the first poll of every peer
  image/music/video job ("Image worker status changed its machine_id"). Only the recovery poll passed `remote`, so a peer job survived only if it finished
  inside `RECOVERY_WINDOW_S` (120 s): Z-Image-Turbo 4-bit (~300 s on 8 GB) never finished remotely and was regenerated on node after node.
- **A node's peer lease ends with its job** (2e29bea): `standalone._release_finished_peer_leases` asks the node's own worker and frees a lease once the job
  is over or gone, instead of waiting for a controller poll that never came (six-hour TTL, "local is reserved for other work on this Mac" while idle).
- **MACHINE_BUSY is a wait in Subtitle (2e29bea) and Chat (ec170ac)**, as in the broker since 0.6.111: try restored, Mac not marked failed, retried after
  `MACHINE_RESERVED_AVOID_S`. Before, three busy nodes ended a transcription, and `MAX_TRIES` busy answers ended a chat pack, as a final error. Any other
  409 is unchanged.
- **The controller cancels peer work it gives up on** (59b6ec6): when a remote Image/Voice job is abandoned for regeneration elsewhere,
  `_cancel_abandoned_peer_job` sends the node the user-cancel route (`DELETE /api/generate/jobs/<id>` via the peer surface) in the background and logs it.
  Tests: `test_remote_worker_identity.py`, `test_subtitle_dispatch.py`, `test_studio_proxy.py`, `test_chat_hub_wiring.ChatPackMachineBusyTests`.

## 0.6.133 — 2026-10-03 · Fleet health follows what each Mac really runs; a renamed Mac shows its new name; rename a Mac from the Gateway

Release of `release/0.6.133` (main at 9a7e786 = 0.6.132 + 8cff7b1 fleet health + 9a7e786 node rename; no merge needed).
- **Fleet health: a node's studios follow the node** (8cff7b1). Studios were recorded on the controller's roster once, at join, and nodes do not
  re-join, so every node that joined while Video shipped kept a `video` studio: the controller polled a worker that does not exist, showed "workers
  offline" and held every idle Mac in `needs_attention` (activity `partial_read`). `fleet_nodes.sync_roster_from_snapshots` now makes each node's roster
  studios follow what its authenticated peer snapshot says it runs, both ways; silence, errors and empty answers never change a row, and the monitor
  reloads its registry when a row changed.
- **An old error stops holding a Mac amber** (8cff7b1): `activity._state_at` treats an error as `needs_attention` only for `JUST_FINISHED_S` (15 min);
  after that the Mac ages to ready / long idle like any finished job. A studio that should run and is down still needs attention.
- **A renamed Mac's name reaches its controller** (9a7e786). `/api/hub/resources` carries `machine: {id, name}`, the peer snapshot keeps it, and the
  roster sync adopts the name only from an authenticated snapshot naming that node id with a valid 1-120 character name (older Hubs and other Macs change
  nothing). Before, the name moved only at join and through the node's own-credential status call, so a node whose credential the controller no longer
  recognised kept its old name forever.
- **Rename a Mac from the Gateway** (9a7e786): new Fleet-token route `POST /api/hub/fleet/nodes/{id}/name` (`local`/own id renames the controller).
  `relay_name` renames the node on the node first (`POST /api/hub/machine-label` with the Fleet token) and only then the roster, and drops the
  pre-rename snapshot. Rule: a Mac's name lives on the Mac, so a Mac that is off or refuses keeps its name and the caller is told why. Neither route is in
  `NODE_ALLOWED`; a fleet-credential `machine-label` needs 1-120 printable characters (no clearing).
- **Handover join uses the machine name** (9a7e786): `_register_with_controller` read `machine_name` from `load_settings` (never there), so a handed-over
  Mac joined under its id; it now reads `public_settings`.
- Connections > Local API on a non-controller says "Machine ID" (stays the same after a rename) instead of "Controller ID"; the Fleet-token line says it
  may rename this Mac. Tests: `tests/test_fleet_health_idle.py`, `tests/test_node_rename.py`, `test_machine_name_and_restart` pins the fleet rename rule.

## 0.6.132 — 2026-10-02 · New Studio Hub icon (Wave Frame), fleet-password rotation no longer strands offline Macs, unregistered voice outputs cleaned

Release of `integrate/0.6.132` (merges fix/small-batch-0132, commit d9e3534, and feat/fish-16gb-test, commit 4fc4694, on 0.6.131; no conflicts, plus the logo commit).
- **New app icon: "Wave Frame"** (owner's pick, concept 2 of 6, 2026-10-02): a picture frame holding a wave with a note, on a dark indigo tile.
  Source of truth is `assets/logo/app-icon.svg` (1024 tile) and `assets/logo/menubar-icon.svg` (18 pt one-colour glyph);
  `packaging/make_app_icon.py` now renders both with AppKit's SVG renderer (each `.icns` size drawn at its exact pixel size, 16-1024 @1x/@2x,
  then `iconutil`) and writes `assets/app-icon.icns`, `assets/app-icon-1024.png`, `assets/menubar-icon.png` (18 px) and `assets/menubar-icon@2x.png`
  (36 px). The Dock, Finder, the About panel and the DMG show the `.icns` (`CFBundleIconFile`, unchanged). The menu-bar item was the SF Symbol
  `waveform.circle`; it is now the Wave Frame glyph as a template image (tinted for light/dark bars), loaded from the bundle by
  `AppDelegate.menuBarMark()`; `stage_voice.py` copies the two PNGs into `Resources` and refuses a build without them. The SF Symbol stays only as
  a fallback if the files are missing. `tests/test_app_icon_assets.py` pins sizes, the `.icns` types and the wiring.
- **Fleet token rotation grace (30 days):** changing the fleet password used to leave Macs that were offline during the change unable to rejoin.
  Nodes that missed a rotation keep working with the token they hold for 30 days, are given the new token when they next answer, and then the old
  one is retired; the app shows how many nodes are still on the previous token. Also fixed: controllers kept the old token in memory until restarted.
- **Never-recorded voice outputs are cleaned:** the disk governor now moves Voice audio the Hub never registered to the self-cleaning Trash Folder once
  it is older than the 7-day unacknowledged window. The first run moves any backlog there (nothing is deleted; the Trash Folder purges after 15 days).
- **16 GB Fish test:** the TTS runner gains Fish S2 Pro 4-bit / 8-bit / bf16 (opt-in via `--models`, `--target-gb`), `RUN-16GB-FISH.command` (refuses a
  non-16 GB Mac, imports exactly the three packages from the kit with `import_kit_packages.py`, then measures), kit README rows.
- Removed the unused `NativeWorkspaceStore.maskedToken`; four flaky tests made deterministic (`test_chat_hub_wiring`, `test_tts_8gb_runner` swap step,
  hub regression timeout, `test_shelved_models` import).

## 0.6.131 — 2026-10-02 · Passwords and tokens hidden behind an eye icon; memory floors from the real 8 GB measurement

Release of `integrate/0.6.131` (merges feat/secret-protection, commit 4fe6d08, built on 0.6.129; no conflicts).
- **Secrets are masked (native app):** the Fleet token and Hub token on Connections, the new Fleet token after a rotation, the Hand-over and
  Join-a-controller token fields and the Hugging Face token are shown as 12 fixed dots (no prefix, suffix or length) until the eye is pressed;
  Copy works while masked; typed tokens use a secure field until revealed. `SecretValue` / `SecretInput` in `native/Design.swift`; the pixel check
  (two different secrets draw identical pixels while masked) runs inside the native lifecycle gate; `tests/test_secret_masking.py` pins that every
  token shown or typed uses them. No auth change.
- **Free-memory floors = 2.8 GB** (the FREE memory a job needs before it starts, NOT its peak) for Qwen3-TTS 0.6B 8-bit and 4-bit, VoxCPM2 4-bit,
  OmniVoice bf16, FLUX.2 Klein 4-bit and Z-Image-Turbo 4-bit. The owner chose 2.8 GB on 2026-10-02 for headroom (live idle 8 GB Macs show 3.27-5.22 GB
  free). Source: the real 8 GB M1 + M2 runs on 0.6.128 (1406 / 1407, 2026-10-02): jobs started
  cleanly from 3.09-4.65 GB free and peaked at 3.65-4.41 GB (voice) / 2.16-2.97 GB (image) with ~0 swap, so the old floors (4.1-4.6 GB for voice, taken
  from peak + margin) made healthy 8 GB Macs wait for memory they did not need. Each floor's `min_free_memory_note` says what was measured.
  Where it lives: the Voice catalog rows (`components/voice/app/backend/catalog.py`); the Image rows (`generation.py`); and for FLUX.2 Klein the
  Hub's `memory_admission._FLEET_DEFAULTS`, which OUTRANKS the worker's catalog row for the free floor (2.0 -> 2.8), so it is set in both. Z-Image-Turbo
  has no fleet default (it would also override its manifest's 8 GB total tier); its catalog row went 3.0 -> 2.8. Breeze and Fish floors unchanged.
- **8 GB runners no longer confuse peak with floor:** the TTS and image runners' summary printed "Recommended free-memory requirement: peak + 0.25 GB",
  which is how the floors above were set too high. It now prints "Peak memory used: X GB" and "Started with Y GB free" (lowest of the clips), and the CSV
  verdict says "peak memory used X GB" instead of "real need about".

## 0.6.130 — 2026-10-02 · Even Fish / VoxCPM2 / Chatterbox / Pocket pauses, Fish 4-bit on 16 GB, Breeze back to 16 GB only

Release of `integrate/0.6.130` (merges fix/pauses-tiers-0130, commit 645d542; no conflicts). Owner decisions 2026-10-02:
- **Pauses:** every family that joined sections with the flat 0.12 s pause (Fish, VoxCPM2, Chatterbox, Pocket) now uses the same text-led pauses
  as OmniVoice and Qwen3-TTS 1.7B: 0.30 s after a sentence, 0.60 s at a paragraph break.
- **Fish Audio S2 Pro 4-bit moves from the 24 GB tier to the 16 GB tier** (from the measured 7.4-8.1 GB S2 peak; catalog row, manifest
  qualification, capture spec, digest pin and benchmarks page agree). The 8.4 GB free floor is unchanged.
- **Breeze TTS 2 4-bit is carried to 16 GB Macs only again** (the 0.6.129 8 GB carry is reverted; its ~5 GB footprint is too tight on 8 GB).

## 0.6.129 — 2026-10-02 · Models can no longer be lost to a test, Fish S2 Pro 4-bit, Fish decodes in far less memory, Breeze on 8 GB Macs

Release of `integrate/0.6.129` (merges fix/library-ownership, feat/breeze-8gb-carry, feat/fish-4bit, in that order; no conflicts).
- **Library ownership (data-loss fix):** only the Hub that owns a model library may move packages out of it. A Hub started with a throwaway
  `--data-dir` against the real library (`~/StudioHubModels` or a kit's `model-library`) used to shelve/retire models into the throwaway
  directory's trash, so deleting the directory destroyed the weights. The library now carries an ownership record and a Hub that does not own
  it refuses to move anything; `qualification/verify-launch.sh`, `tests/test_peer_path_contract.py` and the other launch tests use a temporary
  library (`qualification/library_guard.py`, `tests/test_library_ownership.py`, `tests/test_no_real_library_launch.py`).
- **Fish Audio S2 Pro 4-bit (MLX)** (`majentik/fishaudio-s2-pro-MLX-4bit`, 24 GB tier, ~4.45 GB on disk, Fish Audio Research License recorded
  in the catalog): the package carries the codec from the 8-bit release (`codec.safetensors`, bit-identical tensors) because the 4-bit
  repository's own `codec.pth` is read by mlx-audio 0.5.7 as LLM weights and every job would succeed with noise. Free-memory floor 8.4 GB
  (measured, S2 peak 7.43-8.10 GB + 0.25 GB margin).
- **Fish decoding is memory-bounded** (`mlx_memory`, Fish family `bounded` profile): slabbed codec decode with context, reference encoded once,
  MLX cache capped. Fish S2 Pro peak 14.6 -> ~7.4-8.1 GB, same audio. Applies to every Fish tier.
- **Breeze TTS 2 4-bit is copied to 8 GB Macs** by the kit's copy step (owner-approved trial for the 8 GB TTS run).
- Merge note: the Fish branch's 8.4 GB free floor and `bounded` profile are kept as written; the Breeze copy rule and the ownership guard are
  independent of both.

## 0.6.128 — 2026-10-02 · Image and voice fit 8 GB Macs, Gemma retired, chat JSON fix, sidebar fix

Release of `integrate/0.6.128` (audit 2026-10-01 fixes; merges fix/retire-index, fix/kit-index-retired, fix/chat-json-system, fix/image-memory-8gb,
fix/tts-memory-ceiling, ui/design-pass-2). Highlights; the per-feature engineering notes follow.
- **Image memory (8 GB tier):** FLUX.2 Klein 4-bit footprint 9.4 -> 2.9 GB and Z-Image-Turbo 4-bit 7.5 -> 4.1 GB (staged + streamed, bounded VAE decode, MLX cache cap); the model stays loaded between jobs on larger Macs; all image jobs run on one inference thread, the JPEG encode runs off it.
- **Voice memory:** one codec decode per section no longer balloons (slabbed decode with context, reference encoded once, MLX cache capped): VoxCPM2 / Qwen3-TTS / OmniVoice peak ~3 GB, Breeze ~4.3 GB, with identical audio. New option: Qwen3-TTS 0.6B Base 4-bit (lighter, 8 GB tier).
- **Gemma 3 12B retired** (owner): every Mac, keep-all Macs included, moves its copy to the Trash Folder; 27 packages, ~78.0 GB.
- **Memory admission is bounded:** a job that waits ~4 minutes on the Hub's own free-memory floor, on a Mac with none of our work running, is handed back `INSUFFICIENT_MEMORY` (needs X, has Y) so the Gateway re-routes it. No free-memory floors were changed (Klein 2.0 fleet default, Z-Image 3.0); the measured footprints are in the catalog notes and the floors are to be set from the 8 GB runner.
- **Chat:** a JSON-mode request that also carries a system message no longer fails on Qwen3.5 / Gemma templates.
- **Kit:** retired models leave the kit index; Technical-folder runners updated (Qwen 4-bit in the TTS list, Klein's 8 GB tier pins).
- **UI:** the sidebar list takes the remaining height and scrolls, the footer is its own row and drops the counts line in a short window (no overlap at the minimum window).

### Gemma 3 12B 4-bit is retired everywhere

Owner, 2026-10-02: "it's bad ... remove it all over". `gemma-3-12b-it-4bit` / `mlx-community/gemma-3-12b-it-4bit` (LTX's text encoder; shelved
in 0.6.127) moves from `components/shelved_models.py` to `components/retired_models.py`. Every Mac - **keep-all-models Macs included** - moves its
copy into the self-cleaning Trash Folder at the next Hub start (`retired_models.retire_installed`, 15-day purge) and drops its `installed.json`
receipt; the kit never lists, stages or copies it; a job naming it is refused `MODEL_RETIRED`. Its manifest is parked in
`components/video/retired/` beside LTX's (dormant; the Video worker is not shipped), the capture rows are gone, and the package counts are
recomputed: **27 packages, 78,024,484,788 bytes (~78.0 GB)** in the catalog README, kit README, SSD-KIT-README, NODE-SETUP-CHECKLIST,
runtime-contract and `docs/model-benchmarks.md` (regenerated). Tests: `test_retired_models`, `test_video_retired`, `test_shelved_models`
(Gemma retired not shelved; a keep-all kit is not given it), `test_model_manifest_catalog`. Left for the coordinator: the 8.07 GB copy on each SSD's
`model-library/gemma-3-12b-it-4bit` (move to the trash, as for LTX).

### Image: memory-bounded generation for the 8 GB tier, resident models on larger Macs

**Cause (owner's real 8 GB M1 run, Hub 0.6.126, guard bypassed):** FLUX.2 Klein 4B 4-bit peaked at 9.8 GB of MLX memory and
swapped ~4 GB (3.9 GB written to the SSD) per image; Z-Image-Turbo 4-bit in `--low-ram` swapped 9.6-33 GB per image. Measured per stage:
FLUX.2's VAE decode alone peaks at 9-12 GB at 1280x720 (convolution/GroupNorm temporaries on a 0.17 GB model); mflux encodes the prompt
lazily, so the text encoder's weights stay alive into the first denoise step; `generate_image` keeps a closure (and, off M1/M2,
an `mx.compile` cache) over the transformer, so nothing is freed before the decode; and Z-Image's loop re-touches 3.5 GB of
transformer weights every step on a Mac with ~4 GB to give, which LRU evicts worst (dirty pages: swap, every step).

**Fix (`components/image/app/backend/`):** `staging.py` (evaluate embeddings then free the encoder; free the transformer when the loop
ends), `vae_bounded.py` (FLUX.2 decode in bands with global GroupNorm statistics; 12.1 -> 2.5 GB, PSNR ~50 dB vs mflux's decode, no
seams), `streaming.py` (transformer/encoder blocks loaded from their files one at a time and released: read-only, never swapped; the
pass over a model whose weights are used once costs nothing, the per-step re-read is ~0-8% of a step), MLX cache cap 0.5 GB. Tiers
(`generation.memory_plan`): below 12 GB staged + streamed; Klein 12-24 GB lean (cache cap + bounded decode); 24 GB+ unchanged. Image now
answers the shared five-mode memory policy (`auto` = `balanced` from 16 GB): a model whose plan keeps every component loaded stays
resident for the idle window (released on idle, the Hub's release, memory pressure, another configuration or a failed job; never
preempts a running job); all jobs run on one inference thread (MLX streams are per thread). `docs/mlx-memory-discipline.md` records the rules
for every future model; `tests/test_image_memory_profile_guard.py` requires every image model to have a profile or a documented exemption.

**Measured** (32 GiB M2 Max with an 8 GB Mac's MLX limits; the "before" reproduced the real M1 run's 9.8 GB; `qualification/evidence/image-memory-2026-10-01*`):
Klein 4-bit footprint 9.4 -> 2.9 GB (MLX 9.8 -> 1.6 GB), Z-Image-Turbo 4-bit 7.5 -> 4.1 GB (MLX 6.3 -> 2.8 GB), same seconds on this Mac;
Z-Image images byte-identical to before, Klein within bf16 rounding of the decode. Not yet confirmed on a real 8 GB Mac (swap, SSD writes):
re-run the 8 GB image runner (branch `runner/8gb-image`) with this build.

## 0.6.127 — 2026-10-02 · Disk protection, remote startup, compression at source, memory hand-back, real cancel, design refresh

Release of `integrate/0.6.127` (audit 2026-10-01 follow-ups). Highlights; the per-feature engineering notes follow.
- **Disk governor:** a Mac keeps its own disk from filling (tiers, ordered reclaim, output caps, preflight, log rotation, 90-day Voice history, periodic job-history trim); `disk` object in `/api/hub/resources`.
- **Remote startup control + auto-start:** each Mac reports what it starts at login (`startup` object in `/api/hub/resources`) and the fleet can change it; joined Macs serve by default; kit install/update open the app with `--fleet-mode`.
- **Compress at source:** optional `output_format` for music, voice and image (m4a / jpeg), forwarded from the Gateway.
- **LTX retired + no Video worker:** app ~0.5 GB smaller; Gemma kept in the catalog but shelved (stays on the SSD / "keep all models" Macs only).
- **INSUFFICIENT_MEMORY hand-back:** after 3 memory refusals the job is returned to the Gateway so it can re-route; capacity back-off up to 300 s.
- **Cancel:** a cancel the worker confirms is `cancelled`, not uncertain (image/music/video/subtitle); delivery_fallback_reason carried onto the job item.
- **Speed/health:** cheaper resources and jobs listing, quieter service log, voice artifacts verified while streaming, device_settings atomic read-modify-write (a damaged file fails safe to keep all models), update drain keeps dispatching to idle nodes.
- **UI:** sidebar names this Mac and its place in the group; Music player card and row actions fixed; Hub reports each machine's current job; design refresh (type scale, spacing, status colours, one page scaffold, aligned tables).

### Gemma 3 12B 4-bit is shelved: it stays on the SSD, not on fleet Macs

Owner: Gemma 3 12B 4-bit (`gemma-3-12b-it-4bit`) is no longer carried by every Mac. It stays in the catalog, on the SSD kits and on any Mac
marked "keep all models", so it can still become a chat model later; it is just not copied to fleet Macs any more, and a fleet Mac that
already has it moves it into the self-cleaning Trash Folder (emptied after 15 days) the next time the Hub starts. One line in
`components/shelved_models.py`; the kit's copy step, the start-up sweep and the "do not fetch it back" rule all read that list.

### LTX-2.3 video is retired; the release ships no Video worker

Owner: LTX-2.3 is "bad quality, can't use it" - retired exactly like Lance (0.6.120) and ACE-Step 1.5 (0.6.116), by one line in
`components/retired_models.py`: `ltx-2-3-22b-ram-12gb-mlx` and `baa-ai/LTX-2.3-22B-RAM-12GB-MLX` (LTX-2.3 22B MLX mixed 2-8 bit, RAM-12GB).
- **Gemma is NOT retired** (coordinator, 2026-10-01: the owner may reuse it as a chat model). `gemma-3-12b-it-4bit` (Gemma 3 12B 4-bit,
  `mlx-community/gemma-3-12b-it-4bit`) stays in the catalog, kit listing and copy rules. Verified that nothing else used it as a text
  encoder: the only `requires_packages` in `model-manifests/` was LTX's, and `VideoModel.requires` (video generation.py) the only code
  reader. Chat is `qwen3-5-9b-4bit`; Music's `t5gemma` (Stable Audio 3) is a different checkpoint; Story/Gateway "gemma-4" ids are unrelated.
  It is still engine `video-ltx` (so it is registered and routes), but no studio runs that engine now, so it appears in no picker (the
  app builds pickers from the workers' catalogs) and a job for it is refused `STUDIO_NOT_RUNNING`.
- **What "retired" does, from the one list:** never offered (catalog, kit listing, `release_kit`, `model_ids`, staged manifests), refused by
  name (`MODEL_RETIRED`), dropped from the aggregate catalog a controller publishes even when a not-yet-updated node still reports it, skipped
  by `copy_models_for_this_mac.py` (which also prunes it from a Mac that has it), dropped from the model record.
- **No Video worker ships.** `stage_voice.stage()` ignores `--video-runtime` (NOTE printed) while the LTX package is retired, so the release
  manifest has no `video` component: no `runtimes/video` (**517 MB**, the installed app 4.1 GB -> ~3.6 GB), no `video_worker.py`, no
  `components/video`. A Video worker whose only manifest is retired would otherwise stop the whole Hub at start-up (`_video_spec`). The
  Hub reports no video model, no `video.generation` capability, and refuses a video job with a named code (never a 500, never queued).
  `components/video/`, `packaging/video-runtime/` and the LTX manifest stay in the repo, dormant, for a successor model; the manifest moved out of
  `model-manifests/` (so no catalog, count or doc sees it) to `components/video/retired/ltx-2-3-22b-ram-12gb-mlx.json`, and its capture row left
  `capture_model_manifests.py`.
- **Trap fixed on the way:** the YuE2 runtime (`runtimes/music-yue2`, the sung-song engine, ~698 MB) was staged INSIDE `_stage_video`, after its
  "no video runtime" early return - so a build without `--video-runtime` silently shipped Music with no sung songs. It is now staged by
  `_stage_music` and recorded on `components.music` (it was `components.video.yue2_runtime_*`; nothing else read it). Also fixed: the
  `--yue2-runtime` branch validated `video_runtime` instead of the YuE2 tree.
- **Updates keep working both ways.** `ReleaseInstaller.verifyDeclaredComponents` (native/UpdateClient.swift) checks only the components a
  manifest declares, and `runtimePaths` still lists `video`: an app accepts a release with or without Video (the 0.6.120 incident was a
  release that DECLARED `video` to an installer that did not know it). The 0.6.121 BRIDGE label (`'runtime': 'voice'` in `_stage_video`) and
  its test are untouched: today nothing declares `video`, so the label is dormant; keep it until no Mac runs < 0.6.121 in case Video returns.
- **Native app:** the Video page, with no video model published, says so plainly ("No video model is available...") instead of an empty
  picker and a dead Generate button; the Image-to-video blurb and "Manage Video Models" are hidden in that state. Earlier clips stay listed.
- **Kit/docs:** kit listing 28 -> **27 packages, ~84.4 GB** (LTX 19.1 GB; Gemma's 8.1 GB stays), kit README / SSD-KIT-README / NODE-SETUP-CHECKLIST / runtime-contract / model-manifests README, model record
  (`docs/model-benchmarks.md` regenerated; the LTX reading row removed, its evidence folders kept), HANDOFF stage command (no `--video-runtime`),
  `docs/tasks/video-lane-plan.md` header.
- **Tests:** `tests/test_video_retired.py` (real Hub app + the real catalog with no Video worker: package/catalog/capability routes, LTX absent,
  Gemma present, refusals `MODEL_RETIRED` / `MODEL_NOT_PACKAGED` / not-ready for Gemma; a release without `video` spawns the other five
  workers; installer table still knows `video`; Swift Video page state), `test_retired_models` (LTX ids + Gemma stays offered),
  `test_voice_bundle` (retired Video not staged, YuE2 still is). Mutation-proved: removing the two ids fails three tests.
- **A Mac sheds an installed retired model by itself:** new `retired_models.retire_installed()` (called at Hub start-up, beside the shelved
  models' step) moves any installed retired package's folder (`<library>/<package id>`) into the self-cleaning Trash Folder
  (`<data dir>/trash/<UTC date>/retired-models/`, purged after 15 days) - a same-volume rename, never a delete, a failed move is skipped and
  retried at the next start. It applies to every id on the list (so a leftover ACE-Step / Lance folder goes too); Gemma is not on it.
  (Before this, a retired model left on a Mac was only moved by the copy step's prune, into the library's own `_trash`.)
- **Left for the coordinator when this ships:** move LTX (17.81 GiB) from each SSD's `model-library/` (the 517 MB `runtimes/video`
  inside the old app copy goes away by itself with the app swap).

## 0.6.126 — 2026-10-01 · Long text-to-speech: even pauses between sections, short lines match their neighbours

**Cause (owner: "it's glaring"; audit `_orchestra/audit-20260930/tts-samples`, 300 joins, every engine):** a long script is rendered as
sections and joined by `_join_long_form_wavs` (`components/voice/app/backend/generation.py`), which wrote each section whole and then
appended the designed pause as exact zeros. Every engine returns a section with up to ~1.2 s of near-silence at its start and/or end
(-45 to -60 dBFS), so a designed 0.12-0.6 s pause was heard as median ~1.0 s (OmniVoice 0.98, VoxCPM2 ~1.0, Qwen3 1.7B 1.06, max 2.9 s)
and uneven from join to join. The one-line S4 section ("Father, there is a boat out there!") also came out 3-8 dB off its neighbours:
`loudnorm` cannot measure a ~1.5 s section well.

**Fix (all TTS engines, one place, after the existing `loudnorm` pass):** (1) each section is cut back to its speech: first/last run of
>= 30 ms of 10 ms windows above a threshold relative to the section's own level (95th-percentile window level - 33 dB, clamped to
-60..-40 dBFS), keeping 30 ms before and 60 ms after, with a 10 ms fade at each real cut; the inserted zeros are the designed pause
minus the pads kept either side, so the silence between two sections is the designed pause (unchanged values: engine join pause, or the
OmniVoice / Qwen 1.7B paragraph / sentence / soft sequence). Speech is never cut; a section with no speech-like run, or too short to
analyse, is joined untouched. (2) a section with < 4 s of voiced audio is moved toward the median voiced level of the sections within two
places, by at most +/-6 dB and ignoring < 0.5 dB; peaks the gain pushes past the -1.5 dB ceiling are rounded off by a soft knee, so
nothing clips. Single-section renders are not joined and are not touched.

**Measured on the audit's real clips** (the 36 of 40 long clips whose section sizes add
up to the file, re-joined from their own sections, 274 joins; `_orchestra/audit-20260930/join-fix/`): audible gap median 0.85 s -> 0.14 s (designed pauses 0.12-0.6 s), max 2.92 s ->
0.60 s; after-minus-designed median 0 ms, 95th percentile 30 ms, worst 60 ms. Short-section level step vs neighbours: worst 7.3 dB -> 2.0 dB.

Note for the Qwen3-TTS 1.7B Base audit contract: `private_edge_destructive_trim` stays `False` (speech is never cut); this only removes
near-silence the model itself rendered, as the 0.6B and every other engine's sections already were.

Tests (mutation-proved: 7 of 11 fail on 0.6.125's joiner): `components/voice/app/tests/test_long_form_join_edges.py`: pause equals the
designed one +/-20 ms at 0.12 / 0.3 / 0.6 s whatever the edges, per-join pauses, whole-render edges, speech samples preserved bit for bit,
no-speech section untouched, short-section gain (matched, bounded both ways, no clipping, no reference -> left alone).

## 0.6.125 — 2026-10-01 · The Hub's "free memory" matches what macOS can give; the 8 GB TTS runner measures held models

**Cause (real 8 GB Mac "1101", Hub 0.6.122):** the Hub held Qwen3-TTS 0.6B 8-bit ~11 min ("waiting for memory: needs ~3.2GB, ~3.0GB
free") on a Mac macOS itself called healthy (pressure normal, swap 0). `host_stats()["available_gb"]` was psutil's `available`,
which on macOS is free + speculative + inactive pages: it leaves out the file cache that is *active* (macOS gives it up on demand,
never to swap) and purgeable pages, so it ran 1-2 GB short of what a new job could really use.

**Fix:** one helper, `components/host_memory.py` `available_bytes()` = max(psutil's figure, free + file-backed + purgeable pages), less
the kernel's own free-page target (`kern.vm_page_free_target`), never more than total - wired - compressor; read from the kernel
counters (`vm.page_free_count`, `vm.page_pageable_external_count`, `vm.page_purgeable_count`, `vm.compressor_bytes_used`) with no
subprocess, and psutil's figure on any other platform or a missing counter. Used by `resources.host_stats()` (Hub gate, model health
"fits now", capabilities, metrics, peers), the Voice worker's `_memory_snapshot()` (preflight, "fits now" inventory) and its resource
telemetry, and the 8 GB TTS runner's records. The 3.2 GB requirement itself is unchanged. Note `memory_pressure`'s "free percentage"
(`kern.memorystatus_level`) is NOT headroom: it is (active + inactive + free + speculative) / total, so it stays at 87% on a 32 GB
Mac while free pages fall to near zero (measured by allocating 12 GB); it only drops as wired or compressed memory grows.

**Measure a model the guard would refuse** (owner: "do this along with the 8 GB TTS runs"): `PUT /api/hub/memory/admission`
`{model, min_free_memory_gb | null}` (and `GET ...?model=`) sets/clears the existing operator override
(`memory_admission.set_override`, which had no route). While one is set the Hub forwards it to the Voice worker as `memory_floor_gb`
(`Txt2SpeechBody`), and `GenerationManager._memory_preflight` uses it instead of its own size + headroom estimate, so the two guards
never disagree. `qualification/eight-gb-tts-run.sh`: a model held for memory is cancelled, then re-run with that model's requirement
lowered to `TTS8_RELAX_FREE_GB` (0.5) and a safety net (cancel if swap grows > 1.5 GB or pressure reaches urgent); the override is put
back after the model, on any exit (finally / atexit / SIGTERM / SIGHUP), and at the next start from `tts8-guard-restore.json` if a run
died. New `results.csv` columns `guard_relaxed`, `override_used_gb`, `measured_peak_gb`, `swap_delta_gb`, `hub_free_floor_gb`,
`verdict`; `summary.txt` recommends a requirement = measured peak + 0.25 GB, labelled as measured on that Mac. An older Hub without the
route is skipped as before.

Tests (each mutation-proved): `tests/test_host_memory.py` (recorded 32 GB Mac + synthetic 8 GB 1101-shaped / tight / wired-heavy
fixtures), `tests/test_hub_jobs.py` (override route + forwarding + clearing), `tests/test_tts_8gb_runner.py` (`MeasureAnywayTests`),
`components/voice/app/tests/test_priority_mlx_models.py` and `test_model_defaults.py` (worker honours `memory_floor_gb`; snapshot uses the helper).

## 0.6.124 — 2026-10-01 · Chatterbox is shelved; the 8 GB text-to-speech test (supersedes 0.6.123, never published)

0.6.123 was never published or installed anywhere; this release carries everything in it (relaunch safety net,
shelved models) and adds two owner-requested changes.

**A. Chatterbox disqualified = shelved** (owner: "chatterbox sucks ... we don't retire it completely, just stop
copying it ... both chatterboxes"). `components/shelved_models.py` `SHELVED_MODELS` gains `chatterbox-4bit`
(`mlx-community/chatterbox-4bit`), `chatterbox-8bit` (`mlx-community/chatterbox-8bit`) and their companion
`s3tokenizerv2` (`mlx-community/S3TokenizerV2`). Checked in every manifest: `s3tokenizerv2` appears in
`requires_packages` of the two Chatterboxes and nothing else, so it goes with them (left alone, the copy step would
still have carried it). Same semantics as the other shelved models: they stay in the catalog and work where their
files are; the kit's copy step skips them ("Shelved, not copied"); a Hub on a Mac not marked `keep_all_models`
moves an installed copy into its Trash Folder at start-up (15 days) and refuses to download it; the weights stay in
the SSD model library. **Mark the owner's own Mac keep-all BEFORE it starts 0.6.124**, or its Chatterbox 4-bit /
8-bit / S3TokenizerV2 folders move to the Trash Folder at that first start (recoverable for 15 days).
`tests/test_shelved_models.py` seeds and the Hub-sweep script now include the three; the existing "no kept model
requires a shelved one" test is what proves S3TokenizerV2 is safe to shelve.

**B. The 8 GB text-to-speech test** (`qualification/RUN-8GB-TTS.command` + `eight-gb-tts-run.sh` +
`README-8gb-tts.md` + `tts-script-S2.txt` / `tts-script-S4.txt`), the TTS twin of the 8 GB Z-Image run. Double-click
on the 8 GB Mac; it runs Qwen3-TTS 0.6B 8-bit, then VoxCPM2 4-bit, then OmniVoice bf16, each reading the same two
texts (S2 ~175 words, S4 ~3 minutes, byte-for-byte the 32 GB test's) in "Chris - Soft and Soothing" (`cd07b21a2ccf`)
and "Christian - Slow & Calming" (`0616aec33b41`), one job at a time, through the local Hub's own
`POST /api/hub/jobs` (modality `voice`) so scheduling, section splitting and joins are production's. Pre-checks, each
refusing in plain words and writing nothing: RAM (`hw.memsize`; non-8 GB refuses unless `--quick` /
`--allow-non-target`), Hub running and >= 0.6.122, the three packages `files_verified` + `eligible` and the two voices
present (via the Hub's own model-packages / model-health / voices views), and no active Hub jobs (waits up to 30 min,
re-checked before every clip). Per clip: status + error, wall time, audio seconds, RTF, peak `footprint` of the voice
worker process tree (every second), memory pressure, swap before/after/peak, responsiveness, and a flag when a clip
takes over 3x its 32 GB time. Writes a timestamped folder beside the script (Desktop if read-only) with the WAVs,
`results.csv` (the 32 GB `summary.csv` columns plus status, error, memory pressure, swap, RAM, macOS, app version,
chip, 32 GB reference, ratio ...) and a plain-English `summary.txt`. Changes nothing (no setting, install or delete);
keeps the Mac awake with `caffeinate` while it runs. The program is Python embedded in the shell file and run by the
installed app's own interpreter (no system Python needed). `--quick` = S2 in Chris's voice for each model.
Shipped through `.build/prepare-kit.sh` (copy + `chmod` + added to the Technical folder's managed set so the prune
keeps it). `tests/test_tts_8gb_runner.py` pins the ids against the catalog, the texts, the wrapper, and the kit step.
Verified on the 32 GB Mac against the real local Hub 0.6.122 (`--quick`, all three models: completed, S2 audio
68.4 / 78.8 / 68.08 s, equal to the 32 GB test's).

## 0.6.123 — 2026-10-01 · An update can never leave a Mac stuck; shelved models (never published; carried in 0.6.124)

Two owner-approved changes.

**A. Relaunch safety net** (owner: "can we remove that barrier?"). 0.6.122 fixed the hang; this makes sure no
future bug can strand a Mac. The helper `relaunchAfterUpdate` starts (`/bin/sh`, now `RelaunchHelper` in
`native/StudioHub.swift`) waits for the old app pid; if it is still alive after **120 s** it sends SIGTERM, waits
**10 s**, sends SIGKILL, then runs `open` on the new app. The normal path (the old app exits by itself) is
unchanged: no signal is sent. The helper is now spawned with `posix_spawn` in **its own session**
(`POSIX_SPAWN_SETSID`) with **no inherited descriptors** (`POSIX_SPAWN_CLOEXEC_DEFAULT`, stdio on /dev/null): the
login launch agent (`KeepAlive`, no `AbandonProcessGroup`) makes launchd kill what is left in the app's process
group when it exits, which would have taken the helper with it, and an inherited data-folder lock descriptor would
have kept the new copy out. Seam `spawnRelaunchHelper` now takes `(pid, app path)`.
`tests/native/lifecycle_main.swift` `relaunch-helper` runs the real helper against stand-in old apps: one that exits
by itself (opened, never signalled), one that never exits (SIGTERM), one that ignores SIGTERM (SIGKILL); each must
open the new app only after the old pid is gone, and the helper must be its own session leader.
Mutation-proved: no escalation, no SIGKILL step, no SETSID, kill straight away - each fails an assertion.
A Mac on 0.6.119-0.6.122 still relaunches with its own old helper when it takes THIS update (no safety net
until it is on 0.6.123).

**B. Shelved models** (owner, 2026-09-30). `components/shelved_models.py` (`SHELVED_MODELS`), beside
`retired_models.py`, seeded with Whisper base (`whisper-base`, `mlx-community/whisper-base-mlx`), Whisper small
(`whisper-small`, `mlx-community/whisper-small-mlx`), VoxCPM2 bf16 (`voxcpm2-bf16`,
`mlx-community/VoxCPM2-bf16`) and Fish S2 Pro bf16 (`fish-audio-s2-pro-bf16`,
`mlx-community/fish-audio-s2-pro-bf16`). Whisper turbo is not shelved.
- **Still in the catalog**, offered and usable wherever the files are. Manifests are still staged; the kit's
  model library on the SSDs keeps the weights (the simplest thing the kit tooling already supports: the shelf is
  "the library the copy step skips").
- **Kit copy step** (`qualification/copy_models_for_this_mac.py`): skips shelved models ("Shelved, not copied")
  unless the Mac is marked keep-all. It never removes one already on the Mac.
- **Hub**: at start-up, on a Mac not marked keep-all, an installed shelved model's folder is moved (same-volume
  rename; a failure is skipped) into the Trash Folder `trash/<UTC date>/shelved-models/<package>-<time>/`, which
  the Hub purges after 15 days. The row then reads `state: not_installed`, `shelved: true`, reason "Not on this
  Mac: ... is a shelved model ..." (never `invalid`); `dispatch_state` stays `ineligible`. `POST
  /api/hub/model-packages/{id}/download` and `/import` answer 409 with that reason for a shelved model on such a
  Mac, so nothing brings one back by itself (a moved one would be moved out again at the next start).
- **Keep-all device setting**: `keep_all_models` in `device_settings.json` (default **false**; read by the Hub and
  by the copy step). `PUT /api/hub/settings {"keep_all_models": true}` (Hub token) or add `"keep_all_models":
  true` to `~/Library/Application Support/StudioHub Standalone/hub/device_settings.json`. "Restore defaults"
  keeps it (it is about models, not output limits); a save that does not name it keeps it. **THIS Mac must be
  marked keep-all BEFORE it starts 0.6.123, or its Whisper base/small, VoxCPM2 bf16 and Fish S2 Pro bf16 folders
  move to the Trash Folder at that first start** (recoverable for 15 days, then purged).
- Whisper turbo is already the Subtitle default (`"default": true` in its manifest, the only one); the Gateway /
  Story selection of the subtitle model is separate.
- Tests: `tests/test_shelved_models.py` (list vs manifests, turbo not shelved and the default, no kept model needs
  a shelved one, keep-all parsing, copy step skip / keep-all / not pruned, Hub sweep / Trash layout / 15-day purge /
  not-on-this-Mac row / refusals / catalog / default false / survives restore-defaults). Mutation-proved (8 mutants).
- Bridge kept: the video component is still labelled `runtime: "voice"`.

## 0.6.122 — 2026-10-01 · Updates restart the app by themselves

Found in the 0.6.121 rollout: four controllers that took 0.6.119 -> 0.6.121 installed it and then sat
half-quit for ever (Force Quit, then open, fixed each). `AppDelegate.relaunchAfterUpdate` called `NSApp.terminate`
from the release tick, which is a main-actor task. With the engine running, `applicationShouldTerminate` answers
`.terminateLater` and AppKit waits in a nested loop; the engine's exit is delivered as another main-actor task,
which cannot run until the tick returns, so the reply never came, the app never quit and the reopen helper
waited on it for ever. Diagnosis: `_orchestra/audit-20260930/update-0120/diag/relaunch-*`.
- **Fix** (`554ed1f`): the relaunch stops the engine FIRST and quits from `onStopped` (`engineStopped`), so
  `NSApp.terminate` runs from the engine's exit, when nothing is running and `applicationShouldTerminate` answers
  `.terminateNow`. A hung stop is still force-stopped by itself after 15 s. No engine running: it quits at once.
  Seams `spawnRelaunchHelper` / `terminateApp`; `tests/native/lifecycle_main.swift` `relaunch-after-update`
  asserts the order (helper started once, no quit while the engine runs, quit after it stops, idle quits at
  once). Mutation-proved (terminate straight away -> the quit-while-running assertion fails).
- **Other paths audited:** no other `NSApp.terminate` runs inside a main-actor task. The user-Quit path
  (`applicationShouldTerminate`) is reached from a menu/Dock/Apple event on the run loop, not from a task; its
  task only replies (`NSApp.reply`), which is safe. The one remaining `terminate` is the launch-error alert in
  `applicationDidFinishLaunching` (no controller, `.terminateNow`).
- **Bridge kept:** the video component is still labelled `runtime: "voice"` (`0.6.121`). Macs still on
  0.6.119/0.6.120 verify an update with their own table (no `video`) and must be able to take this release. The
  bridge can only be reverted once no Mac runs below 0.6.121.
- **A Mac on 0.6.119-0.6.121 still has the bug when it takes THIS update:** its own (old) app does that one
  relaunch. After the install the new app is already in place; Force Quit the old process (or `kill` it) and
  open Studio Hub. From 0.6.122 on, updates restart by themselves.

## 0.6.121 — 2026-10-01 · Macs can install updates by themselves again

Found in the 0.6.120 rollout: no controller or node updated. `ReleaseInstaller.runtimePaths`
(`native/UpdateClient.swift`) had no `video`, so `verifyDeclaredComponents` refused, AFTER staging, every
release whose manifest ships Video with `runtime: "video"`. An idle Mac took the offer, staged it, failed the
check, set `autoUpdateFailedBuild` and never retried; nothing showed off the Mac. Diagnosis:
`_orchestra/audit-20260930/update-0120/diag/` (the real 0.6.120 bundle fails the real check; the same bundle with
the label `voice` swaps).
- **Fix** (`7031035`): `video` joins `runtimePaths` (`Contents/Resources/runtimes/video/bin/python3`). Tests: the
  real 0.6.120 `release-manifest.json` is a fixture and must pass `swapStagedApp`; the fake-app helper builds the
  real layout instead of skipping runtimes missing from the table (that skip kept the old tests green by
  construction); `video` joins the per-modality missing-worker loop; a Python invariant
  (`tests/test_installer_knows_shipped_runtimes.py`) checks every runtime `stage_voice.py` writes is one the
  installer verifies. Mutation-proved (table entry removed -> both fail).
- **BRIDGE** (`28ad677`): the apps already installed (0.6.119/0.6.120) check a staged update against THEIR table,
  which has no `video`, so they can never take a release that says `video`. For THIS release only,
  `packaging/stage_voice.py` labels the video component `'runtime': 'voice'` (nothing reads that label except the
  installer check; `_video_spec` reads `runtime_sha256`). Verified by running the real 0.6.119 installer source
  against the staged 0.6.121 app.
- **Reverting the bridge** (set `'runtime': 'video'` back in `_stage_video`, delete the BRIDGE comment and
  `test_bridge_release_video_is_verifiable_by_the_apps_already_installed`) is possible only once no Mac runs
  below 0.6.121. 0.6.122 keeps it (see below).

## 0.6.120 — 2026-09-30 · Lance video is retired; video is LTX-2.3

Owner: "the Lance one sucks and is terrible" - retired the way ACE-Step 1.5 was (0.6.116). Video is now
**LTX-2.3 22B MLX mixed 2-8 bit (RAM-12GB)** plus its `gemma-3-12b-it-4bit` text encoder, and nothing else
(it was already the default: `MODELS` lists it first and `MODEL_ID`/`DEFAULTS` read it).
- **Retired list** (`components/retired_models.py`): `lance-3b-video-bf16`, `mlx-community/Lance-3B-Video-bf16`,
  `lance-3b-processor-qwen2-5-vl`, `Qwen/Qwen2.5-VL-3B-Instruct`. Never offered, never staged into a kit or
  listed, refused by name (`MODEL_RETIRED`) even if an old manifest sits on a drive.
- **Removed** (moved to `_trash/0.6.120-lance-retire/`, never `rm`): both manifests; `lance_runner.py`; the
  `LANCE_3B` model, `run_lance`, `lance_runtime_python`, the Lance canvases/limits, `VideoModel.lance_steps`/
  `lance_cfg_scale`/`MemoryPolicy.relay_on_below_bytes` and the `video-lance` engine (from `VIDEO_ENGINES`,
  `model_packages.ENGINES`, the Hub `_ENGINE_ROUTE`, the video smoke); `packaging/lance-runtime/`; the
  `--lance-runtime` / `--unqualified-lance-runtime` stage flags, `LANCE_RUNTIME_SHA256` and the
  `runtimes/video-lance` tree (nothing else read it - Lance was its only user); the Lance capture rows;
  `qualification/lance-video-worker-run.py`; the Lance reading in `model-readings.json` (record regenerated).
- Kit/listing: 30 -> **28 model packages, ~103.5 GB** (kit README, `SSD-KIT-README`). The installed app
  loses `runtimes/video-lance` (~1 GB).
- Tests: the Lance pins became "Lance is not offered, LTX is the one video model and the default, a Lance
  manifest left on a drive is not a video manifest"; `test_retired_models` covers all four ids. Mutation-proved:
  re-adding a Lance manifest fails four tests; dropping the ids from the list fails the retired test.
- Qualification evidence for Lance (`qualification/lance-video-worker-results-20260927-first/`, the Lance
  section of `README-video-hardware-test.md`) is kept as history.

## 0.6.119 — 2026-09-30 · Quit no longer asks when nothing is running

Owner: "if no job is running, quit is quit. if have job, then we should ask." `applicationShouldTerminate`
(`native/StudioHub.swift`) used to raise "Quit Studio Hub and stop its engine?" whenever the engine ran.
Now it reads **fresh** active work (`NativeWorkspaceStore.activeWorkNow()`: refresh + memory policy, then
the same `activeWorkCount` the updater's drain uses; bounded to 3 s so a hung Hub cannot hold Quit) and
asks **only if that is above zero**. Idle: the engine stops and the app quits at once. Busy: the same
"Keep Running / Quit and Stop" question. An update relaunch (`relaunching`) never asks. Once Quit is
decided (answered or not needed) `forceStopWithoutAsking` is set, so a stop that hangs 15 s is force-stopped
by itself - no second "Force Stop?" dialog. The in-app Stop Engine / Force Stop buttons are unchanged.
Tests: `tests/native/lifecycle_main.swift` `quit` (idle, busy-keep, busy-quit, relaunch; the question is the
`confirmQuitWhileBusy` seam) and `api_main.swift` (`activeWorkNow` reads fresh). Mutation-proved.

## 0.6.118 — 2026-09-30 · A Mac that never goes idle still updates itself; a push no longer stalls the controller

The live pilot (gateway pushed 0.6.117 to controller 1500) showed two gaps that kept every Mac on the
old version, and a slow controller.

**Drain for update** (owner decision 2026-09-30; `native/StudioAPI.swift` `releaseTick`, new
`components/hub/backend/update_drain.py`). An unattended install needs `activeWorkCount == 0`, which a
Mac under continuous work never reaches. Now a Mac with a verified update waits `updateDrainGrace`
(**10 minutes**, default; `defaults write <bundle id> UpdateDrainGraceSeconds -int N`) for a natural idle;
after that it asks its Hub to **stop taking new work**, lets everything running finish (nothing is ever
killed), installs, relaunches, and takes work again.
- **Refusal code: `MACHINE_BUSY`** (409). It is the code the gateway already files as a location refusal
  (`_LOCATION_REFUSAL_CODES` -> `LOCATION_NOT_READY`, the job is re-routed to another Mac) and the broker's
  own "not available right now" (`_is_machine_reservation_refusal`: the item waits, never fails). Applied
  three ways from one flag: `broker.busy_machines()` names every machine while draining (the scheduler
  starts nothing, peer requests are refused, the chat/transcription lanes cannot take the Mac);
  `_submission_readiness` refuses a submission first.
- **Stagger: one Mac per controller group drains or installs at a time, the controller included.** The
  controller holds one in-memory "update slot"; a node asks for it (`POST /api/hub/fleet/update-slot`,
  fleet and node credentials) before its Hub drains. A controller too old to know the route, or
  unreachable, does not hold a node back. Controllers update first anyway (a node is offered a release
  only once its controller runs it), so in practice a controller claims the slot first.
- **A drain can never wedge.** It is a two-minute lease the app renews every 30 s (through the install
  too); the slot is the same. A failed install ends the drain at once and that build is not tried again;
  an app that dies, a Hub that restarts or a Mac that reboots simply lets the lease lapse.
- While draining, jobs that are only *queued* do not hold the drain open (they wait in the Hub's saved
  queue through the relaunch); running work still does.
- Visible: the sidebar says "Updating after current work finishes..." while an update is waiting or
  draining, and `GET /api/health` carries `update_draining` for the fleet view.

**The key is pinned on the unattended path too.** `releaseTick` returned at once when no controller key
was pinned, and the pin was only made when someone opened Connections, so a Mac installed from a drive
never updated. The tick now pins its own Hub's release-signing key on the first read (trust on first use,
the rule a node uses at join) and verifies the offer against it on the very next tick (10 s later, not 10
minutes): a key read at the same time as the artifact still proves nothing. A different key presented
later is refused, never replaced.

**Other things in the unattended path that needed a person, fixed.** The relaunch after an install stops
the services; after 15 s of a hung shutdown it raised "The local services are still stopping" with a
Force Stop button nobody was there to press, and the update never finished. A relaunch after an update
now force-stops by itself (`forceStopWithoutAsking`). Checked and fine: the manual Install's "jobs are
running" confirmation lives in the view, not in the installer; the quit prompt is already skipped while
relaunching; nothing in the path needs a window, the Connections page or a connection notice.
(`memoryInventory != nil` is still required: a Hub with no memory-policy route cannot say whether a Studio
is busy, so it waits - only a Hub older than the app answers that way.)

**A blob push no longer stalls the controller** (`components/hub/standalone.py`, `backend/app_release.py`,
`backend/release_delta.py`). Every `PUT /api/hub/release/blob/<sha>` recomputed the full missing set
(directory listing plus a walk of ~93,000 manifest entries) on the event loop just to fill in a "remaining"
count no sender reads. The PUT reply is now `{"ok": true, "stored": sha}` (gateway asks `GET /need` once at
the end); the disk and hashing work of a PUT runs in a worker thread; the installed app's sha index is
built once, not per blob served or pushed. `POST /api/hub/release` answers with an additive
`basis: {installed_app, wanted_blobs, needed_blobs}` so a push can say whether the controller measured
`need` against its own installed app (a controller that cannot find it needs the whole release).

## 0.6.117 — 2026-09-30 · A Trash Folder instead of deleting; one list of retired models

Two owner decisions (2026-09-30) and two clean-ups of what an update leaves behind.

**Output cleanup moves work to a Trash Folder; it never deletes it** (`components/hub/output_storage.py`).
- Acknowledged output with no reader and no transfer is now **moved** to `<hub data>/trash/<UTC date>/<batch>/`
  (a same-volume rename) instead of unlinked. A move that fails skips the file; there is no fallback
  to a delete. `max_bytes` counts **live** output only.
- The Trash Folder empties itself: a dated folder older than **15 days** is removed (`purge_trash`, run
  at the start of every sweep, even with cleanup switched off). That purge is the only hard delete.
- **A full Trash Folder cannot wedge admission.** The Trash Folder shares the disk, so when free space is
  below the floor `admission()` first removes Trash folders **oldest day first**, only as many as it
  takes, and refuses only if the disk is still short - copies already scheduled for deletion go
  before work is refused. (Chosen over "refuse until day 16": a refusal for up to 15 days is the
  failure this prevents.)
- **New defaults:** 20 GB of live output, 3 days, 5 GB minimum free (were 50 GB / 30 days / 1 GB).
  A `device_settings.json` written before this release that still holds the old default for a value
  reads as "never chosen" and gets the new one; a value the operator changed is kept. Every save and
  restore stamps `defaults_version`, so choosing 30 days afterwards sticks.
- The storage screen shows the Trash Folder's size, "emptied automatically after 15 days"
  (`POST /api/hub/settings/preview` returns `trash: {bytes, days}`); the limits editor and the
  restore-defaults confirmation now say "moves to the Trash Folder".

**Updates no longer pile up on disk** (`native/UpdateClient.swift`, `native/StudioAPI.swift`).
- `swapStagedApp` moves the replaced app into the Trash Folder (`previous-app-<id>/Studio Hub.app`)
  instead of `removeItem`, so a rollback is possible for 15 days. On APFS the new app is clone-staged
  and shares blocks with it, so this costs about the changed bytes. If there is no Trash Folder or it
  is on another volume than the app, it falls back to the old `removeItem` (a move there would be a copy).
- Full-update downloads (`Updates/<key>/`, about 1.9 GB each) were never pruned. After **any completed
  install**, everything under `Updates/` - the download just installed and any older one - moves to the
  Trash Folder. A failed install moves nothing (the downloads are what the retry resumes from).
  `relaunchAfterUpdate` is unchanged.

**One list retires a model** (`components/retired_models.py`, `RETIRED_MODELS`, seeded with
`ace-step-1-5-turbo` and its model id). The Hub catalog (`_model_catalog`), the identity index
(`model_ids`), the aggregate model list published to the gateway (`_assemble_aggregate_catalog`), the
kit listing (`release_kit`), kit staging (`stage_voice.copy_model_manifests`) and the model-copy step
(`copy_models_for_this_mac.py`) all read it, and a retired model is refused by name
(`MODEL_RETIRED`) on submit and on peer dispatch. So an old ACE-Step manifest on a drive can never
bring it back. Retiring the next model is one line.

**Tests (each mutation-proved, failing on an assertion):** `test_output_trash` (prune moves, failed move
skips, purge only past 15 days and only dated folders, admission not wedged, defaults, old-default
migration), `test_retired_models` (adding an id removes it from the catalog, identity index, kit
listing, staging and published list, and refuses it), the model-copy end-to-end case, and the Swift
harness (old app in the Trash Folder after a swap, stored downloads moved after an install, a failed
install moves nothing, the fallback without a Trash Folder).

## 0.6.116 — 2026-09-30 · ACE-Step 1.5 is retired; music is YuE2 and Stable Audio 3

**Owner decision (2026-09-30):** ACE-Step 1.5 Turbo (2B DiT) is not offered any more. The Hub's music
models are **Stable Audio 3 Medium (1.4B)** (instrumental, now the default) and **YuE2 3B (8-bit AR +
bf16 NAR)** (sung songs, with the 0.6.115 decoder fix).

- **Removed:** `model-manifests/ace-step-1-5-turbo.json` (to `_trash/`), so the drive kits and the
  Models list no longer carry it (catalog 31 -> 30 packages, 126.8 -> 119.1 GB, the 7.7 GB ACE-Step package);
  `components/music/app/backend/engine_child.py`, `generation.EngineExecutor`, `_validate_ace_step`,
  `_run`, the ACE source-tree verification (`source_tree_digest`, `engine_contract_reason`) and the
  now-unused `inspect_wav`; the ACE catalog entry, tier table, long-form strategy, checkpoints and
  family; `packaging/music-runtime/` (the ACE-only Python overlay, 1.7 MB); the staged
  `engines/ace-step-1-5` source (11 MB) and `--music-runtime` / `--music-engine` in
  `packaging/stage_voice.py`; `--engine-root` and the overlay check in `packaging/music_worker.py`
  and `_music_spec`.
- **Stable Audio 3 is the default music model:** `catalog.DEFAULT_REPO` (was `MAIN_REPO`, ACE) is what
  `generation.DEFAULT_MODEL`, the package cache and the worker's own manifest resolve. It is first in
  `/api/catalog`, so the app's music picker opens on it.
- **Music no longer has a runtime of its own.** Both engines already ran outside it (Stable Audio 3
  as a subprocess on the Voice runtime, YuE2 in `runtimes/music-yue2`); the overlay carried
  `vector-quantize-pytorch` and friends only for ACE (checked: every Stable Audio 3 module imports
  without it). The release manifest's `music` record is now `runtime: "voice"` with the Voice
  runtime's digest, which every deployed update client already accepts.
- **Nothing is resident, so the memory policy has nothing to release.** `/api/memory-policy` and
  `/api/memory/release` still answer the fleet contract; `loaded_models` is always empty.
  `/api/health` no longer reports `engine_loaded` / `engine_child_pid` (nothing read them).
- **A Stable Audio 3 job that fails after it was marked done** (a persist failure) now clears the
  record's artifact fields, as the ACE path did, instead of leaving an `output_url` for a deleted file.
- Docs and listings: `docs/model-benchmarks.md` (regenerated), `model-manifests/README.md`, the kit
  README, `SSD-KIT-README.md`, the node checklist, the runtime contract count and the R08 checklist.
- **Tests:** rewritten to assert ACE-Step is *not* offered and Stable Audio 3 + YuE2 are
  (`test_music_package_contract`, `test_music_memory_policy`, `test_memory_policy_contract`,
  `test_music_bundle`, `test_music_lane_contract`, `test_model_manifest_catalog`,
  `test_chat_packaging`, `test_activity_operations`, four hub-regression cases, the native fixture).
  The ACE-only tests (engine child, source digest, resident-child release) went with their code.
- **Rollout:** 0.6.115 -> 0.6.116 is the first delta update. Measured from the two stages' file
  manifests: **20 files changed (about 0.8 MB), 0 added, 798 removed (about 11.5 MB)** - the Music
  worker source, `hub_worker.py`, `music_worker.py`, `model_ids.py`, `memory_modes.py`, the Voice
  catalog, the release manifest and notes; the native binary and all six runtimes are byte-identical.
  The app is about 11.6 MB smaller (5,197,040,792 bytes of files down to about 5,185,460,000).

## 0.6.115 — 2026-09-30 · Scarcest-first scheduling, node removal from either side, delta updates

Three reviewed branches (`wave2-sched`, `wave2-fleet`, `wave2-delta`) merged into one release.
**Rollout note: 0.6.114 → 0.6.115 is itself a full (disk image) update**, because 0.6.114 apps have
no delta path. The first delta update is 0.6.115 → 0.6.116.

**Scheduler** (`broker.py`, `memory_admission.py`, `chat_jobs.py`, `transcription_jobs.py`)
- **Memory is a hard eligibility floor.** A job only goes to a Mac with enough unified memory for
  that model. The requirement is, in order: measured → manifest → catalog
  (`memory_admission.requirement_gb`). A Mac below it is never a candidate, however idle.
- **A freed Mac takes the waiting job that the fewest Macs can run**, so big Macs are kept for the
  work only they can do. Ties: stay on the model already loaded, then oldest. **"Send here" wins on
  its Mac.** Chat and subtitle jobs follow the same order.
- **A busy Mac is retried within seconds.** The 30 s `MACHINE_BUSY` park and the 90 s avoid are gone.
- **Capacity numbers count only Macs with enough memory** for the model.
- Simulation (owner's fleet mix 10 x 8 GB / 5 x 16 GB / 2 x 24 GB / 1 x 32 GB; 500 image + 200 TTS +
  100 LLM jobs): 0 idle-while-eligible, 0 scarcest-first violations, 97-98% utilisation per tier.
- Tests: `tests/hub_regression/cases/test_scarcest_first_dispatch.py`; `test_memory_admission`,
  `test_hub_memory_admission`, `test_below_tier_warning`, `test_pool_peer_machine_busy` and
  `test_image_memory_pressure` rewritten where their premise changed.

**Fleet** (`fleet_nodes.py`, `auth.py`, `standalone.py`, `StudioAPI.swift`, `StudioViews.swift`)
- **Removing a node works from either side.** Controller remove: the node leaves within about a
  minute, or at its next start (it polls `GET /api/hub/fleet/nodes/{id}/status`). Node leave: the
  controller forgets it immediately, and the node retries the notice for up to 14 days if the
  controller is off.
- **A Mac the controller removed is told so, with the date** (a 403 carrying the removal detail
  instead of a bare 401). A Mac that left on its own can re-join.
- **Machines are shown by label everywhere** (the id is a tooltip); renaming a node updates the
  controller. A node that has **never connected since it joined** is flagged. The node id is
  persisted on first run.

**Delta updates** (`file_manifest.py`, `release_delta.py`, `release_sync.py`, `app_release.py`,
`UpdateClient.swift`, `install_app.command`, `packaging/stage_voice.py`)
- **Every build carries a per-file manifest** (`Contents/Resources/file-manifest/`). Controllers
  accept **manifest-only releases** and ask only for the files they are missing (`.../release/need`,
  `.../release/blob/{sha256}`); the signed descriptor commits to the manifest.
- **A Mac stages an APFS clone plus the changed files** (measured 0.6.113 → 0.6.114: 9 files,
  13.5 MB) and **falls back to the disk image** whenever it cannot prove the result.
- **Nodes pull their controller's release** (trust on first use of the publisher key).
- **Controllers auto-install when drained** — the 0.6.114 role gate is gone, so a controller with
  nothing running or waiting now updates itself like a node (this reverses 0.6.114's "controllers
  stay manual"; the sidebar Update badge remains).
- **The kit installer keeps the old app until the new one is verified.**
- Wire contract: `_orchestra/audit-20260930/CONTRACT-hub-release-delta.md`.

**Music**
- **YuE2 music works again on every Mac.** Every YuE2 job failed with *"the YuE2 decoder (yue2-vae)
  is not installed"* although the decoder was installed and verified. `_run_yue2` read a `requires`
  key from the generator's preflight report; that report has never had one, so the lookup was `None`
  everywhere. The decoder's folder now comes from the decoder package's own verified receipt
  (`MusicCache.companion_root`, from its manifest's layout destination), and the dead `requires` read
  and `_companion_root` are gone. Pinned by `DecoderResolvesFromARealLayoutTests` in
  `tests/test_music_yue2.py`, which builds the library the way the installer does from the two shipped
  manifests (no hand-written `requires`); reverting the fix fails it with the owner's error text.

**Merge note.** `auth.py`: both fleet and delta added allow-list entries; all are kept (fleet's
`/fleet/nodes/{id}/status` in `FLEET_ALLOWED` and `NODE_ALLOWED`; delta's release files/blob/need in
`FLEET_ALLOWED` and the five read-only release routes in `NODE_ALLOWED`).

## 0.6.114 — 2026-09-30 · The app says what the Hub already knew

Twelve findings from the native-app audit, all of the same kind: the Hub published the fact and the
app drew a fixed sentence, a green tick or nothing. No routing or scheduling behaviour changes.

**Jobs**
- **A queued job shows its reason.** `governor_note` (and `tries` / `retry_at`) are decoded onto
  `NativeJob`, so *"'X' not downloaded on <machine>"* replaces the hard-coded *"Waiting for a free
  worker"*, and a job in the 30 s `MACHINE_BUSY` retry loop says *"retrying in N s (attempt 2)"*.
  The note used to tell the operator to *"broadcast the download"* — no such action exists — and now
  says *"download it on that Mac, or pin another"* (`broker.py`, a string only).
- **A job row says which Mac ran or holds it** and how many tries a failure took; **failed jobs get a
  Retry button** (`POST /api/hub/jobs/{id}/items/{i}/retry` already existed). A retry drops the batch
  from the finished-job cache.
- **Overview "In progress" includes Video and Subtitle jobs**, which it read as idle.

**Fleet**
- **Health is runnability.** The Fleet page reads `/api/hub/model-health` and shows per machine
  *"N models ready · N need download · N blocked"*; a Mac that answers but has no ready model is
  orange, not green, and `degraded`/`down` are orange instead of the neutral clock. The sidebar
  counts models that can *run* (`runsHere`), not models the Hub merely ships a package for. `speech`
  and `voice` no longer both render as "Voice", and modalities with no completed work are not listed.
- **"Send here" is visible where it applies**: a *"Sending to X · Clear"* chip beside Generate on the
  Image, Music, Video, Audio and Subtitle pages (orange when the pinned Mac is offline), and the
  button is disabled for an offline Mac (clearing an existing pin always works).
- **The Fleet page is a dated report**: *as of hh:mm:ss*, a spinner while Refresh runs, unreachable
  Macs first, and a failed read keeps the last good list and names what failed instead of wiping it
  and saying *"unavailable in this build"*.
- **"Hand over…" asks first**, like rotate, leave and remove.

**Updates**
- **Owner decision, 2026-09-30: a joined node updates itself when idle.** Every ten minutes the app
  reads the controller's offer (it used to be read only when Connections was opened). A node with
  nothing running or waiting — its own jobs, downloads, transcription, and every Studio's own `busy`
  flag, which is where a job sent by the controller shows — downloads, verifies and installs it, then
  **relaunches the app** so the version label is current. A busy node waits (checked every minute).
  Work that arrives while the file downloads defers the swap. A failed unattended install is not
  retried for the same build until the app restarts. **Nothing is read or installed unless a
  controller key is already pinned**, so this can never be the read that pins one; verification is
  unchanged.
- **Controllers stay manual** but the sidebar Connections row carries an *Update* badge.
- **A manual Install asks first when jobs are active**, naming how many will stop.
- **The install no longer freezes the window.** SHA-256 over the DMG, `hdiutil attach` and the 5 GB
  copy ran on the main actor; they run detached, the second hash of the same file is gone, and the
  progress line repaints ("copying the app, which takes a minute or two").

**Model downloads**
- **Progress is real.** The downloader prints nothing while it works, so the Hub measures
  `bytes_done` from the staging folder it writes into (`.part` files included) against `total_bytes`,
  the manifest's own sum; the row draws a bar and *"3.2 GB of 12 GB"*.
- **A failure shows the downloader's own words** (`error_tail`, the last 300 characters of its
  output), not *"Hub model download error."*, and stays on the Models and Overview pages with a
  Dismiss button. `recovery_pending` is drawn.
- **Finished runs are pruned** from `native-model-downloads.json` and no longer polled every two
  seconds for ever, and **one failed read no longer drops a running download** — only the Hub's 404.

**Poll cost**
- Failed batches are cached like finished ones, **but only on the Hub's positive word that nothing in
  the batch is waiting or running** (a Retry makes it active again). Polling is 2 s only while work
  is active and the window is visible, otherwise 10 s, and is skipped while the window is hidden or
  minimised — the update check is not, because a node's window is normally closed.
  `/api/hub/jobs` has no `since`/limit parameter, so it still lists every batch; that is Hub-side
  and was not invented here.

**Storage**
- **Restore Defaults confirms**, naming the limits it replaces. The Hub's `preview()` now returns
  `would_delete_bytes` — the sweep's own arithmetic replayed on the index without touching a file —
  and the limits editor shows *"Saving lets cleanup delete about X GB"* for the values typed.

**Tests:** `tests/test_hub_ops_visibility.py` (download progress and failure text, the storage dry
run proven equal to what `enforce` actually frees, the note wording), `tests/test_native_operator_surfaces.py`
(view wiring), and about forty new checks in `tests/native/api_main.swift`. Two existing tests were
rewritten because their premise changed and both said so: the download tripwire ("the Hub has no
byte counts") and the job-cache test, whose comment already named the native retry invalidation as
owed.

## 0.6.113 — 2026-09-30 · Why a node did not answer, as a field rather than a sentence

**The distinction was always made; it was just not readable.** The peer poll separates two
failures that need opposite responses:

- **`no_hub`** — TCP refused. Something answered the network and nothing is listening on `:47873`,
  so the Mac is up and its Studio Hub is not running.
- **`unreachable`** — packets dropped. A firewall is blocking the port, or **the Mac is asleep or
  off.** Nothing about this says the Hub is at fault.

`peers.py` documents both. But a reader could not *use* the distinction, because the two code paths
that produce an unreachable node spelled it differently:

| Path | What it wrote |
| --- | --- |
| `_from_snapshot` (the peer poll) | `could not reach the node (no_hub)` — the token is there |
| `probe` (a direct dial) | `could not reach http://…: ConnectTimeout` — **no token at all** |

So the only way to learn the reason was to parse a sentence that is written two different ways.

**That is how the Gateway came to tell the owner to "fix Studio Hub on that Mac" for eight Macs
that were simply switched off.** The advice is right for `no_hub` and wrong for `unreachable`, and
with no field to read the Gateway had to give one answer to both.

`reason` is now published on an unreachable node by both paths, in the peer poll's own vocabulary.
`detail` is unchanged — it is still the sentence an operator reads.

**Also published for the same reason:** the direct-dial path classifies with the same rule the poll
uses (`httpx.ConnectError` is a refusal, everything else is a drop), so the two agree by
construction rather than by coincidence.

## 0.6.112 — 2026-09-30 · Two corrections 0.6.111 shipped, both found by auditing it

Both of these are in 0.6.111's own footprint. Neither changes a shipped behaviour; one closes a
hole in a guard, the other stops a comment claiming a guarantee the code does not provide.

**The repaired kit test still had the hole it was written to close.** `0.6.111` correctly stopped
pinning the literal `chmod +x` — `prepare-kit.sh` deliberately uses `755` — but the repair waved
through *any* mode beginning with `+`:

```python
if mode.startswith("+"):
    continue        # `chmod +r` passed a test whose message says the operator must run the file
```

`chmod +r "Update this Mac.command"` would have passed, on a file the operator then cannot
double-click. The mode is now parsed rather than pattern-matched: numeric modes are checked by the
owner-execute bit, and symbolic clauses are resolved (who the clause addresses, `+`/`-`/`=`, and
whether `x`/`X` is among the permissions). `a` counts as addressing the owner, `g` and `o` do not.
Pinned by a table of 21 spellings and **proved** by mutating the script to `644` and to `+r` — both
now fail with the reason named.

**The deferral comment named a bound that is not one.** 0.6.111's `MACHINE_BUSY` branch closed with
*"the batch's own lease, cancellation and the stall detector stay the bounds on how long this
waits."* The stall detector is the one thing it names that is **explicitly non-destructive** —
`broker.py:1641` says *"Visible, never destructive: nothing is cancelled on the strength of this"*,
and `capacity_wait_started_at`, which 0.6.111's own capacity branch writes, is read nowhere in the
tree.

The comment now says what is true: **nothing in the broker bounds this wait.** `tries` is restored on
every refusal so the `MAX_TRIES` ladder cannot end it, and cancellation is the operator's — the same
unbounded-queue semantics as the capacity wait directly above, and the same thing the owner asked
for (*"should the work get queued instead of failing outright?"*). It is a queue with no clock, not
a wait with a deadline, and the comment now says so instead of implying otherwise.

**Why this shipped at all:** 0.6.111 was audited independently, from the artefacts rather than from
its own summary. Both defects are in things that audit added or repaired, and both are the same
class the project keeps paying for — a guard that reads as stricter than it is, and a comment that
reads as a guarantee the code never made.

## 0.6.111 — 2026-09-30 · A Mac that is reserved on the far side is a wait, not a verdict

**Reported from the Gateway:** a batch submitted with `routing: "pool"` failed its *first* item,
terminally —

```json
{"item_index": 0, "code": "WORKER_TERMINAL_ERROR",
 "message": "HTTP 409: local is reserved for other work on this Mac"}
```

— while the rest of the pool sat idle. The owner's question was the right one: **"should the work
get queued instead of failing outright?"** Yes, and now it does.

**It was a classification error, not a scheduling error.** `_eligible_studios` already refuses to
hand an item to a machine in `busy_machines()` — that filter was never the problem. But
`busy_machines()` is **this Hub's own lease table only**, and a *peer's* reservation can never be in
it: the controller's row for a node uses `machine` = **node id** (`fleet_nodes.py:498`), the serving
node's own worker row uses `machine="local"` (`packaging/hub_worker.py:344`), and the peer publishes
**no busy fact** — `/api/hub/resources` carries host and per-studio up/down only. So the 409 at the
door is the **first** place the controller can learn a peer is reserved. Pre-filtering it is
impossible today without a new published field.

**The same condition was two different things.** Inside one Hub, a lease is a *skip*. Over the peer
surface it was a *terminal failure*. A transient lease was reported as a verdict — and with every
machine busy, every item in the batch died on its first 409.

**Two fixes, and the second is why the first was invisible.**

1. `_worker_http_error` read the code only from *inside* `detail` (the worker's shape:
   `{"detail": {"code": …}}`). The peer surface puts it *beside* it — `standalone._peer_refusal_response`
   writes `{"detail": "<reason>", "code": "MACHINE_BUSY"}` — so **every peer refusal arrived
   untyped** and landed as `WORKER_TERMINAL_ERROR` naming no cause at all. It now reads both shapes.
2. `MACHINE_BUSY` gets a **deferral** branch: `state="queued"`, `retry_at` +30 s, the machine avoided
   for 90 s so the pool offers the work elsewhere, and `tries -= 1`. **The refusal arrives before
   this item's worker saw the request**, so no attempt is spent and no duplicate is possible.

**The code is required, not just the 409.** A 409 is also how a peer names an operator decision no
other Mac can fix — `MODEL_PAUSED`, `MACHINE_DISABLED`, `STUDIO_DISABLED` — and those stay
**terminal**. Retrying those elsewhere cannot help, so they must not be laundered into a wait.

**The peer side is untouched.** `409 MACHINE_BUSY` is the pinned wire contract
(`tests/test_studio_proxy.py:366-403`); what changed is that this Hub now waits instead of dying.

**Also fixed, and it was going to get much more visible:** when every eligible machine is inside a
temporary avoid window, the dispatcher wrote *"Waiting for a Voice Studio where the selected shared
voice is synchronized"* — **for any modality**. `_shared_voice_allows_studio` returns `True` for every
non-voice modality, so for an image batch the avoid window is the *only* thing that can reach that
line. The note now distinguishes the two waits and names the machine when it is an avoid window.
Shipping the deferral without this would have made a wrong sentence the ordinary outcome of every
pool dispatch.

**Verification.** Gate: `unittest discover -s tests -p 'test_*.py'` → **1519 tests, OK (12 skipped),
606 s, exit 0**. The new cases in `tests/hub_regression/cases/test_pool_peer_machine_busy.py` are
**mutant-proved four ways**, each failing with a clean assertion rather than an error: reverting
`broker.py` to `HEAD` (2 failed — `error` vs `queued`, and `WORKER_TERMINAL_ERROR` vs
`MODEL_PAUSED`), disabling only the code-read (2 failed), removing only the deferral branch
(1 failed, reproducing the reported message verbatim), and restoring only the old voice-only note
(1 failed, reproducing the misleading sentence verbatim).

**A second red test was found and was *not* mine.** The gate's first run failed
`test_the_kit_ships_it`: it pinned the literal `chmod +x` in `prepare-kit.sh`, which was
deliberately changed to `chmod 755` in 0.6.110 (`+x` adds the execute bit to whatever mode the file
arrived with, so a source written 600 landed on a drive as 711 beside 755 neighbours). **The script
was right and the test was stale**, introduced by the previous release and left red. The assertion
now checks the property it was written to protect — that the mode grants its owner execute — and was
**proved to fail** when the mode is mutated to 644.

## 0.6.110 — 2026-09-29 · One command to update a Mac, and it skips the app copy when it can

**The owner, on updating a fleet:** *"what if the app is current and i'm only adding the model
weight, will we still copy the app and replace it? i mean it's a waste of time still. or that's
already properly guarded?"*

**It was not guarded at all.** `1 - Install the app.command` copied the whole bundle on **every**
run — quit the app, copy ~5 GB to a staging folder, delete the installed copy, move the new one in —
with nothing comparing the two builds first. On a fleet where most Macs are already current, that is
most of the work for none of the result, and it is the part that needs an administrator password.

**`Update this Mac.command`** runs the two steps in order:
1. Reads each bundle's own `release-manifest.json` — **version *and* `implementation_commit`** — and
   **skips the copy when they match**, saying so.
2. Otherwise installs the app, then copies the models. No `Press Return` between them.

**Version alone would not do.** Two builds can share `0.6.110` and differ, which is exactly what a
rebuild of the same release is; the commit is what makes the comparison mean "this build", and a
test stubs the commit read out to prove the guard fails without it.

**The identity is read with `plutil`, not Python** — deliberately, because the interpreter this
would otherwise borrow lives *inside the app being replaced*, which is the one thing that may not
be there while the comparison is being made. And an unreadable or missing manifest **installs**
rather than skips: a skip is only ever the result of both identities being present and equal.

**Why the two steps cannot run beside each other** — the other half of the question he asked. `1`
replaces `/Applications/Studio Hub.app`; `2` runs its helper with the Python from inside that same
app. Started together, `2` would either find no app and fall back to the copy on the drive — which
fails if the drive is unplugged — or keep running from files `1` has just deleted. So this runs them
in order and waits. **The waiting is the feature**, and a test asserts neither is started in the
background.

**Two launchers learned to be driven.** `install_app.command` stops twice for a person — which
Applications folder, and reading the result — and honours `STUDIOHUB_UNATTENDED=1` to skip both. The
model copy's *"press Return to start"* is answered by `STUDIOHUB_ASSUME_YES=1`, an explicit variable
rather than a piped newline, because a pipe would also answer any *future* prompt added there,
silently. Running either file on its own is unchanged and still asks.

Both numbered commands remain, unchanged in behaviour, for when a step needs looking at alone.
`12 tests` in `tests/test_model_copy_script.py`, one **proved to fail** with a version-only
comparison. **Gate: 1519 tests, OK (11 skipped).**

## 0.6.109 — 2026-09-29 · A controller that booted before its network was unreachable, and looked fine

**The owner, after a power cut:** *"the controller is headless. It was off then it turned it self
back on with the restart and the backend thing. But I tried to connect to it from the node. It
doesn't work I think the ip was blocked. I fiddled around for a bit then I just just stop server and
then start server again. Only then the node can be connect… I have to restart it every time there's
a new node. this is a terrible bug."*

**Nothing was blocked — there is no IP blocking anywhere in the Hub, no lockout and no rate limit,
and no per-IP state at all.** What happened is worse and simpler. `bindableListenHost()` reads this
Mac's addresses **once, at the instant the services start**, and treats a chosen address that is not
there as one this Mac *no longer has*, dropping it and binding whatever remains — **loopback, if
nothing else is left**. On a headless Mac after a power cut that instant is the worst one available:
the app is a login item, so it starts while Ethernet is still negotiating, DHCP is unanswered and
Tailscale has not come up.

The Hub then came up on `127.0.0.1`, **answered its own health check, wrote its ready file, and
reported "Local services ready"** — while being reachable by no node on the network at all. That is
the whole defect: a service that is healthy by every measure it publishes and unreachable by the only
measure that matters. His manual stop/start was not superstition, it was the same read taken a minute
later, after the network had arrived.

**The second symptom is the same cause.** A controller bound to loopback cannot probe its nodes, so
they showed offline until he restarted it — *"after I've connected the machines, it's still shown
offline, until I click restart or sth."* One cause, both symptoms.

Two changes:

- **A missing address is remembered, not accepted.** `addressesMissingAtStart` keeps the set, and the
  three-second supervisor loop restarts the services the moment one of them appears. The spawn clears
  the set, so a Hub the operator has *deliberately* narrowed is restarted at most once, never in a
  loop. Nothing narrows permanently and he does not have to know any of this.
- **The note stops saying something untrue.** It read *"This Mac no longer has 192.168.1.21"* — a
  statement of fact from a single reading, and the wrong fact. It now says the Mac does not have the
  address **yet**, that this is usual for a minute after a restart, and that the services will restart
  by themselves when it appears.

`ShellConfiguration.arrivedAddresses` is the decision, extracted pure with injectable addresses so the
case that matters — a headless boot where the address turns up a minute later — is tested without
waiting for a network. Five assertions in `tests/native/main.swift`, and one of them was **proved to
fail** with the comparison stubbed out.

`native/build.sh` green. **Gate: 1512 tests, OK (11 skipped).**

## 0.6.108 — 2026-09-29 · A node that had joined came up saying it had not

**The owner, on a second Mac in the group:** *"i was just connecting and adding nodes but it doesn't
show properly. like i already connected but when i go to check with a fresh, it still doesn't show.
and also sometime it shows not joined a controller. but at the bottom it shows i already joined as a
node."*

**Two lines on one screen, contradicting each other, and both were reading a different property for
the same fact.** The standing line reads the *credential* — `fleetCredentialOrigin == "adopted"` —
and correctly said "Joined to a controller". The health note reads the *address*, found it empty, and
said "Not joined to a controller." The dot went grey, and the Connections page had no address to
check, which is why a fresh look showed nothing about the controller at all.

**The omission is one line, on the startup restore path.** `adoptStartupMembership` copied the saved
address into `storedMembership` — which is what the re-join loop reads, and the re-join was working
perfectly the whole time — but never published it to `controllerAddress`, the property the screen
reads. The app saves the address on join (`onControllerJoined` → `configuration.controllerAddress`,
persisted in `ShellCore`), loads it at launch, and passes it in; only the last step was missing.

**This is the second time this same function has hidden a state the UI needed.** Its own comment
already records the first: the launch called `rejoinControllerIfNeeded()` before anything had fetched
the identity, so the guard saw nil and every Mac returned silently. Same function, same shape — a
restore that moved state into the machinery and forgot the screen — which is why the fix publishes
the address and the test asserts the two agree.

**The test that proves it had to avoid the obvious trap.** The first version asserted the published
address after a restore that *also* joined successfully, and a successful join publishes the address
from the join target — the same string — so the defect was invisible behind it. It passed with the
fix removed. The assertion now runs on a restore that produces **no join** (the identity read fails
first, which is the realistic case on a node that boots before its controller), and uses a
**different** address from the previous block, so a merely stale value fails too. With the fix
reverted it fails with `got: 192.168.1.21:8080` — the previous address, which is precisely what a
node was showing: a membership that was fine, behind a screen that still said otherwise.

`native/build.sh` green. **Gate: 1512 tests, OK (11 skipped).**

## 0.6.107 — 2026-09-29 · The long-form joiner levels, and Breeze TTS 2 4-bit ships

Two things the owner asked for in one release, because Breeze is the reason the first one finally
got measured.

### The joiner now matches section loudness — and the obvious fix was the wrong one

**The owner asked directly:** *"did you fix the loudness and the make the joiner work?"* He was right
that it did not. `_join_long_form_wavs` had **no loudness matching for any family**, so a long script
came out at whatever level each independently-rendered section happened to land on. Measured on
Breeze TTS 2 4-bit: the 14 sections of one 1,227-word script span **4.9 LU** (−30.0 to −25.0 LUFS),
which the ear hears as the narrator stepping back from the microphone at every paragraph.

**The first implementation was one linear gain per section, and it was measured to fail.** The
sections span 4.9 LU of loudness *and* **18.4 to 22.7 dB of crest factor**, so what actually decides
each section's final level is how much gain it can take before its peak reaches the ceiling.
`min(target − I, ceiling − TP)` narrowed the span only **4.9 LU → 4.3 LU** — every quiet section was
capped, and the step survived the fix. FFmpeg's EBU R128 `loudnorm` per section brought the same 14
sections to **1.5 LU**, and the joined file measured **−17.50 LUFS / −1.42 dBTP — identical to the
sample the owner listened to and approved.** That is what ships, and the rejected measurement is
recorded beside the constants so the next person does not re-derive it.

Each family declares `section_loudness_target_lufs` on its `catalog.FAMILIES` entry (`None` opts
out), with the true-peak ceiling and loudness range beside it. The pass is length-preserving —
measured ratio **1.000** at 50 ms, 200 ms and 1 s — so it changes level and nothing else; where
FFmpeg is missing or a section cannot be levelled, the join proceeds unlevelled and **says so**,
because a job that has already spent twenty minutes generating must never be lost to a meter.

### Breeze TTS 2 4-bit (MLX) is a family, a model, and a manifest

`mlx-community/Breeze-TTS-2-mlx-4bit` at revision `3a06d26b` — 12 files / 3,042,440,328 bytes — with
a `design_clone_or_direct` resolver, an entry in `MLX_AUDIO_FAMILIES`, and the **0.60 s join pause
the owner chose by ear** from three cuts of the same Aiden render. It designs a voice from a written
description, clones one from a reference clip plus its transcript, takes a direction for how a line
is read, and performs inline vocal events such as `(sigh)` rather than reading them aloud. 24 kHz
mono, English and Chinese, **16 GB floor** — 16 rather than the 8 GB the resident size would allow,
because RSS held flat at ~3.3 GB while the kernel's peak-footprint reading ranged 5.41–23.93 GB and
was never settled. 16 GB is also where 50 of the owner's ~53 machines sit.

**Breeze refuses a voice with no transcript rather than faking one.** It builds its clone prompt from
`f"{speaker}{ref_text}"`, so the placeholder Pocket TTS's resolver substitutes would not degrade the
clone, it would condition it on the wrong words. The refusal names the Voices page.

### The runtime moved 0.4.7 → 0.5.7, and it was measured first

`breeze_tts` exists only in mlx-audio 0.5.7, so the shipping voice runtime had to move. A runtime
swap is exactly the shape that shipped an unnoticed mflux change for five releases, so it was
audited and then **rendered**: every entry point the worker calls is byte-identical
(`generate_audio`, `load_model`, `GenerationResult`, all seven model `generate()` signatures), no
dependency floor rises, **769 voice tests pass identically**, and a 16-render A/B on the one family
with a real behavioural change found **16/16 terminating by EOS and 16/16 accepted** by the project's
own Whisper quality gate.

The change is real and stays recorded: Qwen3-TTS's clone sampler now applies its forced 1.5
repetition penalty to the **last 64 tokens** instead of the whole history, and no longer restores the
EOS logit after top-k/top-p filtering — confirmed live at runtime, and the audio is genuinely
different (same-seed cross-version waveform correlation ≈ 0). What is the same is everything the
project measures, and the **shipped sectioned path is where the delta is smallest** (same-seed totals
within 1–3 tokens). **Two residual risks, stated rather than buried:** the **1.7B Base was not
measured** — no real copy exists on this disk, only dangling symlinks onto an unmounted drive — and
the rare EOS-failure tail did not reproduce in either version, so it is bounded (≤1 in 5 single-shot,
≤1 in 3 sectioned per arm at 95%) without being resolved. The pin is one line either way:
`2c9461f5…` **is** v0.4.7, `94c77162…` is v0.5.7, and the regenerated lock changed **nothing else**.

`tests/test_breeze_family.py` (11 tests) pins the three forms, the transcript refusal, and the
registry wiring. `tests/test_long_form_loudness.py` (12 tests) drives the real joiner over real WAV
files and measures the result — the level test was **proved to fail** with levelling disabled, and
the empty-file test reproduces the `30 != 10` failure that caught a section being silently emptied.
`docs/model-benchmarks.md`, the manifest inventory and five package counts were regenerated for the
31st package. **Gate: 1512 tests, OK (11 skipped).** `native/build.sh` green.

## 0.6.106 — 2026-09-29 · A clip that will not play now says why, and names the Mac that has it

**The owner, asked whether the Hub should keep its own copy of every finished artifact so a result
stays readable when its worker goes offline:**

> *"No not at all. no need to copy. just give a proper clear reason why it can't be played. 502 is no
> proper reason. it must be understandable from a glance. so i know why it didn't play."*

So the behaviour is unchanged and the **explanation** is the fix. That is the right split: copying
every output to the controller would put a second full copy of every render on every machine in the
fleet, against a written decision (`backend/job_storage.py`: *"Image and voice generation artifacts
live on the worker Macs"*), to avoid a sentence.

**What he was reading was `Local engine HTTP 502: worker artifact could not be read`.** Two things
were wrong with it and the status code was the smaller one. "502" names the layer that failed rather
than the cause; and "worker artifact" names a component he has never seen — there is no worker
artifact anywhere in what he uses, there is a clip and there is the Mac that made it. Only the Hub
knows which Mac holds a result, so the Hub's own `detail` is now the message and it says the three
things a person needs: **what** happened, **which Mac**, and **what fixes it**.

`standalone.artifact_unavailable_reason` tells the two causes apart, because their fixes differ:

- **A worker missing from the roster** is a Mac that is switched off or disconnected —
  *"This result was made on studio-2 and is stored on that Mac's disk rather than in the Hub, so it
  can only be played while studio-2 is running and connected — and it is not running or not
  connected to this Hub right now. Start that Mac, or wake it, and play this again."*
- **A worker still registered that does not answer** is one that is running and unhealthy, and the
  same sentence says "not answering this Hub" instead.

A single-Mac install has no machine name and gets the local form, pointing at this app's own
services. The third refusal on that route — a stored reference that does not match the worker's
origin — is deliberately **left as an internal error**: it is a Hub-side integrity failure the
operator neither caused nor can act on, and dressing it in "start that Mac" would send them to
restart a machine that is running perfectly well.

**The app half.** `validateResponse` used to prefix every refusal with `Local engine HTTP N:`. It now
shows the Hub's sentence first and appends the code — `"…play this again. (HTTP 503)"` — because the
code is what makes a report diagnosable and a sentence the operator reads first costs nothing.
Removing it entirely broke `a real failure must carry the engine's own answer`, which was right to
complain: the fix was the order, not the absence.

`tests/test_artifact_failure_reason.py` (11 tests) pins both halves: the sentence names the Mac, the
machine falls back to the `role@machine` studio id when the item does not state one, the reason
carries no status code and is a sentence, all three refusal points on the route are as described,
and the app shows the Hub's `detail` with the code after it. `native/build.sh` green.

## 0.6.105 — 2026-09-29 · The reveal button refused without saying why, and macOS never showed the tooltip

**0.6.104 made Show in Folder work. This makes it explain itself.** Both halves were the same report
— *"even on this machine, i can't click show in finder too"* — and only the first was a bug in the
data.

`revealButton` was `.disabled(!job.canRevealArtifact)` with its explanation in a `.help` tooltip:
*"This result is not a file on this Mac, so there is no folder to show."* **macOS does not show a
tooltip for a disabled control.** So the button declined in exactly the one situation where it had
something to say, and said it nowhere — a grey control with no explanation on screen, which is the
state this project keeps having to fix.

The button is always clickable now. Where the file is here, Finder selects it. Where it is not, the
row reports why through the workspace banner, which is where every other file action in the app
reports — and it says which of two different things went wrong:

- **No path published** — the Hub only records a path for a job this Mac produced, so this means the
  job ran on another Mac. The message names that and points at the Mac that has the file.
- **A path with no file behind it** — the file has been moved, renamed or cleaned up since. This is
  a different fact, and the old sentence got it wrong: it called a local file that had been deleted
  "not a file on this Mac".

Also new `NativeJob.revealUnavailableReason`, and four tests in `tests/test_local_artifact_reveal.py`
(17 in the file now) pinning that the button carries no `.disabled`, that a refused reveal reaches
`fileActionError` rather than a tooltip, and that the two causes stay distinguishable. The two
behavioural ones were proved to fail with the old disabled button restored.

`native/build.sh` green.

## 0.6.104 — 2026-09-29 · The four things the job rows were missing

**The owner, from screenshots of his own Audio and Jobs pages:** *"the audio scrubber is appearing and
disappearing … it shouldn't move the list"*, *"i think we should have a divider or a line between
them — they all in the same black background"*, *"even on this machine, i can't click show in finder
too"*, and *"there's is no generation date so i don't know when each of them was generated on."*

Four reports about one screen. Each was a thing written **once per panel or once per row builder**,
and therefore missing or wrong in five or six places at the same time.

**1. Show in Folder was disabled on every row of every machine.** `broker.public_item` — the function
every job payload is built by — dropped `artifact_path` from every item it published. The app's
`canRevealArtifact` reads `item.artifactPath` and checks the file is there, so the field arrived nil,
the check was never reached, and the button was disabled everywhere, **including the Mac that wrote
the file**. The app's own docstring already described the intended behaviour — *"The Hub records the
worker's `output_path` on the item and this reveals exactly that"* — so it was the publication, not
the button, that was wrong.

The rule the strip came from is real and is kept: a path recorded by a worker on **another Mac** names
a file on that Mac's disk, and offering a reveal Finder cannot perform is worse than a disabled
button. `local_artifact_path` publishes the path only when `peers.is_local_studio` says the producing
worker is this Mac — the same test the broker uses to decide whose memory a worker's is. The app's
`FileManager.fileExists` check is the second gate and still covers a remote job's path.
`worker_artifact_url` and `worker_accepted_params` are still withheld from every item.

**2. The rows had no boundary.** Each panel drew `ScrollView { LazyVStack(spacing: 12) { ForEach } }`
straight onto the workspace background, so there was nothing between one row and the next. The Jobs
page had drawn a `Divider()` all along. All five panels now render through one `RecentRows`, which
draws the row and then the divider, so no panel can be the one without it.

**3. The transport appeared and disappeared.** It was drawn only while a clip was loaded, so pressing
Play pushed the whole list down and Stop pulled it back — the row being read moved under the pointer.
It is now always on screen, takes an optional job, and is inert with a one-line reason until
something is loaded.

**4. No row carried a generation date.** Six row builders each hand-wrote the same "status · model"
`HStack`, and the date was written into none of them. That line is now `jobMetaLine`, in one place, and
it renders `NativeJob.generatedAtLabel` — the Hub's own `batch.created_at` through the same
`moment(_:)` formatter the fleet pages use. A job with no stamp shows no date rather than 1970.

`native/build.sh` green (three suites). `tests/test_recent_job_lists.py` grew from 3 tests to 14 and
now pins the divider, the transport and the date **by shape** — one `RecentRows`, one `stateBadge`
call site, one meta line — so the sixth panel and the seventh row builder are caught. New
`tests/test_local_artifact_reveal.py` (14 tests) pins both halves of the path contract: a local job
publishes its path, a remote job does not, and the app still refuses to reveal a file that is not
here. `tests/native/api_main.swift` adds four checks for the date and the reveal gate. Each fix was
proved to fail with the defect restored.

## 0.6.103 — 2026-09-29 · Every studio's "Recent" list was capped at five, so it could not scroll

**The owner, with a screenshot:** *"audio generation tab only shows a few and i can't scroll it about.
like there's more stuff but it's stuck there. and i think the same goes to the other generators as
well."*

Both halves were right. Each studio page wrapped its list in a `ScrollView` and then handed that
scroll view `.prefix(5)` — so five rows were all it ever had. **A list capped at five cannot be
scrolled by definition**, and the panel read as *refusing* to move rather than as having nothing more
to show. The same five-line mistake was in **Image, Music, Video, Audio and Subtitle**; the cap was
copied along with the layout, which is why he saw it on more than one page.

Nothing explained the 5. It reads as "recent", but a recent list is one the reader can scroll past —
this was a ceiling, and it hid his own history behind a scroll view that had nothing to scroll.

Five caps removed. `tests/test_recent_job_lists.py` pins it by shape rather than by count: no studio
list may pass a fixed slice of its jobs, each must still render its list (the other way to pass is to
stop showing it), and there must be at least as many scroll views as lists. Proved to fail with the
cap restored.

## 0.6.102 — 2026-09-28 · One token to copy, and the screen says which

**The owner asked to merge the two tokens into one: *"can we just make it use only 1 token? do we really need both?"* — then chose the version that keeps the safety property: *"one token to copy — merge the story, keep the secrets."***

**Why the values stay separate, in the code's own words.** `peers.fleet_token` already said it:

> *"A packaged Hub owns a fleet credential of its own, **distinct from its Hub token. The separation is the point** … it is also the whole reason a node's owner cannot administer the controller that owns them."*

And the Connections page already told the operator: *"A Mac that holds this token still cannot administer this Mac."* Merging the **secrets** would end that — the Gateway and every one of 50 nodes would hold a credential that can change any Mac's settings, install or remove its models, delete its output and evict machines. The sharper cost is operational: revoking one sold Mac is one click today, because its own credential dies with its row; with a shared token it becomes a fleet-wide rotation that needs every Mac reachable.

**So the *story* is merged instead, which is what actually cost him time.** The screen drew the two as peers — two rows, two copy buttons, no statement of which to use — and the join form's only instruction named the thing **not** to use (*"not its Hub token"*). He read a token off the joining Mac, pasted it, and the controller refused it with a message that made his token look wrong.

- **The token to hand out is first, and it says so.** The Connections page leads with **“Give this out”** and the Fleet token, then the Hub token below a rule under **“Keep this one.”** A single sentence carries the instruction: *"This is the only token you hand out."*
- **The join form names what to use, not what to avoid.** *"…its Fleet token — the one that Mac marks “Give this out”. On the controller, open Connections and copy it from there."* The `— not its Hub token` clause is gone.
- The old closing line (*"the Hub token is this Mac's key, the Fleet token is the work badge for the group"*) is removed rather than reworded: it existed to explain a choice the operator should never have been asked to make.

**No value changed, and the Gateway needs nothing.** This is the two tokens explained properly, not merged — so `gateway-kh` keeps using the Fleet token exactly as it does, and no Mac has to be re-enrolled for it. The security property in `tests/test_peer_path_contract.py::FleetCredentialWiringTests` is untouched and still asserted.

Three tests pin the framing so it cannot drift back: the token to hand out comes **first**, the screen says **“Give this out”**, and the join form no longer contains `not its Hub token`. Proved to fail on the old wording.

## 0.6.101 — 2026-09-28 · The Join button never sent the token you typed

**Found in the field, on the owner's first real node.** A healthy node (0.6.100, six workers up) joining a healthy controller, with that controller's own Fleet token pasted into the box, refused every single time:

> *Join the controller failed. … a credential that is neither this Mac's Hub token nor the Fleet token this Mac holds. … If the caller presented a Fleet token, the two Macs hold different Fleet tokens.*

Both tokens were correct, and both worked when used directly against the controller — a real join with each returned **201**. **The app was not sending the one he typed.**

`joinController` chose its first credential with

```swift
let own = nodeOriginCredential ?? secret
```

and `nodeOriginCredential` returns what this Mac **already holds** — its own node credential first, else the group token it stored — falling back to what the operator typed only if it holds nothing at all. That order is right for the **startup re-join loop**, where the Mac is re-announcing itself and nobody typed anything. It is wrong for the **Join button**, whose own screen says *"Enter the controller's address and its Fleet token"*.

And the retry that should have rescued it could not: it was gated on `heldNodeCredential`, which a Mac holding a stored **group** token and **no** per-node credential does not have — the state of every row written before per-node credentials existed, which is every Mac the owner is re-joining. So the one control an operator has for correcting a membership did nothing, and the page blamed his token for a request that never carried it.

- **A Mac's own node credential still goes first, always.** It names this Mac alone, so it is the value that survives a group-wide Fleet-token rotation, and a re-join presenting it is correct whether a person pressed Join or the loop is running. An existing assertion pins that — and it caught the first version of this fix, which had the operator's token outranking it.
- **A stored group token has no such privilege.** It is only the last group credential this Mac was given, and it goes stale the moment it joins a different controller. So when the operator is the one asking (`startupAttemptID == nil`; the loop always passes one), what he typed goes first.
- **The retry presents the *other* credential, once.** The first version computed it as `heldNodeCredential ?? secret`, which offers the same value a second time when the held one is what failed — the re-admitted-Mac case, where the group token is the one that gets it back in. A second pre-existing assertion caught that.

**Proved by putting the old line back**: the native suite fails with the wire carrying `token-from-an-earlier-group` — the stale value — where the typed token should be, and passes with the fix. Both halves are pinned in `tests/native/api_main.swift`, alongside the assertions that predate them.

**What this means for the rollout:** this is the control every node uses to join, and it was unusable for any Mac that had ever been in a group. It has to reach the drives before the fleet can be enrolled.

## 0.6.100 — 2026-09-28 · The recorded-open list had three items on it that were already done, and the video harness would have refused on the Mac it exists for

Finishing what rounds 18–20 recorded. Three things, and the first is the one that would have cost
the owner a trip.

**1. `RUN-16GB-LTX-VIDEO.command` would have refused on a 16 GB node — the machine it exists for.**

Its home-folder candidate list named **only the legacy spelling**: `~/StudioHubModels/ltx-2.3-22b-ram-12gb-mlx`
(upstream's dots, flat, no revision folder). The app, the kit's copy step and the worker all use the
**package id** instead — `ltx-2-3-22b-ram-12gb-mlx`, installed as `<library>/<package_id>/<revision>/model`.
So on a node where step 2 had just installed the model *correctly*, the script found nothing and
would have stopped with *"the model is not on this Mac"*.

Measured on this Mac, both spellings present: the package-id copy is **18,674,268 KiB and complete**,
the legacy dot-form is **10,760,852 KiB and partial**. Both are listed now, package-id first, in both
the revision-folder and flat shapes. **Proved with the drive removed from the candidate list** — the
exact node case: the old list answers *"Every copy of the model on this Mac is unfinished"* while the
new one resolves `…/ltx-2-3-22b-ram-12gb-mlx/d8a9d2f…/model (18G)` and runs.

**2. `install_local.sh` stopped Studio Hub and never started it again.**

Measured after the 0.6.97 install: the Hub was down and nothing answered on 47873 until it was
opened by hand. A release that ends there looks finished and is not — the Gateway reports every
Studio Hub location unreachable, every model picker on the fleet empties, and each drive's installer
talks to a machine that is not there. It now relaunches and **waits for `hub-ready.json`**, which the
Hub service writes once it is actually serving, so it cannot report success for a process that is
merely running. Guarded on the real install target like the stop half, so a temp-path install still
starts nothing; a Hub that will not come up says so rather than exiting 0 in silence.

**3. The install guards were collected by nothing.** `tests/test_install_local.py` was written
pytest-style — the only such file in `tests/` — while the release gate is
`python3 -m unittest discover`. Measured: **the gate collected 0 tests from it**, and its two tests
(which exist because an install once left `/Applications/Studio Hub.app` with no `Info.plist`) passed
only when pytest was run by hand. Ported to `unittest`: **3 tests now, all collected**, including a
new one asserting the relaunch sits inside the real-target guard — proved to fail when it does not.

**And the recorded-open list itself was three-quarters stale.** The appendix below the numbered list
said voice publishes no below-tier warning ("two missing links, not one"), that FLUX.2 Klein 4B MLX
4-bit's `79.34 s` traced to nothing, and that chat's memory tier is unmeasured. All three were
already done: voice's three links exist (`catalog.py:1582`, `StudioAPI.swift:7576`,
`StudioViews.swift:2297`), FLUX was closed on 2026-09-26 as **105.943 s** with a guard, and chat's
tier was closed at 0.6.69 — **which the same file says, forty lines above.** Two lists of the same
thing in one file, disagreeing, and nothing compared them.

**Chat's streaming half is now measured** rather than assumed: `POST /api/hub/chat/completions` with
`stream: true` returns real SSE deltas from `mlx-community/Qwen3.5-9B-4bit`, a terminal chunk with
`finish_reason: "stop"` and usage, and `[DONE]` — 0.514 s of generation. **The acceptance half is
still the owner's** — nobody has typed into the packaged page — and the handoff now says plainly not
to record the lane as accepted on the strength of the HTTP result.

**Data repaired, nothing deleted.** ATLAS's and ORICO's model-archive copy of LTX-2.3 22B had the
same truncated `transformer-distilled.safetensors` (1.86 GB against 9.97 GB); both now carry the
complete file copied from the verified kit library **on the same drive**. And this Mac's legacy
dot-form partial went to `~/StudioHubModels/_trash/` — a 10.26 GiB hand-download the harness and the
worker both had reason to pick up and neither should.

## 0.6.99 — 2026-09-28 · The video harness was on every drive, and our own cleanup took it off

**Two defects, found by doing the thing the handoff said had never been done: running
`RUN-16GB-LTX-VIDEO.command` end to end.** It failed, 24 seconds in, with

```
[load_safetensors] Tensor 'transformer.adaln_single.emb.timestep_embedder.linear1.bias'
invalid data offsets (8706572288, 8706580480) exceeding the size of the file.
Perhaps an incomplete download or corrupt file?
```

which reads as **a corrupt model**. The truth was a truncated copy on a drive.

**The script looked in the wrong place, and its own comment says why that is expensive.** It resolved
the model from `/Volumes/KH-STUDIO-*/model-library/video-models/…` — the **drive-root** library, a
stale duplicate that **nothing verifies**. Measured across the four drives, its package count is
**43/35/35/34** while the kit's own verified library is **31 every time**; on ATLAS and ORICO
`transformer-distilled.safetensors` is **1.86 GB where the pack ships 9.97 GB**, and UGREEN has no
copy there at all. The glob expands **alphabetically**, so the script chose **ATLAS** — the truncated
one — and the partial-copy check it does have fired as a *warning after selection* rather than as a
reason not to select it. **The complete copy is on all four drives**, inside the library
`verify-kit.sh` actually checks.

- The kit's own library is now tried **first**, by revision path.
- **An incomplete copy is ineligible, not warned about.** A truncated pack cannot load, so running
  it produces a misleading error and wastes the trip — the script now refuses (exit 3) and names the
  first incomplete copy it rejected. An explicit `STUDIOHUB_LTX_MODEL` is still honoured, loudly,
  because that is an operator naming a copy on purpose.
- **And the harness is on the drives at all now — it had been, and our own cleanup took it off.**
  The six files are dated **Sep 27 01:22** in every drive's `_trash/Technical-retired/`, moved there
  on **Sep 28 03:34–03:53** by the managed-set prune that `prepare-kit.sh` runs on every
  preparation, because they were never added to `MANAGED`. So the drives carried the video test for a
  day, then every `Technical/` folder held the 8 GB Z-Image and Chat-tier commands and nothing for
  video — while the LTX pack sat complete on all four.
  **The mechanism is the finding, not the file.** The prune is right to exist — a folder that only
  grows is a folder nobody can read, and one stale copy of a script is how an operator runs the wrong
  one — but **a file placed on a drive by hand is silently retired by the next preparation**, and
  nothing requires whatever put it there to register it. `RUN-16GB-LTX-VIDEO.command`, its runner,
  `ltx-video-measure.py`, `RUN-LTX-VIDEO-WORKER.command`, `ltx-video-worker-run.py` and the README
  are now in `MANAGED` beside the other harnesses, so they are refreshed rather than pruned.

**What the run proved, on this 32 GB Mac.** With the complete copy selected, the harness goes all the
way through: it resolves the app's own bundled Python 3.11 video runtime with no setup, loads the
transformer's **1,415 mixed-precision layers** (`{2: 29, 3: 225, 4: 348, 5: 631, 6: 125, 8: 57}`),
and generates. The one warning it prints — *"no transformer config … using hardcoded defaults"* — is
present in **every** prior run including both successes, so it is a property of this pack and not a
finding.

**The 16 GB tier is still unmeasured, and this release does not change that.** Every LTX number
remains `measured_on: 32 GiB M2 Max`, and the video lane has **no per-tier memory limit to imitate
one with** (`memory_policy.FIXED = True`; the pipeline is built and dropped per job). A capped run on
a 32 GB Mac would measure the cap, not the machine. **The test needs a 16 GB Mac, and now a drive
carries everything it needs to run there.**

## 0.6.98 — 2026-09-28 · The video lane was written down as unbuilt while it was live, offered and already making files

**Asked what was wrong with the Hub's video lane, the honest answer was "nothing, and this file
said otherwise."** The handoff's open-items list was headed *"as of 0.6.71"* — **twenty-six releases
stale** — and its video entry read:

> *Nothing about it is shipped: no worker, no manifest, no `lane_contract`. … a 7,200 s deadline …
> correct, and unbacked until the worker exists.*

**Every clause was false, and three of them had been false for a while.** Checked against the
running system rather than against another document:

| The claim | What the live Hub answers |
|---|---|
| no worker | `studio_type: video`, `studio_version: 0.6.97`, `online: true`, `ready: true`, serving `video.generation`. `COMPONENT_WORKERS['video'] = _video_spec` spawns it from the release manifest. |
| no manifest | four ship, all published as `modality: video` |
| no `lane_contract` | LTX publishes operation `video.generation`, **eight** parameters and **two measured runtimes**; Lance publishes seven |
| the deadline is unbacked | 298.5 s (97 frames, 704×448) and 351.628 s (49 frames, 1024×576), each with its receipt, `measured_on: 32 GiB M2 Max` |
| it will not appear as an offer | the Gateway publishes `studio-hub::baa-ai/LTX-2.3-22B-RAM-12GB-MLX`, `available: true`, no blockers, modes `image-to-video` + `text-to-video` |
| never run end to end | 13 jobs in the worker's own store. The 11 failures are **one 35-minute debugging window** on 09-26 22:33–23:08; the two runs after it **both succeeded** (1,639,307 B in 378.0 s, 381,087 B in 110.3 s) and both `.mp4` files are on disk. |

**This is `AGENTS.md`'s own rule, inverted, and the inversion is the lesson.** The rule warns that
*"implemented"* is not *"in the build you are running"*. This was the reverse — **a shipped, live,
offered feature written down as unbuilt** — and it is just as expensive: it sends the next session to
build what already exists, and it told the owner his video lane was missing when it was not.

The list now carries the measurements and the **real** remaining gaps, which are narrower:

- **The 16 GB tier is unmeasured, and it is the tier that matters.** Every LTX number is
  `measured_on: 32 GiB M2 Max`, while ~50 of the owner's ~53 Macs are 16 GB or below.
- **The harness that would settle it is on no drive.** `qualification/RUN-16GB-LTX-VIDEO.command`
  exists here; the kits' `Technical/` folders carry `RUN-8GB-Z-IMAGE.command` and
  `RUN-CHAT-TIER.command` and nothing for video — so the owner cannot run the test from a drive even
  though the LTX pack is on every one of them.
- **Lance is the genuinely unbuilt engine**, and the code says so in its own comment.
- **Two successful runs is thin evidence**, not a reliability claim.

The heading carries the version it was written against, and now says when it was last re-read. **A
status list nobody re-reads is a status list that lies**, and the version in its own heading is the
cheapest way to notice.

## 0.6.97 — 2026-09-28 · The check that keeps the drive's index honest was refusing to run, and saying so in a way that read like success

**Every drive's index page has been stamped by hand-copied numbers for as long as the stamper has
existed.** `packaging/kit_stamp_index.py` was written to end a bug recorded three times — a drive
index naming `0.6.63` beside a `0.6.70` bundle, `0.6.70` beside `0.6.89`, and Story `1.4.219` beside
a `1.4.234` installer. It derives each row from the installer actually sitting on the drive.

It was never run successfully. `prepare-kit.sh` passes the bare volume name it loops over, and the
script did `Path(sys.argv[1])` — so `KH-STUDIO-NOVA` was a **relative** path, the kit index was looked
for under the current directory, and the write refused. On every drive, every time.

**What made it survive is the part worth recording.** The refusal was not hidden: it printed
`no kit index at KH-STUDIO-NOVA/Video Generation Kit/Studio Hub KH/README.md`, four lines into each
drive's 30-line preparation log. That message is a real path missing its `/Volumes`, and it reads
like a path. An operator — or a summary that counts `RESULT: PASS` at the end of the same log — has
nothing to catch. So the rows stayed right for four releases **by luck**: the repository template the
drives were copied from happened to name the current versions, while the mechanism that derives them
from the drive was dead. Only the next release would have shown it, and it would have shown it as the
same stale row this script exists to prevent.

- A name with no path separator is now read as a volume: `KH-STUDIO-NOVA` → `/Volumes/KH-STUDIO-NOVA`.
  An explicit path is left exactly as given.
- **A refusal names an absolute path** and asks whether that was the drive, so the next one of these
  is visible in the log rather than merely plausible in it.
- `prepare-kit.sh` passes `/Volumes/$VOLUME` as well. The script is defensible on its own and the
  caller is still corrected, because the caller is the thing that was wrong.
- Two stranded `if __name__ == "__main__":` guards found and moved while fixing this — one inside
  `stamp()` in the stamper (unreachable after its `return`, so it had never run and could not) and
  one **mid-file** in `tests/test_document_inventory.py`, which meant running that file directly
  silently skipped the entire kit-index suite. The second is the same accident in the same shape as
  the first, and it is how a suite of tests stops being run without anything going red.
- Three tests added, asserting the rule rather than the membership: a bare volume name resolves to a
  volume, an explicit path is untouched, and a refusal prints where it actually looked.

Verified on the drives: **ORICO and UGREEN stamped on this pass** (the fix landed between volumes),
**NOVA and ATLAS re-stamped afterwards**, all four now carrying `Story Studio 1.4.235` and
`GatewayKH 0.2.67` derived from the installers beside them rather than from a template.

## 0.6.96 — 2026-09-28 · A drive that looked deliberately lighter was carrying a file that could not verify

**The owner asked why NOVA had 30 packages and the other three had 28, and whether they could be made
the same. The recorded answer was wrong.** The handoff said *"Lance is on NOVA only, deliberately …
those manifests say 'not shipped', which is true"*. Both Lance packages were in fact on **all four**
drives — and on ATLAS, ORICO and UGREEN the processor's `model/config.json` was **1301 bytes where
the manifest records 1373**, so `verify_installed` refused it every time, and refused the video pack
with it because that pack names the processor as a companion. NOVA carried the corrected file, dated
2026-09-27; the other three still had the 6 August copy.

**So `28/30` was not a decision. It was a stale file failing verification, written up as a
trade-off** — which is worse than either, because it reads as intentional and stops anyone looking.
The three drives' own indexes simply omitted the package, and the omission was explained rather than
investigated.

All four drives now carry the corrected bytes and the same set: **30 of 30 packages,
`models_not_shipped: []`, `problems: []`, 115.3 GiB, 295 model files each**, each drive's verifier
PASS, and the kit manifest regenerated from each drive. The only remaining difference between the
four is `.DS_Store` (4/10/4/4), which the app ignores.

**Two habits this re-learns.** *Present is not correct*: the first version of the copy script
skipped any folder that already existed, which is precisely how the stale file survived three
preparations — the script now replaces and says so. And *a claim about what a drive carries has to be
read off the drive*: "not shipped" was a note about intent, and it was published as a fact about
bytes.

## 0.6.95 — 2026-09-28 · The drive's instructions now describe the drive

**0.6.94 stamped the index and stopped there, and the same defect was waiting four lines below it.**
On the three drives prepared today the version *line* said Story Studio 1.4.235 — and the install
step three lines down still said *"Open `Story Studio KH 1.4.219-arm64.dmg`"*, a file that had just
been moved to `_trash`. The Gateway kit was worse: its `CHECKLIST.md`, `01-gateway-host.md` and
`README.md` all named `gateway-kh-0.2.44.dmg`, and its tracked sources were older still — Story
**1.4.219**, GatewayKH **0.2.2**. Anyone following those steps fails at step one.

`packaging/kit_stamp_index.py` now rewrites **every backticked `*.dmg`** in an app's documents from
the installer that is actually in its folder, plus the document's own `**Version x.y.z.**` sentence.
The rule that identifies "this app's installer" cuts a filename **at its version** rather than at its
first hyphen — the Gateway's is `gateway-kh-0.2.67.dmg` (hyphen inside the name) and Story's is
`Story Studio KH 1.4.235-arm64.dmg` (version mid-name), and only cutting at the version handles both
without knowing which app it is looking at. A document may still mention another app's DMG, or a
checksum file, without being touched.

**And the copy step now runs the app you installed.** Both the launcher and the helper searched for
`Studio Hub.app` **beside themselves first**, so a Mac that had just run step 1 still executed the
interpreter from the kit's bundle on the removable drive and read *its* manifests. That is slower,
breaks if the drive is unplugged mid-copy, and leaves two different answers to "which app is this?"
— the launcher's and the helper's. Both now prefer `/Applications`, then `~/Applications`, then the
kit's copy as the fallback for a Mac with nothing installed; the launcher passes the bundle it chose
to the helper as `STUDIOHUB_APP_ROOT`, so the interpreter and the manifests come from one bundle or
the change fails loudly.

**That last fix exposed a hole in the tests, which is worth recording.** Six of them went red the
moment the order changed, because they built a fixture bundle and the helper had been finding the
owner's **real** `/Applications/Studio Hub.app` all along — reading thirty manifests from this Mac
against a fixture's library. A test coupled to a live install is not a test; they set
`STUDIOHUB_APP_ROOT` now.

## 0.6.94 — 2026-09-28 · The kit index names the versions the drive actually carries

**Preparing the three drives for this round turned up the same defect a fourth time.** The kit's
index document named Story Studio **1.4.234** and GatewayKH **0.2.60** on drives that carried
**1.4.235** and **0.2.67**; the drive-root copy of the same 130-line document, untouched since
2026-09-26, still said **1.4.219**, **0.2.44** and **0.6.70**. 0.6.90 and 0.6.91 fixed the copying
and derived the Studio Hub line from the bundle — and left the other two rows to be edited by hand,
which is exactly the pattern that fails. `packaging/kit_stamp_index.py` now reads both from the
newest installer **on the drive** and rewrites those rows, and refreshes the drive-root copy from
the kit copy so the two cannot describe different drives. It **refuses to write a half-stamped
index**: a folder with no installer, or a filename with no version in it, leaves the document
exactly as it was and says which folder confused it, because a stamped `unknown` would be a new
wrong answer rather than a fix for an old one. Wired into `prepare-kit.sh`, so every future prepare
does it without anyone remembering.

## 0.6.93 — 2026-09-28 · The cluster follows the promotion, not just the credential

**A promotion moved every node's token and left the new controller's roster empty.** The §9
rehearsal measured it: the handover reported `verified: 1`, the node adopted the new address and
token, and the new head answered `node_summary.total: 0` — so the site that had three Macs came
back with a capacity of one. The nodes had followed the credential and nobody had told their new
head who they were. A node now **introduces itself to the controller it has just been moved to**,
from the Hub rather than only from the app, using the address the *old* controller reached it at —
which is the only party that knows it, because which interface a Mac answers on is the app's
decision at join time.

**And the promoted Mac was reported as a machine that could not be reached.** It is still on the
retiring controller's roster, so the handover pushed to it, it correctly rejected a token that is
no longer its group's, and the operator's result said `manual: 1` — naming the *new controller* as
the failure. Its row is now forgotten without an eviction record (it is being promoted, not sold),
it is skipped by the push, and it is named in the answer as `promoted` so a machine missing from
the list is visibly recognised rather than silently absent.

Both were found by running the rehearsal, not by reading the code, and both are why that step is
in the release loop.

## 0.6.92 — 2026-09-28 · A removed Mac stays removed, and a cluster follows its own promotion

**Removal used to be bookkeeping, and the fleet treated it as one.** Deleting a node's row left the
Mac holding the group's Fleet token and re-joining on its next start, so "removed" meant "removed
until it reboots" — and with one shared token there was nothing to revoke, which is why the owner's
*"i don't want it to rejoin back because i'm either planning to sell the machine or the machine is
broken"* could not be honoured. Every node is now issued **its own credential at join**, stored
hashed on the controller and `0600` on the Mac that holds it, and presented on every call that Mac
makes home. Removal revokes it, records the removal by name and date, and refuses that id for ever
with a sentence naming the date and where to undo it. Re-admitting is a deliberate action on the
controller. A machine that was already joined before this release adopts its credential on its next
join, so the rollout needs no flag day and no node is touched by hand.

**"Leave this group" worked 0% of the time and reported success.** It sends
`DELETE /api/hub/fleet/nodes/<its own id>` with the credential it holds, and the Fleet allowlist
refused every such request with a 403 — so the app told the operator it had left *and* told him the
controller still listed the Mac, which it did. The route now accepts a machine removing **itself**
and nothing else: a shared group token identifies nobody, so a Fleet-token caller is still refused,
while a node's own credential may remove exactly the row it owns. The two tests that pinned the old
403 were updated deliberately, and the native fixture — which answered a stubbed 200, which is why
this survived a green suite — now answers the real status.

**A cluster now follows its own promotion with nobody touching a node.** Promotion used to mint a
new Fleet token and leave every node holding a dead one, so replacing a controller meant typing an
address and a token into each of thirteen Macs. The promoted node mints its **own** group credential
— never the old controller's — and the **old controller**, while it is still running, is the party
that hands over: it pushes `{new_controller_url, new_token}` to every Mac in its roster over the
channel it already has, then retires itself (it keeps its history, stops accepting dispatch, and says
in its own UI that it handed over to `<M>` on `<date>`). Nodes that could not be reached are named by
name, with the honest limit stated rather than smoothed over: if the old controller is already dead
there is no channel to the nodes, and that case is the runbook plus SSH over Tailscale.

**And the Fleet token can be rotated at last.** `peers.sync_fleet_token` existed with no caller and
no route while the token was shown in the clear on every controller's Connections page, held by every
member, so a leak could not be undone. `POST /api/hub/fleet` with this Mac's Hub token mints a new one
and pushes it to every node, verifying each write with the new credential before committing locally,
and listing by name any Mac that needs the manual path.

**The Fleet page can see who has joined.** `last_seen_at` is stamped for every node that answers a
probe (batched into one write, so a page refresh is not fifty-three rewrites), the roster publishes the
machines it has removed, and the joined-nodes rows carry `role`, `studios`, `version`, `joined_at` and
whether the controller holds a credential for them. A node's green light is now an **authenticated**
read, so after a controller swap it says *credential refused* rather than showing fifty-two Macs green
while the controller calls every one unauthorized.

**Two smaller truths, both found the same way.** The Hub published the same Whisper weights twice for
one Mac — the Subtitle studio and the Voice studio's STT surface both serve them — which became 96
phantom machine-model rows in the gateway's fleet view; one row per machine-and-model is published
now, with the folded count published too rather than silently subtracted. And every one of the 30
model manifests now states its **quantisation** with the sentence saying where it came from, because
the owner's standing rule is that a model named without its quantisation cannot be checked against
what he has actually run.

**In the app:** a **node is told it was removed** and stops re-joining instead of retrying for ever; a
node's own id is exposed; role changes, leaving and removal are confirmed; a "Send here" pin is
cleared when its machine leaves; and "Send here" is hidden on a Mac that cannot dispatch to the pool.

## 0.6.91 — 2026-09-28 · The drive index now stamps itself

**A hand-maintained answer to "which build is this?" has been wrong three times.** The ledger has it
twice — `0.6.63` printed against a `0.6.70` bundle, `0.6.70` printed against `0.6.89` — and this
release is the third: preparing the drives at 0.6.90 copied `Studio Hub KH.md` faithfully, and the
document still said `0.6.89`, because that is what the repository copy said when it was written an
hour earlier. Editing the number again would have been the fourth.

**`prepare-kit.sh` now stamps the two version lines from the app it has just placed on the drive** —
`CFBundleShortVersionString` and the commit in that bundle's own `release-manifest.json` — so the
document beside the app cannot disagree with it, whatever the repository copy says. The repository
copy is corrected to 0.6.90 as well, because a file in git that names the wrong build is the same
defect one level up.

This is the same medicine `README.md`'s Studio Hub row already takes: it names no version at all and
points at the drive's generated `focused-release-manifest.json`. The rule both now follow is the one
this project keeps relearning — **a copy of a fact needs a check, or it needs to be derived.**

## 0.6.90 — 2026-09-28 · The drive index named versions three releases old

**The kit's own READMEs were the last thing anyone updated.** Preparing the drives for distribution
turned up the same drift the ledger has recorded before, in both documents at once: the kit `README.md`
named Story Studio **1.4.219** and GatewayKH **0.2.44** while the kits shipped beside them carried
1.4.234 and 0.2.60, and `Studio Hub KH.md` — the page whose whole job is to tell an operator which
build they have — said **0.6.58 / `eea477e`**, twenty-nine releases behind the bundle in the same
folder. The repository's own copy was worse than the drives': it said GatewayKH **0.2.2**.

**Nothing copied them, which is why they drifted.** `prepare-kit.sh` refreshed the app, the Technical
folder and the kit manifest on every drive and never touched the two index documents, so each drive
kept whatever it was born with. Both are now copied from `packaging/kit-readmes/` on every prepare,
and the Studio Hub row in `README.md` no longer carries a number at all: it points at the drive's own
`focused-release-manifest.json`, which is generated from the bundle on that drive and therefore cannot
disagree with it. The two numbers that remain — Story Studio and GatewayKH — are named, and a prepare
that ships a different build of either is now visibly out of date rather than silently so.

**The kit stopped being a subset of the catalogue.** The drives carried **24 of 30** packages. Every
kit library now has the six the catalogue adds on top — **YuE2 3B and its decoder** (the new singing
engine, on no drive before this), **LTX-2.3 22B**, **gemma-3-12b** (LTX's text encoder) and, where
the drive already held the weights, **Lance 3B and its processor** — in the layout a package verifies
against. Each drive's own `.studiohub/installed.json` records them after a full rehash **on that
drive**: a copy is not an installation until the bytes have been read back, which is why
`release_kit.py` said "not shipped" about packages that were sitting on the disk.

**Where the volume already had the weights, nothing was written twice.** NOVA carried Lance in its
exploration library and LTX on all but UGREEN; the kit layout was built by **hard link** — 15.6 GB
adopted on NOVA with no new bytes. **One deliberate gap remains: Lance is on NOVA only.** It is not
pursued, the owner asked for it to be kept rather than removed, and copying 15.6 GB to three drives
for a model nobody will run is not a good trade; the kit manifests on those drives say it is not
shipped, which is true and is what the app offers as a download.

## 0.6.89 — 2026-09-28 · The worker could not read its own second engine

**The owner installed YuE2 and the app still said "Not installed" — and he asked twice, because the
files were plainly there.** They were: the Hub answered `files_verified` / `eligible` for both
packages, while the picker read `missing` from the *worker's* own catalogue. Three defects were
stacked, each hiding the next.

**1. `MusicCache` verified every manifest against one global engine constant.** `catalog.ENGINE` is
`music-mlx`, ACE-Step's token. YuE2's manifest correctly declares `music-yue2` — it runs in its own
pinned interpreter — so constructing the cache raised *"Music manifest engine does not match the
worker"* before a single file was looked for. Every model now carries its own
`ModelEntry.manifest_engine`, read through `manifest_engine_for`, so the check is the model's own.
`runtime` (the executor inside this worker: `ace-step`, `stable-audio`, `music-yue2`) stays a
separate fact from the manifest's routing token, which is what conflating them cost.

**2. The companion check ran without the catalogue that names companions.** `yue2-3b-mlx` requires
`yue2-vae`; `preflight` takes a `catalog` map to resolve that and the worker passed none, so an
installed, hashed, verified package answered `dependency_missing` for ever.
`model_packages.load_catalog(directory)` is the worker-side reader of a manifest directory (the Hub
keeps its own, which degrades per manifest because it feeds a route), and `MusicCache` now passes it.

**3. The route flattened every failure to `"missing"`.** `cache.state` was
`"cached" if cached else "missing"`, so `manifest_unavailable`, `runtime_unsupported` and
`engine_root_missing` all reached the operator as *"download it"*. It publishes the state the worker
measured, with its reason — a refusal that names its cause and is then renamed by the caller is the
defect, not the refusal.

**And `preflight` refused the engine it ships.** It returned `runtime_unsupported` for any runtime
that was not ACE-Step's or Stable Audio's, so YuE2 could never report as installed even once its
manifest was readable. Both engines that bring their own runtime now pass straight through to the
package answer, which is the only question being asked of them.

**Verified against the real library, not only in tests:** `preflight("vanch007/mlx-Yue2-3B")` now
returns `cached` with its checkpoint root on this Mac. New tests pin the engine token against every
shipped manifest, the companion rule (both packages installed for real from the shipped manifests,
with one-byte stand-ins, because the rule under test is `preflight`'s and a stubbed receipt would
skip it), and that the worker hands its package check the catalogue. One neighbouring test that had
pinned the flattened `"missing"` now pins the named cause it was hiding.

## 0.6.88 — 2026-09-28 · The video verdicts, and six things the app could not show you

**Two owner verdicts recorded, and one of them cancels a run.** Lance is **not pursued** — the owner
tried its output and the quality does not hold up — so the 16 GB Lance run the hardware README had
been asking him for is cancelled. **Nothing is removed:** the runner stays pinned and staged, both
packages stay in the catalog, and its measured rows stay published, because a model kept as a known
spare is a different thing from a model being recommended. LTX-2.3 at **1024×576 true 16:9 with
audio** is accepted as *"close to acceptable"*, so that is the canvas the fleet plans around.

**The accepted canvas now carries the statistic that decides its tier.** Measured through the shipped
worker's own job path — job `e9caa8c4…`, image-to-video, 49 frames at 24 fps, 2.04 s of output with
an audio track, 258.0 s — **16.92 GB peak process footprint** (`ri_phys_footprint`, sampled at 1 Hz)
against **10.05 GB** on the MLX Metal allocator. The allocator alone reads as though a 16 GB Mac fits;
the footprint says it does not, which is why the published tier stays **24 GB minimum / 32 GB
recommended** and 16 GB stays refused by name. The run is in the LTX manifest's `memory_evidence`, in
`qualification/model-readings.json` (so the generated record shows it), and in the receipt
`qualification/ltx-video-worker-results-20260928-004554/`; the manifest's frozen digest moved with it,
as it must.

**An environment lesson, written where the next person will look.** The first attempt at this canvas
died at 15 % after 13 s with `[METAL] Command buffer execution failed: Impacting Interactivity` —
macOS's GPU watchdog firing on a command buffer that held the GPU while the display wanted it, not a
defect in the lane — and the identical command completed on retry. `qualification/
README-video-hardware-test.md` records it beside the numbers, and the "what to expect by Mac" table
now says the 16 GB refusal is confirmed by measurement rather than merely by policy.

### The app surface: six things reported after a real session with it

**The buttons stopped moving.** Every row action was sized by its own label, so `Save…` was narrow
and `Check Original Result` was wide, and the column shifted as the list scrolled. All of them now
share one width and every row reserves one fixed action column (`RowActions`, `rowAction()`), so the
same job reads the same on its Studio page and on the Jobs tab. Two labels were shortened to fit the
shared width: `Check Original Result` is `Check Result`, because a control that cannot fit its own
column is a control that will drift again.

**Every result can be found on disk.** No row could answer *where is the file?* — the app could play,
preview and save a result but never point at it. The Hub has always recorded the worker's own
`output_path` as `artifact_path`; the app now decodes it and every finished row carries **Show in
Folder**, which reveals the file in Finder. It is **disabled, with the reason in its tooltip**, for a
job whose worker is on another Mac, because a button that opens an empty Finder window is worse than
one that explains itself.

**Every row says what it was asked for.** A row showed the result (length, size, and for images the
canvas) and nothing about the request. `requestFacts(for:)` now prints the controls per modality —
Image: steps, guidance, seed; Music: length, steps, guidance, shift, seed, instrumental; Video:
resolution, ratio, length, audio on/off, mode, seed; Audio: the script's character count and the pace
— one function, read by every row, so the Studio page and the Jobs tab cannot describe one job two
ways. **The clip's own measured facts** (canvas, frames, frame rate, whether it has an audio track)
were already on the Hub's item for Video and are now decoded, so a finished 1024×576 clip no longer
reads as "2.0 s · 381 KB" with no dimensions.

**Video rows have a preview.** A clip was a filename and a byte count until it was saved and opened
elsewhere. The row now shows a still from the clip — fetched through the Hub like every other
artifact, handed to AVFoundation from a file in the app's own Playback folder, and cached as a
downscaled frame — and **Preview** plays the clip in place. The frame is taken a tenth of a second in,
not at zero, because frame zero of an image-to-video clip is the still that was submitted.

**The Audio page has a transport.** It had Play and nothing else: no scrub bar, no elapsed time, and
no way to see the pace a clip was made at. It now carries the same transport the Music page already
had — play/pause, stop, a scrub bar, `0:07 / 0:30` — plus the **speed** the job was generated at, and
the finished rows state the **script's character count**, which is the unit a Voice budget is spent in
and which history never showed. The Music lyrics field gained the character count its prompt box
already had.

**YuE2 is installed, and the reason it was not is worth recording.** The Music page said
`yue2-3b-mlx has no files in this library`. Two separate causes: the **SSD kits do not carry YuE2 at
all** — they were built at 0.6.70, before the package existed, which is the open kit-refresh item —
and the copy under `~/StudioHubModels/yue2-3b-mlx/` was in the layout it was downloaded in
(`model/`, `vae/` at the top), not the layout a package verifies against
(`<library>/<package_id>/<revision>/…`). Both packages are now in that layout, hard-linked so no byte
is duplicated, verified by full rehash against their manifests, and the Hub reports
`files_verified` / `dispatch_state: eligible` for `yue2-3b-mlx` and its `yue2-vae` companion. The
VAE's two licence files were absent from the download and byte-identical copies already existed in
the model package, so they were linked from there — the same publisher bytes, checked against the
manifest's own hashes.

## 0.6.86 — 2026-09-27 · YuE2 joins the Music lane, and the tier is measured rather than claimed

**A third music engine, and the first that cannot share an interpreter.** `mlx-Yue` pins
`mlx==0.32.2` while the Voice runtime — and therefore the Music overlay built on it — carries
`mlx==0.31.2`, so YuE2 runs in its own pinned Python 3.12 tree (`packaging/yue2-runtime`, the port
at `9253ed13…`, MLX 0.32.2), staged as `runtimes/music-yue2` and driven by a bridge the worker
spawns. That is the same conclusion the video lane reached with its two engines: the engine a
manifest declares decides the runtime.

**What it is for: a vocal.** YuE2 is the only engine here that writes a *sung* song — ACE-Step 1.5
also sings and Stable Audio 3 cannot — at 48 kHz stereo, up to about six minutes. The lane adds it
beside the other two rather than replacing either.

**The tier is MEASURED, because the model card publishes none.** Three runs on this 32 GB Mac, peak
by sampled `footprint phys_footprint_peak` in the process doing the work:

| Configuration | Audio | Wall | Peak |
| --- | --- | --- | --- |
| 8-bit, mode off, 600 tokens | 24.0 s | 58.6 s | **10.79 GB** |
| 8-bit, mode off, 900 tokens, through this app's bridge | 36.0 s | 103.2 s | **11.81 GB** |
| 8-bit, mode off, 3000 tokens | 120.0 s | 429.8 s | **11.93 GB** |

**The peak barely moves with length, because the weights dominate** — 2.66 GB 8-bit AR + 2.93 GB
bf16 NAR + 0.51 GB VAE — and that is what settles the fleet question the owner asked: **8 GB cannot
run it in any configuration this port offers** (the AR is the only quantizable part; 4-bit would
save about 1 GB against an 11.9 GB peak), **16 GB is the minimum**, and 24 GB is comfortable. The
receipts are in `qualification/yue2-music-results-20260927/`, including the run made through the
app's own bridge.

**Two packages, and the bf16 twin ships on purpose.** `yue2-3b-mlx` (9 files, 9.92 GB) and its
decoder companion `yue2-vae` (7 files, 0.53 GB). The generator's capture deliberately includes
`ar-bf16.safetensors`, the 4.33 GB file the 8-bit AR was quantized from, because the port's
`verify_conversion` refuses a converted tree without it.

**The lane refuses what the engine cannot do, by name.** Lyrics are required — this engine sings and
has no instrumental switch, and the refusal points at the two engines that do. The step count is
bounded by the port's two documented configurations (8 = its fast mode, 32 = its standard), the
guidance scale by its own protocol's 0–20, and the mode by the three the port offers. Seconds are
converted to the port's token budget at the measured ~25 tokens per second.

**One test stopped testing a list.** The music contract test intersected the published parameters
with a hard-coded `{shift, instrumental, lyrics, vocal_language}`; the fourth engine publishes
`mode` and `precision`, so the assertion failed on the new engine's honest parameters while still
passing if a declared one were dropped. It now compares the declared set with the published set in
both directions — the invariant, which holds for any engine.

**Two records were behind the fleet, and tests caught both rather than a reader.** The generated
model record (`docs/model-benchmarks.md`) had nowhere to put a second music engine: its engine→lane
table did not name `music-yue2`, so the package could ship and be silently absent from the page —
exactly the drift that page's own test exists to catch. It learned the engine, and the page gained
the two measured rows that had been living only in `qualification/`: **Lance** (8.11 GB in `relay`,
20.0 GB in `parallel`; 184.9 s / 329.5 s) and **YuE2** (10.79 / 11.81 / 11.93 GB). Every cell in its
measured table now states its unit — the configuration-qualified keys (`peak_mlx_gb_default`,
`generation_seconds_low_ram`) had been quietly dropping them. The second record,
`research/st01-runtime-contracts.json`, refused the new engine until it was told what the engine is;
writing that entry exposed that its `video-lance` reason still said the runner "is not adopted into
this build yet", which has been false since 0.6.82.

## 0.6.85 — 2026-09-27 · The documentation said the small video model could not run

**A claim the code had already outgrown.** Five places still described the `lance-mlx` runner as
"not adopted into this build": the video worker's own module docstring, the capture spec's note
beside the processor package, the LTX runtime's smoke-import explanation, the hardware-test README
a person reads before running Lance on a 16 GB Mac, and a comment beside the manifest digest. The
runner was adopted in 0.6.82 — it is pinned at `f8ecc5a8…`, staged as `runtimes/video-lance`,
measured twice, and published as eligible — so every one of those was a document contradicting the
code that ships beside it.

**Why it mattered rather than being cosmetic.** The hardware-test README told a reader that Lance
"is offered nowhere" and that its 16 GB run "is not something these commands can measure" — the one
document whose whole job is to tell the owner which run to do. Corrected, with the measured numbers
it should have carried: **8.11 GB** peak in `relay` mode (what a 16 GB Mac gets) and **20.0 GB** in
`parallel`, both by sampled `footprint phys_footprint_peak`, and the honest remainder — a 16 GB
machine has still not run it, which is the owner's run to make.

**Also corrected in the same pass:** the LTX runtime's smoke check still maps `video-lance` to
`None`, which is right — that engine lives in its own Python 3.12 tree — but the reason it printed
was the stale one; and a typo (`SDD` for SSD) in the same README.

Nothing about the lane's behaviour changes: this release is the code's own account of itself.

## 0.6.84 — 2026-09-27 · The pickers were empty, and the switch reaches the Video page

**A live defect, found by a test written for something else.** The Video page needed a test for
the audio switch, so the native fixture gained a video model with the lane contract the running
Hub actually publishes — numeric enums, canvases and all. The catalogue stopped decoding, and it
turned out the app on this Mac had been in that state for three releases:

- **`enum` is not always strings.** The video lane publishes `"enum": [2, 4]` for
  `duration_seconds` and `[24]` for `frame_rate`; `NativeLaneParameter` decoded `enum` as
  `[String]`, so the response failed with `typeMismatch … parameters[4].enum[0]`. The lane's
  values are now read as JSON values (`optionValues`) and rendered as text for pickers (`options`),
  so `2` is the integer the lane published and shows as `2`.
- **A snake_case `CodingKey` can never match under this app's key strategy.** The decoder sets
  `.convertFromSnakeCase`, which rewrites the JSON's `aspect_ratio` to `aspectRatio` before
  matching — so `case aspectRatio = "aspect_ratio"` in `NativeLaneCanvas` was unreachable and
  every canvas failed with `keyNotFound: aspect_ratio`.

**Why that emptied every list and not just the video page:** `/api/hub/catalog` is decoded whole,
so one unreadable row fails the entire response. Measured against the running Hub 0.6.83 with the
app's own types: 33 models failed to decode before, and 33 decode now, with the LTX lane's five
canvases, its `["2", "4"]` duration options and its `audio` switch. The fixture keeps a video row
with both shapes, so either regression empties the list and fails the completeness assertion by
name.

**The audio switch is on the Video page now.** Every boolean parameter the selected model
publishes gets a toggle, with the lane's own note under it — for LTX that is the sentence saying
picture and sound are made in one pass, so a silent clip is not cheaper or faster. A lane that
publishes no switch (Lance) gets no control, an untouched switch is left out of the request so the
lane's own default decides, and a model change drops a choice the newly selected model does not
publish rather than sending it on.

**Evidence:** `qualification/native-video-switch-20260927/video-page-with-published-switch.png` —
the real page, rendered offscreen against the running Hub by `native/render-page.sh`, with the
switch, the lane's note and the catalogue counts in the sidebar.

## 0.6.83 — 2026-09-27 · Sound becomes a choice, and 16:9 becomes real

**Ask for a video without sound.** LTX denoises picture and sound *together*, so the audio it makes
is not a separate pass you can pay for or skip — but it does not have to be written. The lane now
publishes an `audio` parameter, and the switch is offered **only by a model that has any**
(`model.generates_audio`): LTX shows it, Lance's text-to-video is silent and shows nothing. It
defaults on, and unticking it sets the pipeline's `generate_audio` attribute to `False`, which skips
the audio decode and writes an MP4 with no audio track.

**The honest note, because it is the whole point of the switch:** a silent run **is not a faster
run**. The joint denoise is unchanged; only the decode and the track are skipped. The contract says
so in as many words rather than letting the checkbox imply a saving it does not make. The artifact
gate matches the request: a track is required only when `audio` was left on, and a track that
appears when silence was asked for is refused.

**A real 16:9 canvas, at a size that is actually 16:9.** The published LTX canvases were 7:4
(448×256) and 11:7 (704×448) — near-widescreen, not widescreen. The smallest canvas on LTX's 64-px
snap grid that is *exactly* 16:9 is **1024×576**, now published with a duration of **2 seconds**.
Measured on this Mac: 49 frames, **9.36 GB** peak Metal, 351.6 s, clean output — receipt in
`qualification/ltx-video-results-16x9-20260927/`.

**Lance was tried at 16:9 and does not hold up.** 512×288 sits on Lance's own grid and renders
(25 frames, **11.0 GB**, 784 s), but the subject degrades badly at that width, and the card only
validates square configurations. So **no non-square Lance canvas is published**; the failure is
recorded rather than shipped as an option.

**Two smaller corrections.** Naming a canvas without a duration used to hand back LTX's default 4
seconds even where that canvas has no 4-second configuration — a request the lane's own validator
would refuse; the default is canvas-aware now. And the 16:9 receipts were moved out of `/tmp` into
`qualification/`, where evidence is supposed to live.

## 0.6.82 — 2026-09-27 · Lance runs, and the lane stops being LTX-shaped

**The second engine is adopted, and it needed a second interpreter.** `lance-mlx` declares
`requires-python >= 3.12` while `ltx_pipelines_mlx` needs 3.11 — opposite constraints, so
"model-agnostic lane" turned out to mean *two pinned runtimes and an interpreter per engine*, not
one tree with two packages. `packaging/lance-runtime/build.sh` builds the second tree (Python
3.14→3.12.14 + lance-mlx at `f8ecc5a8…` + MLX 0.31.2), pinned by content at
`LANCE_RUNTIME_SHA256 = 48dd2bd2…`, and the stager refuses anything else.

**Measured, twice, in the two modes the policy chooses between:**

| Mode | Config | Peak footprint | Wall |
| --- | --- | --- | --- |
| `relay` (what a 16 GB Mac gets) | 256×256, 13 frames | **8.11 GB** | 184.9 s |
| `parallel` (this 32 GB Mac, through the worker's own routes) | 256×256, 25 frames | **20.0 GB** | 329.5 s |

Both by sampled `footprint phys_footprint_peak` **in the process doing the work** — see the defect
below. The card's 16 GB claim stays `[V]`; 8.11 GB on a machine with no memory pressure is a floor.

**The lane stopped assuming LTX's shape.** Canvases, frame rate, frame grid and the latent ceiling
were module constants; a Lance request would have been offered 704×448 at 24 fps on an `8k+1` grid,
outside everything its runner was validated on. They are now per model: Lance publishes square
256²/512² at **12 fps** on a **`4k+1`** grid (measured: 16 frames in, 13 out), checked against its
own card's `n_lat ≤ 16,128` production bound rather than LTX's 30,000-token cliff.

**The bridge, and three defects it found.**

- `run_lance` spawns `runtimes/video-lance/bin/python3 lance_runner.py <spec>` and reads one JSON
  object per line. The runner's own CLI is a **stub** at the pinned commit, so the library API is
  the only surface.
- **The worker measured the wrong process.** Its footprint sampler watches the worker, but for this
  engine the memory is in the child: the first lane run recorded **57 MB** for a job whose real peak
  was ~8 GB, and would have published it. The bridge samples itself now, and its number wins; the
  job names the method.
- **A fixed key list swallowed the measurement.** The bridge reported the peak; `run_lance` copied
  five named keys and dropped it. The list is now explicit and holds the measurement fields.
- **The frame-rate parameter still published 24.** LTX's constant was hardcoded in
  `lane_parameters`, so the Lance default request failed its own validator — found by the first
  worker-alone run, which is what that run is for.

**Also in this release:** the Qwen2.5-VL processor is a **required companion package**
(`lance-3b-processor-qwen2-5-vl`, six files, 4.5 MB, captured at `66285546…`). The weights pack
ships only the tokenizer, so without it the pipeline reaches for huggingface.co at load time; the
first measured run proved a node can work offline with it installed. And
`packaging/video-runtime/normalize_records.py` no longer hardcodes `python3.11` — it walked past the
3.12 tree and would have left every RECORD hashing pre-rewrite scripts whose shebangs carry the
build path, which is exactly the machine-specific digest that file exists to prevent.

**Evidence.** `qualification/lance-video-worker-results-20260927-first/measurement.json` — job
`0f259095f44a4c03958e64119f435b47`, `done`, 256×256, 2.084 s, 276,726 bytes, one video track and
**no audio track** (Lance's t2v is silent, and the LTX-only audio check correctly did not reject
it). `_orchestra/evidence/lance-first-run-20260927.json` for the relay run. Gate: **1380 passed / 11
skipped / 1088 subtests**; `bash native/build.sh` green.

## 0.6.81 — 2026-09-27 · The lane published a canvas its own pack resizes

**Measuring the lane from the SSD found this, and it is the sharpest defect in the video work so
far: the lane's *default* request was a canvas the lane does not produce.** The LTX pack's
two-stage pipeline floors each output dimension to a multiple of 64 before it generates
(`snap_output_dimensions(height, width, two_stage=True)` in the pinned runtime's
`ltx_core_mlx/components/patchifiers.py`: stage 1 runs at half resolution and is upscaled 2x).
`512x288` is not on that grid, so the published `288p` `16:9` canvas came back as **`512x256` —
2:1, not 16:9** — while the job's own acceptance still said `512x288`. Measured in the first run
of the new worker harness:

```
two-stage half-res snaps dims to multiples of 64; output will be 512x256 (requested 512x288)
```

`CANVAS_CLASSES` is now a table of **pairs on the pack's grid** — `256p` (`7:4` 448x256, `4:7`
256x448) and `448p` (`1:1` 448x448, `11:7` 704x448, the measured configuration) — and the model's
default is the canvas it qualified (`11:7` at `448p`), not a ratio the pack would resize. There is
no `16:9` entry and that is arithmetic rather than an omission: with both sides multiples of 64,
the smallest exact 16:9 canvas the two-stage path can produce is `1024x576`. `_assert_canvases_
are_snap_stable()` refuses to import a table off that grid, naming the entry and the size the pack
would substitute.

**The two parameter enums were never a set of canvases, and a consumer built the cross product.**
`lane_contract` now publishes `canvases`: every `(aspect_ratio, resolution)` pair, its exact pixels
and the durations that canvas accepts (from the same `canvas_durations()` the validator enforces,
so published and accepted cannot drift). The validator refuses a pair the table does not carry by
name. `tests/test_video_worker.py` walks the whole cross product and asserts every published
combination is accepted and every unpublished one refused — the invariant whose absence let this
ship.

**Why it mattered to a consumer, measured rather than reasoned:** Story Studio submitted `16:9`
with whatever resolution the picker offered. At `448p` the lane refused it (`448p is not published
for 16:9; that aspect ratio offers: 288p`), and at `288p` it accepted it and returned a 2:1 clip.
The gateway built its video variants as the cross product of the two enums and published
`aspect_ratios` from the union, so the picker offered pairs that do not exist. Both are fixed in
their own repositories in the same change set: the gateway builds variants from `canvases` with
each variant carrying its own ratio, and Story Studio sends the ratio of the row it priced.

**Also in this release — the video hardware test can run from the SSD, on a Mac that has only the
app.** The fleet's hardware step is the owner's, and the SSD kit carried chat and Z-Image commands
and no video one. `qualification/RUN-LTX-VIDEO-WORKER.command` drives the shipped worker (the
app's own `components/` and `runtimes/video`, no checkout), prints the machine, the tier the
manifest publishes and the expected verdict *before* the run — on a 16 GB Mac a refusal is that
test passing — and writes `measurement.json`. `ltx-video-worker-run.py` resolves its hub root from
`STUDIOHUB_HUB_ROOT`, then a checkout, then `/Applications/Studio Hub.app/Contents/Resources`, and
its results-folder guard now permits the folder the wrapper creates for the console log while
still refusing to overwrite a previous receipt (its first run found exactly that:
`FileExistsError` before the run started). `sixteen-gb-ltx-video-run.sh` picks the first runtime
candidate that can actually import `mlx.core` + `ltx_pipelines_mlx`; it used to take the first path
that exists and did not know about the app's runtime, so a node with the app installed was told
"the LTX runtime is not on this Mac" while one sat inside the bundle.

**Evidence.** The wrapper ran end to end on this 32 GB M2 Max with the app bundle's components and
runtime: 16:9 288p 2 s request, job `71865db1877c4e7dbd8918c9e8c9ff64`, `done`, 512x256 at 49
frames, 2.0417 s, 1 video + 1 audio track, 103.7 s wall, peak MLX 9,977,828,986 bytes, peak
footprint 15,386,290,304 bytes. The native Video page reads the same `canvases` list and snaps its
controls onto a published pair. Gate **1376 passed / 11 skipped / 1081 subtests**, `bash
native/build.sh` green.

## 0.6.80 — 2026-09-26 · The app's ffmpeg has no H.264 encoder

**The mux fix in 0.6.79 found the second half of the same problem.** With the Voice runtime's `bin`
on PATH the worker reached ffmpeg and failed inside it:

```
ffmpeg encode_single_frame failed: ... Unrecognized option 'preset'.
```

The Voice runtime's ffmpeg is built from source with a minimal configuration — its encoder list has
`mpeg4` and no H.264 at all. Worse, it **answers `-h encoder=libx264` with exit 0** and the sentence
*"Codec 'libx264' is not recognized by FFmpeg"*, so "there is an ffmpeg" is not the question the
pack needs answered: it names `libx264`, and the output is what says whether the build has it.

`packaging/video-runtime/build.sh` now fetches the **same pinned ffmpeg 8.1** Story Studio already
ships for Final Video Assembly (`osxexperts.net`, arm64, sha256
`ebb82529562b71…` — Story's own `bundled/ffmpeg/MANIFEST.json` records the same URL and hash) plus
its matching ffprobe, verifies both hashes on every build, and **refuses to build a runtime whose
ffmpeg cannot answer for `libx264`**. The binaries land in `runtimes/video/bin`, which is first on
the worker's PATH; the Voice runtime stays behind it for everything else.

`generation.muxer_problem()` now asks the same question at catalog time — no ffmpeg, or an ffmpeg
without `libx264` — and publishes `ready: false` with the sentence instead of advertising a model
that generates for five minutes and then fails at the last step. The answer is cached, because a
catalog read must not fork a subprocess per model.

**Evidence.** The runtime's own ffmpeg round-trips `encode_single_frame`/`decode_single_frame`
(704×448 RGB in, a 1,803-byte H.264 frame out, decoded back to the same shape), `muxer_problem()`
returns None with it on PATH, and `tests/test_video_worker.py` covers all three states — absent,
present without `libx264`, and video-capable. Gate **1373 passed / 11 skipped / 1057 subtests**.

## 0.6.79 — 2026-09-26 · The last step of a video job was missing its muxer

**The pack generated the whole clip and then failed at the mux.** The LTX pipeline decodes the video
and audio latents itself and hands the raw frames and samples to **`ffmpeg`** (`utils/media_io.py`,
`find_ffmpeg()` → `shutil.which`). The packaged Video worker's PATH was its own runtime plus
`/usr/bin`, and the app's `ffmpeg` lives in the **Voice** runtime's `bin` — so the worker generated a
complete clip and then died with the pack's own message:

```
ffmpeg not found on PATH. Install it with: brew install ffmpeg
```

Measured 2026-09-26, on the first through-the-chain run. `packaging/video_worker.py` now puts
`runtimes/voice/bin` on the Video worker's PATH, after its own runtime. Nothing is duplicated: every
Hub build already carries `ffmpeg` and `ffprobe` there (21.7 MB each, the same pair the Voice,
Music and Render lanes use), and a Hub bundle without a Voice runtime is refused at startup. The
alternative — copying them into `runtimes/video` — would add 43 MB and a second licence copy.

**And the catalog says so when it is missing.** `generation.muxer_problem()` asks the same question
the pipeline asks, and a worker that cannot see `ffmpeg` publishes `ready: false` with that sentence
instead of advertising a model that will fail at the last step.

### Video items can be reconciled

An uncertain **video** item had no reconciler: the route fell through to the Image one and answered
`unknown Image batch` — measured on the first uncertain video item this Hub ever held (a cancelled
job whose machine slot stayed pinned until it was discarded). `broker.reconcile_video_item` is the
modest missing entry; the reconciler itself is modality-agnostic and already validated video
receipts. The route's silent Image fallback is gone with it: a modality with no reconciler is named
rather than described as Image.

**Evidence.** `tests/test_video_worker.py` 24 passed / 19 subtests; full Hub gate green.

## 0.6.78 — 2026-09-26 · The video lane's free-memory floor deadlocked it

**Found by the first real chain job, not by a unit test.** The gateway submitted one
image-to-video request through the whole path and the Hub held it `queued` with:

```
governor_note: video: released 5 idle sibling(s); waiting for memory: needs ~16.5GB, ~14.2GB free
```

The item never reached the worker. `min_free_memory_gb=16.5` was copied from the measured *process
footprint* (16.38 GB), and that is not a free-memory requirement: on a healthy 32 GB Mac, available
memory (free + inactive + speculative) sits around 13–20 GB because macOS fills inactive pages with
file cache — and those pages are exactly what the footprint is made of, reclaimable on demand. The
Hub answers an unmet free floor with **wait, not skip** (`broker._memory_gate`), so the lane waited
for memory that was never going to arrive while it had already released every idle model.

The floor is now **8 GB**: a guard against starting into a genuinely full machine, with room for the
run that was measured here to begin. The Image lane recorded this same lesson when it lowered
Z-Image's floor from its 7.1 GB footprint to 3.0 GB, and `tests/test_video_worker.py` now pins the
shape: the live floor must be below the model's tier *and* below the measured footprint.

**What would raise it:** a hardware measurement showing a start below 8 GB free swapping through the
load on a 24/32 GB Mac — which is what the 16 GB/24 GB hardware tests are for.

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
