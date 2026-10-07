# Selected MLX 4-bit MoE preparation implementation plan

Status: in progress, not released or installed. Owner authorized wiring, downloads and SSD preparation; all physical inference remains held.

Goal: stock the three exact owner-selected checkpoints and prepare manifest-driven Chat selection with one resident model, explicit pending admission, and truthful loading/prefill/decode telemetry.

Spec: /build/Developer/_orchestra/audit-20261007/moe-qualification/PREPARATION.md. Canonical plan extension, not a new planning system. Base4e4e3a5 /0.6.152; isolated study/moe-24-32gb-20261007 worktree. Nemotron3.5 Lightning30B-A3B MLX4-bit has independently verified all11 canonical file SHA256s. Qwen3.6 35B-A3B MLX4-bit is downloading second; Gemma4 26B-A4B QAT MLX4-bit third. No inference, baseline, benchmark, candidate activation, app restart or fleet rollout in preparation checks. 32 GiB alone sufficient; English only. Keep all3 on all4 SSDs regardless of qualification results.

## Frozen contracts

- New optional manifest boolean `production_allowed`: absent means legacy policy; only literal true permits production. Validate a present field as boolean. New candidates set false with qualification.status unqualified; files alone never grant admission. Do not globally block legacy unqualified manifests.
- Shared metadata reader `components.model_packages.production_allowed(manifest) -> bool`; malformed values fail closed. Model readiness publishes the boolean and a named admission state/reason. Known pending candidates return MODEL_UNQUALIFIED before joined-node fallback; direct worker and peer requests cannot bypass it.
- Optional engine callback `on_progress: Callable[[dict],None]`. Events carry phase waiting/loading/processing/generating/completed, attempt index, processed_prompt_tokens/total_prompt_tokens, and only measured MLX prompt_tokens/prompt_tps/generation_tokens/generation_tps/peak_memory_bytes/finish_reason. max_tokens is a ceiling, never expected completion progress.
- Preserve existing string stream deltas and float terminal elapsed_seconds contract. Add complete() outcome telemetry and callback completed event telemetry: {attempts:[per-attempt metrics], final_attempt:N}. API forwards this as studio_hub.telemetry. Absent metrics remain absent; no deriving decode TPS from elapsed wall time. Repair attempts remain distinct, final attempt metrics labelled as such.
- Exactly one resident ChatEngine across all catalog models, with one plain global session lock. Every check_prompt_fits/complete/stream uses the requested engine proxy under that lock; switching releases old model, runs GC and cache clearing before loading the next. Failure/cancellation closes engine stream and releases global lock exactly once. Metadata engine objects may coexist; loaded weights/tokenizers may not. Pending selection refuses before load. Direct standalone qualification probe remains separate and held.
- Catalog and route model identity/defaults/limits are per selected manifest. Legacy injected FakeEngine fixtures continue to work. Manifest default:true identifies the baseline; absent defaults with one production model retain existing behavior. Unknown/duplicate/default conflicts refuse clearly. No candidate family/name lists in consumers.
- Worker activity is keyed by request/job id: concurrent waiting requests cannot overwrite another request telemetry or finish it. Bind callbacks to that id and selected model. Preserve private credentials, SSE preflight refusal before200, and cancellation semantics.

## Tasks / owned boundaries

1. Engine metrics: components/chat/app/backend/llm_engine.py and tests/test_chat_worker.py, new tests/test_chat_engine_metrics.py. Reuse stream_generate for measured nonstream results, preserve JSON repair and cache discipline; prove fake prefill/decode separation, missing metrics, two repairs-attempt records, balanced stream lock and cancellation. No real MLX/model import in checks.
2. Admission: components/model_packages.py, components/hub/standalone_config.py, components/hub/standalone.py, new tests/test_model_production_admission.py. Prove all-ready false candidate remains ineligible, legacy compatibility, malformedflag rejection, joined-node/peer admission cannot bypass false.
3. Worker multi-model/phase integration: new components/chat/app/backend/model_manager.py, components/chat/app/backend/main.py, packaging/chat_worker.py, packaging/hub_worker.py, model-manifests/qwen3-5-9b-4bit.json default metadata, new tests/test_chat_model_manager.py. Prove one residency, A-to-B unload order, exact selected identity, duplicate/default rejection, failure/cancel lock safety, pending refusal and request-scoped progress/telemetry.
4. Coordinator: generate candidate manifests only from verified whole-model receipts as each finishes; all3 remain production_allowed:false and RAM/context qualification unmeasured. Forward metrics/progress through Hub chat_jobs/fleet activity and native StudioAPI/StudioViews, with worker stated rates and clear loading/processing/generation labels. Preserve processing numerator/denominator; generation indeterminate. Probe live metrics with fake responses only. Missing Gateway counterpart remains isolated for reconciliation.
5. Independent review and appropriate offline suites; code-only Graphify refresh. Complete CHANGELOG/release-notes/VERSION/handoff, commit/push normal flow and verified DMG. Do not install shared Hub or run model qualification in this phase. Stock canonical packages and full hashes on four mounted SSDs; notify only when all3/all12 copies verified, state any unfinished wiring explicitly.

## Review focus

- Explicit pending flag cannot be bypassed through remote/local/worker routes or a fully verified file receipt.
- Cancellation and failed model switch must leave no two resident models or locked generator; no repeat generation after a TypeError.
- Shared state must not attribute another request or repair attempt tokens/TPS to this one.
- Empty/NaN/missing metrics stay absent; prefill work is never presented as decode speed.
- Legacy catalog/default/auth/JSON/SSE contracts remain usable and app startup never loads a pending candidate.

All workers are bounded, do not commit/deploy/recurse. Use recoverable synthetic fixtures; never delete existing trash. Coordinator owns review, integration and release.
