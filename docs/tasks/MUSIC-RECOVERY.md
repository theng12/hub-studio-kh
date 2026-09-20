# Music recovery card

## Status

[MEASURED] Package/engine source contract implemented on
`codex/music-package-contract` from integration commit
`14cd174fbb3f9ef8e4fff75aaf7ecf1047858c79`. Independent review is pending.

[MEASURED] No model payload was downloaded, copied, or modified. The protected
legacy library at `~/Downloads/model-library` was not inspected. No Music
inference, package staging, supervisor launch, peer routing, native workflow,
or deployment was performed.

## Recovered boundary

- Package identity is `ace-step-1-5-turbo`; engine identity is `music-mlx`.
- The package contains 24 pinned files and 7,709,268,688 expected bytes under
  `model/`: ACE-Step 1.5 Turbo DiT, VAE, Qwen3 0.6B text encoder, and the
  separately pinned ACE-Step 0.6B planner.
- XL Turbo, the 1.7B planner, and the unverified 4B planner are excluded from
  the manifest, catalog, and accepted request contract.
- The model installer receipt selects the immutable installed folder. The
  worker loads only its `model/` child and accepts no external checkpoint-root
  override.
- The engine is a separate explicit contract: the launcher must be run by a
  pinned runtime containing `acestep` and must receive a pinned ACE-Step source
  root containing the handler, inference, and LM entry points.
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

## Verification

[MEASURED] Test-first failures were observed for the absent Music source and
manifest, the unsupported engine, the external checkpoint layout, an arbitrary
engine directory, and terminal receipt failure.

[MEASURED] The focused package/worker suite passes 11 tests with one FastAPI
skip under system Python and all 12 tests under the pinned Voice runtime. The
combined Music, model-manifest, and installer run passes 57 tests. The isolated
Hub regression passes 73 tests; the Voice product suite passes 722 with 19
real-model/environment skips.

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
