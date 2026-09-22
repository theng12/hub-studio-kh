# GatewayKH — what Studio Hub needs from the controller

Studio Hub side is at commit `136c677` (branch `main`, pushed). The Hub exposes
everything below today; the controller side does not use all of it. The gap list
comes from `gatewaykh-contract-inventory.md`, which was read directly from the
GatewayKH source at `736d7b05625fcc46ccdafdc7206319c9214f9b27`.

## What I need from the owner before I can act

1. **GatewayKH checkout + authority.** The inventory read
   `/build/Developer/gateway-kh/repository`. To change it I need either
   that path writable, or you run the prompt below in the GatewayKH project. Tell
   me which.
2. **Scope.** All five items below, or a subset? My order of value is 1 → 2 → 3 →
   4 → 5.
3. **One Hub-side policy question.** The Hub's "discard an uncertain job" route is
   **operator-only**; the fleet/token allowlist deliberately excludes it. So a
   controller cannot release a node's machine. Do you want the controller to have
   that power? If yes, it is a small, explicit allowlist change on the Hub side and
   I should make it deliberately rather than by accident.

## The prompt to paste into the GatewayKH project

> **Goal:** align GatewayKH's Studio Hub adapter with the Hub contract as it now
> stands, so a controller round-trip is complete rather than one-way. Read the Hub
> source at `components/hub/standalone.py` and `components/hub/backend/auth.py` in
> the Studio Hub repository (`theng12/Studio-Hub-KH`, branch `main`, commit
> `136c677`) before changing anything; the routes below are copied from it.
>
> Do not rewrite published history. Do not touch live data or credentials. Add
> tests for each change, and report what changed and what you verified.
>
> 1. **Send the delivery ACK.** Today the adapter downloads the artifact and
>    returns without acknowledging it. After a verified save/download, call
>    `POST /api/hub/jobs/{batch_id}/items/{item_index}/ack` (image and voice) or
>    `POST /api/hub/transcription/jobs/{batch_id}/items/{item_index}/ack`
>    (transcription), with the Hub token, and treat the result as delivered only on
>    a 2xx. The Hub marks its output receipt/index only after a successful ACK, so
>    without this the Hub's copy protection stays unacknowledged.
> 2. **Make cancellation reach the Hub.** The public cancel endpoints currently
>    only call `store.cancel(job_id)`. Also issue `DELETE /api/hub/jobs/{batch_id}`
>    (or `DELETE /api/hub/transcription/jobs/{batch_id}`) and record what the Hub
>    answered. Cancellation after dispatch must not be reported as a remote stop
>    unless the Hub confirmed it.
> 3. **Add Music as a first-class modality.** Map engine `music-mlx` → `music` in
>    discovery, accept `modality: "music"` in `Backend.generate`, and register the
>    capability. Two models are available:
>    - `ACE-Step/Ace-Step1.5` — instrumental **and vocals**, up to 600 s on a
>      large-memory Mac. Item params: `duration_seconds`, `steps`,
>      `guidance_scale`, `shift`, `seed`, `instrumental` (bool), `lyrics`,
>      `vocal_language`.
>    - `stabilityai/stable-audio-3-optimized` — instrumental only, up to 380 s.
>      Item params: `duration_seconds`, `steps`, `guidance_scale`, `seed`.
>    Submit with `POST /api/hub/jobs` using `{"modality":"music","model":…,
>    "client_request_id":…,"items":[{"prompt":…,"params":{…}}]}`, poll
>    `GET /api/hub/jobs/{batch_id}`, fetch
>    `GET /api/hub/jobs/{batch_id}/items/{index}/artifact` (WAV; the Hub sends
>    `X-Content-SHA256` when known), then ACK and cancel as in items 1-2.
> 4. **Propagate voice-reference metadata.** When staging a reference at
>    `POST /api/hub/voice-references`, also send `language`, the declared `sha256`,
>    the source identity, and `ttl_seconds`, and verify the response's digest, byte
>    count, media type, expiry, transcript flag and origin instead of reading only
>    `asset_id`. Clone quality and integrity depend on the transcript segments
>    aligning with the audio.
> 5. **Do not collapse `uncertain` into an ordinary provider failure.** A Hub item
>    that settles `uncertain` after dispatch is a recovery hold — the worker
>    outcome is unknown — not proof the work failed. Map it distinctly so an
>    operator can reconcile or discard it.

## Exact Hub routes (for reference)

| Purpose | Route |
|---|---|
| Submit (image/voice/music) | `POST /api/hub/jobs` |
| Poll | `GET /api/hub/jobs/{batch_id}` |
| Artifact | `GET /api/hub/jobs/{batch_id}/items/{index}/artifact` |
| ACK | `POST /api/hub/jobs/{batch_id}/items/{index}/ack` |
| Cancel | `DELETE /api/hub/jobs/{batch_id}` |
| Discard an uncertain item | `POST /api/hub/jobs/{batch_id}/items/{index}/discard` |
| Transcription submit | `POST /api/hub/transcription/jobs` (multipart) |
| Transcription ACK | `POST /api/hub/transcription/jobs/{batch_id}/items/{index}/ack` |
| Transcription cancel | `DELETE /api/hub/transcription/jobs/{batch_id}` |
| Model packages | `GET /api/hub/model-packages` |
| Aggregate catalogue | `GET /api/hub/catalog` |
| Voice reference | `POST /api/hub/voice-references` (multipart) |
| Shared voices | `POST /api/hub/shared-voices?wait=true` (admin token) |

Auth is `X-Hub-Token`. Remote work uses the Fleet/dispatch credential; shared-voice
administration uses the admin token. The discard route is operator-only today.
