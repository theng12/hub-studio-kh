# R03 Voice proof-fixture alignment

Base: `fb36c89dddf3592209c51e86e2699f07a6b30fd5`  
Worktree: `~/.codex/worktrees/handoff-completion/studio-hub-kh`  
Scope: `tests/_hub_dispatch_proof.py`, embedded `_PROOF` in `tests/test_hub_jobs.py`.

## Finding and repair

Both duplicate Voice fixture workers returned only `{job: {id}}` on acceptance,
then omitted the public `GenerationJob.serialize()` fields on status. Current Hub
Voice validation therefore treated the otherwise successful lifecycle as an
uncertain malformed receipt: the terminal response lacked `params.client_request_id`
and `output_path`.

Both fixtures now retain submitted params and return the real public shape:
acceptance state/params with no terminal artifact, and running/cancelled/terminal
status responses with state, params, exact `/audio` output URL, terminal output
path, and existing bytes/digest/media type. Existing assertions and server
behavior are unchanged; no production files were edited.

## Evidence

Before repair (required focused gate): 2 failures. Both real broker lifecycle
proofs remained `uncertain` with `VOICE_RECEIPT_INVALID` and no `jobID`/artifact
receipt.

After repair:

```text
PYTHONPATH=.build/test-deps .build/voice-runtime/bin/python3 -B -m unittest -v \
  tests.test_hub_dispatch_proof_gate tests.test_hub_jobs
Ran 8 tests in 15.518s
OK
```

Additional checks passed:

```text
git diff --check
PYTHONPATH=.build/test-deps .build/voice-runtime/bin/python3 -B -m py_compile \
  tests/_hub_dispatch_proof.py tests/test_hub_jobs.py
```

Graphify CLI was unavailable and the saved graph is stale; source/contract
inspection was verified directly against `GenerationJob.serialize()` and the
current Hub Voice receipt validator. No commit, push, deployment, live data,
model-library access, or hosted/paid call was used.
