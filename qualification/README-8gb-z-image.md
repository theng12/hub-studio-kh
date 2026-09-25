# The 8 GB Z-Image-Turbo 6B MLX 4-bit run — one command

This is the last unclaimed measurement in the Image work: does **Z-Image-Turbo 6B MLX 4-bit**
complete a generation on an 8 GB M1/M2? The 16 GB tier runs the bounded low-RAM configuration by
the model's own rule (low-RAM below 24 GB), and the 32 GB numbers cannot answer 8 GB, because MLX's
buffer cache grows into whatever RAM is free, so the peak measured there is partly a property of
the host. Evidence labels: **[M]** measured, **[V]** vendor claim, **[I]** inference.

**Do this on one of the two 8 GB Macs (M1 or M2), with the kit drive plugged in.**

1. Get the script onto that Mac: AirDrop `qualification/eight-gb-z-image-run.sh` from this repo
   (or copy it off the kit drive). It needs no editing and takes no arguments.
2. Close every other app (browsers first), plug in power, and leave the Mac alone while it runs.
   The Mac may swap; that is part of what is being measured.
3. Open Terminal (Applications → Utilities → Terminal) and paste this line:

```sh
bash ~/Desktop/eight-gb-z-image-run.sh
```

That is the whole command. The script finds the 4-bit pack on its own (the kit drive,
`~/StudioHubModels`, or the HF cache), finds the engine (the mflux 0.20.0 runtime inside the
installed Studio Hub KH app first; otherwise uv/pipx, which needs network), runs four generations
and prints one block. It needs either the kit's app installed or `uv`/`pipx`.

**How long:** 20–60 minutes **[I]**, typically around 30 minutes — leave it running. Each
generation is capped at 30 minutes, and a run whose system swap passes 6 GB is stopped; both are
recorded as `ABORTED`, not as a failure. For scale, the same scenes on the 32 GiB M2 Max took
166–236 s each **[M]**; an 8 GB Mac is slower and swaps. The last run deliberately uses the
default (non-low-RAM) settings, which is expected to fail on this tier — that failure is part of
the answer, not a problem with the run.

**Send back:** the block between `COPY FROM HERE` and `TO HERE` printed at the end, verbatim. It
is also saved as `~/Desktop/StudioHub Z-Image 8GB Run/VERDICT.txt`. It carries the machine (chip,
memory), the model and quantization, engine version, resolution, steps, the memory mode chosen,
wall time, peak MLX (mflux's own report), peak footprint in bytes, swap before/after and peak, and
the decoded-image hash. Everything else (`results.tsv`, `logs/`, the PNGs) stays on the Mac.

**What the verdict means** — the script prints this line itself:

- **QUALIFIED** — low-RAM mode completed at both 1024×1024 and 1280×720; the tier passes.
- **QUALIFIED ONLY WITH 256-PIXEL TILED VAE** — the standard low-RAM run failed but
  `--vae-tile-size 256` completed; the app then needs `low_ram_vae_tile_size = 256`.
- **INCONCLUSIVE** — the script stopped the run at the swap cap or the time limit; re-run with
  `ZIMAGE_MAX_SWAP_GB=10` (or a longer `ZIMAGE_TIMEOUT`) to finish it.
- **NOT QUALIFIED** — a run ended in an out-of-memory/GPU abort: no configuration the script tried
  fits this tier. That is a real answer, and a useful one.

**It refuses rather than guess, and nothing is written when it does** (a refusal is not a result):
on a Mac that is not the 8 GB tier (any bigger host answers a different question — override only
with `ZIMAGE_ALLOW_NON_TARGET=1`, and then the block says the number is not 8 GB evidence); when
less than 3.0 GB is available at start (the model's published `min_free_memory_gb`; quit apps or
restart and re-run); when the engine is not mflux 0.20.0, the version this model was qualified on;
or when the 4-bit pack is incomplete.

Harness self-check (one 512×512 / 2-step image into a temp folder, never touches the record, no
verdict printed):

```sh
ZIMAGE_SMOKE=1 bash ~/Desktop/eight-gb-z-image-run.sh
```

Note: double-clicking a `.sh` file opens it in an editor on macOS, so use the Terminal line above.
