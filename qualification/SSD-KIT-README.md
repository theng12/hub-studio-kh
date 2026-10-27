# Studio Hub KH — read this first

Everything needed to install and test this on a Mac is in this folder, or in this file.
No step requires another machine, a download, or anything from the machine this kit was
built on.

- **App:** `Studio Hub.app` — its version and build are in `focused-release-manifest.json`,
  written beside it when the kit was staged (a pinned version here goes stale the next build)
- **Models:** `model-library/` is described by a 39-package source catalog (~169.0 GB). Normal setup copies **39 production-allowed packages** (about 169.0 GB). No package is withheld pending qualification any more (owner, 2026-10-27: "We no longer block or lock any model."); the Mac's own memory is what decides which of them it copies and runs. Qwen Image 2.1 Turbo MLX 4-bit is production-eligible on 16 GB and larger Macs; eligibility does not claim this SSD or any Mac already has its weights, which remains a kit-receipt fact.
- **Receipts:** `focused-release-manifest.json`, `Studio Hub.app.tree-sha256`
**Layout:** the app and `model-library/` sit side by side at the top of the kit folder — no nested folders to hunt through.
- **Guide:** `INSTALL-AND-TEST.md` (the long-form operator guide)

The app carries its own Python and every generation dependency. It needs no Homebrew, no
Python, no pip, no FFmpeg, and no internet. macOS 14.0 or later; Apple Silicon only.

---

## 1. Install it — two double-clicks

Two files in the kit folder do the whole job. You do not need Terminal.

**`1 - Install the app.command`** puts the app in `/Applications`. It asks for an administrator
password, because that is what `/Applications` requires. Answer `n` at the prompt and it installs
into `~/Applications` instead, which needs no password and works just as well.

**`2 - Copy the models.command`** reads how much memory this Mac has, works out which models fit,
and copies them into `~/StudioHubModels`. Models that do not fit are left behind; the
ones that need them are bigger Macs.

Run the second file again whenever the SSD is refreshed. That is how models arrive and change:

- models this Mac has never had are copied,
- a model whose shipped files changed is replaced in place,
- models that are already identical are left alone, so a second run takes seconds,
- the older revision of a model that was just updated is removed, so the Mac does not slowly
  fill with versions nothing uses,
- the app's own record is refreshed as each model is copied, so an updated model reads as
  installed without anyone pressing Verify.

If a double-click does nothing, right-click the file → **Open** → **Open**; an unsigned file
needs that once. Both files also work when run by hand:

```sh
sh "/Volumes/KH-STUDIO-ATLAS/Studio Hub App/1 - Install the app.command"
sh "/Volumes/KH-STUDIO-ATLAS/Studio Hub App/2 - Copy the models.command"
```

To place things manually instead — app by hand, library left on the SSD:

```sh
cp -Rp "/Volumes/KH-STUDIO-ATLAS/Studio Hub App/Studio Hub.app" /Applications/
```

A library can stay on the SSD, but it must be **outside** the app bundle — the app refuses a
library inside itself — and the drive must then be **mounted** whenever the app runs. The app
says so plainly if it is not: *"The selected model library is unavailable. Connect its drive or
choose another folder."* Copying it to `~/StudioHubModels` avoids that dependency
entirely, which is why the launcher does it that way.

Either way, you point the app at the library once (step 4) and it remembers.

## 2. Open it

The app is unsigned, so a plain double-click is blocked the first time. Right-click → **Open**
→ **Open**.

If macOS still refuses — which happens when the copy came through iCloud Drive, AirDrop, Mail
or a downloaded archive — paste this into Terminal once, then open it again:

```sh
xattr -cr "/Applications/Studio Hub.app"
```

That is the command for the app in `/Applications`. If you ever run it straight off the SSD
instead, the path is
`xattr -cr "/Volumes/KH-STUDIO-ATLAS/Studio Hub App/Studio Hub.app"`.

**About the errors it prints.** That command will scroll past a wall of `Permission denied` and
`No such xattr` lines against files under `Contents/Resources/runtimes`. That is expected and it
is not a failure: the bundled Python runtimes ship read-only, and they never carry a quarantine
flag in the first place. The files macOS actually quarantines are the app's own executable and
script resources, and `xattr` clears those before it gets to the runtimes. The command exits
non-zero for the same cosmetic reason, so judge it by the result, not the exit code: open the app
and see whether it launches.

If you would rather have a quiet run with no complaints:

```sh
find "/Applications/Studio Hub.app" -path "*/Resources/runtimes" -prune -o -print0 \
  | xargs -0 xattr -d com.apple.quarantine 2>/dev/null; echo done
```

That skips the read-only runtimes entirely and clears quarantine from everything else.

Right-click → **Open** once more afterwards. Once it has opened successfully, double-clicking
works normally from then on.

## 3. Grant the two prompts

Both are normal, and neither is a bug:

- **"Studio Hub would like to find devices on your local network"** → allow. Only matters when
  this Mac is a fleet node, but allowing it now saves a confusing failure later.
- **Firewall prompt about accepting incoming connections** → **Allow**. A node that denies this
  is invisible to its controller while looking perfectly healthy on its own screen.

## 4. Point it at the models

Choose the `model-library` folder the copy step filled in when the app asks:

```
~/StudioHubModels
```

The choice is remembered per machine. If you left the library on the SSD instead, choose it
there and keep the drive mounted — the app refuses a library that is inside itself, and reports
a library that has gone missing rather than guessing.

## 5. Wait for ready

The status line should read **Local services ready**. That means four processes came up: the
Hub plus the Voice, Image and Subtitle workers. Then open **Settings → Model memory** and
confirm each of the three Studios reports a state. A Studio reading *Needs an update* or
*Not running* is a real problem, not a cosmetic one.

## 6. Prove the copy starts — do this one

Before anything else, run this from the kit folder:

```sh
sh verify-launch.sh
```

It starts the app's **own** bundled service with the app's **own** arguments, waits for it to
become ready, asks it for its health, and stops it again. It prints `RESULT: PASS` or
`RESULT: FAIL` plus the reason.

This exists because a copy that looks complete can still be unable to start: the first release
of this app launched its service with a flag the service did not accept, so it exited with code
2 every time and the app could only say *"the engine exited (code 2)"*. Nothing caught it
because every automated test drove the service directly, with the flags it really has. This
script checks the outcome instead of the intention.

If it fails, it prints the service's own message — which is the thing the app never shows you.
Send that message back and it will be obvious what is wrong.

To point it at a different app (it always uses an empty temporary model library, never yours):

```sh
APP="/Applications/Studio Hub.app" sh verify-launch.sh
```

## 7. Optional: prove the copy is intact

```sh
python3 -c "import json;d=json.load(open('/Applications/Studio Hub.app/Contents/Resources/release-manifest.json'));print(d['version'], d['implementation_commit'], 'development_build:', d['development_build'])"
```

Expect exactly:

```
0.4.0 f146bc0e80ac2c3e9f7afa55a0b46abc49ac91c1 development_build: False
```

Change the path if you put the app somewhere else — running it straight from the SSD, that is
`/Volumes/KH-STUDIO-ATLAS/Studio Hub App/Studio Hub.app/...`. If it prints that line,
you are running the exact build the tests passed against.

## Your own voices

**Voices** in the sidebar (under Models) is the voice library: the saved reference clips a
clone reuses. Add one with a clip and its exact transcript, edit it, play it, remove it, and
use it for the next generation with **Use**.

To bring over voices you already have, check the folder first:

```sh
sh "/Volumes/KH-STUDIO-ATLAS/Studio Hub App/check-voices-folder.sh" "/path/to/your/voices"
```

It reports which voices are ready and which have no transcript. Then **Voices → Import
Folder…** and choose the same folder. A voice with a clip but no transcript is skipped — a
clone matches audio to words, so without them the result would sound wrong. Everything
imported is recorded as *Owned by me*, with the consent box ticked.

## Downloading a model

Model downloads need the internet, so **Offline mode must be off**:
**Settings → Model library → "Use imported models offline"**. While it is on, every **Download
Model** button is greyed out on purpose and the screen now says so. Importing a folder you
already have keeps working with it on.

Every model the app ships a definition for carries its own Hugging Face links, so **Download
Model** fetches it at the pinned revision and verifies every file.

## Running anything I send you

1. Open **Terminal** (Applications → Utilities → Terminal).
2. Copy a line out of `COMMANDS.txt` on this SSD.
3. Paste it into Terminal and press Return.

That is all "run this" means. `COMMANDS.txt` has every command in order, and its top section
explains this too.

## The proof, in this order

Simplest first. If one of these fails, stop there — the next one needs it.

**1. One image.** Image page → type any prompt → **Generate Image**. Expect a finished image in
the page's own history. This exercises the Image runtime, the memory handoff and output
storage.

**2. One transcript.** Subtitle page → pick an audio or video file → transcribe. Expect text
and a subtitle file. Then press **Save** — saving is what verifies the file and acknowledges
delivery, and it is what lets the Hub reclaim the protected copy later.

**3. One voice clone.** Voice page → pick a Qwen **Base** model → choose a reference clip and
type its **exact transcript** → generate → **Save**. A mismatched transcript fails closed by
design; if you hear a refusal about the transcript, retype it rather than working around it.

Both Base tiers ship at 8-bit and 4-bit. On a Mac that is also running Image or Subtitle, start
with a **4-bit** Base model — same cloning contract, roughly half the weights. The 8-bit and
bf16 builds are the higher-fidelity tiers when the machine is otherwise idle.

**4. Only then, the fleet.** **Connections → Fleet** → set this Mac to **Controller** → turn on
the network address it should answer on (a Tailscale address if you have one) → restart the
services when prompted. It answers on port **47873** every time, so the address below it stays
true. On the other Mac, turn on one of its own network addresses the same way - a node has to be
reachable too, or the controller can never send it work - then paste this controller's
**address:port** and the **Fleet token** shown under **Connections** into the Join form. **Fleet
overview** should then show both Macs, with the jobs each has run and its measured average time.
A node keeps that address and **re-joins by itself** whenever its services start, so restarting
either Mac does not mean retyping anything. Retiring, replacing or selling a Mac — including the
token rotation that goes with it — is written out in `NODE-SETUP-CHECKLIST.md`.

## If something goes wrong

- **App will not open** → the `xattr -cr` command in step 2, then right-click → Open.
- **A model says *File changed; verify again*** → this happens when a library was copied by
  hard-linking or cloning rather than a real copy. Open **Models** and press **Verify**; it
  re-records the files and the model becomes usable. If it keeps happening, re-copy that
  package folder with Finder.
- **A job sits waiting and never starts** → open **Settings → Model memory**. If a Studio is
  holding a model, release it there and the job proceeds. A job waits when less than ~2 GB is
  free *at that moment*; that is the only memory gate left in this build.
- **A Studio shows *Not running*** → **Settings → Local services → Open Log** and read the last
  screenful. The log names the model, the file or the gate that stopped it.
- **The Mac is slow** → check **Settings → Model memory** for which Studio is resident, and
  **Connections → Fleet overview** for that machine's measured speed. Releasing an idle model
  is usually the whole fix on 8 GB.

## What this build does *not* have

- No per-model RAM gate **in the running app**. Every listed model runs on every Apple Silicon
  Mac; a Mac below a model's qualified tier shows a warning beside it and runs it anyway. The
  library itself is filtered by memory before it gets there - `2 - Copy the models` copies only
  the models whose floor this Mac meets, which is why the app has nothing to gate.
- VoxCPM2 has never produced output on any host that has tried it, including a 16 GiB M4. It
  is in the library; treat it as unproven until it works for you.
- Nothing has run on a second Mac yet. This Studio is the first machine other than the build
  M4, so the fleet path between two physical Macs is genuinely unexercised.
