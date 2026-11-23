# GatewayKH ↔ Studio Hub contract inventory

Read-only inventory at the requested source revisions:

- GatewayKH: `~/Developer/gateway-kh/repository`, `736d7b05625fcc46ccdafdc7206319c9214f9b27`.
- StudioHub: `~/.codex/worktrees/handoff-completion/studio-hub-kh`, `b5506178cd0543a4734911c4fdb3c91d21ad271b`.

The GatewayKH checkout had a pre-existing modified `.DS_Store`; it was not touched. No live service, credential, database, backup, transfer, Downloads model library, or installed app was accessed.

## Wire contract observed in GatewayKH

### Discovery, auth, and identity

- A configured location is a provider `studio-hub-<controller_id>` with `base_url` on the provider definition and a stored `hub_token` (the dispatch/fleet credential); an optional `admin_token` is reserved for shared-voice administration (`gateway/providers/registry.py:55-105`).
- Model discovery calls `GET {base}/api/hub/model-packages` with `X-Hub-Token`, then best-effort `GET {base}/api/hub/catalog`; it intentionally does not use `/api/hub/capabilities` for model discovery (`gateway/providers/discovery.py:464-510`, `513-635`). Engine mapping is `image-mflux → image`, `voice-mlx → tts`, `subtitle-mlx → transcription` (`:457-461`).
- Music is not represented by the GatewayKH adapter/discovery lane: `_HUB_MODALITY` accepts only image and TTS, `Backend.generate` rejects other modalities, discovery maps no music engine, and the registered Studio Hub provider advertises only image/TTS/transcription (`gateway/adapters/studio_hub.py:38-41`, `522-530`; `gateway/providers/discovery.py:457-461`; `gateway/providers/registry.py:69-72`). StudioHub has an internal music worker mapping (`components/hub/standalone.py:1176-1178`), but that does not make Music a supported GatewayKH contract path.
- Fleet reachability/identity separately reads `GET /api/hub/fleet/overview`, `/api/hub/fleet/nodes`, `/api/hub/jobs`, `/api/hub/capabilities`, and `/api/hub/stats` with the dispatch credential (`gateway/fleet.py:663-703`). Stable site identity is taken from `capabilities.site_id`/`controller.site_id`, not the address (`gateway/fleet.py:684-775`).
- Auth is `X-Hub-Token`; local calls may use this Mac's operator token only when the provider id is the local provider. A remote missing credential stays absent (`gateway/hub.py:139-174`; `gateway/adapters/studio_hub.py:57-70`).
- GatewayKH collapses location models into the public `studio-hub` namespace while retaining `studio-hub-<controller_id>` for routing. A voice id carries that location namespace and pins voice work to its owner (`gateway/fleet.py:95-113`; `gateway/catalog.py:1040-1079`). StudioHub aggregate catalog rows retain the worker model id in `repo` and annotate the source worker (`components/hub/backend/monitor.py:849-871`).

### Submit, poll, artifact, and cancellation

- Image and TTS submit `POST /api/hub/jobs` with JSON:
  `{modality: "image"|"voice", model, items:[{prompt|text, params}], clientRequestId, label:"gateway-kh"}`. TTS adds exactly one top-level `voice_library_id` or `voice_reference_asset_id` (`gateway/adapters/studio_hub.py:348-415`). StudioHub accepts this route, preserves/replays `clientRequestId`, and returns `batch_id` (`components/hub/standalone.py:874-896`).
- GatewayKH checkpoints the returned `batch_id`, then polls `GET /api/hub/jobs/{batch_id}` until item state is one of `done/succeeded/complete/completed`; the adapter raises `BackendError(code="PROVIDER_FAILED")` for `error/failed/cancelled/cancelled_items/uncertain` (`gateway/adapters/studio_hub.py:73-121`). This is an adapter-level mapping risk, not proof of an ordinary durable failure: the worker checks checkpoint/dispatch state and can settle a post-dispatch ambiguous error with `recovery_state=RECOVERY_UNKNOWN` (`gateway/worker.py:187-205`), `_fail` forwards that state (`gateway/worker.py:56-80`), and `JobStore.fail` persists a failed row with `recovery_state`/`recovery_detail` while leaving `retryable=0` (`gateway/store.py:304-346`). The API exposes both `failure_class="provider_error"` and the recovery lifecycle fields (`gateway/api.py:205-212`, `313-317`), so this should be described as a failed row with a recovery hold, not an ordinary final provider failure. StudioHub provides the matching status route and its public item contract (`components/hub/standalone.py:898-911`; `components/hub/backend/broker.py:1664-1689`).
- Image/TTS artifact fetch is `GET /api/hub/jobs/{batch_id}/items/0/artifact`; media type comes from `Content-Type`, with PNG/WAV fallback. StudioHub exposes the same authenticated proxy route and emits `Content-Length`/`X-Content-SHA256` when known (`gateway/adapters/studio_hub.py:124-127`, `417-445`; `components/hub/standalone.py:943-994`).
- Transcription submits multipart `POST /api/hub/transcription/jobs` with repeated field names `files` and `item_ids`, plus `model` and optional `language`; it polls the transcription-specific status route and fetches `GET /api/hub/transcription/jobs/{batch_id}/items/0/artifact?format=srt` (`gateway/adapters/studio_hub.py:449-519`). StudioHub defines the matching multipart/status/artifact routes; its artifact is always SRT, so the extra query is tolerated/ignored (`components/hub/standalone.py:1003-1039`, `1131-1157`).
- GatewayKH's public cancel endpoints only call `store.cancel(job_id)` (`gateway/api.py:751-754`, `942-945`), and `Backend` exposes only `generate` (`gateway/adapters/base.py:107-124`). The StudioHub side supports `DELETE /api/hub/jobs/{batch_id}` and `DELETE /api/hub/transcription/jobs/{batch_id}` (`components/hub/standalone.py:996-1001`, `1164-1169`). **Gap: no GatewayKH adapter cancellation request reaches either Hub DELETE route; cancellation after local dispatch is therefore only a local request/uncertain-or-late-result policy, not remote stop proof.**

### Delivery ACK

- GatewayKH downloads the artifact and immediately returns a `BackendResult`; the adapter contains no ACK call (`gateway/adapters/studio_hub.py:417-445`, `503-519`).
- StudioHub exposes `POST /api/hub/jobs/{batch_id}/items/{item_index}/ack`, registered in the packaged app, and the handler requires `done`, forwards an idempotent `/api/generate/jobs/{worker_job}/ack`, persists `receipt_acked_at`, and marks the output index only after successful acknowledgement (`components/hub/standalone.py:1234-1237`; `components/hub/delivery.py:13-63`). The fleet allowlist explicitly includes this route (`components/hub/backend/auth.py:454-467`). **Gap: GatewayKH never sends the required delivery ACK after verified save/download.** This leaves Hub output receipt/index protection unacknowledged and is the clearest counterpart mismatch.

### Voice references and shared voices

- For a TTS voice, GatewayKH resolves a local registry voice to `reference_path`, transcript, and segments; it first probes `GET /studio/voice/api/voices` by `audio_sha256`, otherwise uploads multipart `POST /api/hub/voice-references` and passes the returned id as `voice_reference_asset_id` (`gateway/adapters/studio_hub.py:191-215`, `265-288`, `291-345`).
- StudioHub's one reference route supports local and external-origin clones and accepts `audio`, `transcript`, JSON `transcript_segments`, `language`, `ttl_seconds`, `source_asset_id`, and declared `sha256`; it returns asset id, digest, byte count, media type, expiry, transcript flag, and origin (`components/hub/standalone.py:1041-1106`). **Partial contract gap:** GatewayKH sends only audio, transcript, and segments (`gateway/adapters/studio_hub.py:308-321`), omitting language, source identity, declared checksum, and TTL, and reads only `asset_id` (`:329-345`). Thus the common reference route is reachable, but the Gateway-origin metadata/integrity/expiry fields are not propagated or checked. Missing aligned segments remain a caller-side risk for clone models.
- Shared-voice distribution is separate from per-job staging. GatewayKH calls `POST /api/hub/shared-voices?wait=true` with multipart fields and the controller's `admin_token`, never its dispatch token (`gateway/voice_import.py:245-299`, `444-491`; `gateway/fleet.py:143-165`). StudioHub's route requires name/language/gender/license/permission and validates segment shape; it creates or refreshes by content hash and fans out to joined nodes (`components/hub/standalone.py:753-833`; `components/hub/backend/shared_voices.py:105-148`, `268-309`). This path is compatible in endpoint, token scope, multipart naming, and `start_seconds`/`end_seconds` segment conversion (`gateway/voice_import.py:387-440`).

## Mismatch and compatibility summary

"Compatible" below means source-aligned endpoint/field shapes only, **not** a
passing GatewayKH-to-Hub roundtrip or release qualification. The three offline
tests below cover checkpointing, not the complete integration.

Subtitle also has its own delivery receipt endpoint,
`POST /api/hub/transcription/jobs/{batch_id}/items/{item_index}/ack`
(`components/hub/standalone.py:1158-1162`). GatewayKH's transcription adapter
does not call that endpoint either.

| Area | Result | Evidence |
|---|---|---|
| Discovery/model identity | Compatible for current paths; Gateway uses packages + catalog, and Hub serves both. | Gateway `discovery.py:513-635`; Hub `standalone.py:398-411` |
| Auth/token scope | Compatible when the correct token is configured: dispatch uses fleet token; shared voices use Hub/admin token. | Gateway `hub.py:139-174`, `voice_import.py:257-287`; Hub `auth.py:404-470` |
| Image/TTS submit/status/artifact | Compatible for one-item generation and current `batch_id`/item shape. | Gateway adapter `:348-445`; Hub `standalone.py:874-994` |
| Transcription submit/status/SRT | Compatible; Gateway's `?format=srt` is redundant but accepted by the route. | Gateway adapter `:449-519`; Hub `standalone.py:1003-1039`, `1131-1157` |
| Cancel | **Gap**: Hub DELETE exists; Gateway cancel does not call it. | Gateway `api.py:751-754`, `942-945`; Hub `standalone.py:996-1001`, `1164-1169` |
| Artifact ACK | **Gap**: Hub requires explicit ACK; Gateway never sends one. | Gateway adapter `:417-445`, `503-519`; Hub `delivery.py:13-63` |
| Voice-reference metadata | **Partial gap**: route is shared, but Gateway omits language/source asset/checksum/TTL and does not verify returned digest/bytes. | Gateway adapter `:265-345`; Hub `standalone.py:1041-1106` |
| Shared voice push | Compatible only with optional admin/Hub token and permission-ready entries; not the same as per-job reference staging. | Gateway `voice_import.py:245-299`, `444-491`; Hub `standalone.py:753-833` |
| Music modality | **Unsupported in GatewayKH's current Studio Hub lane**: adapter, discovery map, and provider modality tuple omit Music even though StudioHub has an internal Music worker mapping. | Gateway adapter `:38-41`, `522-530`; discovery `:457-461`; registry `:69-72`; Hub `standalone.py:1176-1178` |
| Uncertain state | **Adapter-level mapping risk with recovery hold downstream**: Hub publishes durable `state: "uncertain"` with recovery details; the adapter raises `PROVIDER_FAILED`, while checkpointed/post-dispatch cases settle as a failed Gateway row carrying `RECOVERY_UNKNOWN` and `recovery_required`, not as an ordinary retryable/final provider failure. Caller-specific UI interpretation remains out of scope. | Gateway adapter `:35-36`, `106-114`; worker `worker.py:56-80`, `187-205`; store `store.py:304-346`; API `api.py:205-212`, `313-317`; Hub `broker.py:903-978`, `1664-1689` |

## Existing offline fixture proof

Safe command run from the GatewayKH checkout (HTTPX mock transport/temp files only; no live Hub):

```sh
.venv/bin/python -m unittest tests.test_adapter_checkpoints
```

Result at the pinned GatewayKH commit: `Ran 3 tests ... OK`. This proves only that the adapter checkpoints a returned Hub `batch_id` before a mocked poll failure (image, TTS, transcription cases); it does **not** prove ACK, cancellation, successful artifact delivery, reference metadata propagation, or a real/disposable Hub roundtrip.

## Limitations

- No live or real-fleet generation was attempted. The Gateway fleet suite requires explicit `GATEWAY_ALLOW_LIVE_GENERATION=1` and is intentionally out of scope here.
- No GatewayKH fixture currently exercises the full adapter against a StudioHub `TestClient`; the adapter checkpoint fixture is a mocked transport, while StudioHub's own local fixtures separately cover submit/poll/cancel/artifact/ACK and voice reference routes.
- The saved Graphify graph is a 747-node older Voice-source graph and `graphify` is unavailable on this machine; it does not cover these current Gateway/Hub files. Findings above were verified by direct source inspection. No graph was created or changed.
- Billing, accounts, Story Studio public semantics, live enrollment, and provider/catalog business policy were not audited; this inventory only covers the GatewayKH ↔ StudioHub wire boundary requested.
