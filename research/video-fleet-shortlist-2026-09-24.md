# Video (text-to-video / image-to-video) — fleet shortlist

Date: 2026-09-24. Research only: nothing downloaded, installed or generated. Sized against the
owner's fleet (see [owner-hardware.md](owner-hardware.md)): **8 GB ×2, 16 GB ×3, 24 GB ×1,
32 GB ×1 (M2 Max Mac Studio)**. Every memory claim is labelled **measured**, **vendor claim** or
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
| **32 GB (M2 Max)** | as above, plus LTX-2.3 distilled Q4 | as above |

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

### Re-measured 2026-09-26 — the rate is 6.4x better, and this changes the plan

The same kind of byte-range fetch, this time against the video package actually under
consideration:

```
$ curl -sL -r 0-33554431 -o /dev/null \
    https://huggingface.co/AbstractFramework/wan2.2-ti2v-5b-diffusers-8bit/resolve/main/transformer/0.safetensors
http=206  bytes=33554432  seconds=29.650  speed=1131667 B/s
```

**1,131,667 B/s = 1.08 MiB/s sustained**, against 178 KB/s two days earlier — **6.4x.** At the
measured rate a 17 GiB package is a **~4.5-hour** transfer, not a 25-hour one, and both leading
candidates together are roughly **9 hours: one overnight job**, not 2.3 days. The conclusion this
file drew from the old number — *"the measurement cannot be completed in a working session"* — **no
longer holds, provided the download is started first and treated as the long pole.** The per-asset
table above is kept as the 2026-09-24 reading; do not reuse its times.

**Package shape, read from the repository's own file listing (2026-09-26), because it decides the
fleet fit better than any second-hand claim does:**

| Part | Size | Note |
|---|---:|---|
| `text_encoder/` (3 shards) | **10.58 GiB** | umt5-XXL; **not** what `q8` quantizes |
| `transformer/` (3 shards) | ~5.15 GB | the part `q8` actually quantizes |
| `vae/` | 1.31 GiB | |
| total, 22 files | **16.94 GiB** | |

**The text encoder alone is 10.58 GiB — larger than an 8 GB Mac's entire memory, before the
transformer, the VAE, activations or macOS itself.** That is the structural reason 8 GB cannot run
this family, and it is a first-party reading of the package rather than a transferred claim. It
also means **a 16 GB Mac cannot hold this package resident at all**: 16.94 GiB of files does not fit
in 16 GB of memory, whatever the quantization label says, because MLX-Gen's own documentation
states that the q8 packages **dequantize to BF16 at load**. For TI2V-5B the honest tier reach is
therefore **24 GB and 32 GB — 2 of the owner's 7 Macs**, which is a fleet answer only if the other
five are meant to send video work to those two.

**Peak memory is transferable between tiers; speed is not.** So the useful measurement is peak
physical footprint (what decides whether 8/16 GB can run it), not the wall-clock, which on an
M2 Max says nothing about an M1/M2.

## Measurement host

This Mac is a **Mac Studio, `Mac14,13`, Apple M2 Max, 32 GB**.

**The discrepancy this section used to raise is resolved, and the resolution is recorded in
[owner-hardware.md](owner-hardware.md:83-85)** (settled 2026-09-24 from the machine itself): the
32 GB machine **is** the M2 Max Mac Studio, the earlier "M1 Max" statement was wrong, and there is
no separate M2 Max-32 machine to add — **the fleet stands at seven.** Every "M1 Max" in this note
has been corrected to M2 Max. One consequence for reading this file: the speed figures it quotes
from other people were measured on an **M5 Max 128 GB**, which is two generations above the fleet's
ceiling, so they bound nothing here. Speed does not transfer between tiers; **peak memory does**,
which is why the useful fleet measurement is peak physical footprint rather than wall-clock.

**The download cost is the constraint that has not changed, and it is measured (178 KB/s on
2026-09-24).** At that rate the two leading candidates are ~25 h and ~29 h of transfer each, so
"measure both, then decide" cannot finish inside a working session. Any plan that begins with
measurement must schedule the download first and treat it as the long pole.

## `Mrbizarro/ltx-2.3-mlx-q4-1.1` — assessed 2026-09-26 at the owner's request

He raised this one himself: *"can you help this model? because i see it was rated for a 24gb machine
on the page at the bottom there."* **The rating is real and this is the most capable MLX video
package found so far — but it does not widen the fleet, and that is the first thing to say.**

| | |
|---|---|
| Package | `Mrbizarro/ltx-2.3-mlx-q4-1.1` |
| Revision | `02e648e9020b80ec95adafba01c51d85b834a06b` (2026-05-07) |
| Quantization | **int4, group=64, `only_transformer_blocks`** — the connector, VAEs and LoRA stay **bf16** |
| Reported size | 37.96 GiB / 20 files — **but `transformer-distilled.safetensors` is a symlink** to `transformer-distilled-1.1.safetensors`, so the true download is **~27.2 GiB** |
| Runtime | [`dgrauet/ltx-2-mlx`](https://github.com/dgrauet/ltx-2-mlx), Python 3.11, `uv venv` |
| Also required | a **separate** text encoder, `mlx-community/gemma-3-12b-it-4bit` — **not counted in any size above** |

**The Hardware section, verbatim:** *"Apple Silicon (M1+), at least **24 GB unified RAM** for safe
T2V inference"*, *"macOS, MLX 0.31.1+ (LTX needs the audio-fix on this exact pin)"*, *"ffmpeg on PATH
for mp4 mux"*.

**So the floor is 24 GB — which is the same reach as TI2V-5B: the 24 GB M4 and the 32 GB M2 Max, 2
of the owner's 7 Macs.** The three 16 GB machines are below the stated floor. This model is a
*better* answer than TI2V-5B on capability, not a wider one on reach. Anyone reading the 24 GB
rating as "the fleet can now do video" would be reading it wrong.

**What it genuinely buys over TI2V-5B**, and these are real:

- **It produces synchronized audio.** `audio_vae` + `vocoder` + the AAC 48 kHz output stream mean
  video *and* soundtrack in one pass — the MiniMax-H3 class of capability at a runnable size. No
  other in-reach candidate does this.
- **It is MLX-native int4**, not a Torch/MPS port, so it fits the project's existing engine shape
  rather than importing a second deep-learning stack.
- **Published benchmarks with the host named** (M4 Max **64 GB**, MLX 0.31.1, 8 distilled steps):
  512×288×25fr = **22.34 s**; 640×480×121fr = **130.5 s**; 1024×576×121fr (5 s @ 24 fps) =
  **273.2 s**. Speed parity with the v1.0 Q4 within ±2.5% per step.
- Apache-2.0, and layout-compatible with the canonical `dgrauet/ltx-2.3-mlx-q4` byte-for-byte.

**Three honest cautions.**

1. **34 GB-class, not 22 GB.** The repo is ~27.2 GiB and the Gemma-3-12B-4-bit encoder is a
   separate multi-gigabyte download on top. On a 24 GB Mac the two cannot both be resident without
   the runtime's block streaming.
2. **The benchmark host is an M4 Max, and the fleet's 24 GB machine is a base M4.** Same generation,
   very different memory bandwidth. **Do not transfer 273 s to an M4** — that is an inference, and
   the base M4 will be materially slower.
3. **`--low-ram` is the untested lever.** The same runtime's block streaming was measured on the
   sibling LTX-2.3 q8 pack to cut peak from 10–12 GB to **~2.8 GB with bit-identical output**. If
   that holds here, the real floor could be well under the publisher's safe 24 GB — **but nobody has
   measured it for this package, so 16 GB remains unproven and must not be promised.**
4. Adoption is thin: **41 downloads, 1 like**, and the uploader says outright it exists only "while
   the LTX-MLX ecosystem catches up". Prefer the canonical `dgrauet` namespace if one appears.

**Verdict: adopt as the quality/audio route for the 24 GB and 32 GB machines; do not count it as a
16 GB or 8 GB answer.** The `baa-ai/LTX-2.3-22B-RAM-12GB-MLX` mixed-precision pack remains the only
candidate with a sub-24 GB claim, and that one is a vendor claim with no measurement at all.

## In flight — 2026-09-26

**A pinned download of Wan2.2 TI2V-5B q8 is running, to a drive rather than the root volume.**

| | |
|---|---|
| Package | `AbstractFramework/wan2.2-ti2v-5b-diffusers-8bit` |
| Revision (pinned) | `6875952a110b6bdbcfc00d72b1d89a8e02ab0fc3` (2026-06-05) |
| Destination | `/Volumes/KH-STUDIO-NOVA/model-library/video-models/wan2.2-ti2v-5b-diffusers-8bit` |
| Size | 16.94 GiB, 22 files |
| Log | `/tmp/wan-dl2.log` (one `ok`/`FAIL` line per file) |

**Three transfers are queued**, all to the same drive shelf, all revision-pinned. **They are not all
concurrent:** TI2V runs in one process while LTX and Gemma run **sequentially** in a second, so
Gemma does not begin until the LTX package finishes. Two streams are live at any moment.

| Package | Revision | Size | Log |
|---|---|---:|---|
| `Mrbizarro/ltx-2.3-mlx-q4-1.1` | `02e648e9020b80ec95adafba01c51d85b834a06b` | ~27.2 GiB (the symlinked twin is **skipped** and recreated locally with `ln -s`, saving 10.8 GiB) | `/tmp/ltx-dl.log` |
| `mlx-community/gemma-3-12b-it-4bit` | `86cc6a8dedbc456dd0e4af01a9d09f396f77e558` | 7.51 GiB | `/tmp/ltx-dl.log` |
| `wan2.2-ti2v-5b-diffusers-8bit` | `6875952a110b6bdbcfc00d72b1d89a8e02ab0fc3` | 16.94 GiB | `/tmp/wan-dl2.log` |

The Gemma encoder is a **shared dependency of both LTX packages**, so it is worth having whichever
one wins. Measured throughput on the TI2V transfer was **1.92 MB/s** at 304 MB in (≈2.6 h for that
package alone); the rate is per-connection, so the parallel transfers do not simply halve.

**The LTX runtime is pinned and its build is in flight** — `dgrauet/ltx-2-mlx`, HEAD
`3b2e77fd73af5f08961f4436ae979c0579d5deed` (2026-09-25, 119 stars, pushed the day before this note),
cloning to **`.build/ltx-2-mlx`** with a `uv venv env --python 3.11` and `uv pip install -e .`,
logging to `/tmp/ltx-runtime.log`. The upstream repo is only **21.3 MB compressed**, so the clone is
small; it is slow only because it shares bandwidth with the two weight transfers. **A silent log is
expected, not a hang** — the script's first `echo` comes after checkout, so there is no output until
the clone completes. Do not read the empty log as a stall; check `pgrep -fl "git|uv"` instead.
The conversion tool is `dgrauet/mlx-forge`
(`eaf2f98c36544dcd4fa60b05f05a6536edf5c96c`) and is **not** needed for inference — only
`ltx-2-mlx` is. The package's own Quickstart asks for Python
3.11 via `uv venv`, which is a **third interpreter version** for this project (the Image runtime is
3.12, and the repo's own tests run under the 3.12 Voice runtime). That is a runtime-adoption
decision, not a detail.

**The owner decided the reach question on 2026-09-26: chase five machines.** Asked directly which
machines the Video studio must reach, he chose *"Chase five machines - get the baa-ai 12GB pack and
test the 16 GB claim"*. So the 16 GB tier is in scope and this package is now the gating artefact,
not an optional extra. Everything below about the 24 GB floor applies to TI2V-5B and the Mrbizarro
q4 pack; **this one is the only candidate that can move the floor down, and its claim is the one
that must be measured before anything is promised.**

**`baa-ai/LTX-2.3-22B-RAM-12GB-MLX` — in flight, and materially different from the Mrbizarro pack:**

| | |
|---|---|
| Revision | `d8a9d2f6081c4136130983a0b612962418ddf203` (2026-05-19) |
| Size | **17.81 GiB / 13 files** — ~9.4 GiB smaller than the Mrbizarro pack |
| Transformer | `transformer-distilled.safetensors` **9.5 GB**, versus Mrbizarro's 10.8 GB — a more aggressive mixed-precision per-tensor quantization (2–8 bit, 1,415 of 1,760 layers) |
| LoRA | **none shipped** — Mrbizarro's carries a 7.1 GiB bf16 LoRA the others do not |
| Audio | yes: `vocoder.safetensors` + `audio_vae.safetensors` are both present |
| Extras | ships its own `generate.py` and `webapp.py` |
| Destination | `/Volumes/KH-STUDIO-NOVA/model-library/video-models/ltx-2.3-22b-ram-12gb-mlx` |
| Log | `/tmp/baa-dl.log` |

**Caution to carry forward: `downloads: 0`.** Nobody on the Hub has pulled this package. It is a
third-party quantization with no independent run behind it, so its ~14 GB memory claim is
**unverified by anyone but its author** — which is precisely why the owner's instruction was to
*test* it rather than adopt it. It needs its own pinned revision and per-file checksums like any
other third-party conversion, and it is the package most likely to disappoint.

**Not started, and deliberately not:** *(superseded — this package is the one now in flight.)*


**Why the destination is a drive:** the root volume has **13 GiB available** and the package is
**16.94 GiB**, so `~/StudioHubModels` — where every other model in this project lives — **cannot
hold it.** All four drives carry a reserved `model-library/video-models/` shelf, and all four were
empty before this. Any video model will have to live on the drives, which is a kit-layout decision
and not just a download detail: it means the video lane's model library is not in the same place as
every other lane's.

**Two failed attempts worth not repeating.** `huggingface-cli download` (the old name) now only
prints help and downloads nothing — **and it exits 0, so a careless `echo $?` after a pipeline
reports success for a download that never started.** Then `hf download` (1.31.0) acquired locks on
every large shard, sat in state `S` for two minutes and moved **180 KB**. It was replaced with a
per-file `curl -C -` loop against the same pinned revision, which reached ~1.6 MB/s immediately.
At that rate the transfer is roughly **3 hours**, so this is the round's long pole; nothing that
needs weights on disk can start until it finishes.


## Suggested next steps (not started)

1. Pick one machine to qualify on — 16 GB M4 is the practical tier — and measure **one** Lance 3B
   t2v clip (256²×16f) and **one** LTX-2.3 12 GB i2v clip, recording peak memory, swap and time.
   That converts two vendor/upstream claims into fleet facts.
2. Decide the engine shape before writing code: Lance needs `lance-mlx`; LTX-2.3 needs
   `ltx-2-mlx`/`ltx-pipelines-mlx`. These are two separate runtimes, not one, so this is a
   Video-Studio-style engine task like ST-12, not a catalog row.
3. Reserve the archive's `video-models/` shelf and size the kit budget *before* importing: Lance
   is ~15.6 GB, LTX-2.3 12 GB is ~18 GB, and shipping both is ~34 GB per drive.

## Decision brief — 2026-09-26, written because the owner stopped the downloads

**His challenge, verbatim: *"wait, why are we downloading the models right now? you didn't tell me
more about each models first or discuss thoroughly."* He is right.** Four packages were started
(~70 GB planned) before he had ever seen them side by side. Each download did trace to something he
said — TI2V-5B to his scope answer, the Mrbizarro pack to a link he sent, the baa-ai pack to his
reach answer, Gemma to both LTX packs — **but "transfer is the long pole" only justifies starting
*after* a choice, and for three of the four there was no choice yet.** All transfers are paused at
**2.3 GB on disk**, and every one resumes with `curl -C -` without re-downloading a byte.

### The four candidates on the axes that actually decide

| | **Wan2.2 TI2V-5B q8** | **LTX-2.3 q4** (Mrbizarro) | **LTX-2.3 RAM-12GB** (baa-ai) | **Lance 3B** |
|---|---|---|---|---|
| Revision | `6875952a110b…` | `02e648e9020b…` | `d8a9d2f6081c…` | *(not fetched)* |
| Package | 16.94 GiB | ~27.2 GiB + **7.5 GiB Gemma** | 17.81 GiB + **7.5 GiB Gemma** | ~15.6 GB |
| Quantization | **q8** (encoder stays bf16 — see below) | **int4** g64, transformer blocks only | **mixed 2/3/4/5/6/8-bit**, avg 4.6 | **bf16** |
| Memory claim | **none stated** | *"at least 24 GB"* [V] | ***"~14 GB"*** [V] | fits **8 GB** via `relay` [M] |
| T2V | **yes** | yes | yes | yes |
| **I2V** | **yes** (first-frame, same package) | *undocumented* | *undocumented* | **no** |
| Audio | no | **yes**, AAC 48 kHz | packages present | — |
| Runtime | `mlx-gen` (PyPI) | `ltx-2-mlx` | `ltx-2-mlx` | `lance-mlx` |
| Install risk | low | **high — see below** | **PyPI: `pip install mlx flask ltx-pipelines-mlx ltx-core-mlx`** | medium |
| Independent evidence | vendor bench, M5 Max 128 GB | vendor bench, M4 Max 64 GB | **none — 0 downloads** | **measured upstream** |
| Reach | 24 / 32 GB | 24 / 32 GB | **16 GB claimed** | **8 GB measured** |

### The five things that decide it

1. **Image-to-video: corrected, and the first version of this brief was wrong.** It claimed the LTX
   packages could not do the operation Story Studio calls. **They can.** The pinned runtime's own
   exports settle it: `TI2VidOneStagePipeline`, `TI2VidTwoStagesPipeline`, `TI2VidTwoStagesHQPipeline`,
   `KeyframeInterpolationPipeline`, `ICLoraPipeline`, `RetakePipeline`, `LipDubPipeline`,
   `A2VidPipelineTwoStage`. **`TI2Vid` is text+image-to-video**, and `KeyframeInterpolation` is
   first/last-frame conditioning. What is true is narrower and still matters: **the baa-ai pack's
   bundled `generate.py` exposes only a prompt** — its full argument list is `--model-dir`,
   `--prompt`, `--output`, `--height`, `--width`, `--num-frames`, `--frame-rate`, `--seed`,
   `--stage1-steps`, `--stage2-steps`, `--gemma-model`, `--low-memory`/`--no-low-memory`, `--quiet`,
   with **no `--image` and no reference input anywhere**. That is a limitation of *the script they
   shipped*, not necessarily of the weights, and the same components under a `TI2Vid*` pipeline may
   well do I2V. Mrbizarro's card, by contrast, names `ti2vid_one_stage.py` in its loader note, so
   that pack is aimed at the TI2Vid path. **TI2V-5B remains the only candidate whose I2V is
   documented by its own publisher, but "only TI2V-5B can do I2V" was an overclaim and is retracted.**
   Withdrawn: *"Only TI2V-5B documents the operation Story Studio actually calls."*

2. **TI2V-5B's "q8" is a disk saving, not a memory saving.** MLX-Gen's own documentation states the
   q8 packages **dequantize every transformer-block linear to BF16 at load**, so runtime memory
   equals the BF16 package. And the text encoder is **10.58 GiB of the 16.94 GiB** — larger than an
   8 GB Mac's entire memory on its own. A 16 GB Mac cannot hold 16.94 GiB resident, whatever the
   label says. **Reach: 24 GB and 32 GB.**

3. **The baa-ai pack's 14 GB is bought with 254 layers at 2–3 bits.** Its own bit distribution is
   2-bit ×29, 3-bit ×225, 4-bit ×348, 5-bit ×631, 6-bit ×125, 8-bit ×57 — so **14% of its quantized
   layers sit at 2–3 bits**. That is where the reach comes from and it is the most likely place for
   quality to fall apart. It also has **zero downloads**, a `license: other` with a *"proprietary
   Black Sheep AI method"*, and its README is substantially an advertisement for the vendor's
   products. It is the candidate most likely to disappoint and the only one that can reach 16 GB.

4. **The LTX runtime is currently broken, and that is a real cost.** `ltx-2-mlx` will not install
   with current setuptools — a rejected `License :: OSI Approved` classifier (PEP 639) — and then
   fails on a chain of **undeclared build dependencies** (`hatchling`, `editables`) and undeclared
   runtime ones (`mlx_arsenal`, `huggingface_hub`). Only after patching our staged copy's
   `pyproject.toml` did the resolver install the real dependency set. **It still fails:
   `cannot import name 'TextToVideoPipeline'`.** By contrast **baa-ai's README advertises a plain
   PyPI install that sidesteps the git route entirely** — which may be the cheaper path for *both*
   LTX packages, and is worth testing before adopting anything.

5. **Lance 3B is the only measured small-memory answer, and it cannot do I2V.** ~15.6 GB, and
   `memory_mode=relay` is measured upstream to fit 8–16 GB with **byte-identical** output to the
   resident mode (frame-MD5 verified); lossless streaming VAE decode is flat in frame count
   (256²×49f = 7.99 GB, 121f = 8.05 GB). But it is **T2V only**, painterly rather than
   photorealistic, and self-described as alpha.

### Recommendation

**Ship Wan2.2 TI2V-5B q8 as the video lane's first model**, and treat the 16 GB chase as a separate,
later question. It is the **only candidate whose publisher documents I2V for the exact package**, the
only one whose runtime installs cleanly from PyPI with no patching, and the only one with no quality
gamble in its quantization. Its 24/32 GB reach is the tier where video is unambiguous anyway.
**It does not answer the owner's five-machine instruction — nothing does except the baa-ai pack — so
that instruction and this recommendation are in direct tension, and he should be told so rather than
have it smoothed over.**

**Two corrections to the reasoning that led here, both found by checking rather than assuming:**

- *"The LTX runtime is currently broken"* — **wrong.** The install does need patching (the
  `License ::` classifier and the undeclared build deps are real and are documented above), but the
  runtime itself works: `DistilledPipeline` imports cleanly, and the failure I reported was **my own
  wrong class name** (`TextToVideoPipeline`, which this version does not export). `ltx-2-mlx` v0.15.9
  exports twelve pipelines and none is called that.
- *"The baa-ai pack is text-to-video only"* — **true of its script, not proven of its weights.** See
  point 1 above.

**Before any further transfer, three questions belong to the owner:**

1. **Is image-to-video a hard requirement?** If yes, Lance 3B is out on capability and TI2V-5B is the
   default. If no, Lance's measured 8 GB path becomes interesting.
2. **Is a 2–3 bit mixed quant acceptable** to reach 14 GB / the 16 GB machines? That is the whole
   basis of the baa-ai reach, and it is unmeasured by anyone.
3. **Does 8 GB matter for video at all**, given nothing measured does I2V there and the 8 GB Macs are
   already carrying image, voice and subtitle work?

**Cheap next steps that need no download:** read `generate.py` in the baa-ai pack already on disk to
settle whether it does I2V; test the PyPI install route for `ltx-pipelines-mlx`; and fix the
`TextToVideoPipeline` import against the pinned runtime.

## Resolved by inspecting the pinned runtime — 2026-09-26

Everything in this section was settled **without downloading a byte**, by asking the installed
`ltx-2-mlx` v0.15.9 what its pipelines actually accept. It supersedes the I2V reasoning in the
decision brief above, which was wrong twice before it was right.

**The operation a pipeline can do is a property of the pipeline, not of the vendor's script.**
`inspect.signature` on `generate_and_save`, listing only image-ish parameters:

| Pipeline | Image-ish parameters |
|---|---|
| `DistilledPipeline` | **`image`, `images`**, `generated_keyframes`, `video_guider_params` |
| `TI2VidOneStagePipeline` | **`image`, `images`**, `generated_keyframes`, `video_guider_params` |
| `KeyframeInterpolationPipeline` | **`keyframe_images`, `keyframe_indices`, `keyframe_strengths`** |
| `ICLoraPipeline` | **`video_conditioning`**, `images`, `refine_steps` |

**So the baa-ai pack can do image-to-video.** It ships the same `DistilledPipeline` that takes
`image=`, and its weights are a quantized LTX-2.3 with the same component split. What it does *not*
ship is a script that exposes it: **`generate.py`'s full argument list has no `--image`, and
`webapp.py` contains no image, upload, i2v, first_frame or keyframe reference at all** — verified by
grep, not assumed. Calling `DistilledPipeline` directly is what unlocks I2V on that pack.

**And the runtime does more than the Hub currently has routes for.** `KeyframeInterpolationPipeline`
is first/last-frame conditioning, and `ICLoraPipeline`'s `video_conditioning` is the restyle path —
which is the operation `broker._video_multipart_fields` already dispatches to
`/api/generate/video2video` and which no worker serves.

**This materially strengthens the LTX route and weakens the recommendation above.** TI2V-5B's
advantage was that its publisher documents I2V for the exact package; that advantage is now much
smaller, because **I2V is available on every LTX pack through the pipeline call**. The honest
comparison is now:

| | TI2V-5B q8 | LTX-2.3 q4 (Mrbizarro) | LTX-2.3 RAM-12GB (baa-ai) |
|---|---|---|---|
| T2V / I2V | yes / yes (documented) | yes / **yes, via pipeline** | yes / **yes, via pipeline** |
| Audio | **no** | yes | yes |
| Reach | 24 / 32 GB | 24 / 32 GB | **16 GB claimed** |
| Quality risk | **none in quant** | low | **254 layers at 2–3 bit, 0 runs behind it** |

**`low_ram_streaming` exists and defaults to `False`.** Both `DistilledPipeline` and
`TI2VidOneStagePipeline` take `(model_dir, gemma_model_id, low_memory=True, low_ram_streaming=False,
tile_count=None)`. **So there is a memory lever above `low_memory` that the baa-ai pack never
enables** — its `--low-memory` maps only to `low_memory`, and its CLI has no streaming switch at all.
The sibling LTX-2.3 q8 pack measured block streaming cutting peak to **~2.8 GB with bit-identical
output**; if that carries here it is the most promising route to a genuine 16 GB video lane, and it
is a **one-argument test on weights we would be downloading anyway.** That is now the single most
valuable experiment available, and it needs no new decision — only the download the owner already
authorised for the 16 GB question.

**One caution found in the same inspection:** `TI2VidOneStagePipeline` defaults to
`dev_transformer='transformer-dev.safetensors'`. **Neither quantized pack ships a dev transformer** —
both ship `transformer-distilled.safetensors`. Mrbizarro's loader note says `ti2vid_one_stage.py`
falls back to the distilled name, so the path should work, but the default expecting a file that is
not in either package is exactly the kind of dead pointer that has bitten this project before.

## The LTX runtime: the install recipe that actually works — 2026-09-26

`ltx-2-mlx` does **not** install from a clean clone. Six attempts were needed; this is the sequence
that succeeds, recorded because it is not guessable and cost real time.

```bash
git clone https://github.com/dgrauet/ltx-2-mlx .build/ltx-2-mlx
cd .build/ltx-2-mlx
git checkout 3b2e77fd73af5f08961f4436ae979c0579d5deed
uv venv env --python 3.11
# 1. its pyproject uses a PEP 639-rejected classifier; patch the STAGED copy only, keep .orig
grep -rl "License :: OSI Approved" --include=pyproject.toml . \
  | xargs -I{} sh -c 'cp {} {}.orig && sed -i "" "/License :: OSI Approved/d" {}'
# 2. its sibling packages have UNDECLARED build deps; the resolver needs them present
uv pip install --python env/bin/python hatchling editables hatch-vcs
# 3. then pin setuptools back down, or step 2 re-upgrades it and step 1 fails again
uv pip install --python env/bin/python "setuptools<80" wheel
uv pip install --python env/bin/python -e . --no-build-isolation
```

**Why each step is there.** The root `pyproject.toml` carries `License :: OSI Approved :: MIT
License`, which current setuptools rejects outright (`InvalidConfigError`, PEP 639), so
`uv pip install -e .` dies at the **metadata** stage — which means **none of the package's declared
runtime dependencies are ever installed**, and each subsequent import reveals the next missing one
(`mlx_arsenal`, then `huggingface_hub`). Installing build deps first and *then* re-pinning setuptools
is the ordering that satisfies both constraints at once; doing it the other way round silently
undoes step 1.

**Result — verified, not assumed:**

```
DistilledPipeline import OK
ltx_pipelines_mlx v0.15.9 exports 12 pipelines:
  A2VidPipelineTwoStage, BasePipeline, DFRPipeline, DistilledPipeline, HDRICLoraPipeline,
  ICLoraPipeline, KeyframeInterpolationPipeline, LipDubPipeline, RetakePipeline,
  TI2VidOneStagePipeline, TI2VidTwoStagesHQPipeline, TI2VidTwoStagesPipeline
mlx 0.32.2
```

**Two traps in this runtime.** There is **no `TextToVideoPipeline`** in v0.15.9 — asking for it
raises `ImportError`, and that import error was twice misread here as a broken install. And the
runtime needs a **Python 3.11** interpreter, a third version for this project alongside the Image
runtime's 3.12. `.build/ltx-2-mlx/env/` is the venv; the `.orig` copies of every patched
`pyproject.toml` sit beside the originals, so the patch is reversible and reviewable.

## Standing video decisions — owner, 2026-09-26

Three decisions taken after the canvas analysis. They are product decisions, not preferences, and
they supersede the reasoning above them.

1. **One canvas for every machine: 1024×576.** Verbatim: *"i hate 2 or 3 types so all of the
   machines get's the same ratio offers."* There is **no per-tier ratio ladder** — an 8 GB Mac and
   the 32 GB Mac are offered the same shape. 1024×576 is exactly 16:9, is valid on both the 32 and
   the 64 grid, and is the cheapest of the candidates in the dominant memory term (attention cost
   is quadratic in token count, so it is **2.33× cheaper than 1280×704** at equal duration).
2. **Longer clips preferred** over higher resolution. *"i prefer longer clips."* So the trade in the
   equivalence table above is settled toward duration: 1024×576 at 97 frames (4.0 s at 24 fps), not
   1280×704 at 65 frames. Note the cost is quadratic in frames too, so this is a real budget line:
   1024×576 is ~4.0 GB/layer at 97 frames and ~6.2 GB/layer at 121 (5.0 s).
3. **Drift is accepted and wanted; art-style retention is the requirement.** *"image-to-video drifts
   off source image. i don't care. as long as it retains same artstyle. that's all i needed. it's
   suppose to be different. if no drift in scene. it just a still image. what's the point of this
   image to video then?"* **He is right and the recommendation above was wrong.** The runtime's
   identity-preservation warning and its IC-LoRA canny-control recipe solve a problem this product
   does not have: identity retention matters for a character or a product, not for a background
   plate whose purpose is to move. **So the plain `DistilledPipeline` scene-first path is correct
   as-is, the IC-LoRA control pass is NOT built, and `RUN-16GB-LTX-VIDEO.command` needs no change.**
   Withdrawn: *"switch the runner from plain DistilledPipeline to the IC-LoRA canny-control path."*

## Node death and failover — what the Hub actually does, checked 2026-09-26

The owner asked the right question: *"say the machine is hung or crashed. will we need to wait 2 hours
to find out or it's found out and the studio redirects from that failed node to another? or reports
back to gateway which report failures or just redirect to another location?"* Answers, from the code:

**Detection is 5 seconds, not 2 hours.** `components/hub/backend/monitor.py:25` is
`POLL_INTERVAL_S = 5.0`, and every studio's `/api/health` is polled on that interval. A hung or
crashed node is noticed in seconds. **The proxy deadline never governs this**, which is the same point
as the stalled-job case: a deadline is a ceiling on honest work, not a failure detector.

**The Hub redirects; the gateway does not.** Machine choice lives in the Hub (`_placement_key`),
so re-dispatch is the Hub's. GatewayKH dispatches a job *to a Hub* and reads the lane contract; it
does not select machines and has nothing to redirect. It sees the outcome through job state.

**What happens to the in-flight job depends entirely on its retry class**
(`components/hub/backend/broker.py:152-168`), and the three classes are about **duplicate
prevention**, not about failover speed:

| Class | Lanes | On a dropped attempt |
|---|---|---|
| `DURABLE_ASYNC` | **image, music** | The exact attempt is **retained**, never silently replayed — a replay could produce a **duplicate artifact**. `requeue_if_peer_changed()` (`:3362`) can move it when its peer disappears. |
| `RECEIPT_DURABLE` | **voice** | Retried safely: the worker recovers a dropped attempt by a stable client-request id. |
| `NON_DURABLE` | **video, render** | No asynchronous attempt is retained at all. |

**The defect this exposes for the video lane.** `video` is `NON_DURABLE` because of this comment:
*"Video and Render answer a terminal result for the POST itself, so there is no separate asynchronous
attempt to retain."* **That describes the legacy inherited Video Studio — a synchronous POST that
returns the finished clip. It does not describe the lane being built here.** An LTX-2.3 generation is
a 15-to-45-minute job; it cannot answer a terminal result for the POST under any sane timeout, so it
is an asynchronous job by nature.

**Consequence if left alone: a node dying at minute 40 of a 45-minute video loses the job** — no
`uncertain` state, no retained attempt, no re-dispatch — while the same failure in the Image lane is
retained and can be requeued. The `_mark_*_uncertain` family (`_mark_async_uncertain`,
`_mark_image_uncertain`, `_mark_voice_uncertain`) is gated on the durable classes, so video gets none
of it.

**So the video lane must be classified deliberately, not inherated.** The class table's own comment
demands it: *"a new modality must be classified deliberately: an unclassified modality used to fall
through to the non-durable default, where a POST that may have reached the worker is replayed and can
produce a duplicate artifact."* The open question for the video lane is the same one voice answered:
**does the worker accept a stable client-request id so a dropped attempt can be recovered, or must the
attempt be retained instead?** A 45-minute job that may have already written a partial MP4 needs one
of those two answers before it is dispatchable.

## Stall detector — design, not built (2026-09-26)

The owner asked for this twice and it is the piece that makes a long deadline **safe** rather than
merely long. Not built; this is the design so the next block starts from something concrete.

**The signal already exists.** `components/hub/backend/monitor.py:121` carries
`activity_received_at` per studio — the moment the Hub last received that studio's activity
snapshot. `backend/activity.py` validates those snapshots (`SCHEMA = "kh-studio.activity.v1"`) and
already does staleness arithmetic of its own against `REPORTER_CLOCK_SKEW_S` (`:243`), so the
plumbing and the pattern are both in place.

**The condition:** a job whose state is `running` (or `cancel_requested`) on a studio whose
`activity_received_at` has not advanced for longer than a threshold. That is a worker that has
stopped reporting while still holding a job — a real hang, distinguishable from a slow job.

**Why this is the right tool and a deadline is not.** Shortening a deadline cannot detect a hang; it
only kills jobs that might have been working, which *manufactures* failures. A stall check notices
silence in minutes no matter how long the ceiling is, so the two settings stop fighting: the
deadline can be generous for honest work while the detector stays impatient about dishonest silence.
That is exactly the distinction the owner drew when he asked whether a failed subtitle job would
take two hours to surface.

**What it must NOT do:** silently kill the job. A stall should become *visible* — a state the
operator and the scheduler can see — because killing a job that was merely quiet loses work that a
long deadline exists to protect. The terminal vocabulary already has `uncertain`, marked **not
terminal** in every lane contract, which is the natural landing place.

**Threshold:** must exceed the workers' own report interval with room to spare, or a healthy worker
will be flagged between two snapshots. That interval is the number to read first, from the workers,
not to guess.

**One thing to check while building it:** whether a stalled job should also stop consuming its
per-machine fairness turn. A hung job holding a turn is a second, quieter failure.
