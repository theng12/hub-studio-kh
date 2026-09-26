# Three text-rendering models — rollout plan

Date: 2026-09-22. Scope agreed with owner: add **Z-Image-Turbo**, **Ideogram 4** and
**Qwen-Image-2.1** as text-rendering image models. This is a plan, not a
qualification; gate each phase on evidence.

## Two engines, not one

| Model | Engine | Why this engine |
| --- | --- | --- |
| Z-Image-Turbo (6B) | **`image-mflux`** (existing) | mflux port exists; 4-bit pack is 5.91 GB |
| Ideogram 4 (9B) | **`image-mflux`** (existing) | mflux port exists; typography-focused |
| Qwen-Image-2.1 (7B + 8B TE) | **`image-mlx-serve`** (new) | mflux refuses to quantize the encoder; mlx-serve's 4-bit pack does |

So this work has one shared prerequisite (the mflux bump) and one genuinely new
engine (mlx-serve).

## Machine fit (owner fleet)

Canonical fleet: 2× 8 GB (M1, M2), 3× 16 GB (M1, M2, M4), 1× 24 GB (M4),
1× 32 GB (M2 Max Mac Studio) — see [`research/owner-hardware.md`](owner-hardware.md). Evidence
class in brackets: **[M]** measured, **[V]** vendor claim, **[I]** inference.

| Model | Footprint | 8 GB (M1/M2) | 16 GB (M1/M2/M4) | 24 GB (M4) | 32 GB (M2 Max) |
| --- | --- | --- | --- | --- | --- |
| Z-Image-Turbo | 5.91 GB pack, 8–9 steps | No **[I]** — below the card's 16 GB floor | **Yes** **[V]** | Yes | Yes |
| Ideogram 4 | q4 = 14 GB disk, **22.0 GB peak** | No | **No** | Tight | Yes **[M]** (22.0 GB peak, M5 Pro/64 GB) |
| Qwen-Image-2.1 | 10.7 GB pack, **9.55 GB peak** | No | Documented 16 GB **[V]**, unverified | Yes | Yes **[M]** (M1 Pro/32 GB) |

Fleet answer: only **Z-Image-Turbo on the 16 GB tier** is a text-rendering option that
reaches beyond the single 32 GB machine. Ideogram 4 and Qwen-Image-2.1 are
single-machine (24/32 GB) capabilities, not fleet ones.

## Phase 0 — standalone evidence (no app changes)

Run on one capable Mac (this dev machine is the 32 GB M2 Max (Mac14,13)) before any integration. Every
model here is a compatibility target until it produces a legible-text image here.

1. **Z-Image-Turbo** (smallest, do first):
   ```sh
   uv tool install mflux
   mflux-generate-z-image-turbo \
     --model filipstrand/Z-Image-Turbo-mflux-4bit \
     --prompt "A vintage enamel sign bolted to a brick wall that reads 'STUDIO HUB' in bold serif letters, warm morning light" \
     --width 1024 --height 1024 --steps 9 --seed 42
   ```
   The pack is already 4-bit, so no extra `-q`.
2. **Ideogram 4** (gated repo; needs HF access approval first):
   ```sh
   mflux-save --model ideogram-4-fp8 -q 4 --path ideogram-4-mflux-q4
   mflux-generate-ideogram4 --model ideogram-4-mflux-q4 --prompt-file caption.json
   ```
   Its captions are JSON with explicit `type: "text"` fields and bounding boxes —
   plain prompts underperform.
3. **Qwen-Image-2.1** (mlx-serve):
   ```sh
   brew install ddalcu/mlx-serve/mlx-serve      # or the release tarball
   mlx-serve pull ddalcu/Qwen-Image-2.1-MLX-Serve-4bit
   mlx-serve serve
   curl localhost:11234/v1/images/generations -H 'Content-Type: application/json' \
     -d '{"model":"ddalcu/Qwen-Image-2.1-MLX-Serve-4bit","prompt":"...","size":"1024x1024"}'
   ```
   The pack card still says "not released yet"; PR #477 is merged and v26.9.5
   shipped, so verify the released binary actually loads it.

Acceptance for Phase 0: a legible-text image from each, plus recorded wall clock
and peak memory. Pick the keepers from that, not from vendor claims.

## Phase 1 — mflux bump + FLUX.2 Klein re-qualification

Do this alone, first, so a regression has one cause.

- Branch; pin `mflux==0.20.0` in `packaging/image-runtime/requirements.in`.
- Rebuild + re-audit the Image runtime (mlx ≥ 0.32, transformers ≥ 5.5,
  torch ≥ 2.13, pillow ≥ 12.3).
- Load the **pinned** `AITRADER/FLUX2-klein-4B-mlx-4bit` snapshot and generate one
  real 1K image; compare against the R07 receipt (PNG, dimensions, hash, elapsed).
- Gate: FLUX.2 Klein re-passes before any new adapter lands. The API is verified
  compatible — see `research/mflux-engine-bump-impact.md` — but the runtime hash
  changes, so the old qualification no longer applies.

## Phase 2 — add Z-Image-Turbo + Ideogram 4 to `image-mflux`

The Image worker is single-model by construction (`MODEL_ID` constant,
`validate_request` repo check, `_run` imports `Flux2Klein`, one `/api/catalog`
entry). Adding either requires the per-model dispatch work in
`research/qwen-image-2-1-adoption.md` §5: dispatch keyed by repo/package_id, a
pinned manifest, a `smoke_imports` entry, a catalog entry, an admission tier, and
an idle-cache decision (Image releases after every job today).

Model-specific extras:
- **Ideogram 4**: JSON caption support (the prompt schema is the feature), gated
  HF access, and no img2img in mflux.
- **Z-Image-Turbo**: LoRA and ControlNet are available in mflux if wanted later.

## Phase 3 — mlx-serve engine for Qwen-Image-2.1

Separate decision, because it is a second runtime product, not a model add:

- Adopt the prebuilt signed `mlx-serve` binary / tarball as a managed subprocess.
- Proxy `/v1/images/generations` through a thin worker.
- Model delivery via `mlx-serve pull` rather than the HF SHA-256 manifest path.
- Report memory through the existing policy contract.
- Weigh the broader catalog value, noting mlx-serve also runs Qwen3-TTS and
  ACE-Step, which overlap the existing Voice and Music workers.

## Open risks

1. Ideogram 4 requires gated Hugging Face access approval.
2. Quantization moves glyphs: Ideogram measures 65.6% of subpixels differing at
   q4 vs fp8 on a typographic caption.
3. Every "16 GB" claim here is a vendor target, not a measurement on a 16 GB Mac.
4. The mlx-serve pack card contradicts its own release notes about availability.
