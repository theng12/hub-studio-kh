# SSD refresh handoff — 2026-09-14

Owner direction: preserve this flow; research I2V now. The owner clicked VoxCPM2 8-bit Download in Voice Studio and will announce completion. Do not poll or monitor that download. Do not run generation while current machines are busy.

## Current SSD

- Volume: `/Volumes/KH-STUDIO-UGREEN`.
- Canonical source: `~/pinokio/api/studiohub-mac/ssd_bootstrap`.
- Hub source release 2.21.6, merged PR https://github.com/theng12/studiohub-mac/pull/21, merge commit ef973838a0087b36c40280bca8b69b389815655a.
- Current stock: Khmer TTS added and weight checksum verified; OmniVoice already intact. VoxCPM2 8-bit pending owner-confirmed download completion.
- Last refresh: 31 Voice packages, 6 Image packages, 10 fleet voice references; 124.2 decimal GB indexed. All 27 release inventory checks passed. No inference or fleet restart.
- Installer manifest now pins the official Pinokio 8.2.0 ARM64 DMG, SHA256 41f5a20d98331ca7395a179b808c04be5e4493f62f405ed0d132336271168388. Bundle identity, signing team, signature and Gatekeeper passed.

## Resume only after owner announces completion

1. Reconfirm the mounted SSD identity and read its AGENTS.md and START HERE handoff. Verify source is current and preserve unrelated work.
2. Check the complete VoxCPM2 8-bit snapshot then run the canonical `studio_models.py stage --root <volume>/studio-models --plan`. No inference.
3. Stage complete local models through the same tool. Preserve SSD packages absent from the current source Mac; do not start Image Studio just to refresh its stock.
4. Run `tools/sync_ssd_bootstrap.py --volume <volume>` followed by `--check`; verify `RELEASE-INVENTORY.sha256` from the kit directory. Verify the newly copied model files and revision.
5. Finish this SSD first. Owner will bring other SSDs one by one. Inspect each separately; ensure pinned installers exist before sync. Keep personal files, credentials and unrelated data outside refresh scope.

## Setup meaning

New Mac flow: Stage 1 Install Mac Apps → Pinokio Install Tools → Stage 2 Install Studios → Stage 3 Manage AI Models (normal suitable-model restore). Stages 4–6 are repair/update paths described in the canonical guide, not mandatory fresh-install steps.

The current kit downloads Studio code and dependencies in Stage 2; SSD stock avoids model-weight downloads and is not the future fully bundled standalone app. Normal 8 GB Voice restore remains Qwen3-TTS 0.6B Base + Whisper large-v3-turbo + Kokoro. Stocking Khmer TTS/OmniVoice does not silently add them to that allowlist. VoxCPM2 8-bit retains a 16 GB floor. No new restore-default decision has been received.
