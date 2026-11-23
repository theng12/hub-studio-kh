# Session dirty-state routing — 2026-09-24

Verified read-only against `~/Developer/studio-hub-kh` on branch
`implementation/voice-runtime` at HEAD `9756528`, with the second worktree
`~/Developer/studio-hub-kh-hotfix-0656` on branch `hotfix-0.6.56`. The
image session was actively concluding during this verification, so `hotfix-0.6.56`
advanced from `7f6e663` to `013eb7e` and the six image research notes left this shared
tree's untracked set while the note was being written. The shared checkout remains one
dirty working tree: 35 tracked files are modified and 24 untracked paths (excluding this
note) belong to four parallel sessions (Image, ST-13 TTS prune, ST-14 Chat/LLM, Video)
plus coordinator/shared documents. The Image session's fix and the TTS prune are already
isolated on `hotfix-0.6.56` in the separate worktree: commit `2f2dabd` is the image 8 GB
fix (`blockReason` no longer blocks on the advisory RAM tier; FLUX.2 Klein label
restored), commit `7f6e663` is the TTS prune (18 unshipped TTS/audio manifests removed,
with their capture script and manifest-catalog test), and commit `013eb7e` is the six
image research notes. This note routes every dirty path to exactly one owning session so
each can conclude its own work without committing another session's files.

## Image (concluded by the orchestrator session)

| File | Status | What it is | Action the owning session must take |
| --- | --- | --- | --- |
| `components/image/app/backend/main.py` | modified | Image worker model label/qualification; "FLUX.2 Klein 4B MLX 4-bit" | Concluded. Already committed as `2f2dabd` on `hotfix-0.6.56`; do not re-commit from this tree. |
| `tests/test_model_reasons.py` | modified | Pins `blockReason` as advisory, not blocking | Concluded. Already in `2f2dabd`. |
| `VERSION` | modified | Version bump for the image fix | Concluded. Already in `2f2dabd`. |
| `CHANGELOG.md` | modified | Image fix release entry | Concluded. Already in `2f2dabd`. |
| `release-notes.md` | modified | Image fix release notes | Concluded. Already in `2f2dabd`. |
| `docs/tasks/HANDOFF.md` | modified | Release handoff for the image fix | Concluded. Already in `2f2dabd`. |
| `native/StudioAPI.swift` — `blockReason` hunk | modified | Drops `memoryWarning` as a hard `blockReason` so 8 GB Macs can generate | Concluded. The hunk is in `2f2dabd`. This file is shared with ST-14 (see caution); only the `blockReason` hunk belongs to Image, and it is already committed. |
| `AGENTS.md` — "Model use scope" section | modified | Scoping note: do not raise model licensing | **Not** in `2f2dabd`. Uncommitted in this tree; the orchestrator/image session must commit it or move it to the hotfix worktree. |
| `research/image-model-inventory.md` | untracked, now relocated | Image model inventory | Concluded. Committed as `013eb7e` on `hotfix-0.6.56` and no longer untracked in this tree; copies staged in `_trash/image-research-notes-2026-09-24/`. |
| `research/qwen-image-2-1-adoption.md` | untracked, now relocated | Qwen-Image 2.1 adoption note | Concluded. In `013eb7e`. |
| `research/mflux-engine-bump-impact.md` | untracked, now relocated | MFLUX engine bump impact | Concluded. In `013eb7e`. |
| `research/text-rendering-model-shortlist.md` | untracked, now relocated | Text-rendering model shortlist | Concluded. In `013eb7e`. |
| `research/three-model-rollout-plan.md` | untracked, now relocated | Three-model rollout plan | Concluded. In `013eb7e`. |
| `research/image-memory-pressure.md` | untracked, now relocated | Image memory-pressure analysis | Concluded. In `013eb7e`. |

## ST-13 TTS prune

| File | Status | What it is | Action the owning session must take |
| --- | --- | --- | --- |
| `model-manifests/` — 18 TTS/audio manifests | tracked, still present in this tree | The prune target: manifests for packages the kit does not ship | Re-remove these 18 files in this tree, or take commit `7f6e663` from `hotfix-0.6.56`, which already deletes them. |
| `packaging/capture_model_manifests.py` | modified | Capture script updated for the prune | Already in `7f6e663`; reconcile, then commit. |
| `tests/test_model_manifest_catalog.py` | modified | Manifest-catalog test updated for the prune | Already in `7f6e663`; reconcile, then commit. |
| `components/voice/app/backend/catalog.py` | modified | Voice catalog changes (not in `7f6e663`) | ST-13 owns this; commit it in this tree. |
| `components/voice/README.md` | modified | Voice README changes (not in `7f6e663`) | ST-13 owns this; commit it in this tree. |
| `packaging/kit-readmes/Studio Hub KH.md` | modified | Kit README changes (not in `7f6e663`) | ST-13 owns this; commit it in this tree. |
| `tests/test_qwen_base_standalone.py` | modified | Voice test changes (not in `7f6e663`) | ST-13 owns this; commit it in this tree. |
| `tests/test_section_policy.py` | modified | Voice test changes (not in `7f6e663`) | ST-13 owns this; commit it in this tree. |
| `components/voice/app/tests/test_catalog_language_support.py` | modified | Voice test changes (not in `7f6e663`) | ST-13 owns this; commit it in this tree. |
| `components/voice/app/tests/test_fleet_auth.py` | modified | Voice test changes | ST-13 owns this; commit it in this tree. |
| `components/voice/app/tests/test_generation_artifact_contract.py` | modified | Voice test changes | ST-13 owns this; commit it in this tree. |
| `components/voice/app/tests/test_long_form_policy.py` | modified | Voice test changes | ST-13 owns this; commit it in this tree. |
| `components/voice/app/tests/test_model_audit_contract.py` | modified | Voice test changes | ST-13 owns this; commit it in this tree. |
| `components/voice/app/tests/test_model_storage.py` | modified | Voice test changes | ST-13 owns this; commit it in this tree. |
| `components/voice/app/tests/test_priority_mlx_models.py` | modified | Voice test changes | ST-13 owns this; commit it in this tree. |
| `components/voice/app/tests/test_section_size_control.py` | modified | Voice test changes | ST-13 owns this; commit it in this tree. |
| `docs/tasks/ST-13.md` | untracked | ST-13 task card | ST-13 owns this; commit it. |
| `research/tts-archive-walkthrough-2026-09-24.md` | untracked | TTS archive walkthrough | ST-13 owns this; commit it. |
| `research/tts-offer-review-2026-09-24.md` | untracked | TTS offer review | ST-13 owns this; commit it. |

The 18 prune targets currently present in this tree are:
`audio8-tts-preview-0-6b-bf16.json`, `chatterbox-turbo-4bit.json`, `echo-tts-base.json`,
`fish-s1-dac-min.json`, `kokoro-82m-bf16.json`, `longcat-audiodit-1b-4bit.json`,
`moss-audio-tokenizer-nano.json`, `moss-tts-nano-100m.json`,
`orpheus-3b-0-1-ft-4bit.json`, `qwen3-tts-12hz-0-6b-base-4bit.json`,
`qwen3-tts-12hz-0-6b-customvoice-8bit.json`, `qwen3-tts-12hz-1-7b-base-8bit.json`,
`qwen3-tts-12hz-1-7b-customvoice-8bit.json`, `qwen3-tts-12hz-1-7b-voicedesign-8bit.json`,
`snac-24khz.json`, `umt5-base.json`, `vibevoice-realtime-0-5b-4bit.json`,
`voxtral-4b-tts-2603-mlx-4bit.json`. They are tracked and unmodified here, so the prune
has not landed in the shared tree; the image session's work is the reason they are
still present rather than deleted.

## ST-14 Chat / LLM session

| File | Status | What it is | Action the owning session must take |
| --- | --- | --- | --- |
| `native/StudioAPI.swift` — chat turns + `sendChat` | modified | Chat state and the `/api/hub/chat/completions` call | ST-14 owns the chat hunks and this shared file; see caution — it must commit the shared native file without discarding Image's already-committed `blockReason` hunk. |
| `native/StudioViews.swift` | modified | Chat UI | ST-14 owns this; commit it. |
| `native/ShellCore.swift` | modified | Shell wiring for chat | ST-14 owns this; commit it. |
| `components/hub/standalone.py` | modified | Hub wiring for the chat worker | ST-14 owns this; commit it. |
| `components/hub/standalone_config.py` | modified | Hub config for the chat worker | ST-14 owns this; commit it. |
| `components/hub/backend/activity.py` | modified | Hub activity handling for chat | ST-14 owns this; commit it. |
| `components/model_packages.py` | modified | Model-package metadata for chat | ST-14 owns this; commit it. |
| `packaging/hub_worker.py` | modified | Expects `model-manifests/qwen3-5-9b-4bit.json` (line 149) to decide packaging | ST-14 owns this; **blocked** until the missing manifest is added, then package the chat worker. |
| `tests/native/main.swift` | modified | Native test changes | ST-14 owns this; commit it. |
| `tests/native/api_main.swift` | modified | Native API test changes | ST-14 owns this; commit it. |
| `tests/test_peer_path_contract.py` | modified | Peer-path contract test changes | ST-14 owns this; commit it. |
| `components/chat/` (6 files: `__init__.py`, `app/__init__.py`, `app/backend/__init__.py`, `app/backend/llm_engine.py`, `app/backend/main.py`, `app/backend/memory_policy.py`) | untracked | The whole Chat worker | ST-14 owns this; commit it. |
| `packaging/chat_worker.py` | untracked | Chat worker packaging entry | ST-14 owns this; commit it. |
| `tests/test_chat_worker.py` | untracked | Chat worker test | ST-14 owns this; commit it. |
| `tests/test_chat_worker_cli.py` | untracked | Chat worker CLI test | ST-14 owns this; commit it. |
| `tests/test_chat_packaging.py` | untracked | Chat packaging test | ST-14 owns this; commit it. |
| `tests/test_chat_hub_wiring.py` | untracked | Chat hub-wiring test | ST-14 owns this; commit it. |
| `tests/test_chat_native_wiring.py` | untracked | Chat native-wiring test | ST-14 owns this; commit it. |
| `docs/tasks/ST-14.md` | untracked | ST-14 task card | ST-14 owns this; commit it. |
| `research/chat-studio-adoption-2026-09-24.md` | untracked | Chat Studio adoption note | ST-14 owns this; commit it. |
| `research/chat-studio-gateway-bridge.md` | untracked | Chat Studio gateway bridge | ST-14 owns this; commit it. |
| `research/story-text-llm-shortlist.md` | untracked | Story-text LLM shortlist | ST-14 owns this; commit it. |
| `research/local-llm-24-32gb-2026-09.md` | untracked | Local LLM fit note for 24–32 GB | ST-14 owns this; commit it. |
| `research/moe-24-32gb-fit-2026-09.md` | untracked | MoE fit note for 24–32 GB | ST-14 owns this; commit it. |

**Hard dependency:** `packaging/hub_worker.py` checks for
`model-manifests/qwen3-5-9b-4bit.json`, and that file does not exist in
`model-manifests/` (38 tracked manifests, none matching). Chat cannot be packaged
until ST-14 adds that manifest. The standing owner decision names Qwen3.5-9B (4-bit) as
the common local text model, so the manifest is the concrete missing artifact.

## Video session

| File | Status | What it is | Action the owning session must take |
| --- | --- | --- | --- |
| `research/video-fleet-shortlist-2026-09-24.md` | untracked | Video fleet shortlist | Research-only; commit when the note is final. |
| `research/ltx-2-5-apple-silicon.md` | untracked | LTX 2.5 on Apple Silicon note | Research-only; commit when the note is final. |

Video has no tracked changes in this tree.

## Coordinator / shared

| File | Status | What it is | Action the owning session must take |
| --- | --- | --- | --- |
| `docs/tasks/BOARD.md` | modified | Task board / holder state | Coordinator owns this; commit it. |
| `README.md` | modified | Project README | Coordinator owns this; commit it. |
| `research/owner-hardware.md` | untracked | Canonical owner hardware fleet and standing text-model brief | Coordinator owns this; commit it. |
| `AGENTS.md` — "Owner hardware fleet" section | modified | Owner machine tiers and standing model decision | Coordinator owns this section. `AGENTS.md` is shared with Image's "Model use scope" section; serialize the two edits before either commits the file. |

## Order of operations

1. **Image concludes first.** Its code fix, tests and release metadata are already
   isolated in `hotfix-0.6.56` commit `2f2dabd`, and its six research notes are in
   `013eb7e`, so the shared tree needs no image commit. The orchestrator session should
   land the remaining `AGENTS.md` "Model use scope" section (which is not in `2f2dabd`
   or `013eb7e`) with that branch.
2. **ST-13 takes the prune commit.** Take `7f6e663` from `hotfix-0.6.56` (or re-remove
   the 18 manifests by hand), then reconcile and commit the rest of ST-13's files
   (`catalog.py`, `components/voice/README.md`, `packaging/kit-readmes/Studio Hub KH.md`,
   `tests/test_qwen_base_standalone.py`, `tests/test_section_policy.py`, and the eight
   voice tests), which are **not** on `hotfix-0.6.56`.
3. **ST-14 adds the missing chat model manifest and packages the chat worker.**
   `model-manifests/qwen3-5-9b-4bit.json` must exist before `packaging/hub_worker.py`
   can package Chat. ST-14 commits its tracked and untracked files, including the shared
   native/Hub files.
4. **Video is research-only.** It commits its two untracked research notes when final
   and has no code or shared-file work in this tree.

## Caution

Nothing should be committed by a session that does not own the file. The shared
native/Hub files — `native/StudioAPI.swift`, `components/hub/*`, and
`packaging/hub_worker.py` — are touched by ST-14 and must be committed by ST-14, not by
Image, ST-13 or Video. `native/StudioAPI.swift` additionally carries Image's
`blockReason` hunk; ST-14 must preserve that change when it commits the file (it is
already in `2f2dabd` on `hotfix-0.6.56`). `AGENTS.md` carries two separately owned
sections ("Model use scope" = Image/orchestrator, "Owner hardware fleet" =
coordinator); whoever commits `AGENTS.md` must include both sections or the sections
must be committed separately after serialization.
