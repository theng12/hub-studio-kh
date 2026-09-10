# Native redesign API contracts

Inventory of the adopted Voice backend for the native redesign. This was read
at baseline commit `53f414a9d6792d94586fd3a091c7814edeed1e61`; the working tree
was clean before this report. The inventory is source-only: no worker was
launched, no dependency was installed, and no model was downloaded.

The backend is already the usable local contract for the first native slice.
Audio and Transcription should be separate workspaces, while Models and
Settings own shared readiness and management state. The native client should
use the worker's loopback base URL and `/api/*` routes; it should not infer
readiness from model declarations alone.

### Local versus remote access

The adopted middleware has no login redirect. Loopback requests (`127.0.0.1`,
`::1`, or `localhost`) are exempt from the fleet-token check. Remote requests
must present the generated token in `X-Studio-Token`, `X-Hub-Token`, a Bearer
`Authorization` header, or the strict cookie `kh_studio_token`; query-string
tokens are not supported. Missing remote credentials return JSON 401. A
browser write with an `Origin` different from the request host returns JSON 403
before route handling. These rules are defined at
[`fleet_auth.py:52`](/build/Projects/studiohub-standalone/components/voice/app/backend/fleet_auth.py:52)
and the capability manifest states the same auth modes at
[`fleet_auth.py:100`](/build/Projects/studiohub-standalone/components/voice/app/backend/fleet_auth.py:100).
The CORS middleware permits headers but does not override the write guard
([`main.py:168`](/build/Projects/studiohub-standalone/components/voice/app/backend/main.py:168)).
The native shell’s local worker therefore needs no token for its API calls;
remote Studio Hub/GenStudio calls remain a secondary, token-authenticated
path and should not be mixed into the local job state.

## Contracts needed by the first native slice

### Audio workspace: local Qwen TTS

The generation availability route is the source for runtime flags and the
Qwen control rosters:

* `GET /api/generate/availability` returns `available`, `qwen3_available`,
  `device`, `qwen3_preset_speakers`, and
  `qwen3_voice_design_examples` (along with other engine-specific flags).
  The adopted frontend consumes these fields at
  [`app.js:2569`](/build/Projects/studiohub-standalone/components/voice/app/frontend/app.js:2569).
* The Qwen CustomVoice roster is nine exact IDs: `Ryan`, `Aiden`, `Serena`,
  `Vivian`, `Uncle_Fu`, `Dylan`, `Eric`, `Ono_Anna`, and `Sohee`. The source
  explains that these come from the model `spk_id` map and that adding phantom
  IDs causes engine rejection at
  [`generation.py:767`](/build/Projects/studiohub-standalone/components/voice/app/backend/generation.py:767).

Submit `POST /api/generate/txt2speech` with JSON. The request model accepts
`repo` and non-empty `text` (maximum 40,000 characters), plus common controls
`language`, `speed`, `temperature`, `seed`, and optional `client_request_id`.
The exact adopted request model is at
[`main.py:297`](/build/Projects/studiohub-standalone/components/voice/app/backend/main.py:297).
For the Qwen subset, send only controls for the selected mode:

| Qwen mode | Required/usable fields | Native control rule |
| --- | --- | --- |
| CustomVoice | `repo`, `text`, `preset_speaker`; optional `instruct`, plus common controls | Populate speaker choices from `qwen3_preset_speakers`; do not hard-code a second roster. |
| VoiceDesign | `repo`, `text`, `voice_design_prompt`; optional `instruct`, plus common controls | Populate examples from `qwen3_voice_design_examples`; the prompt is the user’s voice description. |
| Base/clone | `repo`, `text`, `voice_library_id`, and the saved reference transcript where required | Only show local voices that still exist; a missing voice ID must be treated as unavailable. |

The adopted web client’s mode-to-field mapping is explicit at
[`app.js:3038`](/build/Projects/studiohub-standalone/components/voice/app/frontend/app.js:3038).
The backend rejects an unknown repo, a non-TTS model, empty text, an uncached
model, and cloud-style `provider:` repos before creating a job. The route
returns `{"job": <serialized job>}`; these checks are at
[`main.py:1213`](/build/Projects/studiohub-standalone/components/voice/app/backend/main.py:1213).

The native request should send a stable `client_request_id` when a submit may
be retried after a lost response. The field is accepted by the backend for
idempotent comparison, although the legacy frontend currently omits it.
Do not send every model-specific sampling field by default: the legacy client
sends a large all-engine payload at
[`app.js:3063`](/build/Projects/studiohub-standalone/components/voice/app/frontend/app.js:3063),
but the native Qwen surface only needs the mode fields above and the common
controls.

Generation job responses use a stable job ID and state. `GET
/api/generate/jobs` returns `{ "jobs": [...] }`; `GET
/api/generate/jobs/{job_id}` returns `{ "job": ... }`. The serialized job
contains `id`, `mode`, `state`, `progress` (0..1), `chunk_index`,
`chunk_total`, public `params`, `output_path`, `output_url` when done,
`resolved_seed`, error/cancellation/finish timestamps, duration, resource and
quality fields, and final audio metadata such as format, byte size, SHA-256,
duration, sample rate, and channels. The serializer is at
[`generation.py:1727`](/build/Projects/studiohub-standalone/components/voice/app/backend/generation.py:1727).
The native state should retain the job ID and safe display metadata; treat
`output_path` as private filesystem data and use `output_url` only after
`state == "done"`.

Use `GET /api/generate/stream` for `snapshot` SSE events. Each event data is
`{ "jobs": [...] }`; the adopted frontend treats `queued`, `running`, and
`cancel_requested` as active and terminal states as `done`, `error`,
`cancelled`, or `uncertain` at
[`app.js:2917`](/build/Projects/studiohub-standalone/components/voice/app/frontend/app.js:2917).
Cancel an active/queued job with `DELETE /api/generate/jobs/{job_id}`. A
finished job’s audio is streamed by `GET /api/generate/jobs/{job_id}/audio`;
it returns 425 until the output is ready. `DELETE /api/generate/jobs` clears
terminal history while keeping files; `DELETE
/api/generate/history/{job_id}` removes finished history and its audio. The
routes are grouped at
[`main.py:1371`](/build/Projects/studiohub-standalone/components/voice/app/backend/main.py:1371).

The worker shares one Metal/unified-memory lock between generation and
transcription. The native Audio workspace should show the busy state and avoid
starting an overlapping transcription; queued TTS jobs remain supported.
`/api/health` reports a safe `busy` boolean, but also contains filesystem paths
that should not be shown in the UI. See
[`main.py:415`](/build/Projects/studiohub-standalone/components/voice/app/backend/main.py:415).

### Transcription workspace: Whisper upload and result

`GET /api/transcribe/availability` returns:

```json
{
  "available": true,
  "mlx_audio": true,
  "device": "...",
  "default_model": "owner/repo",
  "models": [
    {
      "repo": "owner/repo",
      "label": "...",
      "size_gb": 1.0,
      "engine": "...",
      "min_unified_memory_gb": 8,
      "languages": ["en"],
      "supports_segment_timestamps": true,
      "supports_word_timestamps": false,
      "supports_long_form": true,
      "recommended": true,
      "cached": true,
      "cache": {}
    }
  ],
  "media": {}
}
```

The complete field construction, including optional candidate metadata, is at
[`transcription.py:227`](/build/Projects/studiohub-standalone/components/voice/app/backend/transcription.py:227).
The native Transcription state only needs the availability flag, selected
model, language, word-timestamp capability, and the model list. Enable the
word-timestamps control only when the selected model says
`supports_word_timestamps == true`.

Submit `POST /api/transcribe` as multipart form data. Provide exactly one
audio source:

* `file`: an uploaded audio file; or
* `job_id`: a completed/current local TTS job whose output is already on disk.

Optional fields are `model`, `language`, `word_timestamps`, and `activity_id`.
The route reads an uploaded file into a temporary file and removes it in a
`finally` block. Its input contract and cleanup are at
[`main.py:1541`](/build/Projects/studiohub-standalone/components/voice/app/backend/main.py:1541).
The adopted frontend uses `file`, optional `model`/`language`, and only sends
`word_timestamps=true` after the capability check at
[`app.js:3646`](/build/Projects/studiohub-standalone/components/voice/app/frontend/app.js:3646).

Success returns:

```json
{
  "text": "full transcript",
  "language": "en",
  "duration": 12.345,
  "model": "owner/repo",
  "segments": [{"start": 0.0, "end": 1.2, "text": "...", "words": []}],
  "srt": "1\n00:00:00,000 --> ...",
  "vtt": "WEBVTT\n\n...",
  "elapsed_seconds": 2.3,
  "resource_telemetry": {}
}
```

The canonical result assembly is at
[`transcription.py:920`](/build/Projects/studiohub-standalone/components/voice/app/backend/transcription.py:920).
The native workspace can keep one result object and offer Text, SRT, and VTT
views/export. A 400 means invalid input or unsupported options, 404 means the
referenced TTS job/file is missing, and 409 means the chosen model is not
cached/ready; these mappings are at
[`main.py:1594`](/build/Projects/studiohub-standalone/components/voice/app/backend/main.py:1594).

### Local voices and cloning references

`GET /api/voices` returns `{ "voices": [...] }`. Each voice is the serialized
metadata object plus `transcript`, `audio_url` (`/api/voices/{id}/audio`), and
`audio_filename`; the source is
[`main.py:1001`](/build/Projects/studiohub-standalone/components/voice/app/backend/main.py:1001)
and [`voices.py:114`](/build/Projects/studiohub-standalone/components/voice/app/backend/voices.py:114).
`GET /api/voices/{voice_id}/audio` streams the stored reference clip.

To add a clone reference, `POST /api/voices` is multipart with required
`audio`, `name`, `language`, `gender`, and `license`; optional `notes`,
`source_url`, `transcript`, and `permission_acknowledged` default to empty or
false. The adopted client sends exactly these fields at
[`app.js:2091`](/build/Projects/studiohub-standalone/components/voice/app/frontend/app.js:2091).
The minimum native Audio slice can list and select existing voices; upload and
metadata editing can remain in the central voice management surface. `PATCH
/api/voices/{voice_id}` accepts optional metadata fields and an empty string
clears `notes`, `source_url`, or `transcript`; `DELETE` removes the local clip.
Those safety-sensitive operations are defined at
[`main.py:1132`](/build/Projects/studiohub-standalone/components/voice/app/backend/main.py:1132).
F5/Qwen clone requests must not silently use a voice without its required
transcript; show the missing-transcript state before submit.

## Models center: readiness, downloads, and removal

`GET /api/catalog` returns `{ "families": ..., "models": [...] }`. Each model
contains catalog identity (`repo`, `label`, `family`, `family_label`), size
and memory requirements, capabilities, languages, execution contract,
reference-audio support, Apple-optimization/quantization metadata, use cases,
and `fit`. The route augments it with:

* `cache`: a snapshot whose `state` must be `cached` for a complete model;
  companion models can downgrade the visible state to `partial`;
* runtime fields `loaded`, `runtime_ready`, memory requirements, and
  `memory_eligible`;
* `available`: true only when runtime-ready **and** cache state is `cached`;
* `active_download`: a serialized download or null; and
* `kind: "local"`.

These are assembled at
[`main.py:626`](/build/Projects/studiohub-standalone/components/voice/app/backend/main.py:626).
Filter the Audio workspace to catalog entries whose `capabilities` includes
`tts`; keep Whisper/STT entries in the Transcription/Models view using their
separate availability response. A model declaration, `apple_optimized`, or
`cached` flag by itself is not enough to enable Generate.

Downloads:

| Method/path | Request/response | Safety and UI use |
| --- | --- | --- |
| `GET /api/downloads` | `{ "jobs": [download] }` | Initial management snapshot. |
| `POST /api/downloads` | JSON `{repo, token?, revision?}`; returns `{job}` or `{job:null, already_cached:true, cache}` | `repo` must be `owner/name`; `revision`, when supplied, must be a 40–64 hex immutable commit. A cached matching revision does not create history. Never put a raw saved token in Swift state. |
| `GET /api/downloads/stream` | `snapshot` SSE with `{ "jobs": [...] }` every second | Preferred progress source. |
| `DELETE /api/downloads/{job_id}` | `{ "job": ... }` | Cancels an active/queued download; terminal jobs return 404. |
| `DELETE /api/downloads` | `{ "cleared": count }` | Clears finished download history only. |

Download serialization has `id`, `repo`, requested revision, `state`, error,
verified/partial/unverified/observed/total byte counters, `percent`,
`speed_bps`, `eta_seconds`, and timestamps at
[`downloads.py:177`](/build/Projects/studiohub-standalone/components/voice/app/backend/downloads.py:177).
`percent == 100` is reserved for terminal success; do not display partial
cache bytes as a completed model.

`GET /api/model-storage` returns a dependency-aware inventory with
`schema_version`, summary counts, and groups of items. Each item has `repo`,
label/family/type/role/detail, `used_by`, `present`, cache totals, and a
`removal` object with `allowed` and `blocked_reason`. `DELETE
/api/model-storage/{repo}` is the only package-removal action for the native
Models center. It returns 409 while downloads, generation, or transcription
are active; it also refuses loaded models and dependency packages still used
by installed models. The route enforces these checks at
[`main.py:682`](/build/Projects/studiohub-standalone/components/voice/app/backend/main.py:682),
with path containment/dependency checks in
[`model_storage.py:314`](/build/Projects/studiohub-standalone/components/voice/app/backend/model_storage.py:314).
The UI can offer stale-incomplete cleanup via `DELETE
/api/cache-maintenance/stale-incomplete/{repo}` only when the inventory says
the package is partial; it should not implement its own filesystem deletion.

The smallest native Models view can therefore use one Swift model item:
`repo`, `title/label`, `function` (`tts` or `transcription`), `cached/cache`,
`available`, `runtimeReady`, `memoryEligible`, `activeDownload`, and the
server-provided block reason. Keep download jobs separate from catalog items
so one repo can report an active transfer without losing catalog metadata.

## Settings and runtime status

`GET /api/settings` returns only `{hf_token_set, hf_token_masked}`. `POST
/api/settings` accepts `{hf_token?: string}` and returns the same safe shape.
The backend stores the token atomically with owner-only `0600` permissions;
the public serializer never returns the raw token at
[`settings.py:91`](/build/Projects/studiohub-standalone/components/voice/app/backend/settings.py:91).
`POST /api/settings/test-hf-token` accepts the same token body and returns
`{ok, name, type, orgs}` or 400; it never returns the token. Native Swift
state should contain only `tokenSet` and `maskedToken`, and the text field
should be transient.

`GET /api/health` is appropriate for a compact native status indicator. Whitelist
`ok`, version/release identity, `busy`, loaded model IDs, and memory/process
status; do not render `hf_home` or `hub_dir` by default because they are raw
local paths. `GET /api/system` returns the Apple Silicon/unified-memory
hardware snapshot used for fit messaging at
[`main.py:565`](/build/Projects/studiohub-standalone/components/voice/app/backend/main.py:565).

Memory management can be a small Settings section: `GET`/`PUT
/api/memory-policy` with body `{ "mode": string }`, followed by `POST
/api/memory/release`. The worker already owns the policy and release behavior;
the native client should display its returned status rather than infer memory
from process metrics. Output retention is separately exposed by
`GET`/`PUT /api/storage-policy` and cleanup, but it is better deferred until
the first Audio/Transcription slice is stable.

## Native state and boundaries

The minimal tested surface can be represented with four independent state
groups:

1. **Audio:** catalog-filtered TTS models, selected Qwen mode/controls, local
   voices, current generation jobs, and the selected completed audio URL.
2. **Transcription:** Whisper availability/models, selected model/language,
   upload or source job ID, word-timestamp capability, running state, and one
   text/SRT/VTT result.
3. **Models:** merged TTS catalog + STT availability items, cache/readiness,
   download jobs, and dependency-aware storage inventory.
4. **Settings/status:** safe token state, health busy/readiness, hardware fit,
   and optional memory policy.

Keep the engine contract in the worker. The native layer should make requests,
surface HTTP errors, consume SSE snapshots, and gate controls from server
capabilities. It should not download implicitly when Generate or Transcribe
returns 409, should not start concurrent Metal work while the shared lock is
busy, and should not derive local paths or delete cache directories itself.
The adopted frontend confirms the intended request order: fetch availability
and catalog first, then submit only after the selected model is ready. This
gives a small native Audio + Transcription implementation without losing the
currently tested Qwen and Whisper flows.

## Deferred or secondary routes

The backend also exposes fleet/remote activity, private reference upload,
seed-voice downloads, auto-update, storage pruning, import scan/link/move, and
external reveal routes. They are real APIs but are not needed for the first
local creative-app flow. Keep them out of the minimal native state until a
local use case requires them; in particular, no fleet setup is required for
local job creation.
