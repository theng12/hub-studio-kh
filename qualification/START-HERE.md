# Start here

This folder holds the Studio Hub app, the models, and your saved voices.

Two things, then the app chooses the models with you.

- **1 - Install the app.command** — puts the app on your Mac
- **Studio Hub.app** — the app itself, which copies the models you choose

If a double-click does nothing, or says the file cannot be opened, right-click it
and choose **Open**, then **Open** again. That is normal for a file that is not
signed, and you only do it the first time.

---

## The whole job, in three steps

### 1. Double-click **1 - Install the app**

It asks for your password, because putting an app in **Applications** needs it. That
is the only time you will be asked. It closes and replaces any older copy for you.

Yam Display installs automatically with **1 - Install the app** (or run
`Extras/Install Yam Display.command`).

### 2. Open Studio Hub from Applications

Right-click it and choose **Open**, then **Open** again the first time. Allow the
local-network and incoming-connection pop-ups.

Then open **Set up this Mac** in the sidebar. It shows how much memory this Mac has,
how much space the models' drive has, and four sets sized for that memory:

- **Essentials · 8 GB**
- **Creator · 16 GB**
- **Creator Plus · 24 GB**
- **Studio Pro · 32 GB+**

Take the one that matches, or tick models by hand. Press Install and give it several
minutes the first time. Running it again copies only what is missing.

**A Lite Mode Mac:** set it up like any Mac and take **Essentials · 8 GB**, then tick
**Lite Mode** in Studio Hub › Settings. There is no separate Lite Mode copy step any
more; the model set is the same.

**Installing from this SSD?** Close the app, double-click **2 - Copy the models.command**,
choose one of the four presets in the Terminal window it opens, then open the app again.
**2 - Copy the models (everything).command** copies every model this Mac can run, for a Mac
with no internet and no other Mac to copy from. Studio Hub never reads this drive by itself;
only these commands do, when you run one.

### 3. Say yes to the network and connection pop-ups

- **"wants to find devices on your local network"** → **Allow**
- **a firewall question about incoming connections** → **Allow**

Studio Hub no longer reads a removable drive on its own, so there is no Removable Volumes
prompt to answer.

Then look at the bottom left. When it says **Local services ready**, everything is
running and you are done.

---

## Where things end up

- the app: `Applications/Studio Hub.app`
- the models: `StudioHubModels` (or the folder you chose)
- your voices: inside the app's own folder, in your user library

---

## Giving this to another Mac

Everything on that Mac comes from this drive, so the drive has to be there for it.

1. Plug the drive into that Mac.
2. Double-click **1 - Install the app**, open the app, and use **Set up this Mac**.
   For the models from this drive, double-click **2 - Copy the models.command** and choose a
   preset there.

The app and the models are copied onto that Mac. After that the drive can be
unplugged and that Mac keeps working — but the next time you want to copy anything to
it, the drive has to be plugged in again.

---

## When there are new models

Open **Set up this Mac** again. It shows what is missing. Models that come from this drive
are copied by **2 - Copy the models.command**, which shows what is missing and copies only
that. The no-internet step, **2 - Copy the models (everything).command**, also does it, and
it moves a model the kit no longer offers into `_trash` inside the models folder rather
than deleting it.

---

## Checking for an update

A Mac updates itself from its controller; nobody has to press anything. To ask right
away, press **Check for update now** beside the version in the sidebar. If work is
running it says it will update when that work finishes — it never restarts mid-job.

---

## What's new in each update

Every update shows a **What's New** page the first time you open it, written in plain
language: what changed, what was broken, and what to do differently. You can reopen it
from **What's New** in the sidebar, from the **Studio Hub** menu, or from the menu bar
icon. The version is at the top of that page.

---

## Making pictures and sound

- **Image** — type what you want, press **Generate Image**
- **Subtitle** — choose a recording, press to transcribe
- **Audio** — three tabs: **Clone a Voice** (your saved voices), **Ready-Made Voices** (built
  into the model) and **Design a Voice** (describe one in words)

Finished work appears in **Jobs**, with its length and size next to it.

Your ten voices are under **Voices**: Christian, Vincent, Leo, Samuel, Todd, Archer,
Aiden, Mark, Leona and Chris. Open **Voices** and press **Refresh** if they are not
there yet.

---

## If something goes wrong

**Set up this Mac says the models are not on this Mac.** The app never lists a drive by
itself: to copy from the SSD, run **2 - Copy the models.command** from the drive and choose
a preset. The drive must be plugged in for that.

**A model says "not installed" when you know it is there.**
Open **Set up this Mac** again — it copies what is missing and repairs the record. If it
still says that, open **Models** and press **Verify**.

**The app will not start the services.**
Quit the app and open it again. If that does not fix it, tell me.

**You want to see what went wrong.**
Open the app's menu bar icon and choose **Open Engine Log**. Or run this and send me
the result:

```
sh "/Volumes/KH-STUDIO-ATLAS/1 Install Apps/Studio Hub/Technical/verify-launch.sh"
```

**Anything else.** Send me a photo of the screen, or the text. I will sort it.

---

## The one rule

If a message mentions **Python**, **developer tools**, or asks you to install
something — say no and tell me. The app carries everything it needs. It should never
ask for anything to be installed.
