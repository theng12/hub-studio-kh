# Hub Studio KH — what it is

Hub Studio KH is the owner's local AI work machine: a native macOS app that runs
generation studios on Apple Silicon and serves their results to apps and people on
the owner's network. It is used from its own window or menu bar, without a cloud
account.

## What it does

- Runs a local Hub service with authenticated jobs, model
  import/download/verification and editable device policy.
- Image Studio generates pictures with FLUX.2 Klein 4B MLX 4-bit.
- Voice Studio offers Qwen3 TTS Base and CustomVoice, OmniVoice and VoxCPM2.
  VoxCPM2 is exposed but needs more unified memory than a 16 GB Mac has with other
  engines resident; it reports that failure explicitly.
- Subtitle Studio transcribes with Whisper large-v3-turbo, small or base and
  exports Text, SRT or VTT. Word timestamps are not exposed because there is no
  word-level viewer.
- Keeps a verified model library in Application Support or on an external SSD. A
  missing library is reported, not silently replaced or redownloaded.
- Works as a location controller or a joined worker: fleet identity, token
  rotation, per-machine stats and a scoped local API for GatewayKH.
- Cleans generated output on editable defaults: 20 GB per device across studios or
  7 days after generation, with a Trash Folder and a 5 GB free-disk floor.
- Starts its services at login and keeps them available from the menu bar when the
  window closes; Quit handles active work first.
- Music's backend and engine contract are integrated, but there is no native Music
  workflow and no real-model qualification. Chat and Video remain deferred; the
  README says Video's Jobs tab and clips remain.

## How it is built

SwiftUI/AppKit shell (`native/`), Python Hub and studio engines (`components/`,
including `hub`, `image`, `voice` and `subtitle`), packaging and gate scripts
(`packaging/`, `scripts/`) and Python tests (`tests/`). Model manifests and voice
data live under `model-manifests/`, `model-presets/` and `voices/`.

Build the native artifact with:

    bash native/build.sh

The README's current build note then stages a Voice runtime with
`packaging/stage_voice.py`.

## Where it fits

It runs standalone and offline with a local model library, and it can be a location
controller or joined worker for GatewayKH. GatewayKH reaches it by address, port and
a fleet token; the administrative Hub token is a separate scope used only on this
Mac. Hub owns local jobs, their admission and their status; it does not bill or
route cloud work.

## Current version

0.6.255, from the root `VERSION` file; the newest `CHANGELOG.md` entry agrees. The
README's "0.6.38" is an old observed installed version, not the source version.
