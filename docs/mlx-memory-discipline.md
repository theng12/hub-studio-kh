# MLX memory discipline

Why this exists: on 2026-10-01 an 8 GB Mac mini ran Qwen3-TTS 0.6B 8-bit (weights 2.1 GB) to a **7.7 GB** peak, VoxCPM2 4-bit to 7.5 GB and OmniVoice to 6.0 GB,
with critical memory pressure and 97-130 GB of SSD swap writes per hour of generation (a 256 GB mini would be worn out in months). The 3-minute script never finished.
Nothing was leaking: one section's codec decode allocated 5-7 GB of transient buffers, and MLX kept every freed buffer in a cache whose ceiling scales with free RAM
(12-17 GB on a 32 GB Mac).

**The target on an 8 GB Mac is a worker peak (process footprint) of about 4.5 GB or less, so that swap-outs stay near zero.** Peak **active** MLX memory
(`mx.get_peak_memory`) is the number to optimise; process footprint (`footprint`/`proc_pid_rusage` phys_footprint, includes Python, ~0.7-0.9 GB over MLX) is the number to
publish.

## Rules (all studios)

1. **Cap the MLX buffer cache.** `mx.set_cache_limit` once per worker process, before the model loads. Default 512 MB (`STUDIOHUB_MLX_CACHE_LIMIT_MB`). 0 is slower (VoxCPM2 1.6x); 256 MB-2 GB measured the same.
2. **Never decode a whole section/image in one pass.** Codec, vocoder and VAE decodes build one lazy graph for the whole input and hold every intermediate until the end (command buffers keep
   inputs alive). Run the stack in **slabs with context**: a causal conv stack needs `receptive field` frames of left context; a non-causal ("same"-padded) stack needs it on both sides. Add `mx.eval` after each layer/op so Metal can recycle the previous stage.
   Keep the audio identical: compare slabbed against the original decode on real or random latents (target max difference <= 1e-5 in float; a joined 16-bit WAV must match to 1 LSB).
3. **Encode a reference once.** Every section of a long script re-encoded the same reference clip (+1.3-1.8 GB each time). Cache the encoded features keyed on the file (path, mtime, size) or on a hash of the samples. Keep at most a few entries.
4. **Sync inside big transformers when weights are upcast.** A bf16/4-bit layer fed fp32 activations holds an fp32 copy of *every layer's* weights until the graph is evaluated (VoxCPM2's local encoder: +0.8 GB per call, 170 calls per section). `mx.eval` after each layer.
5. **Clear the cache between sections** (`_release_device_memory` already does) and after any stage you slab.
6. **Measure before you set a floor.** `min_free_memory_gb` is the *measured peak process footprint plus about 0.25 GB*, on the **8 GB runner** (`qualification/eight-gb-tts-run.sh`, S2 and S4, both owner voices). A number computed
   from file sizes, a multiple of the weights or "what the other model uses" is a formula, and **a floor must never be a formula**. Every non-empty `min_free_memory_gb` carries a `min_free_memory_note` saying what was measured, on which Mac, when.
7. **Do not trust RTF measured on a busy Mac.** Peaks are exact under load; timings are not (load average 17-54 during this work made RTF swing 2-5x).

## Voice

Code: `components/voice/app/backend/mlx_memory.py`, applied by `GenerationManager._mlx_audio_get_model` (`bound_cache()` before load, `bound_model(family, model)` after).
`mlx_memory.PROFILES` is the registry: **every family in `generation.MLX_AUDIO_FAMILIES` must be listed**, either `bounded` (with its bounder) or `cache-only` with the reason. `tests/test_mlx_memory_registry.py` fails otherwise.

Measured 2026-10-01/02 on a 32 GB M2 Max (same code path as the Hub's voice child; script S2 = 5 sections, ~67 s of audio; library voice Chris, seed 1234; MLX peak active GB):

| Model | Before | After | Process footprint after | What was wrong |
|---|---|---|---|---|
| Qwen3-TTS 0.6B Base 8-bit | 7.28 (S4 8.00) | 3.06 | 3.8-3.9 | `chunked_decode` whole 325-token chunk; reference encode +1.3 GB |
| VoxCPM2 4-bit | 9.22 | 3.3 | 4.1-4.3 | audio-VAE decode of 25-30 s at 48 kHz in one pass; fp32 weight copies in the local encoder; reference encoded twice per section |
| OmniVoice bf16 | 5.41 | 2.7 (3.44 first section) | 3.4-4.1 | Higgs codec decode of the whole section; reference re-encoded each section |
| Qwen3-TTS 0.6B Base 4-bit | 7.17 | 2.8-3.2 | 3.6-3.8 | same speech tokenizer as the 8-bit |
| Breeze TTS 2 4-bit | 9.9 | 4.2-4.4 | 4.85-5.33 (above the 4.5 GB target: weights are 3.1 GB) | shares Qwen3's speech tokenizer (decode + reference encode), plus a staged backbone prefill; weights alone are 3.1 GB |
| Fish S2 Pro 8-bit | 13.3 (fleet) | not bounded | - | 6.7 GB of weights: not an 8 GB candidate, registered `cache-only` |

### Adding a new MLX TTS engine
1. Add the family to `catalog.py` and `MLX_AUDIO_FAMILIES` as usual.
2. Run it through the standalone harness (`_orchestra/audit-20260930/qwen-mem/qwen_mem.py --model <name> --script S2`) with `QM_TRACE` style wrappers around its codec `decode`/`encode` to find the peak: print `mx.get_peak_memory()` before/after each stage with `mx.reset_peak_memory()` between.
3. If decode dominates: write a slabbed decode in `mlx_memory.py` (copy `_voxcpm_decode` for causal stacks, `_omnivoice_decode` for symmetric ones), pick the slab (24-32 frames) and context (>= receptive field; check that a context of 1 *fails* your equality test), cache the reference encode, add the bounder to `PROFILES`.
4. Add an exactness test in `tests/test_mlx_memory.py` with a tiny randomly initialised copy of the real classes (see the existing ones).
5. Measure the peak footprint on the 8 GB runner and set `min_free_memory_gb` **and** `min_free_memory_note`.
6. If you genuinely will not bound it (weights alone exceed the 8 GB tier, e.g. Fish S2 Pro 5B), register it `cache-only` with that reason.

## Image

*(To be filled in by the image-memory work on `fix/image-memory-8gb`: the same rules applied to the image studio - peak active/footprint per model on the 8 GB runner, the VAE/decoder
tiling it uses, where its cache cap lives, and how a new image model is registered.)*
