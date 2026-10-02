# MLX memory discipline

Rules every Studio that runs an MLX model follows, learned from measurements on an 8 GB Mac. The fleet is ~35
8 GB machines, and an MLX worker there fails in one of two ways: its **hot working set** (what it touches every
step) exceeds what the OS can give it, so it swaps and thrashes; or its **peak** (usually one stage) pushes the
Mac into critical memory pressure. Both are visible in a 32 GB Mac's numbers if you measure per stage, with MLX's
cache capped, and look at the process **footprint** and not only MLX's own peak.

`tests/test_image_memory_profile_guard.py` fails if an image model is added without a memory profile (or a
documented exemption in this file); the Voice worker's `components/voice/app/backend/mlx_memory.py` is the same
discipline for TTS.

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

See `components/voice/app/backend/mlx_memory.py` (cache cap, slabbed codec decode with left context, per-stage
evaluation): the same rules 1, 2 and 4 applied to the TTS codecs - peaks of 7-9 GB brought to ~3.1-3.3 GB with
identical audio. This section is maintained by the Voice work.
