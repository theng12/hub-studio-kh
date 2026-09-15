# ST-03 real packaged inference — completed, previously unrecorded

Status: evidence of a **completed real FLUX generation** through the assembled
Hub + Image worker + native UI. This happened after
[`st03-image.md`](st03-image.md) was written, and was not recorded on the card,
the receipt or the board. It was found in the `st-03` worktree's gitignored
`.build/` during the 2026-09-15 record reconciliation and copied here so it
survives `.build/` cleanup.

## What was proven

One real 1K text-to-image generation through the packaged application, with the
native client driving the bundled Hub and the separate Image worker.

| Item | Value |
| --- | --- |
| Job ID | `af4913b2b52b41b0931f919112daf969` |
| Model | `AITRADER/FLUX2-klein-4B-mlx-4bit` |
| Model revision | `7fd24828501390b67a92c8b66d2fc5a707d0ba1a` |
| Image runtime tree SHA-256 | `c9cd56a6d1cde6b72dee8ba22cf45fcbd640538207038087261cae91ec67f67e` |
| Assembled app implementation commit | `4377237e350d6adc7636aaf23dddc454af74469d` |
| Mode / resolution | `txt2img`, 1K preset, 1280 × 720 |
| Steps / guidance / seed | 4 / 1.0 / 42 (resolved) |
| Prompt | "A quiet riverside garden at sunrise, soft mist over the water, green trees, natural light, photographic detail." |
| Duration | 105.943 s |
| Output bytes | 1,696,066 |
| Output SHA-256 | `290d2fb8cf0cd927d7d0633412bb157a9ee8675684ed2da7e2e1267c6ce919bd` |
| PNG dimensions / signature | 1280 × 720, `89504e470d0a1a0a` |
| Peak process RSS | 1,105,281,024 bytes (~1.03 GiB) |
| Worker state | `done`, `progress: 1.0`, `tries: 1` |
| Hub output group | `local:image:af4913b2b52b41b0931f919112daf969:1`, `state: finalized`, `attempt: 1` |
| Receipt acknowledgement | `receipt_acked_at: null` — **not acknowledged** |

The worker emitted a complete final receipt (`bytes`, `sha256`, `width`,
`height`, `media_type`, `output_path`, `runtime_revision`, `resolved_seed`), and
the Hub registered it as one finalized output group with the matching SHA-256.

## Evidence in this repository

- [`evidence/st03-image-real-inference.jsonl`](evidence/st03-image-real-inference.jsonl)
  — the collector run (126 records: 122 RSS samples, job seen, worker terminal,
  Hub item plus finalized output group).
- [`evidence/st03-flux2-klein-1280x720-seed42.png`](evidence/st03-flux2-klein-1280x720-seed42.png)
  — the generated output (SHA-256 verified above).
- [`evidence/st03-image-studio-completed.jpg`](evidence/st03-image-studio-completed.jpg)
  — the native Image Studio at 1405 × 768 showing Image size `16:9 - 1280 × 720`,
  Steps 4, Guidance 1, Seed 42, Model `FLUX.2 Klein · Installed`, the rendered
  preview, a `Recent Images` entry marked `FLUX.2 Klein · Completed`, and
  `Local services ready`.
- [`evidence/st03-image-studio-minimum-1040x712.jpg`](evidence/st03-image-studio-minimum-1040x712.jpg)
  — the same workspace at the 1040 × 680 minimum layout.

Captured on Apple M4, 16 GiB RAM, macOS 26.6.2 — the same host class recorded for
the rest of the phase, and within the 16 GB / 1K / 4-step workload the historical
Image audit approves. Peak RSS ~1.03 GiB means the workload fits the 16 GB host
with large headroom.

## What this does and does not close

Advances:

- ST-03.1 (FLUX.2 Klein 4B MLX 4-bit adoption) — exercised for real.
- ST-03.RELOCATE — the relocated runtime produced a real image.
- ST-03.2 — readiness and dispatch exercised with a real model.
- ST-03.5 — real packaged generation observed; **export/receipt acknowledgement
  is not proven** (`receipt_acked_at` is null) and restart-history checks are not
  evidenced in this run.

Still open:

- Bounded retry counts distinguishing attempts from requested jobs (ST-03.6).
- Native export with verified destination acknowledgement.
- Independent review of the final integrated Image changes.
- Physical original-M1 / Sonoma checks (ST-11).
- The assembled `ST03 Image Integration.app` was built from `4377237` in the
  `st-03` worktree and predates the `cd6ed5d` integration merge. Re-stage from the
  merged tree before citing artifact hashes: staging Image requires
  `--image-runtime <built runtime>`. The pinned Image runtime was later preserved at
  `.build/st03-image-runtime-worker` in the main checkout (tree SHA verified against
  `c9cd56a6…`, copied out of the disposable `.worktrees/st-03/.build/`).

## Export / receipt acknowledgement — proven 2026-09-15

The Hub ↔ worker receipt contract was exercised end to end against the
Image-inclusive artifact staged from the merged tree
(`dist/Studio Hub 0.4.0 Image Proof.app`, `implementation_commit 86b11d3`,
payload `0cabfe85…`, bundling `runtimes/image` and `image_worker.py`):

| Step | Result |
| --- | --- |
| Submit through the Hub | `POST /api/hub/jobs` → HTTP 200, batch `2002393f6a` |
| Real generation | `done`; 1,696,066 bytes; SHA-256 `dc995e8a…`; 107.73 s; studio job `f04f1b92f84e48cfac9f32be2b7b24d4` |
| **Before** acknowledgement | `receipt_acked_at: null` |
| `POST /api/hub/jobs/2002393f6a/items/0/ack` | HTTP 200, `{"ok": true}` |
| **After** acknowledgement | `receipt_acked_at: 1789485143.736741` |

Both states were captured for the same job in a single run and are stored together in
[`evidence/st03-ack-proof.json`](evidence/st03-ack-proof.json) alongside the artifact identity
that produced them (`transition_confirmed: true`). The app was then stopped; both readiness
files were removed and no project process remained.

An earlier capture (batch `d8c27a4e8e`) recorded only the post-acknowledgement state. The
independent review correctly flagged that the null→stamp transition was therefore asserted
rather than evidenced; that capture is superseded by the run above.

## Packaged restart history — verified 2026-09-15

The same artifact was fully stopped and relaunched against the same external state directory.
Before the restart the Hub listed batch `d8c27a4e8e`; after the restart it was still listed with
the identical output SHA-256 `5e4cd5db…` and `receipt_acked_at` still set, on a **different** Hub
port. So job history, output identity and delivery acknowledgement all survive a packaged
restart. This is a ledger-level restart check through the Hub API; a human-driven click-through
of the same history in the window remains part of the GUI pass.

### Native wiring

`native/StudioAPI.swift` `saveImage` now acknowledges delivery **after** the written
file is byte-verified, so an unverified or failed save can never acknowledge. It
checks the returned `ok`; on failure it reports "saved and verified, but its delivery
could not be acknowledged" and leaves the file on disk. The managed copy then stays
protected and ST-07 cleanup treats it as undelivered — the conservative direction.

`tests/native/api_main.swift` covers both branches: an unverified read acknowledges
nothing, a verified save acknowledges exactly
`/api/hub/jobs/image-job/items/0/ack` once, and a rejected acknowledgement is reported
while the saved file remains. `bash native/build.sh` passes boundary and API contract
checks.

Still open: exercising the path through the real GUI **Save** panel against a live Hub (the
proof above drove the Hub API directly), ST-03.6 retry-count confirmation, and a
human-driven click-through of history in the window. Restart history is verified above and
independent review has run — see below. Separately, `StudioViews.swift` `saveAudio` still
writes audio bytes with no verified-destination acknowledgement, so the same wiring is owed
for Voice export under ST-08.

## Independent review — 2026-09-15

A separate read-only reviewer inspected the merged ST-03 code, the acknowledgement wiring and
every recorded claim, and re-ran the suites. Verdict: **no code blocker** — the
acknowledgement gating is correct and it found no path where the app acknowledges delivery
without a byte-verified artifact. It did find defects in the *evidence and tests*, all of
which are fixed in this pass:

| Finding | Resolution |
| --- | --- |
| The null→stamp transition was asserted, not shown; the `null` in the jsonl belonged to a different job | Re-ran and captured **both** states for one job in `evidence/st03-ack-proof.json` |
| The receipt cited a `5c8f85d` artifact that is no longer at the cited path | Proof re-run against the current `86b11d3` build; its identity is recorded inside the evidence |
| The board said restart-history was proven while the card said it was open | Restart-history tested, then recorded once and consistently |
| No test made the destination write fail, so "a failed save never acknowledges" was unenforced | Added a failing-destination case asserting **zero** acknowledgements |
| The rejection fixture returned `200 {"ok": false}`, a shape the Hub never returns | Fixture now returns the real `502` rejection |
| Message claimed "cleanup will retry later", but nothing retries an acknowledgement | Message now tells the user to save again, which is what actually retries it |
| `saveImage` did not re-check the connection around the acknowledgement | Guard added: a changed connection reports instead of acknowledging the wrong Hub |

Review findings accepted as minor and **not** fixed here: the broker's uncertain-image
*recovery* poll path is exercised only indirectly (the malformed-poll cases drive the normal
poll), and the extra `.build/` logs cited by [`st03-image.md`](st03-image.md) live in the
`st-03` worktree — the important ones are copied into [`evidence/`](evidence/).
