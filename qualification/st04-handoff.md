# ST-04 handoff — the voice-clone contract the native/staging layer must satisfy

Audience: the coordinator who owns `native/**` and `packaging/stage_voice.py`.
Companion receipt: [`st04.md`](st04.md). The contract below is what
`implementation/st-04` actually implements and tests; the native client
committed as `1a010e5` already matches it except for the one item in §5.

Task card: ST-04 (Qwen3-TTS 0.6B/1.7B Base). Status: **PARTIAL** — backend and
real inference proven; cancellation/restart and physical hardware open.

## 1. Why clone references exist

Voice consistency comes from a reference clip, not from preset speakers. Two
origins must converge on one contract and produce identical worker behaviour:

- **Local** — the user picks a clip in the native app; the app uploads it.
- **GenStudio** — the controller pushes a clip through the location controller,
  carrying its own durable `source_asset_id` and metadata. No local file exists
  first, and the GenStudio validation is not weakened.

## 2. Stage a reference — the contract

### `POST /api/hub/voice-references`

`multipart/form-data`, owner-authenticated (`X-Hub-Token` or
`Authorization: Bearer <hub token>`).

| Field | Required | Meaning |
| --- | --- | --- |
| `audio` | yes | The reference clip. Extensions `.wav .mp3 .m4a .flac .ogg .opus .aac .webm`; at most 25,000,000 bytes. |
| `transcript` | optional | The exact words spoken in the clip. Optional **at the Hub**, required **by the Base model** (§4). |
| `transcript_segments` | optional | JSON array `[{"start_seconds","end_seconds","text"}, …]` (also accepts `start`/`end`). Only needed when the clip must be trimmed to a model's maximum duration. |
| `language` | optional | ISO code, stored with the asset. |
| `ttl_seconds` | optional | Default 86400, clamped to `[300, 172800]` (48 h). |
| `source_asset_id` | optional | **Omit** for local uploads. **Send** for GenStudio pushes; when present it keeps the existing required-source semantics. |
| `sha256` | optional | If supplied, a full 64-hex digest that must equal the uploaded bytes' SHA-256. Local uploads may omit it; the Hub computes and stores the digest either way. |

**201 response**

~~~json
{
  "asset_id": "24 lowercase hex characters",
  "sha256": "server-computed 64 hex digest",
  "bytes": 252084,
  "media_type": "audio/wav",
  "created_at": 1789486318.3,
  "expires_at": 1789572718.3,
  "transcript_supplied": true,
  "origin": "local"
}
~~~

`asset_id` is passed back **verbatim** as `voice_reference_asset_id` on the job.
`origin` is `"local"` or `"genstudio"`. The private source fingerprint and the
transcript are never published.

**Idempotency.** The same bytes + transcript + segments + language (+ source
identity) return the same `asset_id`; re-staging only extends the expiry.
Because the id is derived from the audio digest and transcript, **editing the
transcript produces a new id** — re-upload before generating.

**Errors** — status `400` (or `409` for the GET below) with
`{"detail": {"code": "<CODE>", "detail": "<human sentence>"}}`:

`REFERENCE_AUDIO_EMPTY` · `REFERENCE_AUDIO_TOO_LARGE` ·
`REFERENCE_AUDIO_TYPE_UNSUPPORTED` (includes a path-traversal filename) ·
`REFERENCE_AUDIO_CHECKSUM_REQUIRED` (malformed `sha256`) ·
`REFERENCE_AUDIO_CHECKSUM_MISMATCH` · `REFERENCE_AUDIO_UNREADABLE` ·
`REFERENCE_TRANSCRIPT_TOO_LARGE` · `REFERENCE_TRANSCRIPT_SEGMENTS_INVALID` ·
`SOURCE_ASSET_ID_REQUIRED` (only when `source_asset_id` is supplied but blank
or oversized — GenStudio semantics) · `VOICE_REFERENCE_ASSET_MISSING` /
`_EXPIRED` / `_INACCESSIBLE`.

### `GET /api/hub/voice-references/{asset_id}`

Owner-authenticated. `200` returns the same public shape minus `origin`;
`409` returns `{"detail": {"code", "detail"}}` when the asset is expired,
missing or checksum-failed. Use it to warn about expiry before a long job.

## 3. Use a reference in a job

`POST /api/hub/jobs`, `modality: "voice"`, with the staged id either

- nested as GenStudio sends it: `sharedParams.voice_reference_asset_id`, or
- **top level beside `model`/`items`**, which is how the shipped native
  envelope sends it.

The Hub folds the top-level form into the item parameters; an explicit
`sharedParams` value wins. The Broker then streams the staged clip to the Voice
worker's `POST /api/generate/txt2speech/reference` as multipart field `audio`,
with the transcript from the staged asset, and records the asset id on the item
for audit. `voice_reference_asset_id` and `voice_library_id` are mutually
exclusive (`VOICE_REFERENCE_CONFLICT`). Nothing else about `/api/hub/jobs`
changes.

## 4. What the Base model actually requires

- **The reference transcript is mandatory.** Qwen3-TTS Base conditions on it,
  and the internal Whisper check compares the supplied transcript against the
  clip. Missing it fails with `QWEN_REFERENCE_TRANSCRIPT_REQUIRED`; a wrong one
  fails closed with `QWEN_REFERENCE_TRANSCRIPT_MISMATCH`. The Hub stores the
  transcript as optional so the same asset contract serves both origins; the
  model is what enforces it.
- **The clip must be real speech**: at least ~3 s of usable speech, ≤ 15 s after
  preparation, decodable, non-silent. The worker derives the execution copy
  (speech-aware trim, 24 kHz mono, loudness-normalized) and refuses
  `REFERENCE_AUDIO_NO_SPEECH`, `REFERENCE_AUDIO_TOO_SHORT`,
  `REFERENCE_AUDIO_DECODE_*`, `REFERENCE_TRANSCRIPT_ALIGNMENT_REQUIRED`.
- **Catalog data the UI can rely on** (`GET` Voice `/api/catalog`, or the Hub's
  aggregated catalog): `reference_audio.declared` (family claims cloning),
  `reference_audio.supported`, `reference_audio.profile_audited`,
  `reference_audio.profile_source` (`"audit"`/`"unverified"`),
  `reference_audio.transcript` (`"required"` for both Base tiers),
  `minimum_duration_seconds`, `maximum_duration_seconds`, `sample_rate_hz`, and
  `execution_contract.quality_guardrails.required_local_models`
  (`["mlx-community/whisper-large-v3-turbo"]`).
- **Companion requirement**: Base cloning needs the shared Whisper Turbo package
  present and verified for its internal quality check. It resolves the narrow
  `WhisperTokenizer` path from the local cache; the packaged app must supply
  `openai/whisper-large-v3-turbo` offline (the ST-04 proof links it in as a
  shared companion). Missing it fails `QWEN_REFERENCE_VALIDATION_UNAVAILABLE` /
  `QWEN_OUTPUT_VALIDATION_UNAVAILABLE` rather than silently skipping the check.

## 5. What the coordinator must change

1. **Nothing is strictly required** for the native client to work: the Hub now
   accepts the top-level `voice_reference_asset_id` the client already sends.
   If you prefer an explicit envelope, move it into `sharedParams` — the lift
   then becomes a no-op.
2. Consider deriving `requiresReference` from `reference_audio.declared` /
   `reference_audio.transcript` instead of a repo-name `contains("Base")`, so a
   catalog change cannot drift from the UI.
3. **Keep shipping `components/voice/model-audits`.** `packaging/stage_voice.py`
   already copies it to `Resources/voice/model-audits`; the Base reference
   contract is audit-bound and refuses to prepare a clip without it
   (`REFERENCE_AUDIO_CONTRACT_UNAVAILABLE`). This was observed while building
   the real proof, which had to add the same directory.
4. Do not expose an 8 GB eligibility claim for the 1.7B tier, and do not present
   CustomVoice preset proof as Base clone proof.
5. Do not route the internal quality check as a Subtitle job. It is in-process,
   under the parent Voice job lease; a second queued Subtitle job would contend
   for the same GPU/memory admission.

## 6. Reference files and cleanup

Staged Hub references live in the Hub data directory under
`execution_assets/voice_references/<id>/` (owner-only, 0600) and expire. Worker
execution copies live under the Voice data directory in
`reference_cache/`. Neither is under the generated-output root
(`<data>/voice/output`), and the Hub's finalized-output index refuses a path
outside the managed output roots — so reference audio is never counted or
deleted as a generated output. Expiry-driven cleanup is separate from output
cleanup.

## 7. Staging / packaging checklist

- `Resources/voice/app/backend` — the adopted backend (this branch's code).
- `Resources/voice/model-audits` — the audit records (required, see §5.3).
- `Resources/voice_worker.py` — unchanged entry point; `--offline` and
  `--hub-token-file` still work as before.
- `Resources/runtimes/voice` — the audited runtime.
- The Hub side needs `components/hub/backend/execution_assets.py` and
  `components/hub/standalone.py` as staged by `stage_voice.py --hub`.
