# First-release model inventory — source-backed rescope input

**Scope.** Read-only inventory prepared 2026-09-15 for the simplified bundled
app request. It records what current local sources and historical audit receipts
prove; it does **not** download, run, or newly qualify a model. “Candidate”
means present in source but not accepted here for the requested release.

## Source boundary and important distinction

- Standalone baseline: `29d0baa`. Its adopted Voice source is
  `154e7390fcee942ca6bbbcd326fcf9027b37404d` (**Voice 2.7.2**), while the
  read-only reference is `b0df1802cef64d150c6155c514bc0572c7cc5010`
  (**Voice 2.8.1**). This is an adoption gap to assess deliberately, not a
  reason to replace the standalone component during planning.
  See [source observations](source-observations-final.json) and
  [0.1 release manifest](../qualification/voice-release-manifest.json).
- The only standalone end-to-end TTS proof is **not** a requested Base model:
  `mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit` at
  `049ef77fe8816b536193c0c25f9a214d17921282`. It uses preset speakers. Base
  models instead require reference audio and an exact transcript for cloning;
  therefore the CustomVoice pilot is no qualification evidence for either Base
  request. Sources: [import inventory](../qualification/model-inputs.json),
  [pilot result](../qualification/native-redesign.md), and current
  [catalog](/build/pinokio/api/voicestudio-mac.git/app/backend/catalog.py:157).
- All Voice models below use the Apple-Silicon MLX path. TTS and transcription
  share one generation lock in the adopted worker, so no overlapping MLX memory
  spike is permitted. [transcription.py](../components/voice/app/backend/transcription.py)

## Release inventory

| Family / requested model | Exact current model ID and immutable revision | Evidence-backed capability and memory state | First-release status |
| --- | --- | --- | --- |
| Image: FLUX.2 Klein 4-bit | `AITRADER/FLUX2-klein-4B-mlx-4bit` @ `7fd24828501390b67a92c8b66d2fc5a707d0ba1a` | **Passed** 1K text-to-image only on a 16 GB Apple-Silicon worker, four steps and guidance 1.0. The audit deliberately excludes reference-image, edit, and 2K work. | Existing fleet-qualified release choice, subject to adopting its recorded mflux 0.17.5 / MLX 0.31.2 runtime. |
| Qwen3 TTS 0.6B Base 8-bit | `mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit` @ `50f45ef0047cde7e84c2ef04326acb8ada2436a7` | **Passed, narrow scope:** clone-only TTS with 3–15 s reference and exact transcript; 16 GB floor, 24 GB preferred. Audit field `measured_peak_memory_gb` is **9.791 GB**, but does not define whether it is host-wide RAM, MLX allocation, or process RSS. Three short English cases passed; no listening, WER/CER, cross-language, or long-form benchmark. Requires local Turbo Whisper quality validation. | Strongest voice Base starting point; 8 GB is explicitly ineligible. |
| Qwen3 TTS 1.7B Base 8-bit | `mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit` @ `e7dd0585652209fa0d7783659aad4e8a324de11c` | Clone-only TTS; 16 GB floor / 24 GB preferred. The latest source audit is passed and sets transcript-based reference and terminal quality gates, but this inventory has no standalone execution of it. | Candidate for first release after runtime reconciliation and its own qualification; do not infer it from 0.6B or CustomVoice. |
| VoxCPM2 4-bit | `mlx-community/VoxCPM2-4bit`; **revision pending archive/Hub catalog pin step** | TTS, zero-shot, voice-design and cloning; 48 kHz / 30 language catalog claim. 4-bit is the only Vox tier with reported fleet observation: 7.963 GB peak, 3.95× realtime on 17.2 GB M2; 8.6 GB ran but swapped to 47.09× realtime. Source calls 16 GB the floor, 24 GB recommended. | Candidate. Capture a 40-hex snapshot and file hashes before import; qualify on 16/24 GB. |
| VoxCPM2 8-bit | `mlx-community/VoxCPM2-8bit`; **revision pending archive/Hub catalog pin step** | Same modes, 3.23 GB download, 16 GB conservative floor / 24 GB recommendation. Catalog explicitly says no local speed or quality benchmark. | Candidate only; not justified as a release tier until a pinned revision and targeted run exist. |
| VoxCPM2 full | `mlx-community/VoxCPM2-bf16`; **revision pending archive/Hub catalog pin step** | Same modes, 5.0 GB download. Catalog calls it the reference-quality tier and estimates ~0.5× realtime; 16 GB is a known-not-below floor, not a measured safe budget. | Candidate only; needs a 24 GB qualification before exposing as “full.” |
| OmniVoice full | `mlx-community/OmniVoice-bfloat16` @ `6119f7072580cc30c3cbdf496269216b03ad9742` | **Passed historical audit:** TTS with either voice design, optional 3–10 s clone reference, or both; 24 kHz. 8 GB floor / 16 GB recommended; 3.5 GB measured peak. 8 GB is only suitable for a dedicated worker because warning pressure leaves under 1 GB free. | Evidence-backed candidate, but still needs standalone runtime qualification; request is full/bf16 only. |

FLUX evidence: [exact audit](/build/pinokio/api/imagestudio-mac/model-audits/2026-08-02-group-a/aitrader--flux2-klein-4b-mlx-4bit.audit.json). Qwen 0.6B evidence: [production audit](/build/pinokio/api/voicestudio-mac.git/model-audits/2026-09-10-qwen3-tts-0.6b-production-v1/mlx-community--Qwen3-TTS-12Hz-0.6B-Base-8bit.audit.json). Qwen 1.7B evidence: [production-v2 audit](/build/pinokio/api/voicestudio-mac.git/model-audits/2026-08-15-qwen3-17b-production-v2/mlx-community--Qwen3-TTS-12Hz-1.7B-Base-8bit.audit.json). Vox catalog/evidence: [catalog](/build/pinokio/api/voicestudio-mac.git/app/backend/catalog.py:679). Omni evidence: [audit](/build/pinokio/api/voicestudio-mac.git/model-audits/2026-08-08-omnivoice/mlx-community--OmniVoice-bfloat16.audit.json).

## Subtitle Studio: three existing Whisper choices

These are **recommendations**, not an owner-confirmed selection of individual
models. The owner-confirmed first-release shape is “about three Whisper models.”
All three already exist in the adopted registry and provide segment, word and
long-form timestamps.

| Choice | Existing ID / revision evidence | Why it belongs | Status |
| --- | --- | --- | --- |
| Default: Turbo | `mlx-community/whisper-large-v3-turbo` @ `a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb` | The registry’s recommended default (1.6 GB); passed current Voice audit and is the only standalone subtitle engine exercised end-to-end. It also remains the required local validator for Qwen Base cloning. | **Proven standalone pilot and audit-backed**. Existing model import also includes companion tokenizer `openai/whisper-large-v3-turbo` @ `41f01f3fe87f28c78e2fbf8b568835947dd65ed9`. |
| Compact: Small | `mlx-community/whisper-small-mlx`; **revision pending archive/Hub catalog pin step** | Existing 0.5 GB option marked “fast, decent accuracy” for clean English TTS audio. It is a useful editable default for short draft subtitles without adding another inference engine. | **Candidate.** Pin both model and `openai/whisper-small` processor companion revisions, then qualify subtitle accuracy/telemetry. |
| Fastest: Base | `mlx-community/whisper-base-mlx`; **revision pending archive/Hub catalog pin step** | Existing 0.15 GB option marked “very fast, lower accuracy” for clean-audio drafts. It gives a practical low-memory choice while Turbo remains the quality default. | **Candidate.** Pin both model and `openai/whisper-base` processor companion revisions, then qualify subtitle accuracy/telemetry. |

The MLX repositories lack their own processor/tokenizer assets. The current
adapter deliberately attaches a tokenizer from the matching OpenAI base repo;
each new Whisper import must include and pin that small companion snapshot.
Sources: [registry and sizes](../components/voice/app/backend/transcription.py),
[processor map and rationale](../components/voice/app/backend/transcription.py),
[Turbo audit](/build/pinokio/api/voicestudio-mac.git/model-audits/2026-08-17-whisper-decode-conditioning/mlx-community--whisper-large-v3-turbo.audit.json), and
[standalone transcript receipt](../qualification/transcription.json).

## Adopted-runtime gaps that block a truthful release claim

1. **Resolved lock mismatch.** Both current source and standalone declarative
   requirements request mlx-audio commit `2c9461…d384` (the runtime named by
   Qwen 1.7B and OmniVoice audits), but the resolved lock still pins
   `d28d68…8ceb`. Qwen 0.6B’s passed audit uses the lock’s older runtime;
   Qwen 1.7B and OmniVoice do not. Do not call a bundled runtime compatible
   with all requested models until this is reconciled and requalified.
2. **Missing immutable assets.** The standalone has only CustomVoice 0.6B,
   Turbo Whisper, and Turbo’s processor companion in its input inventory.
   It does not carry requested Qwen Base, Vox, OmniVoice, Small or Base Whisper
   assets. Vox, Small and Base Whisper revisions are pending the
   archive/Hub-catalog pin step; an import plan must first record their selected
   snapshot revisions, complete file lists, and checksums.
3. **Memory tiers are model-specific.** 8 GB is unsuitable for Qwen Base and
   operationally poor for Vox 4-bit; it is possible but dedicated-only for
   OmniVoice. The existing worker serializes TTS/STT, but fleet admission and
   one-model-at-a-time unloading still need to be carried into the rescope.
4. **FLUX is bounded.** “Fleet current” proves only 1K text-to-image. The
   release UI must not imply image editing, reference image, img2img, or 2K
   support from this receipt.

Requirement/lock sources: [standalone requirements](../components/voice/app/requirements-generation.txt) and [resolved lock](../components/voice/app/requirements-generation.lock.txt). Existing imported assets: [model inputs](../qualification/model-inputs.json).

## Per-family acceptance commands for the implementation phase

These are proposed local commands/checkpoints, **not commands run for this
inventory**. Each live model acceptance must use the exact imported revision,
record cache/file hashes and telemetry, then add a new receipt rather than
overwriting the older audit.

| Family | Minimum code/contract check | Live acceptance evidence required |
| --- | --- | --- |
| FLUX.2 Klein | `cd components/image && pytest -q app/tests/test_model_audit_contract.py app/tests/test_generation_artifact_contract.py && python audit_truth.py --strict` | One 1K text-to-image on a 16 GB target using the pinned `7fd…ba1` snapshot; verify PNG, dimensions, hash, elapsed time and memory. |
| Qwen Base 0.6B / 1.7B | `cd components/voice && pytest -q app/tests/test_qwen_quality_guardrails.py app/tests/test_qwen_revision_evidence.py app/tests/test_model_audit_contract.py app/tests/test_audit_contract_runtime.py` | For each revision: reference mismatch rejection; valid exact-transcript clone; Turbo transcript gate; long-form section/retry control; memory/telemetry on eligible 16 GB and 24 GB targets; owner listening. |
| VoxCPM2 4/8/bf16 | `cd components/voice && pytest -q app/tests/test_priority_mlx_models.py app/tests/test_generation_artifact_contract.py app/tests/test_memory_policy.py` | First pin asset revision/checksums, then separately prove zero-shot, voice-design and transcript-assisted clone at each exposed tier; record duration, peak memory, swap, output sample rate and listening result. |
| OmniVoice bf16 | `cd components/voice && pytest -q app/tests/test_priority_mlx_models.py app/tests/test_catalog_language_support.py app/tests/test_long_form_policy.py app/tests/test_model_audit_contract.py` | Reproduce clone-only, design-only and combined requests; validate 3–10 s reference handling, tags, 120 s pass limit, 8/16/24 GB telemetry and long-form edge listening. |
| Subtitle Studio | `cd components/voice && pytest -q app/tests/test_transcription_model_registry.py app/tests/test_transcription_engines.py app/tests/test_transcription_decode_options.py app/tests/test_transcription_resource_telemetry.py` | For Base, Small and Turbo: exact model + processor companion revisions, SRT/VTT timestamps, clean and noisy/accented samples, transcript scoring, memory/swap telemetry, and model-switch release behavior. |

## Planning conclusion

The first-release menu is source-feasible. FLUX.2 Klein, both Qwen Base tiers,
OmniVoice bf16, and Turbo Whisper have exact receipt-backed revisions; only
CustomVoice 0.6B plus Turbo Whisper have standalone end-to-end proof. The
smallest defensible build order is: reconcile the MLX lock; in ST01, record
official archive/Hub-catalog metadata for the five missing revisions (Vox 4/8/
bf16 and Whisper Small/Base) without model-download polling; import/pin selected
assets after that checkpoint; preserve serialized MLX admission; qualify Qwen
Base/Turbo then FLUX; add Vox and OmniVoice with separate receipts; finish the
three-model Subtitle Studio after Base/Small Whisper qualification.

### Catalog/default posture

Initial release must expose only the bounded models in this inventory. Store the
initial model selection and the per-workspace default as editable application
settings, with Turbo selected for Subtitle Studio unless the owner changes it.
The current registry is already a modular list of `TranscriptionModel` records
and resolves the recommended default from its `recommended` field, so a later
catalog extension can be an explicit data/configuration change with its own
revision, asset and qualification record. It must not silently make an
unqualified model selectable or change a saved default.
