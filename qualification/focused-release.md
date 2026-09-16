# Focused release — offline install kit

Status: kit buildable and verified on the build machine; per-machine acceptance is an owner
step (see [ST-11](tasks/ST-11.md) checkpoints 11.3–11.6).

This is the operator-facing description of what is handed to a Mac, how to install it, and
what has to be true before it can be called qualified.

## What ships

| Item | Notes |
| --- | --- |
| `Studio Hub 0.4.0.app` | The app: native shell + bundled Hub + Voice, Image and Subtitle workers. Unsigned. |
| `model-library/` | The verified model packages, with `.studiohub/installed.json` recording the exact revision and file digest of each. |
| `focused-release-manifest.json` | Generated receipt: app payload hash, per-component runtime identity, every advertised package and its installed revision. |

The kit is **unsigned and unnotarized by decision** (owner, 2026-09-15): the distribution
method is "copy the app over and open it with right-click → Open". macOS blocks a plain
double-click the first time; right-click → Open then confirms once.

Generate or refresh the manifest with:

```sh
.build/voice-runtime/bin/python3 packaging/release_kit.py \
  --app 'dist/Studio Hub 0.4.0.app' \
  --library model-library \
  --output qualification/focused-release-manifest.json
```

It exits non-zero if the app advertises a package that the library does not contain at the
pinned revision, so a kit cannot be described as complete when it is not.

## Install

1. Copy the app and the model library folder to the target Mac. Keep `model-library/` outside
   the app bundle — the app refuses a library inside itself.
2. Right-click the app → **Open** → **Open**.
3. When the app asks where the models live, choose the copied `model-library` folder. The
   choice is remembered per machine.
4. Wait for the status line to read **Local services ready**. Four processes come up: the Hub
   and the Voice, Image and Subtitle workers.
5. Open **Settings → Model memory** and confirm each Studio reports a state. A Studio reading
   *Needs an update* or *Not running* is a real problem, not a cosmetic one.

## Models and what they need

| Studio | Model | Requirement |
| --- | --- | --- |
| Image | FLUX.2 Klein 4B MLX 4-bit | 16 GB unified memory |
| Subtitle | Whisper large-v3-turbo, small, base | 8 GB |
| Voice | Qwen3-TTS 12Hz 0.6B Base | Reference clip **and** its exact transcript |
| Voice | Qwen3-TTS 12Hz 1.7B Base | Reference clip **and** its exact transcript |
| Voice | Qwen3-TTS CustomVoice | Preset speakers, no reference |
| Voice | OmniVoice bfloat16 | Reference clip |
| Voice | VoxCPM2 4-bit / 8-bit / bf16 | Fails with its own memory message below ~24 GB free |

A clone **fails closed** on a transcript mismatch. A reference clip must contain roughly 5
seconds or more of usable speech; shorter clips are refused by a guard rather than producing a
degraded voice.

## Measuring a machine

Run one generation per model on each Mac and the numbers record themselves:

```sh
.build/voice-runtime/bin/python3 qualification/evidence/measure_machine.py \
  --app 'dist/Studio Hub 0.4.0.app' --library /path/on/that/mac/model-library \
  --label 'Mac mini M4 16GB' \
  --reference /path/to/reference.wav --transcript 'exact words in the clip' \
  --out qualification/evidence/machine-macmini-m4-16gb.json
```

It starts the bundle's own Hub and workers, submits one job per model over the real Hub API,
and writes elapsed time, output bytes and SHA-256, peak process RSS, the reference digest and
the machine's swap state. Add `--kinds image,subtitle` to omit the voice run. Without
`--reference` and `--transcript` the voice run is skipped with the reason recorded, never
guessed.

## What is proven, and what is not

Proven on the build machine (16 GiB M4 Mac mini):

- The app stages, starts and serves. Real jobs completed through it for Image, Subtitle and
  Voice cloning, each recorded in `qualification/evidence/`.
- Every advertised package is present in the shipped library at its pinned revision.
- The full regression set passes against this source (see [st11.md](st11.md)).

Not proven, and deliberately not claimed:

- Real inference on any other Mac. RAM tiers, disk sizes and macOS versions are unverified
  until someone runs the measurement above on that machine.
- An original M1 on Sonoma. The candidate floor is macOS 14.0 and the Image studio refuses
  anything older, but neither has been exercised on that hardware.
- Login items, reboot recovery and startup approval have automated coverage only.
- VoxCPM2 producing output at all: every attempt on this 16 GiB host has failed with the
  model's own memory message.

Signing, notarization and publication are **not** part of this kit and require separate
authority. Licences and attribution for the shipped models and adopted sources are recorded in
[research/licenses.md](../research/licenses.md).
