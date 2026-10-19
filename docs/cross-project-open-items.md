# Cross-project open items — the Studio Hub side

**What this file is.** The Studio Hub rows for the master open-items tracker GatewayKH keeps
(`_handoffs/2026-09-25-MASTER-open-items-tracker.md`, `5183355`). The master is theirs; this is the
half we own and keep current, so it can be pasted across rather than reconstructed from memory at
each relay.

**Why it is a file here and not only a message.** The failure this project has actually suffered is
not losing an item — it is *keeping* one after it stopped being true. Three examples, all from the
last two releases:

- `docs/tasks/HANDOFF.md` carried *"chat is absent from `auth._FLEET_STUDIO`, so a remote fleet
  caller gets 403"* for a release after it was fixed.
- The ST-14 review recorded *"the app ignores the warning"* about chat's below-tier warning; the app
  has rendered it all along, and the sentence could be reached at one end and drawn at the other
  with nothing joining them.
- The image lane contract published `185.61 s` for a configuration whose only stored record says
  `166 s`, and nothing in the tree held the 185.61.

Each was a tracker row that had become a second source of truth and quietly lied. So the rules below
exist to make a row cheap to *disprove*.

## The rules these rows follow

1. **Every row names where the truth lives** — a file and line, a route, or a commit. A reader
   verifies rather than trusts.
2. **Every row names how it was last verified**, as a command and its observed result. A status word
   is not evidence; `pytest tests/… → 1295 passed` is.
3. **A row is closed with a date and a commit** or an artefact, never with "done".
4. **If a row disagrees with the code, the code wins and the row is fixed.** Same clause as our
   HANDOFF, and the reason ours is short.
5. **Cross-project rows say who owes whom**, so "open" is never ambiguous at handoff time.
6. **Nothing is restated from a project's own tracker.** Link it. Two copies of one status is one
   copy too many.
7. **`.models[0]` is not a comparable row across two endpoints.** GatewayKH measured three surfaces
   by taking the first element of each list and concluded the surfaces disagreed; index 0 was a
   *voice* model on one, a *chat* model on another and a *music* model on the third, so they had
   measured a **lane** difference and written it up as a surface difference. Compare by model id, or
   do not compare. This is a general trap, not theirs: any two endpoints that return a list will
   differ at index 0 for reasons that have nothing to do with either endpoint.

## Answered and shipped in 0.6.70 — GatewayKH's asks, both closed on release

**Neither is waiting on anything from us.** SH-19 and SH-20 are answered, in the build, installed, on
all four drives and pushed. The only remaining action is **theirs**: refresh discovery so the cached
`deadline_seconds: 900` is replaced by the per-generator numbers. That is not an open item here.

| # | Item | Owed by | Answer |
|---|---|---|---|
| SH-20 | **The voice lane's deadline, which their HUB-19 table left at 900 s.** Their note says it outright: *"my last ask — the voice lane stays at 900 s in their new table, and voice is the lane that actually failed us (a ~17-minute narration part killed at 900 s)."* This is not HUB-18/19/20/21/22; it is a row of its own, and the answer is either "raise voice" or "voice stays, and Story Studio lives with the loud failure." | Studio Hub | **Answered: raised to 1,800 s.** See below. |

**Answered 2026-09-26: voice's floor is 1,800 s.** `MODALITY_DEADLINE_FLOOR_S["voice"] = 1800.0`, in the same change that made the deadline per-generator. **1,800 rather than 1,200**, and the reason is not round numbers: 1,200 is only three minutes of headroom over the 17-minute part that already died, and that part ran on one Mac — the same narration on a slower node among the fleet's 35 eight-GB and 15 sixteen-GB machines takes longer, so "just enough" buys the identical failure again at eighteen minutes. 1,800 is **1.76x** the observed worst case, and it matches chat, so the two long-form lanes share one number.

**The reason a 30-minute ceiling is safe now and was not before:** `stall.py` catches a worker that has gone quiet in roughly two minutes *whatever the ceiling is*. The ceiling no longer doubles as a failure detector, which is the only thing that made the shorter number attractive.

**Evidence label, stated plainly: 17 minutes is an observation of a failure, not a clean measurement, and 1.76x is inference.** The right end state is to measure a real voice part and derive this the way Z-Image's 8 GB floor was derived from the owner's run. Until then this is a defensible bound, not a measured one.

## Closed — Studio Hub owed, and this one is now empty

**SH-7 is done, and it was the last item owed.** Nothing on the Studio Hub side needs a machine any
more.

| # | Item | Closed | By |
|---|---|---|---|
| SH-7 | **The 8 GB Z-Image-Turbo 6B MLX 4-bit run — the tier PASSES.** | 2026-09-26 | The owner's run on an **Apple M1 Mac mini (`Macmini9,1`), 8.0 GB, macOS 26.6.2**, engine **mflux 0.20.0** from the installed app, model pinned at `b3a8f311…`, seed 42, 9 steps. Four configurations, every one `OK`, each with a real PNG: **1024×1024 low-RAM** 392 s at 5.70 GB peak MLX / 5.85 GB footprint; **1280×720 low-RAM** 336 s at 5.70 / 5.85; **1024×1024 low-RAM + vae-tile 256** at 4.95 / 5.50; **1024×1024 default** at 8.93 / 8.72. Evidence in the owner's iCloud at `StudioHub Z-Image 8GB Run/` — `VERDICT.txt`, `results.tsv`, `host.txt`, per-run logs and `.swap-peak` records, four hashed images. |

**Verified here rather than accepted from the summary**, because the summary was the claim:
the four PNGs are real and correctly sized (`file` reports 1024×1024 ×3 and 1280×720 ×1); the log's
peak-footprint bytes (`6281456768`) match `results.tsv` exactly; the `.swap-peak` records match the
table; and both images sampled render clean, legible text rather than noise.

**The one reading in that verdict that is wrong, and it matters.** `VERDICT.txt` says run 04's
completion *"contradicts the expected result"* and that the app's low-RAM rule *"was not needed
here."* It was needed. The default run finished with an **8.72 GB footprint on an 8.0 GB machine**
and **3443 MB of peak swap** — about 2.7× the low-RAM runs' 1274–1857 MB. **It fit by paging, not by
fitting.** So `Z_IMAGE_LOW_RAM_BELOW_BYTES` stays at 24 GB, and the measured outcome is recorded
beside it in `generation.py` so the completion cannot later be read as licence to drop the rule.

**What this settles:** the 8 GB floor is real for this model. The manifest's
`minimum_unified_memory_gb` moves **16 → 8** and its `status` **unqualified → qualified**, with the
measured numbers and the mandatory-low-RAM caveat in `memory_evidence`. Note what it does *not*
settle: low-RAM still swaps 1.3–1.9 GB on 8 GB, so this tier runs on **5.6–6.5 minutes per image
with disk paging**, and it says nothing about the 8 GB tier running video — which needs a different
model, a different runtime, and its own measurement.

**Needing a machine, not code: nothing, for the first time.** SH-6's 16 GB tier is measured and its
8 GB tier is a documented exclusion.

## Nothing is owed TO Studio Hub — this section is now empty

**Both rows that stood here were answered by GatewayKH on 2026-09-25, and the answers were never
copied back.** The section stayed up for a day showing Studio Hub as waiting on work that was
already done — the exact failure rule 4 exists to prevent, arriving from the other direction.

| # | Item | Closed | Answer |
|---|---|---|---|
| SH-10 | The chat-lane handoff document had never been read by Studio Hub | 2026-09-25 | **The one-line summary was the whole of it.** Its single standing ask was the exposure notification, which became HUB-12, and HUB-12 is closed. Nothing else in it asked us for anything. |
| SH-12 | Whether GatewayKH wants `--fleet-mode` wired into its install flow | 2026-09-25 | **Yes.** The kit's install step runs `open -a "Studio Hub" --args --fleet-mode` — the one call that makes a freshly-provisioned Mac reachable after a reboot with nobody opening the app. **Sleep stays out of scope**: holding a Mac awake is a power decision, so where a site wants always-reachable nodes the kit sets `pmset` as its own visible step. |

## Owed BY Studio Hub — the inbound rows, in one place

**These are the rows GatewayKH's master tracker numbers `HUB-n`.** They are listed here because
**ten of them previously appeared in no file in this repository** — the entire inbound debt list was
invisible to the repo that owes it, and only HUB-18 was reachable at all. GatewayKH wrote out the
four it had noticed and asked us to say in one line whether our tracker deliberately held only our
own rows; this section is that line, and the answer is that it did not, and now it does not.

The master tracker is still theirs (rule 6). This is the half we own, with our own status.

| # | What is owed | Status |
|---|---|---|
| **HUB-20** | Publish the limit's **source as a stable token**, not as a sentence. `_published_max_output_tokens` returned `entry.get("source")`, and the wire carried free prose (`"worker default: the checkpoint ships no generation_config.json"`) where Story Studio needs to *branch* on a value. **Highest of the inbound rows** — Story Studio prints the number without being able to label it. | **Closed 2026-09-26** — shipped in **0.6.72**: every `model_limits` entry now carries `source_kind` (`model` / `curated` / `worker_default` / `unknown`) beside its sentence, set in the same branch so the pair cannot drift, and `tests/test_chat_worker.py` pins both the checkpoint-stated and the worker-default case. Its value is on the wire as `main.py` publishes it: `max_output_tokens: 128000` for the local chat model, whose checkpoint states no output figure (it was 16384 when this closed, and 4096 before that; the constant is `llm_engine.FALLBACK_MAX_OUTPUT_TOKENS`). GatewayKH relays the token as `max_output_tokens_source` (their **0.2.48**), so it reaches Story Studio; **their app does not yet read it**, which is the remaining half and theirs. |
| **HUB-21** | Publish `max_structured_items_per_request` on the chat lane. Today `lane_contract.structured_output` carries `formats`, `enforcement`, `grammar_constrained`, `repair_attempts` and `note` — not this field. It is in **no installed bundle and no gateway.** | Open, **no longer blocking** — see below |
| **HUB-22** | State what an omitted parameter means, and on which Mac. `_resolved_parameters` resolves each default through `model_defaults.resolved(data_dir, model_id, …)` **per Mac**, and publishes the in-force value as `default`. Story Studio wants a `defaults_scope`. | Open (a statement) |
| **HUB-18** | A completion cut off at the worker default still reports `finish_reason: "stop"`, so a truncated answer is indistinguishable from a finished one. Measured from GatewayKH's own job store: **4 of 13 `Qwen3.5-9B-4bit` jobs stopped at exactly 4,096 completion tokens and all 13 reported `stop`.** | Open |
| **HUB-8** | Validate `lane_contract` **on publish**. Absent is safe (callers fall back); **malformed is worse because it looks authoritative.** Omit what cannot be validated. | Open |
| **HUB-9** | `model_limits` on non-chat lanes where a model states a maximum (music, video). GatewayKH would publish it instead of its own cap. | Open |
| **HUB-10** | State per-lane `uncertain_is_terminal` semantics **in prose**. The flag exists; their poll loop holds `uncertain` unconditionally until the lanes are named, so they cannot act on it without a hardcoded list. | Open |
| **HUB-13** | Make a **held release clearable**, and re-word the banner announcing it. `POST /api/hub/release` stores one; no route or control clears it. A held 1.28 GB `0.6.50` DMG made Connections read *"Older release available — this Mac is on a newer version"* while the app ran 0.6.63. | Open |
| **HUB-14** | Install a pushed release **without a click**, once the Mac is safely drained. The *push* half needs nobody; the *install* half is a button on Connections, on every Mac, for ~50 Macs. | Open |
| **HUB-15** | Get a held release **onto the nodes**. `peers.release_reconciler` is a module global nothing ever assigns, and a node's `GET /api/hub/release` reads its **own local files**, so a node shows nothing and `wake_peer` is dead code. | Open |
| **HUB-16** | A **manual listen tick silently drops every address not ticked**, and nothing on the page says so. `pinListenHosts` ends automatic mode; a controller's tailnet address was lost this way, and the gateway dials the address the location was added with (all five are stored as `100.x`). | Open |
| **HUB-17** | Placement ordered work by **modality instead of the model's own floor**, so an 8 GB-floor TTS model took a 32 GB Mac. | **Closed 2026-09-26** — `_placement_key` is now one factory for every lane. GatewayKH's master tracker still shows this open; their row is the stale one. |

**HUB-21's status changed on 2026-09-26, and the change is on Story Studio's side, not ours.**
GatewayKH's row says the consequence lands in Story Studio — their scene-plan generator *throws*
when the field is absent (their STORY-10). **That half is now fixed and shipped** (Story Studio
`38b87ae3`, v1.4.225, installed): an unpublished item limit no longer refuses the pack, bounded
instead by the model's published output tokens. So publishing this field is now an **improvement**
— it would let a lane state a real bound — rather than the thing unblocking a customer. It should
not be scheduled as urgent on our side.

## SH-19 in detail — the verification record, kept because it is how the defect was found

| # | Item | Owed by | Answer |
|---|---|---|---|
| SH-19 | **The Hub's peer-proxy deadline is 900 s, and that wall kills every video job.** GatewayKH raised this on 2026-09-26 and asked Studio Hub to raise it, since the Hub is the only party that applies it. | Studio Hub | A raised `PEER_PROXY_TIMEOUT_S`; they then refresh discovery and both sides move together |

**SHIPPED in 0.6.70 (`7eb09ac`)** — installed, on all four drives `RESULT: PASS`, pushed. The
constant below no longer exists: it was replaced by per-generator floors derived from one function.
The description that follows is kept because it is how the defect was found and verified.

**Verified here independently, not accepted on their word, at the time it was raised.**
`components/hub/standalone.py:41` **was** `PEER_PROXY_TIMEOUT_S = 900.0`, both **enforced** and **published**: line 2144 passes it as
`timeout=httpx.Timeout(PEER_PROXY_TIMEOUT_S, connect=5.0)` on the proxied job request, and line 333
spreads it into every lane contract as `deadline_seconds`. So the deadline a scheduler reads is
exactly the deadline the Hub holds the job to — their reading of our code is correct, and their
decision not to carry a second copy (`JOB_DEADLINE_S` as fallback only) is the right shape.

**Why this is now urgent, and not merely tidy: video cannot ship under a 900 s wall.** The LTX-2.3
runtime's own validated runs on M2 Pro 32 GB hardware are **44:38** for q8 HQ at 480×704×97 and
**~15 min** for the identity test at 1280×704×97 — that is 2,678 s and 900 s against a 900 s wall.
The first is **3× over** and the second sits exactly on it. Every video generation on this fleet
would be cut off mid-flight by the Hub, and `deadline_seconds` would have advertised the wall to any
scheduler that read it.

**One trap removed while verifying, which is why this row is not simply "raise a number":**
`tests/test_hub_model_packages.py:487` asserted `contract["deadline_seconds"] == 900.0` — a literal
that any deliberate raise would break, in a test whose entire purpose is to prove the deadline comes
from one place. It now asserts the **join** against `standalone.PEER_PROXY_TIMEOUT_S`, so it still
fails if the Hub stops publishing its own constant, and no longer fails when the constant is
deliberately changed. `docs/tasks/ST-20.md` had the same defect in prose — it said "the 900 s" and
pointed at `standalone.py:1867`, a line that no longer exists; it now names the constant and states
that it deliberately does not restate the value.

**The value is the owner's call, not ours to pick.** It is one constant for every lane, so it is a
product decision: video's longest measured run is ~45 min, so **2 hours (7,200 s)** gives 2.7×
headroom and covers every other lane without them noticing. A per-lane deadline was considered and
rejected — the Hub's own comment records "one constant owned by the party that enforces it", and a
second copy is the defect this design exists to avoid.

## Closed, with what closed them

| # | Item | Closed | By |
|---|---|---|---|
| — | Chat dispatchable by a Fleet credential (review finding F6) | 2026-09-25 | `7267e62`; `tests/test_chat_hub_wiring.py` |
| — | The listen setter GatewayKH asked to be named | 2026-09-25 | `--listen-auto`; automatic by default; `7fcb0fb` |
| — | The bind as a readable fact | 2026-09-25 | `/api/health` + `/api/hub/settings`, one helper; `154f0b4` |
| SH-11 | The Story Studio id / two-pass question | 2026-09-25 | Answered by `lane_contract.structured_output`; GatewayKH asked only for the **version**, and confirmed the absence themselves (`git show a4a78dc:… \| grep -c structured_output` → 0). Ships in **0.6.64**, commit `363e50e`. **Correction to their note:** it is no longer unpushed — `363e50e` is an ancestor of `origin/main` (`2bb74f7`) as of this release |
| SH-1 | The Models tab, and per-model defaults for four of five lanes | 2026-09-25 | `308ff36`, `3e62bc8`, `f8efed4`; `bash native/build.sh` + 12/12 negative controls; two app-side consumptions remain — see SH-14 |
| SH-2 | Release 0.6.64 — reachability, node-local generation, the bind, the Models tab, `structured_output` | 2026-09-25 | `fabe498` installed, on all four kits, `RESULT: PASS`, pushed |
| SH-5 | The model-exposure approval surface has no caller | 2026-09-25 | **Closed by the answer to HUB-12**: the gateway does not need one, verified from both sides. `approve()`/`is_approved()` have zero callers, nothing is gated by exposure, and an unapproved model still publishes through `_model_capability`'s direct-evidence path |
| SH-9 | Video | — | **Shelved by the owner, then deliberately raised by him on 2026-09-26 — the phase is open work again.** The shelving is kept here as the decision it was; what follows supersedes it. The owner's instruction: *"let's begin we the video phase now. we can circle back later for the z-image 8gb. proceed."* Two facts established from the **installed** build before any design work, both checkable: the engine the Image lane ships, **mflux 0.20.0**, exposes 30 `mflux-generate-*` commands and **every one is an image model** — there is no text-to-video and no image-to-video command in it; and the only video-adjacent model present, `mflux-upscale-seedvr2` (**SeedVR2 3B MLX**, optional 7B), is an **image** upscaler in mflux despite its upstream lineage, taking `--image-path` and documented as "highly faithful to the original **image**". The Wan 2.2 3D VAE classes under `models/fibo/` are borrowed for FIBO's image VAE, not a video pipeline. **So video is a new engine, not an extension of the shipped one** — the phase carries a runtime dependency the Image phase did not. Reusable: mflux publishes `mflux-capabilities`, a machine-readable dump of what each CLI honours, discovered from installed console scripts and introspected from the live parsers with the honoured/ignored split read from the same constants the runtime warnings use — a published contract to consume rather than transcribe |
| HUB-12 | Model exposure becoming an approval step — *"tell us if it does"* | 2026-09-25 | **Closed by GatewayKH answering it from our own code**, and verified here rather than accepted. Four checks reproduce: `approve()` and `is_approved()` have **zero callers** anywhere in `components/` or `packaging/`; there is **no** exposure reference in `standalone.py` at all; `model_exposures.json` **does not exist** on this Mac, which is what "no caller" predicts; and the dispatch gate (`standalone.py:444`) refuses on `MODEL_MODALITY_MISMATCH` / `MODEL_NOT_READY` (files, runtime, macOS, chip) plus studio liveness, with exposure absent. `_model_capability` builds availability from the worker's own `available` / `runtime_ready` / `memory_eligible` / `execution_ready` booleans — the candidate block is read as evidence and withholds nothing. **No work, nothing to read.** They also dropped their own standing watch deliberately: if it ever became a gate the Hub would answer `MODEL_NOT_READY` at submit, which their adapter already maps to `LOCATION_NOT_READY` and re-routes — a lost re-route, not a lost job |
| SH-13 | The lane-contract key-set "difference" | 2026-09-25 | **Retracted by GatewayKH as their measurement error**, and the retraction is the good kind: they showed what `.models[0]` actually was on each surface (voice / chat / music) and re-ran it joined by model id — 21 model ids on both surfaces, packages adds `deadline_seconds` and drops nothing, 21 of 21; `dispatch` is Voice's own field on both surfaces, 13 of 13. No defect, no spec line. Verified here independently: `deadline_seconds` is added by spread at `standalone.py:333`, and `\"dispatch\"` appears in exactly two files, both Voice |
| SH-16 | **`preset_speaker` was published by no voice lane contract** — accepted and read by the worker, sent by the app on every preset job, absent from every family's `parameters`, so a scheduler reading only the contract could not learn how a speaking voice is chosen. | 2026-09-25 | **Fixed on both sides, because publishing it alone would have created a default that could not be reached.** Producer: the roster moved to one home (`catalog.PresetRoster`, carried on the family, one source for the picker payload and the contract) and `lane_contract._preset_speaker_parameter` publishes `preset_speaker` with the family's own ids as the enum — for a model that offers preset **and** has a roster, and for no other. Khmer (single built-in voice), Pocket TTS (roster is a won't-fix) and every cloning tier publish nothing, per the lane-discards rule. **No shipped row qualifies today**, because the roster's own model was pruned in ST-13.B; a CustomVoice tier returning to the catalogue publishes it with no code change. App: `HubAudioParams.presetSpeaker` was a non-optional `String` carrying `""` or a name **seeded from the roster** — the same defect as the pace — so a voice stored in the Models tab could never reach the engine and an untouched picker looked like a choice; it is optional now and only a voice the operator actually chose from a roster this model publishes is sent. Two tests that pinned the seeding are replaced by that invariant. |
| HUB-17 | **Placement ordered audio onto the biggest Macs by *modality*, not by the model's requirement** — so the 8 GB-floor Qwen3-TTS 0.6B took a 32 GB Mac that a Z-Image-class job needed, while the models that genuinely need a big Mac (Qwen3-TTS 1.7B at 12 GB, VoxCPM2 at 16 GB, Fish Audio at 24–32 GB) queued behind it. The owner found it and restated the rule precisely: *"the qwen3 tts 0.6 can run on 8gb machine so it must take up the small ones first. but qwen3 tts 1.7b needs 16+ so it will wait for freed up 16+ machines… this should be generator specific at all. instead this priority case should be model - ram requirement specific."* **He is right, and the data was already published.** | 2026-09-26 | **Fixed by deleting the branch rather than adding one.** `_placement_key` is now a single factory for every lane: a machine below **this model's** published floor sorts after every machine that can run it, and among those the smallest comes first. `_audio_placement_key` and the artifact-class test are gone, so there is no modality rule left to drift. His two cases are pinned as tests: an 8 GB-floor model prefers the 8 GB Mac, and a 16 GB-floor model is **not** offered it. Two tests that asserted the old policy ("audio and video artifacts prefer the large machines", "image prefers the small machine") were replaced by the invariant they should have asserted — one rule, checked across every lane. |
| — | **The TTS prune** | 2026-09-26 | **No work — the owner confirmed the set is already what he wants.** A TTS prune ran on 2026-09-24 (`~/StudioHubModels/_trash-tts-prune-2026-09-24`, now empty) and removed the nine packages that were broken or absent on his Mac, leaving 8 voice models there and 16 voice packages on the drives. Checked before starting: **all four drives hold 24 receipts with 0 missing folders, and every package present is still shipped, name for name** — so the prune had nothing to move and nothing to forget. The prune *feature* stays (`qualification/copy_models_for_this_mac.py::plan_prune`); it only ever removes whole models the kit stops shipping, never one a given Mac merely cannot run, which is a separate decision nobody has asked for. |
| SH-6 | **Chat's memory tier: 16 GB QUALIFIED by measurement, 8 GB EXCLUDED by the owner's decision.** Three 16 GB Macs (M1, M2, M4) each completed a 256-token completion: 12.40-12.48 / 14.14 / 18.33-18.60 tok/s, peak MLX 5.23 GB on all three (identical - the model's footprint, not the host's), load 9.08-13.69 s. 8 GB does not run it, so the tier is excluded: the harness refuses an 8 GB Mac immediately (verified 0 s, exit 3), the copy step never gives it to an 8 GB Mac, and the app says it does not run there. | 2026-09-26 | Three verdict blocks in the owner's `StudioHub Chat Tier Run m{1,2,4}-16*` folders; `qualification/README-chat-tiers.md`; the manifest carries it as `status: qualified` |
| — | **Five releases shipped the wrong Image engine** (found by the owner's 8 GB Z-Image run) | 2026-09-26 | 0.6.59–0.6.62 shipped mflux **0.20.0**; **0.6.63–0.6.67 shipped 0.17.5**, because the release loop in `HANDOFF.md` named `.build/st03-image-runtime-worker` — the superseded tree kept as the ST-03 receipt — and that line was never re-checked after 0.20.0 was built. Every Image number published in those five releases described an engine the app did not contain. Fixed in **0.6.68**, verified by content digest: `.build/image-runtime-mflux020` is byte-for-byte the tree 0.6.62 shipped (`c3acf766…`). **Guarded** — `stage_voice.py` now pins the qualified runtime by digest and refuses any other, naming both digests and the path to use; staging an unqualified runtime requires `--unqualified-image-runtime REASON` and the bundle records `runtime_qualified: false`. A version string would not have caught it: both trees carry an `mflux` package and accept the same commands. |
| SH-4 | **FLUX.2 Klein 4B MLX 4-bit's `79.34 s` traced to nothing** — and it was worse than a missing source: the scene the row described was recorded, at a different number, on a different host. | 2026-09-26 | **The number was never in the tree.** The stored run of that exact scene — same model, same pinned revision, 1280x720, 4 steps, guidance 1.0, seed 42 — is **105.943 s**, in `qualification/st03-image-real-inference.md` and again as `worker_terminal.duration_seconds` in `evidence/st03-image-real-inference.jsonl` (job `af4913b2`, 2026-09-15). Two records agreed; the published figure appeared in **no file in this repository**. The host was wrong too: the row said a 32 GiB M2 Max and mflux 0.20.0, and the record says **"Captured on Apple M4, 16 GiB RAM, macOS 26.6.2"** — a *better* fact, because it is a measurement on the tier the fleet runs rather than on the one machine. Also corrected: `qualification.packaged_inference` said `pending` while this completed packaged run sat in the tree. **Guarded**: `tests/test_image_loading_mode.py::test_every_published_runtime_is_found_in_the_record_it_names` opens the file each row names and fails if the figure is not in it — proved to fail on the old 79.34 and pass on 105.94 |
| SH-8 | **`max_output_duration_s` was published by no family.** The shape, the API field and its validator were real and the worker genuinely enforced it — the dispatcher turns it into Qwen's audio-token ceiling and the quality path refuses a finished WAV that exceeds it — but no family named it, so **no lane contract offered a bound that was being applied**. | 2026-09-26 | **A family declares it now.** The `qwen3-tts` family names it in `lane_parameters`, which is where it is honoured (Base clones and the other Qwen3-TTS tiers); no other family declares it, which is correct because they accept the field and ignore it, and a lane must not advertise a key it throws away. **Guarded**: `test_every_parameter_shape_reaches_a_published_contract` — a set difference, not a list of names, so nobody has to remember it when the next shape is added. Proved to fail naming exactly `max_output_duration_s` when the declaration is removed |
| SH-18 | **The rule that keeps a lane-stated default from swallowing a stored one lived in a comment.** | 2026-09-26 | **Pinned as a rule, and the exception is Voice's alone** — verified rather than assumed: Image and Music fill an omitted field in `validate_request`, Subtitle in `with_model_defaults`, Chat in `_resolved_parameters`, all unconditionally. Voice's `_job_parameters` is the only place that leaves a field alone, and only when the lane *states* the value. `TestLaneDecidesBoundary` (4 tests) pins all four cases: stored applied when the lane states none, left for the engine when the lane states it, **not swallowed** when the operator stored something else, and a supplied value always winning. **Proved**: widening the condition to `if params[name] is None` — the mutation that would silently kill every optional parameter's stored default — fails 6 tests. The class is named `Test*` because the first version was not, and pytest collected nothing: a guard the runner skips is the defect it was written to prevent |
| SH-17 | **Consumers that enumerated voice rosters or families by hand** — the web picker read the exact key `qwen3_preset_speakers`, and `native/StudioAPI.swift` fell back to `family == "qwen3-tts"` for the reference-transcript requirement. | 2026-09-26 | **Both fixed, and the rule is pinned.** The web picker (`components/voice/app/frontend/app.js`, which *is* staged into the bundle) now discovers every roster by the **shape** of its key — `<family>_preset_speakers` — exactly as the native app always has, and keeps what it found; a second roster family would have been published, shipped and correct, and simply invisible. The native fallback is gone: a transcript is required only where a **producer states** it, and the catalogue states `'required'` for both Qwen3-TTS Base tiers. Checked against every shipped cloning model before removing it — the two that need a transcript both publish `'required'`, and every model that fell back to the family name resolved to `'optional'` anyway, so the change alters nothing today. `test_every_roster_consumer_joins_on_the_published_key_shape` pins both. **Left deliberately:** the three `qualification/` dev scripts keep their own family→key tables; they are not shipped, and the first fails loudly for an unknown family rather than guessing. |
| SH-3 | **Voice published no below-tier warning** — three missing links (no sentence on the row, no `NativeModel` wiring, no render site), and it could not have fired anyway. | 2026-09-26 | **All three links built, and the reason the warning was impossible found underneath them.** `catalog.serialize_model` now publishes `memory_warning` under the same key and in the same place as the other four Studios, derived from the `fit` verdict the worker already computes (`risky` is this worker's own answer; its `hint` already names both numbers, so there is no second threshold). The audio `NativeModel` carries it and the Audio page draws it with the same triangle the other pages use — **advice, never a refusal**. The deeper defect: `components/voice/app/backend/system_info.py` called a **bare `sysctl`**, which is in `/usr/sbin` and not on PATH for a packaged worker *or* for this project's own shell, and `_sysctl` swallowed the failure — so `detect_memory_gb()` and `detect_chip()` were both None and **every model's fit verdict read `unknown`, fleet-wide**. Probed by absolute path now, reporting this Mac's 32 GB and M2 Max. **Guarded**: `tests/test_below_tier_warning.py` requires five publishing workers and a render site for each (its expectation finally includes Voice), and `components/voice/app/tests/test_system_info.py` pins the probe against a `PATH` with no `sysctl`; proved by reverting it to the bare name, which fails two of the three. |
| SH-15 | **The `native/build.sh` cancellation flake — hardened, deliberately not claimed as fixed.** Seen once: *"the in-flight read must have been torn down by the cancellation"*, passing on an immediate re-run with `native/` provably unmodified. | 2026-09-26 | **A deliberate reproduction was attempted and it did not reproduce**: 60 runs of the native suite with the pre-fix behaviour restored, and 40 with the fix in place — 100 runs, zero failures. Two real timing races were found in the assertion's own test and both are removed: the hold's semaphore was process-wide, so a `signal()` arriving after its hold had ended stayed in its count and would release the *next* hold early; and the assertion read the cancellation count once, racing the `stopLoading` callback that increments it on URLSession's delegate queue. **The original failure remains unattributed**, and the code comment says so rather than claiming a cause. Closed as hardened with the evidence recorded: if it recurs, the log-preserving work means it will leave more to read than it did. |
| — | Subtitle's warning published where nothing read it | 2026-09-25 | `9506c8c` |
| — | The staging guard refusing a bundle over a dead import | 2026-09-25 | `95ad36e` |
| SH-14 | **The page control is the override, and the model's own default is what it overrides** — the owner's ruling for every generator: *"we have a default but control on the page is overwrite… stuff that can be change or altered such as speed must be given to the user."* Closed by making an untouched control supply **nothing**, so the Models tab's stored value is what the worker applies, and by reading ranges and values from the lane contract the producer publishes. Voice's `speed` was the named case: the app sent a flat `1.0` on every job, so a `speed` default set in Models could never reach the engine *from the app*. Subtitle's `word_timestamps` was worse — a persisted `false`, submitted on every request, with **no control anywhere in the app**, so the Models value was unreachable and the operator could not reach it either; it now has a toggle, offered only where the model's runtime claims word timings. The second half is closed too: the weight format is the row's own `weight_format`, not the extension of a file name (`index, safetensors` was never a format). Found alongside it: the app sent `language` to **12 of 13** voice families whose lane publishes no such parameter — the lane discards it — so the field is now offered only where the lane publishes one, and a lane that *requires* one is refused clearly rather than filled in with an invented code. Three tests that pinned the old behaviour (`var speed: Double`, "every audio path sends `speed: audioSpeed`", "the slider uses the audited range" declaring `0.5...2.0` in the app) were **replaced by the invariant they should have asserted** — they were green while the defect shipped | 2026-09-25 | `9859855`; `GenerationDefaultOwnershipTests` in `tests/test_peer_path_contract.py` (6 tests); the native pace, timestamp and weight-format assertions in `tests/native/api_main.swift`; worker halves already proven by `components/voice/app/tests/test_model_defaults.py` and `tests/test_subtitle_standalone.py` |
