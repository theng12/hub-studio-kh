# Music recovery card

## Status

[MEASURED] Package/engine source contract implemented on
`codex/music-package-contract` from integration commit
`14cd174fbb3f9ef8e4fff75aaf7ecf1047858c79`. Independent review of the first
implementation found lifecycle and upstream-initialization blockers; fixes are
implemented and awaiting exact-commit re-review.

[MEASURED] No model payload was downloaded, copied, or modified. The protected
legacy library at `~/Downloads/model-library` was not inspected. No Music
inference, package staging, supervisor launch, peer routing, native workflow,
or deployment was performed.

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
  `ca1e85fe9430179831e6bc6be790c332190a3866`: the launcher must be run by a
  pinned runtime and must receive that exact clean ACE-Step checkout. Runtime
  imports must resolve inside it, and the model-code files are checked against
  their immutable digests.
- Studio Hub overrides ACE-Step's initialization hooks with read-only bounded
  checks. It neither runs the upstream whole-repository downloader (whose gate
  incorrectly requires the excluded 1.7B planner) nor lets upstream synchronize
  executable files into the verified package.
- Hub model-package readiness understands `music-mlx`, but the Music worker is
  not staged or supervised yet and therefore remains unavailable in packaged
  builds.

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
tests. The isolated
Hub regression passes 73 tests; the Voice product suite passes 722 with 19
real-model/environment skips.

[MEASURED] Independent review of `1401b1f` reproduced eight important defects
and two minor contract defects: upstream auto-download and package mutation,
unbound engine imports, cancellation uncertainty, ACK and running-state
durability, corrupt-ledger destruction, truncated WAV acceptance, zero-value
defaulting, and an overstated qualification label. Each now has a bounded fix
and regression coverage; final re-review remains pending.

[MEASURED] Broad discovery ran 608 tests: 592 passed and 11 skipped. Its five
non-passes are the intake baseline rather than Music failures: no worktree-local
Image runtime, no `espeak-ng` on the shell PATH, two stale staged 0.5.4 release
checks while source is 0.6.38, and an initially undiscoverable shared pytest
dependency. Supplying the existing shared test-dependency path made both nested
pytest gates pass at the counts above.

[MEASURED] Graphify was unavailable in this environment. Callers and impact
were checked directly; no graph consultation is claimed.

## Next bounded task

Package the pinned Music runtime and ACE-Step source, stage the Music component
and launcher, add the supervisor/storage spec, and prove private worker startup
without touching native UI or fleet routing. Real-model qualification remains
a later explicit step.
