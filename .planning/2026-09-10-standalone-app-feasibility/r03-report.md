# R03 — Voice receipt and payload integrity report

Base `b5506178cd0543a4734911c4fdb3c91d21ad271b`; branch
`codex/local-readiness-gatewaykh`; worktree
`~/.codex/worktrees/handoff-completion/studio-hub-kh`.

## Result and changed files

R03 is source/test complete and frozen for coordinator review. The Hub now validates
Voice acceptance, live poll, shared success, recovery and explicit reconciliation
receipts before recording success. Malformed successful receipts persist
`uncertain`, retain the original Studio and any previously accepted job ID, and cannot enter the
remote-regeneration branch. A successful terminal receipt must name the exact worker artifact route and
a non-empty worker output path. Valid terminal output whose artifact download,
parse, byte count or hash verification fails remains intentionally `done` with
`artifact_metadata_error`. PCM WAV metadata is emitted only when all declared frame
bytes can be read.

Owned production/tests:

- `components/hub/backend/broker.py`
- `components/hub/backend/artifact_metadata.py`
- `tests/hub_regression/cases/test_voice_durability.py`
- `tests/test_hub_standalone.py` (the exact existing artifact test file reported
  before scope expansion)

Coordinator-approved test-scope expansions:

- `tests/hub_regression/cases/test_execution_assets.py`
- `tests/hub_regression/cases/test_terminal_progress.py`
- `tests/test_standalone_connections.py`
- `components/voice/app/tests/test_native_executor.py` (only the existing durable
  acceptance/idempotency test)

The fixture updates add real public Voice receipt fields without removing or
weakening existing assertions. `GenerationJob.serialize()` publishes `state`,
public `params` (including `client_request_id`), and terminal artifact fields. The
fleet stub therefore captures its submitted multipart `request_json` and echoes
those params in the status receipt.

## Contract decisions

- Receipt state is type-checked before membership testing, so a list/dict state is
  a malformed receipt rather than an unhandled `TypeError`. A focused remote test
  proves one POST, one created worker job, retained accepted job/studio identity,
  persisted uncertainty and no retry/regeneration.
- The Hub compares accepted job ID, stable request ID, and any captured model/runtime
  revisions. It deliberately does not compare worker `machine_id`/`worker_id` with
  the Hub registry's routing `machine`/fallback worker ID: these are different
  namespaces, and the public Voice job does not promise those fields.
- Acceptance requires a known state. Live completion additionally requires the
  artifact reference; the shared success boundary repeats the same validation.
- A malformed HTTP 2xx acceptance without a usable job ID is conservatively fenced
  on its original Studio. It is not safe to dispatch that stable request ID to a
  different worker; receipt-less work requires operator reconciliation. A cancel
  racing the malformed acceptance is likewise not falsely reported cancelled.
- Invalid JSON and malformed recovery receipts use the same typed receipt failure.
  True remote transport failures retain the existing bounded regeneration policy.
- `OverflowError`, `TypeError` and `ValueError` from malformed worker byte/hash
  metadata join the existing HTTP-error fallback, preserving generated output as
  terminal-but-unverified rather than requesting new generation. Expected bytes
  must be a positive integer (not bool/fractional) and match exactly.
- No Image/Music, valid-receipt cancellation, privacy, billing or legacy wire-field
  behavior was changed.

## RED / GREEN evidence

Baseline before new tests:

```text
PYTHONPATH=.build/test-deps .build/voice-runtime/bin/python3 -B -m pytest -q \
  tests/hub_regression/cases/test_voice_durability.py tests/test_hub_standalone.py
28 passed, 2 upstream warnings
```

Focused permanent tests before the repair (new malformed live/reconcile receipts,
artifact verification fallback, and truncated WAV cases):

```text
PYTHONPATH=.build/test-deps .build/voice-runtime/bin/python3 -B -m pytest -q \
  tests/hub_regression/cases/test_voice_durability.py tests/test_hub_standalone.py \
  -k 'live_voice_poll_rejects_malformed_receipt or voice_reconcile_rejects_malformed_terminal_receipt or malformed_voice_acceptance_repost or voice_artifact_verification_failure or wav_metadata_rejects_truncated_declared_frames'
9 failed, 7 passed, 2 upstream warnings
```

The initial `malformed_voice_acceptance_repost` expectation modeled replay to the
same worker. Independent review superseded it with the conservative two-worker
contract below: a receipt-less HTTP 2xx is fenced, so another worker receives no
POST. Worker-local exact-body replay remains separately proven in the real Voice
manager test.

Two additional edge reproductions before their fixes:

```text
PYTHONPATH=.build/test-deps .build/voice-runtime/bin/python3 -B -m pytest -q \
  tests/hub_regression/cases/test_voice_durability.py \
  -k 'non-scalar-state or infinite_voice_artifact_byte_count'
2 failed, 34 deselected, 2 upstream warnings
```

Their focused GREEN rerun was `2 passed, 34 deselected, 2 upstream warnings`; the
original focused set was `16 passed, 28 deselected, 2 upstream warnings`.

Independent-review RED follow-up:

```text
PYTHONPATH=.build/test-deps .build/voice-runtime/bin/python3 -B -m pytest -q \
  tests/hub_regression/cases/test_voice_durability.py \
  -k 'invalid_json_voice_poll or transport_then_malformed or fractional-byte-count'
4 failed, 37 deselected, 2 upstream warnings

PYTHONPATH=.build/test-deps .build/voice-runtime/bin/python3 -B -m pytest -q \
  tests/hub_regression/cases/test_voice_durability.py \
  -k 'malformed_voice_acceptance_is_fenced or cancel_racing_malformed_voice_acceptance'
5 failed, 37 deselected, 2 upstream warnings
```

Final focused integrity GREEN:

```text
PYTHONPATH=.build/test-deps .build/voice-runtime/bin/python3 -B -m pytest -q \
  tests/hub_regression/cases/test_voice_durability.py \
  -k 'invalid_json_voice_poll or transport_then_malformed or fractional-byte-count or malformed_voice_acceptance_is_fenced or cancel_racing_malformed_voice_acceptance'
9 passed, 33 deselected, 2 upstream warnings in 0.24s
```

Final regression checks:

```text
PYTHONPATH=.build/test-deps .build/voice-runtime/bin/python3 -B -m pytest -q \
  tests/hub_regression/cases
108 passed, 2 upstream warnings in 1.71s

PYTHONPATH=.build/test-deps .build/voice-runtime/bin/python3 -B -m pytest -q \
  tests/test_hub_standalone.py
10 passed, 2 upstream warnings in 0.73s

PYTHONPATH=.build/test-deps .build/voice-runtime/bin/python3 -B -m pytest -q \
  tests/test_hub_delivery.py tests/test_hub_voice_references.py \
  tests/test_shared_voices.py tests/test_voice_standalone.py
31 passed in 4.82s

PYTHONPATH=.build/test-deps .build/voice-runtime/bin/python3 -B -m pytest -q \
  tests/test_standalone_connections.py::FleetVoiceDispatchTests::test_a_voice_clone_submitted_to_the_controller_runs_on_the_node
1 passed in 23.77s

cd components/voice
PYTHONPATH=../../.build/test-deps ../../.build/voice-runtime/bin/python3 -B -m pytest -q \
  app/tests/test_generation_artifact_contract.py app/tests/test_fleet_activity.py \
  app/tests/test_fleet_auth.py app/tests/test_fleet_job_details.py \
  app/tests/test_reference_audio_contract.py \
  app/tests/test_native_executor.py::test_queued_job_is_durable_before_worker_start_and_reloads_as_uncertain
79 passed, 2 upstream warnings in 3.10s

PYTHONPATH=../../.build/test-deps ../../.build/voice-runtime/bin/python3 -B -m pytest -q \
  app/tests/test_native_executor.py::test_queued_job_is_durable_before_worker_start_and_reloads_as_uncertain
1 passed in 0.26s
```

The durable real-Voice test now asserts that exact-body replay returns the restored
job, while changing `text` under the same `client_request_id` raises the existing
`different request` conflict and leaves one job. `py_compile` and `git diff --check`
over all eight R03 source/test files are clean.

## Limitations

- Graphify CLI was unavailable. The saved 747-node Voice-only graph is stale, so
  all caller and contract findings above were verified directly in current source.
- Evidence is model-free and local. It does not certify physical hardware,
  GatewayKH billing, live fleet enrollment, signing/notarization, or deployment.
- The coordinator owns the independent review, combined full suite and packaged
  application gate. No commit, push, deployment, protected model-library access,
  paid call or subagent was used.
