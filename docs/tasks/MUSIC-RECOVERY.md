# Music recovery card

## Status

[MEASURED] Package/engine source contract implemented on
`codex/music-package-contract` from integration commit
`14cd174fbb3f9ef8e4fff75aaf7ecf1047858c79`. Independent review of the first
implementation found lifecycle and upstream-initialization blockers. The
hardened commit `3a11c6f` passed exact-commit re-review with no remaining
findings and was approved for this bounded merge.

[MEASURED] The package/engine contract is integrated through reviewed
[PR #10](https://github.com/theng12/Studio-Hub-KH/pull/10) at `9f25c03`.
The next bounded segment is implemented on `codex/music-package-runtime`: pinned
runtime-overlay construction, content-only engine-source staging, private Hub
supervision, closed peer routing, and a staged startup/shutdown proof.

[MEASURED] No model payload was downloaded, copied, or modified. The protected
legacy library at `~/Downloads/model-library` was not inspected. No Music
inference, native workflow, installed-app replacement, deployment, or fleet
enrollment was performed.

## Recovered boundary

- Package identity is `ace-step-1-5-turbo`; engine identity is `music-mlx`.
- The package contains 26 pinned files and 7,709,377,612 expected bytes under
  `model/`: ACE-Step 1.5 Turbo DiT, VAE, Qwen3 0.6B text encoder, and the
  separately pinned ACE-Step 0.6B planner. The two Python files ACE-Step would
  otherwise copy into the checkpoint directory are pinned package members.
- XL Turbo, the 1.7B planner, and the unverified 4B planner are excluded from
  the manifest, catalog, and accepted request contract.
- The model installer receipt selects the immutable installed folder. The
  worker loads only its `model/` child and accepts no external checkpoint-root
  override.
- The engine is a separate explicit contract at Git revision
  `ca1e85fe9430179831e6bc6be790c332190a3866`. Staging accepts only that clean
  checkout, copies a 618-file allowlisted source tree without `.git`, and records
  the canonical content digest
  `a55b4bf166464a690e967b4a31862e39efc6bd954ef0214ac91030d99e97cb33`.
  Runtime verification checks that digest and the two immutable model-code
  digests before accepting imports from the bundled tree.
- Studio Hub overrides ACE-Step's initialization hooks with read-only bounded
  checks. It neither runs the upstream whole-repository downloader (whose gate
  incorrectly requires the excluded 1.7B planner) nor lets upstream synchronize
  executable files into the verified package.
- The Music dependency overlay is a 1.7 MB, seven-package, hash-locked layer over
  the existing immutable Voice Python. Torch and MLX are not duplicated. The
  complete staged overlay is pinned as
  `5f79f4de4856e370d8984e7fdd86cf418f31f731c5092e923264f03718bcbc3e`;
  both staging and worker startup reject added, removed, or changed files. The
  staged Hub owns Music as an independent process, publishes its output root,
  carries one composite runtime identity through release/registry/worker health,
  and routes the explicit Music health/catalog/job/generation surface. The
  worker remains unavailable for generation until the manifest-defined model
  package is installed.

## Safeguards retained

The recovered worker keeps owner-token authentication, bounded request
validation, one isolated engine child, durable job state, cancellation,
staged WAV publication, digest-backed artifact delivery, acknowledgement, and
restart reconciliation. A terminal receipt failure removes published outputs;
if removal cannot be confirmed, the durable job records
`JOB_PERSISTENCE_UNCERTAIN` plus the unverified paths and exposes no output URL.
Corrupt history is preserved and blocks startup, acknowledgement rolls back if
its receipt cannot be written, and cancellation is not reported as successful
unless engine termination is confirmed. WAV publication verifies that the PCM
payload actually contains every frame declared by its header.

## Verification

[MEASURED] Test-first failures were observed for the absent Music source and
manifest, the unsupported engine, the external checkpoint layout, an arbitrary
engine directory, and terminal receipt failure.

[MEASURED] The focused package/worker suite passes all 21 tests under the pinned
Voice runtime. The combined Music, model-manifest, and installer run passes 66
tests. The isolated Hub regression passes 73 tests; the Voice product suite
passes 722 with 19
real-model/environment skips.

[MEASURED] Independent review of `1401b1f` reproduced eight important defects
and two minor contract defects: upstream auto-download and package mutation,
unbound engine imports, cancellation uncertainty, ACK and running-state
durability, corrupt-ledger destruction, truncated WAV acceptance, zero-value
defaulting, and an overstated qualification label. Each now has a bounded fix
and regression coverage. Exact-commit re-review found no Critical, Important,
or Minor findings; it independently passed all 66 focused tests, seven executor
fault reproductions, four adapter reproductions, and a two-file installer
download/receipt proof.

[MEASURED] The resumed final root gate passed 631 tests (7 skips) in 442.126
seconds, including the restored Hub bridge (85 passes) and Voice product suite
(722 passes/19 skips). The earlier baseline failures were resolved by restoring
verified runtime paths, testing the real library-backed phonemizer and selecting
the current staged artifact explicitly. Native build and five original audit
probes passed. See `qualification/new-computer-resume-2026-09-21.md` for the
complete evidence and test-harness warning/qualification boundaries.

[REPORTED] Final independent review found no remaining findings after fixing
normalized Music request recovery and terminal-persistence rollback. Exact accepted
parameters are durable before polling; failed terminal publication retains the
machine fence and permits exact-job recovery without another generation POST.
Callbacks follow durable publication and recovered assets keep their existing ID.

[MEASURED] Graphify was unavailable in this environment. Callers and impact
were checked directly; no graph consultation is claimed.

[MEASURED] The real overlay builder installed exactly the seven locked packages
and imported the pinned ACE-Step inference modules against the shared Voice
runtime. A content-staged worker then published private loopback readiness,
accepted only the owner token, reported `checkpoints: missing`, and kept
`engine_loaded: false`. A complete 1.7 GB local proof bundle subsequently
started Hub, Voice, Subtitle, and Music, proxied authenticated Music health,
matched the Music runtime identity at all three boundaries, and removed its
readiness and owned-worker descriptors on shutdown.

[MEASURED] The first complete-bundle proof found three omitted closed maps:
packaged worker validation, fleet-safe route authorization, and the Hub peer
router. Test-first fixes added Music to those maps without widening settings,
storage, model-management, or other owner-only routes.

## Next bounded task

First reproduce/repair the native lifecycle audit findings and reference validation
candidates recorded in the resume receipt. Then build the native Music workflow
against the existing durable Hub job contract:
navigation, request form, readiness/blocking reason, per-studio history, artifact
open/save, and focused rendered-layout/accessibility review. Keep real-model
inference, installed-app replacement, and physical fleet qualification as later
explicit steps.
