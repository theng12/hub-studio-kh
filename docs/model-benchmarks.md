# Model record — what runs on which Mac, and what was measured

**What this is.** One page for every model this fleet ships: the memory it asks for, whether
that number was *measured* or only *declared*, and what actually happened when it ran. It is
generated from the manifests and the stored evidence — do not edit it by hand; run
`qualification/model_record.py --write` and it is rebuilt from the records.

**How to read it.** `[M]` measured — a run left a record, and the source is named.
`[V]` the publisher's claim. `[I]` inference, ours, and said so. A tier with no `[M]` beside
it is a *declared floor from catalog metadata*, which is a starting point rather than proof.
**Every model is named with its quantization**, because a model named without it cannot be
checked against what is actually on disk.

## Your machines

| Unified memory | Chips | How many |
| --- | --- | ---: |
| 8 GB | Apple M1, M2 | 35 |
| 16 GB | Apple M1, M2, M4 | 15 |
| 24 GB | Apple M4 | 2 |
| 32 GB | Apple M2 Max (Mac Studio) | 1 |

## What each machine can run

Read this as the **declared** floor, not as a verdict — a model whose floor is above a Mac
may still run there, and one at or below may still not (see the peak-memory note at the end).

| Model | Engine | RAM floor | 8 GB | 16 GB | 24 GB | 32 GB |
| --- | --- | --- | --- | --- | --- | --- |
| **Chat (LLM)** | | | | | | |
| Qwen3.5-9B 4-bit | MLX (text) | 16 GB | no | yes | yes | yes |
| **Voice (TTS)** | | | | | | |
| Chatterbox 4-bit (MLX, 2026 build) — recommended | MLX (speech) | 8 GB (recommended 16 GB) | yes | yes | yes | yes |
| Chatterbox 8-bit (MLX, 2026 build) | MLX (speech) | 8 GB (recommended 16 GB) | yes | yes | yes | yes |
| Khmer TTS Female (VITS/ONNX) — single speaker | VITS / ONNX | 8 GB (recommended 16 GB) | yes | yes | yes | yes |
| Khmer TTS (MMS VITS) — single speaker | VITS / ONNX | 8 GB (recommended 16 GB) | yes | yes | yes | yes |
| OmniVoice 0.6B bf16 (MLX) — recommended | MLX (speech) | 8 GB (recommended 16 GB) | yes | yes | yes | yes |
| Pocket TTS 4-bit (MLX) — English clone or preset | MLX (speech) | 8 GB (recommended 16 GB) | yes | yes | yes | yes |
| pocket-tts-preset-voices *(supporting files)* | MLX (speech) | 8 GB | yes | yes | yes | yes |
| pocket-tts-tokenizer *(supporting files)* | MLX (speech) | 8 GB | yes | yes | yes | yes |
| Qwen3-TTS 0.6B Base 4-bit (MLX) — lighter | MLX (speech) | 8 GB (recommended 16 GB) | yes | yes | yes | yes |
| Qwen3-TTS 0.6B Base | MLX (speech) | 8 GB (recommended 16 GB) | yes | yes | yes | yes |
| s3tokenizerv2 *(supporting files)* | MLX (speech) | 8 GB (recommended 16 GB) | yes | yes | yes | yes |
| VoxCPM2 4-bit (MLX) — recommended | MLX (speech) | 8 GB (recommended 16 GB) | yes | yes | yes | yes |
| Qwen3-TTS 1.7B Base — quality voice cloning (MLX 4-bit) | MLX (speech) | 12 GB (recommended 16 GB) | no | yes | yes | yes |
| Breeze TTS 2 4-bit (MLX) | MLX (speech) | 16 GB | no | yes | yes | yes |
| VoxCPM2 8-bit (MLX) — balanced | MLX (speech) | 16 GB (recommended 24 GB) | no | yes | yes | yes |
| VoxCPM2 bf16 (MLX) — full precision | MLX (speech) | 16 GB (recommended 24 GB) | no | yes | yes | yes |
| Fish Audio S2 Pro 5B 8-bit (MLX) — fleet candidate | MLX (speech) | 24 GB | no | no | yes | yes |
| Fish Audio S2 Pro 5B bf16 (MLX) | MLX (speech) | 32 GB | no | no | no | yes |
| **Subtitle (speech-to-text)** | | | | | | |
| Whisper Base | MLX (speech-to-text) | 8 GB | yes | yes | yes | yes |
| Whisper Small | MLX (speech-to-text) | 8 GB | yes | yes | yes | yes |
| Whisper Large v3 Turbo *(supporting files)* | MLX (speech-to-text) | 8 GB | yes | yes | yes | yes |
| **Image** | | | | | | |
| Z-Image-Turbo 6B MLX 4-bit | mflux (MLX) | 8 GB (recommended 16 GB) | yes | yes | yes | yes |
| FLUX.2 Klein 4B MLX 4-bit | mflux (MLX) | 16 GB | no | yes | yes | yes |
| **Music** | | | | | | |
| Stable Audio 3 Medium (1.4B) | MLX (audio) | 8 GB (recommended 16 GB) | yes | yes | yes | yes |
| YuE2 3B Music (8-bit AR + bf16 NAR) | mlx-Yue2 (MLX) | 16 GB (recommended 24 GB) | no | yes | yes | yes |
| yue2-vae *(supporting files)* | mlx-Yue2 (MLX) | 16 GB (recommended 24 GB) | no | yes | yes | yes |

`yes` / `no` above is arithmetic on the published floor. It is not a measurement, and the
section below is where measurements live.

## What has actually been measured

| Model | Quantization | Machine | Result | Source |
| --- | --- | --- | --- | --- |
| Qwen3.5-9B 4-bit | uniform 4-bit MLX | 16 GB · Apple M1 | [M] completed a 256-token completion — speed 12.40–12.48, generation 20.64 / 20.51 s, load 9.08 s, peak MLX 5.23 GB, swap peak 0.25 MB, memory free at start 7.7 GB | `qualification/README-chat-tiers.md` |
| Qwen3.5-9B 4-bit | uniform 4-bit MLX | 16 GB · Apple M2 | [M] completed a 256-token completion — speed 14.14–14.02, generation 18.11 / 18.26 s, load 13.69 s, peak MLX 5.23 GB, swap peak 0.0 MB, memory free at start 8.85 GB | `qualification/README-chat-tiers.md` |
| Qwen3.5-9B 4-bit | uniform 4-bit MLX | 16 GB · Apple M4 | [M] completed a 256-token completion — speed 18.33–18.60, generation 13.97 / 13.76 s, load 12.48 s, peak MLX 5.23 GB, swap peak 0.0 MB, memory free at start 7.78 GB | `qualification/README-chat-tiers.md` |
| Z-Image-Turbo 6B MLX 4-bit | 4-bit mflux pack | 32 GiB · Apple M2 Max | [M] 1280×720, 9 steps, seed 42 — completed in both configurations — generation, default 166 s, generation, low RAM 236 s, peak MLX, default 9.84 GB, peak MLX, low RAM 6.26 GB, peak footprint, default 17.7 GB, peak footprint, low RAM 7.12 GB, swaps 0 | `qualification/z-image-turbo-2026-09-24.md` |
| FLUX.2 Klein 4B MLX 4-bit | 4-bit MLX | 32 GiB · Apple M2 Max | [M] measured beside Z-Image in the same configuration; no stored runtime of its own — peak MLX, default 12.9 GB, low-RAM mode none — this model has no bounded configuration, weights on disk 4.62 GB | `qualification/z-image-turbo-2026-09-24.md` |
| YuE2 3B Music (8-bit AR + bf16 NAR) | 8-bit AR + bf16 NAR + bf16 VAE (MLX) | 32 GiB · Apple M2 Max | [M] three songs at 24 / 36 / 120 s of 48 kHz stereo, mode off, seed fixed; the 36 s one through this app's own runner bridge and its runtime pinned by content — audio produced 24.0 / 36.0 / 120.0 s, generation 58.6 / 103.2 / 429.8 s, load 6.8 s, peak footprint 10.79 / 11.81 / 11.93 GB, peak RSS 6.23 GB, weights on disk 9.9 GB | `qualification/yue2-music-results-20260927/bridge-run.json` |

`[M]` on every row above: each is a run that left the record named in the last column.

### Voice lane runtimes, per item

These are the Voice worker's own measured per-item runtimes — the same numbers it
publishes as `runtime_seconds`, read from the probe receipts the runs left behind.

| Model | Quantization | Measured on | Runtime | Scene |
| --- | --- | --- | --- | --- |
| mlx-community/VoxCPM2-4bit | MLX 4-bit | M2 Max 32 GiB unified memory, packaged app path, 2026-09-21 | **22.29 s** | 93-character sentence cloned from the project reference clip (qualification/evidence/st04-reference-clip.wav) through the packaged app's own job path; 5.76 s artifact at 48 kHz |
| mlx-community/VoxCPM2-4bit | MLX 4-bit | M2 Max 32 GiB unified memory, packaged app path, 2026-09-21 | **20.19 s** | same 93-character sentence with the reference clip staged without a transcript, through the installed app's job path; 491,580-byte artifact |
| mlx-community/VoxCPM2-8bit | MLX 8-bit | M2 Max 32 GiB unified memory, packaged app path, 2026-09-21 | **18.13 s** | 93-character sentence cloned from the project reference clip through the packaged app's own job path; 5.60 s artifact at 48 kHz |
| mlx-community/VoxCPM2-bf16 | MLX bf16 | M2 Max 32 GiB unified memory, packaged app path, 2026-09-21 | **23.21 s** | 93-character sentence cloned from the project reference clip through the packaged app's own job path; 5.60 s artifact at 48 kHz |
| mlx-community/OmniVoice-bfloat16 | MLX bf16 | M2 Max 32 GiB unified memory, packaged app path, 2026-09-21 | **18.13 s** | 93-character sentence cloned from the project reference clip through the packaged app's own job path; 6.44 s artifact at 24 kHz |
| mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit | MLX 8-bit | M2 Max 32 GiB unified memory (this repository's local qualification host) | **26.18 s** | 2026-09-21, packaged app: 93-character sentence cloned from the project reference clip (transcript supplied); 4.88 s artifact at 24 kHz |
| mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit | MLX 8-bit | M2 Max 32 GiB unified memory (this repository's local qualification host) | **24.26 s** | 2026-09-25, installed 0.6.61 Hub bundle: the recorded smoke sentence ('This is a standalone Voice Studio test. The bundled engine is speaking clearly.') cloned from the same clip with the recorded smoke parameters (language=en, seed=42); 230,444-byte artifact, 4.80 s at 24 kHz |

**Withheld on purpose** — a model with no stored probe receipt gets no runtime rather
than a curated one:

- mlx-community/VoxCPM2-4bit — No probe receipt exists for the fleet run: the figure survives only as prose (components/voice/CHANGELOG.md 1.31.0, the catalogue comment beside the model's 16 GB memory floor, research notes). It was published as this model's runtime until 2026-09-25 on the strength of that prose. The model keeps a runtime from the stored r07 probe instead, and the prose stays where it is as the evidence for the memory floor it was measured for.
- mlx-community/pocket-tts-4bit — No probe receipt exists: the numbers live in a manifest's `qualification.memory_evidence` string, in the owner-review notes and in the comment this record replaced. The measurement happened, but a number with no stored run behind it must not be republished as a scheduler's runtime. Re-admit it by storing the run's own receipt.

## What is NOT measured

22 of 27 packages have no run behind them — their floor is
catalog metadata, which is a starting point rather than proof. That is not a defect in any
one of them; it is the honest state of the record, and it is the list of what a test would
settle:

`breeze-tts-2-mlx-4bit`, `chatterbox-4bit`, `chatterbox-8bit`, `fish-audio-s2-pro-8bit`, `fish-audio-s2-pro-bf16`, `khmer-tts-female-onnx`, `khmer-tts-mms`, `omnivoice-bfloat16`, `pocket-tts-4bit`, `pocket-tts-preset-voices`, `pocket-tts-tokenizer`, `qwen3-tts-12hz-0-6b-base-4bit`, `qwen3-tts-12hz-0-6b-base-8bit`, `qwen3-tts-12hz-1-7b-base-4bit`, `s3tokenizerv2`, `stable-audio-3-medium`, `voxcpm2-4bit`, `voxcpm2-8bit`, `voxcpm2-bf16`, `whisper-base`, `whisper-small`, `whisper-turbo`

## Two things that surprise people reading this

**A peak measured on a big Mac is inflated, and that is why a "too big" model runs on a
small one.** MLX grows its buffer cache into whatever memory is free, so the peak it reports
on a 32 GB machine is partly a property of that machine. On an 8 GB Mac the cache cannot
grow, so the same model peaks lower. This is why Z-Image-Turbo 4-bit reports a 12.9 GB-class
peak and still runs on 8 GB machines. **The Hub's RAM floor for a model is a wall, and it is the
measured peak the fleet has proven where there is one, else the manifest tier (2026-09-30).**

**Two kinds of evidence sit behind "it works".** The rows in this document are
*benchmarks*: one run, watched, with a number. FLUX.2 Klein 4B MLX 4-bit's 8 GB
qualification is not that — it rests on completed runs in ordinary use across the fleet. Both
are real evidence and they answer different questions, so a tier is never "qualified" here
without saying which kind it is.

## Adding a record

So that a future test does not become the thing you cannot remember:

1. **Run the harness, not a guess.** The kit drives carry the 8 GB image run
   (`Technical/RUN-8GB-IMAGE.command`) and the 8 GB voice run (`Technical/RUN-8GB-TTS.command`);
   the chat-tier and LTX video harnesses live in the repo's `qualification/` folder only.
2. **Keep what it wrote.** Each harness writes a folder of results next to itself. That folder
   *is* the evidence; a number pasted into a chat without it cannot be admitted.
3. **Record the reading** in `qualification/model-readings.json`, naming the record in
   `source_path`. The generator refuses a row whose source is missing.
4. **Rebuild this page**: `qualification/model_record.py --write`. The test that guards it will
   fail until you do, which is the point — the page cannot drift away from the records.

---

Generated from 27 manifests, 6 recorded readings and 5 voice runtime rows.
