# LTX-2.5 (Lightricks) — Apple Silicon viability research

Date: 2026-09-24. Research only: no model download, licence acceptance, conversion,
install or generation was performed. No runtime, packaging or fleet change is intended
or authorized by this note.

## Verdict

LTX-2.5 is a real open-weights video+audio foundation model, but it is **not currently a
qualified Apple Silicon candidate for this fleet**, and it is out of scope while Video
remains deferred. The vendor supports NVIDIA/CUDA only; every Mac path is third-party and
unqualified by us. On the evidence below, 2.5 needs a **64 GB+** Apple Silicon machine to
be plausible at all, because the bundled Gemma 4 12B text encoder alone measures ~22.7 GB
in bf16 and quantization makes it *larger*, not smaller (~29.1 GB). A 32 GB node should be
treated as not viable for 2.5; LTX-2.3 is the lighter sibling (~30 GB weights, 32 GB+
recommended) if a predecessor is ever revisited. Treat this as a compatibility target, not
a passing qualification.

## What the model is

- Released ~2026-08-11. HF repo `Lightricks/LTX-2.5`, code `github.com/Lightricks/LTX-2`,
  paper `arXiv:2601.03233`.
- A single ~22B DiT that generates **synchronized video and audio**; text / image / video /
  audio in. Capabilities: T2V, I2V, V2V (retake), audio-to-video, text-to-audio, keyframe
  interpolation, IC-LoRA editing (relight, in/outpainting, Union/Motion control), Dub-It
  lip-sync and Foley (the last two are documented as LTX-2.3 specialised workflows).
- New vs 2.3: native multi-shot in one pass, Diffusion Fidelity Rendering (DFR), a new
  timestep-conditioned diffusion video decoder (DiffVAE), Gemma 4 12B text encoder plus a
  separate optional prompt enhancer, auto-duration head, native EXR/HDR.
- Two transformers: `dev` (fine-tune/quality base) and `distilled` (fixed 8-step schedule,
  no CFG). Distilled is what most local users run and what the Mac ports target.

## Exact assets and disk cost

The HF page is **gated**: an unauthenticated fetch of the model card returns HTTP 401, and
access requires agreeing to share contact information plus marketing consent. Asset sizes
below are therefore taken from the official `Lightricks/LTX-2` README and the vendor docs,
not by downloading.

Minimal distilled set (official Quick Start) is **~66 GiB**, component-split:

| Component | File | Approx. size |
|---|---|---:|
| Transformer (distilled) | `diffusion_models/ltx-2.5-22b-distilled-transformer-bf16.safetensors` | ~42 GB (22B bf16) |
| Text encoder (bundled) | `text_encoders/gemma4-12b-with-proj-ltx-2.5-bf16.safetensors` | ~26.3 GB |
| Video VAE | `vae/ltx-2.5-video-vae-bf16.safetensors` (or `-conv-`) | ~1.5 GB |
| Audio VAE | `vae/ltx-2.5-audio-vae-bf16.safetensors` | ~0.4 GB |
| Spatial upscaler | `latent_upscale_models/ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors` | — |
| Temporal upscaler (DFR) | `latent_upscale_models/ltx-2.5-latent-temporal-upscaler-x2-bf16-1.0.safetensors` | — |
| Duration head (optional) | `model_patches/ltx-2.5-duration-head-bf16.safetensors` | — |
| Detailing IC-LoRA (DFR) | separate repo `LTX-2.5-22b-IC-LoRA-Pixel-Spatial-Upscaler` | — |

Official minimum storage is 100 GB free; recommended 200 GB+. Note the text encoder is
**not** substitutable with stock Gemma 4 — loading checks the `gemma4-12b-ltx-v1` version
tag. The bundled encoder is documented as encode-only (its LM head is vestigial), so
`--enhance-prompt` loads a *second* Gemma 4 E2B-it on top (bf16 ~10.2 GB by default).

## Licence

`LTX-2.x Community License Agreement` (a copy dated 2026-01-05 is redistributed in
`xocialize/ltx-2.3-mlx`; the HF page names `ltx-2.x-community-license-agreement`). This is
**not** Apache/MIT — it is a source-available community licence with real conditions:

- **Revenue gate.** Free grant only below **$10,000,000** annual revenue. At or above it,
  a paid commercial licence is mandatory. "Entity" is aggregative: subsidiaries,
  affiliates and companies under common control count together.
- **Copyleft-ish on derivatives.** Fine-tuned or adapted weights, derivative architectures
  and models trained on LTX outputs must be distributed **exclusively under this same
  licence**, with a complete copy included and modified files marked. You may not claim
  exclusive ownership of derivatives that restricts the granted rights.
- **Downstream flow-through.** Any redistribution or SaaS must embed the use-based
  restrictions (Attachment A) as enforceable terms and give recipients a full licence copy.
- **Outputs.** Licensor claims no rights in generated output, but you are accountable for
  inputs, outputs and downstream use.
- **Attachment A is broad**, including: no impersonation/deepfakes without consent; no
  machine-generated content placed in context without clear disclosure; no medical advice;
  no law-enforcement/justice uses; no military/weapons; and two commercial-restrictive
  clauses — (18) commercial use may not train/improve another model except permitted
  LTX-2 derivatives, and (20) no use in a product or service that directly competes with
  Licensor's commercial offerings without a separate commercial licence.
- **Dispute terms.** New York law, ICC arbitration in New York, jury-trial and class-action
  waiver, and a remote-update/termination clause (Section 6) reserving vendor rights to
  restrict or update the model.

Practical implications for this project: bolting LTX-2.5 into a distributed kit means
shipping the full licence and Attachment A to every node/user, propagating the use
restrictions as enforceable terms, and confirming the product does not fall under clause 20
or the $10M revenue gate. The community quantization `mlx-community/ltx-2.5-mlx` and
similar are Derivatives under this licence, not independently licensed. This section is
technical reading of the text, **not legal advice or a redistribution decision**.

## Official hardware position

From the vendor's own System Requirements page: **NVIDIA GPU, minimum 32 GB+ VRAM**,
32 GB system RAM, 100 GB storage, **CUDA 12.7+**, Python 3.12+. Recommended A100 80GB /
H100, 64 GB+ RAM. There is **no official macOS or Apple Silicon support**. The repo notes
`natten` (fastest diffusion-VAE backend) is Linux+CUDA only and silently falls back to
Triton or eager elsewhere. A "16 GB class" self-host claim circulates in third-party
marketing and community quant repos; it is a product claim contradicted by the vendor docs
and is not transferable to Macs.

## Apple Silicon routes found (all third-party)

| Route | What it is | Status / evidence |
|---|---|---|
| `VincentGourbin/ltx-video-swift-mlx` | Swift + MLX, MIT code; supports 2.3 and 2.5, T2V/I2V/V2V, audio, LoRA, QLoRA training, keyframe interpolation, upscale/interpolate | Most complete and best-documented Mac path. **Not ours, not reviewed.** Requires macOS 26.3+, Xcode 26+. Benchmarks below. |
| `ltx-2-mlx` (Python/MLX, used by `georgezhang1225/LTX2.5-MLX-Bridge-For-Mac-ComfyUI`) | Python MLX port driven through a FastAPI bridge + ComfyUI node | Single-author, Chinese-language repo, first push 2026-09-05. Reports 512×896×121 frames in ~5.8 min on **M1 Max 64 GB**, distilled. Anecdotal, not independently verified. |
| `mlx-community/ltx-2.5-mlx` | Community MLX conversion (bf16, split components) | Existence only; not evaluated. Derivative of the gated base. |
| Official `ComfyUI-LTXVideo` | Vendor node pack | Target is ComfyUI; Mac support is incidental, not vendor-tested. |
| Community NV quant packs (e.g. `joeygambino/LTX-2.5-Quantized`) | GGUF / comfy-native quants down to ~8 GB | Aimed at 12–24 GB **NVIDIA** cards and depend on CUDA quant kernels. Not applicable to Metal/unified memory. |

### Measured Apple Silicon evidence (Swift/MLX port, author-reported)

M3 Max 96 GB, macOS 26.3, I2V+audio at 1024×576, 241 frames (10 s):

| Quantization | Generation time | Peak GPU | Mean GPU (denoise) |
|---|---:|---:|---:|
| bf16 (default) | 1145 s (~19 min) | 54.8 GB | 49.7 GB |
| qint8 | 1458 s | 44.6 GB | 32.7 GB |
| int4 | 1294 s | 38.4 GB | 23.7 GB |

These are author-reported numbers for one machine, not our measurement, and do not
establish speed or fit on M1/M2/M4 or on our fleet.

### The decisive memory finding

The same project documents a measured pitfall on the 2.5 path: on-the-fly quantization of
the bundled Gemma 4 encoder **adds** memory rather than replacing it. bf16 encoder measures
**22,711 MB**; after `--transformer-quant int4` the post-quantize measurement is
**29,100 MB** (bf16 weights never released, ~6.4 GB of quantized weights coexist). This was
verified independent of eval ordering, chunk granularity and reference lifetime, and traced
to upstream `SwitchLinear`/`mlx-swift-lm`/`mlx-swift` rather than the port itself. The port's
README states 2.5 "even with `--transformer-quant int4` currently peaks around 38 GB just
loading models at a small resolution", before denoising and VAE decode. Consequence: **on
Macs, quantizing does not rescue a 32–36 GB machine for 2.5.** A 64 GB machine has the
headroom; a 32 GB machine does not.

## Constraints that affect product fit

- Frame count must be `8n+1`, max 481 (20 s at 24 fps, the RoPE limit).
- Width/height divisible by 64; typical outputs 768×512, 1024×576, 832×480.
- Distilled = fixed 8-step (stage 1) + 4-step (stage 2) schedule, CFG 1.0.
- Local generation is minutes-scale per clip, not real-time. The vendor's "10 s in 6.8 s"
  figure is 2× GB200 datacenter hardware, not a Mac or a single consumer GPU.
- The HF repo is gated, so any automated per-node model import needs an accepted licence
  and a read token with gated-repo scope.

## Relationship to this project

- Video is **deferred** by the current design (Hub, Image, Voice, Subtitle only), so this is
  awareness and contingency research, not a task.
- The existing Video Studio source uses Torch/MPS + Diffusers for LTX/Wan/HunyuanVideo and
  native MLX only for Lance 3B. LTX-2.5 is **not a drop-in** for that path: the official
  Python pipeline is CUDA-oriented, and the credible Mac route is a separate Swift/MLX or
  `ltx-2-mlx` runtime with its own bridge and model library.
- Disk/ops impact is material: ~66 GiB minimum for the distilled set plus optional enhancer
  (~10 GB) and upscaler LoRAs, against a fleet whose model library is curated per manifests.
- Audio generation and lip-sync/Dub-It overlap the Voice function's territory and would
  need a deliberate boundary if Video is ever un-deferred.

## Open questions — not executed

1. Confirm any node's actual unified-memory ceiling and whether 64 GB nodes exist in the
   fleet plan before treating 2.5 as a target.
2. Decide whether LTX-2.3 (lighter, ~30 GB, 32 GB+ recommended, mature MLX support) is the
   intended Mac video candidate instead of 2.5.
3. If pursued, qualify exactly one Apple Silicon node on a pinned `ltx-video-swift-mlx`
   commit at 256×256/9 frames first, measuring load peak, denoise peak, VAE decode peak,
   wall time, swap and output quality separately, with concurrent generation disabled.
4. Get the licence/Attachment-A redistribution and clause-18/20 competition questions
   answered before any packaging, and record the outcome in `research/licenses.md`.
5. No download, licence acceptance, build or runtime change is authorized by this note.

## Sources

- HF model page (gated; card body not retrievable anonymously): https://huggingface.co/Lightricks/LTX-2.5
- Official code + Quick Start, asset list and sizes: https://github.com/Lightricks/LTX-2
- Vendor open-source overview: https://docs.ltx.io/open-source-model/getting-started/overview
- Vendor System Requirements (NVIDIA-only): https://docs.ltx.io/open-source-model/getting-started/system-requirements
- Vendor Quick Start / workflow chooser: https://docs.ltx.io/open-source-model/getting-started/quick-start
- API model matrix and durations: https://docs.ltx.io/models/ltx-2-5
- License text (republished copy): https://huggingface.co/xocialize/ltx-2.3-mlx/raw/main/LICENSE
- Swift/MLX Mac port, benchmarks and memory pitfall: https://github.com/VincentGourbin/ltx-video-swift-mlx
- Gemma 4 quantization pitfall (measured): https://github.com/VincentGourbin/ltx-video-swift-mlx/blob/main/docs/knowledge/pitfalls/gemma4-quantize-does-not-release-bf16.md
- Third-party Mac ComfyUI bridge (anecdotal M1 Max timing): https://github.com/georgezhang1225/LTX2.5-MLX-Bridge-For-Mac-ComfyUI
- Community NVIDIA quant pack (not Mac): https://huggingface.co/joeygambino/LTX-2.5-Quantized
- Launch summary with licence/API context: https://llm-stats.com/blog/research/ltx-2.5-launch
