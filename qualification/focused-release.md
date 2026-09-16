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

## Running more than one Mac

A Mac is `Standalone`, a `Controller` or a `Node`, chosen in **Connections → Fleet**. Only a
controller takes customer work; a node offers its Studios to the controller it joined.

Joining is two fields: the controller's address and the fleet token it shows under
**Connections → Local API → Fleet token**. A node reports the address it will answer on itself —
loopback-only Macs refuse to join, because a controller could never reach them — and it adopts
the controller's fleet credential as part of joining, so one new machine needs nothing else
pre-configured.

**Connections → Fleet overview** is the one place that answers "how is my fleet doing": one row
per Mac with reachability, app version, chip, memory, free disk, completed jobs and the average
seconds measured from real runs. **Send here** pins new Image, Voice and Subtitle work to that
Mac until it is cleared; **Automatic** (the default) lets the Hub choose.

Replacing a controller means promoting a node to `Controller` in its own Settings and pointing
the other machines at its address and token. Nothing needs to be migrated: an artifact lives on
the Mac that produced it and is pulled through whichever controller asked for it, and a
controller's job history is disposable.

**Settings → General → Start services at login** is off by default. With it on, signing in starts
the Hub and its Studios, so a node is ready for work without anyone opening the app.

Run the fleet path against the staged bundle — two packaged Hubs, a join, a real Subtitle job,
a second job pinned to the node with `machine:node-a`, a pin naming a Mac that is not there, and
the artifact pulled back through the controller:

```sh
.build/voice-runtime/bin/python3 qualification/evidence/fleet_packaged.py
```

It writes `qualification/evidence/fleet-packaged.json` and exits non-zero when the job does not
run on the node, when the pin is not honoured or is not refused, when the artifact does not come
back through the controller, or when the fleet overview does not report both Macs with the node's
measured work.

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
