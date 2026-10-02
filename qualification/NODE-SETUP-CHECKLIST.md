# Setting up a Mac mini node — the honest checklist

Version being deployed: the one this kit's own `focused-release-manifest.json` records (its
`version` and `implementation_commit` — a version pinned in this sentence went stale every
build), with **28** model packages (Stable Audio 3 Medium and YuE2 3B are the music models).

## What I have verified

Run these on the FIRST node, in this order. Each one has been proven on the build machine, so a
failure here means the machine differs, not that the step is wrong.

| # | Step | What "good" looks like |
|---|---|---|
| 1 | Double-click **2 - Copy the models** | It prints the Mac's memory, lists the models that fit, copies them and all 10 voices. ~2–5 minutes. |
| 2 | Double-click **1 - Install the app** | Asks for the password once. Installs to `/Applications`. |
| 3 | Open Studio Hub, press **Use the Models Already on This Mac** | The button appears only because step 1 filled `~/StudioHubModels`. |
| 4 | Allow the two pop-ups | Local network, incoming connections. |
| 5 | Status line bottom-left | **Local services ready** |
| 6 | **Settings → Model library** | Shows `/Users/<you>/StudioHubModels` |
| 7 | **Models** page | Every model the copy step listed reads as installed |
| 8 | **Audio → Clone a Voice** | The ten voices are in the dropdown |
| 9 | Generate one clip | A playable WAV. **Play** works on an MP3 voice too (try Vincent or Aiden). |
| 10 | **Subtitle**, choose the WAV just made, **Transcribe** | A transcript appears; **Open Transcript** from Jobs loads it too |
| 11 | **Image**, generate one image | An image appears |

If any step fails, stop and send me: the step number, a photo of the screen, and the output of
`sh "/Volumes/KH-STUDIO-ATLAS/Video Generation Kit/Studio Hub KH/Technical/verify-launch.sh"`.

## Before the rest of the fleet

Do NOT enrol all the machines yet. Do this first:

1. Set ONE node to **Node** role (Settings → Connections → role) and join it to a controller.
2. Send one job to that node and confirm it runs there and the controller shows it.
3. Only then repeat across the fleet.

**Fleet enrolment has never run on a second physical Mac.** Every fleet result so far is on one
machine with an empty roster. That is the single biggest unknown, and it is the thing you are about
to do — so do it once, carefully, before doing it forty-nine times.

## On every node, in macOS System Settings (owner decision, 2026-10-02)

- [ ] Turn **off** macOS automatic update *downloads* (System Settings > General > Software Update >
  Automatic Updates > "Download new updates when available" off) and turn **Time Machine off**.
  A downloaded macOS update is 8-15 GB and local Time Machine snapshots take space the Hub cannot free;
  on a 256 GB node either one alone can push it into the Hub's amber/red disk tiers. Apply macOS updates by hand, when chosen.

## Retiring, moving or selling a Mac

1. **If it is a node:** **Connections → Leave this group**. This Mac stops using the group's token
   and mints its own, and it asks the controller to drop it from the list while the old token still
   works. Check the controller no longer shows it.
2. **If it is a controller:** promote one of its nodes to **Controller** first — that mints a fresh
   token — then **Join** each remaining node to it with the new address and token, and only then
   retire the old Mac. Until the nodes re-join, dispatch to them fails.
3. **Before the Mac goes:** quit Studio Hub and delete
   `~/Library/Application Support/Studio Hub Standalone/`. It holds the credential, the job history,
   the saved voices and the outputs. Nothing else on the Mac identifies it to the group.

Rotating a controller's token is a group-wide operation, not a one-machine edit: every node has to
be told the new token once, because a node cannot take on a new credential by itself.

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

## One token per controller

There is **one token** to think about. On the controller it is under **Connections** as the **Hub
token**, and it does three jobs:

- it is the controller's own credential on that Mac;
- the **gateway** uses it to reach that controller's Hub;
- each **node** is given it when it registers, so a controller can send work to its nodes.

The owner's decision, recorded: **one token per controller, copied to each joining Mac**, rather than
a separate credential per node. That keeps signup to a single value.

**What that costs, so it is a known trade rather than a surprise:** losing the token for one
controller means re-entering it on that controller's nine or ten nodes. The five controllers are
independent, so one group's token cannot reach another group. Keep each controller's token with that
controller.

**If a node is ever promoted to controller**, it mints a fresh token on the spot, so the new group
never runs on the old group's credential. Its nodes then re-register with the new one.

### Setting up a group

1. On the controller: **Connections** → turn on the network address a node will use → restart the
   services → **Copy Fleet Token**, and note the address and port shown there (**47873** unless it
   was changed). Give a node the **Fleet token**, never the **Hub token**: the Hub token is full
   authority over the controller, and the Fleet token is the narrow credential this arrangement
   rests on.
2. On each node: turn on one of its own network addresses the same way and restart the services. A
   node must be reachable, or the controller can never send it work; the Join form refuses and says
   so if it is not.
3. Then **Connections → Join** on the node with the controller's `address:port` and the Fleet
   token. The node registers and takes that token on. The controller should then list it as reachable.
4. Nothing else to remember: a node keeps the controller's address and re-joins by itself every
   time its services start. Only if the controller's address genuinely changes - a new network, or
   a different port because 47873 was already taken on that Mac - press Join again on each node.
