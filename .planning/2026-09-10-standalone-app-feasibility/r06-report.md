# R06 — Native Music workflow: local verification report

Date: 2026-09-21. Owner: DeepSeek (autonomous takeover).
Base commit: `e0205da` (the R06 changes are uncommitted at the time of writing).
Implementation: a bounded background subagent; the coordinator reviewed the full
diff and ran the integration gate.

[MEASURED] All claims below are from commands run on this computer. Self-review
only; no independent review. No real Music model ran, and no GUI save panel was
driven — model-free and fixture evidence only.

## Defect

[MEASURED] Music backend/runtime were integrated (PR #11), but `StudioAPI.swift`
had **no** Music support at all and the native page fell through to the
"Engine not installed" placeholder (`StudioViews.swift:375`, `:2153`).

## Implementation

- `native/StudioAPI.swift` (+410): `NativeMusicSettings`/`NativeMusicLimits`/
  `NativeMusicConfiguration` decoded from the worker's `generation_defaults`/
  `generation_limits`; `musicJobs`, `musicConfigurations`, `musicPrompt`,
  `musicModel`, `musicSettings`, `musicNote`, `submittingMusic`, `pendingMusic`
  with on-disk persistence (`native-pending-music.json`); `generateMusic`,
  `cancelMusic`, `reconcileMusic`, `musicData`, `saveMusic`, `playMusic`,
  `restoreMusicDefaults`; `musicModels(catalog, packages:)` maps catalog rows whose
  `capabilities` contain `music` to `function: .music` and appends them to the
  offered models; `"music"` added to the history/job filters.
- `native/StudioViews.swift` (+255): a Music workspace wired as
  `case .music: musicWorkspace`, with prompt editor + character count, Generate,
  Generation/model picker, Manage Music Models, Preview (`playMusic`), Save Audio,
  Recent music and per-job Cancel / Check Original Result / Save.
- `tests/native/api_main.swift` (+195): Music fixtures and cases.
- `tests/test_generation_time.py`: three job-row styles → four (`musicJobRow`).

## Verification

| Check | Result |
| --- | --- |
| `STUDIOHUB_MACOS_SDK=macosx26.5 bash native/build.sh` | **exit 0** — boundary, API, lifecycle checks all passed, optimized app built |
| `.build/StudioHub` sha256 | `81151261c084f1906c3428b924b57ebd6a5e9f57f1b250334978765939cff2f7` |
| `.build/native-api-tests` sha256 | `2d8350cd4ee8905c0f5413cb8166545af4f491dc948ee7a2864f714365450a76` |
| Music render 1920×1053 + 1040×680 | exit 0; both inspected: real form, model picker, Generate, preview, history — no placeholder |
| `test_generation_time.py` + `test_audio_layout.py` + `test_native_build_scripts.py` + `test_peer_path_contract.py` | 42 passed |
| Full root suite (no staged app selected) | **655 tests OK, 11 skipped**, 440.729s |

The native Music tests assert, among others: defaults and limits come from the
runtime; the submit envelope binds `modality: "music"`, model, prompt and the
worker's exact parameter names (`duration_seconds`, `guidance_scale`, `shift`,
`instrumental`, `vocal_language`); a corrupt artifact is rejected with an
actionable message and **no** ACK; a save that cannot be written never ACKs; a
verified save ACKs **exactly once**; a rejected ACK is reported and the verified
file remains on disk; an uncertain Music request is never resubmitted as new work
while a different request is allowed; the pending request is persisted; and
Restore Defaults uses runtime values.

## Limits

- No real ACE-Step inference; the model-free render and fixtures only.
- Actual GUI save-panel/keyboard/accessibility interaction was not driven; only
  offscreen renders at minimum and normal sizes were inspected.
- Independent review has not been performed.
- R06 does not qualify real Music generation; that is R07.
