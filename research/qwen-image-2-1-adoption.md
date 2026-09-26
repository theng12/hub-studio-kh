# Qwen-Image-2.1 — Studio Hub adoption research

Date: 2026-09-22 (quantized-Mac findings updated). Status: **research only — not
adopted, not packaged, not qualified.** Sources are cited inline; third-party
measurements are labelled as such.

## Which pack was measured, and which was not — 2026-09-26 `[M]`

The owner asked which version was tried, and the distinction decides how far the measurement
travels. **One pack was run, and it is not the one this document's newest note points at.**

| Pack | Revision | Layout / runner | Tried? |
| --- | --- | --- | --- |
| **`ddalcu/Qwen-Image-2.1-MLX-Serve-4bit`** | **`88eb1b3bb5591ed59b68a6e1a1c2d9baade73e38`** (`refs/main`) | mlx-serve; text encoder **and** DiT affine 4-bit (group 64), `--preset 16gb` | **YES — measured below** |
| `mlx-community/Qwen-Image-2.1-mflux-q4` | `746a58556820933a2df5c75887a2570f1ad200c0` | mflux layout, card names a **Rapid-MLX low-memory loader** | **No** |
| `mlx-community/Qwen-Image-2.1-MLX-4bit` | `4db4e8c0c0e7` | diffusers, **no runner named** | No |
| Rapid-MLX's own `qwen-image-2.1` alias | not resolved | mflux 0.20, **8-bit transformer + bf16 text encoder** | No |

**So the 15 GB peak and the "not a 16 GB model" verdict belong to the mlx-serve 4-bit pack** —
the one whose own card says *"10.7 GB, for 16 GB Macs"*. They do **not** transfer to
`…-mflux-q4`, whose 4.68 / 5.14 GiB figures remain a card claim `[V]` with no fleet measurement
behind them.

### The 4.68 / 5.14 GiB claim has a problem, and it is Rapid-MLX's own published table

The `…-mflux-q4` card says the pack "is intended for the reviewed low-memory loader in
**Rapid-MLX**". Rapid-MLX is a real, released project (`raullenchai/Rapid-MLX`, Apache-2.0,
`brew install rapid-mlx`), and its README publishes a per-alias image matrix. Its
**`qwen-image-2.1`** row reads `[V, vendor docs 2026-09-26]`:

| Alias | Modes | Download | Minimum unified memory | Default steps |
| --- | --- | ---: | ---: | ---: |
| `qwen-image-2.1` | Generate + edit | **30.9 GiB** | **32 GB** | 40 |
| `flux2-klein-4b` | Generate + edit | 4.3 GiB | 12 GB | 4 |
| `z-image-turbo` | Generate | 5.5 GiB | 16 GB | 8 |

and the prose beside it says *"`qwen-image-2.1` uses mflux 0.20 with an **8-bit transformer and
bf16 text encoder**"* `[V]`. That is the **encoder-quantized pack's whole point removed**: a bf16
encoder is the 17.5 GB floor this document already identified as the reason mflux cannot reach a
small Mac. Nobody's released alias claims 5.14 GiB.

**What that means for the "is there a sub-32 GB Qwen-Image-2.1" question.** Two independent,
released routes were checked and neither offers one: mlx-serve's 4-bit pack measured **15 GB**
here, and Rapid-MLX's own alias declares **32 GB minimum with a bf16 encoder**. The
4.68 / 5.14 GiB figures come from a pack card whose named loader is not what the loader's own
product ships. Treat them as unverified until someone runs *that pack* on *that loader* and
measures it — which is the only experiment that would settle it.

## Measured on this Mac — 2026-09-26 `[M]`

The owner asked to try it. Both pieces were already on the machine: the pack at
`~/.mlx-serve/models/ddalcu/Qwen-Image-2.1-MLX-Serve-4bit` (10,673,921,337 bytes on disk `[M, HF
API]`) and `mlx-serve 26.9.5` with `mlx 0.32.2` at `~/.local/mlx-serve/`. Served on loopback only
(`serve --host 127.0.0.1 --port 11234`), on the **32 GB M2 Max**, with Studio Hub's five workers,
the gateway, Story Studio and the agent harness all running.

**The pack README's "Not released yet … load only on the branch `feat/qwen-image-2.1`" is stale.**
`mlx-serve list` reports the pack as `TYPE image, 9.9 GB`, and 26.9.5 generated from it. The §3
finding that PR #477 shipped in v26.9.5 is the correct one; treat the card's notice as out of date,
which is what §3 already said to do.

| Run | Wall | Peak process footprint | Server's own accounting |
| --- | ---: | ---: | --- |
| 512×512, 8 steps | **50 s** | RSS peak 8.96 GB | — |
| 1024×1024, 8 steps | — | **15 GB** (`footprint` `phys_footprint_peak`) | `bytes_resident` **13.94 GB** |
| 1024×1024, 20 steps | **460 s** | **15 GB** | 13.94 GB |

Quality is strong at 20 steps: photorealistic, correct hands, real depth of field. Samples kept at
`/tmp/voiceaudit/qwen-quality.png` (1024², 20 steps) and `qwen-512-decoded.png` (512², 8 steps).

**The pre-flight refuses to load this model on a normal desktop, and it is right to.** First
attempt: `[preflight] media peak ~13.94 GB (staged residency), available 13.49 GB` →
*"needs ~16.7 GB free (13.9 GB for the model plus headroom for warmup buffers) but only 13.5 GB is
available"*. macOS reported **0.1 GB free with 14.3 GB inactive** — the reclaimable-cache case
`--skip-mem-preflight` exists for, and the run then succeeded with no crash.

**So "10.7 GB, for 16 GB Macs" does not survive measurement on this machine.** The card's 9.55 GB
"peak memory" is not the figure that decides a fit: the model's **residency is 13.94 GB** by the
server's own count, the **observed peak process footprint is 15 GB**, and the loader wants ~16.7 GB
free before it will start. A 16 GB Mac has ~15.5 GB usable, so this is **not a 16 GB model** on this
evidence, and it is nowhere near the 8 GB tier that FLUX.2 Klein 4B MLX 4-bit and Z-Image-Turbo
MLX 4-bit run on. §1's verdict stands and is now measured rather than inferred.

**What it would take to adopt it is unchanged by the measurement**: a second runtime (Zig, its own
server, packs and build pipeline) inside a single-model Image worker, per §5. The measurement adds
one more thing to that list — **an idle-cache/reload decision**, because at 15 GB peak the encoder
and DiT cannot both stay resident next to anything else on a small Mac.

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
and cannot load this pack without that loader change** [V]. Studio Hub's Image runtime already
pins **mflux 0.20.0 + MLX 0.32.2** [M — verified in the shipped bundle, 2026-09-26], so the engine
bump this pack names is **already in place**; what is still missing is the **Rapid-MLX low-memory
loader** (a separate runtime path, not stock mflux), and then a physical 8/16 GB qualification. Do
not read the 5.14 GiB figure as an 8 GB pass.

### The two "4-bit" packs are not the same thing

The owner asked whether
[`mlx-community/Qwen-Image-2.1-MLX-4bit`](https://huggingface.co/mlx-community/Qwen-Image-2.1-MLX-4bit)
(`4db4e8c0c0e7`, 2026-09-20, 10.50 GB [M, HF API]) and the `…-mflux-q4` pack above are the same or
different. Same **base model** (`Qwen/Qwen-Image-2.1`), same 4-bit idea; different **layout**,
different **documented runner**, and different **encoder precision**:

| | `…-MLX-4bit` | `…-mflux-q4` |
| --- | --- | --- |
| Layout | diffusers (`model_index.json`, `scheduler/`, per-component configs) — upstream-shaped | mflux (`text_encoder/0.safetensors`, sharded) |
| `library_name` | `mlx` | `mflux` |
| Text encoder on disk | 5.13 GB | 4.26 GB |
| Runner named on card | **none** | Rapid-MLX low-memory loader |
| Memory measured on card | **none** | 4.68 / 5.14 GiB |
| Conversion toolchain stated | no | **mflux 0.20.0 + MLX 0.32.2** |

The `…-MLX-4bit` card is six lines long [M, card] — no runner, no steps, no memory, no conversion
revision. A pack with **no named loader and no numbers cannot be qualified**; "diffusers layout" is
fidelity to the reference structure, not evidence of better images. The `mflux-q4` card instead
states it was converted from upstream `790c92633540` **with mflux 0.20.0 and MLX 0.32.2** [M, card]
— the exact pair Studio Hub already pins [M, shipped bundle] — and names its loader.

**Why neither loads in stock mflux, in mflux's own words.** The upstream port
(`src/mflux/models/qwen21/README.md`) states: *"The text encoder is kept in bf16 like the 1.x port;
quantization applies to the transformer and VAE"*, and *"Peak memory is ~46 GB (the Qwen3-VL text
encoder, ~17.5 GB bf16, is never quantized and stays resident)"* [M, mflux repo]. It also lists
`-q 8`, which quantizes the transformer and VAE only. So on the shipped engine the **floor is the
encoder**: no 8/16/24 GB tier can run 2.1 through stock mflux at all, and `-q 8` (≈30.7 GB peak,
third-party) is marginal even on the single 32 GB M2 Max. Both community 4-bit packs exist precisely
because they quantize what mflux refuses to — the very change mflux's maintainers declined to make,
which is where any quality risk lives.

**Unmeasured:** which 4-bit conversion makes better images. The DiT is ≈4.00 GB in both, so the
delta is mostly the encoder, and no comparison has been published. Do not claim one is better.

**Answer to the owner:** not the same, but not two models either — one is a pack with a named runner
and numbers (`mflux-q4`, the only actionable one), the other is a layout conversion with no runner
(`MLX-4bit`). "Feels more original" is not a quality signal here: on layout the *diffusers* pack is
the upstream-shaped one; on provenance the *mflux* pack is the better-documented one, because it
names its conversion toolchain, its source revision and its loader.

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
