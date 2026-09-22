# Self-review — session of 2026-09-22

This is the author's own review, not an independent one. It covers everything
committed since the takeover base `9a4570da` (141 files, +18,445/−219), with the
machine checks re-run at the end rather than quoted from earlier.

## What was checked, and the result

| Check | Result |
|---|---|
| Full root test suite | **OK** (10 skipped) |
| `native/build.sh` (boundary, API, lifecycle, optimized app) | **exit 0** |
| Image runtime rebuilt **offline** after the R05 wheel fetch | **exit 0**, runtime produced |
| Four SSD kits, each drive's own `verify-kit.sh` | **PASS** ×4, 35/35 packages |
| Music worker suites after the fix below | **149 passed** |
| Mac library synced from ATLAS | **35/35**, 83 GB |

## Findings

1. **Music had a single point of failure — fixed.** `GenerationManager.__init__`
   built the Stable Audio cache eagerly and unguarded, so a missing or unreadable
   Stable Audio package (a) raised during construction and (b) stopped the whole
   Music worker, taking ACE-Step down with it. This is exactly what happened while
   preparing the SSDs, when `.studiohub` was not owner-only. Now the second engine
   is constructed in a guard: a failure is recorded and reported by that model's
   `preflight` as `manifest_unavailable`, and ACE-Step keeps working. Regression
   test added in `tests/test_music_stable_audio.py`.

2. **Stable Audio reader thread is a daemon.** It drains the CLI's stdout for
   progress and ends when the pipe closes; if a grandchild ever held the pipe open
   it could outlive the call, bounded by process exit. Acceptable.

3. **The uncertainty auto-discard window (30 min) is a judgement call**, not a
   measured threshold. It is env-overridable
   (`STUDIOHUB_UNCERTAIN_DISCARD_SECONDS`) and documented. On this Mac a worst-case
   ACE-Step run (cold load ~3 min + 600 s at ~2× realtime) is ~23 min, so it should
   not overlap a live job; a much slower Mac could, which is why it is configurable.

4. **Discard was operator-only; the owner changed that.** The fleet allowlist
   originally excluded the discard route, so a controller could not release a node's
   machine. On the owner's decision (2026-09-22) the controller **may** now discard
   an uncertain attempt on a node it sent work to; the node's own operator can still
   do it locally. The allowlist change is explicit and covered by
   `tests/test_fleet_credentials.py`.

5. **VoxCPM2 without a saved transcript clones with lower fidelity.** It now works
   offline (empty `ref_text` avoids mlx-audio's undeclared STT model) but the trade
   is accepted rather than solved; a local transcription pass would recover quality.

6. **The image wheel cache is a cache, not a guarantee.** The fetch populated uv's
   default cache and the offline build now succeeds; if that cache is ever pruned,
   the fetch must be repeated. The R05 recipe is the one used here.

7. **No independent review exists for any of this session's work.** Items 1-6 are
   the author's own; an outside pass is still the honest way to find the rest.

## Things intentionally left alone

- `codex/recovered-music-draft` — one `wip` commit not in `main`; unfinished, so
  not merged.
- `_trash/` folders on this Mac and on all four drives — the owner empties them.
- The 153 GB plain archive on NOVA — untouched by request.
