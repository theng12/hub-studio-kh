# mflux engine bump 0.17.5 → 0.20.0: effect on FLUX.2 Klein

Date: 2026-09-22. Question: if Studio Hub bumps its `image-mflux` engine to get
Z-Image / Ideogram 4 / ERNIE / Boogu, does the existing fleet-qualified
**FLUX.2 Klein 4B** break? Answer: **the code path is backward-compatible; the
qualification is not — it must be re-run.**

## What the Image worker actually uses

`components/image/app/backend/generation.py` calls:

```python
from mflux.models.flux2.variants.txt2img.flux2_klein import Flux2Klein
flux = Flux2Klein(model_path=..., quantize=None, lora_paths=None, lora_scales=None)
result = flux.generate_image(seed=, prompt=, num_inference_steps=, height=, width=, guidance=)
result.save(str(staged), overwrite=True)
```

plus `mflux.models.common.config.model_config` in
`packaging/image-runtime/smoke_imports.py`.

## Verified against mflux 0.20.0 (`main`)

| Symbol / call | Status in 0.20.0 |
| --- | --- |
| `...flux2.variants.txt2img.flux2_klein.Flux2Klein` | **present**, same path |
| `Flux2Klein(quantize, model_path, lora_paths, lora_scales, ...)` | **present**; new optional `bake_lora`, `model_config` added after them |
| `generate_image(seed, prompt, num_inference_steps, height, width, guidance, ...)` | **present**; new optional `pid_decode` added |
| `GeneratedImage.save(path, overwrite=...)` | **present**, `overwrite` still a keyword |
| `ModelConfig.flux2_klein_4b()` | **present** |
| `mflux.models.common.config.model_config` | **present** |
| `flux2/weights/flux2_weight_definition.py`, `flux2/flux2_initializer.py` | **present** (HTTP 200) |

So there is **no import break, no constructor break, no `generate_image` break and
no `save` break** for the pinned FLUX.2 Klein path. FLUX.2 Klein also received
fixes in the interval, e.g. 0.18.0 "Fix `FileNotFoundError: text_encoder_2` by
routing Klein models through `Flux2Klein` and skipping the unused T5/`text_encoder_2`
weight path", and a memory fix that evicts the text encoder after encoding.

## What does change (the real costs)

1. **Runtime identity changes.** The Image worker publishes `runtime_revision` as
   the runtime's SHA-256. A new mflux changes that hash, so the Hub's
   `runtime_compatible` read and the R07 FLUX qualification receipt (recorded
   against mflux 0.17.5) no longer describe the new artifact. Per `AGENTS.md`,
   this is a **compatibility target, not a passing qualification**: FLUX.2 Klein
   must be re-qualified on the new runtime before the fleet trusts it.
2. **Hosted deps move.** mflux 0.20.0 requires `mlx >= 0.32, < 0.33`,
   `transformers >= 5.5`, `torch >= 2.13`, `pillow >= 12.3`,
   `huggingface-hub >= 1.1.6`. The whole Image runtime is rebuilt and re-audited
   (Mach-O, arm64, macOS floor, relocation smoke, hash manifest). MLX 0.32.2 still
   publishes `macosx_14_0_arm64` wheels, so the Sonoma floor holds.
3. **Weight-mapping risk is the one thing tests cannot cover.** The existing
   `tests/test_image_standalone.py` uses a **fake** mflux module, so it proves the
   worker's behaviour, not that the *pinned AITRADER snapshot* loads through the
   new initializer. A key-mapping change in `flux2_weight_definition.py` would
   only surface in a real load.
4. **Memory reporting may shift.** The 0.18.0 text-encoder eviction changes peak
   RSS, which the job record stores. Not a correctness issue.

## Recommended sequencing

Bump as its own step, before any new model:

1. Branch the bump; keep the 0.17.5 runtime artifact intact.
2. Rebuild the Image runtime on 0.20.0; run `smoke_imports` and the existing suites.
3. Load the **pinned** FLUX.2 Klein snapshot and generate one real 1K image;
   compare PNG, dimensions, hash and elapsed time against the R07 receipt.
4. Only once FLUX.2 Klein re-passes, add the Z-Image / Ideogram 4 adapters on the
   new engine.

That ordering isolates "did the engine bump regress FLUX?" from "does the new model
work?", so a failure has one cause instead of two.
