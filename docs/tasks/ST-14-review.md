# ST-14 — second-pass independent review, 2026-09-25

Reviewer: independent of the Chat implementation. Reviewed revision: `457fc27` (clean when the
review started), shipped as 0.6.62/`3e1a0eb203d63b72e67807ddbcd1d836f291f3a5`; the installed app and
all four kit apps are that build. **The tree advanced during the review** to `d8b7ca6` (two other
workers: `bbc6b0d` native Chat Studio shape, `d8b7ca6` the Verify fix), which is why that fix is in
the tree and not in the bundle — see F2. This is the **second** pass:
[`qualification/st14-review-2026-09-24.md`](../../qualification/st14-review-2026-09-24.md) reviewed
`9ef3ac6` (0.6.57/0.6.58), and its ten findings are re-checked in §3.

Labels: **[M]** measured on this Mac (Apple M2 Max, Mac14,13, 32 GiB, macOS 27.0), **[V]** vendor
claim, **[I]** inference. Every [M] here was run against the **shipped 0.6.62 bundle** or the
current tree; the scripts and raw output are in
`qualification/evidence/st14-review-2026-09-25/`. I changed no worker, Hub, native or packaging
code — this is a review, not a rewrite. Only the card, this file, the bridge note and the new
harness/README were written.

> **The tree moved while this review ran.** It was clean at `457fc27` when I started; two other
> workers then committed `bbc6b0d` and `d8b7ca6` and left further uncommitted edits in
> `native/StudioAPI.swift`, `native/StudioViews.swift`, `tests/native/api_main.swift`,
> `tests/test_chat_native_wiring.py` and `tests/test_audio_layout.py`. I did not touch any of it,
> and the focused chat gate was green with those edits present (69 → **70** passed as they added a
> behavioural native test). Anything they change about the Chat page is theirs, not part of this
> review's snapshot; the one place their work changes a finding is F2/F3/F9, noted there.

---

## 1. Verdict

**The wiring is real, the packaging is real, and one claim in the card is simply false — the kit
blocker it still carries.** In detail:

- **The 0.6.61/0.6.62 changes did not break the lane.** The shipped bundle carries
  `components.chat` (`runtime: voice`, digest `8524ca23…`), the Hub starts all five workers, and
  `/api/hub/model-packages` answers **HTTP 200** with exactly one chat row (`qwen3-5-9b-4bit`,
  engine `chat-mlx`, worker `chat`). The 0.6.57 `KeyError: 'chat-mlx'` 500 (the first review's F1)
  is fixed and stays fixed. **[M]**
- **The aggregate catalog is exactly what the card says:** one chat row,
  `mlx-community/Qwen3.5-9B-4bit`, `capabilities: ["chat.completion"]`, `context_window_tokens`
  262144 (source `model/config.json text_config.max_position_embeddings`), `max_output_tokens`
  4096 (source: *worker default — the checkpoint ships no `generation_config.json`*). **[M]**
- **ST-14.6 is true and its closing note is false.** All four `KH-STUDIO-*` drives are mounted and
  stocked: 24 installed / 24 advertised, the chat receipt present with the exact
  `manifest_digest`, all eleven model files size-matched, the kit app at 0.6.62/`3e1a0eb` **[M]**.
  The card's closing note ("the four SSDs were unmounted, so the kit libraries could not be
  stocked") is stale, and the *same card* already says so three sections earlier. Corrected.
- **ST-14.2's receipt: absent when this review measured it (12:2x), present from 12:27.** The
  bytes were always there and complete; at the moment of measurement the receipt was not, so the
  shipped app read `files_state: not_installed`, `dispatch_state: ineligible`, and the Chat page's
  Send stayed disabled **[M]**. The button meant to create that receipt is fixed by commit
  `d8b7ca6`, which is in the tree but **not in the shipped 0.6.62 bundle** (built 11:34, before
  that commit at 12:30) — so the shipped Verify Files could not have written it, and the receipt
  was written by running the fixed code against the library. See the correction below.

  **CORRECTION (coordinator, 2026-09-25, after this review shipped):** the receipt is now **present**. The verify-path worker's run wrote it into `~/StudioHubModels/.studiohub/installed.json` — digest `68a3cf02…` == the manifest digest, 11 files recorded — and the **installed** 0.6.62 app reads it through its own preflight path and reports `files_verified` / `dispatch_state: eligible` / `worker_state: running`. Verified end to end against the unmodified installed app: `POST /api/hub/chat/completions` → **HTTP 200 in 11.98 s**, answer `'READY'`, usage 17/1/18, `finish_reason: stop`. So the Chat page can send on this Mac today. What remains true is narrower: the **Verify button** in the shipped build is still the old code, so it could not have created that receipt — it was created by running the fixed code against the library. A package with no receipt is still a dead end until the next build; a package that has one, as this one now does, is read correctly by the shipped app. The lesson worth keeping: this review inferred "no receipt" from the shipped code rather than re-reading the index after the other worker had run, and the inference was wrong.
- **The 8 GB question has a different answer than the card implies.** Nothing in the shipped
  product refuses on memory tier: the worker publishes the manifest's 16 GB as a
  `memory_warning` while `eligible` stays true, the Hub's `effective_min_total_memory_gb` is
  deliberately `0` with no operator override, and the native gate ignores the tier entirely
  **[M, code]**. So an 8 GB Mac does **not** refuse honestly today — it warns and tries. That is
  now a measurable question, and the harness in §5 is the instrument.
- **The implementation record's own numbers are stale, not wrong.** Test counts, the 0.6.56
  bundle identity, and the kit figures all moved after 0.6.57–0.6.62. Corrected in the card.

---

## 2. Method

| What | How |
| --- | --- |
| Shipped bundle | Packaged Hub booted from `.build/StudioHub-0662/Studio Hub.app` with `--offline` and the real `~/StudioHubModels`; `GET /api/health`, `/api/hub/model-packages`, `/api/hub/catalog`, the chat worker's own `/api/catalog`. No model loaded. **[M]** |
| Gateway-facing surface | `GET /studio/chat/api/catalog` with the Hub token (200, chat row) and with no credential (401). **[M]** |
| Kits | Each drive's `.studiohub/installed.json`, `focused-release-manifest.json`, app manifest and all eleven chat files, by size. **[M]** |
| Route behaviour | The real FastAPI app with a recording engine double: the accepted parameter surface. **[M]** |
| Streaming disconnect | Real uvicorn + the real app + an engine double that holds one lock across yields (the earlier review's F4 harness). **[M]** |
| Test gate | `pytest tests/test_chat_*.py` → **69 passed**, then **70** after another worker added a native case mid-review; `tests/test_model_manifest_catalog.py` → **17 passed, 48 subtests**; `bash native/build.sh` → all three suites pass. **[M]** |
| Read | `components/chat/**`, `packaging/chat_worker.py`, `packaging/hub_worker.py`, `components/hub/standalone.py`, `backend/auth.py`, `backend/activity.py`, `backend/memory_admission.py`, `components/model_packages.py`, `native/StudioAPI.swift`, `native/ShellCore.swift`, the tests, `qualification/st14-chat-2026-09-24.md`, the 2026-09-24 review, `docs/tasks/HANDOFF.md`, gateway-kh (`~/Developer/gateway-kh/repository`, read-only). **[M]** |
| Not run | Any 8/16/24 GB measurement (this is a 32 GiB host); a GUI session; a full root-suite run (the Voice timeout that ST-13 owns); `verify-kit.sh` (it starts a Hub from a removable drive while other workers are active). |

---

## 3. The card, claim by claim

### 3.1 `[x]` claims

| Claim (card) | Verdict | Evidence and correction |
| --- | --- | --- |
| **ST-14.0** recon: Hub is chat-aware (`capabilities.py` → `chat.completion`, `memory_control`, ledger, broker) | **HOLDS** | `capabilities.py:31` maps `chat → chat.completion`; `memory_control.SUPPORTED_MODALITIES` now derives from `vocabulary.WORKER_FOR_MODALITY` (chat included); `activity.VALID_STUDIOS`/`VALID_OPERATIONS` derive from the same table. **[M]** |
| ST-14.0: "`worker_specs()` builds only voice, image, subtitle and music"; "there is no `components/chat/`, no `packaging/chat_worker.py`"; "no local Hub chat route"; "`.chat` sits in `comingLater`" | **HISTORICAL, now false** | All four are false in the current tree: `packaging/hub_worker.py:182` registers a chat spec; `components/chat/` and `packaging/chat_worker.py` exist and are tracked; `/api/hub/chat/completions` exists; `native/ShellCore.swift` derives `createOrder` with `.chat` and `comingLater` from `!hasEngine`. The bullets are correct *as recon of 2026-09-24*; they must not be read as the current state, and the card now says so. **[M]** |
| ST-14.0: upstream Chat Studio source absent (`/build`, `chatstudio-mac`) | **HOLDS** | Neither exists on this Mac today. **[M]** |
| ST-14.0: `.build/voice-runtime` carries `mlx 0.31.2` / `mlx-lm 0.31.3` | **HOLDS** | Re-read today. **[M]** |
| ST-14.0: model download network-bound at ≈75–96 KB/s | **NOT RE-CHECKED** | A 2026-09-24 observation; irrelevant now that the weights are local. **[I]** |
| ST-14.1b: packaged-app inference proven on 0.6.56/`928ebd4` — health ok, catalog 262144/4096, `'HELLO WORLD'`, no `Thinking Process:`, JSON `json_valid: true` / 0 repairs, peak MLX 5.22 GB | **HOLDS as a dated receipt** | Every number matches the saved artefacts in `.build/chat-qual/` (`completion.json`: 3 tokens, 1.025 s, `HELLO WORLD`; `json_mode.json`: `json_valid true`, `repairs 0`). The 0.6.56 bundle no longer exists on this Mac, so it stands as a historical measurement. **New today:** the *0.6.62* installed bundle's own worker loaded the real 4-bit snapshot and completed (peak MLX **5,207,474,786 bytes = 5.21 GB**), so the packaged path still works on the shipped build. **[M]** |
| ST-14.2: manifest captured, validated, registered; all 11 files SHA-256-verified and imported into `~/StudioHubModels/…/model/` (5,977,071,067 bytes) | **BYTES YES; RECEIPT ABSENT WHEN MEASURED, PRESENT SINCE 12:27** (see the correction in §1) | Manifest registered and `validate_manifest` accepts it; all eleven files are present with the manifest's exact sizes; the kit receipts carry `manifest_digest 68a3cf02…`. But `~/StudioHubModels/.studiohub/installed.json` (41 entries, mtime 2026-09-25 00:16) has **no** `qwen3-5-9b-4bit`, so the shipped Hub reports `files_state: not_installed` and the native gate reads "Its files are not verified" **[M]**. The 2026-09-24 review hashed all eleven files and they matched. The card's "imported" is true of the bytes and false of the app's own notion of installed. |
| ST-14.6: Mac library and all four kit libraries stocked; each kit carries the chat-capable app and `qwen3-5-9b-4bit`; `RESULT: PASS` | **HOLDS in substance, numbers stale** | Four drives, today: **24 installed / 24 advertised** (was 20/20), chat receipt present, `manifest_sha256` matches `model_packages.manifest_digest`, all 11 files size-match, kit app **0.6.62/`3e1a0eb`** (was 0.6.56/`4e87afd`), **215–234 files / 70.5–72.5 GB** (was 181 / 64.5 GB). I did not re-run `verify-kit.sh` itself. **[M]** |
| ST-14.4b: Hub-dispatched generation proven — five workers, one authenticated `POST /api/hub/chat/completions`, 24 completion tokens, `elapsed_seconds` 0.973, clean shutdown | **HOLDS as a dated receipt** | `.build/chat-qual/hub_chat_completion.json` contains exactly that (usage 23/24/47, reply about the sky, `elapsed_seconds 0.973`). Today's probe confirms the Hub still starts all five workers (`/api/health` → `workers: 5`) and that `/api/hub/model-packages` → 200 with the chat row; the dispatch hop itself was not re-taken here (it needs a 6 GB load). **[M]** |
| Gated apply list item 5: `packaging/hub_worker.py` chat spec landed, guarded | **HOLDS** | `_chat_spec()` refuses an incomplete bundle, a bad runtime identity and a catalog with no single `chat-mlx` manifest. **[M]** |
| Gated apply list items 1–4 and 7 (git-add the worker, copy the manifest, capture tool, catalog test, stock the libraries) | **ALL LANDED** | `components/chat/**` and `packaging/chat_worker.py` are tracked; `model-manifests/qwen3-5-9b-4bit.json` is registered; `capture_model_manifests.py` has the `CHOICES` row; `tests/test_model_manifest_catalog.py` has the EXPECTED row (17 passed / 48 subtests); `packaging/stage_voice.py` has `_stage_chat` and the 0.6.62 bundle carries the component. **[M]** |

### 3.2 `[~]` claims, re-checked now that staging has landed

| Claim | Now | Correction |
| --- | --- | --- |
| **ST-14.1**: engine + worker done; "14/14 focused tests pass"; **"real generation proof is pending the model download"** | **CLOSED** | The proof exists (ST-14.1b, ST-14.4b) and the focused suite is **40** tests, not 14. The `pending` clause was already stale when 1b was written. Marked `[x]`. |
| **ST-14.3**: chat spec landed **guarded** "so it is inert until staged"; `stage_voice.py` staging **gated on ST-13** | **CLOSED** | Staging landed: the shipped 0.6.62 release manifest carries `components.chat` with `runtime: voice` and the same runtime digest, so the guard is no longer inert. Marked `[x]`. |
| **ST-14.4**: Hub wiring done; "Remaining: the packaged catalog must surface the chat model once staging lands" | **CLOSED** | The packaged catalog surfaces it: `/api/hub/model-packages` → **200**, 24 rows, **exactly 1 chat row**; `/api/hub/catalog` → 31 rows, **exactly 1 chat row** with `capabilities: ["chat.completion"]`. Marked `[x]`. The row's `not_installed` state is the missing receipt (ST-14.2), not a wiring gap. |
| **ST-14.5**: native Chat page added, `.chat` out of "Coming later", `sendChat()` posts to the Hub route, build green. "Remaining: a live packaged render check once the worker is staged" | **STILL OPEN** | The staged app has the page and the sidebar is derived (`createOrder` includes `.chat`; `comingLater` from `!hasEngine`) **[M]**. But no human has typed into the page on a real Mac; on this Mac it cannot send (no receipt); and `sendChat()` still posts `model: ""` (F3). The render check is still owed. |
| **ST-14.7**: bridge researched and handed off; "gateway-kh must add the chat modality and a chat branch" | **STILL OPEN, our side nearly settled** | gateway-kh today still has chat in neither `KIND_STUDIO_HUB` nor the `studio-hub-local` provider nor `StudioHubBackend.generate` **[M]**. Our side: the Hub-token path the adapter uses (`GET /studio/chat/api/catalog`) answers **200** with the chat row and **401** without a credential **[M]**, so no Hub change is needed for a *local* bridge. The one open question on our side is the fleet allowlist (F6). See §5. |
| **ST-14.8**: Independent review — `[ ]` | **STALE as "not done"** | A review was done on 2026-09-24 (`qualification/st14-review-2026-09-24.md`, commit `40453b9`) and its verdict was *not passable*; the card never recorded it, while `BOARD.md:402` did. This file is the second pass. Marked `[x]` with both verdicts. |

### 3.3 Closing note

The card's "Remaining limitation/blocker" said the four SSDs were unmounted so the kits could not
be stocked. **That is false today** and was already contradicted by ST-14.6 in the same card. It
has been replaced with what actually remains (§6).

---

## 4. The 2026-09-24 review's findings, today

| # | Finding (2026-09-24) | Today | Evidence |
| --- | --- | --- | --- |
| F1 | `/api/hub/model-packages` → HTTP 500 with the shipped catalog | **FIXED** | Shipped 0.6.62 → **200**, chat row present, `worker_id: chat`. **[M]** |
| F2 | The chat package has no install receipt, so the Chat page cannot send; `verify_installed` could not create one | **FIXED IN THE TREE, NOT YET SHIPPED** | Commit `d8b7ca6` (2026-09-25 12:30) rehashes whatever is on disk and writes the receipt; HANDOFF records **2.97 s → `files_verified`, `dispatch_state: eligible`**, then a real Hub completion. **It is not in 0.6.62**: the shipped bundle's and the installed app's `model_packages.py` are byte-identical to each other (`537586e7…`) and still carry the early `return {**base, 'state': 'not_installed'}` — the bundle was built at 11:34, before the fix. So on the owner's current app Verify Files still does nothing for a package with no receipt, and the receipt needs the next build (or the fixed code run against the library, which is the other worker's job). On this Mac the receipt is absent, so the page still refuses **here** **[M]**. I did not run verify. |
| F3 | `sendChat()` posts `model: ""`, so the Hub's model gates never run | **OPEN IN THE SHIPPED BUILD; FIX IN PROGRESS** | The shipped 0.6.62 bundle and `git show HEAD:native/StudioAPI.swift` both send `HubChatRequest(model: "", …)` (line 2034 at HEAD) **[M]**. While this review ran, another worker's **uncommitted** edit moved the working tree to `HubChatRequest(model: chatModel.isEmpty ? nil : chatModel, …)` (line 2080) and began adding behavioural chat cases to `tests/native/api_main.swift` — i.e. the finding is being fixed as I write, in files I do not own. It is not shipped until that lands, and the shipped wire test would not have caught it either way. |
| F4 | A streaming request that loses its client wedges the worker's model lock | **OPEN — reproduced today** | Real uvicorn + the real app + an engine double holding the lock across yields: the client reads one chunk and leaves; **20 s later** `lock.locked()=True`, `streams started=1 finished=0` (the full stream would have taken 6 s). The endpoint's `finally` records the job as `cancelled` but never closes the engine generator. **[M]** |
| F5 | An abandoned request reserves the Mac for up to six hours (`asyncio.CancelledError` skips the lease release) | **OPEN** | `standalone.py` still releases only in `except (httpx.HTTPError, OSError)` and in `_settle_peer_lease`; `CancelledError` is a `BaseException`. Not re-measured today (the earlier review measured `MACHINE_BUSY` on the next request). **[M, code]** |
| F6 | The fleet allowlist has no chat, so a fleet credential gets 403 on `/studio/chat/…` | **OPEN** | `auth.py:413` `_FLEET_STUDIO = (?:voice\|subtitle\|image\|transcription\|music)` — no chat. The **Hub-token** path works (200 today), so only the remote/fleet caller is affected. **[M, code]** |
| F7 | `activity` accepts the chat studio but raises on a chat job | **FIXED** | `VALID_STUDIOS`/`VALID_OPERATIONS`/`_DEFAULT_OPERATION` now derive from `vocabulary`, with the chat case named in the comment. **[M]** |
| F8 | Chat input validation is materially weaker than the Image worker's | **PARTLY FIXED** | Now refused: negative `temperature`, `top_p` ≤ 0 or > 1, `max_tokens` < 1, non-object `response_format` (422 each). Still accepted: `thinking: "false"` → **thinking ON**; `temperature: NaN` → 200 with `nan` reaching `make_sampler`; a 200 000-character prompt and 500 messages; `max_tokens: 1e11` although the lane contract publishes `maximum: 4096`; a string `seed` silently dropped; `tools`/`n`/`logit_bias` silently discarded. Details in §5 and `qualification/evidence/st14-review-2026-09-25/route-parameters.json`. **[M]** |
| F9 | The native suite is a text search; the engine tests skip the thinking path | **OPEN IN THE SHIPPED SUITE, BEING ADDRESSED** | `tests/test_chat_native_wiring.py` still asserts substrings (`test_chat_turns_are_published_state_not_a_local_only_buffer` checks three names; the Send test searches 700 characters), which is why the shipped `model: ""` passed it. The uncommitted `tests/native/api_main.swift` work noted under F3 adds the behavioural fixture this finding asked for; I did not review that work (it is another worker's, uncommitted). **[M]** |
| F10 | The default memory policy unloads the model after every turn | **OPEN (observation)** | `DEFAULT_MODE = "immediate"`, `idle_seconds: 0`, 5 s tick — a conversation pays a cold load per message unless the operator changes it. **[M, code]** |

---

## 5. New findings from this pass

### F11 — two published strings are stale against the record **[M]** *(cosmetic, but they are read as facts)*

- The chat worker's `/api/catalog` row publishes
  `"qualification": {"owner_decision": "Qwen3.5-9B-4bit, MLX, text-only", "packaged_inference":
  "pending measured proof"}` — while `qualification/st14-chat-2026-09-24.md` and ST-14.4b record
  that proof, and I re-ran the packaged load on 0.6.62 today. Either the string is stale or the
  card's proof claim is; the measurement exists, so the string is.
- `model-manifests/qwen3-5-9b-4bit.json` still says `qualification.status: "unqualified"` with
  `"Local import and inference qualification remain required"`. The *import* and the *32 GB
  inference* have happened; the **tier** qualification has not, so `status: unqualified` is
  defensible and should stay until §7's harness says otherwise. The sentence about "import… remain
  required" is what is stale.
- Neither affects behaviour. I did not touch the worker or the manifest (the manifest is another
  worker's file); this is the correction to make when the tier measurement lands.

### F12 — the route accepts things its own contract does not advertise **[M]**

Measured against the real app with a recording engine (`route-parameters.json`):

| Sent | HTTP | Engine received |
| --- | --- | --- |
| `thinking: "false"` | 200 | `thinking: True` — **a string turns thinking ON** |
| `thinking: "no"` | 200 | `thinking: True` |
| `temperature: NaN` (raw body) | 200 | `nan` → `make_sampler(temp=nan)` |
| `max_tokens: 100000000000` | 200 | `100000000000` (the engine caps it at 4096) |
| 200 000-character prompt / 500 messages | 200 | accepted, unbounded |
| `seed: "not-a-number"` | 200 | silently dropped |
| `tools` / `n` / `logit_bias` | 200 | silently discarded |

The lane contract publishes `thinking` as a boolean with default `false`, `max_tokens` with
`maximum: 4096`, and states the rule "a parameter this worker ignores is deliberately absent".
The route is stricter than it was (F8's negative/haircut cases now 422), but these remain: the
`thinking` coercion is the exact opposite of the task's central fix, `NaN` reaches the sampler, and
a scheduler can send a 200 k-character prompt to a 262 144-token context. No sandbox escape — the
model path comes from the pinned manifest — so this is a contract defect, not a security one.

### F13 — the gate is green **[M]**

`pytest tests/test_chat_worker.py tests/test_chat_worker_cli.py tests/test_chat_hub_wiring.py
tests/test_chat_packaging.py tests/test_chat_native_wiring.py -q -p no:randomly` → **69 passed** at
the start of this review and **70** at the end (native wiring 9 → 10, another worker's uncommitted
case). Per file: worker **40**, CLI **4**, hub wiring **6**, packaging **10**, native wiring
**9 → 10** (the card's "29 / 2 / 6 / 3 / 9" is stale; the earlier review's "30" also is).
`tests/test_model_manifest_catalog.py` → 17 passed, 48 subtests. `bash native/build.sh` → boundary,
API-contract and lifecycle suites all pass. I did not re-run the full root suite (710-test claim);
the one known error is the ST-13 Voice timeout.

---

## 6. Honest limits — what has never been exercised on a real Mac

These are stated as limits, not as covered work:

1. **Per-tier throughput (8/16/24 GB) is unmeasured.** The only chat numbers on record are from
   this 32 GiB M2 Max. The manifest's 16 GB floor is a fit claim, and the "8 GB is not viable"
   sentence in the manifest is an inference nobody has run. §7's harness exists to close this; it
   has not been run on either tier.
2. **A human typing into the Chat page has never happened.** The proofs are HTTP-level. The page
   can send on this Mac now that the receipt exists (the coordinator's end-to-end 200 in 11.98 s),
   but the shipped build posts `model: ""` (F3; being fixed uncommitted in the tree), so the page's
   own model gates and pause states are still untested in use.
3. **Nothing refuses on memory tier.** The worker warns below 16 GB and stays `eligible`; the
   Hub's total-memory floor is `0` without an operator override; the native gate ignores the
   warning. So "does an 8 GB Mac refuse honestly?" is, today, **"no — it tries"** **[M, code]**.
   The harness's own floor refusal is the harness's, and says so.
4. **Streaming end-to-end through the Hub was never exercised**, and the worker's SSE path is
   wedged by a client disconnect (F4). Nothing in the app sends `stream: true` today.
5. **The fleet/peer chat path is closed to fleet credentials** (F6). Only the Hub-token path is
   proven (200 today).
6. **The OptiQ comparison** in the card's design note (8.22 GB snapshot, byte-identical template)
   is a 2026-09-24 [V]/[I] reading, not re-checked here.
7. **The full root suite** (710-test claim) is not re-run.

---

## 7. The per-tier throughput harness (job 2 of the same brief)

`qualification/chat-tier-run.sh` + `qualification/README-chat-tiers.md`:

- One command, no editing: `bash ~/Desktop/chat-tier-run.sh` on a 16 GB Mac, then on an 8 GB Mac.
- Finds the app (installed, or any mounted kit), the chat manifest **by engine, not by file name**
  (so a second chat model needs no edit), and the model in the library layout the worker itself
  uses; starts the packaged worker on a private loopback port; pins the memory policy to
  `performance` (otherwise the shipped default releases the model between runs and the two runs
  are not comparable); runs a load probe, then two 256-token completions at temperature 0, seed 42,
  thinking off; samples swap during every run.
- Prints one paste-back block: chip, memory, macOS, app build, model + quantization + repo +
  revision, engine, the prompt and token counts, wall time, **load time**, **tokens/second**,
  **peak MLX memory**, swap peak, the answer, and a verdict.
- Refuses before creating anything, each refusal naming what it needed and the override: not an
  8/16 GB host (`CHAT_ALLOW_NON_TARGET=1`); no packaged app; no complete model; available memory
  below the checkpoint's own 5.98 GB + 0.5 GB working room (`CHAT_IGNORE_FREE_FLOOR=1`). On an
  8 GB Mac that last refusal is itself the fit answer and is printed as the same block with a
  `NOT RUN` header.
- `ABORTED` (swap cap or wall limit) yields **INCONCLUSIVE**, never "failed"; an out-of-memory
  abort yields NOT QUALIFIED; a run on a 24/32 GB host yields **NOT TIER EVIDENCE**. It estimates
  no tier it did not run.
- Verified locally: syntax; the tier guard (exit 4, nothing created); the free-memory guard and
  its block (exit 3, nothing created); the model guard (exit 6); the app guard (exit 5, against a
  copy with the installed-app paths removed, because this Mac has the app); `SMOKE=1` from the
  installed app, from a kit drive named explicitly, **and** with `HOME` pointed at an empty
  directory so the model was found on the mounted kit drive by the script's own search (all `OK`,
  no verdict); and one full non-target run at 8 tokens that exercised the verdict block
  (`NOT TIER EVIDENCE`). Evidence:
  `qualification/evidence/st14-review-2026-09-25/chat-tier-harness-verification.json`.
- **Unverified:** the actual 8 GB and 16 GB runs. The 32 GiB dry run measured peak MLX 5.25 GB,
  load ~7.5 s, and 15–20 tok/s on 8-token completions; those are harness-verification numbers on a
  host that is not a tier, and they must not be read as tier evidence **[M]**.

---

## 8. The gateway bridge (ST-14.7): what our side owes, what theirs does

**Their side (gateway-kh, read-only, today):** `KIND_STUDIO_HUB` modalities are still
`{image, tts, music, transcription}`; the `studio-hub-local` provider's `modalities` likewise; and
`StudioHubBackend.generate` still raises `MODEL_MODALITY_MISMATCH` for chat. All four items in
`research/chat-studio-gateway-bridge.md` §"The change gateway-kh needs" are still theirs. **[M]**

**Our side:** the worker, both Hub routes and the lane contract are shipped, and the adapter's own
credential path works today — its `_resolve_base`/`_headers` use the local `hub-ready.json` and the
Hub token, and `GET /studio/chat/api/catalog` with that token answers 200 with the chat row
(`capabilities: ["chat.completion"]`, context 262144), while the same request without a credential
answers 401 **[M]**. The adapter also already reads our `lane_contract.deadline_seconds`.

What remains on our side is a decision, not code: **chat is absent from the fleet allowlist**, so a
*remote* gateway node (fleet credential) still gets 403 on `/studio/chat/…`. Either add chat to
`auth._FLEET_STUDIO` when the fleet story for text is decided, or record in the card and the bridge
note that the peer surface is deliberately closed to chat. The bridge note previously implied the
peer path worked for everyone; it now carries the caveat, and its stale model line (the OptiQ
manifest) is corrected to the shipped `qwen3-5-9b-4bit` (uniform 4-bit MLX, 11 files,
5,977,071,067 bytes).

---

## 9. What is genuinely well built

- **The lane contract is real, published by the party that owns the API**, built from the model's
  own limits, and already consumed by the gateway (`deadline_seconds` read from the Hub's echo).
- **Provenance everywhere it matters**: `model_limits` carries the file and key for every number,
  and says *"worker default: the checkpoint ships no `generation_config.json`"* for 4096. The
  2026-09-25 "never publish a number the model did not state" rule is implemented, not aspirational.
- **The manifest is exact** (11 files, per-file sha256, pinned revision in every URL, a digest the
  kit receipts reproduce) and the packaging path is careful (guarded worker spec, single-model
  enforcement by catalog scan, owner-only tokens, `-I -B`, refused incomplete bundles).
- **The F1 fix is the right shape** — a named mapping, a `ValueError` naming both files to update,
  and a regression test that walks the real catalog. The `verify_installed` rewrite (`d8b7ca6`,
  committed after 0.6.62 was built) is the same shape: it rehashes what is on disk and refuses by
  name instead of returning an early `not_installed` — it simply has not shipped yet.
- **The Hub tests test behaviour**: `test_chat_hub_wiring.py` stands up a real loopback worker and
  asserts the re-signed token and the forwarded path.

---

## 10. Corrections made

1. `docs/tasks/ST-14.md`: ST-14.1, ST-14.3, ST-14.4 → `[x]` with the closing evidence; ST-14.5
   keeps `[~]` with what remains; ST-14.7 keeps `[~]` with our one open decision; ST-14.8 → `[x]`
   (two reviews, verdicts recorded); the ST-14.0 recon bullets are marked as recon-of-that-date;
   test counts and bundle/kit figures corrected; the closing blocker note replaced.
2. `research/chat-studio-gateway-bridge.md`: the stale OptiQ manifest line, the fleet-allowlist
   caveat, and the verified Hub-token reachability.
3. New: `qualification/chat-tier-run.sh`, `qualification/README-chat-tiers.md`,
   `qualification/evidence/st14-review-2026-09-25/`.
4. Corrected by the coordinator while this review was being finished: `docs/tasks/HANDOFF.md`'s
   "ST-14.8 independent review has not happened" line and `BOARD.md` (commit `4c3d894`), and the
   receipt/Verify correction (commit `6c1a4de`). Still not mine: `components/chat`'s catalog string
   and the manifest's stale sentence (F11) wait for the tier measurement. I touched no worker, Hub,
   native or packaging file.
