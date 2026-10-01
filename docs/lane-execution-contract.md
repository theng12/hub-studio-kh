# The lane execution contract

**Why this exists.** Gateway Studio asked for it in `_handoffs/2026-09-25-studio-hub-lane-contract-prompt.md`
(committed there as `1ad796e`), and their framing is exact:

> Discovery is already solved — the gateway now reads the modality the Hub states, so a model on a
> new engine arrives instead of vanishing. Dispatch is not, and that's the ask.

A gateway can learn *that* a lane exists from a modality string. It cannot learn from a modality
string what to send, what comes back, which states are terminal, or what a cancel means. Today it
guesses those from an engine table, and a guess is what produced the silent drop the modality field
just removed. This document is the shape every studio publishes so that a new lane is a **data
change on the Hub side and no gateway release at all**.

## Where it is published

| Surface | Field | Who computes it |
| --- | --- | --- |
| A studio's own `GET /api/catalog`, per model row | `lane_contract` | the studio — it is the only thing that knows its own API |
| The Hub's aggregate `GET /api/hub/catalog`, per model row | `lane_contract` | passed through verbatim from the studio |
| The Hub's `GET /api/hub/model-packages`, per package row | `modality` and `lane_contract` | the Hub, joined from the engine route and the studio row |

`/api/hub/model-packages` is the one read a scheduler should need. It already carries `engine`,
`state` and `dispatch_state`; it gains `modality` so a reader does not have to make a second,
best-effort read of `/api/hub/catalog` and join on model id — the failure mode gateway measured.

**The name is `lane_contract`, not `execution_contract`.** `execution_contract` already exists on
Voice rows and means something else: TTS *presentation* facts (`text_max_characters`,
`private_section_max_characters`, `quality_guardrails`, `qualification_source`) that the native
Voice page reads. Dispatch facts and presentation facts have different audiences and different
lifetimes, and overloading one object with both is how a field ends up meaning two things. Both
keys ship; a reader that wants to know how to *ask for work* reads `lane_contract`.

## Shape — `studiohub.lane-contract.v1`

```json
"lane_contract": {
  "schema": "studiohub.lane-contract.v1",
  "modality": "image",
  "operation": "image.text_to_image",
  "parameters": [
    {"name": "prompt", "type": "string", "required": true, "max_characters": 4000},
    {"name": "width", "type": "integer", "required": false, "minimum": 256, "maximum": 2048, "default": 1280},
    {"name": "height", "type": "integer", "required": false, "minimum": 256, "maximum": 2048, "default": 720},
    {"name": "steps", "type": "integer", "required": false, "minimum": 1, "maximum": 50, "default": 4},
    {"name": "guidance", "type": "number", "required": false, "minimum": 0.0, "maximum": 20.0, "default": 1.0},
    {"name": "seed", "type": "integer", "required": false, "note": "-1 draws a random seed"}
  ],
  "artifact": {
    "modality": "image",
    "media_type": "image/png",
    "extension": "png",
    "per_item": 1
  },
  "states": {
    "accepted": ["queued"],
    "active": ["running", "cancel_requested"],
    "terminal": ["done", "error", "cancelled"],
    "uncertain": "uncertain",
    "uncertain_is_terminal": false,
    "discard_supported": true
  },
  "cancel": {
    "supported": true,
    "request": "DELETE /api/hub/jobs/{batch_id}",
    "semantics": "asks every studio holding an item of this batch to stop; an item already in flight may still land an artifact, so a cancelled item is not evidence that nothing was written"
  },
  "runtime_seconds": {
    "measured": [
      {"seconds": 166, "steps": 9, "memory_mode": "default",
       "scene": "1280x720, 9 steps, seed 42",
       "source": "qualification/z-image-turbo-2026-09-24.md, \"Generation time [M]\""},
      {"seconds": 236, "steps": 9, "memory_mode": "low_ram",
       "scene": "1280x720, 9 steps, seed 42",
       "source": "qualification/z-image-turbo-2026-09-24.md, \"Generation time [M]\""}
    ],
    "measured_on": "32 GiB M2 Max, mflux 0.20.0, 4-bit pack",
    "evidence": "measured"
  }
}
```

### Field rules

- **`operation`** is the operation name the lane should be asked for — the same token the Hub's
  `capabilities.OPERATION_BY_MODALITY` publishes, so a gateway can join the two without a table.
  Where a worker's own label differs from the canonical token it publishes the **canonical** one
  here: the Hub already normalises its `hub_operation` field and keeps the worker's own wording in
  `hub_reported_operation` (`capabilities.OPERATION_BY_MODALITY` / `hub_reported_operation`), and it passes `lane_contract` through
  verbatim — so a contract carrying the legacy label would be the one field a reader could not join
  on. Music is the live case: the worker's label is `music.text_to_music`, the canonical token is
  `music.generation`.
- **`parameters`** lists the **canonical** name of each parameter. A lane may still accept a legacy
  alias (`duration` for `duration_seconds`, `inference_steps` for `steps`, `cfg` for
  `guidance_scale`); aliases are honoured but deliberately not advertised, so a scheduler writes
  one name and the surface does not grow a second way to say everything.
- **`parameters`** is the *asked-for* surface, not the whole internal one. Each entry states
  `name`, `type` (`string` / `integer` / `number` / `boolean` / `array` / `object`), whether it is
  `required`, and the bound that will actually be enforced (`minimum` / `maximum` /
  `max_characters` / `enum`). A parameter the lane ignores must not be listed: a gateway that sends
  it and sees it silently dropped has been lied to. **Build the list from the model's own limits and
  defaults**, not from a literal — if the numbers live in two places the contract is the next thing
  to go stale.
- **`artifact`** describes what a successful item yields. `per_item` is how many artifacts one item
  produces (transcription yields one transcript plus optional segments; image yields one file).
  `media_type` is the exact type of the proxied bytes, so a gateway can serve them without sniffing.
- **`states`** is the terminal vocabulary. `uncertain` is **not** terminal: an item whose outcome
  the Hub cannot establish is held for an operator decision, and `discard_supported` says whether
  this lane can release it. A scheduler that treats `uncertain` as finished will either lose an
  artifact or double-charge a machine.
- **`cancel`** states the request shape and, in `semantics`, what a cancel does *not* promise.
- **`runtime_seconds`** is what one item of this lane actually costs, and it is published only where
  it has been measured — with `evidence` saying so. Where nothing has been measured the key is
  absent, not estimated: a guessed runtime is how a scheduler sets a deadline that fails on use.

  `measured` is **always a list of rows**, never a single object. Each row carries `seconds` plus
  whatever names the configuration that produced it (`steps`, `duration_seconds`, `memory_mode`,
  `scene`), so a lane whose cost depends on a request parameter publishes the same shape as one
  whose cost does not, and a scheduler can pick the row matching the request it is about to send.
  More than one row is normal, not exceptional: Z-Image-Turbo 4-bit is 27 % slower in low-RAM mode,
  and a lane that published one number would set the wrong deadline for whichever mode the host
  selects.

- **`structured_output`** is optional and currently published by the `chat` lane alone. It
  states **how** a structured answer is guaranteed, which "json_object is supported" does not:
  `formats`, `enforcement` (`prompt_validate_repair` here), `grammar_constrained`,
  `repair_attempts`, and a sentence saying what a caller should therefore do. It exists because a
  downstream consumer was deciding a *client-side* technique — Story Studio's two-pass planning for
  strict-JSON call sites — and the honest contract is the fact about the lane, not a verdict on the
  technique: a producer that published `two_pass: true` would be letting one consumer's strategy
  into the model's data. Absent means "not applicable"; a lane that gains a structured-output
  guarantee publishes this field rather than a note, because a consumer can match a field and
  cannot match prose.

**`deadline_seconds` is not in the worker's contract.** It is the **Hub's** own peer-proxy request
timeout, resolved **per lane** by `standalone.deadline_floor_seconds(modality)` from
`MODALITY_DEADLINE_FLOOR_S` (`components/hub/standalone.py`) — chat **1,800 s**, voice **1,800 s**,
video **7,200 s**, and everything else the `PEER_PROXY_TIMEOUT_FLOOR_S` fallback of **900 s** — owned
by the party that enforces it. It was one flat constant (`PEER_PROXY_TIMEOUT_S = 900.0`) until Studio
Hub 0.6.70, which is why a single number is now wrong for half the lanes. A worker that restated it
would be a fifth copy of a Hub number, which is the duplication this whole document exists to remove.
The Hub adds `deadline_seconds` when it echoes the contract, so the number a gateway reads is the
number the Hub will actually hold it to.

### Per-lane values

The `operation` tokens are **not invented here**. They are the values
`components/hub/backend/capabilities.py:OPERATION_BY_MODALITY` already publishes, quoted verbatim
so a gateway can join the contract to the operation namespace without a table of its own:

| Lane (modality) | `operation` | dispatch route | artifact `media_type` | `per_item` | `discard_supported` |
| --- | --- | --- | --- | --- | --- |
| `image` | `image.text_to_image` | `POST /api/generate/txt2img` | `image/png` | 1 | yes |
| `music` | `music.generation` | `POST /api/generate/txt2music` | `audio/wav` | 1 | yes |
| `voice` | `voice.tts` | `POST /api/generate/txt2speech` | `audio/wav` | 1 | yes |
| `transcription` | `audio.transcription` | `POST /api/transcribe` | `application/json` | 1 | yes |
| `chat` | `chat.completion` | `POST /v1/chat/completions` | `application/json` | 1 per pack | no — a pack has no artifact to discard |
| `render` *(local only)* | `video.render` | `POST /api/generate/render` | `video/mp4` | 1 | yes |
| `video` | `video.generation` | `POST /api/generate/txt2video` | `video/mp4` | 1 | yes |

The dispatch routes are `broker.MODALITY`'s own strings; `transcription` and `chat` are absent from
that table on purpose, because they have their own job modules (`transcription_jobs.py`,
`chat_jobs.py`) rather than going through the batch dispatch path. The state vocabulary is
`broker._OUTCOME_STATES = {done, error, cancelled}` plus `queued`, `running`, `cancel_requested` and
`uncertain`; `broker.discard_uncertain_item` refuses any item that is not already uncertain; and
`auth.FLEET_ALLOWED` names the cancel request as `DELETE /api/hub/jobs/{batch_id}`.

### `output_format` — compress at source (image, music, voice)

*(Not in the 0.6.126 build; lands with the build that adds `components/audio_encode.py`.)*

`image`, `music` and `voice` publish an optional per-job parameter `output_format` in
`parameters`, marked **`request_only`** (a per-job choice, never an operator default; the model
defaults editor does not offer it). A request that omits it gets today's file, byte for byte; a
request that sets it asks the worker to deliver the compressed file instead:

| Lane | `enum` | native (omitted) | compressed |
| --- | --- | --- | --- |
| `image` | `png`, `jpeg` | PNG | JPEG quality 90, 4:2:0, progressive (`image/jpeg`) |
| `music` Stable Audio 3 | `wav`, `m4a` | WAV (`audio/wav`) | AAC-LC 256 kbps `.m4a` (`audio/mp4`) |
| `music` YuE2 | `flac`, `m4a` | FLAC (`audio/flac`) | AAC-LC 256 kbps `.m4a` (`audio/mp4`) |
| `voice` | `wav`, `m4a` | WAV (`audio/wav`) | AAC-LC 128 kbps mono `.m4a` (`audio/mp4`) |

Rules a scheduler can rely on:

- **The worker encodes after the lossless step, outside its inference lock**, so the next
  generation starts while this one compresses. The job record, receipt and artifact headers always
  describe **the delivered file** (`media_type`, `format`, `bytes`, `sha256`); `audio_duration_s`,
  `sample_rate_hz`, `channels`, `width` and `height` describe the audio or image and come from the
  lossless master.
- **An encode failure delivers the native file** and records `delivery_fallback_reason` (and a
  `delivery` block with the master's own bytes and sha256). A paid job is never failed by
  compression, so a caller must read `media_type`, not assume it got what it asked for.
- **Send it only to a lane that publishes it.** The controller strips `output_format` from the
  worker body when the target worker's `lane_contract` does not list the value (an older node), and
  leaves it out of the recovery request comparison, so adoption of a finished job is unaffected.
- The Hub controller accepts `image/png|image/jpeg` and `audio/wav|audio/flac|audio/mp4` receipts.
  A compressed voice artifact is streamed through a SHA-256 and byte count (never buffered) and the
  worker's decoded duration, rate and channels are kept.
- Gateway idempotency replays by key alone, so a caller that changes the format must change the key.

Two notes a gateway must not skip:

- **`render` is local-only** (`monitor.LOCAL_ONLY_MODALITIES = {"render"}`). It is in the table
  because it exists, not because a remote scheduler may place it.
- **`video.generation` ships as of 0.6.76** (`components/video/`), and its row was corrected here in
  0.6.83: it was written as a forward declaration while no video worker existed, and the worker has
  since arrived. The lane publishes two things the other lanes do not, both for the same reason —
  a video request is a *pair* of choices, not two independent ones:
  - **`canvases`**: every `(aspect_ratio, resolution)` pair that exists, with its exact pixels and
    the durations that pair accepts. The two parameter enums cannot express the pairing, and the
    first consumer to read them as a cross product (Story Studio) offered `448p + 16:9`, which the
    lane refuses. `published_canvases()` and `validate_request()` read the same function.
  - **`audio`**: a boolean, published **only by a model that has any**
    (`VideoModel.generates_audio`). LTX denoises picture and sound together, so unticking it does
    **not** shorten the run — it skips the audio decode and writes an MP4 with no track. The
    parameter's own `note` says so, because a checkbox that looks like a saving it is not is the
    kind of thing a scheduler would otherwise plan around. A model that is silent publishes no such
    parameter, and the artifact gate requires a track only when `audio` was left
    on (it also refuses a track that appears when silence was asked for).
- **Correction, 2026-09-26: the Hub already has an image-to-video path, and it is the one that
  matters.** This bullet said *"there is no image-to-video operation"*. The code says otherwise and
  the code wins. `broker._video_multipart_fields` is documented *"Flatten an image-to-video request
  for Video Studio's multipart API"*, and the reference-image path sets `body["mode"] = "img2video"`
  and dispatches to `POST /api/generate/video2video`, gated on **`img2video` being in the model's
  published `capabilities`**. Both paths exist: `broker.MODALITY["video"]` → `POST
  /api/generate/txt2video` for text-to-video, and the `video2video` multipart route for
  image-to-video. **The image-to-video path is untested and undocumented — `img2video` and
  `video2video` appear in `broker.py` and nowhere else in the tree**, which is its own finding and
  is why a video worker must publish the operation explicitly rather than inherit it.
- **A worker must publish the operations its consumers actually call.** The only shipping consumer
  is Story Studio KH, and it retired text-to-video (`routes/ai-video.ts` answers *"400 Text-to-video
  has been retired"*) and sends **image-to-video only**. So `capabilities` is what a scheduler reads
  to tell the two apart, and a lane that publishes one while the consumer calls the other is the
  guess this document exists to remove.

## Model limits — never publish a number the model did not state

Gateway's fourth ask, verbatim:

> most importantly — context_window_tokens / max_output_tokens per model. Where nothing states them
> I publish your curated flat 1,000,000 / 128,000 labelled curated; honest label, but it isn't the
> model's own number and Story Studio shows it as if it were.

The fix is not a better curated number; it is to publish the number **with its provenance**, and to
publish nothing where nothing states one:

```json
"model_limits": {
  "context_window_tokens": {"value": 262144, "source": "model/config.json text_config.max_position_embeddings"},
  "max_output_tokens": {"value": 4096, "source": "worker default: the checkpoint ships no generation_config.json"}
}
```

- `value` is `null` when no source states the limit. `source` is then the reason, so a reader can
  label its own fallback honestly rather than inheriting ours as a fact.
- A worker default must say it is a worker default. `max_output_tokens` for Qwen3.5-9B 4-bit is
  **4096**, which is `llm_engine.FALLBACK_MAX_OUTPUT_TOKENS`, not a number the checkpoint states.
- Measured for the shipped chat model: `mlx-community/Qwen3.5-9B-4bit` publishes
  `text_config.max_position_embeddings = 262144` in its own `config.json`, so the context window is
  **262144** [M] — two orders of magnitude away from the flat `1,000,000` a reader currently sees.

## The three defects gateway measured

1. **A node detail that reads `could not reach the node: ` with nothing after the colon.**
   `components/hub/backend/fleet_nodes.py`'s node-detail fallback interpolates the exception, and an
   exception with an empty message renders as nothing. A message that stops at the colon is worse
   than no message: it reads as though the reason were withheld. The refusal must always name
   *something* — the peer it tried, the exception type, and the reason when there is one.
2. **`workers[].studio_version` is the literal string `"standalone"` on three of four workers.**
   The Image, Music, Subtitle and Chat workers each answered `{"app_version": "standalone"}` on
   `/api/health`; only Voice read a real version. The Hub used to publish whatever the worker said,
   so a version field carried a non-version. Each worker now reports the release it was staged from,
   and the Hub refuses to republish a value that cannot be a version
   (`monitor.reported_app_version`), so the defect cannot return through a new worker either.
3. **A 401 from `studio-mac` on `/studio/voice/api/transcribe/availability`.**
   The path *is* inside the fleet scope (`auth._FLEET_STUDIO` includes `voice`), so this is a
   credential question between that controller and that node, not a scope gap. The Hub side of the
   fix is to make the refusal self-describing: which credential was presented, which one the route
   required, and which Mac to look at. A bare 401 across a fleet is unattributable.

## What this document does not cover

Nothing above is published until the lane's worker publishes it. The `video` row is a forward
declaration; `render` exists in the Hub's own table but is local-only. A published contract for a
lane with no worker is exactly the guess this file exists to remove.
