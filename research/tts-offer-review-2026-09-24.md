# TTS offer review — pruning the menu to make room for video

Date: 2026-09-24. Review plus local verification. No model was removed, moved or edited; the
only disk written was a tiny test script and two audio renders under `.build/tts-verify/`
(gitignored build output). Nothing here is a redistribution or product decision.

Owner's goal: the Voice Studio menu is larger than it is useful, and the space is wanted for a
video model. This note inventories what is offered, what has real evidence behind it, what
cannot run on an offline kit node, and what the verification actually proved.

## Headline findings

1. **Voice Studio offers 39 entries across 18 families.** The shipped voice library is
   **67.46 GB across 29 packages** (`engine=voice-mlx`).
2. **15 of the 39 offers have no packaged manifest.** They are download-only, so on an offline
   kit node they cannot be obtained. This is the largest block of never-used options.
3. **Only 5 TTS models have a passed audit**: Kokoro 82M bf16, Qwen3 0.6B Base 8-bit,
   Qwen3 1.7B Base 8-bit, Qwen3 1.7B Base 4-bit, OmniVoice bf16.
4. **2 offers failed their audit and are still on the menu** —
   `Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit` and `chatterbox-4bit` (the latter is labelled
   **"recommended"**).
5. **VoxCPM2's Khmer claim does not survive verification.** VoxCPM2 is the only family that
   enumerates Khmer (`km`), but its tokenizer contains **zero Khmer characters** — 97% of Khmer
   text becomes raw byte-fallback tokens — and the same holds for Burmese (95%), Lao (94%) and
   mostly Thai (67%). See
   [Language verification](#language-verification--what-was-actually-measured).
6. **No offered model is proven to speak Khmer, and the automatic check cannot settle it.**
   OmniVoice (audit-passed, claims 646 languages, not enumerated) is the best candidate inside
   the app; a purpose-built Khmer VITS model sits unused in the drive archive. Whisper turbo
   cannot identify Khmer at all — it labels even the purpose-built Khmer model as Vietnamese —
   so **these must be judged by ear, not by transcribe-back.**

## What is offered, and what backs it

"Audit" = a receipt under `components/voice/model-audits/`. "Packaged" = a JSON manifest in
`model-manifests/`, which is what the offline kit ships and what
`packaging/release_kit.py` requires to be present in the library.

| Offer | GB | Khmer | Audit | Packaged |
|---|---:|:--:|---|---|
| VoxCPM2 4-bit | 2.30 | claimed — **disproved** | none | yes |
| VoxCPM2 8-bit | 3.23 | claimed — **disproved** | none | yes |
| VoxCPM2 bf16 | 4.96 | claimed — **disproved** | none | yes |
| OmniVoice 0.6B bf16 | 2.04 | claimed 646, not enumerated | **passed** | yes |
| Qwen3-TTS 1.7B Base 8-bit | 3.10 | no | **passed** (production v3) | yes |
| Qwen3-TTS 1.7B Base 4-bit | 2.34 | no | **passed** (v3-derived) | yes |
| Qwen3-TTS 0.6B Base 8-bit | 1.99 | no | passed, narrow scope | yes |
| Qwen3-TTS 0.6B Base 4-bit | 1.71 | no | conditional only | yes |
| Kokoro 82M bf16 | 0.34 | no | **passed** | yes |
| Qwen3-TTS 0.6B CustomVoice 8-bit | 1.97 | no | **FAILED** | yes |
| Qwen3-TTS 1.7B CustomVoice 8-bit | 3.08 | no | none | yes |
| Qwen3-TTS 1.7B VoiceDesign 8-bit | 3.08 | no | none | yes |
| Chatterbox 4-bit | 0.61 | no | **FAILED** | yes |
| Chatterbox 8-bit | 0.94 | no | none | yes |
| Chatterbox Turbo 4-bit | 0.42 | no | none | yes |
| Orpheus 3B 4-bit | 1.89 | no | none | yes |
| VibeVoice Realtime 0.5B 4-bit | 0.73 | no | none | yes |
| Voxtral-4B-TTS 4-bit | 2.54 | no | none | yes |
| Audio8 TTS Preview 0.6B bf16 | 2.55 | no | none | yes |
| MOSS-TTS-Nano 100M | 0.29 | no | none | yes |
| LongCat AudioDiT 1B 4-bit | 1.42 | no | none (marked "internal candidate") | yes |
| Fish Audio S2 Pro 8-bit | 6.73 | no | none | yes |
| Fish Audio S2 Pro bf16 | 11.01 | no | none | yes |
| Echo-TTS base | 5.60 | no | none | yes |
| F5-TTS v1 Base | ~1.3 | no | none | **no** |
| Bark | ~4.5 | no | none | **no** |
| Spark-TTS 4-6bit / 8-bit / bf16 | ~7.6 total | no | none | **no** |
| Orpheus 8-bit / bf16 | ~9.8 | no | none | **no** |
| KittenTTS Mini 4-bit / fp16, Micro, Nano | ~0.64 | no | none | **no** |
| VibeVoice 8-bit / fp16 | ~3.3 | no | none | **no** |
| Voxtral-4B-TTS bf16 | ~8.0 | no | none | **no** |
| Marvis TTS 250M | ~2.3 | no | none | **no** |

Dependency-only packages: `s3tokenizerv2` (0.49, Chatterbox), `snac-24khz` (0.08, Orpheus),
`umt5-base` (0.02, LongCat), `moss-audio-tokenizer-nano` (0.09, MOSS), `fish-s1-dac-min`
(1.87, Echo-TTS).

### Two "recommended" labels are wrong

- `chatterbox-4bit` is labelled **recommended**, but its audit is **failed**: Hebrew failed
  quality on 8, 16 and 24 GB; Japanese failed on 8 GB.
- `Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit` is offered as "M1 preset speakers" but its audit is
  **failed** (it produced 9 preset voices, so it runs — this is a quality/approval failure).

Both should go regardless of scenario, or the app keeps recommending a model its own evidence
rejects.

## Language verification — what was actually measured

All tests ran locally with the app's own runtime (`.build/voice-runtime`, mlx-audio 0.4.7,
torch 2.14) against models already on the Mac or on the mounted kit drive. No model was
downloaded.

### The objective finding: VoxCPM2 has no Khmer vocabulary

VoxCPM2 uses a `LlamaTokenizer` (vocab 73,440) with byte fallback. Encoding each language it
claims and counting raw-byte tokens:

| Claimed language | byte-fallback tokens | Verdict |
|---|---:|---|
| English | 0% | real tokens |
| Chinese | 0% | real tokens |
| Greek | 0% | real tokens |
| Russian | 0% | real tokens |
| Arabic | 8% | real tokens |
| Japanese | 17% | real tokens |
| Vietnamese | 29% | partial |
| Hebrew | 38% | partial |
| Korean | 56% | partial |
| Hindi | 58% | partial |
| **Thai** | **67%** | byte-fallback only |
| **Lao** | **94%** | byte-fallback only |
| **Burmese** | **95%** | byte-fallback only |
| **Khmer** | **97%** | byte-fallback only |

For Khmer, **none** of the 27 distinct codepoints used appear as single tokens in the
vocabulary; 135 of 139 tokens were `<0xE1>`-style raw bytes. VoxCPM2 has no Khmer vocabulary at
all, so the catalog's `km` enumeration and the "30 languages" claim are wrong for Khmer,
Burmese and Lao, and weak for Thai. VoxCPM2 is still strong for English, Chinese, Arabic, Greek,
Russian and partial Japanese/Korean/Vietnamese/Hindi/Hebrew, with voice design and emotion — it
is simply not a Khmer engine.

### The audio comparison — and why it is not decisive

The same 47-character Khmer sentence was rendered by three candidates and transcribed back with
the app's Whisper turbo. The English control validates the harness:

| Render | Model | Whisper (forced km) | avg logprob |
|---|---|---|---|
| English control | VoxCPM2 8-bit | `"Hello, my name is Sofea. Today the weather is very nice."` | **-0.32** |
| Khmer | VoxCPM2 8-bit | unintelligible | -2.15 |
| Khmer | OmniVoice bf16 | unintelligible | -2.11 |
| Khmer | MMS VITS `khmer-tts` | unintelligible | -1.87 |

**Whisper turbo cannot identify Khmer.** Run in auto-detect it labels the purpose-built Khmer
MMS VITS model as **Vietnamese** (`logprob -3.35`), OmniVoice as Vietnamese and VoxCPM2 as
Thai. Because it fails on a model that certainly does speak Khmer, this test **cannot rank the
three**. It proves the audio is real speech, not which candidate is intelligible.

### The candidates to judge by ear

| Candidate | Where | Size | Khmer signal |
|---|---|---:|---|
| `khmer-tts` (MMS VITS) | drive archive `voice-models/`, **not** in the app | 0.32 GB | purpose-built; 74-token vocab includes Khmer characters; needs torch, not integrated |
| OmniVoice bf16 | in the app kit | 2.04 GB | byte-level BPE covers Khmer with 0 unknown tokens; 646-language claim; audit-passed |
| VoxCPM2 8-bit | installed on the Mac | 3.23 GB | **no Khmer vocabulary — weak by construction** |

Clips: `.build/tts-verify/render-vits/audio.wav` (MMS VITS),
`.build/tts-verify/render-omni/audio.wav` (OmniVoice),
`.build/tts-verify/render/audio.wav` (VoxCPM2), `.build/tts-verify/render-en/audio.wav`
(English control). **No automatic check can settle Khmer — this needs the owner's ear.**


## Disk accounting

Shipped voice catalog per engine (what travels inside every kit):

| Engine | Packages | Size |
|---|---:|---:|
| `voice-mlx` (TTS + codecs) | 29 | **67.46 GB** |
| `music-mlx` | 2 | 14.59 GB |
| `subtitle-mlx` (Whisper) | 3 | 2.25 GB |
| `image-mflux` (FLUX.2 Klein) | 1 | 4.62 GB |

Measured on the four mounted drives (2026-09-24). **Every drive carries two libraries:**

| Location | Contents | Size |
|---|---|---:|
| `Video Generation Kit/Studio Hub KH/model-library/` | 36 packages, the app's shipped set | **~85 GB** |
| `model-library/` (top-level archive, app-independent) | 53 models | **~153 GB** |

That is ~238 GB per drive, ~950 GB across the four. The archive is shelf-organised
(`voice-models/` 77.3 GB, `image-models/` 53.2 GB, `music-models/` 10.7 GB, `llm-models/`
8.6 GB, `transcription-models/` 3.6 GB) and includes a **reserved, empty `video-models/`
shelf** — the intended home for the video model. It also holds voice models the catalog never
offered, including `khmer-tts` (MMS VITS), `indextts2-2.5` (9.8 GB), `chatterbox-resembleai`
(3.0 GB) and bf16 variants of Orpheus and F5.

The Mac's own `~/StudioHubModels` holds only ~26 GB across 12 folders (the kit filters by RAM at
copy time). The reclaimable space is on the drives, in both libraries.

## Proposed keep/cut

Confirmed preferences: clone first, voice design wanted, emotion wanted, presets not important,
Khmer + English.

**Keep (~12.4 GB):**

| Model | GB | Why |
|---|---:|---|
| OmniVoice 0.6B bf16 | 2.04 | only audit-passed clone **and** design model; expressive tags; Khmer candidate pending your ear |
| VoxCPM2 4-bit | 2.30 | voice design + emotion; the languages it genuinely tokenises; 16 GB floor |
| VoxCPM2 bf16 | 4.96 | final-render quality tier (your call to keep both) |
| Qwen3-TTS 1.7B Base 8-bit | 3.10 | strongest audit-passed English/10-language clone |

Kokoro is cut (preset-only, and presets are not important).

**Cut (~55.1 GB of shipped voice packages), plus all 15 unpackaged offers:**

VoxCPM2 8-bit; the 0.6B Qwen tiers; both CustomVoice tiers; Qwen3 1.7B VoiceDesign and Base
4-bit; all three Chatterbox + `s3tokenizerv2`; Orpheus + `snac-24khz`; VibeVoice; Voxtral;
Audio8; MOSS + tokenizer; LongCat + `umt5-base`; Fish Audio ×2; Echo-TTS + `fish-s1-dac-min`;
and from the catalog only, F5-TTS, Bark, Spark-TTS, KittenTTS, Orpheus 8-bit/bf16, VibeVoice
8-bit/fp16, Voxtral bf16, Marvis.

**Separate decision — the drive archive.** The archive duplicates most of the kit's voice
packages at a second ~77 GB per drive; pruning it is the single largest saving. But it holds the
only genuine Khmer model found (`khmer-tts`) plus models the app never offered, so it should be
reviewed shelf by shelf rather than by the same cut list.

## What verification proved about the wiring

- Every family is genuinely wired (`_WIRED_FAMILIES`, `MLX_AUDIO_FAMILIES`); no offer is dead
  code, so pruning is a product choice, not a bug fix.
- `voxcpm_flex` resolves clone + `instruct` design + emotion in one mode; `omnivoice` resolves
  design + cloning. The confirmed capabilities are covered by the keep list.
- No production subsystem depends on any cut candidate. References are confined to
  `catalog.py`, the frontend's dynamic model list, tests, qualification scripts and docs.
- The frontend has **no hardcoded model list** — it renders whatever `/api/catalog` returns, so
  removing entries needs no frontend edit beyond dead knob cleanup.
- No test asserts the catalog or family count; only `tests/test_model_manifest_catalog.py`'s
  `EXPECTED` dict and `tests/test_section_policy.py` name specific models, and those need
  updating when manifests are removed.
- A stored selection pointing at a removed model degrades to a clear 404, not a crash.

## Open verification before any removal

1. **Your ear on the three Khmer clips** (paths above). No automatic check can judge Khmer, and
   the archive's purpose-built MMS VITS model may already be the answer — if so, the app's Khmer
   story is "expose `khmer-tts`", not "keep VoxCPM2 for Khmer".
2. **Shelf-by-shelf review of the 153 GB archive on one drive** before touching the other three.
3. **Confirm the other three drives match ATLAS** before any fleet-wide prune.

## Decisions needed

1. Which Khmer candidate sounds right — MMS VITS `khmer-tts`, OmniVoice, or VoxCPM2?
2. Confirm the keep list and the cut list above.
3. Prune the drive archive too, or keep it as the reference store?
4. Then I do the removal in one atomic pass per `AGENTS.md`: catalog + manifests + library
   folders moved to `_trash/` (never deleted), tests updated, kit manifest regenerated,
   `verify-kit.sh` re-run, and `copy-models.command` / `install_app.command` updated.

## How removal would work (not started)

1. **Voice catalog** — delete `Family`/`ModelEntry` blocks in
   `components/voice/app/backend/catalog.py` and fix the tests that name them
   (`test_catalog_language_support.py`, `test_priority_mlx_models.py`,
   `test_model_audit_contract.py`, `test_qwen_revision_evidence.py`,
   `test_reference_audio_contract.py`, `test_section_size_control.py`,
   `test_wave1_qualification.py`, `test_generation_artifact_contract.py`,
   `test_model_storage.py`, plus `tests/test_model_manifest_catalog.py` and
   `tests/test_section_policy.py`).
2. **Packaged catalog** — delete the matching `model-manifests/*.json`, atomically with the
   library folder: `release_kit.py` exits non-zero when an advertised package is missing.
3. **Physical packages** — move folders out of each kit's
   `Video Generation Kit/Studio Hub KH/model-library/` and `~/StudioHubModels/` into `_trash/`
   per `AGENTS.md`. Moving into a same-volume `_trash/` does **not** free space until you empty
   it, so the space returns only when you say so.
4. **Regenerate** the kit manifest and re-run `verify-kit.sh` on each drive.
5. **Update** `docs/tasks/HANDOFF.md` at the next release.
