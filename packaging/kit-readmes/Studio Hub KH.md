# Studio Hub KH — the Macs that run the models

**Version 0.6.90** (build `20d1795`).

This folder is the whole Studio Hub kit: the app, the model library, your saved voices,
and the two installers. Everything here is meant to be copied to each Mac as-is.

## What it is for

Studio Hub runs the actual AI models on a Mac — images, voice, subtitles and music — and
hands the results to GatewayKH. Every Mac that generates anything runs this app.

A Mac running Studio Hub is one of three things, chosen in **Connections → Role**:

- **Controller** — owns a site: its own Mac plus every Mac that joins it. GatewayKH talks
  to controllers only, never to nodes. This Mac also generates.
- **Node** — a Mac that has joined a controller. It is sent work by the controller. Nodes
  never talk to GatewayKH.
- **Standalone** — a Mac on its own, not part of a group.

Work is placed by memory: **voice and music** go to the Macs with the most memory,
while **images and subtitles** prefer the smaller ones, so a big Mac stays free for audio.
If the preferred Mac is busy the job goes to another; nothing waits.

## Install on a Mac

Two double-clicks, then open it. `START-HERE.md` has the long version with pictures of
what you should see.

1. **`2 - Copy the models.command`** — reads how much memory this Mac has and copies only
   the models it can run, plus your saved voices. Give it several minutes the first time.
   It is safe to run again: it adds what is missing and leaves the rest alone.
   **A Lite Mode Mac:** set it up like any Mac; use **`2 - Copy the Lite Mode models.command`**
   instead of this one if you want only the Lite models (about 15 GB); then tick **Lite Mode**
   in Studio Hub > Settings.
2. **`1 - Install the app.command`** — puts the app in **Applications**. It will ask for
   your password once. It closes and replaces any older copy for you, then **opens the app
   in fleet mode** (`--fleet-mode`): the Mac starts Studio Hub's services now, at every login
   and after every restart, with nobody at the keyboard - so its controller can always reach
   it. This sets **Settings → Startup → Start services on startup**; without it the app opens
   after a restart but serves nothing. If Studio Hub is busy with a job it is left running and
   the file tells you to run it again when it is idle.
   Yam Display installs automatically with **1 - Install the app** (or run `Extras/Install Yam Display.command`).
3. The first time macOS opens the app you may need to **right-click → Open** (the app is not
   signed); if so the install file says so, and asks you to run it again.
   **`Update this Mac.command`** does the same at the end of every update, whether or not the
   app itself changed.
4. Allow the local-network and incoming-connection prompts, and wait for
   **Local services ready**.

The app shows its mode, what it offers, its services and its version and build under the sidebar — for example
`0.6.90 · 20d1795`. That is how you tell whether an update landed.

## Setting up a controller

1. **Connections → Role → Controller.**
2. **Connections → Listening on** — tick **Tailscale** (or "Any address on any network").
   A fresh install already listens on Tailscale and your local network; if you change it,
   the app offers the **Restart Services** button that applies it.
3. **Connections → Copy Fleet token.** This is the token the gateway and any joining Mac
   will need.

The Hub's port is fixed at **47873** so a node can name this Mac again after a restart.

## Joining a Mac to a controller

On the **joining** Mac's own app: **Connections → Join a controller**, then enter the
controller's address (`100.x.y.z`, or a name like `mac.tailnet.ts.net` - the port is optional) and the **controller's Fleet token**. The
controller's Fleet overview will then show whether this Mac is reachable.

## Seeing your pool

**Connections → Fleet overview** lists every Mac in the group, and it reads the same on
both ends — the controller and a node show the same list.

- On the **controller**: every Mac that has joined, with a **Controller** or **Node** badge
  and the address it answers at. A Mac that is switched off stays in the list, marked
  offline with the reason. It is still part of the group.
- On a **node**: the controller and every other Mac in the group, beside this Mac. If the
  controller is not answering, the page says so instead of showing an empty group.
- **Send here** pins new work to one Mac. It appears on a controller and on a Mac running
  on its own; a node does not choose where work runs, so the button is not offered there.

A node records which controller it joined, on the Mac itself, so it can still name its
group after the app is reinstalled.

## The two tokens — which is which

There are two credentials on the Connections page and they are not interchangeable.

**Hub token** — *this Mac's own key.* It never leaves this Mac. It is for the owner only:
changing settings, installing or removing models, deleting output, managing shared
voices, and deciding to join or leave a group. Do not give it out; anyone who holds it can
do everything on this Mac.

**Fleet token** — *the work badge for the group.* Give it to GatewayKH so it can send work
here and collect results, and to every Mac that joins this controller. It allows sending
work, following a job, downloading the result, acknowledging, cancelling or discarding an
uncertain job, and reading the model and fleet lists. It **cannot** change settings,
install or remove models, delete output, manage shared voices, or make a Mac join or
leave.

**The one that matters:** a node joins a controller with the controller's **Fleet** token,
*not* its Hub token. If a join is refused, that is almost always why.

## Reachable on your own network, without a setting to remember

A Mac that comes up loopback-only is invisible to every other Mac in the group, and the only
symptom is work that never arrives. So a Mac now answers on **its own private and Tailscale
addresses automatically** — recomputed every time it starts, because a tailnet address is not
the same number forever. Nothing needs ticking, and an address you added yourself is never
removed.

If you want to be explicit, or to set a Mac up from a script:

| Command | What it does |
|---|---|
| `--listen-auto` | Answer on this Mac's own networks, and keep doing it |
| `--listen-host 127.0.0.1` | Answer on exactly these addresses, nothing else |
| `--fleet-mode` | The above, plus start at login **and start the Hub at login** — for a Mac that must be ready for work after a reboot without anyone opening it |

Run them as arguments to the app, e.g. `open -a "Studio Hub" --args --listen-auto`. Each one is
remembered, so it is a one-time step per Mac.

The Hub reports what it is actually bound to, so "is this Mac reachable, and on what" is a fact
you can read rather than guess.

## The model library

The kit carries **28 model packages** (about 82.5 GB). `2 - Copy the models.command` copies
only what the Mac's memory can run, so an 8 GB Mac gets the image models and a 16 GB+ Mac
gets the audio ones too. Voices you have saved are copied at the same time. The library on
a drive is the library the Mac gets — there is no download step.

The step also keeps the library in step with the kit between releases: a model the kit no
longer offers is moved into **`_trash`** inside `~/StudioHubModels` (never deleted) and the
app's record is updated, so a model cut from a release does not sit on the Mac forever. The
space comes back when you empty that folder — that is your call, and the step says where it
went. Models the kit *does* ship are never removed, including ones this Mac is too small to
run: those are listed as left behind and stay. **Z-Image-Turbo 6B MLX 4-bit** is carried on
the 8 GB Macs: it is qualified there (measured on an 8 GB M1 Mac mini, low-RAM mode).

## Other files here

| File | Use |
|---|---|
| `START-HERE.md` | the plain walk-through for a new Mac |
| `NODE-SETUP-CHECKLIST.md` | tick list for setting up a node |
| `2 - Copy the Lite Mode models.command` | the Lite Mode Mac's model copy: the same as `2 - Copy the models`, but only the models Lite Mode uses (about 15 GB). It does not change the Mac's role or tick Lite Mode - see `NODE-SETUP-CHECKLIST.md`, "Shops" |
| `Token and Stuff.md` | a place you have written down this Mac's addresses and tokens |
| `Technical/verify-kit.sh` | proves this copy installs, starts and answers health |
| `Technical/RUN-16GB-FISH.command` | the 16 GB Fish test: double-click on the 16 GB Mac. It first puts Fish S2 Pro 4-bit, 8-bit and bf16 on that Mac from this kit's `model-library` (bf16 and 8-bit are not part of the normal copy step), then measures all three; read `README-8gb-tts.md` beside it |
| `Technical/RUN-8GB-TTS.command` | the 8 GB voice test (Qwen3-TTS 0.6B 8-bit and 4-bit, VoxCPM2 4-bit, OmniVoice bf16, Breeze TTS 2 4-bit) |

Keep your own record of the Fleet token somewhere safe, and use a **different** Fleet
token per controller.
