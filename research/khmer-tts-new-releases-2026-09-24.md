# Newly released Khmer-capable TTS — 2026-09-24

Owner request: "check for any newly released TTS that supports Khmer that I don't already
serve." Read-only web research. **No model was downloaded, installed, catalogued or removed.**
Nothing here is an integration or product decision.

Companion to [tts-offer-review-2026-09-24.md](tts-offer-review-2026-09-24.md) and
[tts-archive-walkthrough-2026-09-24.md](tts-archive-walkthrough-2026-09-24.md).

## What "already serve" means here

The Voice Studio catalog (`components/voice/app/backend/catalog.py`) offers 18 TTS families.
Only two of them were actually judged to speak Khmer well:

| Served family | Khmer status |
|---|---|
| **VoxCPM2 (MLX)** | Owner judged the 8-bit render **good** (tokenizer has no Khmer vocabulary, so quality is a surprise, but the ear ruled) |
| **OmniVoice (MLX)** | Owner judged the bf16 render **good**; audit-passed |
| Qwen3-TTS | Explicitly **rejects** Khmer in its own audit evidence |

Not served, but already owned: the purpose-built MMS VITS **`khmer-tts`** sits in the drive
archive (`model-library/voice-models/`), not in the app catalog. §"Aliases to what we own"
below explains why several "new" hits are really this model.

## Genuinely new, Khmer-capable, not served

| Model / work | Released | Type | Khmer evidence | Verdict |
|---|---|---|---|---|
| **VoxCPM2 + Khmer LoRA (r=64)** — Pov et al., arXiv [2606.26618](https://ar5iv.labs.arxiv.org/html/2606.26618) | 2026-06 | LoRA adapter on the **already-served** VoxCPM2 base | Native-speaker MOS **3.85 → 4.23** (p<0.001); prosody 3.76 → 4.36; rank chosen by listening. First parameter-efficient Khmer adaptation of a foundation TTS model | **Most relevant find.** Bolts onto a model already in the kit; cheapest path to better Khmer |
| [`Mariya987/VoxCPM2-khmer-DDD-khmer-speech-LoRA-Fine-tuned`](https://huggingface.co/Mariya987/VoxCPM2-khmer-DDD-khmer-speech-LoRA-Fine-tuned) | 2026-07-04 | VoxCPM2 LoRA, DDD-Cambodia dataset | Gated repo; no public MOS | Worth requesting access; unverified |
| [`sengtha/khmer-tts-female-v2`](https://huggingface.co/sengtha/khmer-tts-female-v2) | 2026-07-14 | **Native Khmer VITS**, ONNX, 22.05 kHz, single female | Trained from scratch on ~727 h DDD-Cambodia read speech (not an MMS derivative); plugin/on-device use | **New real Khmer voice.** Closest to a drop-in offline voice; single-speaker, no cloning |
| [`AnotherPotatoCoder/Khmer-TTS`](https://huggingface.co/AnotherPotatoCoder/Khmer-TTS) (SimpleKhmerTTS) | 2026-07-24 | Decoder-only transformer + WavTokenizer | DDD-Cambodia data; model card admits **hallucination/babbling on rare words**, no chunking, 3–10 s clips | Research-grade; not deployment-ready |

## New big-model releases checked — none added Khmer

These are the recent headline TTS releases; all were checked against their language lists and
**none cover Khmer** (so none is a new Khmer option):

- **Chatterbox Multilingual V3** (Resemble) — 20+ languages, no Khmer; and the served
  `chatterbox-4bit` audit already failed. [link](https://www.resemble.ai/learn/models/chatterbox-multilingual)
- **Higgs Audio v2** (`bosonai`, MLX q8 port) — en/zh/ko/de/es only.
- **JetlinkTTS-v1.0-2B** — an Apache-2.0 **organization-managed copy of VoxCPM2**; its 30-language
  list includes Khmer, but it is the same base the app already serves, not new capability.
- **KugelAudio-0-open**, **MOSS-TTS v1.5**, **IndexTTS-2.5**, **VieNeu-TTS v3**, **Sopro v2**,
  **Supertonic-3** — language lists do not include Khmer.

## Aliases to what we already own (do not re-add)

Several Hub hits with "Khmer" in the name are re-hosts or duplicates rather than new models:

- [`phonsobon/TTS-Khmer-Checkpoint`](https://huggingface.co/phonsobon/TTS-Khmer-Checkpoint)
  (2026-08-14) — explicit re-host of `facebook/mms-tts-khm`, i.e. the same lineage as the
  owned `khmer-tts`.
- `reaksmeydev/VoxCPM2-khmer` (2026-08-06), `sumnim/VoxCPM2-Khmer` (2026-05-14),
  `ravuthz/VoxCPM2-khmer` (2026-04-25) — VoxCPM2 copies/re-uploads; no verified Khmer
  fine-tune or eval.
- `KrorngAI/mms-tts-khm-finetuned` (2025-07) — older MMS Khmer fine-tune, same VITS family.
- `meanleap/mms-tts-khm-test` (2026-07-02) — test repo.

## Cloud-only Khmer voices (outside the offline kit's scope)

For completeness: **Azure Speech** lists Khmer neural voices (`km-KH`, e.g. Sreymom/Piseth) and
**Google Cloud TTS** lists Khmer voices. Both are online APIs; they cannot ship inside an
offline kit node and are out of scope for Voice Studio. No ElevenLabs/Gemini Khmer support was
confirmed.

## Bottom line

1. **No new foundation TTS model released in 2026 added Khmer.** Every Khmer-capable option is
   still built on one of three lineages: **VoxCPM2**, **OmniVoice**, or **Meta MMS VITS**.
2. The one genuinely new, evidence-backed capability is **Khmer LoRA adaptation of VoxCPM2** —
   the arXiv 2606.26618 r=64 adapter (MOS +0.38, native speakers) and the gated Mariya987
   LoRA. Because VoxCPM2 is already served, this is the lowest-effort upgrade path to better
   Khmer in Voice Studio.
3. The one genuinely new standalone voice is **`sengtha/khmer-tts-female-v2`** — a native
   VITS/ONNX single-speaker Khmer voice, offline-capable, but noise-free single-speaker only
   (no cloning, no design).
4. Everything else new is either a re-host of an owned model or a general-purpose model with no
   Khmer coverage.

## Suggested next step (not started)

Render the same Khmer test sentence through (a) the VoxCPM2 Khmer LoRA and (b)
`sengtha/khmer-tts-female-v2`, alongside the existing `khmer-tts` / OmniVoice / VoxCPM2 clips,
and let the owner judge by ear — Whisper cannot score Khmer
(see [tts-offer-review-2026-09-24.md](tts-offer-review-2026-09-24.md) §language verification).
That needs a download decision first.
