# Studio Hub KH 0.6.63 → GatewayKH

> ## ⚠️ Read this before the sections below
>
> **This document describes behaviour that is NOT in 0.6.63.** It was written as the release
> notes for a build that had not been cut, and GatewayKH spent real time diagnosing a fleet
> against promises the running binary could not keep. Verified against the installed bundle, not
> the checkout: the same version string, **thirteen commits apart**.
>
> | Documented below | Commit | In 0.6.63 (`a4a78dc`)? | Ships in |
> |---|---|---|---|
> | `bind` published on `/api/health` | `154f0b4` | **No** — returns only `{ok, workers, role}` | 0.6.64 |
> | Listen on this Mac's own networks automatically | `7fcb0fb` | **No** | 0.6.64 |
> | `--listen-auto` (the named setter) | `7fcb0fb` | **No** — only the old `--listen-host` | 0.6.64 |
> | `--fleet-mode` | `ef679e1` | **No** — flag absent from the installed binary | 0.6.64 |
> | `lane_contract.structured_output` | `363e50e` | **No** | 0.6.64 |
> | Chat dispatchable by a Fleet credential | `7267e62` | **Yes** | 0.6.63 |
> | Per-model defaults; the Models tab | `308ff36`, `3e62bc8`, `f8efed4` | **No** | 0.6.64 |
>
> **The rule this produced, in `AGENTS.md`:** a document that describes behaviour names the
> build that has it. "It is implemented" and "it is in the build you are running" are different
> claims, and the second is the one an operator acts on.
>
> Everything below is accurate as a description of the **next** build. Nothing here needs a
> GatewayKH release; it needs Studio Hub 0.6.64 on the Macs.

**For:** the GatewayKH session. **From:** Studio Hub KH. **Date:** 2026-09-25.

This is the "stuff to send" that goes with the prompt: everything Studio Hub KH changed that
GatewayKH has to accommodate, what it can newly read, and what is deliberately still absent. It
supersedes the credential caveat in [`research/chat-studio-gateway-bridge.md`](../research/chat-studio-gateway-bridge.md),
which was measured on 0.6.62 and is no longer true.

**Nothing in this release needs a GatewayKH release to keep working.** The lane contract exists so
that a new lane is a data change on our side; the sections marked *must change* are the ones where
GatewayKH currently refuses a lane it could now serve, not changes that break it.

---

## 1. Your seven asks — all answered

| # | Ask | State | Where it is answered |
|---|---|---|---|
| 1 | `modality` on `/api/hub/model-packages` | **done** (0.6.61) | derived from the Hub's own engine→worker table, so a model on a new engine arrives instead of vanishing |
| 2 | each lane's execution contract, per model | **done** (0.6.61) | every catalog row publishes `lane_contract`; the shape is [`docs/lane-execution-contract.md`](lane-execution-contract.md) |
| 3 | video, if next | **shelved by the owner** | no video worker ships and none is planned in this build. Do not schedule video; there is nothing to read |
| 4 | LLM route/answer shape + `context_window_tokens` / `max_output_tokens` | **done**, and the lane is newly **dispatchable** — see §2.1 | chat worker's `lane_contract` + `model_limits`, each value carrying its source |
| 5 | a node detail reading `could not reach the node: ` with nothing after the colon | **done** | the refusal names the peer, the exception type and the reason |
| 6 | `workers[].studio_version` == `"standalone"` | **done** | every worker reports the release it was staged from; a value that cannot be a version is refused and republished as `unknown` |
| 7 | 401 from a controller on `/studio/voice/api/transcribe/availability` | **done** | not a scope gap — the worker's refusal now names the credential it requires and the one offered |

---

## 2. New in 0.6.63 — what to accommodate

### 2.1 Chat is dispatchable by the fleet (the one you should act on)

On 0.6.62 a Fleet credential got **403** on every chat route while `GET /api/hub/catalog` — which
*is* Fleet-allowed — advertised the chat model as available. That was the inconsistency; it is
fixed. `chat` is now in the Fleet surface for its **read** contracts, and exactly one work route is
allowed:

| Route | Fleet credential |
|---|---|
| `GET /studio/chat/api/health` | **200** |
| `GET /studio/chat/api/catalog` | **200** |
| `GET /studio/chat/api/fleet/activity` | **200** |
| `GET /studio/chat/api/memory-policy` | **200** |
| `POST /studio/chat/v1/chat/completions` | **200** — the one work route |
| `PUT`/`PATCH`/`DELETE` anywhere in `/studio/chat/...` | 403 |
| `POST /api/hub/chat/completions` | 403 — **Hub-token only, deliberately** |

`POST /api/hub/chat/completions` runs the same operation and is *not* offered to the fleet: it is
the local app's spelling, and a second spelling of one operation is the second vocabulary both
projects keep paying for. Use the peer surface.

**Request:** OpenAI-shaped and unchanged — `{"model", "messages": [{"role","content"}], "stream",
"max_tokens" | "max_completion_tokens", "temperature", "top_p", "seed", "thinking",
"response_format"}`. `model` may be omitted; the worker then serves the one it was started with.
Validation is strict and refuses by name: a non-boolean `thinking` is 422, a prompt that cannot fit
the model's stated context is 422 with the measured count and the model's own limit, never a
truncation.

**Answer (non-streaming):** `{"id", "object": "chat.completion", "created", "model", "choices":
[{"index", "finish_reason", "message": {"role", "content"}}], "usage": {"prompt_tokens",
"completion_tokens", "total_tokens"}, "studio_hub": {"elapsed_seconds"}}`.

- `studio_hub.elapsed_seconds` is the **worker's own generation time**, measured in the engine after
  the model is resident — it does **not** include a cold turn's model load. That is the number to
  derive a tokens/second rate from if you publish one.
- Streaming is supported by the worker and buffered by the Hub. If you stream, the final chunk
  carries `usage` and the same `studio_hub` block. (The app uses non-streaming.)

**Every chat completion is recorded as a job on the node that ran it**, exactly like a local one, so
it is listed by `GET /api/hub/jobs` (Fleet-allowed) as `"modality": "chat"` and is collectable
through the same routes as any other modality. Item fields worth knowing:

| Field | Meaning |
|---|---|
| `text` | the assistant's answer — the item **is** the artifact, because the worker streams a completion and keeps no file. `GET /api/hub/jobs/{id}/items/0/artifact` serves it as `text/plain; charset=utf-8` with a matching `X-Content-SHA256` |
| `usage` | `{prompt_tokens, completion_tokens, total_tokens}` — **validated**: whole non-negative counts with `total >= prompt + completion`, otherwise `null`. Never a coerced number |
| `runtime_s` / `duration_s` | the **node's** measured round trip for the proxied call |
| `generation_s` | the **worker's** own stated generation time, or `null` when it stated none. It is a different fact from `runtime_s` and is never derived from it |
| `error` / `error_code` | `CHAT_COMPLETION_FAILED`, `CHAT_EMPTY_ANSWER`, `CHAT_WORKER_UNREACHABLE`, `CHAT_REQUEST_CANCELLED` |
| `model_revision` | honestly `null` — the worker states none |

A Hub-level refusal (for example `409 MODEL_NOT_PACKAGED`) leaves **no** job, because nothing ran.

### 2.2 The memory policy has a fifth mode, and it is the default

`GET /api/memory-policy` on every Studio now offers **`auto`, `performance`, `balanced`,
`memory_saver`, `immediate`** — five, and `auto` is what a Studio that has never been set reports as
its default. The rule behind `auto` is decided **per machine** and must not be re-derived on your
side:

- **at 16 GiB of unified memory and above** → behaves as `balanced` (release after 600 idle seconds)
- **below 16 GiB, or when memory cannot be measured** → behaves as `immediate` (release when each
  job ends)

New fields in the status payload, so a consumer never has to guess what `auto` resolved to:

| Field | Meaning |
|---|---|
| `mode` | the **selected** mode — may be `"auto"` |
| `resolved_mode` | the mode actually in force (`"balanced"` or `"immediate"` when the selection is auto) |
| `idle_seconds` | the **resolved** threshold. Never `null` for `auto` — a consumer reading `null` would render "keeps the model loaded until you release it", which is the opposite of the truth |
| `machine_mode` | what `auto` means on that Mac, always present |
| `options[]` | one entry per mode: `{mode, label, idle_seconds, detail}` where `detail` is an operator-facing sentence naming what the mode does **on this machine** |

`PUT /api/memory-policy` accepts `"auto"`. **Mixed-version skew to be aware of:** a Hub **older**
than 0.6.63 rejects `auto` (its mode table has four entries). Update controllers before nodes, or
keep sending an explicit four-mode value.

Unchanged and still true: a release never preempts queued or running work, and the Hub releases an
idle sibling Studio's model before a job that would otherwise be refused (`prepare_machine_memory`).

### 2.3 Image models publish a second, different memory setting

Image has a **loading-size** control that is not a release policy, and both are published:

- `GET`/`PUT /api/loading-mode` (per machine, `auto` | `low` | `full`) — which mflux memory
  configuration the next job loads with.
- Per model in the catalog, `loading_modes`:
  `{supported, selected, effective, threshold_bytes, options: [{id,label,detail}], measured:
  {default: {peak_gb, seconds}, low_ram: {...}, measured_on, evidence, scene, source}, reason}`.

`supported: false` means the model has exactly one loading mode — `options` is **empty** and
`reason` is the model's own sentence saying so. Do not offer a control from this row when
`supported` is false; it would do nothing. Everything is derived from each model's own policy and
its own measurements, so a new model appears without a GatewayKH change.

### 2.4 Subtitle now publishes its below-tier warning where consumers read it

`memory_warning` is now a **top-level** field on every Subtitle/transcription catalog row (it was
one level down, inside `readiness.host`, where nothing reads it). `null` when the Mac is at or above
the tier. Same key, same place, as `image`, `music` and `chat` already used.

### 2.5 A published runtime now traces to a stored record, or is absent

- **Voice:** two models publish **no** `runtime_seconds` any more, because their numbers existed
  only as prose — VoxCPM2 4-bit (MLX 4-bit) and Pocket TTS 4-bit (MLX 4-bit). Absent means
  unmeasured. If GatewayKH carries a curated fallback for a lane, this is where it would silently
  substitute a number nobody measured; a `null` should stay `null`.
- **Image:** Z-Image-Turbo 6B MLX 4-bit's default configuration was published as `185.61 s`. Nothing
  in the tree stored that figure and the only record that exists says **166 s** (as do four other
  places). Both rows now state 166 s / 236 s and name the document they came from. If you had
  cached the old value, the new one is the sourced one.

### 2.6 The wire vocabulary still says `genstudio_*`

The protocol spells the consumer `genstudio_*` in a few places (`genstudio_candidate` on an audit
record, `genstudio_execution` on a batch). Those names are **unchanged** in this release — renaming
them is a coordinated change, not a unilateral one, and nothing about the runtime depends on the
word. Say the word if you want them renamed and we will do it in the same release as your side.

---

## 3. What GatewayKH has to change

From the bridge research ([`research/chat-studio-gateway-bridge.md`](../research/chat-studio-gateway-bridge.md)),
re-checked against 0.6.63:

1. **Advertise the modality.** `gateway/adapters/__init__.py`'s `KIND_STUDIO_HUB` set and the
   `studio-hub-local` provider's `modalities` both list `image, tts, music, transcription`. Add
   `chat`.
2. **Serve it.** `StudioHubBackend.generate` raises
   `BackendError("Studio Hub does not serve 'chat'", code="MODEL_MODALITY_MISMATCH")` before a chat
   request reaches the Hub. Add a `chat` branch that forwards the OpenAI-shaped body to
   `POST {base}/studio/chat/v1/chat/completions` — through the Hub, not straight to a worker, so
   admission, the machine lease and worker signing all apply — and return the assistant text as the
   `BackendResult` content. The adapter contract is single request/response; start non-streaming.
   **The credential caveat in that note is closed:** a remote node's **Fleet token** now works on
   that route, so a remote-node bridge no longer needs a decision from us.
3. **Publish it.** Discovery must surface the Hub's chat model in the chat catalogue. Report
   `verified_token_usage: false` unless the gateway independently re-measures the token counts — the
   Hub validates the counts the worker states, but validating is not re-measuring.
4. **Decide the published id.** Story Studio's `lmstudio:`-prefixed behaviour is keyed on a model id
   the gateway controls. The tracked Story source has no code that selects two-pass from a model id
   (only comments and fixtures), so ask the Story session which id form unlocks it rather than
   guessing.

---

## 4. Test plan once GatewayKH serves chat

1. `GET /v1/models` lists the local chat model on the `studio-hub-local` lane.
2. A one-shot prose completion returns non-empty text, and the job appears in the node's
   `GET /api/hub/jobs` as `"modality": "chat"`.
3. A `json_object` request either returns parseable JSON or reports the contract honestly. The MLX
   worker has **no grammar-constrained decoding** — `mlx-lm` exposes none — so strict-JSON call
   sites rely on prompting plus validation and retry. A GGUF/`llama.cpp` lane (GBNF) would be the
   reliable route for outline, thumbnail-prompt and metadata contracts.
4. From a **remote** node with a Fleet token, repeat 1–3 to prove the dispatch path rather than the
   local one.
5. In Story Studio, generate an **outline**, a **chapter**, a **thumbnail prompt set** and
   **metadata** from the local model and record the receipts.

---

## 5. Reachability: the bind, and the setter you asked to be named

This answers the listen-by-default prompt (`_handoffs/2026-09-25-studio-hub-listen-by-default-prompt.md`
on your side). **The setter you asked us to name is `--listen-auto`.** There are three forms, all
of them persisted — a flag is a setting, not a one-launch override:

| What you want | The supported call |
|---|---|
| Let this Mac answer on its own networks, and keep doing it | `--listen-auto` |
| Pin an exact list of addresses | `--listen-host 127.0.0.1` (a comma-separated list works) |
| A node that must be reachable after a reboot without anyone opening the app | `--fleet-mode` |

They are arguments to the app, so the kit's install step runs e.g.
`open -a "Studio Hub" --args --fleet-mode`. Each one **writes the preference down**, which is the
part that did not exist before: `--listen-host` used to last exactly until the next launch, so a
kit could configure a Mac and then watch it come up on the old addresses with no way to tell
"ignored" from "forgotten".

**The default changed, so most Macs need no call at all.** A fresh install always computed its
addresses from the machine, but the moment *anything* was saved they froze — so a Mac set up
before it joined a tailnet stayed on loopback forever. An absent `listenHostAutomatic` now means
**automatic**, and automatic is **additive**: the saved list plus every current private and
Tailscale address, recomputed on every launch. It never removes an address, so an upgrade cannot
take away a way in that worked. An operator who genuinely wants loopback-only sets that preference
to false, which the Connections screen now does on the first manual change.

**We never bind `0.0.0.0`.** The wildcard is reported as `wildcard: true` and classified as
`other`, deliberately not as a private address — it is the absence of a scope, not a network.

### Reading it as a fact — your ask 3, with one deliberate departure

You asked for it over `/api/hub/settings`. It is there, **and on `/api/health`, which is the one
that will actually serve you**: `/api/hub/settings` is owner-only and scoped to output storage, so
a **controller cannot read it** — and a controller is exactly who needs to know that a node came
up loopback-only instead of discovering it as a timeout. `/api/health` is the route both sides
already use to answer "is this Mac there?", and it is reachable with a fleet credential.

```json
"bind": {
  "known": true,
  "hosts": ["127.0.0.1", "192.168.18.248", "100.126.154.54"],
  "classes": {"127.0.0.1": "loopback", "192.168.18.248": "lan", "100.126.154.54": "tailnet"},
  "port": 47873,
  "loopback_only": false,
  "reachable_off_this_mac": true,
  "tailnet": ["100.126.154.54"],
  "wildcard": false
}
```

`loopback_only` and `reachable_off_this_mac` are there so you do not have to re-derive them.
**`known: false` is a different answer from loopback** and carries the reason — a Hub started
without its launcher cannot say what it is bound to, and we would rather say that than have you
render it as "loopback" and go and fix a Mac that was fine.

`classes` uses the same rules the shell uses (RFC1918, link-local, and Tailscale's
100.64.0.0/10), so "tailnet" means the same thing on both sides of this.

### Joining is already scriptable — your ask 4

No new work needed, and here is the exact sequence for the install flow:

1. `PUT /api/hub/controller` on the node's own Hub, with the **Hub token** (read it from
   `<data>/hub/.hub_token`; it is `0600` and the installer already runs as the owner). This is
   owner-only by design — a remote caller cannot point a Mac at a controller.
2. `POST /api/hub/fleet/nodes` on the **controller**, with the Fleet token. This one is
   fleet-reachable, so a node can introduce itself.

The app's own Join button is a UI over those two steps, not a separate mechanism.

### What we did not build — your ask 5, partly

`--fleet-mode` covers **listen + start at login + start services at login**. It deliberately does
**not** touch sleep. Holding a Mac awake is a power decision with its own costs, and nobody has
asked for it; if you want it, say so and we will scope it rather than fold it into a flag that
already means three things.

## 6. Per-model defaults (new in this release, and relevant to you)

Every Studio now lets its operator set **their own default for each parameter that model
publishes**, per model per machine. Two consequences for GatewayKH:

- **The resolved values are published.** Both `generation_defaults` and the lane contract's
  per-parameter `default` carry what this Mac will actually use, from one call, so a scheduler
  reads the operator's answer rather than the model's. They cannot disagree — that is asserted per
  row in four lanes.
- **You do not have to send them.** A worker applies the stored default when the request *omits*
  the parameter, and a parameter the request supplies always wins. So an omitted field is not
  "the model's default", it is "whatever this Mac is set to" — which is the more useful meaning
  for a scheduler and the one we now guarantee.

The editor surface itself (`GET/PUT/DELETE /studio/<modality>/api/model-defaults`) is
**Hub-token only**, as settings should be. You neither need it nor can reach it with a fleet
credential, and the resolved numbers above are how you see the result.

## 7. Deliberately not done

- **Video.** The owner has shelved it; nothing is decided and nothing is published. Do not schedule
  video work, and do not read the `video` rows in some tables as a shipping lane.
- **GenStudio exposure of the chat model.** The node-side dispatch works (§2.1), but the *exposure*
  surface does not: no chat file publishes a `genstudio_candidate` block, so
  `model_exposure.candidate_summary` returns `None` for a chat row, and separately
  `monitor.candidate_models()`, `model_exposure.approve()` and `.revoke()` have no caller anywhere
  in our tree — the owner-facing approval surface has no route or UI for any modality. So if
  GatewayKH's path to a model goes through that approval, it is a piece of work on our side, not a
  switch. Tell us if that is the path you use and we will build it.
