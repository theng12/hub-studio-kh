# R03 — Read-only preflight

Source base: `0ec3b3b09c9642b670ec0a092f4223ea4ffe0e24`; broker/Voice/parser
unchanged during preflight. Reviewer: `music_recovery_read`; root verified callers.
No R03 implementation has started. Final worker brief must name the newly
integrated R01/R02 base before edits.

[MEASURED] Existing focused durability suite: 46 passed, two warnings, 0.74s.

Current-source candidates to reproduce in regression tests:

1. Live Voice polling validates neither known state nor complete output reference;
   unknown non-active/non-error states flow into `_record_worker_success`.
   Explicit Voice reconciliation already rejects unknown states.
2. `done` with no output URL bypasses artifact fetch and still records an asset
   and `done`; the artifact route later cannot fetch it. Test live and reconciliation.
3. [MEASURED independent and root reproduction] A valid 1,644-byte PCM WAV truncated to
   64 bytes still returns 0.1-second metadata because the parser reads headers
   without validating declared PCM frame bytes. Root reproduced with the pinned
   Python runtime, `io.BytesIO`/`wave` (8kHz mono 16-bit, 1,600 PCM bytes), and
   `wav_metadata(full[:64])`: bytes=64, audio_duration_s=0.1, no error. A failing
   permanent regression test remains required before the repair.

Preserve these intentional contracts:

- `_cache_voice_artifact_metadata` records fetch/parse/hash/count failure as
  `artifact_metadata_error`; generated-but-unverified output is not a new inference
  request. Do not conflate it with a missing artifact reference or change billing.
- The native/Hub stable request ID and Voice worker's exact-parameter idempotency
  mean a retransmitted POST can return the same job. Count actual worker-created
  attempts/results as well as POSTs; changed body under one ID is still a conflict.
- Remote-node regeneration policy is explicit in existing fleet tests. Do not
  casually replace it with Image/Music/local Voice reconciliation semantics.

Root ruling recorded in task_plan.md: narrow the original R03 shorthand to
false terminal states, absent artifact references, truly duplicate accepted work,
and falsely verified PCM metadata. Preserve deliberate compatibility; external
GenStudio billing behavior is not certified by these local fixtures.
