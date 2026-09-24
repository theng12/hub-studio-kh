# Text-rendering image model shortlist (Apple Silicon)

Date: 2026-09-22. Goal: pick an image model that renders **text/typography inside the
image** well, that runs on the owner's Macs — primarily the **M4 16 GB Mac mini**.
Research note, not a qualification. All sizes are from Hugging Face metadata or the
model/mflux cards; peak-memory figures are labelled by their source.

Owner machines: see the canonical fleet in [`research/owner-hardware.md`](owner-hardware.md)
(8 GB M1/M2; 16 GB M1/M2/M4; 24 GB M4; 32 GB M2 Max (Mac14,13)).

## Shortlist

| Model | Params | Text-rendering strength | Quantized footprint | Fits 16 GB? |
| --- | --- | --- | --- | --- |
| **Z-Image-Turbo** | 6B | Card: "excels at accurately rendering complex Chinese and English text"; explicitly rated to "fit comfortably within 16G VRAM" | mflux 4-bit pack **5.91 GB** on disk | **Yes, comfortably** |
| **Boogu-Image-0.1-Base** | 10B | Card: strong **dense** text (posters, documents, brand guides, UI), bilingual; use at 2K, 25–50 steps | q4–q8 (large repo) | Yes at q4–q8 (verify) |
| **Boogu-Image-0.1-Turbo** | 10B | Same family, 4 steps; "solid" text but photography-leaning | q4–q8 | Yes at q4–q8 (verify) |
| **ERNIE-Image / Turbo** | 8B | Baidu, Chinese-text oriented | mflux card: **q4 ≈ 6.2 GB**, q8 ≈ 12 GB | Yes at q4 |
| **FIBO** | 8B | JSON-native with an explicit `text_render` field (control, not just accuracy); SmolLM3-3B encoder | `mflux-community/fibo-mflux-q4…` | Yes at q4–q8 |
| **Ideogram 4** | 9B | **Most typography-focused**; JSON captions with per-text bounding boxes | fp8 26 GB; **q4 ≈ 14 GB disk, 22.0 GB peak RSS** (M5 Pro/64 GB) | **No** — needs 32 GB+ |
| **Qwen-Image-2.1** | 7B+8B TE | Best-in-class overall text (LongText-Bench leader class) | mflux: 17.5 GB bf16 encoder floor; mlx-serve 4-bit pack 10.7 GB | Only via mlx-serve (9.55 GB peak measured on 32 GB M1 Pro) |

## Ranked pick for the M4 16 GB mini

1. **Z-Image-Turbo 6B — start here.** It is the only model whose own card claims a 16 GB
   fit *and* names bilingual text rendering as a headline strength. The mflux
   pre-quantized 4-bit pack is 5.91 GB and generation is 8–9 steps, so it is also the
   fastest on an M4.
2. **Boogu-Image-0.1-Base 10B — when the job is dense text** (posters, documents,
   brand guides). Its card recommends the **Base** variant at 2K for dense layout
   fidelity; Turbo is the fast/photographic one.
3. **ERNIE-Image-Turbo 8B** — a Chinese-text alternative; q4 is ~6.2 GB.
4. **FIBO 8B** — when you want *control* over where text goes (`text_render` field), not
   just accuracy.

On the bigger machines:
- **M4 24 GB / 32 GB M2 Max (Mac14,13):** Ideogram 4 is the pure-typography specialist, but its
  measured 22 GB peak means 32 GB+ is the comfortable tier — treat 24 GB as tight.
- **Any of them:** Qwen-Image-2.1 is the best text renderer of the group, but on Apple
  Silicon it needs either a 32 GB+ mflux machine or the mlx-serve 4-bit route.

## Integration note

Studio Hub's `image-mflux` engine is pinned to **mflux 0.17.5** (`packaging/image-runtime/`).
Z-Image, Boogu, ERNIE-Image, FIBO and Ideogram 4 are all in **mflux 0.20.0**, so adding
any of them shares one prerequisite: the engine bump plus the per-model adapter work
described in `research/qwen-image-2-1-adoption.md` §5. Pre-quantized packs exist for
several under `mflux-community/*` and `filipstrand/Z-Image-Turbo-mflux-4bit`.

## Caveats

- "Fits 16 GB" for Z-Image is the model card's own claim; the mflux 4-bit pack size
  (5.91 GB) makes it plausible, but no 16 GB Mac measurement was found. Verify locally.
- Boogu/ERNIE 16 GB fits are inferred from parameter count and quantization level, not
  from a published peak on 16 GB.
- Quantization changes typography: Ideogram's own note measures 65.6% of subpixels
  differing at q4 vs fp8 on a typographic caption ("displacement rather than damage").
  Expect low-bit text to move glyphs, not just soften them.

## First measured result — 2026-09-24

FLUX.2 Klein 4-bit (the model the fleet ships today), run locally on the 32 GB
M2 Max (Mac14,13) with mflux 0.20.0, against the two owner scenes:

| Scene | Resolution / steps | Wall | Peak | Text result |
| --- | --- | --- | --- | --- |
| Sleep-story cover, title "GOLDEN SLUMBER" | 1280×720 / 4 | 57.2 s | 2.37 GB | **correct** |
| Storybook cover, title + subtitle | 1024×1024 / 4 | 73.3 s | 3.28 GB | title correct; subtitle asked "A Bedtime Story", rendered **"A Bediime Story"** |

Evidence class: **[M]** measured on the owner's 32 GB M2 Max (Mac14,13).

Conclusion: the shipped model renders a large title cleanly but drops characters in
smaller secondary text. That single misspelling is the concrete failure the
candidate models are being tested against, and it is exactly the kind of error a
low-bit quant makes worse. Outputs and the review README live in
`~/Desktop/StudioHub Image Text Review`. This is a **baseline measurement, not a
qualification** of any candidate.

### Z-Image-Turbo 4-bit — same two scenes, 2026-09-24 [M]

| Scene | Resolution / steps | Peak MLX | Wall | Text result |
| --- | --- | --- | --- | --- |
| Sleep-story cover, "GOLDEN SLUMBER" | 1280×720 / 9 | 9.84 GB | — | **correct** |
| Storybook cover, title + subtitle | 1024×1024 / 9 | 10.70 GB | — | title correct; subtitle **"A Bedtime Story" correct** |

This is the decisive result: Z-Image-Turbo renders the exact secondary text FLUX.2
Klein misspelled ("A Bediime Story"). It also peaks ~1.3–1.7 GB *lower* than FLUX in
the same engine. Matches the owner's online observation that Z-Image's text quality
far exceeds FLUX.2 Klein.
