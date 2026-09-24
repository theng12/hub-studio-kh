# mflux 0.17.5 → 0.20.0 — upgrade findings and the true first step

Date: 2026-09-24. Scope: the `image-mflux` engine bump that Z-Image-Turbo needs. Investigation
by a read-only research sub-agent; the findings marked **[verified here]** were re-checked
against the repo by the coordinator before being recorded, because three of them change the
plan. Evidence class: **[M]** measured, **[V]** vendor claim, **[I]** inference.

Host everything was measured on: Apple M2 Max, `Mac14,13`, 32 GiB, macOS 27.0.

## Versions after the shipped 0.17.5 [M]

| Version | PyPI upload (UTC) |
| --- | --- |
| 0.17.5 (shipped) | 2026-04-10 |
| 0.18.0 | 2026-06-07 |
| 0.18.1 | 2026-08-07 |
| 0.19.0 | 2026-08-18 |
| 0.19.1 | 2026-08-26 |
| 0.19.2 | 2026-09-17 |
| **0.20.0** (newest) | 2026-09-21 |

`CHANGELOG.md` was retired in 0.19.2; release notes now live in GitHub Releases [M].

## The blocker nobody had hit yet: the runtime cannot be rebuilt offline — **[verified here]**

`packaging/image-runtime/build.sh` builds strictly offline:

- it reads `DOWNLOADS="$PROJECT_ROOT/.build/downloads"`, falling back to
  `$(dirname $(dirname $PROJECT_ROOT))/.build/downloads`, and **exits** if the pinned CPython
  archive is not there;
- it runs `uv pip sync --offline` against that same directory, so every wheel in the lock must
  already be present;
- it refuses to write into an existing runtime directory (`Refusing to replace an existing
  runtime`), so a rebuild always lands somewhere new — that part is safe by construction.

**Neither cache exists.** `.build/downloads` is absent, `Developer/.build/downloads` is absent,
`.build/uv-cache` is absent, and nothing matching either is in `_trash/`. `.build/cleanup-build.sh`
does list `downloads` and `uv-cache` among the paths it moves to `_trash`, but its target
`_trash/build-superseded-2026-09-23/` is not present either — so whatever became of them, they
are not recoverable from this repo. `packaging/voice-runtime/refresh-lock.sh` also needs
`.build/voice-runtime-base/bin/python3`, which is likewise absent, and `packaging/image-runtime/`
has no `refresh-lock.sh` of its own.

**Consequence: repopulating the downloads cache is the first step of this work, not a detail.**
It needs the network (the pinned CPython archive from `packaging/voice-runtime/pins.env` plus the
full wheel closure of the new lock). Anyone who starts by editing `requirements.in` and running
`build.sh` will get "Pinned CPython archive is unavailable" and conclude the bump is broken.

## The hard version conflict — **[verified here]**

Read from the two wheels' own `METADATA`:

| Requirement | mflux 0.17.5 | mflux 0.20.0 |
| --- | --- | --- |
| `mlx` (darwin) | `>=0.27.0,<0.32.0` | **`>=0.32.0,<0.33.0`** |
| `pyyaml` | absent | **`>=6.0,<7.0`** (new hard dep) |
| `twine` | `>=6.1.0,<7.0` | removed |

`packaging/image-runtime/constraints-sonoma.txt:3-4` pins `mlx` and `mlx-metal` at **0.31.2** by
URL + SHA-256, mirrored in `requirements.lock.txt`. That is inside 0.17.5's range and outside
0.20.0's, so the lock as written cannot resolve against mflux 0.20.0. Fix: re-pin **0.32.2**
(the 0.19.2 notes record a matmul regression in 0.32.1; macOS 14 wheels exist for 0.32.x).
`packaging/image-runtime/build.sh:44` also hard-codes the lock file's SHA-256
(`5fd5e79d…`) and refuses to build when it does not match, so the lock and that hash must move
together.

## Source compatibility is total — the risk is numeric, not structural [M]

The worker never shells out to mflux; `subprocess` in `components/image/app/backend/main.py` is
`sysctl` only. All mflux use is the Python API in `generation.py`. Every symbol it touches
survives 0.20.0 with compatible signatures: the `mflux.models.flux2…flux2_klein` module path,
`Flux2Klein`, `generate_image`, `GeneratedImage.save`. Zero of the 29 console scripts were
removed or renamed (38 now). `flux2_weight_definition.py` is **byte-identical** between the two
versions, which retires the weight-mapping risk that
[`mflux-engine-bump-impact.md`](mflux-engine-bump-impact.md) called the one thing tests could not
cover.

The FLUX.2 Klein pack the app ships (`AITRADER/FLUX2-klein-4B-mlx-4bit` @
`7fd24828501390b67a92c8b66d2fc5a707d0ba1a`) **loads and generates** on 0.20.0 through the worker's
exact code path [M].

## The pixels change — and the planned oracle could never have caught it — **[verified here]**

Same host, same pack, same seed/prompt/params, comparing SHA-256 of the **decoded** pixels:

| Engine | Decoded-pixel SHA-256 | Wall |
| --- | --- | --- |
| mflux 0.17.5 + mlx 0.31.2 (the shipped runtime) | `67a27847…` | ~55 s |
| mflux 0.20.0 + mlx 0.32.2 | `16a5535f…` | ~58 s |

`67a27847…` is the repo's own R07 baseline PNG, so the 0.17.5 harness reproduced the shipped
output exactly and 0.20.0 genuinely differs. Two 0.20.0 runs produced identical pixels, so the
new output is deterministic too.

**Coordinator's visual check [M]:** the two 1280×720 R07 images are the same picture — same
composition, same lighting, same objects. The visible differences are one extra rock on the left
bank and minor foliage detail. This is numerical drift from the engine moving, not a change in
what the model produces. It is not a quality regression on this scene.

**Caveat, and why the oracle question still matters:** the R07 scene is a landscape with no text,
so it says nothing about the text rendering that Z-Image was chosen for. And the change cannot be
attributed to mflux alone — mflux and MLX moved together, and their version bounds forbid the
cross combinations that would separate them [I].

**The oracle is broken independently of the bump.** The repo's plan
([`mflux-engine-bump-impact.md`](mflux-engine-bump-impact.md)) says to re-qualify FLUX by
comparing "PNG, dimensions, **hash** and elapsed time" against the R07 receipt. But the PNG
carries an EXIF block holding `generation_time_seconds` **and** `mflux_version` — verified by
reading the bytes of `qualification/evidence/r07-2026-09-21/flux2-klein-1280x720-seed42-r07.png`.
A whole-file hash therefore can never match, at any version, which is why the repo's own two R07
receipts already both say `"matches_historical": false`. Re-qualification must compare **decoded
pixels**, which are run-to-run deterministic [M], and must be understood as host-class specific:
the ST-03 M4/16 GB PNG has different pixels from the M2 Max PNG at the same version and seed [M].

## Z-Image-Turbo specifics [M]

Pack `filipstrand/Z-Image-Turbo-mflux-4bit` @ `b3a8f31115a11f2f9e2fa0bfbc8d78dcc3e6568b`,
5,911,138,787 bytes. **Two traps for the adapter:**

1. The pack exists only in the Hugging Face cache here
   (`~/.cache/huggingface/hub/models--filipstrand--Z-Image-Turbo-mflux-4bit/snapshots/b3a8f311…`),
   **not** under `~/StudioHubModels`, and there is no `model-manifests/z-image-turbo-*.json`
   yet. It must be imported into the library like every other package.
2. **The pack root is the model directory** — there is no `model/` subdirectory, unlike the FLUX
   convention `<pkg>/<revision>/model`. Adapter code that assumes the FLUX layout will not find
   it.

`--model` accepts a built-in alias, an HF repo id, or a **local path** (there is no
`--model-path`; non-builtin values route through `model_path`). Passing the bare alias
`--model z-image-turbo` resolves to the ~31 GB bf16 base repo, not the 4-bit pack — a 31 GB
download waiting to happen if the adapter is written from the CLI help rather than the pack path.

`--steps` defaults to **9** for this model in 0.20.0. On 0.17.5 it silently inherited FLUX.1-dev's
25 [V]. `--guidance` and `--negative-prompt` are **ignored** (CFG is disabled on this distilled
model) and the CLI warns. `--vae-tiling` and `--vae-tile-size` are **new in 0.20.0** and are the
extra memory lever the 8 GB tier may need — the 0.17.5-era record did not have them.

## Both existing test gates are blind to this

`packaging/image-runtime/smoke_imports.py` is model-free by design, and its mflux imports all
still resolve on 0.20.0 — it cannot see the change. `tests/test_image_standalone.py` installs a
**fake** mflux module via `patch.dict(sys.modules, …)`. Neither can detect a weight-loading or
numeric change. A real on-device load and generate is the only gate that can.

## Corrected sequencing

1. **Repopulate the offline caches** (`.build/downloads` with the pinned CPython archive and the
   full wheel closure; a base runtime for lock generation). *New first step — without it nothing
   below can run.*
2. Re-pin `mlx`/`mlx-metal` **0.32.2** in `constraints-sonoma.txt`, regenerate
   `requirements.lock.txt`, update the hard-coded lock hash in `build.sh:44`.
3. Build the new runtime into a **fresh** directory (never over the shipped one), run
   `smoke_imports`.
4. Load the pinned FLUX.2 pack and generate one real image; compare **decoded pixels** and
   dimensions — not a file hash — and re-check the two **owner text scenes**, because the R07
   landscape says nothing about typography.
5. Only then add the Z-Image adapter: import the pack, write the manifest, add the catalog entry,
   handle the missing `model/` prefix.

## What is still unverified

- Whether the pixel change is mflux's or MLX's — the version bounds forbid separating them.
- Whether the two outputs are *perceptually* equivalent beyond the coordinator's eyeball check;
  no SSIM or blind comparison was done.
- That a 0.20.0 runtime actually builds, passes the Mach-O/rpath audit at macOS 14.0, relocates
  cleanly and produces a valid runtime SHA-256. **Nothing has been built.**
- That the regenerated lock resolves offline from a repopulated cache.
- The Z-Image `--steps` default of 9 was read from the resolver, not observed from an omitted
  flag; the 0.17.5 default of 25 is a vendor claim from the 0.19.0 notes.
