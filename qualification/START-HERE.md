# Start here

This folder has the Studio Hub app and all the models it needs.

Two files do the whole setup. You do not need to open anything else.

- **1 - Install the app.command** — puts the app on your Mac
- **2 - Copy the models.command** — puts the models on your Mac

If a double-click does nothing or says the file cannot be opened, right-click it
and choose **Open**, then **Open** again. That is normal for a file that is not
signed. You only do it the first time.

---

## The whole job, in four steps

### 1. Double-click **1 - Install the app**

It asks for your password because putting an app in **Applications** needs it.
That is the only time you will be asked.

### 2. Double-click **2 - Copy the models**

It reads how much memory your Mac has and copies only the models this Mac can
actually run. On a 16 GB Mac that is about 28 GB, so give it a few minutes.

Leave it alone until it says **Done**. You can run it again any time.

### 3. Open Studio Hub

Open it from **Applications**. It asks for a model folder. Choose:

```
Downloads/model-library
```

The copy step tells you this path again when it finishes. Do this once. It
remembers.

### 4. Say yes to the two pop-ups

- **"wants to find devices on your local network"** → **Allow**
- **a firewall question about incoming connections** → **Allow**

Then look at the bottom left. When it says **Local services ready**, everything
is running and you are done.

---

## When there are new models

Double-click **2 - Copy the models** again. It is built to be run over and over:

- models you do not have yet are copied,
- models that changed on the SSD are updated in place,
- models that are already correct are left completely alone, so a second run
  takes seconds instead of minutes,
- an older copy of a model that was just updated is removed, so your Mac does not
  slowly fill up with versions you no longer use.

Then open Studio Hub again. The new models are ready with nothing else to do.

---

## What's new in each update

Every update now shows a **What's New** page the first time you open it, written in plain
language: what changed, what was broken, and what to do differently. You can reopen it any time
from **What's New** in the sidebar, from the **Studio Hub** menu, or from the menu bar icon.

The version is shown at the top of that page (this one is 0.5.0).

---

## Making pictures and sound

- **Image** — type what you want, press **Generate Image**
- **Subtitle** — choose a recording, press to transcribe
- **Audio** — type your words, choose a voice, press to generate

Finished work appears in **Jobs**, with its length and size next to it.

---

## Your voices come with it

The kit carries your ten saved voices — Christian, Vincent, Leo, Samuel, Todd, Archer, Aiden, Mark,
Leona and Chris. **2 - Copy the models** puts them on your Mac along with the models, and a voice
already on a Mac is never overwritten, so nothing you have changed there is lost.

Open Studio Hub, go to **Voices**, and press **Refresh**. All ten are ready to use.

A voice whose clip is longer than about 15 seconds already carries the timing the app needs, so you
can pick it and press **Prepare Voice** straight away. If you ever see a message about a transcript
needing timestamps, press **Transcribe Clip** once and try again.

## If you have voice clips from before

If you have voice clips from before and want them in the app, tell me and I will
do it for you. It is one command and I would rather run it than have you guess.

---

## If something goes wrong

**The copy step says it cannot find Studio Hub.**
Run **1 - Install the app** first, then the copy step again.

**A model says "not installed" when you know it is there.**
Run **2 - Copy the models** again — it repairs the record. If it still says that,
open **Models** and press **Verify**.

**The app will not start the services.**
Quit the app and open it again. If that does not fix it, tell me.

**You want to see what went wrong.**
Open the app's menu bar icon and choose **Open Engine Log**. Or run this and send
me the result:

```
sh "/Volumes/KH-STUDIO-ATLAS/Studio Hub App/Technical/check-everything.sh"
```

**Anything else.** Send me a photo of the screen, or the text. I will sort it.

---

## The one rule

If a message mentions **Python**, **developer tools**, or asks you to install
something — say no and tell me. The app carries everything it needs. It should
never ask for anything to be installed.
