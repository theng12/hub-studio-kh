# ST-02 Hub regression proof

Date: 2026-09-15
Standalone worktree: `implementation/st-02` at baseline `c16833c`
Adopted Hub source: `/build/pinokio/api/studiohub-mac` at
`06e893e1643bffbcbd162fae122e5a4d0f29e283`

## Result

The bounded regression set passed **32 tests** with the packaged Hub backend
and a disposable packaged worker manifest:

```text
................................                                         [100%]
32 passed in 3.66s
```

Full output is retained at
`.build/st02-hub-regression.log`.

The root-suite wrapper is the supported entry point; it launches the adapted
cases in a child interpreter so their module-level state paths cannot pollute
the other tests. Run it from the standalone worktree:

```sh
PYTHONPATH=/build/Projects/studiohub-standalone/.build/test-deps \
PYTHONDONTWRITEBYTECODE=1 \
/build/Projects/studiohub-standalone/.build/voice-runtime/bin/python3 \
-m pytest -q tests/hub_regression
```

The child command is `python -m pytest -q tests/hub_regression/cases`, with the
parent interpreter's `PYTHONPATH` retained and only the disposable Hub state
variables removed. The wrapper has a 60-second bound and writes the child
stdout/stderr to the log above.

No live worker, network request, model generation, source checkout, or model
cache was used. The fixture creates a private temporary state directory and a
three-worker packaged manifest before importing `components.hub.backend`.

## Upstream provenance and selection

These files are the exact upstream test sources at the adopted commit. The
listed values are Git blob IDs from that commit:

| Upstream source | Blob | Tests retained |
| --- | --- | ---: |
| `app/tests/test_ledger_recovery.py` | `82149d5acd1450a78e14466fd9b2775b33c3dc8d` | 3 |
| `app/tests/test_memory_admission.py` | `deca10307a75dcfd09d944802759abfde2a4b916` | 5 |
| `app/tests/test_model_failure_protection.py` | `19f0e6a0fed8dbb345b095f7e040e7879d688ba9` | 7 |
| `app/tests/test_execution_identity.py` | `5866a967ec5a44f44a63795da14f2ceeadb99613` | 13 |
| `app/tests/test_execution_assets.py` | `863d8f6fc359ff60a4d6e61261646f0e43878de2` | 4 |

The selected contracts cover malformed-ledger quarantine and rollback,
memory floors and live headroom, model-specific failure quarantine and fresh
readiness, lost-poll failure classification, external identity/idempotency/
fencing/lease recovery, and private checksum-bound voice assets.

## Adaptation record

- Imports use `components.hub.backend` instead of the upstream `backend`
  package. No alias or legacy module was introduced.
- `tests/hub_regression/conftest.py` sets packaged state paths before import,
  resets only the adopted backend modules, and binds a `StudioMonitor` fixture
  to a temporary owner-only worker manifest. It does not import the unavailable
  upstream `main` application or its full legacy fixture.
- The two upstream asyncio tests use `asyncio.run` because the bundled test
  environment has no `pytest-asyncio` plugin. Their assertions and mocked
  worker behavior remain unchanged.
- Tests requiring upstream management routes were excluded. In particular, the
  upstream private staging endpoint test is not registered by the current
  packaged `components.hub.standalone` app; the underlying execution-assets
  checksum/privacy and broker multipart protections remain covered by the four
  selected tests.
- The upstream memory-admission HTTP management tests and unrelated scheduler
  variants were excluded for the same bounded packaged-core scope. No test
  assumes an unregistered route.

## Limits

This is core backend regression evidence, not full ST-02 qualification. It does
not prove native Swift routing, process crash/no-orphan behavior, real model
inference, physical-device memory, macOS Sonoma compatibility, external
GenStudio transport, or the separate process test owned by the other worker.
