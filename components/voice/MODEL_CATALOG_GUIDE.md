# Voice Studio Model Catalog Guide

The Models tab is family-first. A **family** explains the voice technology and
shared workflow; a **model entry** is one downloadable option within that
family, such as 4-bit, 8-bit, full precision, Base, CustomVoice, or VoiceDesign.

## Taxonomy

- `family`: Stable engine/product identity, for example `qwen3-tts` or `pocket-tts`.
- `capabilities`: User outcomes. Use existing values where possible: `tts`,
  `voice-cloning`, `multilingual`, `expressive`, and `streaming`.
- `apple_optimized`: Derived in `serialize_model()` from the family's own
  `engine` (`catalog.ENGINE_MLX_AUDIO`). There is no second MLX list to keep in
  sync: a family that declares the MLX engine is labelled "Apple MLX" and
  survives the "🍎 MLX only" filter automatically.
- `min_unified_memory_gb`: Realistic runtime floor, not just model file size.
- `size_gb`: Decimal download size after `ignore_patterns` are applied.
- `label`: Family name plus a concise option name. Include precision when it
  matters and use `recommended` only for the preferred option in a family.

## Add A New Family

1. Add one `Family` to `app/backend/catalog.py` with a stable id, its `engine`
   and `requirements` (both required — the import refuses a family that omits
   them), its sectioning facts (`section_max_characters`, `join_pause_seconds`,
   any `section_refinements`), the request fields its resolver honours
   (`lane_parameters`), a plain-language summary, usage guidance and accurate
   `TextGuidance`.
2. Add one or more `ModelEntry` rows using that family id.
3. If the engine is new, add it to `_WORKER_BY_ENGINE`,
   `_RUNTIME_PROBE_BY_ENGINE` and `_ENGINE_RUNTIME_LABEL` in
   `app/backend/generation.py`, plus the dispatch branch.
4. If it uses an existing engine, nothing else in `generation.py` changes. For
   `mlx-audio` specifically, add its per-family config row to
   `MLX_AUDIO_FAMILIES` — both files validate the two sets against each other at
   import and refuse to load, by name, when they disagree.
5. Add dependency requirements to the family entry, not to a second list.
6. `GET /api/catalog` publishes the family's `lane_contract` automatically
   (see `app/backend/lane_contract.py` and `docs/lane-execution-contract.md`).

## Add An Option To A Family

Add one `ModelEntry`; no frontend edit is required. The Models tab automatically
places it below its family and derives runtime, precision, fit, status, and
download actions from catalog fields.

Use `ignore_patterns` when a Hugging Face repository contains unused checkpoints.
Keep `best_for` short and decisive. Put concrete strengths and limitations in
`use_cases` using `good`, `weak`, or `avoid`.

## Verification

1. Run `python -m compileall -q app/backend`.
2. Run the server and inspect `GET /api/catalog`.
3. Confirm the family count, option label, decimal size, capabilities, cache
   state, and hardware fit.
4. Open `#/models`: verify default view shows the full catalog, the family opens,
   Download works, cached models show Use model, and Details explains tradeoffs.
5. Test at desktop and mobile widths and run `git diff --check`.

Never add a frontend-only model list, and never add a second family/engine list
in a consumer. `app/backend/catalog.py` is the single source of truth; a
consumer reads `Family.engine`, `Family.requirements`, the family's sectioning
fields or the manifest, and refuses by name when a value it needs is missing.

## The lane contract

Every `/api/catalog` row carries `lane_contract` beside `execution_contract`.
`execution_contract` is TTS presentation for the native Voice page;
`lane_contract` is the dispatch contract a scheduler reads (operation, honoured
parameters with their enforced bounds, artifact, terminal states, cancel
semantics, and a measured `runtime_seconds` where one exists). It is built from
the model's own limits, so a model that cannot clone never advertises a
reference parameter. `deadline_seconds` is the Hub's number, never repeated
here. `docs/lane-execution-contract.md` is the shape.

## Add A Transcription Model

Transcription models use the separate `TRANSCRIPTION_MODELS` registry in
`app/backend/transcription.py`; the frontend consumes that registry through
`GET /api/transcribe/availability`. Record the engine, decimal download size,
language scope, minimum unified-memory candidate, timing support, long-form
support, and whether it is an internal candidate. Do not invent a
`genstudio_candidate` record: that evidence appears only after a real model
audit exists.

New engines must dispatch through the existing `TranscriptionManager`, Hugging
Face cache, download manager, and global generation lock. Normalize their output
into the existing text/segments/SRT/VTT response rather than adding a second
transcription API. Whisper processor companions belong only to Whisper rows.

For internal candidates, verify selection and capability truth in the operator
UI at desktop and mobile widths. Appearance in Voice Studio is not fleet
qualification or GenStudio publication; those remain separate decisions.
