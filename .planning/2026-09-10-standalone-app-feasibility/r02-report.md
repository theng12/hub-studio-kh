# R02 — Reference integrity repair

Base: `0ec3b3b09c9642b670ec0a092f4223ea4ffe0e24`.
Owner: coordinator inline fallback (second worker service rejected task creation).
Status: implemented and independently reviewed; integration waits for R01.

[MEASURED] Real authenticated local/GenStudio endpoint fixtures accepted NaN,
infinite, negative, reversed and zero-length segment intervals (16 subcases).
Stored corrupt expiry caused an uncaught error or incorrect status; the new route
suite initially failed with 20 assertion failures and one error in 28 child tests.
Purge separately removed four entries instead of one because explicit empty
containers/null were coerced to expired zero. Logs: `.build/r02-red.log` and
`.build/r02-purge-red.log` (ignored, local).

The shared asset boundary now requires finite timestamps with `0 <= start < end`.
One expiry parser gives the existing `VOICE_REFERENCE_ASSET_INACCESSIBLE` error
for invalid/nonfinite stored deadlines. Stage/read retain their existing locks and
route mappings (400/409); refusal happens before metadata writes. Purge skips
invalid explicit deadlines but retains missing-deadline cleanup. Original bytes,
metadata, unrelated assets and valid idempotent extension are covered by fixtures.

[MEASURED] Proving command from this worktree:

```sh
PYTHONPATH="$PWD/.build/test-deps" .build/voice-runtime/bin/python3 -B -m pytest -q \
  tests/test_hub_voice_references.py tests/test_execution_asset_purge.py \
  tests/test_hub_standalone.py
```

Result: **19 passed**, including **28 child HTTP contract tests**, two inherited
dependency deprecation warnings, 2.98 seconds. Existing refresh/sweep and
reader/sweep concurrency tests pass. No route, broker, native or model change.

Ruling: explicit invalid null/container deadlines are not proof of expiry and
are preserved, unlike an absent deadline (existing cleanup contract). Cost:
corrupt explicit deadlines require deliberate repair/removal. Cross-segment
ordering and inside-clip checks remain owned by the Voice worker, unchanged.

[MEASURED] Broader isolated Hub suite: **85 passed**, two dependency warnings,
1.40 seconds (`pytest -q --confcutdir=tests/hub_regression/cases
tests/hub_regression/cases`). Independent reviewer `music_recovery_read` inspected
the three owned code/test files, traced stage/status/broker/storage callers and
re-ran focused checks: 19 passed in 2.97 seconds, no actionable findings.

Limitations: no real-model inference or installed-app use; final assembled/native
gate and ordered integration remain pending. No commit/publish/deploy performed
for this slice.
