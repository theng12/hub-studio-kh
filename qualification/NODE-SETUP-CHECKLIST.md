# Setting up a Mac mini node — the honest checklist

Version being deployed: the one this kit's own `focused-release-manifest.json` records (its
`version` and `implementation_commit` — a version pinned in this sentence went stale every
build), with **40** model packages in the source catalog (Stable Audio 3 Medium and YuE2 3B are the music models).
Normal setup copies **40 production-allowed packages** (about 177.0 GB). No package is withheld pending qualification any more (owner, 2026-10-27: "We no longer block or lock any model."); the Mac's own memory is what decides which of them it copies and runs. Qwen Image 2.1 Turbo MLX 4-bit is production-eligible on 16 GB and larger Macs; eligibility does not claim an existing SSD or Mac already has its weights, which remains a kit-receipt fact.

## What I have verified

Run these on the FIRST node, in this order. Each one has been proven on the build machine, so a
failure here means the machine differs, not that the step is wrong.

| # | Step | What "good" looks like |
|---|---|---|
| 1 | Double-click **1 - Install the app** | Asks for the password once. Installs to `/Applications`. |
| 2 | Open Studio Hub, then **Set up this Mac** | It shows this Mac's memory and the free space on the models' drive first, then the four presets by size. Pick the one for this Mac and copy from the drive - its card reads "9 of 14" and rises as files arrive, and only says installed when every file is verified. ~2–5 minutes. (**`2 - Copy the models`** no longer chooses: it prints what the drive holds and stops. Use **`2 - Copy the models (everything)`** only on a Mac with no internet and no other Mac to copy from.) |
| 3 | **Models** page | Every model the preset copied reads as installed |
| 4 | Allow the two pop-ups | Local network, incoming connections. |
| 5 | Status line bottom-left | **Local services ready** |
| 6 | **Settings → Model library** | Shows `/Users/<you>/StudioHubModels` |
| 7 | **Audio → Clone a Voice** | The ten voices are in the dropdown |
| 8 | Generate one clip | A playable WAV. **Play** works on an MP3 voice too (try Vincent or Aiden). |
| 9 | **Subtitle**, choose the WAV just made, **Transcribe** | A transcript appears; **Open Transcript** from Jobs loads it too |
| 10 | **Image**, generate one image | An image appears |

Yam Display installs automatically with **1 - Install the app** (or run `Extras/Install Yam Display.command`). It is the virtual display a headless Mac mini needs.

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

## Shops: cashier Macs in Lite Mode (owner, 2026-10-03)

A shop's cashier iMac (8 GB; Chrome POS, Telegram, PDF viewer, Printer Studio KH) lends the fleet its
idle time and never its user's. **Set a work Mac up like any other Mac** - install, copy models, join
as usual (or leave it standalone, or make it a controller) - **then tick Lite Mode** in Studio Hub >
Settings. That is the only extra step, and Lite Mode works the same on every role (owner, 2026-10-04):
every generation job the Mac runs itself is governed, whether a controller sent it, it is the Mac's own
queue, or it is a controller's own share of its group's work (a controller standing by keeps routing
and sends its group's work to its nodes; a standalone Mac's work just waits).

**Lite Mode Mac: set it up like any Mac and take the Essentials 8 GB preset on the app's "Set up this
Mac" screen, then tick Lite Mode in Settings.** The kit has no Lite Mode copy step of its own (the
last one was retired and its launcher deleted, 2026-10-30); the 8 GB preset carries every model Lite
Mode runs. Lite Mode still refuses a job for any other model at its door, so the runtime allowlist in
`components/workstation_models.py` is what makes a shop Mac a shop Mac - not an install set.

| # | Where | Step | What "good" looks like |
|---|---|---|---|
| 1 | the always-on Mac mini | The usual install (app, Yam Display, models, services started), like any Mac; tick **Lite Mode** in Studio Hub > Settings if it is also a work Mac | The usual "good" for an update. It is a regular controller: it also generates. |
| 2 | the Mac mini | Studio Hub > **Connections**: role **Controller**, Tailscale address on, restart services, **Copy Fleet Token** | The Gateway's Fleet page shows the group with this Mac **Working**. |
| 3 | each cashier Mac | The usual install (**1 - Install the app**), then the **Essentials 8 GB** preset on **Set up this Mac**, then tick **Lite Mode** in Studio Hub > Settings | "Lite Mode: ON"; the 8 GB preset carries every Lite Mode model (FLUX.2 Klein 4B 4-bit, Z-Image-Turbo 4-bit, Qwen3-TTS 0.6B 4-bit, VoxCPM2 4-bit, Whisper large-v3 turbo, Stable Audio 3 Medium), and anything else it copies simply waits unused. |
| 4 | each cashier Mac | Studio Hub > **Connections**: Tailscale address on, restart services, **Join** with the controller's address and Fleet token | The Mac appears under the Shops group as **Working**. |
| 5 | the cashier Mac, or the Gateway | Studio Hub > **Settings > Lite Mode** on the Mac itself, or Fleet > the Mac's **…** menu on the Gateway | Only when idle (default off, 3 min), Step aside when someone uses it (default off, resume after 2 min), free memory needed to start a job (default: each model's own, ~2.8 GB), quiet hours, hide the menu-bar icon. |

What Lite Mode does: every worker runs at macOS background priority (lowest CPU band, throttled
disk, low-priority GPU); only the six models above are accepted (others are refused by name and go to
another Mac); no pop-ups or desktop alerts from Studio Hub. **A job it starts always finishes** - step
aside lets the current job finish, then unloads the model and takes no new work until the Mac has been
left alone for the resume time. The only interruption is macOS reporting **critical** memory pressure
for 20 seconds (rare); the job then moves to another Mac at once. Staff should know the Mac may be busy
in the background; heat and fan noise are expected.

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
