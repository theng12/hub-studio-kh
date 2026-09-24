# ST-14 handoff prompt — local Chat Studio

Copy the block below into a new session. It is self-contained: everything needed is either in
it or named as a repo file to read first.

---

## PROMPT

You are continuing **ST-14 — local Chat/LLM Studio** in the Studio Hub KH standalone app.

**Workspace:** `/build/Developer/studio-hub-kh` (macOS, Apple Silicon).

**Read these first, in order:**
1. `AGENTS.md` — project scope and working rules (mandatory).
2. `docs/tasks/BOARD.md` → the `### ST-14 — Local Chat Studio` take-over block.
3. `docs/tasks/ST-14.md` — the full task card, checkpoints, evidence and the gated apply list.
4. `research/chat-studio-adoption-2026-09-24.md` — the implementation record, measured receipts
   and "key findings worth not relearning".
5. `research/chat-studio-gateway-bridge.md` — the gateway-kh handoff.
6. `research/story-text-llm-shortlist.md` and `research/owner-hardware.md` — why this model.

**Objective.** Add a local LLM Chat Studio to the standalone app (moving it out of "Coming
later"), stock the model in the Mac library and the four SSD kits, and expose it so Story Studio
KH can generate scripts/outlines/thumbnail-image prompts/metadata from it.

**Owner decisions already made (do not re-litigate):**
- MLX-first via `mlx-lm`.
- Model: `mlx-community/Qwen3.5-9B-4bit` @ `8b2b98c00a6b4d291155e4890773ca8f769aee53`, 5.98 GB,
  text-only accepted (the one reference-thumbnail vision call stays on the cloud). The OptiQ
  build was rejected: its weight index references `optiq/optiq_vision.safetensors` as a required
  shard, making its complete snapshot 8.22 GB.
- Order: local Chat Studio + stocking first, Story Studio bridge second.

**State — what is already built and verified (do not rebuild):**
- Worker `components/chat/`: `app/backend/llm_engine.py` (single-model MLX engine: lazy load,
  one lock, honest release, streaming, JSON mode, seed, memory telemetry, `text_config` context
  lookup), `app/backend/memory_policy.py` (fleet memory contract), `app/backend/main.py`
  (FastAPI: owner-token middleware, `/api/health`, `/api/catalog`, `/api/fleet/activity`,
  `/v1/chat/completions`, `/api/chat/completions`, `/v1/models`, `/api/chat/models`,
  `/api/chat/diagnostics`, `/api/memory-policy`, `/api/memory/release`).
- `packaging/chat_worker.py` (owned-worker entry point; reuses `runtimes/voice`, which carries
  `mlx-lm 0.31.3`), and a **guarded** chat spec in `packaging/hub_worker.py` that stays inert
  until staging records `release['components']['chat']`.
- Hub wiring: `components/hub/standalone.py` (`chat-mlx → chat` engine map,
  `WORKER_FOR_MODALITY["chat"]`, chat admitted as work, native-facing
  `POST /api/hub/chat/completions` reusing the peer proxy's admission/signing/lease),
  `components/hub/backend/activity.py` (`VALID_STUDIOS`), `components/hub/standalone_config.py`
  (`_REQUIRED_WORKERS`), `components/model_packages.py` (`chat-mlx` allowlist).
- Native: `native/ShellCore.swift` (Chat moved into `Create`), `native/StudioAPI.swift`
  (`chatModel`, `chatPrompt`, `chatTurns`, `submittingChat`, `sendChat()`, catalog surfacing,
  wire structs), `native/StudioViews.swift` (`chatWorkspace`, Send gated on a ready model).
- Tests (49 green): `tests/test_chat_worker.py` 29, `tests/test_chat_worker_cli.py` 2,
  `tests/test_chat_hub_wiring.py` 6, `tests/test_chat_packaging.py` 3,
  `tests/test_chat_native_wiring.py` 9; plus updates to `tests/test_peer_path_contract.py` and
  `tests/native/api_main.swift`.
- Manifest captured/validated, staged at `.build/chat-stage/model-manifests/qwen3-5-9b-4bit.json`
  (11 files / 5,977,071,067 bytes).
- Weights downloaded locally: `.build/chat-model/qwen3.5-9b-4bit`.

**Measured first real generation (Apple M2 Max, 34.4 GB):** `preflight files_verified`; load
2.3 s; context 262144; first completion 160 tok / 59.7 s (≈2.68 tok/s); warm 120 tok / 40.9 s
(≈2.93 tok/s); peak MLX memory 5.72 GB.

**TOP PRIORITY — fix thinking mode.** With the chat template's default, this model emits a long
`Thinking Process:` trace instead of the answer, which also made JSON mode return
`json_valid: false`. Passing `enable_thinking=False` to `apply_chat_template` produces clean,
usable output. Add a thinking toggle to the engine/worker (default off for this model, exposed
on the request body) and a test, then re-run the real proof and record the new receipt. `_prompt`
in `llm_engine.py` does **not** pass this kwarg yet. This is required before the model is usable
for Story Studio.

**Then the gated integration (one atomic change — exact list in `ST-14.md`):**
1. `git add components/chat packaging/chat_worker.py tests/test_chat_*.py` — `stage_voice.py`'s
   `copy_tracked_tree` reads `git ls-files` and silently skips untracked files.
2. Copy `.build/chat-stage/model-manifests/qwen3-5-9b-4bit.json` into `model-manifests/`.
3. Add the `CHOICES`/`DETAILS` rows to `packaging/capture_model_manifests.py` (engine `chat-mlx`).
4. Add the `EXPECTED` row in `tests/test_model_manifest_catalog.py` and update the count.
5. Add the chat block to `packaging/stage_voice.py` inside `if include_hub:`, mirroring the
   Subtitle block, reusing `release['runtime_sha256']` (the worker reuses `runtimes/voice`).
6. Import the model into `~/StudioHubModels/qwen3-5-9b-4bit/<revision>/model` and stock all four
   kit libraries; then stage a release, DMG and refresh the drives per HANDOFF's release loop.

**Concurrency warning.** Another session was active in this repo on 2026-09-24: it released
**0.6.56** (`VERSION`/`CHANGELOG`/`release-notes`/`HANDOFF` changed at 15:19–15:20) and
`model-manifests/` is now back to **37 files with no deletions** — the ST-13 prune I had been
serializing behind no longer appears in the working tree. **Re-read the board and `git status`
and re-confirm ownership of `model-manifests/`, `packaging/capture_model_manifests.py`,
`packaging/stage_voice.py` and the kit libraries before editing them.** If another session still
has uncommitted edits there, serialize with it rather than overwriting.

**Gateway bridge (separate session).** `gateway-kh` is read-only in this project. Its
`KIND_STUDIO_HUB` modality set is `{image, tts, music, transcription}` and
`StudioHubBackend.generate` raises `MODEL_MODALITY_MISMATCH` for chat, so the gateway session
must add the chat modality and a chat branch (details in
`research/chat-studio-gateway-bridge.md`). No tracked Story Studio code selects two-pass from a
model id (only comments), so the gateway's published id is the lever to confirm with the Story
session.

**Known limitation (worker-side, already recorded).** The Hub's `studio_proxy` buffers the
upstream response, so streaming through the Hub returns one complete response; the native page
uses non-streaming. Direct-to-worker streaming works. Streaming through the Hub would be a Hub
change.

**Constraints (from `AGENTS.md`):**
- Move every removal to this project's `_trash/` — never `rm`.
- `gateway-kh`, `story-studio-kh`, `media-studio-kh`, `label-studio-kh` and `chrome-extensions`
  are read-only references.
- The owner runs these models himself; do not raise licensing.
- Run checks locally; do not publish, deploy, tag or enroll nodes without explicit authority.
- Keep the board and `docs/tasks/HANDOFF.md` current; keep measured evidence.

**Verify before you claim anything:**
```sh
cd /build/Developer/studio-hub-kh
.build/voice-runtime/bin/python3 -m unittest tests.test_chat_worker tests.test_chat_worker_cli \
  tests.test_chat_hub_wiring tests.test_chat_packaging tests.test_chat_native_wiring
.build/voice-runtime/bin/python3 -m unittest tests.test_peer_path_contract tests.test_model_manifest_catalog
bash native/build.sh
```
The packaged-app inference, Hub-dispatched generation, per-tier fleet throughput and independent
review are still open. No qualification claim has been made.

---

**End of prompt.**
