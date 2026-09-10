# Voice runtime qualification

**Local Voice proof passed on 2026-09-10. Sonoma and fleet adoption remain unqualified.**

## Delivered artifact

- App: `dist/Studio Hub Voice Proof.app`, version **0.1.0**, approximately **1.7 GB allocated**.
- Implementation: `b21b263c0dc03389d5658eafe632b795e1dccca1`, built from a clean local checkout.
- App tree SHA-256: `84bf78172326698531d8ae36b574e80a7a6f3e285f357f7e5a4a9b76457abe01` (also in the adjacent `.tree-sha256` file).
- Source and runtime identity: [release manifest](voice-release-manifest.json).
- Scope: adopted Voice backend and web interface with a relocatable Python/MLX runtime and FFmpeg/FFprobe. This is a **command-line worker in an app bundle**, not the final native application window.
- This project did not modify the existing Pinokio installations, enroll or migrate nodes, or install service registration. Final read-only checks found Hub/Image unchanged and clean. Voice had advanced to `b0df180` (PRs #13/#14) during the task and was clean; that checkout was preserved. The standalone adoption remains pinned to `154e739`, so concurrent upstream work was not silently mixed into this tested artifact. See `research/source-observations-final.json`.

## Verified results

| Check | Result |
|---|---|
| Combined adopted/backend/standalone/packaging regression suite | **665 passed**, 5 upstream warnings, 77.52 seconds |
| Runtime dependency set | 136 resolved packages; 137 installed distributions including pip; 81 fewer installed distributions than the initial full candidate |
| Native compatibility audit | **284 Mach-O binaries passed** arm64, deployment target ≤14.0, library resolution, rpath and install-ID checks |
| Runtime integrity | 32,784 manifest records; zero writable runtime entries and zero `.pyc` files at finalization |
| Runtime relocation | Imports, Japanese G2P, TorchAudio resampling and file-manifest verification passed after two moves, including a path with spaces |
| App relocation | Same app started twice, including a moved path with spaces; complete recorded app digest unchanged after startup and model execution |
| Release/API checks | Health, version, release notes and packaged identity agree; catalog responds; loopback/actual port reported correctly |
| Isolation | Inherited credentials and development environment stripped; new external token has mode 0600; state and models stay outside bundle; managed Git updates rejected |
| Model inputs | Every recorded file size/hash and exact Hugging Face cache reference verified before execution |
| Qwen speech | **5.04-second WAV, 24 kHz, 241,964 bytes**; API output checksum verified |
| Whisper transcription | Expected sentence returned, with SRT/VTT output |

Final speech SHA-256: `36b937640719dc84fd2385ae0c1224eecec77302b6f15068d460ac068e9e597b`.

Transcript: “This is a standalone voice studio test. The bundled engine is speaking clearly.”

The final app took **37.294 seconds** to reach readiness on its first launch and **1.977 seconds** after relocation. Speech generation including model loading took **22.462 seconds**; generation plus transcription took **42.111 seconds**. These measurements came from a shared, memory-pressured development host with swap activity, not a clean-machine performance benchmark. MLX reported a 4.55 GB peak during Qwen generation; that is not total system/process memory. Whisper's recorded resource telemetry is in [transcription.json](transcription.json).

## Host, inputs and evidence

Host: Apple Silicon Mac16,10, 16 GiB unified memory, macOS 26.6.2 (25G83).

[Model inputs](model-inputs.json) record the exact Qwen 0.6B CustomVoice 8-bit snapshot, MLX Whisper large-v3-turbo snapshot, and required OpenAI Whisper tokenizer assets. Selected files were copied into this project's isolated `.build/pilot-models`; existing model caches were not altered. Cache reference files must contain the exact revision bytes without a trailing newline. The smoke preflight now checks this along with all file hashes.

- Durable app evidence: [voice-smoke.json](voice-smoke.json), [transcription.json](transcription.json).
- Generated sample: `../.build/final-app-smoke/generated-audio.wav`.
- Regression log: `../.build/final-regression.log`.
- App logs: `../.build/final-app-smoke/first-start.log` and `second-start.log`.
- Runtime manifests: `../.build/voice-runtime/manifest/`.
- Runtime file-manifest SHA-256: `ca0514095cc3f641a5e557407983b6f9051636af80eb66cff67da328cedd9ec6`.
- Build/audit logs: `../.build/voice-runtime-build.log` and `voice-runtime-audit-final.log`. The audit log prints the pre-move `.incomplete` path because auditing preceded final relocation. The same embedded audit JSON moved with the runtime; immutable-manifest verification passed after each move.

Independent reviews covered backend behavior, staging, smoke contracts and native packaging. Their concrete findings were resolved and verified. Initial failed development runs remain under `.build` for diagnosis; the evidence above is from the final artifact.

## Qualification still required

1. Run this exact artifact on a fresh physical **Sonoma Apple Silicon Mac**, without Pinokio, Homebrew, user Python or developer tools. A minimal process environment on macOS 26 is not a clean account or Sonoma test.
2. Repeat with physical network access disabled after complete model import. This run used Hugging Face/Transformers offline mode; physical networking remained available.
3. Qualify realistic workloads, memory requirements, audio quality, restart recovery and external SSD behavior. These short requests are functional evidence only.
4. Package Hub/Image, implement the native window/model setup, and verify authenticated GenStudio integration and mixed-fleet behavior. `managed_exact_commit` stays false; prior upstream model candidates remain withheld.
5. Complete [redistribution review](../research/licenses.md), signing/notarization and the controlled new-node pilot. Missing eSpeak loader notices and GPL/LGPL delivery obligations remain recorded release work.

The pilot excludes F5-TTS and its dedicated TorchCodec/numba/librosa dependency tree because the TorchCodec wheel requires unbundled shared FFmpeg libraries. Other catalog engines are unqualified or unavailable. The release manifest intentionally remains `unqualified-local-proof`; a successful local run does not authorize fleet admission or deployment.
