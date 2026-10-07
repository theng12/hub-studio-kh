# Per-Mac work policy and LLM priority

Owner-authorized implementation, 2026-10-07. Coordinator owns integration, verification and releases. Original implementation base was `d754437` (Hub 0.6.154 / `52e0f67` runtime). The separately qualified Gemma 4 26B-A4B QAT MLX 4-bit release is now integrated at Hub 0.6.155 / `1bb472a`; its exact receipts and 32 GiB admission evidence are preserved. Gateway base remains 0.2.143 / `7efedb6`. Release versions are Hub 0.6.156 and Gateway 0.2.144, dated 2026-10-08. Hub source verification is recorded below. Final Gateway gate and all commit/artifact/running identities belong to the coordinator's authoritative `WORK-POLICY-DELIVERY.md` receipt; this plan does not establish deployment.

## Intended behavior

- Settings exposes the local Mac's registered functions, each with an enable switch. Disabling prevents new work; existing work completes. No model files are removed.
- Optional **Prefer LLM jobs**: finish active work, serve eligible waiting Chat work before new non-Chat generation, then lend idle capacity to other enabled functions. No model-name special cases. Existing render priority and safety/admission gates remain.
- Global owner pause stays independent. Editing this policy never unpauses a Mac. Other Macs retain default scheduling.
- Story's Gateway requests must participate, not only Hub's internal Chat queue. Preserve synchronous OpenAI completion payloads, finish reasons and unknown-outcome handling.
- Bound pending requests and wait time. A cancelled or disconnected waiting request must not later begin generation. Do not reserve capacity for an invalid or unqualified model.
- Fix valid JPEG results being rejected by the native PNG-only check, preserve byte/hash checks and validate image decoding. Image-preview errors belong to their image view, not unrelated pages. Export correct file formats.

## Interfaces and ownership

- Backend worker: existing registry/operator controls, shared machine scheduler, peer Chat admission, capability publication; Gateway chat queue allowance/adapter only as needed. `GET /api/hub/work-policy` returns `machine_enabled`, `chat_priority`, dynamic local `studios` (`id`, `title`, `modality`, `enabled`). `POST` changes only `chat_priority`; existing per-Studio enable route persists flags. Default priority false.
- Native UI worker: Settings, authoritative state loading/saving, isolated save errors, disabled/disconnected states, no optimistic success, stale-refresh protection; Image preview banner lifecycle and correct export extension.
- Image worker: real-format verification and media-type propagation, image-specific regression checks. No weakened integrity checks.
- Coordinator: independent review, cross-repo contract proof, shared test lane, version/docs, commit/push/build/install, graphs and evidence. Workers do not commit or deploy.

## Proving checks

- [x] Finish a simulated TTS lease, dispatch waiting LLM requests consecutively, then resume TTS/image when no LLM is waiting.
- [x] Cover both Hub queue and actual Gateway direct-chat transport. Extra pending admission must be advertised, bounded and disabled for old Hubs/cloud providers.
- [x] Cancellation before arrival, while waiting, disconnect, timeout, overload, pause/model disable, unqualified model, memory refusal and unknown accepted outcome.
- [x] UI reads current flags, persists only requested change, retains manual pause, rejects stale responses and exposes save failures. Light/dark and narrow-width screenshots.
- [x] Real valid JPEG and PNG display; wrong hashes/sizes, missing receipts and malformed image data remain rejected. Correct exported suffix/media type.
- [x] Hub source/native verification and independent review, with fixture-repair replay evidence and bounded actual-view inspection recorded below.
- [ ] Final Gateway gate, commits/pushes, artifact hashes and installed behavior, as verified by the delivery receipt. Preserve the existing Gemma 4 26B-A4B QAT MLX 4-bit 32 GiB qualification; add no 24/16 GiB or new physical inference claim.
- [x] Integrate the separately published 0.6.155 qualification release at `1bb472a`; preserve its admission evidence.
- [ ] Build distributable DMGs and refresh code-only graphs after final verification.

## Execution record

Implementation is authorized by the owner's direct request; no additional design-approval pauses. Shared test execution is serialized. Existing isolated worktrees reused. Impeccable launcher lacked execute permission; incumbent source and existing PRODUCT/DESIGN read directly. Their historical deferred-feature statements are superseded by current runtime and owner instructions.


### Hub source verification — 2026-10-08

Hub discovery: 2,270 tests, 15 skips, two fixture failures, zero errors. The repaired combined affected replay passed 70 of 71; its remaining synthetic-library directory fixture was fixed and the single Chat case passed under full-discovery context. No production behavior changes repaired these gate failures. Logs: `hub-unittest-0156.log`, `hub-0156-fixture-replay.log`, `hub-chat-priority-fixture-final-replay.log`.

Native boundary/API passed, including nil-MIME JPEG verification. A three-reference private-error compile issue was repaired only in the view. The tail guarded unchanged API/source fixture hashes, then passed secret masking, lifecycle and optimized release compilation (`hub-native-0156.log`, `hub-native-tail-0156.log`). Independent review found no high-impact issue.

Rendering exited 0 and retained 24 Settings/Image/Video captures across controller/node roles, light/dark and 1440x1200/980x1000. The coordinator inspected seven across pages, widths and themes, confirming localized Image warnings; all-24 visual inspection is not claimed. The existing offscreen selected-sidebar black appearance in light mode is a capture limitation. Captures: `hub-work-policy-ui/fixture-build.1X6LVA/png/`; log: `hub-render-0156.log`.

All paths above are under `/build/Developer/_orchestra/codex-remediation-20261007/`. Delivery checkboxes remain open until the coordinator's `WORK-POLICY-DELIVERY.md` records commits, push, installer hashes and measured running identities. Keep the ordinary Gateway This Mac pause set. The 0.6.155 installed and SSD qualification records remain intact.
