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

A second probe proves the part a node actually is — a Hub serving a real network address:

```sh
.build/voice-runtime/bin/python3 qualification/evidence/fleet_network.py
```

It binds the node to `127.0.0.1` plus this Mac's own LAN or Tailscale address, then checks that
the node still answers on loopback, that its readiness record keeps this Mac's own app on
loopback, that its resources are published over the network, that it adopts the controller's
credential, and that a Subtitle job runs on the network node and returns its artifact. Receipt:
`qualification/evidence/fleet-network.json`. It reports why it cannot run on a Mac with no
non-loopback address rather than passing vacuously.

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

### If macOS refuses to open the app

The app is unsigned by decision, so Gatekeeper blocks the first launch. Two remedies, in order:

1. **Right-click the app → Open → Open.** This is the normal path and is all most Macs need.
2. If the app was copied through iCloud Drive, AirDrop, Mail or a downloaded archive, macOS adds
   a quarantine flag that can survive right-click → Open. Clear it once, then open again:

   ```sh
   xattr -cr "/Applications/Studio Hub 0.4.0.app"
   ```

   `-c` clears every extended attribute, `-r` does it through the whole bundle. Point it at the
   copy you actually installed — clearing attributes on the copy you are still holding on the
   SSD does not help the one on the Mac. Then right-click → Open once more.

   Until the app is signed, do this on each Mac rather than assuming the last one's result
   carries over; quarantine is recorded per machine, per copy.

### When you turn a Mac into a fleet node

Two macOS prompts are normal the first time, and neither is a defect:

- **"Studio Hub would like to find devices on your local network"** — grant it. Without local
  network access the Mac can listen but other Macs on the LAN may not reach it. It appears the
  first time this Mac is bound to something other than loopback.
- **A firewall prompt about accepting incoming connections** — choose *Allow*. A node that
  denies incoming connections is invisible to its controller even though it looks like it
  started cleanly.

Prefer the tailnet for a fleet that is not all on one trusted LAN: a Tailscale address is
accepted by the same field the LAN address goes in, needs no firewall exception, and does not
depend on which desk a Mac is sitting at. Loopback-only Macs refuse to join at all, with that
reason stated in the join form, because a controller could never reach them.

### What each Mac can run

Every model runs on any Apple Silicon Mac. There is **no per-model RAM gate**: the published
figures (FLUX 16 GB, Whisper 8 GB, Qwen Base 8 GB, larger tiers 16–32 GB) are the tiers each
model is *qualified* at, and a Mac below them shows a warning beside the model instead of being
refused. That is not a guess — FLUX.2 Klein has produced tens of thousands of images on the
owner's 8 GB Macs.

| Memory | What to expect |
| --- | --- |
| 8 GB | Everything runs. Use one Studio at a time; a large Voice tier will swap and be slow. |
| 16 GB | Everything runs, comfortably for Image and Subtitle. |
| 24 GB | Everything runs, including the larger Voice tiers. |
| 32 GB | Everything runs, with the most headroom. |

The only memory gate left is **live**: a job waits if less than ~2 GB is free at that moment,
and starts as soon as memory is released. That is deliberate. "Your Mac has 8 GB" is a fact the
operator already knows; "your Mac has 0.4 GB free right now because three Studios are holding
models" is a fact about this moment, and starting a generation into it is what turns a slow job
into a killed worker. If a job is waiting on memory, release a Studio from
**Settings → Model memory** or from the fleet overview, and it proceeds.

## Models and what they need

The only requirements here are inputs. A memory figure, where shown, is the tier the model is
qualified at — a Mac below it warns and runs anyway.

| Studio | Model | Required input | Qualified at |
| --- | --- | --- | --- |
| Image | FLUX.2 Klein 4B MLX 4-bit | A prompt | 16 GB |
| Subtitle | Whisper large-v3-turbo, small, base | An audio or video file | 8 GB |
| Voice | Qwen3-TTS 12Hz 0.6B Base | Reference clip **and** its exact transcript | 8 GB |
| Voice | Qwen3-TTS 12Hz 1.7B Base | Reference clip **and** its exact transcript | 16 GB |
| Voice | Qwen3-TTS CustomVoice | A preset speaker, no reference | 8 GB |
| Voice | OmniVoice bfloat16 | Reference clip | 16 GB |
| Voice | VoxCPM2 4-bit / 8-bit / bf16 | Reference clip | 24 GB |

A clone **fails closed** on a transcript mismatch — that is an input contract, not a memory
gate, and it stays. A reference clip must contain roughly 5 seconds or more of usable speech;
shorter clips are refused by a guard rather than producing a degraded voice.

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
