# Qwen-Image-2.1 — Studio Hub adoption research

Date: 2026-09-22 (quantized-Mac findings updated). Status: **research only — not
adopted, not packaged, not qualified.** Sources are cited inline; third-party
measurements are labelled as such.

## Update 2026-09-26 — a native mflux q4 pack quantizes the encoder

A new pack,
[`mlx-community/Qwen-Image-2.1-mflux-q4`](https://huggingface.co/mlx-community/Qwen-Image-2.1-mflux-q4)
(revision `746a58556820933a2df5c75887a2570f1ad200c0`, added 2026-09-26), changes the analysis
below: **both the transformer and the Qwen3-VL text encoder are stored in MLX affine 4-bit**
[M, card]. On disk **≈9.6 GB total** [M, HF API]: text encoder 4.27 GB, transformer 4.00 GB,
VAE 1.32 GB, tokenizer. License Apache-2.0 (recorded once).

Card-stated measurements, **on a Mac Studio** [V — the pack author's own numbers, not a fleet
machine]:

| Setting | Peak MLX memory |
| --- | ---: |
| 512×512, 40 steps | **4.68 GiB** |
| 1024×1024, 4 steps | **5.14 GiB** |

Measured with prompt materialization, encoder/transformer eviction, a zero MLX cache and tiled
VAE decoding; packaged size ≈8.9 GB [V]. **Physical 8 GB and 16 GB qualification is explicitly
still required** [V].

**Revised fit read (still unqualified):**

| Tier | Read |
| --- | --- |
| 8 GB | **Plausible, but tight and unproven.** 5.14 GiB peak MLX plus macOS and the runtime leaves little; for scale, FLUX.2 Klein 4B 4-bit measured ~1.03 GiB peak RSS on this project's packaged proof. |
| 16 GB | **The realistic first qualification target.** 5.14 GiB peak leaves headroom for macOS and one Studio. |
| 24 / 32 GB | Comfortable. |

**The blocker is the loader, not the size.** The card says the pack "is intended for the reviewed
low-memory loader in **Rapid-MLX**", and that **stock mflux 0.20.0 skips text-encoder quantization
and cannot load this pack without that loader change** [V]. Studio Hub's `image-mflux` engine is
pinned to mflux 0.17.5, so adopting this means an engine bump **plus** Rapid-MLX's loader (a
second runtime path), and then a physical 8/16 GB qualification. Do not read the 5.14 GiB figure
as an 8 GB pass.

## 1. Verdict for the current fleet (8 / 16 / 24 / 32 GB Apple Silicon)

| Mac unified memory | Any macOS Qwen-Image-2.1 configuration? | Best evidence |
| --- | --- | --- |
| 8 GB | No | Smallest credible pack (mlx-serve 4-bit) measured **9.55 GB peak** at 1024² on a 32 GB M1 Pro. |
| 16 GB | **Only via an unreleased-in-Studio-Hub route** | mlx-serve's 4-bit pack is *documented* "for 16 GB Macs", but measured on 32 GB hardware, by a second runtime Studio Hub does not use. |
| 24 GB | Yes, mlx-serve 4-bit; marginal on mflux | mlx-serve 4-bit measured 9.55–12.95 GB peak; mflux keeps a 17.5 GB bf16 encoder. |
| 32 GB | Yes, mlx-serve 4-bit/8-bit; mflux `-q 8` marginal | mflux `-q 8` ≈30.7 GB peak (M5 Max, third-party); mlx-serve 8-bit measured 12.95 GB on M1 Pro. |

The remaining blockers are **runtime/integration** and the fact that no low-memory
path is integrated in Studio Hub — not the existence of a quantized Mac build. A
quantized Mac build does exist — see §3.

## 2. What the model is

- Unified text-to-image **and** image-editing, open weights released 2026-09-20.
- **7.1B DiT** (32 single-stream block-causal layers) + **Qwen3-VL 8B text encoder**
  + 64-channel RGBA VAE. Up to 10 reference images; native transparency.
- bf16 on disk ≈ **33 GB**: 14.2 GB transformer, 17.5 GB encoder, 1.4 GB VAE.
- The memory floor is the **encoder**, not the 7B DiT. Every Mac design below is
  really a decision about what to do with the 17.5 GB encoder.
- License (recorded once, not revisited): Qwen Research License.

## 3. Quantized builds — what actually exists for macOS

This is the part that changed the picture. Real, primary-source data:

| Pack | Size | Encoder | DiT | Consuming runtime | Status |
| --- | --- | --- | --- | --- | --- |
| `ddalcu/Qwen-Image-2.1-MLX-Serve-4bit` | **10.69 GB** | q4 (5.15 GB) | q4 (4.20 GB) | **mlx-serve** | Packer's PR **#477 merged** and mlx-serve **v26.9.5 shipped 2026-09-21** |
| `ddalcu/Qwen-Image-2.1-MLX-Serve-8bit` | 17.65 GB | 8-bit | 8-bit | mlx-serve | Same release; documented for 32 GB Macs |
| `mlx-community/Qwen-Image-2.1-MLX-4bit` | 10.49 GB | q4 (5.13 GB) | q4 (4.00 GB) | not documented | diffusers layout; no released runner named |
| `toxicdog/Qwen-Image-2.1-MLX` | 10.71 GB | q4 (6.02 GB) | q4 (4.00 GB) | not documented | custom layout; no released runner named |
| `JoyFusionAI/Qwen-Image-2.1-MLX-4bit` | 20.47 GB | **bf16 (17.3 GB)** | q4 (4.0 GB) | mflux-shaped | Matches mflux, but saves no memory |
| mflux `-q 4` / `-q 8` | bf16 encoder on disk | **bf16** | q4/q8 | mflux | ~30.7 GB peak at `-q 8` (M5 Max) |
| Community GGUF (unsloth, Abiray, AlperKTS, leejet, …) | ~11–15 GB sets | separate | GGUF | ComfyUI-GGUF (CUDA) | No supported macOS replay |

### The decisive technical finding

**mflux cannot use the encoder-quantized 10.5 GB packs.** In mflux's own weight
definition (`src/mflux/models/qwen21/weights/qwen21_weight_definition.py`) the
text encoder is declared:

```python
name="text_encoder",
hf_subdir="text_encoder",
skip_quantization=True,  # Quantization causes significant semantic degradation
```

and `WeightApplier.apply_and_quantize_single` skips the quantize step for a
`skip_quantization` component before loading. So mflux always keeps the encoder
bf16, which is exactly why its floor is ~17.5 GB and why `-q 8` still peaks near
30.7 GB. Feeding it a q4-encoder pack is not a supported path.

### The one route that does lower the floor: mlx-serve

The pack author's model card gives measured numbers (M1 Pro, 32 GB):

| Pack | Resolution / steps | Wall clock incl. load | Peak memory |
| --- | --- | --- | --- |
| 8-bit | 1024² / 40 | 985 s (~23 s/step) | 12.95 GB |
| 4-bit | 1024² / 3 | 87 s | **9.55 GB** |
| 4-bit | 512² / 20 | 118 s | — |

mlx-serve "loads the text encoder per request and frees it before the denoise, so
the resident set is the DiT and VAE" — the trick that makes 16 GB plausible.
It supports text-to-image, image-to-image and negative-prompt guidance.

Hardware caveat for the mlx-serve route: **every published measurement is on an
M1 Pro (32 GB), never on a 16 GB machine.** The "for 16 GB Macs" line is the
packer's target, and 9.55 GB peak on a 16 GB Mac mini is tight against macOS plus
the GPU wired limit. Speed also differs sharply by chip: diffusion is largely
memory-bandwidth bound, and a base M1 mini (~68 GB/s, 8-core GPU) is roughly
2.5–3× slower than the measured M1 Pro (~200 GB/s, 16-core GPU). The measured
8-bit 40-step run took 985 s on M1 Pro; a base M1 could plausibly take ~40–50 min
per 1024² image (estimate, not measured). The 4-bit 87 s figure was **3 steps**,
not the model's 40-step default.

Two caveats on this route:
1. **The pack's own card is stale.** `ddalcu/Qwen-Image-2.1-MLX-Serve-4bit` still
   says "Not released yet … load only on the branch `feat/qwen-image-2.1`", but PR
   #477 **merged 2026-09-21** into `main` and mlx-serve **v26.9.5** (published the
   same day) lists Qwen-Image-2.1 generation from these two packs. Treat the card's
   warning as out of date, but re-verify against the released build before relying
   on it.
2. **The pack card's license metadata is inaccurate.** It declares
   `license: apache-2.0`; the base weights are Qwen Research License (the
   `toxicdog` pack records this correctly). Noted once for accuracy.

## 4. Adaptor options, in order of fit for Studio Hub

1. **mflux** (Studio Hub's existing `image-mflux` engine) — smallest architectural
   step, but **cannot quantize the encoder**, so it cannot reach the 8–24 GB fleet.
   Still needs the runtime bump to mflux 0.20.0 (`mlx ≥ 0.32`, `transformers ≥ 5.5`,
   `torch ≥ 2.13`); MLX 0.32.2 still ships macOS 14 arm64 wheels, so the Sonoma
   floor would hold. Only txt2img and plain img2img; editing/reference and the
   prefix KV cache are not in the mflux port.
2. **mlx-serve** (the only released low-memory route) — a **second, non-Python,
   Zig-based runtime** with its own server, model packs and build pipeline. This is
   a far larger adoption than an mflux model: new engine, new runtime audit, new
   packaging and its own memory/load model. It is also the only way to run
   Qwen-Image-2.1 below 32 GB today.
3. **ComfyUI + GGUF/int8** — huge pack ecosystem, but CUDA/ComfyUI-GGUF centric;
   not a packaged Studio Hub path on macOS.
4. **Diffusers on MPS** — reference implementation, ~85 s/image, memory-heavy.

## 5. What adding an adaptor means in Studio Hub (concrete work)

The Image worker is single-model by construction (see
`research/image-model-inventory.md`): `MODEL_ID` is a module constant,
`validate_request()` rejects any other repo, `_run()` imports `Flux2Klein`
directly, `/api/catalog` advertises one model, and `memory_policy.py` hardcodes
`MODEL_ID` with `FIXED = True`.

For either route this means: a per-model dispatch keyed by repo/package_id, a new
pinned manifest, a rebuilt + re-audited runtime, a new `smoke_imports` entry, and a
per-model admission tier. Image also releases its model after every job, so a
large encoder would reload each generation unless an opt-in idle-cache mode is
added — a change to the fleet memory contract, not just the worker. The mlx-serve
route adds a second runtime product to build, sign, audit and update.

## 6. Blockers, in priority order

1. **Runtime/integration — no low-memory Mac path exists in Studio Hub today.** The
   only sub-32 GB route is a whole second runtime (mlx-serve).
2. **Engine gap — editing and prefix KV cache are missing from the mflux port**,
   which is where much of this model's value is.
3. **Unverified 16 GB behavior** — the only measurements are on a 32 GB M1 Pro; see
   the hardware caveat in §3.

## 7. Recommendation

The honest answer to "is there a quantized Qwen-Image-2.1 that runs on macOS" is
**yes, one — the mlx-serve 4-bit pack — and it is new and second-runtime.** Nothing
that fits the 8–16 GB nodes is integrated in Studio Hub, and nothing released is
mflux-loadable at low memory.

Recommended sequence:
1. **Do not adopt into the fleet yet.** Keep FLUX.2 Klein 4-bit as the fleet image
   engine.
2. Verify on **one** 16 GB M1 mini first (mlx-serve v26.9.5 + the 4-bit pack,
   outside the product) before buying or stocking hardware. That is the only
   configuration with published Mac measurements, and none of them are on 16 GB.
3. Revisit mflux only if it gains encoder quantization or the edit/reference path.
