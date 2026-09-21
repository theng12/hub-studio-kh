# Music engines: Stable Audio 3 (MLX) and the ACE-Step MLX DiT

Owner request (2026-09-22): keep **both** music models — the existing ACE-Step 1.5
and a new **Stable Audio 3.0** — and switch ACE-Step's DiT/VAE from PyTorch MPS to
the upstream MLX implementation. MiniMax Music 3 is explicitly parked.

## Why these two

| | ACE-Step 1.5 (shipped) | Stable Audio 3.0 Medium (new) |
|---|---|---|
| Released | 2026-02 (v1 2025) | 2026-05 |
| Length | up to 600 s (10 min) | up to 380 s (6 min 20 s) |
| Vocals | yes (lyrics + language) | no — instrumental/SFX |
| Licence | MIT | Stability AI Community |
| Runtime | LM on MLX, DiT/VAE on PyTorch MPS | pure MLX (`optimized/mlx`) |
| Weights | bundled package | `stabilityai/stable-audio-3-optimized` (ungated) |

ACE-Step stays the long-form and vocal engine; Stable Audio is the second,
faster instrumental engine.

## What was measured / found

- ACE-Step warm timing, one resident engine, 30 s clips: cold **106.25 s**, warm
  **32.93 s**, warm **42.76 s**. The cold load is ~65-70 s; the app keeps the
  engine resident. 6-minute instrumental: 360.0 s of audio in 256 s cold.
- The ACE-Step LM planner already runs on **MLX**
  (`STUDIOHUB_MUSIC_BACKEND` defaults to `mlx`); the 2B DiT/VAE run on MPS.
  Upstream ships an MLX DiT/VAE at `acestep/models/mlx/` which our pinned package
  did not adopt.
- Stable Audio 3 ships a **pure-MLX** implementation at `optimized/mlx` (no
  PyTorch/transformers at runtime; deps are `mlx`, `numpy`, `sentencepiece`,
  `huggingface_hub`, `soundfile`). `medium` (1.4 B, SAME-L codec) runs on Apple
  Silicon: upstream reports ~5 s for 10 s and ~48 s for 380 s on an M4 Pro at
  ~5 GB peak RAM. Output is 44.1 kHz 16-bit stereo trimmed to `--seconds`.
- This Mac: **M2 Max / 32 GB**, 211 GB free, `uv` 0.12.17 present.

## Plan

### Phase 1 — local proof (no app changes)
1. `optimized/mlx/install.sh -y --download medium` into `.build/sa3-source`
   (project-local `.venv`, ~6.7 GB weights from `stable-audio-3-optimized`).
2. Generate a short instrumental and a 380 s one; record wall time, peak RAM,
   output duration/hash. Confirm it is genuinely torch-free.

Gate: the proof produces valid audio within RAM and a time the app can queue.

### Phase 2 — adopt Stable Audio as a second Music engine
1. Pin the upstream commit and vendor the MLX runtime into a new engine package
   (`components/music` engine family `stable-audio-3`, or a sibling adapter).
2. Add `model-manifests/stable-audio-3-medium.json` with exact revisions and
   per-file SHA-256, matching the existing `import|scan|verify|download` contract.
3. Teach the Music worker to dispatch by engine: ACE-Step vs Stable Audio, each
   with its own params (`duration_seconds`, `steps`, `cfg`; no lyrics/vocals for
   SA3). Keep the digest-locked runtime overlay discipline.
4. Packaging: extend `packaging/stage_voice.py` to bundle the SA3 MLX runtime and
   weights; keep `release-manifest.json` fields.
5. Native UI: the model picker already reads the catalogue, so an SA3 Music entry
   appears automatically; hide the Lyrics/vocal fields when the selected model is
   instrumental-only.

### Phase 3 — ACE-Step DiT/VAE on MLX: already true (corrected)
Verified 2026-09-22 from the engine's own log during a real generation:
`[MLX-DiT] Native MLX DiT decoder initialized successfully`,
`[MLX-VAE] Native MLX VAE initialized (dtype=mlx.core.float32, compiled=True)`,
and the LM `MLX model loaded successfully in 2.17s`. The engine's
`initialize_service` defaults `use_mlx_dit=True`, so on macOS (`device="mps"`) the
DiT and VAE *compute* already run on native MLX; only the **checkpoint load** goes
through PyTorch (the orchestrator always loads a PyTorch copy first, which is where
the meta-tensor error and the ~57 s cold load come from).

So there is no port to do. The only remaining optimisation is to skip that
redundant PyTorch load on Apple Silicon, which is an upstream engine change behind
the digest-locked overlay and only saves cold-start time (the app keeps the engine
resident, so warm jobs already run at ~33-43 s per 30 s clip). Reclassified as a
small, optional follow-up rather than a phase.

### Phase 4 — qualification
Real generations (short, 6-min instrumental, vocal), cancel/restart, ACK, and
history after restart for both engines; keep receipts under
`qualification/evidence/`.

## Gates
- Network download of the SA3 weights (owner authorized by requesting the model).
- The Stability Community Licence governs SA3 use (commercial per
  <https://stability.ai/license>); ACE-Step remains MIT.
- Signing/deployment/enrollment unchanged and still requires separate authority.
