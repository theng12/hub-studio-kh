# TTS archive walkthrough — model by model

Date: 2026-09-24. Read-only plus local generation. Nothing removed; the only moves were four
duplicate review clips into `_trash/desktop-tts-review-dupes/`.

Companion to [tts-offer-review-2026-09-24.md](tts-offer-review-2026-09-24.md). This note walks
the **153 GB archive** on each drive (`model-library/`), which is separate from the app's 85 GB
kit library (`Video Generation Kit/Studio Hub KH/model-library/`).

## Reconciliation — the tree moved during this review (2026-09-24)

**ST-12 landed while this review was running.** The board's `▶ TAKE OVER HERE` names ST-12
("Khmer VITS families, 0.6.55 cut locally"), committed at `3ab8984` / `ed5a733`, which added two
Khmer VITS families to Voice Studio and the kit:

- `khmer-tts-mms` — `khmerttsopensource/khmer-tts`, Meta MMS VITS, 16 kHz, 0.34 GB
- `khmer-tts-onnx` — `sengtha/khmer-tts-female-v2`, Coqui VITS via ONNX, 22.05 kHz, 0.115 GB

The catalog is therefore now **20 families / 41 entries** and `model-manifests/` holds
**37 packages**, not the 18 / 39 / 35 measured earlier in this note. Any counts earlier in this
document are the pre-ST-12 snapshot; the final list below is against the current tree.

**Consequence for the Khmer question:** the app now genuinely supports Khmer through a
purpose-built VITS engine, with measured receipts. The earlier finding that "no *mlx-audio*
model speaks Khmer" still stands (VoxCPM2's tokenizer has no Khmer vocabulary), but the app is
no longer without a Khmer engine. ST-12.7 — the owner's ear check of the two new voices — is its
only open checkpoint, and its generated clips are numbers 22–25 in the review folder.

## Final keep/cut against the current 41 entries

Owner decisions combined with the audit evidence:

**KEEP — 12 entries:**

| Model | GB | Why |
|---|---:|---|
| `khmer-tts-onnx` | 0.115 | ST-12 Khmer family (ONNX) |
| `khmer-tts-mms` | 0.34 | ST-12 Khmer family (MMS VITS) |
| `chatterbox-4bit` | 0.6 | owner keeper (clip 09) |
| `chatterbox-8bit` | 1.0 | owner keeper (clip 11) |
| `qwen3-0.6b-base-8bit` | 1.9 | owner keeper (clip 12) |
| `OmniVoice-bfloat16` | 2.0 | owner keeper (clip 02); the only 8 GB clone+design engine |
| `VoxCPM2-4bit` | 2.3 | owner keeper (clips 01/02/03 set) |
| `qwen3-1.7b-base-4bit` | 2.3 | owner keeper (clip 15) |
| `VoxCPM2-8bit` | 3.2 | owner keeper |
| `VoxCPM2-bf16` | 5.0 | owner keeper |
| `fish-audio-s2-pro-8bit` | 6.73 | owner keeper (clip 20) |
| `fish-audio-s2-pro-bf16` | 11.01 | owner keeper (clip 21) |

Plus **not in the catalog**: `pocket-tts-4bit` (0.085 GB) — owner decided keep and add to the
app; the two 8 GB machines otherwise have only OmniVoice.

**CUT — 29 entries**, of which 14 are packaged and need manifest + kit-library removal:
`echo-tts-base`, `qwen3-1.7b-base-8bit`¹, `qwen3-1.7b-customvoice-8bit`,
`qwen3-1.7b-voicedesign-8bit`, `audio8-tts-preview-0-6b-bf16`,
`voxtral-4b-tts-2603-mlx-4bit`, `qwen3-0.6b-customvoice-8bit`, `orpheus-3b-0-1-ft-4bit`,
`qwen3-0.6b-base-4bit`, `longcat-audiodit-1b-4bit`, `vibevoice-realtime-0-5b-4bit`,
`chatterbox-turbo-4bit`, `kokoro-82m-bf16`, `moss-tts-nano-100m`; plus the four now-orphaned
dependencies `fish-s1-dac-min`, `snac-24khz`, `umt5-base`, `moss-audio-tokenizer-nano`
(`s3tokenizerv2` stays — the kept Chatterbox tiers still need it). The other 15 are
download-only catalog rows with no manifest.

¹ `qwen3-1.7b-base-8bit` was not among the numbered clips the owner judged; it is the
audit-passed production tier, so it is listed for an explicit yes/no rather than assumed.



## What the archive is

From its own README: "A plain, sorted folder of models… Nothing here is tied to any application
and nothing needs maintaining — it is just storage." 53 models, 153.4 GB, in five shelves plus a
reserved one:

| Shelf | Models | Size |
|---|---:|---:|
| `voice-models/` | 37 | 77.3 GB |
| `image-models/` | 6 | 53.2 GB |
| `music-models/` | 1 | 10.7 GB |
| `llm-models/` | 4 | 8.6 GB |
| `transcription-models/` | 5 | 3.6 GB |
| `video-models/` | 0 | 0 — **reserved for the video model** |

## The archive-only voice models (the real decisions)

Eight of the 37 voice models are **not** in the app kit. These are the only genuinely new
decisions on the voice shelf:

| Model | Size | Engine | Status | Recommendation |
|---|---:|---|---|---|
| `khmer-tts` | 317 MB | MMS VITS (torch) | **Ran it.** 3.3 s at 16 kHz in 1.8 s. Only candidate with Khmer characters in its vocabulary. | **KEEP** if clip 01 sounds right |
| `pocket-tts-4bit` | 85 MB | MLX (`pocket_tts`) | **Ran it.** Cloned from your "Christian" reference: 6.2 s audio in **0.73 s (8.4× realtime)**, 1.9 GB peak. English round-trip `logprob -0.13` — the best measured. | **KEEP** if clip 05 sounds right |
| `indextts2-2.5` | 9.8 GB | MLX (`indextts`) | **Not runnable as stored.** Its config needs `qwen0.6bemo4-merge/`, which is absent. README says "INTERNAL QUALIFICATION ONLY. Do not add to the production catalog." | **HOLD/CUT** — biggest archive item, incomplete |
| `chatterbox-resembleai` | 3.0 GB | PyTorch (needs `chatterbox` pkg, not installed) | Original checkpoint. The MLX `chatterbox-4bit` in the kit **failed** its audit. | **CUT candidate** — test only if you want it |
| `f5-tts` | 1.3 GB | PyTorch (needs `f5_tts` pkg, not installed) | The app catalog lists F5-TTS but never packaged it. | **CUT candidate** |
| `csm-1b` | 7.2 MB | Sesame | **No weights** — config + prompts only. | **CUT** |
| `kokoro-82m-prince-canuma` | 512 KB | — | Voices folder only, not a model. | **CUT** |
| `orpheus-3b-0.1-ft-bf16` | 27 MB | llama | **No weights** — config + tokenizer only. | **CUT** |

Archive-only total ≈ **14.5 GB**, of which 12.8 GB is the three heavy items
(`indextts2-2.5` 9.8, `chatterbox-resembleai` 3.0, `f5-tts` 1.3).

## The 29 duplicates

The remaining voice models are byte-for-byte the same models the kit already ships. If the kit
keeps a model, the archive copy is redundant unless the archive is deliberately the master
store. Cutting all 29 would free **≈ 62.8 GB per drive**.

`audio8-tts-preview-0-6b-bf16`, `chatterbox-4bit`, `chatterbox-8bit`, `chatterbox-turbo-4bit`,
`echo-tts-base`, `fish-audio-s2-pro-8bit`, `fish-audio-s2-pro-bf16`, `fish-s1-dac-min`,
`kokoro-82m-bf16`, `longcat-audiodit-1b-4bit`, `moss-audio-tokenizer-nano`,
`moss-tts-nano-100m`, `omnivoice-bfloat16`, `orpheus-3b-0-1-ft-4bit`,
`qwen3-tts-12hz-0-6b-base-4bit`, `qwen3-tts-12hz-0-6b-base-8bit`,
`qwen3-tts-12hz-0-6b-customvoice-8bit`, `qwen3-tts-12hz-1-7b-base-4bit`,
`qwen3-tts-12hz-1-7b-base-8bit`, `qwen3-tts-12hz-1-7b-customvoice-8bit`,
`qwen3-tts-12hz-1-7b-voicedesign-8bit`, `s3tokenizerv2`, `snac-24khz`, `umt5-base`,
`vibevoice-realtime-0-5b-4bit`, `voxcpm2-4bit`, `voxcpm2-8bit`, `voxcpm2-bf16`,
`voxtral-4b-tts-2603-mlx-4bit`.

## Owner decisions so far (2026-09-24)

- **Keep all three Khmer models** — `khmer-tts`, OmniVoice and VoxCPM2 were all judged good.
- **Cut the 29 duplicate archive copies** — done on all four drives (see below).
- **`pocket-tts` — KEEP and add to the app.** Best measured result of anything tested, and one of
  only two engines with an 8 GB path.
- **`f5-tts` — CUT.** Good clone quality but 40.2 s per sentence on CPU and needs import/audio
  workarounds; clone capability is already covered.
- **Next: walk the app kit's packaged models** the same way, then prune the app catalog + kit
  library.
- **`chatterbox-resembleai`** — not tested (network blocker); its MLX equivalent already failed
  its audit.

## Action taken: duplicate archive copies moved

All 29 duplicates were moved on **all four drives** to
`/Volumes/<drive>/_trash/archive-voice-dupes-2026-09-24/`, with a `NOTE.txt` recording why.
Nothing was deleted. Verified first: upstream revision matched the manifest pin for every one,
and every payload file matched the kit copy by name and size — the only difference found
anywhere was a 6 KB `.DS_Store`.

| Drive | Moved | Free after |
|---|---:|---|
| KH-STUDIO-ATLAS | 29 | 408 GB |
| KH-STUDIO-NOVA | 29 | 424 GB |
| KH-STUDIO-ORICO | 29 | 320 GB |
| KH-STUDIO-UGREEN | 29 | 292 GB |

The kit copies at `Video Generation Kit/Studio Hub KH/model-library/` were **not** touched.
Actual space returns when each `_trash/` is emptied — the owner's call.

## Fleet fit — the 8 GB constraint

The owner's fleet (see [owner-hardware.md](owner-hardware.md)) has **two 8 GB machines as the
hard constraint**. Among everything being kept, only three have a credible 8 GB path:

| Model | Declared floor | Measured peak | 8 GB machine? |
|---|---|---:|---|
| OmniVoice bf16 | 8 GB | 3.3–3.5 GB (audit) | **yes**, as a dedicated worker (leaves <1 GB free) |
| `pocket-tts-4bit` | none declared | **1.9 GB** | **yes** |
| `khmer-tts` (VITS) | none declared | 317 MB of weights | **yes** (needs torch) |
| VoxCPM2 4-bit / bf16 | **16 GB** | — | **no** |
| Qwen3-TTS 1.7B Base 8-bit | **16 GB** | — | **no** |
| Kokoro 82M | 8 GB | — | cut by owner decision |

**Consequence:** if OmniVoice and `pocket-tts` were both dropped, the two 8 GB machines would
have **no usable TTS clone or voice-design engine at all** — only the cut Kokoro. That is a
strong argument for keeping both regardless of the Khmer question, and it means the keep list
should be described per tier rather than as one flat set.

| Tier | Voice engines |
|---|---|
| 8 GB | OmniVoice, `pocket-tts`, `khmer-tts` |
| 16 GB+ | the above, plus VoxCPM2 (design/emotion), Qwen3 1.7B Base (English clone) |

## What has been tested so far

| Model | Test | Result |
|---|---|---|
| `khmer-tts` (VITS) | Khmer render + Whisper | Real speech; ASR cannot judge Khmer — owner judged **good** |
| `OmniVoice` bf16 | Khmer render + Whisper | Same — owner judged **good** |
| `VoxCPM2` 8-bit | Khmer render + Whisper | Owner judged **good**, though its tokenizer has no Khmer vocabulary |
| `VoxCPM2` 8-bit | English control | `logprob -0.32` — pipeline sound |
| `pocket-tts-4bit` | English clone, sentence | `logprob -0.13`, 6.2 s audio in 0.73 s, 1.9 GB peak |
| `pocket-tts-4bit` | English clone, 284-char paragraph | `logprob -0.059` — near-perfect, 1.4 s |
| `f5-tts` v1 Base | English clone, sentence | `logprob -0.33` (good) but **40.2 s on CPU** for one sentence; needed a stubbed `wandb`/`datasets` import chain and a `torchaudio.load` shim to avoid TorchCodec |
| `chatterbox-resembleai` | — | **BLOCKED.** The upstream PyTorch loader downloads a watermarker asset from `release-assets.githubusercontent.com` on construction; the download times out here. Not tested. |

**Whisper turbo cannot identify Khmer** (it labels even the purpose-built MMS model as
Vietnamese), so Khmer is ear-only. English round-trips are reliable and pocket-tts scores the
best of anything measured.

## Audio for judgement

`~/Desktop/StudioHub TTS Review/` — seven clips and a scoring sheet:

- `01 khmer-tts VITS - KHMER.wav`
- `02 OmniVoice - KHMER.wav`
- `03 VoxCPM2 8bit - KHMER.wav`
- `04 English control - VoxCPM2.wav`
- `05 pocket-tts 4bit - ENGLISH (cloned).wav`
- `06 pocket-tts 4bit - ENGLISH paragraph (cloned).wav`
- `07 f5-tts v1 Base - ENGLISH (cloned).wav`

## Environment used for the tests

A throwaway venv at `.build/tts-test-venv` was created with `--system-site-packages` so it
inherits the build runtime's torch without modifying the runtime tree. It holds `chatterbox-tts`
0.1.7, `f5-tts` 1.1.22 and their inference dependencies, plus a minimal `wandb` stub. **The app's
`.build/voice-runtime` was not modified.** Both directories are gitignored build output.

## App kit batch — 14 samples generated (2026-09-24)

Generated from the drive's kit library through the real `mlx_audio` path, offline, with the
five companion packages (`snac`, `s3tokenizer`, `umt5`, MOSS tokenizer, fish DAC) shimmed into a
project-local HF cache. All 14 that produced audio round-trip intelligibly in English
(`logprob -0.17` to `-0.54`). Renders are numbers 08–21 in the Desktop review folder.

| Package | Load | Generate | Round-trip |
|---|---:|---:|---|
| `voxtral-4b-tts-2603-mlx-4bit` | 4.1 s | 5.1 s | -0.29 |
| `chatterbox-4bit` | 2.4 s | 5.2 s | -0.45 |
| `chatterbox-turbo-4bit` | 0.9 s | 1.9 s | -0.23 |
| `chatterbox-8bit` | 2.2 s | 3.5 s | -0.54 |
| `qwen3-0.6b-base-8bit` | 2.8 s | 4.8 s | -0.18 |
| `qwen3-0.6b-base-4bit` | 2.6 s | 2.4 s | -0.17 |
| `qwen3-1.7b-base-4bit` | 3.2 s | 3.4 s | -0.21 |
| `qwen3-0.6b-customvoice-8bit` | 3.0 s | 2.9 s | -0.20 |
| `qwen3-1.7b-customvoice-8bit` | 4.5 s | 2.0 s | -0.25 |
| `qwen3-1.7b-voicedesign-8bit` | 4.5 s | 2.2 s | -0.30 |
| `longcat-audiodit-1b-4bit` | 2.0 s | 7.1 s | -0.25 |
| `echo-tts-base` | 10.6 s | **56.0 s** | -0.27 |
| `fish-audio-s2-pro-8bit` | 10.5 s | 16.8 s | -0.25 |
| `fish-audio-s2-pro-bf16` | 16.4 s | 20.5 s | -0.29 |

### Failures found in the kit — root causes (investigated 2026-09-24)

- **`vibevoice-realtime-0-5b-4bit` cannot load — this is a real bug.** The kit checkpoint
  declares `model_type: vibevoice_streaming`, which `mlx_audio` 0.4.7 remaps to `vibevoice`
  via `MODEL_REMAPPING`. But `get_model_class()` applies the remap only inside
  `if model_name is not None and model_type_mapped != model_type:` — and the checkpoint supplies
  no `model_name`, so the loader passes an **empty list**. `[] is not None` is true, the
  `for part in []` loop never runs, the `elif` that would have applied the remap is skipped, and
  the import is attempted as `mlx_audio.tts.models.vibevoice_streaming`, which does not exist.
  The app calls the same `load_model`, so **the app's VibeVoice offer fails too.** A one-line
  config `"model_name": "vibevoice"` or an upstream fix would resolve it.
- **`audio8-tts-preview-0-6b-bf16` is NOT broken in the app.** Re-running with the app's own
  `verbose=False` workaround produced a valid file. Only the naive path fails.
- **`moss-tts-nano-100m`** loads (0.5 s) and reports success but writes no file even with the
  reference clip the app's resolver requires. Needs the app's own call path to confirm.
- **`orpheus-3b-0-1-ft-4bit`** fails offline because it resolves assets by repo id; the app's
  local companion wiring is what makes it work. Not testable standalone.
- **`echo-tts-base` takes 56 s** for one sentence — the slowest by 3×.

## Follow-up renders (owner requests, 2026-09-24)

- **`qwen3-1.7b-base-8bit` — OK.** 9.5 s load, 5.1 s generate, round-trip `-0.20`. Clip 26.
- **`audio8-tts-preview-0-6b-bf16` — OK through the app's own `verbose=False` workaround.**
  3.4 s load, 2.2 s generate, round-trip `-0.27`. Clip 27. The offer is **not** broken.
- **`moss-tts-nano-100m` — still blocked offline.** With the offline cache populated it now gets
  past load and reaches "transcribing ref_audio…", then needs a Whisper repo that is not in the
  cache. The app's own cache module supplies it.
- **`orpheus-3b-0-1-ft-4bit` — still blocked offline.** Its loader resolves a model repo by id at
  a revision the offline cache does not hold.
- Also shimmed all 37 kit packages into a project-local HF cache
  (`.build/tts-hf-cache`) so offline runs resolve; this is build output, not a repo change.

**Conclusion:** moss and orpheus need the running app's cache module to be tested faithfully.
A standalone harness cannot settle them.

## Not yet tested

- `chatterbox-resembleai` — blocked on the watermarker download; its MLX equivalent already
  failed its audit.
- `indextts2-2.5` — cannot run as stored; its `qwen0.6bemo4-merge` emotion asset is missing.
- `moss-tts-nano` and `orpheus` — see above; need the app worker.
- The other shelves (image 53.2 GB, music 10.7 GB, llm 8.6 GB, transcription 3.6 GB) have not
  been walked; image is the second-largest saving after voice.

## Nothing has been removed

No catalog entry, manifest or model folder has been changed. The four duplicate clips created
during this review were moved to `_trash/desktop-tts-review-dupes/`, not deleted.
