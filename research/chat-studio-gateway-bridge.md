# Chat Studio → gateway-kh bridge (ST-14.7 research)

Date: 2026-09-24, re-checked 2026-09-25 against the shipped 0.6.62 bundle. Status: **research /
handoff, not implemented.** `gateway-kh` and `story-studio-kh` are read-only in this project, so the
gateway change below belongs to the gateway-kh session. This note exists so that change is precise
and does not have to be re-derived.

> **2026-09-25 correction.** The model line below used to name the **OptiQ** manifest
> (`qwen3-5-9b-optiq-4bit`, 10 files / 7.12 GB) as "staged until ST-13 lands". That build was
> dropped on 2026-09-24 (its OptiQ index makes `optiq_optiq_vision.safetensors` a required shard,
> so the complete snapshot is 8.22 GB). The shipped model is the plain build:
> **`mlx-community/Qwen3.5-9B-4bit`, uniform 4-bit MLX, 11 files / 5,977,071,067 bytes (5.98 GB),
> revision `8b2b98c00a6b4d291155e4890773ca8f769aee53`**, package id `qwen3-5-9b-4bit`. Staging has
> landed: the 0.6.62 bundle carries `components.chat` and the packaged Hub publishes the model
> (`/api/hub/catalog` → exactly one chat row, `capabilities: ["chat.completion"]`).
>
> **Credential caveat — CLOSED in 0.6.63, 2026-09-25.** This paragraph used to say the peer path
> below works with the Hub token but that a *fleet* credential gets **403** because `chat` was
> absent from `_FLEET_STUDIO`. The owner decided that every model which is running should be
> **shown and runnable**, and 0.6.63 does it: `chat` is in `_FLEET_STUDIO` for its read contracts
> and one work route — `POST /studio/chat/v1/chat/completions` — is allowed to a Fleet credential.
> So a **remote node's** fleet token now drives chat as well as the local Hub token does, and that
> decision is no longer a prerequisite for this bridge. `POST /api/hub/chat/completions` stays
> Hub-token only, deliberately: a second spelling of one operation for the fleet is the second
> vocabulary this project keeps paying for. The Hub also echoes our
> `lane_contract.deadline_seconds`, which this adapter already reads. The full current picture,
> including the job record a chat completion now leaves on the node, is in
> [`docs/gatewaykh-handoff-0.6.63.md`](../docs/gatewaykh-handoff-0.6.63.md).

## What Studio Hub already exposes (this project)

- `components/chat/` — a packaged local MLX worker serving OpenAI-shaped
  `POST /v1/chat/completions` (streaming and non-streaming), `/api/health`, `/api/catalog`,
  `/api/fleet/activity` and the fleet memory-policy contract.
- Hub routes: the native-facing `POST /api/hub/chat/completions` and the peer surface
  `POST /studio/chat/v1/chat/completions`. Both resolve the local `chat` worker, apply the
  peer admission reservation, re-sign the request with the worker's own token and settle the
  lease on the response.
- Hub modality maps already include chat: `capabilities.OPERATION_BY_MODALITY["chat"]`,
  `memory_control.SUPPORTED_MODALITIES`, `activity.VALID_STUDIOS`, the `standalone.py`
  engine→modality map (`chat-mlx → chat`) and `WORKER_FOR_MODALITY["chat"]`.
- Model manifest (shipped in `model-manifests/`): `qwen3-5-9b-4bit`, engine `chat-mlx`,
  `mlx-community/Qwen3.5-9B-4bit` (uniform 4-bit MLX), revision
  `8b2b98c00a6b4d291155e4890773ca8f769aee53`, 11 files / 5,977,071,067 bytes. It publishes
  `capabilities: ["chat.completion"]` and a 16 GB qualified tier.

## What gateway-kh does today (read-only inspection, 2026-09-24)

- `gateway/adapters/__init__.py` — `KIND_STUDIO_HUB` supported modalities are
  `frozenset({"image", "tts", "music", "transcription"})`. **Chat is not among them.**
- `gateway/providers/definitions.py` — the `studio-hub-local` provider declares
  `modalities=("image", "tts", "music", "transcription")`.
- `gateway/adapters/studio_hub.py` — `StudioHubBackend.generate` raises
  `BackendError("Studio Hub does not serve 'chat'", code="MODEL_MODALITY_MISMATCH")`, so a
  chat request to the local lane is refused before it reaches the Hub.
- Custom providers are data, not code: `CUSTOM_PROVIDERS_PATH = DATA_DIR / "providers.local.json"`.

## The change gateway-kh needs (its session)

1. **Advertise the modality.** Add `"chat"` to the `KIND_STUDIO_HUB` modality set and to the
   `studio-hub-local` provider's `modalities`. (Re-checked 2026-09-25: neither carries chat yet,
   and `StudioHubBackend.generate` still raises `MODEL_MODALITY_MISMATCH` for it.)
2. **Serve it.** Add a `chat` branch to `StudioHubBackend.generate` that forwards the
   OpenAI-shaped body to the Hub — preferably `POST {base}/studio/chat/v1/chat/completions`,
   so the Hub applies admission and worker signing rather than the gateway reaching a worker
   directly — reusing the adapter's existing base/header helpers, and returning the assistant
   text as the `BackendResult` content. The current adapter contract is single request/response;
   start non-streaming. Use the local Hub token (verified working, 2026-09-25); see the credential
   caveat above before pointing it at a remote node.
3. **Publish it.** Discovery must surface the Hub's chat model in the chat catalogue. Report
   `verified_token_usage` truthfully: the Hub worker returns prompt/completion counts, but the
   gateway does not independently re-measure them, so it should stay `false` unless the
   gateway verifies.
4. **Decide the published id.** Story Studio's `lmstudio:`-prefixed behaviour (two-pass scene
   planning, small windows) is a Story-side convenience keyed on a model id; the gateway
   controls that id. **Checked in tracked Story Studio source (2026-09-24):** `planning_mode` /
   two-pass is implemented in `electron/server/generators/scene-plan.ts` and typed in
   `electron/server/models/types.ts`, but there is **no code that selects two-pass from a model
   id** — the only `lmstudio:` strings are comments in `types.ts` (lines 4633 and 4686) and two
   frontend fixtures using `lmstudio:gemma-4-e2b-it-mlx`. So the Story build under test does not
   itself key on that prefix; the gateway's published id (or a Story-side change) decides. Ask
   the Story session which id form actually unlocks two-pass before relying on it.

## Story Studio side

- No code change is required if the gateway publishes a normal chat product: Story already
  sends `prose` and `json_object` output contracts, and a live model appears in its catalogue.
- Strict-JSON call sites are the caveat: the MLX worker has no grammar-constrained decoding
  (`mlx-lm` exposes none), so those calls rely on prompting plus the app's own validation and
  retry. A later GGUF/`llama.cpp` lane (GBNF) would be the reliable route for the outline,
  thumbnail-prompt and metadata contracts. See `research/story-text-llm-shortlist.md` §7.

## Test plan once gateway-kh serves chat

1. `GET /v1/models` lists the local chat model on the `studio-hub-local` lane.
2. A one-shot prose completion returns non-empty text.
3. A `json_object` request either returns parseable JSON or reports the contract honestly.
4. In Story Studio, generate an **outline**, a **chapter**, a **thumbnail prompt set** and
   **metadata** from the local model and record the receipts in ST-14.
