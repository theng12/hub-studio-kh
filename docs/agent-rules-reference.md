# Agent rules reference — moved detail

This file preserves detail moved out of `AGENTS.md` on 2026-11-08 so the working
rules stay short. **`AGENTS.md` is authoritative.** Historical sections are records,
not current rules: do not treat them as instructions or revive retired features.
Operational sections are detail only; the summary in `AGENTS.md` wins.

## September 2026 fleet history

**Historical record — superseded by `research/owner-hardware.md`, the live source.**

| Unified memory | Chip(s) | Machines |
| --- | --- | ---: |
| 8 GB | M1, M2 | ~35 |
| 16 GB | M1, M2, M4 | ~15 |
| 24 GB | M4 | 2 |
| 32 GB | M2 Max | 1 |

**Corrected by the owner 2026-09-26 — the fleet is ~53 machines, not 7.** 16 GB is the tier to qualify against (50 of ~53 are 16 GB or below), and the ladder is **qualify at 16 GB first, fall back to 24 GB only if that fails**. (The earlier "one model across the fleet, not a model per tier" preference was relaxed the same day.) A package that fails qualification is still kept on the SSDs as a backup — nothing is deleted for failing.

Size every recommendation to the smallest machine that must run it, and label each memory claim as measured, vendor claim or inference. He runs these models himself, on his own Macs, so a fit that only works on the 32 GB machine is not a fleet answer.

**The live list and current model direction are in `research/owner-hardware.md` and `AGENTS.md`; this section is history only and must not be read as current instructions.**

## Hardcoded model-list incidents

**Historical record — examples behind the AGENTS.md rule, not instructions.**

This has already cost us three times, all the same shape — something new reaching the catalog while a hardcoded list in a consumer did not learn about it:

- **`chat-mlx` missing from the Hub's engine→worker map (0.6.57).** `GET /api/hub/model-packages` raised `KeyError` and returned **HTTP 500**; the native `refresh()` reads that route with a hard `try`, so it marked the Hub unreachable and **blanked every model picker on every Mac.**
- **A hardcoded voice family allowlist in `native/StudioAPI.swift`.** `formCompatible` accepts only `qwen3-tts`, `voxcpm*` and `omnivoice`, so `khmer-tts-mms`, `khmer-tts-onnx`, `chatterbox-mlx` and `fish-audio-mlx` — **six of thirteen voice packages** — were invisible in the Audio picker while the catalog, Hub, manifests and kits all offered them correctly.
- **A test asserting the broken behaviour.** The native suite required the Audio picker to contain exactly Qwen and VoxCPM2, and its fixture had a `family: "other"` model to prove unknown families were rejected. It was green by construction.

## Retired and former model detail

**Operational detail — the summary in `AGENTS.md` is authoritative.**

**One list retires a model:** `components/retired_models.py` (`RETIRED_MODELS`). The Hub catalog, the model list published to the gateway, the kit / SSD listing, kit staging and the model-copy step all read it, and a retired model is refused by name (`MODEL_RETIRED`). Retiring a model is one line there.

**A model is blocked by RAM, never by name (owner, 2026-10-27):** *"They are models we can run so we should have them installable. Able to run on the machine or not is based off its RAM. We no longer block or lock any model."* So `RETIRED_MODELS` is for a PRODUCT the owner has retired (the local video pack and its processor companion), not for a family or a model he wants back, and `"production_allowed": false` is not a stand-in for "not measured yet". Every shipped package must be installable on every Mac; `qualification.minimum_unified_memory_gb` is the only gate, and a package that publishes no floor is read by the kit copy and by dispatch as fitting **every** Mac - so a newly admitted model publishes a floor, labelled `PROVISIONAL, NOT MEASURED` in `memory_evidence` when it is derived from the shipped weight bytes rather than measured. `tests/test_catalog_visibility.py` and `tests/test_document_inventory.py` are where that invariant is pinned.

**Shelving is deleted — do not restore it (owner, 2026-10-28).** `components/shelved_models.py`, `SHELVED_MODELS`, `keep_all_models` and the kit's "Shelved, not copied" skip are **gone**, along with the "offered but not stocked" list (`NOT_STOCKED`, whose one entry was `qwen-image-2-1-mlx-serve-4bit`). The seven packages that list held — `whisper-base`, `whisper-small`, `voxcpm2-bf16`, `fish-audio-s2-pro-bf16`, `chatterbox-4bit`, `chatterbox-8bit`, `s3tokenizerv2` — are ordinary catalog entries: listed, tickable, in **no** preset, and gated by RAM alone. A copy already installed on a Mac stays where it is and works. **RAM is the only gate; no model is refused or hidden by name.** `components/retired_models.py` is the one list that still moves an installed package out, and it stays exactly as it is (ACE-Step 1.5, Lance 3B Video and its Qwen2.5-VL processor, LTX-2.3 22B).

## Evidence and build-claim incidents

**Historical record — examples behind the AGENTS.md rules, not instructions.**

### Never publish a number the model did not state (2026-09-25)

Gateway Studio measured the failure from the other side of the API: with nothing stated, it fell back to a curated flat `context_window_tokens = 1,000,000` / `max_output_tokens = 128,000` and labelled it *curated* — an honest label on a number that is not the model's, which a downstream UI then shows as if it were. Meanwhile the shipped chat model's own `config.json` states `text_config.max_position_embeddings = 262144`, two orders of magnitude away.

### A version field must carry a version (2026-09-25)

Four of five workers answered the literal string `"standalone"`, which reads as data, is indistinguishable from a version that failed to load, and makes every Mac look like the same build.

### A document names the build that has the behaviour (2026-09-25)

A handoff written while the work was in flight described `--listen-auto`, `--fleet-mode`, the bind on `/api/health` and `lane_contract.structured_output` as things a reader could rely on. **None of them were in the released build.** GatewayKH diagnosed an unreachable fleet against those promises, verified the installed bundle instead of the checkout, and found the same version string **thirteen commits apart**. The fleet's five nodes had been unreachable the whole time.

This is the third time in two days that a claim outran the artefact — a tracker row said a field was "live on every chat row" when it was in an unpushed working tree, and a review recorded "the app ignores the warning" where the app had rendered it all along.

## Library ownership incident

**Historical record — the data-loss incident behind the AGENTS.md safeguard.**

A Hub started with a throwaway `--data-dir` over a real library (`~/StudioHubModels`, a kit `model-library`) used to move real weights into the throwaway trash — deleting that dir destroyed them. The fix is `components/library_ownership.py`: the owner (data dir + machine) is recorded in `<library>/.studiohub/owner.json`, claimed by the first Hub whose data dir is not under a temp folder. A non-owner Hub, or one whose data dir is under `/tmp` / `/var/folders` / `$TMPDIR`, logs why and never shelves, retires, trashes or prunes the library, and weights are never moved from a real library into a temp trash. `qualification/library_guard.py` gives every test or script that starts a Hub a temp or copy-on-write library; `tests/test_no_real_library_launch.py` enforces it.

## MLX memory incident

**Historical record — the measurement story behind the AGENTS.md rules.**

An 8 GB Mac mini peaked at 6-7.7 GB on 2 GB models and wrote 100+ GB/hour of swap to its SSD. The cause was never the weights: one section's codec/VAE decode and an unbounded MLX buffer cache.

The 0.6.131 image floors were set by the owner for headroom: Klein and Z-Image at 2.8 GB free-before-start. The real 8 GB M1+M2 runs started cleanly from 3.5-4.2 GB free, peaks 2.2-3.0 GB. Klein's floor lives on its own catalog row and nowhere else: the Hub keeps no copy (the free number was removed from `memory_admission._FLEET_DEFAULTS` on 2026-10-19, because that table outranked the row and made an edit to the row invisible; the operator's override on the Models screen is the only other number, and `describe`'s `min_free_source` says which of the two is in force).

## Release flow history

**Historical implementation detail (0.6.198–0.6.202) — the current release discipline is in `AGENTS.md`.**

The flow used from 0.6.198 to 0.6.202: prepare the release on a `release/<version>` branch in its own worktree cut from `main`; merge each finished feature branch with `--no-ff`, no squash; write the plain-language release entry in `CHANGELOG.md` (packaging generates the in-app notes), bump `VERSION` last; stage and build the DMG; then integrate through `land/<version>-into-main` onto `main` (a fast-forward when the release was cut from `main`). Record start commit/worktree, owned files and completion proof; if no card is READY, the top of HANDOFF names the single task to resume instead of starting new work.

## Output defaults detail

**Operational detail — the summary in `AGENTS.md` is authoritative.**

Per-device generated-output defaults are editable: **20 GB of live output, kept 7 days, 5 GB minimum free disk** (owner rule 2026-10-19; 3 days from 0.6.117; were 50 GB / 30 days / 1 GB); operating defaults are configurable, while integrity/authentication/active-job safeguards remain enforced. Cleanup never deletes: it **moves** acknowledged output into the Hub's **Trash Folder** (`<hub data>/trash/<UTC date>/<batch>/`), a same-volume rename whose failure just skips the file. The Hub never empties the Trash Folder: the same cleanup timer hands items older than `trash_handoff_days` (default 7), and the oldest items while the unified Trash Folder (every root) is over `trash_handoff_gb` (default 20), to macOS Trash, where they can still be restored. `max_bytes` counts live output only. A settings file written before 0.6.117 that still holds the old defaults reads as the new ones, and one that still holds the untouched 3-day default of `defaults_version` 2 reads as 7.

## Fleet rollout and removal detail

**Operational detail — the summary in `AGENTS.md` is authoritative.**

A fleet rollout fans out — owner, 2026-10-26: *"it shouldn't block the rest. we can just keep updating or installing ... we can just fan it out."* `scripts/hub_rollout.py` publishes to every site in parallel, each with its own bounded gate: a busy site is re-polled on its own, an unreachable one is reported *deferred: unreachable* and retried inside its window, and a member that is offline or silent past the window is a warning in the receipt — none of them halts another site. The one rule that still binds: nothing is published into a site with work running, or with non-terminal Gateway jobs routed to it. A rerun never republishes.

Every removal goes to this project's `_trash/` — never `rm`. This covers *all* of it, not only what the owner asks for: a file he names, and equally a file you decide is clutter while cleaning up. Move it into `_trash/` (create the folder if absent), keep any sub-structure, and say where it went. `_trash/` itself is his to sort out: never delete or tidy anything *inside* it. The one thing that is not a file is a Git branch or remote ref — record its name and SHA in a note under `_trash/` before deleting it, and never rewrite published history.

One narrow carve-out: `.build/` scratch. Gitignored build scratch (staged bundles, runtime trees, downloads) may be reclaimed only through `packaging/prune-build-scratch.sh`, and only after that script has made its own verified copy on a kit drive. Moving it to `_trash/` frees nothing (same volume). Nothing else is exempt: every other removal still goes to `_trash/`.

## Disk governor detail

**Operational detail — the summary in `AGENTS.md` is authoritative.**

One governor keeps a Mac from filling (0.6.127 candidate; `components/hub/disk_governor.py`, owner decisions 2026-10-02): it runs in the Hub's cleanup thread at start-up and every 5 minutes. Tiers by free space: green >= 12%, amber < 12%, red < 6%, stop < 5 GB. Every tier: temp/partial files, leftover release downloads, orphan `.studiohub-old-*`/`-staged-*`, exactly one previous app, and the Trash Folder's size (measured in **physical** bytes, reported against a cap of `min(15 GB, 6% of the disk)`; the governor never empties it - APFS clones share blocks, so logical sizes are never used). Amber/red go on in this order until green: logs, stale `Updates/`, orphans (snapshot views of absent models, partial model staging, HF mirror entries of packages not installed), acked outputs oldest-first (red may take ones younger than the 7-day window). **Never touched:** installed models, unacked or leased outputs, any `_trash` folder. Unacked outputs older than 7 days move to the Trash Folder (ordinary retention). Job history (`hub.db` batches, worker `jobs.json`) is kept 90 days. The state is the `disk` object in `/api/health`, `/api/hub/resources` and each `/api/hub/fleet` row. A model download is refused (507) when it plus a 5 GB margin does not fit; a peer 507 makes the broker avoid that node for 5 minutes without using one of the item's tries; the service log rotates by size while running (16 MB x 2, `packaging/voice_worker.py`), and the rotated copy goes to the Trash Folder.

## Lite Mode detail

**Operational detail — the summary in `AGENTS.md` is authoritative.**

Workstation mode - shown to people as "Lite Mode" (owner, 2026-10-03/04, "Shops"; `components/hub/workstation.py`). Every user-visible string says Lite Mode; code, API paths, JSON keys, `workstation.json` and error codes keep "workstation". A shop's cashier Mac lends its idle time; it works the same on **every role** (owner, 2026-10-04: set the Mac up like any other, then tick Lite Mode): settings in `<hub data>/workstation.json` (default off), changed on the Mac itself in the app's Settings > Lite Mode (`POST /api/hub/workstation`, no restart) or from the Gateway through `POST /api/hub/fleet/nodes/{id}/workstation` (relayed to the node's `/api/hub/workstation`), published as `workstation` on `/api/hub/resources` and every fleet row. When on: worker processes at `PRIO_DARWIN_BG`; only `components/workstation_models.py` (one list); optional "only when idle" (HID idle >= `idle_minutes`, default 3, or screen locked) and "step aside" (input -> the current job **finishes**, the model is unloaded, no new work until `resume_after_minutes`, default 2, without input); quiet hours; no pop-ups / optional hidden menu-bar icon (the app reads the same file). A job starts on the model's own free-before-start floor or the Mac's `start_free_gb`; **a started job always finishes** except under macOS critical memory pressure for 20 s, when it is cancelled and handed back (`WORKSTATION_YIELDED`: the controller re-queues it on another free Mac or hands it to the Gateway at once, no try spent). A standing-by Mac is busy for routing (`peers.dispatch_ready`, capacity), never offline or failed. Every generation job the Mac runs locally is governed: peer work at the door (`standalone._peer_admission_refusal`), its own queues (broker, Subtitle, Chat) through the scheduler, which treats `"local"` like any Lite Mode Mac (`peers.dispatch_ready`/`standing_by`/`workstation_start_free_gb` read this Mac's own `workstation.status()`; `peers.lite_mode_allows` is the model allowlist) - a controller standing by keeps routing and sends its group's work to its nodes; with nowhere else to go the work waits (`governor_note` "Waiting: Lite Mode - ..."), and a job this Mac hands back is re-queued here on a standalone/node or handed to the Gateway on a controller. The pressure-yield loop watches peer jobs and `broker.local_running_jobs()`. Kit (owner, 2026-10-04; **simplified by the owner 2026-10-30**): a Lite Mode Mac is set up like any Mac (1 - Install the app, copy models, join) and takes the **Essentials 8 GB** preset on the app's **Set up this Mac** screen, then Lite Mode is ticked in Settings. The kit has **no** Lite Mode copy step: `copy-lite-models.command` / `2 - Copy the Lite Mode models.command` was retired and deleted, `qualification/copy_models_for_this_mac.py` no longer filters by `STUDIOHUB_WORKSTATION`, and `packaging/prepare-kit.sh` moves a drive's leftover copy to that volume's `_trash`. `components/workstation_models.py` is a **runtime allowlist**, not an install set - do not reintroduce a Lite-only model copy. The Shops controller is a regular controller that also generates: set it up like any Mac, then tick Lite Mode. The old `Set Up the Shops Controller.command` and `Set Up a Shop Mac.command` are retired; `prepare-kit-extras.sh` moves a drive's copies to `<volume>/_trash/Extras-retired/`. No file sets `STUDIOHUB_WORKSTATION` any more (the launcher and the copy filter that read it are gone, 2026-10-30). "Today: N jobs" counts every job this Mac starts in Lite Mode: peer door (`standalone`) and its own queues (`peers.note_dispatch` in broker, transcription, chat). A synchronous `/api/transcribe` is never cut.

## Remote render detail

**Operational detail — the summary in `AGENTS.md` is authoritative.**

The setting is `<data root>/render-authority/opt-in/unqualified.json` (owner-only, strict reader, absent/loosened/malformed = off). It is written **only** by `POST /api/hub/render-authority`, which the controller reaches through `POST /api/hub/fleet/nodes/{id}/render-authority` (Fleet token, dial first, the check-in queue when the Mac cannot be dialled). It is in `FLEET_ALLOWED` and not `NODE_ALLOWED`; no app screen sets it. Body: `{"accept_unqualified": bool, "requested_by": str, "encode"?: {...}}`; `requested_by` is required and recorded with the time.

**On** the loader offers a production-shaped authority labelled `accepted_unqualified` (provenance `owner-opt-in-unqualified`, who, when) for **one** encode: the (tier, encoder) pair the pinned compiler's first tier builds for the chosen `encode` (`render_encode.primary_pair`; default `DEFAULT_OPT_IN_ENCODE`). Tool identity, hardware class, descriptor pins and the memory floor (the profile's own process-tree limit) are the same checks a measured authority passes. A valid `enabled/` record always wins.

**Never a `measured` block.** The first real render on such a Mac writes the first real receipt (`receipts/<hw>.<hub job id>.<digest>.json`, real wall seconds, never clamped); a run whose receipt cannot be written is not published. Copying that receipt into `enabled/` is the designed promotion.

**One new optional row key.** `accepted_unqualified` is added to the leaf's capability row **only while on**; `fleet_nodes._render_capability` accepts the old key set or the old set plus that key. A 0.6.195 controller drops an opted-in row (zero render capacity, as before); every other combination keeps working. Do not add keys to the always-published row: the exact key set is the mixed-fleet contract.

Turning it off does not strand accepted work silently: a job accepted under it keeps its stored authority document, but a job not yet claimed is refused at claim.
