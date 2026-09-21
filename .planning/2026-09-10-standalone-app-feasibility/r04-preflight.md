# R04 peer admission — local preflight

Base `b550617`; coordinator reproduction, 2026-09-21. No R04 product repair yet.
Real FastAPI peer route, disposable state via `StudioProxyTests`, real local
recording HTTP worker and separate fixture fleet credential. No models or live nodes.

Command: `PYTHONPATH=.build/test-deps .build/voice-runtime/bin/python3 -B
qualification/evidence/local-readiness-2026-09-21/r04_admission_probe.py` in
handoff-completion worktree (initial run from identical ignored .build copy).
Exit 0. Probe records current behavior; it is not a passing safeguard test.

| Node-owned condition | Peer POST response | Recording worker received |
| --- | --- | --- |
| `broker.model_block_state(image, fixture/flux)` confirmed blocked | 200 | 1 generation request |
| Studio disabled through authenticated `/api/hub/registry/studios/image/enabled` | 200 | 1 generation request |
| Actual output admission returns `OUTPUT_STORAGE_BLOCKED` for fixture policy | 200 | 1 generation request |
| Local physical machine reserved through `acquire_external_machine` | 200 | 1 generation request |

The paused model row was injected into the fixture's in-memory protection map;
the studio disable used the real operator endpoint. The caller then used the
fixture fleet token on `/studio/image/api/generate/txt2img`. Source trace:
`standalone.py:1201` forwards without consulting node admission. This proves the
node proxy boundary bypass, not a full controller scheduling reproduction.

R04 must add permanent negative coverage, two-disposable-Hub stale-capability
dispatch proof for these four cases and the smallest shared node-owned admission
repair while preserving cancel/status/artifact/ACK access for already-accepted work.
Do not make private forwarding itself forbidden or rename credentials. The existing
worker can enforce its own engine limits, but node-owned pause is not worker state.
One upstream Starlette deprecation warning was visible. Owned fixtures stopped.
