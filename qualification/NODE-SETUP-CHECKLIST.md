# Setting up a Mac mini node — the honest checklist

Version being deployed: **0.5.0, build `79ded7f2`**, `development_build: false`.

## What I have verified

Run these on the FIRST node, in this order. Each one has been proven on the build machine, so a
failure here means the machine differs, not that the step is wrong.

| # | Step | What "good" looks like |
|---|---|---|
| 1 | Double-click **2 - Copy the models** | It prints the Mac's memory, lists the models that fit, copies them and all 10 voices. ~2–5 minutes. |
| 2 | Double-click **1 - Install the app** | Asks for the password once. Installs to `/Applications`. |
| 3 | Open Studio Hub, press **Use the Models Already on This Mac** | The button appears only because step 1 filled `~/Downloads/model-library`. |
| 4 | Allow the two pop-ups | Local network, incoming connections. |
| 5 | Status line bottom-left | **Local services ready** |
| 6 | **Settings → Model library** | Shows `/Users/<you>/Downloads/model-library` |
| 7 | **Models** page | Every model the copy step listed reads as installed |
| 8 | **Audio → Clone a Voice** | The ten voices are in the dropdown |
| 9 | Generate one clip | A playable WAV. **Play** works on an MP3 voice too (try Vincent or Aiden). |
| 10 | **Subtitle**, choose the WAV just made, **Transcribe** | A transcript appears; **Open Transcript** from Jobs loads it too |
| 11 | **Image**, generate one image | An image appears |

If any step fails, stop and send me: the step number, a photo of the screen, and the output of
`sh "/Volumes/KH-STUDIO-ATLAS/Studio Hub App/Technical/check-everything.sh"`.

## Before the rest of the fleet

Do NOT enrol all the machines yet. Do this first:

1. Set ONE node to **Node** role (Settings → Connections → role) and join it to a controller.
2. Send one job to that node and confirm it runs there and the controller shows it.
3. Only then repeat across the fleet.

**Fleet enrolment has never run on a second physical Mac.** Every fleet result so far is on one
machine with an empty roster. That is the single biggest unknown, and it is the thing you are about
to do — so do it once, carefully, before doing it forty-nine times.

## What is not proven, said plainly

- **A second Mac has never joined a fleet.** See above.
- **Inference quality is not measured.** Qwen cloning, Whisper, FLUX and OmniVoice all produce real
  output, but nobody has judged whether that output is *good* across the models. Listen and look.
- **VoxCPM2 has never produced output on any host.** It needs a 24/32 GB Mac; the small nodes will
  not have it installed.
- **The 1.7B Qwen tiers on a 16 GB Mac are tight.** The 0.6B tiers are the safe default; a 1.7B
  model may swap. If a node struggles, pick a 0.6B model.
- **Nothing is signed or notarised.** First open needs right-click → Open. Expected, and fine.
- **No human has clicked most of this UI.** I verified the engine, the API and the bundled app's own
  launch; the windows themselves have only been seen by you.

## If something goes wrong

The app is a folder and the models are a folder, so nothing is hard to undo:

- Replace the app: drag a fresh `Studio Hub.app` into Applications over the old one.
- Point the app at a different library: **Settings → Model library → Choose Folder…**
- Models are only ever added, never deleted, except an older revision of a model just updated.
