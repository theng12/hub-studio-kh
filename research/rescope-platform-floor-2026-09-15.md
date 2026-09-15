# M1 / macOS platform-floor audit

Read-only audit on 2026-09-15 against standalone baseline `29d0baa`. No build,
download, dependency install, inference, or service start was performed.

## What is established

- The engineering candidate is Apple Silicon M1 or newer and macOS 14 Sonoma.
  `docs/standalone-plan.md` explicitly records the owner direction to support the
  original M1 without requiring the newest OS, and cites MLX's current macOS
  14.0-or-later installation requirement. The same section says M1 support, OS
  support and RAM eligibility are separate checks.
- The native shell is compiled as arm64 with `-target arm64-apple-macos14.0` in
  [native/build.sh](../native/build.sh). The staged Voice proof uses
  `LSMinimumSystemVersion: 14.0` and builds FFmpeg with `MACOSX_DEPLOYMENT_TARGET=14.0`
  ([packaging/voice-runtime/pins.env](../packaging/voice-runtime/pins.env)).
- The runtime pins explicit `macosx_14_0_arm64` MLX 0.31.2 and mlx-metal 0.31.2
  wheels in [constraints-sonoma.txt](../packaging/voice-runtime/constraints-sonoma.txt),
  with hashes. This proves an intended artifact selection for the 14.0 candidate;
  a wheel tag does not prove the Metal implementation works on every 14.x point
  release or on every M1 model.
- The native packaging tests parse and enforce a 14.0 binary floor
  (`tests/test_runtime_packaging.py`), and the recorded runtime audit reports 284
  arm64 Mach-O binaries with deployment target <=14.0. Those are artifact checks,
  not physical Sonoma execution.

No local record names macOS 14.2 as a required lower bound. The current evidence
therefore supports testing 14.0 first and recording the first physically passing
14.x point release, rather than silently raising the floor to 14.2 or claiming
14.0 is already qualified.

## Model-specific M1 / precision evidence

| Model | Existing receipt | What it does and does not prove |
| --- | --- | --- |
| FLUX.2 Klein 4B MLX 4-bit | Image audit `2026-08-02-group-a/aitrader--flux2-klein-4b-mlx-4bit.audit.json`; pinned model revision `7fd248...d0ba1`; runtime mflux 0.17.5 / MLX 0.31.2; Apple M4 16 GB observation | Passed `image.text_to_image` at 1K only. The audit has an 8 GB minimum/16 GB recommendation but no M1 or macOS 14 execution; its measured 16 GB run did not measure peak memory. |
| Qwen3-TTS 0.6B Base 8-bit | Voice audit `2026-09-10-qwen3-tts-0.6b-production-v1`; checkpoint `50f45e...36a7`; mlx-audio 0.4.6 + `d28d68...`; hardware tiers 8/16/24 GB | 8 GB is explicitly ineligible; 16 GB is minimum and 24 GB preferred. The receipt provides no M1 chip or macOS point-release result. |
| Qwen3-TTS 1.7B Base 8-bit | Voice audit `2026-08-15-qwen3-17b-production-v2`; checkpoint `e7dd05...de11c`; mlx-audio 0.4.7 + `2c9461...`; qualified runtime host Apple M2 17.18 GB | 8 GB is explicitly ineligible; 16 GB minimum and 24 GB preferred. M2 evidence cannot be transferred to M1 or macOS 14 without a physical run. |
| VoxCPM2 4-bit / 8-bit / bf16 | Current Voice catalog only | Catalog says 4-bit has a 16 GB floor and 24 GB recommendation, with an M2 17.2 GB observation; 8-bit is not locally benchmarked; bf16 has a known-not-below 16 GB floor and an “M1 Pro / M2 16 GB recommended” label, but no formal M1/bf16 receipt. Pin revisions before acceptance. |
| OmniVoice bf16 | Voice audit `2026-08-13-omnivoice-6119f707-word-safe-fades-v4`; checkpoint `6119f7...9742`; mlx-audio 0.4.7 + `2c9461...` | Explicitly says an 8 GB run was verified on Apple M1 and two other 8 GB machines. The measured 8 GB tier passed 90 seconds/2250 latent frames but failed 120 seconds/3000 frames; it leaves under 1 GB free at warning pressure. This is the only direct M1 model evidence, and it does not state macOS 14.x. The compatible precision is bf16 because the audit says the published 4/8-bit conversions use an unreadable row-wise scale layout. |
| Whisper large-v3 Turbo | Voice audit `2026-08-17-whisper-decode-conditioning` / earlier `2026-08-02-group-a`; model `a4aa...f6fb` | Apple Silicon minimum 8 GB/recommended 16 GB and measured peak 2.142 GB; SRT/VTT and segment/word timestamps are recorded. No M1 or macOS point-release result is recorded. |

The model receipts establish RAM and operation boundaries, not a universal M1
claim. The requested app must publish readiness using the exact app/runtime/model,
macOS point release, chip and RAM. It must leave unsupported combinations
unavailable before job submission and must not substitute another precision or a
CPU path to make an M1 claim pass.

## Startup and deferred-update caveat

The current packaged worker deliberately disables Git/launchd updates: the
standalone updater reports app replacement as the update mechanism. Existing
Voice source updater code has idle/defer states, but that code is not a valid
standalone update path. The product direction requires work to continue while an
update is deferred or downloading; implementation should therefore use Hub-owned
pause/drain and per-worker readiness, with no mid-job binary replacement or claim
that generation survives an OS/update restart.

The current native shell auto-starts its single Voice worker after a saved model
library is present, but `qualification/native-launch.json` records
`start_at_login_enabled: false`, and `qualification/native-redesign.md` says no
login registration was performed. Automatic startup for the multi-worker app is
still an implementation and physical login/reboot qualification item.

## Required physical qualification

1. Test the final immutable app/runtime on an original M1 at the oldest candidate
   Sonoma 14.x point release, then on a newer supported macOS release. Record the
   exact point release, Metal behavior, model/precision, RAM, swap, output and
   duration for every advertised model tier.
2. Include M1-specific cases for FLUX 4-bit, Qwen Base only at eligible RAM,
   OmniVoice bf16's 90-second/120-second boundary, each pinned VoxCPM2 tier, and
   the selected Whisper choices. Do not use current M2/M4 receipts as substitutes.
3. Verify pause/drain before OS restart and post-login readiness without duplicate
   jobs or model downloads. Keep the last qualified app and external model package
   available when a replacement needs a newer OS.

The existing plan/tasks already reserve these checks in ST-01.OS, ST-02.OS,
ST-08.OS, ST-09.OS and ST-11.OS; this audit confirms they remain open conditions.
