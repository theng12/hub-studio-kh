# Voice runtime qualification

Status: local feasibility work in progress. This is not approval for fleet adoption or Sonoma compatibility.

## Scope

This milestone adopts Voice 2.7.2 at the exact commit in `research/voice-adoption.json`, adapts its writable paths and release identity, and packages a private runtime with native media tools. The proof is a headless `.app` structure launched from the command line. The native application window, Hub/Image packaging, GenStudio contract integration, signing/notarization and node enrollment are not implemented by this milestone.

## Host and models

- Build/test host: Apple Silicon Mac16,10, 16 GiB unified memory, macOS 26.6.2 (25G83).
- Binary compatibility target: macOS 14.0; runtime audit and physical qualification are distinct checks.
- Imported pilot inputs: exact Qwen 0.6B CustomVoice 8-bit and MLX Whisper large-v3-turbo snapshots, plus the required OpenAI Whisper tokenizer assets. See `model-inputs.json` for all revisions, bytes and file SHA-256 values.
- Models were copied from existing caches into this project's isolated `.build/pilot-models`; existing caches were not altered. No installed node credentials, jobs or environment were adopted.

## Evidence available

- Five root bundle tests pass: external/missing model paths, clean launch environment, complete payload hashing, tracked-only resource adoption, unsafe runtime symlinks and refusal to overwrite an existing artifact.
- Eight standalone backend tests pass: external writable paths/tools, unavailable managed updates, service supervision disabled, cached release identity, accurate loopback metadata, prior candidate qualification withheld while limits remain enforced.
- Real base-server proof at `.build/server-verified-smoke/report.json`: two launches of the same artifact, including a moved path with spaces; health/catalog/connectivity/update checks; external private token creation; no bundle mutation. This base-only run did not execute a model.
- FFmpeg and FFprobe 9.0.1 built from the pinned source archive. Both run; arm64 and macOS 14.0 deployment target verified. FFmpeg links only to Apple system libraries. A generated 0.1-second WAV was probed successfully.
- Full adopted backend regression: **637 passed, 2 skipped**, with runtime bytes unchanged across the run. Log: `.build/backend-full-tests-fixed.log`.
- Preliminary actual model proof from the relocated development artifact: Qwen produced a 5.04-second 24 kHz WAV (241,964 bytes); API download SHA-256 `36b937640719dc84fd2385ae0c1224eecec77302b6f15068d460ac068e9e597b`. Whisper returned “This is a standalone voice studio test. The bundled engine is speaking clearly.” Generation including load took 31.598 seconds; generation plus transcription took 55.207 seconds on this shared development host. MLX reported 4.55 GB peak memory during Qwen generation; this is not total machine/process peak memory. Log/report: `.build/model-retry2/`.
- That preliminary model artifact predates the final dependency narrowing and is not the Sonoma candidate. Repeat both models on the final immutable artifact before recording local completion.
- The narrowed Qwen/Whisper pilot excludes the F5-TTS dependency tree because its TorchCodec loaders require unbundled shared FFmpeg libraries. Other Voice engines remain unqualified or unavailable. The resulting 284 native binaries passed arm64, macOS 14 minimum and library-resolution checks; final build-prefix cleanup/manifest/relocation remains in progress.

## Acceptance work still required

1. Complete full-runtime audits, relocation and actual model execution on the current newer-OS host.
2. Repeat the same artifact on a fresh physical Sonoma Apple Silicon Mac with no Pinokio, Homebrew, user Python or developer tools. A minimal process environment on this development machine is not a fresh account or clean OS installation.
3. Repeat with physical network access disabled after complete model import. Hugging Face offline mode alone does not prove every library avoids the network.
4. Record workload memory, latency and audio quality under realistic fleet contention; a short synthetic request is a functional check only.
5. Complete Hub/Image and authenticated GenStudio/mixed-fleet integration before exposing any packaged runtime as eligible. `managed_exact_commit` remains false and inherited model candidates are withheld.
6. Resolve redistribution obligations, then qualify signed/quarantined app launch, model setup, replacement and rollback for a controlled pilot.

No source checkout, live service registration or fleet node is intentionally changed by this proof. Build inputs, generated runtimes and model copies remain local to the separate project.
