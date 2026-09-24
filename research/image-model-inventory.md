# Studio Hub image model inventory

Date: 2026-09-22. Scope: every image-generation model the Studio Hub application
actually supports today, verified against current source and manifests. This is a
factual inventory, not a roadmap.

## Summary

**Studio Hub ships exactly one image model: FLUX.2 Klein 4B, MLX 4-bit.** There is
no second image model, no model picker with alternatives, and no cloud/hosted image
fallback (hosted work is refused by design — `components/hub/backend/cloud_guard.py`,
`CLOUD_WORK_RETIRED`).

## The one supported image model

| Field | Value |
| --- | --- |
| Display name | FLUX.2 Klein |
| Package id | `flux2-klein-4b-mlx-4bit` |
| Model repo | `AITRADER/FLUX2-klein-4B-mlx-4bit` |
| Pinned revision | `7fd24828501390b67a92c8b66d2fc5a707d0ba1a` |
| Engine | `image-mflux` (mflux on MLX/Metal) |
| Runtime lock | `mflux==0.17.5`, MLX 0.31.2 (`packaging/image-runtime/`) |
| Weights on disk | ≈4.6 GB across the pinned manifest files |
| Capabilities | **1K text-to-image only** |
| Excluded (refused) | 2K, reference images, editing/img2img, LoRA, negative prompt, quantization selection |
| Defaults | 16:9, 1K, 4 steps, guidance 1.0 |
| Qualification | manifest `unqualified`; historical **16 GB** Apple-Silicon 1K receipt |
| Fleet admission tier | **8 GB** measured default (override in `memory_admission.py` and `PROVEN_SMALLER`) |
| Hub operation | `image.text_to_image` |

Evidence:
- `model-manifests/flux2-klein-4b-mlx-4bit.json` — the only `image-mflux` manifest
  in the catalog (the other 33 packages are `voice-mlx`, `subtitle-mlx` or `music-mlx`).
- `components/image/app/backend/generation.py` — `MODEL_ID`/`MODEL_REVISION` are
  module constants; `validate_request()` rejects any other repo and every excluded
  capability.
- `components/image/app/backend/main.py` — `/api/catalog` returns a single hardcoded
  model entry; `/api/generate/txt2img` is the only generation route.
- `components/hub/backend/memory_admission.py` — one image entry in `_FLEET_DEFAULTS`.
- `packaging/image_worker.py` — bundles exactly `flux2-klein-4b-mlx-4bit.json`.
- `components/hub/backend/capabilities.py` — `"image": "image.text_to_image"` is the
  only image operation the Hub recognises.

## What is *not* supported (checked, so it is not assumed)

- No other FLUX family (FLUX.1 schnell/dev, FLUX.2 dev/9B).
- No Qwen-Image, Z-Image, Krea, Stable Diffusion, or any other image family.
- No image editing, inpainting, img2img or reference-image workflow.
- No cloud image provider. `cloud_guard.py` refuses `provider:`, `fal:`, `kie:`,
  `replicate:` schemes and `cloudflare/`, `gemini/`, `together/`, `nebius/`,
  `pollinations/`, `huggingface/` namespaces at submission.
- No second local image worker: the Hub registry has one `Image Studio KH` entry
  (`components/hub/backend/registry.py`), and the Image runtime only imports the
  FLUX.2 Klein module (`packaging/image-runtime/smoke_imports.py`).

## Historical context (read-only source, not this app)

The adopted source launcher (`imagestudio-mac` at `36f88a0…`) had a models tab that
showed green "engine ready" chips for **FLUX.1 schnell / FLUX.1 dev / FLUX.2 dev**,
but clicking Generate raised `NotImplementedError: Phase 2 currently supports only
FLUX.2 klein family`. That drift is documented in
`components/voice/audit_truth.py` (the v1.2.0 "commission lies" bug). It is
**history from the source app, not a capability of Studio Hub** — none of those
families were adopted into `components/image/`.

## Implication for adding a model

Because the Image worker is single-model by construction, any new image model
(Qwen-Image-2.1 included) is not a catalog toggle. It requires a per-model dispatch
layer, a new pinned manifest, a rebuilt/re-audited runtime if the engine version
changes, and a new admission tier. See `research/qwen-image-2-1-adoption.md`.
