# Start here

This folder has the Studio Hub app, the models, and your saved voices.

Three things do the whole job. You never need to open anything else.

- **1 - Install the app.command** — puts the app on your Mac
- **2 - Copy the models.command** — puts the models and your voices on your Mac
- **Studio Hub.app** — the app itself

If a double-click does nothing, or says the file cannot be opened, right-click it
and choose **Open**, then **Open** again. That is normal for a file that is not
signed, and you only do it the first time.

---

## The whole job, in four steps

### 1. Double-click **2 - Copy the models**

It reads how much memory your Mac has and copies only the models this Mac can run,
plus all ten of your voices. On a 16 GB Mac that is about 34 GB, so give it several
minutes. Leave it alone until it says it is done.

It can be run again any time. It is built for it: models and voices you do not have
are added, ones that changed are updated, and ones that are already correct are left
alone. A second run takes about a second.

### 2. Double-click **1 - Install the app**

It asks for your password, because putting an app in **Applications** needs it. That
is the only time you will be asked.

### 3. Open Studio Hub from Applications

Right-click it and choose **Open**, then **Open** again the first time.

It will say **Choose where your models live** and offer a button:

> **Use the Models Already on This Mac**

Press it. That is the folder step 1 filled in, and it is where your models and voices
are. The app remembers, so you never do this again.

### 4. Say yes to the two pop-ups

- **"wants to find devices on your local network"** → **Allow**
- **a firewall question about incoming connections** → **Allow**

Then look at the bottom left. When it says **Local services ready**, everything is
running and you are done.

---

## Where things end up

- the app: `Applications/Studio Hub.app`
- the models: `StudioHubModels`
- your voices: inside the app's own folder, in your user library

---

## Giving this to another Mac

Everything on that Mac comes from this drive, so the drive has to be there for it.

1. Plug the drive into that Mac.
2. Double-click **2 - Copy the models**, then **1 - Install the app**, then open the
   app and press **Use the Models Already on This Mac**.

The app and the models are copied onto that Mac. After that the drive can be
unplugged and that Mac keeps working — but the next time you want to copy anything to
it, the drive has to be plugged in again.

---

## When there are new models

Double-click **2 - Copy the models** again. It adds what is new, updates what
changed, leaves what is already correct alone, removes an older copy of a model it
just updated, and repairs the app's record as it goes.

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

**The copy step says it cannot find Studio Hub.**
Run **1 - Install the app** first, then the copy step again.

**A model says "not installed" when you know it is there.**
Run **2 - Copy the models** again — it repairs the record. If it still says that, open
**Models** and press **Verify**.

**The app will not start the services.**
Quit the app and open it again. If that does not fix it, tell me.

**You want to see what went wrong.**
Open the app's menu bar icon and choose **Open Engine Log**. Or run this and send me
the result:

```
sh "/Volumes/KH-STUDIO-ATLAS/Studio Hub App/Technical/check-everything.sh"
```

**Anything else.** Send me a photo of the screen, or the text. I will sort it.

---

## The one rule

If a message mentions **Python**, **developer tools**, or asks you to install
something — say no and tell me. The app carries everything it needs. It should never
ask for anything to be installed.
