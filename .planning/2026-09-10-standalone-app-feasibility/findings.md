# Findings

## Evidence already established
- Voice SDK fix is merged as 2.7.2; it handles native-build SDK mismatch, not general packaging compatibility.
- MLX 0.31.2 and mlx-metal 0.31.2 publish separate macOS 14/15/26 arm64 wheels. Current installed wheels target 26; packaging must explicitly select the intended target builds.
- MLX documents macOS 14+ Apple Silicon support; full model/engine qualification on 14 remains unproven.
- Existing SSD bootstrap installs generation environments; it is not a standalone relocatable application.
- GenStudio consumes authenticated Hub API and immutable release/runtime identities. Git/Pinokio maintenance assumptions require explicit packaged-runtime adaptation.
- Existing Graphify graph at /build/pinokio/graphify-out/graph.json has 747 nodes, 1669 edges and 27 source-file entries, covering Voice backend only. It is a navigation aid, not current proof of runtime reachability. No Hub graph was found.

## Sources
https://ml-explore.github.io/mlx/build/html/install.html
https://pypi.org/pypi/mlx/0.31.2/json
https://pypi.org/pypi/mlx-metal/0.31.2/json
https://conda.github.io/conda-pack/
https://pyinstaller.org/en/stable/usage.html

## Source inspection
Voice auto_update.py fixes interpreter to conda_env/bin/python, resolves Pinokio home and invokes pterm; main.py has source-relative writable paths and Pinokio UI port. Wrapping existing folders is insufficient; standalone-owned source adapters must separate app resources, node data, process lifecycle and package updates. Missing native_execution.py search path was harmless; discovered module names will be used instead.

## Final planning conclusions
- Source-linked independent Hub inventory is saved in research/hub-contract-inventory.md; primary review confirmed its release identity and role constraints directly.
- Fixed upstream repository identities and Git-derived runtime hashes are a concrete packaged release integration issue. Manual pilot updates cannot be called full managed-release support.
- Thin app shell, isolated runtimes, external writable state/model storage, and Voice-first qualification are proposed. No app implementation or hardware qualification occurred.
- First scope defaults to Hub + Image + Voice after an optional scope question received no reply during drafting; owner can revise it.
- Apple direct-distribution and SMAppService documentation support the proposed later distribution/login workflow; no signing submission or login service registration occurred.
- Missing guessed child CAPABILITY_CONTRACT.md files were resolved through actual Hub contract/child README paths; no mutation or lost work.

## Implementation findings
- Voice runtime uses managed FFmpeg and FFprobe; portability build must include both.
- Multiprocessing native execution uses spawn; launcher uses bundled interpreter with isolated Python startup and explicit backend path, retaining child-process design.
- App entry validates external model storage before creation and rejects writable paths under its own bundle.
- Source-only adoption includes upstream audit reference evidence; adapted catalog must not advertise it as fresh standalone qualification.

Runtime inventory found existing source/lock drift: requirements-generation.txt selects mlx-audio 2c9461f and transformers 5.14.1, but the upstream lock records d28d68c and transformers 5.9.0. Standalone will resolve its own exact lock from the selected source requirements; neither old file is changed. Builder chose pinned Python-build-standalone CPython 3.12.14+20260901 with archive SHA verification and a base-first runtime build. FFmpeg/FFprobe target macOS 14 on the build host.

Read-only model inventory found reusable complete cached pilot snapshots: Qwen CustomVoice0.6B8bit at049ef77fe8816b536193c0c25f9a214d17921282 (~1.97GB,14files) and Whisper large-v3-turbo at a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb (~1.61GB,4files), no broken links. No model copy or execution yet; later proof can import only these complete weights/config packages into isolated test storage, without copying old credentials/state.

Whisper MLX snapshot is weights-only. Source _attach_processor uses companion OpenAI tokenizer; imported its cached revision41f01f3fe87f28c78e2fbf8b568835947dd65ed9 (7tokenizer files, no duplicate OpenAI model weights) into isolated HF model library and recorded hashes. This is required offline input data, not a Python dependency or new paid/API route.

## Khmer TTS stock — 2026-09-14

Owner requested a personal-use local copy. The pinned VITS model package is recorded in research/khmer-tts-stock.json and kept under model-library/khmer-tts/c9380a9d1c817f2b21980d54f29f7a61b0d626f5/. Preserve this folder when cleaning build artifacts. Weights and companion files are checksum-verified; inference quality, Mac compatibility and native Audio integration remain untested. Latest owner direction is personal use only.

## Narrow release planning — 2026-09-15 (supersedes six-function release scope)

- Owner confirmed editable per-device output retention defaults: 50 GB total generated files and 30 days after generation, whichever limit is reached first. Model libraries and app/runtime bytes are outside this generated-output budget.
- Current artifact is still 0.3.0. Source baseline29d0baa retains its independently verified native shell, immutable bundled Voice runtime, external state and Qwen CustomVoice/Whisper proof. NativeWorkspaceStore explicitly defaults to Qwen0.6B CustomVoice, which is not the newly requested Base model. Existing success does not establish Base/VoxCPM2/OmniVoice support.
- `packaging/stage_voice.py` currently stages only Voice and its frontend/audits; native StudioController owns one Voice worker. There is no packaged Hub/Image or standalone Subtitle service yet.
- `packaging/voice-runtime/requirements-pilot.txt` still inherits broad non-F5 Voice dependencies. The new bounded runtime should prune only after tracing the requested models and testing their required functions; no runtime package is removed during planning.
- `SMAppService.mainApp` register/unregister and menu-bar lifecycle are implemented in native/StudioHub.swift. Actual login/reboot and physical Sonoma qualification remain missing. Automatic login startup is distinct from pre-login/headless boot operation.
- Build inputs already pin CPython archive hash, FFmpeg source hash, model input hashes and final runtime payload. Preserve this foundation and retain build artifacts instead of resolving dependencies separately on each device.
- Official Apple SMAppService HTML exposes a Markdown link that the web tool cannot fetch (unsupported content type); existing source and recorded docs provide lifecycle evidence. Developer ID guidance rechecked on2026-09-15; signing/notarization remains distribution work, not a claim of current qualification.

Final design review: managed output-root changes retain cleanup ownership; only explicit exports outside it are excluded. Separate software startup/connection implementation from physical release qualification. Permit the named native pilot checkpoint to consume reviewed partial model receipts without claiming full catalog completion. Eleven task cards now define evidence, ownership and next-session handoff; ST-01 is the first available implementation task.

## Original M1 and older macOS requirement

Owner wants oldest practical OS support, retaining operation while an upgrade is deferred. Official MLX install documentation checked 2026-09-15 specifies Apple Silicon and macOS >=14.0: https://ml-explore.github.io/mlx/build/html/install.html . Existing constraints-sonoma.txt explicitly selects MLX/mlx-metal 0.31.2 macosx_14_0_arm64 wheels; pins.env and native/build.sh target14.0. Existing qualification is on macOS26, not physical14.0. Therefore14.0 is the candidate baseline, with actual per-model/precision/point-release proof required. Do not promise pre-Sonoma support, newest-OS-only upgrades, or generation through a restart. M1, OS and RAM eligibility remain separate.
