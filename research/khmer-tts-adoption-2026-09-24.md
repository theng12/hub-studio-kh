# Khmer VITS adoption — implementation record (ST-12)

Date: 2026-09-24. Owner request: adopt the two surveyed Khmer TTS models into Voice
Studio offerings and the model library. Two families, per the owner's ownership
decision; offline kit distribution on all four drives.

Companion to [khmer-tts-new-releases-2026-09-24.md](khmer-tts-new-releases-2026-09-24.md).

## What was built

Both models are non-MLX, so each got its own engine rather than a catalog-only row.

| Family | Model | Engine | Sample rate | Weights |
|---|---|---|---|---|
| `khmer-tts-mms` | `khmerttsopensource/khmer-tts` @ `c9380a9…` | transformers `VitsModel` on torch (CPU) | 16 kHz | 332 MB |
| `khmer-tts-onnx` | `sengtha/khmer-tts-female-v2` @ `c6fde511…` | ONNX Runtime, `khmer_tts_v3.onnx` | 22.05 kHz | 115 MB |

Both are single-speaker and Khmer-only; neither supports cloning. Sectioning uses
the existing `long_form_policy` conservative default (280 chars), inherited for free.

### Files changed

- `components/voice/app/backend/catalog.py` — two `Family` entries, two `ModelEntry` rows.
- `components/voice/app/backend/generation.py` — engine requirements, `_WIRED_FAMILIES`,
  `_KHMER_VITS_FAMILIES`, dispatch branches, two workers, cache slots, memory tracking.
- `components/voice/app/requirements-generation.txt` — `onnxruntime>=1.20`.
- `packaging/capture_model_manifests.py` — per-choice `exclude` filter; **tree pagination fix**
  (the Hub `/tree` endpoint returns 50 entries by default and silently truncated the MMS
  repo to 2 files; it now follows the `Link` header). This was a latent bug for any repo
  with more than 50 files.
- `components/model_packages.py`, `components/hub/standalone.py` — new packaging engine
  `voice-vits` mapped to the Voice worker/modality.
- `model-manifests/khmer-tts-mms.json`, `model-manifests/khmer-tts-female-onnx.json`.
- `tests/test_model_manifest_catalog.py`, `components/voice/app/tests/test_khmer_vits_families.py`.

## Verification (measured on this Mac)

Runtime `.build/voice-runtime` (Python 3.12.14, torch 2.14.0, transformers 5.14.1,
onnxruntime 1.30.0). Models resolved through the installed-package library path
(`~/StudioHubModels`, `files_verified`).

| Test | Result |
|---|---|
| `khmer-tts-mms`, 46-char Khmer sentence | 16 kHz, **2.88 s**, 92,204 B, wall 7.0 s (cold load), peak RSS +727 MB |
| `khmer-tts-onnx`, same sentence | 22.05 kHz, **3.82 s**, 168,492 B, wall 1.05 s, peak RSS +331 MB |
| Both, 576-char text through the **full parent→native-child path** | 3 sections each, joined cleanly: MMS 31.28 s @16 kHz / wall 9.7 s; ONNX 42.36 s @22.05 kHz / wall 8.6 s |
| Catalog serialization | correct family label, sample rate, `km`, `apple_optimized=False` |
| Model package verify | `files_verified` for both, locally and on all four drives |

Tests: Voice suite **716 passed / 2 skipped**; root suite **659 passed / 11 skipped /
1 failed** — the single failure is the documented pre-existing, order-dependent
`test_subtitle_standalone.py::test_a_small_mac_is_warned_about_not_refused`, which passes
alone and is unrelated to this work. New manifest + engine tests pass (10 + 8).

## Distribution state

- Mac library `~/StudioHubModels`: both packages installed and registered.
- All four kits (`Video Generation Kit/Studio Hub KH/model-library/`): both packages
  copied and registered; each library now has **37** entries.
- The `examples/inference.py` file from the MMS repo is included (1 KB) and the
  sengtha training checkpoint (`best_model.pth`, 998 MB) is excluded.
- **Release 0.6.55 cut locally**: staged at `.build/StudioHub-0655`, installed to
  `/Applications`, DMG at `dist/StudioHub-0.6.55-3ab8984.dmg` (verified by mounting), and all
  four drive kits refreshed with the new app bundle + `focused-release-manifest.json`. Local
  commit `3ab8984`. **Nothing pushed.**

## Not done / open

- **Not pushed** to `main` or `implementation/voice-runtime` — the owner runs the two pushes.
- **Khmer quality was accepted by ear by the owner (2026-09-24):** both voices judged good, and
  all four generated clips (short + long-form for each) accepted. Clips are under
  `.build/khmer-adoption/audio/`. Whisper cannot score Khmer, so this ear judgement — not the
  passing tests — is the quality evidence.
- Independent review not performed.
- The capture-tool pagination fix means any *other* manifest captured from a >50-file repo may
  have been silently truncated in the past. Only the two new packages were recaptured; the
  existing 35 were not re-verified against a full tree in this session.
