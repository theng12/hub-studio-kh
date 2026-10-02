# MLX memory discipline

Rules every Studio that runs an MLX model follows, learned from measurements on an 8 GB Mac. The fleet is ~35
8 GB machines, and an MLX worker there fails in one of two ways: its **hot working set** (what it touches every
step) exceeds what the OS can give it, so it swaps and thrashes; or its **peak** (usually one stage) pushes the
Mac into critical memory pressure. Both are visible in a 32 GB Mac's numbers if you measure per stage, with MLX's
cache capped, and look at the process **footprint** and not only MLX's own peak.

`tests/test_image_memory_profile_guard.py` fails if an image model is added without a memory profile (or a
documented exemption in this file); the Voice worker's `components/voice/app/backend/mlx_memory.py` is the same
discipline for TTS.

Why this exists: on 2026-10-01 an 8 GB Mac mini ran Qwen3-TTS 0.6B 8-bit (weights 2.1 GB) to a **7.7 GB** peak, VoxCPM2 4-bit to 7.5 GB and OmniVoice to 6.0 GB,
with critical memory pressure and 97-130 GB of SSD swap writes per hour of generation (a 256 GB mini would be worn out in months). The 3-minute script never finished.
Nothing was leaking: one section's codec decode allocated 5-7 GB of transient buffers, and MLX kept every freed buffer in a cache whose ceiling scales with free RAM
(12-17 GB on a 32 GB Mac).

**The target on an 8 GB Mac is a worker peak (process footprint) of about 4.5 GB or less, so that swap-outs stay near zero.** Peak **active** MLX memory
(`mx.get_peak_memory`) is the number to optimise; process footprint (`footprint`/`proc_pid_rusage` phys_footprint, includes Python, ~0.7-0.9 GB over MLX) is the number to
publish.

## Rules that hold for every model

1. **Measure per stage, not per job.** Reset `mx.reset_peak_memory()` between load, encode, denoise, decode and
   read `mx.get_peak_memory()`; sample the process footprint (`proc_pid_rusage`, `ri_phys_footprint`) beside it.
   `qualification/image_memory_probe.py` is the template. Footprint = MLX active + MLX cache + Python (~0.5 GB);
   it is what the kernel swaps on.
2. **Cap the MLX buffer cache** (`mx.set_cache_limit`). MLX keeps every freed buffer for reuse and its default
   limit scales with the machine: after one 1280x720 FLUX.2 Klein decode a 32 GB Mac held 24 GB of cache. A cap of
   0.25-2 GB cost nothing measurable in speed (Klein 79-82 s at 0.25-2 GB) and is the cheapest memory win there is.
3. **Free a stage's weights before the next stage loads, and make sure they are actually free.** Three traps found
   in mflux 0.20.0, all invisible without a per-stage measurement:
   * mflux loads weights lazily (`mx.load` reads a file when an array is first evaluated), and the prompt
     embeddings are returned *unevaluated*. Deleting `model.text_encoder` before the loop frees nothing, because the
     embeddings still reference its weights; **evaluate the embeddings first, then drop the encoder.**
   * `generate_image` keeps a local `predict` closure over the transformer for the whole call, so
     `model.transformer = None` after the loop frees nothing; and `mx.compile` (used on every Mac that is not an
     M1/M2 non-Max) caches the weights it traced until the function object dies. Route the loop through one holder
     object you can empty.
   * A freed model is not freed until `gc.collect()` **and** `mx.clear_cache()` have both run.
4. **Bound the decode.** A VAE/codec decode is a stack of convolutions at full output resolution; MLX materialises
   an unfolded copy of every 3x3 convolution input (about 9x) and mlx's `pytorch_compatible` GroupNorm makes
   several float32 copies. A 0.17 GB FLUX.2 VAE peaked at 9-12 GB for one 1280x720 image. Do not tile a decoder whose
   GroupNorm statistics are global (the colour shifts between tiles; mflux marks this decoder
   `supports_implicit_tiling = False`): band the *local* ops (3x3 convolution with a one-row halo) and keep the
   *global* ones global (two-pass GroupNorm statistics over bands). Same maths, bf16-rounding-level difference.
5. **Evaluate per block where a pass uses each weight once or can re-read it.** A layer-by-layer `mx.eval` lets MLX
   recycle the previous layer's buffers (text encoder: 1.56 GB -> 0.40 GB of activations over the weights). For a
   pass that runs every step, stream the blocks from their files: only read-only data is ever dropped, so nothing
   is written to swap.
6. **Never evaluate inside `mx.compile`.** Streaming needs mflux's non-compiled denoise function (the one it
   already uses on M1/M2).
7. **Keep what is worth keeping, only where there is room.** A model whose configuration keeps every component
   loaded can stay resident between jobs (saves the weight read: ~3 s of ~80 s on a warm 32 GB Mac, more on a cold
   disk); drop it on the idle window, on the Hub's release, on memory pressure, or when another configuration is
   needed. A configuration that frees components as it goes can never be resident.
8. **Verify the emulation, then the claim.** On a bigger Mac, emulate the small one's MLX limits
   (`mx.set_memory_limit`/`set_cache_limit` at 8.5 GB) and its compile behaviour; the emulated "before" matched the
   real 8 GB M1 run (9.8 GB MLX, 9.1-9.4 GB footprint). Then confirm on the real Mac: this repo's Mac-wide runner
   (`qualification/eight_gb_image_run.py`) records swap and SSD writes per image.

### Further rules from the Voice (TTS) work

Rule 2 above and Voice's cache cap are the same rule (the Voice worker's default is 512 MB, `STUDIOHUB_MLX_CACHE_LIMIT_MB`; 0 is slower, VoxCPM2 1.6x); rule 4 above is the Image form of Voice's slabbed decode.

9. **Never decode a whole section in one pass.** Codec, vocoder and VAE decodes build one lazy graph for the whole input and hold every intermediate until the end (command buffers keep
   inputs alive). Run the stack in **slabs with context**: a causal conv stack needs `receptive field` frames of left context; a non-causal ("same"-padded) stack needs it on both sides. Add `mx.eval` after each layer/op so Metal can recycle the previous stage.
   Keep the audio identical: compare slabbed against the original decode on real or random latents (target max difference <= 1e-5 in float; a joined 16-bit WAV must match to 1 LSB).
10. **Encode a reference once.** Every section of a long script re-encoded the same reference clip (+1.3-1.8 GB each time). Cache the encoded features keyed on the file (path, mtime, size) or on a hash of the samples. Keep at most a few entries.
11. **Sync inside big transformers when weights are upcast.** A bf16/4-bit layer fed fp32 activations holds an fp32 copy of *every layer's* weights until the graph is evaluated (VoxCPM2's local encoder: +0.8 GB per call, 170 calls per section). `mx.eval` after each layer.
12. **Clear the cache between sections** (`_release_device_memory` already does) and after any stage you slab.
13. **Measure before you set a floor.** `min_free_memory_gb` is the *free memory a job needs before it starts* (NOT its peak), read from the **8 GB runner**'s "Started with N GB free" for jobs that started cleanly (`qualification/eight-gb-tts-run.sh`, S2 and S4, both owner voices; images: `qualification/eight_gb_image_run.py`). A number computed
    from file sizes, a multiple of the weights or "what the other model uses" is a formula, and **a floor must never be a formula**. Every non-empty `min_free_memory_gb` carries a `min_free_memory_note` saying what was measured, on which Mac, when.
    (0.6.131: Qwen3-TTS 0.6B 8/4-bit, VoxCPM2 4-bit, OmniVoice, Klein and Z-Image are all 2.8 (the owner's choice, for headroom) over the real 8 GB M1+M2 runs of 2026-10-02: started cleanly from ~3.1-4.6 GB free, peaks 3.65-4.41 GB voice / 2.16-2.97 GB image, ~0 swap. The old peak + 0.25 GB rule made healthy 8 GB Macs wait.)
14. **Do not trust RTF measured on a busy Mac.** Peaks are exact under load; timings are not (load average 17-54 during this work made RTF swing 2-5x).

## Image

Worker: `components/image/app/backend/` - `generation.py` (registry, `MemoryPolicy`, `memory_plan`), `staging.py`
(rule 3), `vae_bounded.py` (rule 4), `streaming.py` (rules 5-6), `memory_policy.py` (rule 7).

What dominated, measured per stage at 1280x720 (this Mac, mflux 0.20.0, 8 GB MLX limits emulated):

| model (4-bit) | weights | stage peak (MLX) | what it was |
|---|---|---|---|
| `flux2-klein-4b-mlx-4bit` | transformer 2.2 GB, Qwen3 text encoder 2.3 GB, VAE 0.17 GB | **decode 9-12 GB** | the VAE's convolution / GroupNorm temporaries, on a 0.17 GB model |
| | | encode 3.4-3.7 GB | encoder 2.3 GB + 1.2 GB of activations |
| | | denoise 3.8 GB | transformer 2.2 GB + 1.6 GB of activations; the text encoder still resident |
| `z-image-turbo-mflux-4bit` | transformer 3.46 GB (34 blocks of 102 MB), encoder 2.3 GB, VAE 0.16 GB | encode 4.8 GB | encoder + encode activations; the lazy embeddings kept it alive into the first denoise step |
| | | denoise 5.0-5.1 GB | transformer 3.5 GB + 1.7 GB of activations - read in the same order every step: the pattern an LRU evicts worst, so on 8 GB each step re-read and re-wrote the weights (9-33 GB of swap-outs per image) |

Why Z-Image's `--low-ram` swapped so much: low-RAM was not low. It evicted the encoder *after* it was needed
and the transformer before nothing, so its working set during the loop was 5-6 GB on a Mac with ~4 GB to give,
all of it dirty (anonymous) memory, and the kernel paged it every step. The fix is a working set that fits
(streamed blocks: 1.4 GB MLX, 2.8 GB footprint in the loop), not a smaller cache.

Configuration per tier (`generation.MODELS[...].memory`, `memory_plan`):

| host memory | Klein 4-bit | Z-Image-Turbo 4-bit |
|---|---|---|
| below 12 GB (8 GB tier) | staged + streamed + bounded decode, cache cap 0.5 GB, released after every job | staged + streamed, tiled decode (as before), cache cap 0.5 GB, released after every job |
| 12-24 GB (16 GB tier) | lean: cache cap 2 GB + bounded decode, resident for the idle window (`balanced`: 600 s) | staged (as the bounded configuration), released after every job |
| 24 GB and up | the configuration it shipped with (bit-identical), resident for the idle window | the configuration it shipped with, resident for the idle window |

`loading_mode` (`auto`/`low`/`full`) overrides the tier. Packages registered here: `flux2-klein-4b-mlx-4bit`,
`z-image-turbo-mflux-4bit`. **A new image model needs a `MemoryPolicy`**: declare `low_ram_below_bytes`, a cache
cap, and (if it has the encoder/transformer/VAE shape) `streamed_components` and `weight_definition`; or set
`single_mode_reason` and name the package id in this section with the reason it has no bounded configuration.

Results, before and after, same seeds and prompts, with the table and side-by-side images:
`_orchestra/audit-20261001/image-memory/` (`index.html`, `table.md`); the probe and report scripts are
`qualification/image_memory_probe.py` and `qualification/image_memory_report.py`.

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
| Fish S2 Pro 4-bit | 14.14 (S2, footprint 14.61) | 6.2-7.7 | 7.43 (Chris) / 8.10 (Christian) | the Fish S1 DAC decoded a whole ~26 s section in one pass (+10.1 GB on the real codec, 26 s of codes); slabbed: +1.1 GB. Weights ~6 GB resident, so a 24 GB-tier model, not an 8 GB one |
| Fish S2 Pro 8-bit | 13.3 (fleet) | same bound (shared codec) | S1 9.8 | same codec and code path as the 4-bit; S1 checked (Whisper reads the sentence back exactly) |

### Adding a new MLX TTS engine
1. Add the family to `catalog.py` and `MLX_AUDIO_FAMILIES` as usual.
2. Run it through the standalone harness (`_orchestra/audit-20260930/qwen-mem/qwen_mem.py --model <name> --script S2`) with `QM_TRACE` style wrappers around its codec `decode`/`encode` to find the peak: print `mx.get_peak_memory()` before/after each stage with `mx.reset_peak_memory()` between.
3. If decode dominates: write a slabbed decode in `mlx_memory.py` (copy `_voxcpm_decode` for causal stacks, `_omnivoice_decode` for symmetric ones), pick the slab (24-32 frames) and context (>= receptive field; check that a context of 1 *fails* your equality test), cache the reference encode, add the bounder to `PROFILES`.
4. Add an exactness test in `tests/test_mlx_memory.py` with a tiny randomly initialised copy of the real classes (see the existing ones).
5. Measure the peak footprint on the 8 GB runner and set `min_free_memory_gb` **and** `min_free_memory_note`.
6. If you genuinely will not bound it, register it `cache-only` with that reason. (Fish S2 Pro 5B was `cache-only` until 2026-10-02 on the
   grounds that its weights exceed the 8 GB tier; it is now `bounded` anyway, because its codec decode was half of its 24 GB-tier peak:
   `mlx_memory._fish_decode` runs the DAC's upsample + decoder convolutions in 64-frame slabs with 16 frames of left context, measured
   identical to 9.4e-6 on the real codec, and context 8 is *not* enough at 8.5e-5. Its reference encode, +2.2-2.7 GB once per job, is cached.)
