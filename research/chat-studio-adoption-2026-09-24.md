# Local Chat Studio adoption — implementation record (ST-14)

Date: 2026-09-24. Scope: the owner directed that **Chat is no longer deferred**; a local
language-model Studio was added to the standalone app, taken out of "Coming later", and is to
be stocked and then exposed to Story Studio through gateway-kh.

## What was built

- **Worker** `components/chat/`:
  - `app/backend/llm_engine.py` — single-model MLX engine: lazy load, one lock, honest release,
    streaming, token counts, real MLX memory telemetry, `text_config` context lookup, and
    `make_sampler(temp=…, top_p=…)` (mlx-lm 0.31 takes a sampler, not raw sampling kwargs).
  - `app/backend/memory_policy.py` — the fleet memory contract (`GET/PUT /api/memory-policy`,
    `POST /api/memory/release`) with immediate / balanced / memory_saver / performance and an
    idle-release thread; default **immediate**, like every other Studio.
  - `app/backend/main.py` — FastAPI app: owner-token middleware, `/api/health`,
    `/api/catalog`, `/api/fleet/activity`, `/v1/chat/completions` (streaming and not),
    `/api/chat/completions`, `/v1/models`, `/api/chat/models`, `/api/chat/diagnostics`,
    and the memory-policy routes. `from_environment()` builds it from the worker environment.
    A `response_format` of `json_object`/`json_schema` injects a JSON instruction, validates the
    reply and makes **one bounded repair attempt**, reporting `json_valid`/`repairs` under
    `studio_hub`. This is prompt-level, not grammar-constrained: mlx-lm exposes no JSON sampler,
    so it reduces but does not eliminate invalid JSON (Story Studio still validates). An
    OpenAI-style `seed` is applied via `mx.random.seed` when supplied.
- **Packaging** `packaging/chat_worker.py` (owned-worker entry point; reuses `runtimes/voice`,
  which already carries `mlx-lm`) and a **guarded** chat spec in `packaging/hub_worker.py` —
  inert until the release records the component.
- **Hub wiring**: `components/hub/standalone.py` (`chat-mlx → chat` engine map,
  `WORKER_FOR_MODALITY["chat"]`, the peer-work predicate for `v1/chat/completions`, and the
  native-facing `POST /api/hub/chat/completions` reusing the peer proxy's admission, worker
  signing and lease settlement); `components/hub/backend/activity.py` (`VALID_STUDIOS`);
  `components/hub/standalone_config.py` (`_REQUIRED_WORKERS`); `components/model_packages.py`
  (`chat-mlx` engine). `memory_control`/`capabilities` already knew chat.
- **Native**: `ShellCore.swift` (Chat moved into `Create`), `StudioAPI.swift` (`chatPrompt`,
  `chatTurns`, `submittingChat`, `sendChat()`, catalog surfacing, wire structs),
  `StudioViews.swift` (`chatWorkspace`, ready status).
- **Model manifest** (staged, not yet in the catalog):
  `qwen3-5-9b-4bit` = `mlx-community/Qwen3.5-9B-4bit` @
  `8b2b98c00a6b4d291155e4890773ca8f769aee53`, 11 files / 5,977,071,067 bytes, engine `chat-mlx`,
  captured and accepted by `components.model_packages.validate_manifest`, staged at
  `.build/chat-stage/model-manifests/qwen3-5-9b-4bit.json`.
- **Research / memory**: `research/story-text-llm-shortlist.md`,
  `research/owner-hardware.md` (chosen-model decision), `research/chat-studio-gateway-bridge.md`
  (gateway handoff), `docs/tasks/ST-14.md`.

## Verification (measured on this Mac)

| Check | Result |
|---|---|
| `tests/test_chat_worker.py` | 29 passed — includes the **real engine through the real app with only `mlx_lm` stubbed** (template prompt, sampler, usage counts), `engine_from_environment` layout resolution, JSON mode (instruction injected, one bounded repair, validity reported), and seed application |
| `tests/test_chat_hub_wiring.py` | 6 passed — chat is a valid activity studio; chat POST is admitted as work; native route registered; **end-to-end forward to a real loopback stub worker signed with the worker token**; 401 without the Hub credential; 503 "Chat worker is unavailable" when none is registered |
| `tests/test_chat_packaging.py` | 3 passed — guarded chat spec, incomplete bundle refused, bad identity refused |
| `tests/test_chat_worker_cli.py` | 2 passed — the real `chat_worker.py --check` from a throwaway bundle, and refusal of a bundle missing the manifest |
| `tests/test_chat_native_wiring.py` | 9 passed — Chat in `Create`, page switch, ready status, Send gated on a ready chat model, retired-model reconciliation, Hub route, wire structs |
| `tests/test_peer_path_contract.py` | 22 passed after updating the pinned routing set and the Swift fixture |
| Affected Hub/platform suites | hub-standalone 10, fleet-nodes 16, fleet-credentials 6, memory-policy-contract 16, hub-bundle 3, image-bundle 2, music-bundle 5, model-copy-script 38, model-manifest-catalog 10 — all green |
| `bash native/build.sh` | exit 0 — boundary, API-contract and lifecycle checks |
| Full root suite | 710 tests, 686 ok, 11 skipped, 1 error — the error is the Voice product-suite subprocess timing out at 300 s (ST-13 territory); **no Chat test failed** |
| Upstream contract | Pinned `chatstudio-mac` `2eb7950…` fetched from raw GitHub: it builds `make_sampler(temp=…, top_p=…)` (confirms the sampler fix) and exposes `/api/chat/models`, `/api/chat/load`, `/api/chat/completions`, `/api/chat/diagnostics`, `/v1/chat/completions`, `/v1/models` |
| Runtime support | `mlx-lm 0.31.3` ships `qwen3_5`/`qwen3_5_moe`; the pinned tokenizer renders the model's own chat template; `text_config.max_position_embeddings` = 262144 |

**First real generation receipt (Apple M2 Max, 34.4 GB RAM, 2026-09-24).** The 5.98 GB snapshot
completed locally; `mlx_lm 0.31.3` loads it and `ChatEngine` produced real output:

| Measure | Result |
|---|---|
| `preflight` | `files_verified` |
| Load time | **2.3 s** |
| Context window parsed | **262144** (from `text_config`) |
| First completion (160 tok) | **59.7 s ≈ 2.68 tok/s** |
| Warm completion (120 tok) | **40.9 s ≈ 2.93 tok/s** |
| Peak MLX memory | **5.72 GB** (active 5.04 GB) |

**Decision-critical finding — thinking mode must be disabled.** With the chat template's default
the model emits a long `Thinking Process:` reasoning trace instead of the requested output; the
JSON-mode call failed (`json_valid: false` after one repair) because the trace consumed the token
budget. Passing `enable_thinking=False` to `apply_chat_template` produces clean, directly usable
outline/hook text. The worker's `_prompt` does not yet pass that kwarg, so **a thinking toggle
(or default-off) is required before this model is usable for Story Studio**.

**Performance concern to verify.** ~2.9 tok/s on an M2 Max for a 5.6 GB 4-bit model is far below
expectation (tens of tok/s); this checkpoint is a `Qwen3_5ForConditionalGeneration` loaded
through the text path, and the vision tower / mixed quantization may be defeating the fast path.
Whether 16 GB fleet Macs are usable in practice needs a measurement, not an assumption.

**Still not measured:** packaged-app inference, Hub-dispatched generation, and per-tier fleet
throughput.

## Key findings worth not relearning

0. **Qwen3.5 defaults to a thinking trace.** The engine must pass `enable_thinking=False` (or
   offer a toggle) or the model's "Thinking Process" consumes the budget and JSON comes back
   invalid. Measured above.
1. **`mlx-lm 0.31` takes `sampler`, not `temp`/`top_p`.** Passing raw kwargs to
   `generate`/`stream_generate` raises `TypeError`: `generate_step` has no such parameters.
2. **The OptiQ build's index requires its vision shard.** `optiq/optiq_vision.safetensors` is a
   real shard (and the weights carry a `vision_tower`), so the complete OptiQ snapshot is
   8.22 GB — the 6.6 GB figure was the two main shards only. The plain
   `mlx-community/Qwen3.5-9B-4bit` is 5.98 GB with a byte-identical chat template.
3. **`stage_voice.py`'s `copy_tracked_tree` reads `git ls-files`.** An untracked
   `components/chat` would be silently skipped; `git add` is a prerequisite for staging.
4. **Four Hub gates enumerate modalities/workers.** A local chat worker needs `chat` in
   `WORKER_FOR_MODALITY`, `_REQUIRED_WORKERS`, `activity.VALID_STUDIOS`, the `chat-mlx` engine
   map and the `model_packages` engine allowlist; the peer-path contract test and Swift fixture
   then pin the new set.

## Distribution state

**Not staged and not stocked.** The manifest is staged in `.build/`; the app bundle and the
four SSD kits do not yet carry Chat. Staging is **gated on ST-13** (the pocket-tts/TTS-prune
task), which owns `model-manifests/`, `packaging/capture_model_manifests.py`, the shared
staging path and the four kit libraries, and which currently reports failing tests with a
"Do not stage a release until both are green" instruction.

## Not done / open

- **Streaming through the Hub is buffered, not streamed.** The worker serves SSE at
  `/v1/chat/completions`, but the Hub's peer proxy reads the whole upstream response before
  returning it, so a client that goes through the Hub gets one complete response. The native
  Chat page uses non-streaming for that reason; direct-to-worker streaming works. If streaming
  is wanted through the Hub, `studio_proxy` needs a streaming passthrough — a Hub change, not a
  worker one.
- The exact single-change integration list is in [ST-14](../docs/tasks/ST-14.md) → "Gated apply
  list": `git add` the new files, copy the manifest, add the capture/catalog rows, stage
  `components/chat` in `stage_voice.py`, then stock the library and four kits.
- The gateway-kh bridge is a handoff: gateway-kh's `studio_hub` kind does not serve chat today
  (`StudioHubBackend.generate` raises `MODEL_MODALITY_MISMATCH`). Story Studio's `two-pass`
  planning is implemented but **no tracked Story code selects it from a model id**, so the
  gateway's published id is the open question.
- No real-weight proof, and no independent review.
