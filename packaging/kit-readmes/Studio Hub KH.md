# Studio Hub KH — the Macs that run the models

**Version 0.6.43** (build `c851bec`).

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

Work is placed by memory: **voice, music and video** go to the Macs with the most memory,
while **images and subtitles** prefer the smaller ones, so a big Mac stays free for audio.
If the preferred Mac is busy the job goes to another; nothing waits.

## Install on a Mac

Two double-clicks, then open it. `START-HERE.md` has the long version with pictures of
what you should see.

1. **`2 - Copy the models.command`** — reads how much memory this Mac has and copies only
   the models it can run, plus your saved voices. Give it several minutes the first time.
   It is safe to run again: it adds what is missing and leaves the rest alone.
2. **`1 - Install the app.command`** — puts the app in **Applications**. It will ask for
   your password once. It closes and replaces any older copy for you; quit and reopen the
   app afterwards to be sure you are running the new build.
3. Open **Studio Hub** from Applications. The first time, **right-click → Open** (the app
   is not signed).
4. Allow the local-network and incoming-connection prompts, and wait for
   **Local services ready**.

The app shows its version and build at the bottom of the window — for example
`0.6.43 · c851bec`. That is how you tell whether an update landed.

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

## The model library

The kit carries **35 model packages** (about 83 GB). `2 - Copy the models.command` copies
only what the Mac's memory can run, so an 8 GB Mac gets the image models and a 16 GB+ Mac
gets the audio ones too. Voices you have saved are copied at the same time. The library on
a drive is the library the Mac gets — there is no download step.

## Other files here

| File | Use |
|---|---|
| `START-HERE.md` | the plain walk-through for a new Mac |
| `NODE-SETUP-CHECKLIST.md` | tick list for setting up a node |
| `Token and Stuff.md` | a place you have written down this Mac's addresses and tokens |
| `Technical/verify-kit.sh` | proves this copy installs, starts and answers health |

Keep your own record of the Fleet token somewhere safe, and use a **different** Fleet
token per controller.
