# Studio Hub Standalone

## Current product direction — 2026-09-15

One native Apple Silicon app with Hub management, Image Studio, Voice Studio and
Subtitle Studio. It works locally without GatewayKH and can be configured as a
location controller or joined worker. Optional authenticated GatewayKH connectivity,
including protected token reveal/copy, belongs in the focused release.

The bounded catalog is FLUX.2 Klein 4B MLX 4-bit; Qwen3 TTS Base 0.6B/1.7B;
VoxCPM2 4-bit/8-bit/full; OmniVoice full; and three Whisper choices.
Music is the accepted extension tracked in the current implementation plan;
Chat and Video remain deferred. Exact GatewayKH compatibility is a qualification
task, not implied by replacing the inherited GenStudio name (owner correction,
2026-09-21).

The owner values editable defaults. Device mode, studio enablement, model choices,
generation parameters, startup, storage paths and limits, labels and connections are
configurable through shared settings flows. Generated-output cleanup defaults to
20 GB across all studios on each device and 3 days after generation, whichever
limit is reached first; cleaned-up work goes to a self-emptying Trash Folder (15 days). Models, user originals and protected work are excluded.

Distribute tested immutable runtimes with the app and complete model packages
separately, optionally in one offline SSD kit. No target-device dependency resolution
or Git updates. Native UI, local and remote jobs share Hub's admission and recovery.
Existing Pinokio devices retain their original flow.

Support original M1 devices without requiring the latest macOS. Sonoma 14 is the
current MLX/app minimum candidate, pending physical per-model qualification. Retain
compatible runtimes and existing working app releases while OS upgrades are deferred;
do not promise generation on pre-Sonoma systems or through an update restart.

Install complete model packages by downloading from configured GitHub/original-host
sources or copying folders into the chosen model library. Both paths verify weights
and required companion assets before use; executable dependencies remain bundled.
Keep address/IP, port and token connections independent of GatewayKH's deployment,
so the client can use the API without shared state directories.

## Existing implementation

Version 0.3.0 is a native Voice preview with Qwen CustomVoice and Whisper Turbo proof.
It is not yet the narrowed release and does not yet bundle Hub or Image. Preserve its
tested lifecycle, private data paths, relocation evidence and immutable artifacts.

See [canonical design](docs/standalone-plan.md), [implementation plan](docs/implementation-plan.md)
and [agent task board](docs/tasks/BOARD.md). Historical artifacts remain under qualification/.
