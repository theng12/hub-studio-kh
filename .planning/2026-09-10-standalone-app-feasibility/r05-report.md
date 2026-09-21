# R05 — ST-card reconciliation, harness inputs and known limitations

Date: 2026-09-21. Owner: DeepSeek (autonomous takeover). Base `9a4570d`
(R04 and host-identity fixes follow at `e1d167a` and `b68e7b2`).

[MEASURED] Method: compared each open ST-03/04/05/06/08/10/11 checkpoint against
current source paths, focused tests and the recorded receipts, then labeled it.
No model inference or physical-hardware test was run here. A checkbox is only
"IMPLEMENTED" when current source/tests demonstrate it; receipt-dependent and
hardware-dependent checkpoints stay GATED even where the code exists. Historical
card text is preserved; this report is the reconciliation, and checkboxes are
deliberately not bulk-ticked.

## Verdict legend

- **IMPLEMENTED** — current source + focused tests demonstrate it locally.
- **PARTIAL** — some sub-parts implemented, others proven absent or unproven.
- **GATED** — depends on real models, physical hardware, or external authority.
- **REVIEW** — awaits independent review.

## Per-card verdicts

| Card | Verdict | Evidence | Gate |
| --- | --- | --- | --- |
| ST-03 FLUX vertical slice | IMPLEMENTED locally; ST-03.5 GATED | Image worker/backend adopted; `tests/test_image_standalone.py`, `tests/hub_regression/cases/test_image_uncertainty.py` pass; R02/R03 preserve it | real packaged generation/restart receipt (R07) |
| ST-04 Qwen Base | Code present; checkpoints GATED on receipts | `components/voice`, `model-manifests`, `tests/test_qwen_base_standalone.py`, `components/voice/app/tests/test_qwen_revision_evidence.py`; clone/reference contract used by R02/R03 (`execution_assets.py`) | QWEN06/QWEN17 real-model receipts; independent review (R07) |
| ST-05 VoxCPM2 / OmniVoice | Code present; checkpoints GATED | Catalog exposes VoxCPM2 4/8/bf16 and OmniVoice as clone models (`51c31a4`, cited in ST-08.2); variants kept distinct in manifests | VOX4/VOX8/VOXFULL/OMNIFULL real receipts; prior heavy-run failures unproven (R07) |
| ST-06 Subtitle | **Implemented, card stale (shows WAITING/0 checked)** | Independent `components/subtitle/app/backend/{main,manager,engine}.py`; `tests/test_subtitle_standalone.py`, `test_subtitle_cancel_wins.py`, `test_subtitle_upload_limit.py`, `test_subtitle_video_audio.py`, `test_subtitle_bundle.py`; H-05 artifact disposition + R03 delivery fixes | ST-06.TURBO / 06.6 real-model + offline restart receipts (R07) |
| ST-08 native workflows | PARTIAL | ST-08.2/08.3/08.DATA "~" notes remain accurate; native build/boundary/API/lifecycle pass; R01 hardened lifecycle | ST-08.PILOT needs ST-04/06 receipts; ST-08.OS needs original M1/Sonoma; Music workflow is R06 |
| ST-10 fleet / GatewayKH | PARTIAL | `tests/test_standalone_connections.py` **33 passed** (dispatch, artifact, cancel, auth, rotation, adoption); R04 adds node-owned admission | ST-10.4 per-device fanout, ST-10.6 remote discovery/retry matrix, physical node (R09) |
| ST-11 kit / qualification | PARTIAL, mostly GATED | `tests/test_model_packages.py`, `test_model_downloads.py`; `packaging/release_kit.py`; local readiness receipt | clean Mac without tools/network, original M1, reboot/login, second node (R10) |

## Not implemented (real gaps, not missing evidence)

- **ST-08 / R06:** the native Music workflow. The Music backend/runtime is
  integrated (PR #11) but the native UI still falls through to "Engine not
  installed" rather than offering readiness, controls, submit/progress/cancel,
  uncertain recovery, Open/Save and verified ACK.
- **R09:** GatewayKH sends no delivery ACK after artifact download, does not call
  Hub DELETE on cancel, and its adapter omits Music.
- **R05 Image offline inputs:** the declared exact CPython/wheel/cache inputs are
  not yet established, so an offline reconstruction from an empty user cache was
  not attempted here.

## Test-dependency setup (recorded, not rebuilt)

[MEASURED] Test dependencies are a development-only target directory
`.build/test-deps`, separate from the immutable `.build/voice-runtime`. Exact
versions recorded in the handoff: `pytest==9.1.1`, `iniconfig==2.3.0`,
`packaging==26.3`, `pluggy==1.6.0`, `Pygments==2.21.0`. Current invocation used
throughout this session:

```sh
PYTHONPATH=.build/test-deps .build/voice-runtime/bin/python3 -B -m pytest -q <paths>
```

No test tool was installed into the immutable runtime. Old qualification harnesses
must be given explicit paths; do not replace a live library or hardlink into a
verified package (ctime changes invalidate verification).

## Image offline rebuild (attempted; declared inputs were incomplete)

[MEASURED] Declared inputs found and verified present:
`packaging/voice-runtime/pins.env` (CPython `3.12.14+20260901`, sha256
`3ee3ee547cedfeb7c2b16b2b7156039f7b470bb8f857e226fd3d2eb11db83c76`), the pinned
archive `.build/downloads/cpython-3.12.14+20260901-aarch64-apple-darwin-install_only.tar.gz`
(checksum OK), and `packaging/image-runtime/requirements.lock.txt`
(sha256 `5fd5e79db7f8bec7dfa5c3ebc0fd1495ba7460138b44caf06bb6c0346c186b4f`, checksum
OK). The existing `.build/st03-image-runtime-worker` still verifies.

[MEASURED] Offline reconstruction was then attempted with an **empty user cache**
and only project inputs:

```sh
env -i HOME=<fresh temp> PATH="<system tools>:/opt/homebrew/bin" \
  UV_CACHE_DIR="$PWD/.build/uv-cache" \
  bash packaging/image-runtime/build.sh .build/deepseek-image-offline
```

Result: **failed loudly at `uv pip sync --offline`** with
`Because contourpy was not found in the cache and you require contourpy==1.4.0`,
network disabled. The builder never silently reached the network, which is the
required safety property, and it left no partial runtime.

[MEASURED] The project uv cache `.build/uv-cache` is not a complete declared input
for Image. Comparing the 73 locked package names with the cache's registry
metadata (`simple-v25/pypi`), **21 are absent**: `contourpy cycler docutils
fonttools hf-transfer id jaraco-classes jaraco-context jaraco-functools keyring
kiwisolver matplotlib mflux nh3 opencv-python piexif platformdirs
readme-renderer requests-toolbelt toml twine`. `mflux` itself is among them.

Gate: proving an offline Image rebuild from an empty user cache requires an
explicitly populated, declared Image wheel cache. Populating it needs a network
package fetch, which this session treats as a separate authorization, not a silent
step. The existing transferred runtime remains valid; this is a bootstrap/kit gate,
not an app defect. `.build/deepseek-offline-home.*` and any stage directory were
removed after the attempt.

## Graphify

[MEASURED] The Graphify CLI/interpreter is unavailable on this computer and the
saved graph at `research/voice-source-graph/graphify-out/graph.json` predates the
current Hub/native/Music/GatewayKH work. Callers were inspected directly with
`rg` and source reads. No generated graph was committed, and no paid extraction
was used. Restoring code-only Graphify support remains an optional R05 item.

## Harness caveat found this session

[MEASURED] This harness's `PATH` omits `/usr/sbin`. `resources.apple_chip_name()`
and `apple_machine_type()` originally invoked `sysctl`/`system_profiler` by bare
name and returned nothing; fixed in `b68e7b2` with absolute-path preference and a
`system_profiler` fallback, verified by `tests/test_hardware_identity.py`.

## Limits

- No model inference, physical node, original M1, reboot or clean-Mac run.
- ST-04/05/06 implementation presence is asserted from source + focused tests, not
  from real generation; their qualification checkpoints remain gated.
- Independent review of the whole reconciliation is not performed.
