# Standalone six-function source inventory

Date: 2026-09-10. This is a read-only inventory for the requested native
SwiftUI/AppKit shell with one WKWebView surface and six UI functions: Audio,
Transcription, Image, Music, Video, and Chat.

The inspection covered tracked files only: each repository's `README.md`,
`VERSION`, `pinokio.json`, requirements/lock files, and the current backend
modules named below. No source checkout, environment, cache, log, credential,
service, or model data was changed or used as an integration test. The existing
Graphify snapshot is Voice-only and stale; it was not used to infer absence.

## Current source identities

All six sibling repositories are on `main`, report `## main...origin/main`, and
have empty porcelain status (clean, including untracked files). `VERSION` is
the source version at the inspected commit.

| Component | Repository | Current commit | VERSION | Recorded planning baseline |
|---|---|---|---|---|
| Hub | `~/pinokio/api/studiohub-mac` | `2b6b089a4ebfdc4dccd2330761f25fe6628dbe80` | 2.21.4 | `e74ff728654d4c2ac6707b1655b1382451c30d6e` / 2.21.3 |
| Image | `~/pinokio/api/imagestudio-mac` | `207e788ff3b9bb9b173ffe773691f571b567b79f` | 1.32.1 | same commit / 1.32.1 |
| Voice | `~/pinokio/api/voicestudio-mac.git` | `b0df1802cef64d150c6155c514bc0572c7cc5010` | 2.7.4 | `154e7390fcee942ca6bbbcd326fcf9027b37404d` / 2.7.2 |
| Music | `~/pinokio/api/musicstudio-mac` | `7679e8968e47ba091956883154fb95a44b503041` | 1.9.1 | not recorded |
| Video | `~/pinokio/api/videostudio-mac` | `790dd191d2d9dd4f9e28eeb140bc9d4e9fa95627` | 0.11.1 | not recorded |
| Chat | `~/pinokio/api/chatstudio-mac.git` | `2eb7950132994cfabec542593658167094b3cf5e` | 1.25.3 | not recorded |

The source identity file at
`~/Projects/studiohub-standalone/research/source-checkouts.json`
therefore needs to remain a historical adoption record, not a statement of the
current sibling revisions.

## Requested function mapping

| UI function | Existing source and engine | What is supported in tracked code | Readiness status |
|---|---|---|---|
| **Audio** | Voice Studio, `~/pinokio/api/voicestudio-mac.git/app/backend/generation.py` | Local TTS, preset voices, VoiceDesign and reference-audio cloning. The wired families list is MLX `mlx-audio` for Qwen3-TTS, VoxCPM, Kokoro, Bark, Chatterbox-MLX, Spark-MLX, Orpheus, VibeVoice, Voxtral, Marvis, OmniVoice, Fish Audio and LongCat, plus an F5-TTS worker; see lines 8-28. | **Known source wiring; runtime qualification is separate.** The sibling declares Darwin/arm64 and its generation lock pins MLX/MLX-audio, but this inventory does not certify every family/model. |
| **Transcription** | Voice Studio, `~/pinokio/api/voicestudio-mac.git/app/backend/transcription.py` | Whisper MLX is the recommended path; Moonshine and Nemotron candidates are also registered. The worker calls `mlx_audio.stt.utils.load_model` on a local snapshot, attaches an OpenAI Whisper tokenizer when the MLX snapshot is weights-only, and returns text, segments, SRT and VTT; see lines 57-120 and 693-931. | **Known source wiring; model/package pair must be requalified.** Offline use needs the complete Whisper weights plus the companion tokenizer. |
| **Image** | Image Studio, `~/pinokio/api/imagestudio-mac` | `mflux`/MLX for most FLUX, Qwen-Image, FIBO and SeedVR2 families; a second PyTorch/MPS + Diffusers engine for Sana, PixArt Sigma, Lumina 2, AuraFlow and SDXL. Source dispatch and per-engine requirements are in `app/backend/generation.py` lines 129-157 and 1296-1412. | **Known source wiring; no standalone runtime proof here.** The lock currently pins `mflux==0.17.5`, `mlx==0.31.2`, `mlx-metal==0.31.2`, Diffusers and Torch, while the generation input file intentionally uses floors. |
| **Music** | Music Studio, `~/pinokio/api/musicstudio-mac` | PyTorch/MPS inference only: Transformers `MusicgenForConditionalGeneration` for MusicGen, Diffusers `StableAudioPipeline` for Stable Audio Open, and Transformers `BarkModel` for expressive/vocal audio; see `README.md` lines 22-44 and `app/backend/generation.py` lines 5-17, 531-553, 734-757. | **Source implementation exists; Apple-native MLX is explicitly absent.** `README.md` lines 22-26 says there is no native MLX equivalent. Any single-runtime plan must retain a separate Torch/MPS lane or accept a future port. |
| **Video** | Video Studio, `~/pinokio/api/videostudio-mac` | Lance 3B Video uses native MLX for text-to-video; LTX, Wan 2.2, HunyuanVideo and CogVideoX use Torch/MPS + Diffusers. The source supports text-to-video, image-to-video and CogVideoX video-to-video; see `app/backend/video.py` lines 128-172 and 384-445, plus `app/backend/main.py` lines 704-824. | **Known source wiring; high-risk runtime qualification.** The source warns that local video is heavy/slow and gives 16 GB/24 GB Lance modes, but that is a product declaration, not proof for the packaged app. |
| **Chat** | Chat Studio, `~/pinokio/api/chatstudio-mac.git` | MLX `mlx-lm` text generation, optional `mlx-vlm` vision-language path, streaming `/api/chat/completions`, and OpenAI-compatible `/v1` endpoints. `app/backend/llm_engine.py` lines 116-177 and 240-449 show the import probes and single MLX worker; `app/backend/main.py` lines 4-16 and 242-264 expose the API/capabilities. | **Known source wiring; runtime and memory qualification are unverified here.** The lock pins MLX 0.31.2, mlx-metal 0.31.2, mlx-lm 0.31.3 and mlx-vlm 0.6.4. |

Audio and Transcription can share the Voice process and its global MLX lock;
the source explicitly serializes STT and TTS to avoid two MLX models spiking
unified memory (`transcription.py` lines 21-25, 43-52, 830-867). Music and Video
are separate heavy-work lanes, with their own process/model caches. A native
shell may present one six-tab product while retaining isolated worker processes
and dependency trees.

## Apple Silicon and MLX evidence

All five model-hosting siblings declare `"platform": ["darwin"]` and
`"arch": ["arm64"]` in their tracked `pinokio.json`: Image, Voice, Music,
Video, and Chat. Hub declares Darwin but does not host models. These declarations
establish the intended target only; they do not prove that a relocatable runtime,
model snapshot, native extension, or requested macOS version works.

- **Image:** lock evidence exists for `mflux==0.17.5`, MLX 0.31.2 and
  `mlx-metal==0.31.2`; Diffusers/Torch is also required for part of the catalog.
- **Voice/Transcription:** the current lock contains MLX 0.31.2, MLX-LM 0.31.3,
  MLX-Metal 0.31.2 and an older `mlx-audio` commit (`d28d68c...`), while the
  tracked generation input requests `mlx-audio@2c9461f...` and Transformers
  5.14.1. This source/lock drift must be resolved in the standalone lock.
- **Video:** the generation input pins `lance-mlx` at
  `f8ecc5a86d28b94bd8dc688c200c3e22ffafff4a`, with Torch/Diffusers as the other
  lane. The lock additionally contains MLX, MLX-Video and MLX-VLM commits.
- **Chat:** the generation input and lock provide the cleanest MLX-only lane:
  MLX/Metal 0.31.2, MLX-LM 0.31.3 and optional MLX-VLM 0.6.4.
- **Music:** there is no MLX evidence in the source. Its tracked README says
  PyTorch + MPS is the current implementation and deliberately avoids
  `audiocraft` because of its old Torch pin; requirements use Torch,
  Transformers, Diffusers, Accelerate, SoundFile and NumPy.

The separate standalone project already has a local Voice proof, but it was
built from the historical Voice baseline and has only been tested on macOS 26;
Sonoma compatibility remains unqualified. It should be treated as evidence for
the Voice packaging method, not as certification of this six-function app or of
the current sibling commit.

## Native dependencies and integration blockers

**Music.** The current engine depends on Torch/MPS, Transformers model classes,
Diffusers for Stable Audio, Accelerate, SoundFile/libsndfile, and NumPy. The
source deliberately excludes `audiocraft` (`app/requirements-generation.txt`
lines 1-15). This creates a separate Python/native dependency lane from the
MLX-based Audio, Transcription and Chat paths. A single app must isolate that
lane and re-check Torch/MPS memory release, model switching, audio export and
licenses; sharing the MLX runtime is unsupported by current source.

**Video.** The native Lance path depends on `lance-mlx`; the other paths require
Torch/MPS + Diffusers + Transformers. Export/input handling adds ImageIO,
`imageio-ffmpeg`, OpenCV headless, Pillow, NumPy and Numba (`app/requirements-
generation.txt` lines 10-44). The catalog includes 13B/14B-class workloads and
the source itself limits low-memory Lance output to short clips. The standalone
app needs a measured memory policy and isolated media tool/runtime packaging;
no current source evidence proves that all model families or even the selected
Sonoma target can run from one packaged artifact.

**Chat.** Chat has a usable local MLX API and a single-thread MLX manager, but it
is currently a separately managed background service. Its README says the API
binds `0.0.0.0:47871` and accepts any non-empty API key for local OpenAI-
compatible use. The standalone shell must convert this to an app-owned loopback
worker and preserve remote auth, session/history storage, model downloads and
single-model memory limits. `mlx-vlm` is optional and missing it disables vision
models while text chat still works (`llm_engine.py` lines 139-177).

**Hub/GenStudio integration.** Current Hub source is intentionally production-
scoped to Image and Voice: `registry.py` lines 204-220 defines
`TRACKED_MODALITIES = ("image", "voice")`, and `main.py` line 301 repeats that
boundary. The Hub README says legacy Music, Chat, Video and Render registrations
are ignored (`README.md` lines 1-6). The capability contract already names
`audio.transcription`, `chat.completion`, `music.generation`, and
`video.generation` (`CAPABILITY_CONTRACT.md` lines 132-160), but that is a
contract vocabulary, not current production routing. Extending the native app
to six functions therefore requires an additive Hub registry/admission,
capacity/lease, authentication, exact runtime identity, model exposure and
maintenance design. Existing startup service specs still assume five separate
fixed ports (Music 47869, Voice 47870, Chat 47871, Video 47872, Hub 47873),
which must not be copied into the app as an external multi-service topology.

## Unverified items to carry into the plan

1. Clean-machine execution on macOS 14 Sonoma and a newer OS for each selected
   engine/model; declarations and wheel metadata are compatibility targets only.
2. The already-resolved standalone Voice lock (137 installed distributions,
   136 resolved in the existing proof) must remain relocatable without Pinokio,
   Conda, Homebrew, developer tools, or writes inside the app bundle; each
   newly added engine needs its own separately qualified lock rather than one
   shared all-engine environment.
3. Model package completeness, revisions, licenses and offline behavior for
   Audio, Transcription, Image, Music, Video and Chat; especially Whisper's
   companion tokenizer and Video's large media pipelines.
4. Measured unified-memory ceilings and scheduling when a six-function app has
   concurrent requests. Current siblings mostly enforce one heavy model per
   process, not six simultaneous GPU workloads.
5. Native shell process lifecycle, crash/restart semantics, loopback binding,
   external writable state, model-library relocation and WKWebView routing.
6. Additive GenStudio/Hub representation for one packaged node exposing six
   operations, including truthful runtime revision/hashes and safe mixed-fleet
   maintenance. Existing Hub sources do not provide that representation today.
7. Whether “Video” includes the separate Render Studio source. The requested
   six-function list maps cleanly to Video Studio generation; Render Studio is a
   seventh, separate checkout and is not included in this inventory mapping.

No existing Pinokio repository was modified. The only intended write from this
task is this report in the standalone project.
