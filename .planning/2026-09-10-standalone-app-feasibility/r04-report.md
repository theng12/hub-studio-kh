# R04 — node-owned peer admission: local verification report

Date: 2026-09-21. Owner: DeepSeek (autonomous takeover).
Base commit: `9a4570da57cb9109cd7da1d06d2197f923a71139`.
Working tree: main checkout, branch `implementation/voice-runtime`.

[MEASURED] All claims below are from commands run on this computer. Self-review
only; no independent review has been performed. No remote integration, release
qualification or hardware qualification is claimed.

## Defect and root cause

[MEASURED] The fleet peer proxy in `components/hub/standalone.py` (`studio_proxy`)
forwarded every `/studio/<modality>/...` request straight to the local worker
without consulting any node-owned admission decision. A controller talking to
this Mac could therefore start generation while the model was paused, the Studio
or machine was disabled, output storage was refusing new output, or the physical
machine was already reserved for other heavy work.

Baseline reproduction on `9a4570d`
(`qualification/evidence/local-readiness-2026-09-21/r04_admission_probe.py`,
log `.build/deepseek-r04/r04-probe-baseline.log`):

| Case | Peer POST | Worker generation requests |
| --- | --- | --- |
| model paused (`broker.model_block_state` confirmed) | 200 | 1 |
| Studio disabled via authenticated operator route | 200 | 1 |
| output admission `OUTPUT_STORAGE_BLOCKED` | 200 | 1 |
| local machine reserved via `acquire_external_machine` | 200 | 1 |

## Repair

[MEASURED] `studio_proxy` now gates work-submitting POSTs only
(`/api/generate/...` and `/api/transcribe`) through `_peer_admission_refusal`
before the worker is contacted:

1. `MACHINE_DISABLED` / `STUDIO_DISABLED` from the node's own registry flags.
2. `MODEL_PAUSED` from `broker.model_block_state` (the model is read from the
   request: JSON `repo` for generation, multipart `model` for Subtitle).
3. `MACHINE_BUSY` from `broker.busy_machines()`.
4. `OUTPUT_STORAGE_BLOCKED` from `output_storage.admission` for the worker.
5. Packaged `_submission_readiness` (files/runtime/OS/chip) where the Hub owns a
   model library to verify against; otherwise the worker remains the authority.

Refusals are machine-readable JSON (`code`, `detail`): 409 for
disabled/paused/busy, 507 for storage, 503 for readiness. GET status, DELETE
cancel, artifact and acknowledgement paths are deliberately not gated, so work
this Mac already accepted stays reachable while new work is paused.

[MEASURED] Accepted asynchronous work now holds a node-owned machine lease
(`broker.acquire_external_machine` with a `peer:` owner) from before the worker
request until the job is observed terminal through the same proxy (a terminal
status response or a raw artifact). A synchronous route (Subtitle
`/api/transcribe`) or a refusal releases immediately, before the call returns. A
6-hour safety TTL, pruned on every proxied request and by a 60-second background
sweep, keeps a vanished controller from reserving the Mac forever. Releasing the
lease on the asynchronous POST's return was deliberately not done.

## Verification

| Check | Result |
| --- | --- |
| R04 probe after repair | `409 / 409 / 507 / 409`, **0** worker requests each |
| `tests/test_studio_proxy.py` | **14 passed**, 2 warnings, 6 subtests (1.68s) |
| New two-Hub stale-capability test in isolation | **1 passed** (18.52s) |
| `tests/test_standalone_connections.py` full | **32 passed, 1 failed** (326.91s) |
| `tests/hub_regression/cases` | **108 passed**, 2 upstream warnings (1.64s) |
| `tests/test_hub_jobs.py` + `test_hub_dispatch_proof_gate.py` + `test_hub_standalone.py` | **18 passed** (15.57s) |
| `tests/test_hub_statistics.py` + `test_paused_model_recovery.py` + `test_studio_proxy.py` | **28 passed** (3.49s) |
| Original five handoff probes | **5 tests OK**, exit 0 |

The single connections failure is pre-existing and environmental:
`test_the_overview_shows_every_machine_in_one_row` asserts `chip` is populated,
but `resources.apple_chip_name()` runs `sysctl` by name from PATH, and this
harness's PATH omits `/usr/sbin`. The same test **passes** with
`PATH="/usr/sbin:$PATH"`. It also failed identically before any R04 edit. This is
recorded as a separate confirmed defect (see below), not attributed to R04.

Permutation note: the first `test_hub_jobs.py` run after the repair failed with a
`CancelledError` during lifespan shutdown because the new
`_peer_lease_loop()` did not swallow cancellation (unlike
`job_storage.budget_loop`). Fixed by mirroring that loop; the job/dispatch suites
then passed 18/18.

## New permanent coverage

- Four node-owned negatives at the real FastAPI peer route (paused, Studio
  disabled, machine disabled, storage blocked, machine reserved) asserting an
  explicit refusal **and** zero worker requests.
- An accepted asynchronous job holds the machine lease until a terminal poll
  releases it.
- An abandoned lease expires (safety TTL).
- A two-real-Hub stale-capability test: the controller has cached the node as
  reachable with its Image studio, the node's owner then disables the studio, and
  the node — not the controller's stale snapshot — refuses the dispatch with no
  worker submission.

## Preserved contracts

- The proxy still forwards to the same worker surface; credentials are still
  re-signed with the worker's own token and the caller's credential never
  reaches the worker (`test_a_peer_path_reaches_the_local_worker`).
- Existing happy-path dispatch, artifact retrieval and cancellation tests pass
  unchanged.
- Image/Music/Voice/Subtitle durable behavior is untouched outside the peer
  proxy.

## Limits and remaining gates

- The four two-Hub cases were proven at the proxy/HTTP boundary plus one
  controller-stale case at two processes; a two-Hub proof of each of the four is
  not claimed.
- Node-owned state is in-process; a Hub restart forgets an in-flight peer lease
  (as it already did for other external leases). The TTL is a safety net, not
  durable recovery.
- Independent review is **pending**. R04 is locally verified and self-reviewed,
  not release-qualified.
