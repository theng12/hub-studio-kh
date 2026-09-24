# Video (text-to-video / image-to-video) — fleet shortlist

Date: 2026-09-24. Research only: nothing downloaded, installed or generated. Sized against the
owner's fleet (see [owner-hardware.md](owner-hardware.md)): **8 GB ×2, 16 GB ×3, 24 GB ×1,
32 GB ×1 (M1 Max)**. Every memory claim is labelled **measured**, **vendor claim** or
**inference**, and no speed measured on a larger machine is transferred to the fleet.

Companion notes: [ltx-2-5-apple-silicon.md](ltx-2-5-apple-silicon.md) (LTX-2.5 is out of reach —
64 GB+ class), [yume-i2v-24gb.md](yume-i2v-24gb.md).

## What already exists in this project

- The read-only Video Studio checkout (`videostudio-mac` 0.11.1) is **not on this Mac** — the only
  reference is `research/function-scope-inventory.md`. It wires **Lance 3B via native MLX** for
  text-to-video, and LTX / Wan 2.2 / HunyuanVideo / CogVideoX via **Torch + MPS + Diffusers**.
- The app catalog has **no video engine** (`model-manifests/` engines are image-mflux, music-mlx,
  subtitle-mlx, voice-mlx, voice-vits).
- The drive archive has a **reserved, empty `video-models/` shelf**, and the kit libraries have
  ~58.6 GB of packages after the TTS prune — the space this work was meant to free.

## The shortlist

| Model | Task | Weights | Fits 8 GB | 16 GB | 24 GB | 32 GB | Runtime |
|---|---|---|---|---|---|---|---|
| **Lance 3B Video bf16** | T2V only | ~15.6 GB | **yes** (`relay`) | yes | yes | yes | `lance-mlx` |
| **LTX-2.3 22B 12GB (baa-ai)** | T2V + I2V | ~18 GB | no | **yes** (vendor) | yes | yes | `ltx-2-mlx` |
| **LTX-2.3 22B 24GB (baa-ai)** | T2V + I2V | larger | no | no | yes | yes | `ltx-2-mlx` |
| **LTX-2.3 distilled Q4** | T2V + I2V | ~22 GB | no | no | tight | yes | `mlx-video-with-audio` |
| **Wan2.2 TI2V-5B** | T2V + I2V | 16.9–21.2 GB | no | no | likely (inference) | likely | `mlx-gen` |
| **Bernini-R 1.3B** | reference→video / edit | ~15.6 GiB | no | likely (inference) | yes | yes | `mlx-gen` |
| **Yume 1.5 5B 4-bit** | I2V | 17.1 GB | no | no | unqualified | unqualified | `mlx-video` |
| LTX-2.5 | T2V + I2V | ~66–110 GB | no | no | no | no | — |
| Wan2.2 A14B (T2V/I2V) | T2V + I2V | 39.5–64 GiB | no | no | no | no | `mlx-gen` |
| MiniMax H3 | T2V + I2V + audio | 92–144 GB | no | no | no | no | `h3.c` |

### 1. Lance 3B Video — the only credible 8 GB option, but **T2V only**

`mlx-community/Lance-3B-Video-bf16` (Apache-2.0 port; weights `model` 12.87 GB + `vit` 1.34 GB +
`vae` 1.41 GB). ByteDance's 3B-active video specialist, run through the `lance-mlx` source repo.

- **`memory_mode=relay` (auto below ~18 GiB) fits 8–16 GB Macs** and is **byte-identical** to the
  resident `parallel` mode (frame-MD5 verified upstream). It sheds the understanding tower after
  prefill and frees the generation tower before VAE decode, so peak ≈ the heaviest single phase
  rather than the sum. *(Measured upstream on real weights.)*
- Lossless streaming VAE decode is **flat in frame count**: 256²×49f **7.99 GB**, 256²×121f
  **8.05 GB**, 512²×61f **12.64 GB** — all fit 16 GB. *(Measured upstream.)*
- **768² video is the exception on 16 GB**: lossless needs >~21 GB, so 16 GB must use the lossy
  decode path. *(Measured lower bound / prediction.)*
- Quality envelope: **production-quality t2v through 768²×25 frames**; at n_lat ≥ ~30k
  (768²×49f and the 480×848×121f reference) output degrades to colored mesh artifacts. *(Measured
  upstream, open issue #1.)*
- Speed, **M5 Max 128 GB**: 256²×16f ~33 s, 512²×16f ~60 s, 768²×13f ~145 s. *(Measured, but on a
  machine far above this fleet — do not transfer these numbers to an M1/M2.)*
- Cautions: **alpha**; **painterly, not photorealistic**; **no image-to-video**; English/Chinese
  prompts only; no streaming or batching; no INT4 video variant exists.

### 2. LTX-2.3 12 GB (baa-ai) — the T2V **and I2V** option for 16 GB

`baa-ai/LTX-2.3-22B-RAM-12GB-MLX`, a mixed-precision per-tensor quantization of Lightricks'
LTX-2.3 (transformer 9.3 GB, **~18 GB total**, 1,415 of 1,760 layers quantized, 2–8 bit), run via
`ltx-2-mlx` / `ltx-pipelines-mlx`.

- **Vendor claim: ~14 GB unified memory, M1 or later, macOS 13+.** That is the claim — no
  independent measurement, and no fleet-speed figure. Treat 16 GB as tight but plausible, and
  24 GB as comfortable.
- Frame count must be `32k+1` (33, 65, 97, 129…); the card's example is 480×704, 65 frames.
- A sibling **24 GB** variant exists for the same family. Because these are **third-party
  quantizations**, each needs its own pinned revision and checksums before import.
- LTX-2.3 supports **image-to-video** (first/last frame and multi-keyframe on the MLX paths), so
  this family is the practical answer for I2V on the 16 GB and 24 GB machines.

### 3. Wan2.2 TI2V-5B — T2V + I2V, the middle tier

`AbstractFramework/wan2.2-ti2v-5b-diffusers-bf16` (21.2 GiB) or the q8 package (16.9 GiB) through
`mlx-gen`. Important published caveat: since the 2026-06-12 runtime-precision fix, **Wan q8
packages dequantize to BF16 at load, so q8 saves disk only — runtime memory equals BF16.** No q4
or mixed q4/q8 TI2V-5B is published. *(Vendor claim; fleet fit is inference.)*

Wan2.2 **A14B** is explicitly out: its own measurements are 33 GB physical peak, and the vendor
recommends a 128 GB Mac.

### 4. Also noted, not leading

- **Bernini-R 1.3B** (`mlx-gen`, ~15.6 GiB BF16): ordered reference images → video and
  prompt-guided video editing. Experimental; seven example rows accepted. Plausible I2V-adjacent
  value on 24 GB+.
- **Yume 1.5 5B MLX 4-bit**: already researched at 24 GB and judged an unqualified candidate,
  because its text-encoder loader is float32-heavy.
- **MiniMax H3** (synchronized audio + video) needs 92–144 GB — out of fleet, but worth remembering
  as the eventual audio-video target.

## What the fleet can actually do

| Tier | Text-to-video | Image-to-video |
|---|---|---|
| **8 GB (M1, M2)** | Lance 3B, `relay`, ≤256²–512² clips | **nothing credible** |
| **16 GB (M1, M2, M4)** | Lance 3B (≤768² with lossy decode); LTX-2.3 12 GB (vendor-claimed) | **LTX-2.3 12 GB only** |
| **24 GB (M4)** | Lance; LTX-2.3 12/24 GB; Wan TI2V-5B (inference) | LTX-2.3; Wan TI2V-5B; Bernini-R |
| **32 GB (M1 Max)** | as above, plus LTX-2.3 distilled Q4 | as above |

**The honest headline: image-to-video has no 8 GB path, and its 16 GB path rests on a vendor
memory claim.** If I2V must run on every node, the smallest usable tier is 16 GB via LTX-2.3 12 GB,
and that should be measured before it is promised.

## Download reality — measured on this Mac, 2026-09-24

A 33.5 MB byte-range fetch from the Lance repo measured **178 KB/s sustained**
(`http=206`, 33,554,432 B in 183.7 s). At that rate:

| Asset | Size | Time |
|---|---:|---:|
| Lance 3B Video bf16 | ~15.6 GB | **~25 hours** |
| LTX-2.3 22B 12GB (baa-ai) | ~18 GB | **~29 hours** |
| Both, for a comparison | ~34 GB | **~2.3 days** |

That is a real constraint on "measure both, then decide": the measurement cannot be completed in
a working session, and if either model is to ship in the kit the multi-gigabyte transfer has to
happen regardless. It is also consistent with the ~50–100 KB/s figure the Chat session recorded.

**Peak memory is transferable between tiers; speed is not.** So the useful measurement is peak
physical footprint (what decides whether 8/16 GB can run it), not the wall-clock, which on an
M2 Max says nothing about an M1/M2.

## Measurement host

This Mac is a **Mac Studio, `Mac14,13`, Apple M2 Max, 32 GB**. Note this **contradicts**
[owner-hardware.md](owner-hardware.md), which states the 32 GB machine is an **M1 Max** and says
that an M2 Max-32 would be a *ninth* machine to be added rather than inferred. That discrepancy is
the owner's to settle; until it is, treat the 32 GB row as "M2 Max (this session)" and do not
assume it is an M1 Max.

## Suggested next steps (not started)

1. Pick one machine to qualify on — 16 GB M4 is the practical tier — and measure **one** Lance 3B
   t2v clip (256²×16f) and **one** LTX-2.3 12 GB i2v clip, recording peak memory, swap and time.
   That converts two vendor/upstream claims into fleet facts.
2. Decide the engine shape before writing code: Lance needs `lance-mlx`; LTX-2.3 needs
   `ltx-2-mlx`/`ltx-pipelines-mlx`. These are two separate runtimes, not one, so this is a
   Video-Studio-style engine task like ST-12, not a catalog row.
3. Reserve the archive's `video-models/` shelf and size the kit budget *before* importing: Lance
   is ~15.6 GB, LTX-2.3 12 GB is ~18 GB, and shipping both is ~34 GB per drive.
