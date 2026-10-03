# What's new in Studio Hub

Written to be read by the person using the app. Newest first.

## 0.6.138 - 2026-10-03

### Voices with a long silence in the middle, or a missing sentence, are made again

**A piece of a voice-over with a long silence inside it is now caught.** In 0.6.137 a piece could pass its check with half a minute of
silence in the middle, because the rest of it sounded fine. Studio Hub now measures the longest quiet stretch between the first and last
words. If it is far longer than a natural pause (about 4 seconds at normal speed), the piece is made again, the same way as other
failed pieces. This check needs no extra memory, so 8 GB Macs do it too.

**A dropped phrase is now caught.** When the Mac listens back to a piece, it used to allow a few missing words anywhere. If more than three
words in a row are missing, a whole phrase was dropped, and the piece is now made again.

## 0.6.137 - 2026-10-03

### Voices are checked piece by piece, and long chapters are split into pieces that finish in time

**Every piece of a voice-over is checked before it goes into the finished file.** A long text is voiced in pieces. Each piece is now
checked: is it silent, or just a hum, or much too long for its words, and (when the Mac has the memory for it) does it actually say
the words it was given? A piece that fails is made again, up to three more times. If it still fails, the job stops and tells you which
piece was the problem, instead of handing back audio with silence, humming or missing sentences in it.

**Breeze no longer runs on for a minute and a half on a short sentence.** Each Breeze piece now gets only as much time as its words
need, which is where the long stretches of humming and babble came from.

**Breeze chapters are split into pieces a slower Mac can finish.** Studio Hub now tells the Gateway the most text one Breeze request
should carry (1,500 characters), worked out from the slowest Mac allowed to run it, so a long chapter is sent in parts instead of
running out of time.

## 0.6.136 - 2026-10-03

### Updates no longer wait for ever, Studio Hub only reopens after a crash, and lost work is redone

**A busy group of Macs no longer holds back its update.** When an update is ready, the main Mac of a group finishes its own work and then
pauses the whole group: the other Macs finish what they are doing, take nothing new for a moment, and the update installs. Before, a long
queue could keep the group busy so the update never got its turn.

**Studio Hub reopens by itself only after a crash.** If Studio Hub crashes, it opens again on its own. If you choose Quit, use Force Quit,
or macOS closes it because the Mac ran out of memory, it now stays closed.

**See why a Mac went offline.** Each Mac now remembers why Studio Hub last stopped (you quit it, it crashed, Force Quit, low memory, an
update, or the Mac was shut down), and the Gateway can show that next to a Mac that is offline.

**Work is redone when a Mac or studio stops in the middle of it.** If a Mac goes quiet, or one of its studios stops while making something,
that job is now started again on another Mac (or on the same one once it is back) for every kind of job: images, music, video and voices.
If the first try turns out to have finished after all, its result is kept and the second try is stopped.

## 0.6.135 - 2026-10-03

### Studio Hub keeps itself running, and you can pause a Mac

**One studio stopping no longer stops everything.** If one studio (for example Chat) stops unexpectedly, Studio Hub now starts that studio
again by itself after a short wait. The other studios, and the jobs they are running, carry on as normal.

**Studio Hub comes back after a crash or a Force Quit.** If Studio Hub crashes, or is closed with Force Quit (for example when the Mac runs
low on memory), it now opens again by itself. Choosing Quit from its menu still closes it and it stays closed.

**Saved voices from this Mac stay on this Mac.** A voice that only exists in this Mac's own library is now only used here, instead of
being sent to another Mac that does not have it.

**Pause a Mac from the Gateway.** A Mac can now be paused: it finishes the work it is doing, takes no new jobs, and stays in the fleet.
Resume it and it starts taking work again.

## 0.6.134 - 2026-10-03

### Thumbnails, music and video finish on your other Macs

**Jobs sent to another Mac now finish there.** A controller wrongly rejected the first progress update from every image, music or
video job running on another Mac, so a job that took more than two minutes - such as a Z-Image thumbnail on an 8 GB Mac - was dropped and
started again on the next Mac, over and over, and never finished. These jobs now run to the end on the Mac that took them.

**A Mac no longer stays "reserved for other work" after its job is done.** A Mac now frees itself as soon as its job ends, instead of
refusing new work for up to six hours.

**A busy Mac means "wait", not "failed".** Subtitles and Chat now wait for a free Mac instead of giving up with "reserved for other work on
this Mac" when the Macs they tried were busy.

**Work that is given up on is stopped.** When a controller gives up on a job on another Mac and starts it elsewhere, it now tells that Mac to
stop, so the Mac is not kept busy on work nobody will use.

## 0.6.133 - 2026-10-03

### Fleet health shows real problems only, and renamed Macs show their new name

**Idle Macs no longer show as needing attention.** Macs that joined while the Video studio still shipped kept a Video worker on their
controller's list, so Fleet showed "workers offline" for a worker that no longer exists and kept every idle Mac amber. Each Mac's
studios now follow what that Mac actually runs.

**An old failed job stops holding a Mac in "Needs Attention".** After 15 minutes a failed job no longer keeps the Mac amber; it goes back
to Ready like any other finished job. A studio that should be running and is not still shows as needing attention.

**A renamed Mac shows its new name everywhere.** When you rename a Mac, its controller and the Gateway now pick up the new name from the
Mac itself, within a minute or so.

**Rename a Mac from the Gateway.** Studio Hub is now ready for the Gateway's new Rename option for a Mac in the fleet. The name is changed on that Mac first, so if the Mac is
switched off it keeps its name and you are told why.

**Connections shows "Machine ID".** On a Mac that is not a controller, Connections now shows its Machine ID, which stays the same when the
Mac is renamed. A Mac handed over to a new controller now joins under its name instead of its ID.

## 0.6.132 - 2026-10-02

### A new Studio Hub icon, and changing the fleet password is safe for offline Macs

**A new Studio Hub icon.** The app has a fresh look: a picture frame holding a wave and a note, on a dark blue tile. You will see it in the Dock,
in Finder, in the About window and in the menu bar, where it follows light and dark mode.

**Changing the fleet password no longer leaves Macs behind.** A Mac that was switched off or away when you changed the password now keeps working
with its old one for up to 30 days. As soon as it comes back online it is given the new one, and the old one is retired. Connections shows how many
Macs are still on the previous password. We also fixed a bug where a controller kept the old password until it was restarted.

**Voice recordings that were never saved are tidied up.** Audio the app generated but never recorded is now moved to the Trash Folder after 7 days.
The first time this runs it moves any backlog there too. Nothing is deleted straight away: the Trash Folder empties itself after 15 days.

**A 16 GB test for Fish voices.** The kit now has a one-click test that measures Fish S2 Pro (4-bit, 8-bit and full precision) on a 16 GB Mac.

**More reliable tests.** A few tests that sometimes failed by chance now give the same answer every time.

## 0.6.131 - 2026-10-02

### Passwords and tokens are hidden, and jobs start on 8 GB Macs

**Passwords and tokens are hidden behind an eye icon.** The Fleet token, the Hub token, the Hugging Face token and the token boxes for handing over
or joining a controller now show dots until you press the eye. Copy still works while they are hidden, and what you type is hidden too.

**Jobs start on 8 GB Macs showing about 3 GB free.** We measured real 8 GB Macs (an M1 and an M2). Voice and image jobs started cleanly from about
3 GB of free memory and never swapped, so the Hub no longer holds them back until 4 GB or more is free. It now starts a job once 2.8 GB is free,
which leaves a little room to spare. This applies to Qwen3-TTS 0.6B (8-bit and
4-bit), VoxCPM2 4-bit, OmniVoice, FLUX.2 Klein 4-bit and Z-Image-Turbo 4-bit.

**The 8 GB test reports are clearer.** They now say "Peak memory used" and "Started with N GB free" as two separate lines, so a peak is never mistaken
for the free memory a job needs before it starts.

## 0.6.130 - 2026-10-02

### Proper pauses for Fish and more voices, and a lighter Fish on 16 GB Macs

**Pauses you can hear.** Fish, VoxCPM2, Chatterbox and Pocket now pause between sections the way OmniVoice and Qwen already did: a short
breath (0.3 s) after a sentence and a longer one (0.6 s) at a paragraph break, instead of one flat, barely audible gap.

**Fish Audio S2 Pro 4-bit now fits 16 GB Macs.** Its measured memory use is about 7.4 to 8.1 GB, so it is offered from 16 GB instead of 24 GB.

**Breeze is for 16 GB Macs again.** The earlier trial that copied Breeze to 8 GB Macs is undone; it needs about 5 GB, which is too tight there.

## 0.6.129 - 2026-10-02

### Models can no longer be lost by a test, and a lighter Fish voice

**Your models are safe from tests.** A test or check that used the real model library could move models into a temporary folder that was then
deleted, and the models went with it. Now a model library records which Hub owns it, and only that Hub may move anything out of it.

**New: Fish Audio S2 Pro 4-bit.** A lighter Fish voice (about 4.5 GB on disk) for Macs with 24 GB. It is made under the Fish Audio Research
License. It uses the codec from the 8-bit release, because the 4-bit download's own codec file cannot be read by the Hub's engine.

**Fish now uses far less memory.** Reading out a long script no longer balloons: the biggest memory use of Fish S2 Pro fell from about
14.6 GB to about 7.4-8.1 GB, with the same sound.

**Breeze on 8 GB Macs.** Breeze TTS 2 4-bit is now copied onto 8 GB Macs by the model copy step, so it can be tested there.

## 0.6.128 - 2026-10-02

### Image and voice now fit on 8 GB Macs, and a few things are tidied

**Images on 8 GB Macs.** FLUX.2 Klein and Z-Image-Turbo now use far less memory while they draw (Klein about 3 GB instead of about 9 GB),
so an 8 GB Mac no longer swaps for minutes or wears its disk. Larger Macs keep the model ready between jobs and are quicker for the next one.

**Voice-overs on 8 GB Macs.** VoxCPM2, Qwen3-TTS and OmniVoice now stay near 3 GB, and Breeze near 4.3 GB, with exactly the same sound. A long
script no longer runs the Mac out of memory.

**A lighter Qwen voice.** Qwen3-TTS 0.6B Base is now also offered in a smaller 4-bit version.

**Gemma is gone.** The Gemma 3 12B text model is retired and every Mac moves its copy to the Trash Folder (emptied after 15 days).

**Jobs do not wait for ever.** If a Mac cannot free the memory a job needs for about four minutes, the job is handed back so another Mac can
take it, with a plain sentence saying how much memory was needed.

**Chat fix.** Asking Chat for JSON together with a system message no longer fails.

**Tidier sidebar.** In a short window the sidebar scrolls and its footer no longer overlaps the list.

## 0.6.127 - 2026-10-02

### A Mac protects its own disk, starts by itself, and the app looks tidier

**Your Mac's disk is protected.** Each Mac now watches its own free space, clears old finished work in a safe order (into a Trash
Folder, never straight to deletion), and warns before the disk gets full. The Gateway shows a Disk column for every Mac.

**Macs start the Hub by themselves.** Each Mac reports whether it starts Studio Hub when you log in, and you can change that from
the Gateway without walking over to it. Macs set up from the drive start serving automatically.

**Smaller files straight from the Hub.** Music, voice-overs and images can come out already compressed (m4a audio, JPEG images)
when the caller asks for it, so less data travels and less disk is used.

**Out of memory? The job moves on.** If a Mac keeps saying it does not have enough memory for a job, the Hub now hands the job back
so another Mac can take it, instead of leaving it waiting.

**Cancel means cancelled.** When you cancel a running job and the Mac confirms it stopped, the job shows as cancelled.

**Video is gone, the app is smaller.** The LTX video model is retired and its engine is no longer part of the app (about 0.5 GB
smaller). The Gemma text model stays available on the install drives and on any Mac set to keep all models, but fleet Macs no longer
copy it.

**Easier to read.** The sidebar says which Mac this is and whether it is a controller, a joined node or standalone. Pages share one
layout, type and set of status colours; tables line up; the Music player and row buttons no longer overlap text.

### Video (LTX-2.3) is retired

**There is no video model any more.** The local video model, LTX-2.3, did not make good enough clips, so it is no longer offered, no
longer put on the install drives, and no longer part of the app. The app gets about 0.5 GB smaller, and the model kit goes from 28
to 27 model packages (about 19 GB less). The Video page now simply says there is no video model, instead of showing an empty list.
Story Studio never asks for it: it uses a cloud video service if one is set up, and otherwise the scenes stay still.
Clips you already made stay where they are on disk. The Gemma text model that used to ride along with LTX is not removed: it
stays available in case we use it for chat later. Music is unaffected, including sung songs.

## 0.6.126 - 2026-10-01

### Long voice-overs: even pauses, and short lines no longer jump in volume

A long script is read in sections. The voice models left up to a second of near-silence at the start or end of each one, so the
gaps between sections were long and uneven (about a second where about a third of a second was meant). The Hub now trims that
empty stretch and puts in exactly the pause it is meant to have, for every voice model. A very short line in the middle of a story
(for example "Father, there is a boat out there!") is also nudged to the volume of the lines around it, instead of coming out
noticeably louder or quieter.

## 0.6.125 - 2026-10-01

### A Mac that is fine is no longer told it is short of memory

The Hub used to count only part of what macOS can free up, so a healthy
8 GB Mac could have a voice job held for ever with "waiting for memory". It now counts what macOS can really give. The 8 GB voice test
can also measure a model the Hub would hold back (with a safety stop, putting the setting back afterwards) and recommends a number from what it saw.

## 0.6.124 - 2026-10-01

### Chatterbox is shelved; a text-to-speech test for the 8 GB Macs

**Chatterbox is shelved.** Both Chatterbox models (4-bit and 8-bit), and the small helper they need, are still listed
and still work wherever they are installed, but the drives no longer copy them onto new Macs, and a Mac that has them
moves them into the Hub's Trash Folder (emptied after 15 days). A Mac marked "keep all models" keeps them.

**New: an 8 GB text-to-speech test.** On the kit drive, in the Technical folder, double-click `RUN-8GB-TTS.command` on
an 8 GB Mac. It reads the same two texts in the two voices with Qwen3-TTS 0.6B 8-bit, VoxCPM2 4-bit and OmniVoice
bf16, and writes a folder with the audio files and a short plain-English summary of what fits. It changes nothing on
the Mac.

(0.6.123 was never released; this includes everything in it.)

## 0.6.123 - 2026-10-01

### Updates can no longer leave a Mac stuck; a few big models are shelved

**An update can no longer leave a Mac stuck.** If the old app ever fails to close after an update, the update
now closes it for you after two minutes and opens the new one. Nothing to do.

**Some models are "shelved".** Whisper base, Whisper small, VoxCPM2 bf16 and Fish S2 Pro bf16 are still listed and
still work wherever they are installed, but they are no longer copied onto other Macs from the drives, and a Mac
that has one now moves it into the Hub's Trash Folder (emptied after 15 days). A Mac you mark "keep all models"
in its settings keeps them all. Whisper turbo is the subtitle model to use.

## 0.6.122 - 2026-10-01

### Updates restart the app by themselves

**Updates now restart the app by themselves** - the previous versions could hang half-quit after installing
an update (Force Quit fixed it). Studio Hub now stops its engine first, then closes and reopens on its own.

## 0.6.121 - 2026-10-01

### Macs can install updates by themselves again

**Updates install by themselves again.** The installer was refusing any release that includes the video
engine, so Macs quietly skipped the last update and stayed where they were. This release is labelled so the
installed apps accept it, and from here on the app knows the video engine, so later updates install normally.
Nothing you made is touched.

## 0.6.120 - 2026-09-30

### Lance video is retired; video is LTX-2.3

**Lance is gone. Video is LTX-2.3.** The small Lance video model did not make good enough pictures, so it is
no longer offered, no longer put on the install drives, and no longer part of the app. The video model is
LTX-2.3 22B (with its Gemma text encoder) on Macs with 24 GB of memory or more. The app gets about 1 GB smaller
and the model kit drops from 30 to 28 model packages. Nothing you already made is touched.

## 0.6.119 - 2026-09-30

### Quit just quits

**Quit no longer asks when nothing is running - it only asks if a job is in progress.** Choose Quit
(Command-Q, the menu or the Dock) while the Mac is idle and Studio Hub stops its engine and closes
straight away. If something is generating, transcribing or downloading, you still get the question, so a
job is never cut off by accident. Closing the window still keeps everything running in the background.

## 0.6.118 - 2026-09-30

### Updates now install by themselves even on a Mac that is always busy

**A busy Mac no longer misses updates.** Until now a Mac only updated itself when it had nothing at all to
do, so a Mac that was always generating never updated. Now, when an update is ready, a Mac waits up to
**10 minutes** for a quiet moment. If none comes, it stops taking **new** work (other Macs pick it up),
lets what it is doing **finish** - nothing is ever cut off - then installs, restarts and carries on. The
sidebar shows **"Updating after current work finishes..."** while it waits. Only one Mac in a group does
this at a time, so the rest keep working. If an install fails, the Mac goes straight back to taking work.

**Macs set up from a drive update on their own.** A Mac installed from a drive used to ignore updates
until someone opened the Connections page. It now trusts its own Studio Hub's update key the first time
and checks the next update against it, with nobody needing to do anything.

**No question nobody can answer.** If shutting down for an update hangs, Studio Hub now ends it by itself
instead of showing a "Force Stop" dialog on a Mac that nobody is sitting at.

**Faster updates for the controller.** Sending an update to a controller no longer makes it re-check
everything for every file it receives, so it stays responsive while the update arrives.

## 0.6.117 - 2026-09-30

### Cleanup now uses a Trash Folder, old updates no longer pile up, and retiring a model is one line

**Nothing is deleted straight away any more.** When Studio Hub tidies up finished pictures, voices and
music, it moves them into a **Trash Folder** inside its own data folder instead of deleting them. The
Trash Folder empties itself: anything that has been there more than **15 days** is removed for good, and
if the disk gets short, the oldest trash goes first so it never stops you generating. **Storage** in
Settings now shows how big the Trash Folder is.

**Tidier defaults.** Studio Hub keeps **20 GB** of finished work for **3 days** and wants **5 GB** of the
disk left free (it used to be 50 GB, 30 days and 1 GB). A Mac that was still on the old numbers picks up
the new ones on its own; any number you changed yourself stays as you set it. The 20 GB counts only
your current work, not the Trash Folder.

**Updates clean up after themselves.** When an update replaces the app, the old copy goes into the Trash
Folder instead of being deleted, so you can go back for 15 days. The downloaded update files, which
used to stay on the disk for good (about 1.9 GB each), move to the Trash Folder once an update has
installed.

**Retired models stay retired.** There is now one list of models we no longer offer. A model on it is
never shown, never put onto a drive kit and never accepted for a job, even if an old copy of its files
is on a drive. ACE-Step 1.5 is on the list.

## 0.6.116 - 2026-09-30

### ACE-Step 1.5 is retired; music is YuE2 and Stable Audio 3

**ACE-Step 1.5 is retired; music is YuE2 and Stable Audio 3.** The Music page now offers two models:
**Stable Audio 3 Medium (1.4B)** for instrumental music, which is the one it opens on, and
**YuE2 3B (8-bit AR + bf16 NAR)** for songs with sung lyrics. ACE-Step 1.5 Turbo is gone from the
model list, from the drive kits, and from the app itself, so the app is about 12 MB smaller and the
model library is about 7.7 GB smaller on a drive.

**Nothing changes for what you already made.** Music you generated with ACE-Step stays where it is.
Models you already copied to a Mac are not touched; the model step moves a model the kit no longer
carries into `_trash` the next time you run it, and never deletes it.

**This is the first small update.** Going from 0.6.115 to 0.6.116 copies the previous version and
downloads only the files that changed.

## 0.6.115 — 2026-09-30

### Work goes to the right Mac, removing a Mac works from both ends, and updates get much smaller

**Jobs only go to Macs that can run them.** Studio Hub now checks that a Mac has enough memory for
the model before it sends it a job. When a Mac frees up, it takes the waiting job that the *fewest*
Macs could run, so your big Macs are kept for the work only they can do. If two jobs are equal, it
prefers the one that uses the model the Mac already has loaded, then the one that has waited longest.
**Send here** still wins on the Mac you chose, and chat and subtitles follow the same order. A Mac
that is busy is asked again within seconds instead of being left alone for half a minute, and the
capacity numbers you see count only the Macs that have enough memory. On a test of your fleet mix
(10 Macs with 8 GB, 5 with 16 GB, 2 with 24 GB, 1 with 32 GB, running 500 image, 200 voice and 100
chat jobs) no Mac sat idle while it could have worked, and each size of Mac was 97-98% busy.

**Removing a Mac works from either side.** Remove a node on the controller and the node leaves the
group within about a minute, or the next time it starts. Leave from the node and the controller
forgets it straight away; if the controller is switched off, the node keeps trying for up to 14 days.
A Mac the controller removed is told so, with the date. A Mac that left on its own can join again.
Machines are shown by their **name** everywhere (the ID appears when you hover), renaming a Mac
updates the controller, and a Mac that has **never connected since it joined** is flagged.

**Updates are much smaller.** Every build now carries a list of its files. A Mac that already has the
previous version copies it and downloads only what changed - for the last update that was 9 files and
13.5 MB instead of a 1.8 GB disk image - and if it cannot prove the result is exact, it falls back to
the full disk image. Nodes fetch the update from their own controller, and **a Controller now installs
an offered update by itself once it has nothing running or waiting**, just as a Node does. The kit
installer keeps the old app until the new one has been checked.

**YuE2 music works again on every Mac** - the decoder was installed but the Hub looked for it in the
wrong place.

**One thing to know:** going from 0.6.114 to 0.6.115 is still a full update, because the older app
cannot do the small kind. The first small update will be 0.6.115 to 0.6.116.

## 0.6.114 — 2026-09-30

### The app now tells you what it already knew

**A waiting job says why it is waiting.** Instead of *"Waiting for a free worker"* it shows the
reason — for example that the model is not downloaded on the Mac it was sent to — and, when it is
trying again, when. Every job row also says **which Mac ran it**, and a **failed job has a Retry
button**.

**The Fleet page tells the truth about each Mac.** A Mac that answers but has no model it can run is
no longer a green tick: each row says how many models are ready, how many need a download and how
many are blocked. Unreachable Macs come first, the page shows *as of* when it last read, a spinner
while it refreshes, and if something could not be read it says *what* — and keeps the last list
instead of going blank. **Send here** is now visible on the create pages ("Sending to … · Clear"),
and cannot be set on a Mac that is off. **Hand over…** asks you to confirm.

**Updates.** *If this Mac is a Node,* it now installs an offered update **by itself when it has
nothing to do** — no running or waiting jobs, no downloads — and then reopens Studio Hub so the
version shown is the new one. A busy Node waits until it is idle. **A Controller still updates only
when you press Install**, but the Connections entry in the sidebar now shows an **Update** badge.
Pressing Install while jobs are running asks first and says how many will stop. Installing no longer
freezes the window.

**Model downloads show progress** (a bar and "3.2 GB of 12 GB"), show the **real reason** when they
fail, and the failed row stays until you dismiss it.

**Storage.** Restore Defaults asks first, and the limits editor shows how much finished work cleanup
would delete with the numbers you typed.

**Smaller:** Overview's "In progress" now includes Video and Subtitle jobs; the sidebar counts the
models this Mac can actually run; "Voice" is not listed twice; an idle Mac polls less often, and a
hidden window does not poll at all.

## 0.6.113 — 2026-09-30

### A machine that is switched off now says so, instead of blaming Studio Hub

Nothing changes on screen in this release. It exists so that the **Fleet page can tell you the
right thing to go and check** when a Mac does not answer.

Two different things look identical when a Mac is unreachable, and they need opposite responses:

- **The Mac is on, but Studio Hub is not running on it.** Then the fix is to start Studio Hub.
- **The Mac is asleep or switched off** (or a firewall is blocking the port). Then there is nothing
  wrong with Studio Hub at all — the Mac needs waking.

This Hub has always told the two apart internally. It just never *said which* in a way the gateway
could read, so the gateway had to guess — and it guessed "Studio Hub", which is wrong for a Mac that
is simply off. That is why eight powered-off Macs could look like a Hub fault.

The reason is now published as its own field, so the Fleet page can say **"Check that Mac is
switched on and awake"** for a Mac that never answered, and **"Start Studio Hub on that Mac"** for
one that answered without its Hub. Nothing else changes, and no work is affected either way.

## 0.6.112 — 2026-09-30

### Nothing changes for you in this one — it fixes two things 0.6.111 got wrong

**You do not need to do anything differently.** This release has no new behaviour and nothing to
learn. It corrects two mistakes inside 0.6.111, both found by going back over that release rather
than by anything going wrong in use.

**One was a check that looked stronger than it was.** The test that makes sure the update file on
each drive is actually runnable had been loosened to accept any `chmod` starting with `+`. That
meant `chmod +r` — which makes the file readable, not runnable — would have passed a test whose
whole purpose is to catch a file you cannot double-click. It now works out the permission properly
instead of matching the first character. The drive file itself was correct the whole time; only the
check was weak.

**The other was a comment that promised more than the code delivers.** The note explaining how long
a job waits for a busy Mac claimed the stall detector was one of the limits on that wait. It is not
— the stall detector is deliberately there to *show* you a quiet job, never to cancel one. The
comment now says plainly what is true: **a job waiting for a busy machine waits until that machine
frees up or you cancel it**, exactly as it did before. Nothing about the waiting changed; the
description of it was just wrong.

**Why bother shipping this?** Because a check that quietly accepts the wrong thing, and a comment
that describes a safety net that is not there, both get trusted later. You asked for the full cycle
on every change, and this is that.

## 0.6.111 — 2026-09-30

### A busy Mac now makes your work wait, instead of failing

You asked the right question about a job that died:

> *Should the work get queued instead of failing outright?*

**Yes. That's what it does now.** If a Mac in your pool is reserved by something else at the moment
your work arrives, the job no longer **fails**. It **waits**, and then it runs — on that Mac if it
frees up, or on a different free Mac if another one opens first.

Before this, the first thing that happened was a failure, and it happened even when the rest of the
pool was sitting idle:

```
WORKER_TERMINAL_ERROR — HTTP 409: local is reserved for other work on this Mac
```

The waiting is deliberate. A reservation is temporary by nature — the work holding it finishes, or it
expires — so it's a wait, not a rejection. **No attempt is used up by waiting**, and nothing is
generated twice.

### Two things are still genuine failures, and still fail loudly

Some refusals aren't temporary, and we won't pretend they are:

| What the Mac said | What happens |
| --- | --- |
| The **model is paused** | Fails — retrying elsewhere won't fix a decision someone made |
| The **Mac or Studio is switched off** | Fails — same reason |

Those stay as errors, with the reason attached.

### The waiting message now tells you what's actually happening

While your work waits for a free Mac, the Hub used to say:

> *"Waiting for a Voice Studio where the selected shared voice is synchronized"*

**— even for an image or video job.** It now says it's waiting for a free machine, and why that
machine isn't available (reserved by other work, or very recently failed). The Voice message still
appears where it belongs: a voice clone that really isn't synchronized yet.

### Nothing changed about the Mac at the other end

The refusal itself — the `409` that says *"this Mac is busy"* — is unchanged and stays that way.
This is a different Hub learning to **wait** instead of **give up**. Your other Macs need nothing.

## 0.6.110 — 2026-09-29

### One file to update a Mac, and it stops copying the app when it doesn't need to

You asked whether it still copies the whole app when the app is already current, just to add a
model. **It did — every time.** About 5 GB copied, the old one deleted, the new one moved in, plus
the password prompt, for a Mac that already had that exact build.

There is now **`Update this Mac.command`** on the kit. Double-click it and it:

- **Compares the two builds first.** If this Mac already has the exact build the kit carries, it
  says so and **skips the copy** — that's the slow part, and there's nothing to gain from it.
- Otherwise installs the app, **then** copies the models, one after the other, without asking you to
  press Return in between.
- If it did have to install, it tells you which it is doing, so a skip never looks like a failure.

**Don't start `1` and `2` at the same time**, though — one file is still the answer there. `1`
replaces the app, and `2` runs using the app it just replaced, so started together `2` would either
find no app or work from files that are being deleted underneath it. The new file does them in the
right order and waits, which is the part that makes it safe.

`1 - Install the app` and `2 - Copy the models` are still there and unchanged, for when you want to
look at one step on its own.

## 0.6.109 — 2026-09-29
## 0.6.109 — 2026-09-29

### The controller no longer comes up unreachable after a restart

This is the one you called a terrible bug, and you were right. Two things you saw were the same
fault.

**Nothing blocked your IP** — there is no blocking, no lockout and no rate limit anywhere in Studio
Hub. What happened is that the Hub reads this Mac's network addresses **once**, at the moment it
starts. After a power cut a headless Mac starts its login items *before* the network is up, so its
own address is not there yet — and Studio Hub read that as "this Mac no longer has that address" and
started on loopback only.

On loopback the Hub answers its own health check perfectly. It said **"Local services ready"** while
no machine on the network could reach it. That is why clicking stop and then start fixed it, and why
it seemed like luck: by then the network was up, and the same reading came back with the address in
it.

**The offline nodes were the same fault.** A controller listening only on loopback cannot check on
its nodes either, so they showed offline — not because they were, but because it could not ask.

Now:

- **The Hub remembers which addresses were missing and restarts itself the moment one appears.** You
  do not have to stop and start anything, and it does not narrow permanently.
- **The message tells the truth.** It used to say this Mac *no longer has* that address, as a fact,
  from one reading. It now says it does not have it **yet**, that this is normal for a minute after a
  restart, and that the services will restart on their own once it arrives.

If you ever do see it, the honest reading is: **the controller is up, and it is not reachable from
the network yet** — not that a node did something wrong and not that an address is banned.

## 0.6.108 — 2026-09-29
## 0.6.108 — 2026-09-29

### A Mac that joined no longer says it hasn't

This is the one you spotted on the other computer: **"not joined a controller" at the top while the
bottom of the same screen said you had joined as a node.**

Both lines were reading the same fact from two different places. The bottom line reads your Mac's
**credential**, which was correct — you had joined. The line above it reads the **controller's
address**, which came back empty, so it said you had not. As a result the dot went grey and the
Connections page had no address to check, which is why looking again showed nothing about your
controller even though the connection was real.

**Your Mac was genuinely joined the whole time.** The re-join was working; only the part that tells
the screen was missing, and it was missing on every restart — so a node that had joined would look
detached until you joined again by hand.

Now the address is restored to the screen along with the rest of the membership, so the badge, the
line, the dot and the Connections page all say the same thing. If your controller is off you will
see that it is off, which is different from not being joined — and it will say which.

## 0.6.107 — 2026-09-29
## 0.6.107 — 2026-09-29

### Breeze TTS 2 is here — and it's the one you picked

You asked for it shipped, so it is. **Breeze TTS 2 4-bit (MLX)** is now a voice model in the Audio
page. It designs a voice from a description you write, clones one from a saved voice, takes a
direction for how a line should be read, and performs things like `(sigh)` and `(laugh)` instead of
reading them out. 24 kHz, English and Chinese.

It joins long scripts at **0.60 s** — the pause you chose by ear — and it is the reason the next
item got fixed.

**One thing to know about cloning with it:** Breeze needs the reference clip **and its exact
transcript**. If you pick a saved voice that has no transcript, it will say so and point you at the
Voices page rather than quietly producing a voice that doesn't match. That's deliberate — a guessed
transcript would make the clone wrong, not just worse.

It wants **16 GB**. The memory it uses while running is small, but one measurement of its peak never
settled, so the floor is the honest one rather than the flattering one.

### Long scripts no longer step up and down in volume

This is a real fix to every voice model, not just Breeze.

A long script is made in sections, one after another, and nothing was telling each section what
level the one before it came out at. On your own 1,200-word Aiden render the sections varied by
**4.9 LU** — audible as the narrator stepping back from the microphone at each paragraph and forward
again at the next.

My first attempt was the obvious one, and it barely helped: 4.9 down to 4.3. The quiet sections were
exactly the ones with tall peaks, so the ceiling stopped them being lifted. The pass that works
brings the same sections to **1.5 LU**, and produces a file measuring **exactly what the sample you
approved measured**. That's what's in now.

It only changes level — the length is untouched — and if the meter can't run for any reason the
script is still joined as before, with a note saying so.

### The voice engine underneath was upgraded, carefully

Breeze only exists in a newer version of the speech engine, so that engine moved forward. Before it
did, it was checked two ways: every call the app makes is unchanged, and the one voice model whose
behaviour genuinely changed was rendered side by side, old against new — **16 out of 16 runs
finished cleanly and passed the quality check on both.** The difference in output is real but small,
and it's smallest on the long-script path the app actually uses.

Two things I could not check and am not going to pretend otherwise: the **larger Qwen clone model
wasn't measured** (its files aren't on this Mac), and the rare failure this change touches didn't
reproduce in either version, so it's bounded rather than settled. Both are written down in the
project record.

## 0.6.106 — 2026-09-29

### A clip that will not play now tells you why, and which Mac has it

You asked what happens when a finished clip will not play, and said "502" is not an answer. You are
right, and it is not the answer you get any more.

The reason is not a fault in the clip. A finished result is kept **on the Mac that made it**, not
inside Studio Hub — the Hub only fetches it from that Mac when you press play. So if that Mac is off,
asleep or disconnected, there is nothing to fetch. Studio Hub now says exactly that, and names the
machine:

> *"This result was made on studio-2 and is stored on that Mac's disk rather than in the Hub, so it
> can only be played while studio-2 is running and connected — and it is not running or not connected
> to this Hub right now. Start that Mac, or wake it, and play this again."*

If the Mac is on but its engine is not answering, it says that instead, because the thing to try is
different. And on a single-Mac setup it points at this app's own services rather than at another
computer.

We did **not** change where results are kept, per your answer — no copies, just a clear reason.

## 0.6.105 — 2026-09-29

### Show in Folder now tells you why when it can't

The button works now — but it was still going grey and saying nothing about it. Its explanation was
in a tooltip, and macOS doesn't show a tooltip on a greyed-out button, so there was nowhere on
screen to read the reason.

It is always clickable now. If the file is on this Mac, Finder opens with it selected. If it isn't,
Studio Hub says so at the top of the page, and it tells you which of two things happened:

- **The clip was made on another Mac** — so there is no file for it here. It names the Mac that has it.
- **The file has been moved or cleaned up** — it says the file is no longer where it was recorded.

Before, both of those got the same sentence, and that sentence was wrong for the second one.

## 0.6.104 — 2026-09-29

### Show in Folder works now — it never could

The button was there on every finished row and it was dead on every finished row, on every Mac,
including the one that made the file. The Hub was storing where each result went and then removing
that field before the app ever saw it, so the button had nothing to point at and stayed grey.

A result this Mac produced now carries its own location, and **Show in Folder** selects that file in
Finder. A job that ran on another Mac still shows a disabled button with the reason in its tooltip,
because there is genuinely no folder on this Mac to open.

### Every row says when it was generated

The rows told you what made a clip and how long it took, but never *when* — so two runs of the same
script looked identical and the only way to date one was to count down from the top. Each row now
carries the date and time the job was filed, in the same style the rest of the app uses. A job the
Hub never stamped shows no date rather than a wrong one.

### The rows are separated, and the player no longer jumps the list

Two things about the same panel. The rows sat directly on the workspace background with nothing
between them, so a long history read as one block; there is a divider between rows now, the same as
the Jobs page has always drawn.

And the player bar above the list used to appear only once you pressed Play on something — which
pushed the whole list down as it appeared and pulled it back up when you stopped, moving the row you
were reading out from under the pointer. The player is now always in place. Before anything is
loaded its controls are greyed out and it says what to do to load a clip.

## 0.6.103 — 2026-09-29

### Every studio's "Recent" list now scrolls to everything

The small list beside each generator — Recent images, Recent audio, and the same on Music, Video and
Subtitle — stopped at five items and would not move. It looked like it was refusing to scroll; in
fact it was only ever given five things to show, so there was nothing below to reach. Every one of
those panels now lists your whole history and scrolls normally.

## 0.6.102 — 2026-09-28

### There is one token to give out, and the screen now says which

You asked to merge the two tokens. The part that was actually costing you time was not the two
values — it was that **nothing told you which one to use**. Connections showed both the same way,
each with its own copy button, and the Join box only said *"not its Hub token"*, which names the one
to avoid instead of the one to take.

Now Connections leads with **“Give this out”** and the Fleet token — that is the one for GatewayKH and
for every Mac that joins — and the Hub token sits below a line under **“Keep this one.”** The Join box
says to copy the token marked *“Give this out”*, and nothing else to choose between.

**Why I did not make them the same secret**, since you should know what you decided: the token you
hand out deliberately cannot administer the Mac you handed it to. If it were the same as your private
key, then GatewayKH and all fifty nodes would each hold a key that can change any Mac's settings,
remove its models and throw machines out of the group. And removing a Mac you have sold would stop
being one click — it would mean changing the token on every Mac you kept, which needs them all
switched on.

Nothing about the tokens themselves changed, so **GatewayKH needs no update** and no Mac needs
re-joining for this.

## 0.6.101 — 2026-09-28

### Joining a controller now uses the token you type

When you joined a Mac to a controller and typed the controller's Fleet token, the app **sent an old
token it was holding instead**, and never sent yours. The controller refused it, correctly, and the
message blamed the token you had just pasted — which was right. That is why neither the Hub token nor
the Fleet token appeared to work: neither was ever sent.

It happened on any Mac that had ever been part of a group before, which is every Mac you are
re-joining. The Join button now sends what is in the box. A Mac that already holds its **own** node
credential still presents that first — it is the one value that survives rotating the group token —
so re-joining after a rotation keeps working exactly as it did.

If you were blocked joining a controller, this is the fix. It needs to be on both Macs: update the
controller and the node.

## 0.6.100 — 2026-09-28

### The video test would have failed on your 16 GB Mac for the wrong reason

The test looks for the model in your home folder as well as on the drive, and it was looking under
the **wrong name**. It knew the older spelling — the one with dots, from a manual download — and not
the name the app actually installs. So on a 16 GB Mac where step 2 had just put the model in place
correctly, the test would have said *"the model is not on this Mac"* and stopped. It now knows both
names, and the app's own spelling is checked first.

### Installing Studio Hub left it switched off

The install step quits Studio Hub so it can replace it — and then never started it again. This was
harmless-looking and wasn't: until you opened it, your Gateway showed every Studio Hub location
unreachable and every model list on every Mac came up empty. It starts the app again now, and waits
until the Hub is genuinely answering before saying it is done.

### The chat lane streams, measured

Chat replies now have a recorded end-to-end streaming run through the Hub, not just an assumption:
real word-by-word output, a proper finish, token counts. **Nobody has typed into the chat page
itself yet** — that is still yours to try, and it is the last thing on the chat list.

### Cleanup worth knowing about

Two SSD copies of the LTX video model were **cut short** (a 1.9 GB file where it should be 9.9 GB) in
each drive's plain model archive. Both now hold the complete file, copied from the checked copy on
the same drive. A stale half-downloaded duplicate in your own home folder was moved to
`~/StudioHubModels/_trash/` rather than deleted.

## 0.6.99 — 2026-09-28

### The video test is now on every SSD, and it was pointing at a broken copy

Two things were wrong with the video test you would run on a 16 GB Mac.

**It was on none of your drives.** It had been put on all four on 27 September, and then our own
drive-cleanup step removed it the next morning — because the list of files a drive is supposed to
carry never learned about it. Every SSD's Technical folder was left with the 8 GB Z-Image test and
the chat-tier test and nothing for video. It is in that list now, so it will be kept and refreshed
like the others instead of quietly cleaned away.

**And the copy it picked was truncated.** The test looked for the model in a second model folder at
the top of each drive — a leftover that nothing checks. On two drives the biggest file in that folder
was cut short at 1.9 GB instead of 9.9 GB, so the test died after 24 seconds saying the model looked
*corrupt*. It is not corrupt: that folder is stale, and the complete model has been sitting in the
drive's proper model library the whole time, checked and reported as 30 of 30 on every drive. The
test now uses that one, and if it ever finds only an unfinished copy it stops and says so instead of
giving you a misleading error.

**What this does not do is answer the 16 GB question.** Every video measurement so far was on the
32 GB Mac Studio, and the video lane has no smaller-machine setting to imitate one with — so a test
run here would measure a pretend limit, not a real Mac. It needs one of your 16 GB machines. It ran
all the way through here on the good copy, so the test itself is known to work; take a drive to a
16 GB Mac and run it.

## 0.6.98 — 2026-09-28

### Your video lane already works — our notes said it didn't

You asked what was wrong with the Hub's video lane. The answer is nothing, and the paperwork was
wrong. The video worker has been live all along: it is running right now, it offers **LTX-2.3 22B**
for both text-to-video and image-to-video, and your Gateway is already showing it to Story Studio
with nothing blocked. It has made real video files — the last two runs both succeeded.

What our notes described as "no worker, no manifest, no contract" was a status list written many
releases ago and never re-read. It is corrected now, with the measurements.

What is genuinely left is one thing that matters to you: **every video test has been on the 32 GB
Mac Studio.** Nobody has run it on a 16 GB Mac — and that is most of your fleet. The test harness
for it exists but was never put on the SSDs, so there is currently no way to run it from a drive.
That is the next honest step for video, not building the lane.

## 0.6.97 — 2026-09-28

### The drive's front page now really does check itself

Each SSD has a front page listing which version of each app it carries. That list was supposed to be
read off the installers sitting on the drive, so it could never name something the drive does not
have. It never actually ran — the check was looking in the wrong place, and quietly gave up every
time while the numbers stayed correct by coincidence. They were right today and would have been
wrong on the next release, which is the worst way for a thing to be broken.

The numbers on all four drives are now read from the installers beside them, and if the check ever
cannot read one, it says so plainly instead of leaving a page that merely looks current.

## 0.6.96 — 2026-09-28

### All four drives now carry the same 30 models

Three of the four drives were missing the Lance video model — or so the paperwork said. In fact all
four had it, and three had an **older copy of one small config file** that failed its integrity
check, taking the video model down with it. That is why those drives reported 28 of 30 models. The
correct file is on every drive now and all four report the same 30, verified on each.

## 0.6.95 — 2026-09-28

### The drive's instructions now match the drive

The sheet that says which build a drive carries was fixed in 0.6.94 — but the install steps a few
lines below still told you to open the *previous* installer, by name, one that had just been moved
to `_trash`. Every filename in those instructions is read from the installer actually sitting in the
folder now, so the steps and the files cannot disagree.

### "2 — Copy the models" uses the app you installed

It used to run the copy using the copy of Studio Hub **on the drive**, which is slower and stops if
you unplug the drive while it works. It uses the app in your Applications folder now, and falls back
to the drive's copy only if you have not installed it yet.

## 0.6.94 — 2026-09-28

### The sheet on the drive can no longer name the wrong versions

The index document on each drive listed the apps and their versions — and two of those three
numbers were typed in by hand, so a drive could carry Story Studio 1.4.235 and GatewayKH 0.2.67
while its own index said 1.4.234 and 0.2.60. They are read from the installers sitting on the drive
now, and the drive-root copy of that document is refreshed from the kit's copy so the two cannot
disagree. If a folder has no installer to read, nothing is written and the reason is printed —
a half-updated index would be worse than a stale one.

## 0.6.93 — 2026-09-28

### The Macs really do follow a promoted controller now

Promoting a Mac to controller and handing over moved every Mac's key but left the new controller
not knowing who its members were, so the group came back with a capacity of one machine. Each Mac
now introduces itself to its new controller as soon as it is told where that controller is. The
promoted Mac is also no longer reported as a machine that "could not be reached" — it was the new
controller, and the result now says so.

## 0.6.92 — 2026-09-28

### Removing a Mac now means it is gone

When you remove a Mac from a group, it stays removed. It cannot re-join itself, not even after a
restart, and the controller says so by name and date if it ever asks again. That matters because you
remove machines to sell them: every Mac in a group now carries its **own** key rather than one shared
key, so removing one machine takes that machine's key away and leaves everyone else's alone. If you
change your mind, re-admit it from Connections and it can join again. Macs you already have joined
keep working — each one picks up its own key the next time it joins.

### Leaving a group actually works now

*Leave this group* used to fail every single time — the controller refused it — while the app
reported success and told you to go and remove the Mac by hand. It works now, and a Mac can only ever
remove itself.

### Replacing a controller no longer means visiting every Mac

Promote one of the Macs to controller, then use **Hand over to…** on the old controller. Every node
in the group is told where the new controller is, so **nobody has to touch the other Macs**. The old
controller keeps its history, stops taking new work, and shows that it handed over and to whom. If a
Mac was switched off at the time, it is named in the result so you know which one needs the manual
route — and if the old controller is already dead, there is no way to tell the nodes automatically,
which the app says plainly rather than failing quietly.

### The Fleet token can be rotated

The group's Fleet token could never be changed, even though it is shown on screen and held by every
Mac — so a leak could not be undone. **Rotate Fleet token** mints a new one and hands it to every
node, and names any Mac it could not reach.

### You can see who has joined

The Connections page now shows each joined Mac's role, studios, version, when it joined and when it
was **last seen**, and it tells you when a controller is refusing a Mac's credential instead of
showing a green light that means nothing.

## 0.6.91 — 2026-09-28

### The drive's version label writes itself now

The sheet on each drive that says which build you have was wrong three times over — it said 0.6.89
while the app beside it was 0.6.90 — because someone had to remember to edit it. It is now stamped
from the app itself as the drive is prepared, so the two cannot disagree.

## 0.6.90 — 2026-09-28

### The kit's own paperwork is now part of the kit

The documents that tell you which version is on a drive were three releases out of date — one of them
said 0.6.58 while the app beside it was newer — because nothing copied them when a drive was prepared.
They are copied now, and the Studio Hub line no longer states a number at all: it points at the kit's
own generated manifest, so it cannot disagree with the app in the same folder.

### The drives carry the whole library now

They had 24 of the 30 models. YuE2 (the new singing model), LTX and its text encoder are on every
drive now, each verified against its published checksums *on the drive itself*, so "Copy the models"
has what the catalogue advertises. Lance went on the drive that already had it — you are not using it,
and 15.6 GB on three more drives would buy nothing.

## 0.6.89 — 2026-09-28

### YuE2 said "not installed" when it was installed

You asked twice, and you were right both times. The files were there and verified; the app was being
told "missing" by the music worker, which had three problems at once — it checked YuE2's package
against ACE-Step's engine name, it looked for YuE2's decoder without the list that names it, and it
described every kind of failure with the same word, "missing". All three are fixed, and the picker
shows YuE2 as installed now. Nothing about the model changed: only what the app was able to say
about it.

## 0.6.88 — 2026-09-28

### The video decisions, written down

**Lance is not being pursued.** You tried its output and the quality is not good enough, so the 16 GB
test this guide was asking you to run is cancelled. It stays installed and offered — nothing was
removed, and its measurements stay published — it is simply not the small video model we are betting
on.

**The canvas you accepted now has its numbers.** LTX-2.3 at **1024×576 true 16:9 with audio** measured
**16.9 GB** of memory at its peak, against the 10 GB the graphics allocator reports on its own. That
is why it needs a 24 GB Mac, and why a 16 GB Mac is refused before anything loads: the weights sit
outside what the smaller number counts.

### If a video run dies with a Metal error, try it once more

The first attempt at that canvas failed after 13 seconds with a Metal "impacting interactivity"
error. That is macOS's graphics watchdog cutting in — the app was holding the graphics chip while the
screen wanted it — not a fault in the app. The same run completed on the second try.

### Six things you could not see, now on the screen

**The buttons hold still.** Every row's buttons were sized by their own label, so the column shifted
as you scrolled. They are one width now, in one column, on every page.

**"Show in Folder" on every finished result.** The question the app could not answer was *where is
it?* Now every row can open Finder on the actual file. A result made on another Mac says why it
cannot instead of opening an empty window.

**Each row says what it was asked for.** Below the model you now see the settings that made it —
steps, guidance, seed for images; length, steps and whether it is instrumental for music; resolution,
ratio, length and whether audio is on for video; the script's length and speed for audio — and for a
clip, its real canvas, frame count and frame rate.

**Video rows show a frame of the clip**, and Preview plays it right there instead of making you save
it first.

**The Audio page has a proper player now**: play/pause, stop, a bar you can drag, the elapsed and
total time, and the speed the clip was made at. Finished audio rows also show how many characters the
script was.

**YuE2 is installed and verified.** It was not missing from your Mac — it was in the folder you
downloaded it to rather than the layout the app checks, and the SSD kits predate it entirely. Both
YuE2 packages are now verified against their published checksums and ready in the Music picker.

## 0.6.86 — 2026-09-27

### A third music model, and the only one that sings a full song

**YuE2 3B** joins ACE-Step 1.5 and Stable Audio 3 in the Music picker. It writes a sung song from
your lyrics and a style, at 48 kHz stereo, up to about six minutes. ACE-Step also sings; Stable
Audio cannot sing at all; this one is the strongest of the three at a vocal over a full arrangement.

### What it costs, measured on this Mac

Three runs, 8-bit: **10.8 GB** of memory for a 24-second song, **11.8 GB** for 36 seconds, **11.9 GB**
for two minutes. The memory barely changes with length — the weights are most of it — which is why
the answer is simple: **a 16 GB Mac is the minimum, 24 GB is comfortable, and an 8 GB Mac cannot run
it at all.** It also takes its time: about three minutes of work for a 36-second song.

### Two things the app will tell you rather than let you discover

**Lyrics are required.** This model sings, and it has no "instrumental" switch — ask for a song
without words and the app says so and points you at the two models that can do it. And **the memory
guard is set from your Mac**: the engine refuses to thrash, and the app tells it how much it can
afford instead of assuming 16 GB.

### The measurement page now lists every model it has numbers for

`docs/model-benchmarks.md` — the one page that answers "what can this Mac run, and what has actually
been run?" — was missing the new music model, and it was missing the smaller video model's
measurements too. Both are on it now, with the run each figure came from named beside it, and every
number in the measured table says its unit.

## 0.6.85 — 2026-09-27

### The instructions said the smaller video model could not run

The smaller video model's runner was adopted a few releases ago — it is pinned, packaged, measured
and offered by the app. Five documents still said it was "not adopted yet", including the sheet that
tells you which model to test on which Mac, which claimed the app offers it nowhere. All five now
say what the app does, with the measured numbers: about **8 GB** of memory in the mode a 16 GB Mac
uses, about **20 GB** on this Mac's faster mode. Nothing about the app's behaviour changed in this
release; it is the paperwork catching up with the app.

## 0.6.84 — 2026-09-27

### The model lists were coming up empty

A test written for the video switch found something worse: the app could not read your Hub's model
catalogue at all. Two things in a video model's published settings broke it — a list of numbers
where the app expected words (`2 s`, `4 s`, `24 fps`), and a field name the app was looking for in
a spelling its reader never produces. Because the catalogue arrives in one piece, one unreadable
model emptied **every** picker: Image, Audio, Chat and Video alike. Both are fixed, and the whole
list of 33 models now reads.

### The small model's sound control is on the Video page

The video page now shows a **switch for every on/off setting the selected model publishes** — for
the big model, that is Audio. Under it is the model's own sentence about what the switch does: it
makes picture and sound in one pass, so leaving sound off does not make the clip cheaper or
quicker, it just writes a file with no audio track. The smaller model publishes no such switch, so
its page shows none, and a setting you never touched is left to the model to decide.

## 0.6.83 — 2026-09-27

### You can ask for a video with no sound

The video screen now has an **audio** switch, on by default. Untick it and the clip is written with
no sound at all. The switch only appears for models that make sound — the smaller model's
text-to-video is silent, so it does not offer a choice it cannot honour.

**One thing to know before you rely on it: a silent video is not a quicker video.** The model
denoises picture and sound in the same pass, so unticking audio does not make generation shorter —
it skips the audio decode and leaves the track out of the file. The lane's own description says so,
so nobody has to guess.

### A true widescreen size

Until now the two widescreen sizes were 448×256 and 704×448 — close to 16:9, not 16:9. There is now
a real one: **1024×576**, exact 16:9, two seconds, measured here at about 9.4 GB of memory and six
minutes for a clip. It comes out clean.

The smaller model was also tried at 16:9 (512×288). It runs, but the picture falls apart at that
width, so it is not offered — that model stays square.

## 0.6.82 — 2026-09-27
## 0.6.82 — 2026-09-27

### The second video model can run now

The smaller video model, Lance 3B (bf16), was listed but not runnable: its runner had not been
adopted into the app. It is now part of the build, with its own runtime, and it has made real clips
on this Mac — including one measured through the app's own video worker. Text-to-video at 256×256:
about three minutes for a two-second clip, and about 8 GB of memory in the mode a 16 GB Mac uses.

### What it needed, and what that changed

The two video models need **different Python versions**, which is why the app now carries two video
runtimes rather than one. The smaller model also needs a small helper file set (4.5 MB) beside its
weights; without it the model would try to reach the internet while loading. Both are packaged and
checked by content, like every other runtime in this app.

### Also fixed

The app used to describe every video model with the big model's sizes and frame rate. The smaller
model has its own — square frames at 12 per second — and now says so. And a memory measurement that
was reported as a few dozen megabytes (it was watching the wrong process) now reports the real
figure.

## 0.6.81 — 2026-09-27

### A video was not the shape the app said it was

The video model works on a grid: both sides of the picture have to be a multiple of 64 pixels, and
the app was offering a size that is not on it. The model quietly rounded the picture down — a clip
asked for as 16:9 came back as 2:1 — while the job still reported the size that was asked for. The
sizes the app offers are now the sizes the model actually produces, and the app's own screens only
offer a shape and a size that go together, so a request it cannot honour is never presented as a
choice.

### The video test can run from the SSD

The Technical folder on the drives now carries a video command: double-click it on any Mac and it
measures one video generation through the app's real worker — how long it took, how much memory it
truly used, and the finished clip's real length and tracks. On a Mac below the model's published
memory tier it reports the refusal, which is that Mac's honest answer rather than a failure. The
same folder's second command measures the older standalone path for comparison, and the README
beside them says which to run and what to send back.

## 0.6.80 — 2026-09-26

### The video tool the app shipped could not write video

The video model finishes its clip and hands the frames and sound to `ffmpeg`. The copy of `ffmpeg`
this app carried was built for audio only — it has no H.264 encoder, which is the format a video
file needs — so the clip was made and then could not be written. The video worker now carries its
own pinned `ffmpeg` (the same one Story Studio uses for its final assembly), and if it ever cannot
find a capable one the app says so before offering the model rather than after a five-minute
generation.

## 0.6.79 — 2026-09-26

### A video job could make the whole clip and then throw it away

The video model hands its finished frames and sound to a small helper program, `ffmpeg`, to write
the MP4 file. This app already includes that helper, but the video worker was not being told where
it was — so the clip was generated in full and then the job failed at the last step. The worker now
looks where the app keeps `ffmpeg`, and if it ever cannot find it the app says so *before* offering
the model, instead of after a five-minute generation.

### A cancelled video job could not be checked

If a video job's outcome was uncertain, pressing the check button answered with a message about
images and could not settle it; the job held its machine until it was discarded. Video has its own
check now.

## 0.6.78 — 2026-09-26

### Video jobs could sit waiting for memory that never came free

The first real video job sent through the whole chain never started: the Hub held it waiting for
16.5 GB of *free* memory, which a healthy 32 GB Mac does not keep aside — macOS uses that memory for
file cache, and it hands it back the moment a program needs it. The wait was for a number that could
not arrive.

The gate is now 8 GB, which still refuses to start work on a Mac that is genuinely full. The rule
that decides whether a Mac can run the big video model at all is unchanged: 24 GB of total memory.

## 0.6.77 — 2026-09-26

### A support file no longer looks like a model

The video model needs a second file — its text encoder — and the app was listing that encoder as if
it were a video model you could pick. It has no clip to make and no memory figure of its own, so it
showed as unavailable for a reason that did not apply. It is now treated as what it is: a file the
video model loads, not a model in the list.

## 0.6.76 — 2026-09-26

### Video is here

There is a **Video** page in the Create section now, and it is a real Studio: pick the video model,
describe the shot, choose from the sizes and durations that model itself publishes, and generate.
The first model it runs is **LTX-2.3 22B** — the big one, for this Mac and the other 24/32 GB
machines — and it makes a short clip **with its own soundtrack**.

Image-to-video is the other half and it comes from the app that owns your scene images: **Story
Studio** uploads the exact scene and asks this Hub to animate it. Both routes run the same lane on
this Mac.

- **The 22B model needs 24 GB and this build says so before it offers it.** On a 16 GB or 8 GB Mac
  the model is simply not offered, instead of being offered and then failing to load. The measured
  numbers behind that rule are in the changelog: a 4-second, 704×448 clip with audio took about five
  minutes and peaked at a 16.4 GB process footprint on the 32 GB Mac.
- **A smaller model is prepared but not switched on.** **Lance 3B** is the one meant for the 16 GB
  machines. Its download and its memory tier are recorded, but the runner it needs is not part of
  this build yet, so the app lists it honestly as not ready rather than pretending.
- **A clip's length is measured, not guessed.** Each finished clip reports how long it actually runs,
  and the file it produces is checked before it is offered for saving.
- The **Jobs** page now has real video rows, and the sidebar no longer has a "Coming later" section —
  every Studio has an engine.

## 0.6.75 — 2026-09-26

### Installing a new version can no longer half-install it

While installing the previous version, the Mac ran out of disk space part-way through copying the app.
What was left behind looked installed but was not: the app had lost the two files that make it an app
at all, and the automatic undo that should have put the previous version back instead put it *inside*
the broken remains. The installer reported a failure, but nothing could tell you what state you were in.

Three things changed:

- The installer now **refuses to start if there is not enough room**, and it tells you how much it
  needs. It checks before it quits the running app, so a full disk no longer interrupts you just to
  report a problem.
- **The undo can no longer make things worse.** It clears the incomplete copy properly (those runtime
  files are deliberately read-only, which is what defeated the old delete), and it puts the previous
  version back only once the way is clear. If it cannot, it stops and tells you exactly where your
  previous version is, instead of claiming a success.
- **It checks the whole app arrived**, by counting, rather than looking for one file that a partial
  copy also has — which is why a half-copied app previously passed the check.

### The disk stops filling up on its own

Every version you install writes a full copy of the app into the build folder and a disk image beside
it, and the old disk images were moved to `_trash/` — which is on the same disk, so nothing was ever
actually reclaimed. That is what filled the Mac: the build folder alone was holding **100 GB**, 70 GB
of it thirty-two complete copies of past versions, each one rebuildable from the source in a couple of
minutes.

There is now a step that keeps the version you are running, keeps the most recent one, and moves the
rest to one of your SSDs — checking every file against its checksum first, so nothing is removed until
a verified copy exists elsewhere. It does nothing at all if no drive has room.

## 0.6.74 — 2026-09-26

### Voice Studio tidies up only while it is actually running

Voice Studio frees a model that has gone idle, and clears out old generated audio according to your
retention setting. Both of those chores had a flaw with the same cause: they started the moment the
Voice program was *loaded*, rather than the moment it started *running*.

Two things that caused, both now fixed:

- A test run could start those chores too. The idle timer would unload a model that a test had just
  pretended to load, which made a test fail occasionally for no visible reason — it was reported more
  than once as a slow test, and it was never slowness.
- The cleanup timer reads your real output folder, so a test run could have deleted real generated
  audio once it was older than your retention setting. Your actual Voice Studio was never at risk —
  it is told not to run its own cleanup, because the Hub does that — but a test was not.

Neither chore starts until Voice Studio is genuinely serving, which is how the Music and Subtitle
parts of the Hub have always done it.

Nothing about your settings, your voices or your generated files changes, and idle unloading and
cleanup behave exactly as before while the app is running.

## 0.6.73 — 2026-09-26

### The Chat page gets its margin back

The box you type into sat flush against the top and left of the window, while everything below it —
the model picker, the buttons — was properly inset. Chat was the only one of the five Studio pages
without that outer margin; Image, Audio, Subtitle and Music all had it.

That is fixed, and the test that should have caught it now does: it checks the margin on all five
pages. Every other check in that test passed while this was wrong, which is why nobody noticed.

### The video runner stops wasting runs

Two things went wrong while testing video generation, and both cost a full attempt before anyone
could see why.

- It picked up **a half-finished copy of the model** sitting on the internal disk instead of the
  complete one on the drive. The error it produced said the model might be corrupt, which sent us
  looking in the wrong place. It now prefers the complete copy, and says so up front if the only copy
  it can find is unfinished.
- It turned on a memory-saving mode that **this particular model does not support**. The mode fails
  immediately rather than saving anything. It is off by default now — the model's own configuration
  is the one that works.

### What the video test actually measured

One 4-second clip with sound, on this 32 GB Mac:

| | |
|---|---|
| Time | **5 minutes** |
| Picture | 704×448, 4 seconds, with audio |
| Peak memory | **16.4 GB** |
| Swap used | 3.9 GB |

**Worth knowing plainly: 16.4 GB on a 32 GB machine is more than a 16 GB Mac has in total.** That
does not prove it cannot run there — this was measured on the big machine, where nothing was forcing
it to economise — but the "fits in 16 GB" claim is not supported yet, and the next test that matters
is on one of the 16 GB Macs rather than another one here.

## 0.6.72 — 2026-09-26

### The app can now say *whose* number a limit is

Every model publishes an output limit, and the question that matters is where it came from: the
model's own files, or somebody's sensible fallback. This Hub has always written the answer down in a
sentence — *"worker default: the checkpoint ships no generation_config.json"* — but a sentence is for
a person to read, and the piece of software that shows it to you cannot act on one.

So the gateway in between had to decide for itself, and it decided wrong: it described **Qwen3.5-9B's
16,384-token ceiling as a number the model stated**, when 16,384 is this Hub's own fallback for a
checkpoint that declares nothing. If you had seen that number labelled as the model's own, it would
have been this.

Each limit now says what kind of number it is in a form software can read, so the gateway passes it
along untouched and what reaches the screen is the truth: the checkpoint said it, this Hub packaged
it, or this Hub fell back to it.

Everything else in this release is the same Hub as 0.6.71.

## 0.6.71 — 2026-09-26

### A job that has gone quiet now says so on the page

When a model stops reporting in the middle of a job, the Hub has been able to see it since 0.6.70 —
it notices silence in about two minutes, however long that job's ceiling is. That is the whole reason
a thirty-minute deadline is safe: the deadline can be generous about honest work while the silence
check stays impatient.

**But you could not see it.** A job that had stopped reporting looked exactly like one that was
working, because the app was not reading the number the Hub was already publishing.

Now the Jobs row says it, right under the progress bar:

> **No progress for 4 min 12 s — still held, not cancelled**

Two things worth knowing about that sentence.

- **It never cancels anything.** A quiet job is very often just a slow one — a long render, a model
  still loading. The Hub's rule is that a stall is *shown* and never acted on, so a job that has gone
  quiet keeps running and nothing is thrown away on the strength of this.
- **It tells you whether to keep waiting or to go and look.** Two minutes of silence on a long video
  is ordinary; twenty is not. Now you can tell the difference without watching a bar that has not
  moved.

Everything else in this release is the same Hub as 0.6.70.

## 0.6.70 — 2026-09-26

### Long jobs stop being cut off, and a stuck one stops being invisible

Video clips take a long time to make — fifteen minutes to three quarters of an hour on these Macs —
and the Hub used to give every job the same fifteen-minute ceiling. So a video would be cut off
part-way through with nothing to show for it.

The ceiling is now set per kind of work. **Video gets two hours, chat gets thirty minutes, and
images, voice, subtitles and music keep fifteen** — so a stuck subtitle job is still noticed quickly
instead of hiding behind a long video's allowance.

Chat also gets a bigger output budget: **16,384 tokens instead of 4,096**, in the same change,
because the two numbers are one ceiling and moving either alone would break the other.

### If a job goes quiet, you can now see it

A Mac that hangs or drops off the network mid-job used to leave you waiting with no way to tell a
slow job from a dead one. The app now notices **silence** — a job that is running while its Mac has
stopped reporting — and marks how long it has been quiet. **It never cancels the job**, because a job
that is merely slow is still work you asked for; it tells you, so you can decide.

### Long narrations are no longer cut off

A narration part of about seventeen minutes was being stopped part-way through, because the voice studio shared the same fifteen-minute ceiling as everything else. **Voice now gets thirty minutes**, so long-form narration finishes. It matches chat, which also got thirty minutes in this release.

### Z-Image-Turbo now runs on the 8 GB Macs

The 8 GB machines were previously told Z-Image was too big. They were wrong about it, and now it has
been measured: on an 8 GB M1, Z-Image-Turbo produces a 1024x1024 image in about six and a half
minutes and a 1280x720 one in about five and a half, in low-memory mode.

**Expect it to use some disk paging at that size, and expect those times.** The bigger Macs remain
the comfortable place to run it, and the app still recommends 16 GB.

## 0.6.69 — 2026-09-26

### The Audio page can tell you when a model is too big for this Mac

Every other page already did — Image, Music, Subtitle and Chat all warn you when a model asks for
more memory than the Mac has, and then let you try anyway. Audio was the one that said nothing.

Fixing that turned up why it *couldn't* have said anything: the part of the app that works out how
much memory your Mac has **had never worked**. It asked for `sysctl` by name, and on macOS that tool
isn't somewhere the app looks — so it got nothing back, quietly, and every Audio model has been
recorded as "memory: unknown" the whole time. It reads your Mac properly now.

So on the Audio page you'll see an orange note under the model when it wants more than this Mac has
— the same note the other pages show, in the same place. It's advice: you can still press Generate.

### Two smaller things

The web picker inside the app now finds every model's ready-made voices, not just one family's — so
a future voice model's voices will show up without anyone editing a list. And the app no longer
decides whether a cloning model needs a transcript from its family name; it reads what the model
itself says, which is what the two models that need one already publish.

## 0.6.68 — 2026-09-26

### The image engine has been wrong since 0.6.63, and your 8 GB run is what caught it

Z-Image was measured and approved on **mflux 0.20.0**. Releases 0.6.63 through 0.6.67 shipped
**mflux 0.17.5** instead — so every image timing you've been shown (Z-Image's ~166 s and ~236 s, the
memory peaks, the "measured on mflux 0.20.0" note) was describing an engine your app didn't have.

It happened because the release instructions pointed at the wrong folder — one that was correct when
they were written, and was never re-checked after a newer engine was built. I followed that line for
every release I cut. This build ships the right engine again.

Your 8 GB Z-Image run refused because of it, and the refusal was right. Its advice, though, was
wrong — it told you to install the kit's app because that carried 0.20.0, and the kit carried 0.17.5
too. So you'd have reinstalled and been refused again. That advice is now replaced with a command
that shows you what your app actually has, before the run decides anything.

**Nothing has to be re-measured.** The numbers were taken on 0.20.0 and this build now contains
0.20.0, so they describe what you're running again.

### The 8 GB Z-Image run is one double-click now

It was a `.sh`, which Finder opens in a text editor — the same thing that happened with the chat
tier runner. There's now **`RUN-8GB-Z-IMAGE.command`** on every drive beside it. Double-click that
one.

### The Technical folder on your drives has been tidied

You were right that it was a mess: **29 files where 9 belong**, including three that were exact
duplicates of each other under different names (two copies of the Z-Image runner, two of its
readme, two of the launch checker). They're leftovers from older kit layouts that nothing wrote and
nothing cleaned up.

They've been moved — not deleted — to a `_trash` folder on each drive, and the kit now maintains
that folder itself, so it can't fill up again. What's left in `Technical/` is the nine files the kit
actually uses.

## 0.6.67 — 2026-09-26

### A picture's timing was wrong, and so was the machine it was measured on

The Image page told you how long FLUX.2 Klein 4-bit takes: about **79 seconds**. That number was
invented — not deliberately, but it was sitting in the app with nothing behind it, and no record
anywhere had it.

The real run was found in your own files: **105.9 seconds**, and it turns out it was measured on a
**16 GB M4**, not on the big Studio. So the honest number is a bit slower than you were told, and
it's a better number — it's what the machine you'd actually generate on does, not the fastest Mac
you own.

### A limit nothing could see

Qwen3-TTS had a *"stop at N seconds"* limit that worked — it genuinely capped the audio — but it was
invisible: nothing published it, so nothing could find it. It's published now, for the Qwen models
that honour it. Nothing about how it behaves changed.

### Nothing you'd notice

Two housekeeping fixes: a rule about your per-model defaults now has a test instead of a comment, and
a test that wasn't actually running (the test runner silently skipped it) now runs. Neither changes
what you see.

## 0.6.66 — 2026-09-25

### The tab was hiding the voices

You were right that it was dodgy, and it was worse than fiddly: **the two controls were reading each
other in a circle.** The voice tabs (Clone / Ready-Made / Design) were the tabs *your selected model*
supports, and the model list showed only models that support *the tab you are on*. So a model that
couldn't do the mode you wanted also hid every model that could.

That is why Khmer TTS was invisible. It was installed, it works, and there was no Ready-Made tab to
press and nothing on screen saying a preset voice existed on your own machine — because the model you
happened to have selected only knew how to clone.

**The tabs are now what your Mac can do**, across all its voice models. Installing a preset model
brings the Ready-Made tab with it; a Mac that can only clone shows one tab, which is the truth.
Pressing a tab moves you to a model that can actually do it.

### Your page opened on a broken model

The Audio page was opening on **VoxCPM2 4-bit**, which is not installed on that Mac — its folder is
gone while its install record still said it was there. That's why Generate was greyed out with an
error under the model before you touched anything. It now opens on a model that can actually run.

If you deliberately pick a model you're about to download, it stays picked — a refresh won't take it
away from you.

### `[Errno 2] No such file or directory: 'voxcpm2-4bit'`

That string was shown to you twice on that page, and it should never have reached a screen. When a
model simply isn't installed, the app was reporting it as *damaged* and passing Python's own error
text straight through. Six of your models were labelled that way.

A model that isn't there is now just **not installed**, with an instruction you can act on —
download it in Models, or copy its folder in. (It also used to say "press Verify Files", which
can't conjure files that were never downloaded.)

### Ready-made voices can have a default now

Last release made the Models tab's defaults actually reach the engine for pace and word timings.
The **voice** had the same problem — the page always sent a name, so a voice you set in Models was
overridden — and it's fixed the same way. The voice picker now has a **Model default** row at the
top: leave it there and the model uses the voice you stored; pick one and that's what you hear.

## 0.6.65 — 2026-09-25

### The default is a default now

You said it plainly: set a default in **Models**, but the control on the page is yours and wins —
and anything you can change has to actually be given to you. That is now how every generator works,
and it fixed three things that were quietly ignoring you.

**Speed, on the Audio page.** You could set a speaking pace per model in Models and it did nothing
at all — the page was sending its own `1.0` on every job, so your value never left the app. Now the
slider sends nothing until you move it. Leave it alone and the model uses **your** default. Move it
and that is what you hear.

The slider's ends are no longer numbers written into the app: they are the model's own, so a model
with a narrower range gets a slider that matches it instead of one the engine silently clamps. And a
model with no pace setting now shows no slider, instead of one that did nothing. There is a
**Model default** button to put it back.

**Word timings, on the Subtitle page.** This one had no control anywhere in the app at all — it
stored `off` and sent `off` on every transcription, so a word-timings default set in Models could
never arrive, and you had no way to turn it on from the page either. There is now a **Word timings**
switch, and it only appears for models that actually produce them.

**The weight format line** used to guess from file names — a sharded model showed up as
"index, safetensors", which is not a format. It now shows what the package itself states, and lists
the weight files separately underneath, because which files were found is real information.

**One small thing you would have hit eventually:** the Audio page was sending a language code to
almost every voice model, including the Khmer VITS voices that take no language at all — 12 of your
13 voice families throw that field away. The box now only appears for a model that reads one
(Chatterbox), where it is required and says so.

### If you had set speed to exactly 1.0

Then it reads as "not chosen" now, so your Models default works. If you deliberately set 1.0
against a model whose default is something else, move the slider once to put it back.

## 0.6.64 — 2026-09-25

### Your Macs can reach each other again

None of your nodes could be reached by the controller. The joins had worked — the controller knew
all of them — but each Mac was answering only on an address it had years ago, or on nothing but
itself. **A Mac now answers on whatever networks it is actually on, every time it starts**: the
local one and the Tailscale one, added automatically. Nothing to tick, and nothing you set by hand
is ever removed.

This is the fix for the thing you kept having to remember. A new Mac always did the right thing;
the problem was that the moment *anything* was saved, that list was frozen — so a Mac set up
before it joined your tailnet stayed on a dead address forever.

You can also now see the truth from outside: every Mac reports **what it is actually listening
on**, so "bound to loopback only" is a fact you can read rather than a job that never arrives.

### One command to set a Mac up properly

If you would rather be explicit, or you are setting up from a script:

| Command | What it does |
|---|---|
| `--listen-auto` | Answer on this Mac's own networks, and keep doing it |
| `--listen-host 127.0.0.1` | Answer on exactly these addresses |
| `--fleet-mode` | The above, plus start at login **and start the Hub at login** — for a Mac that must be ready after a reboot without anyone opening it |

Each one is remembered, so it is a one-time step per Mac.

### The Models page, built around what each Studio actually does

**Models is now split by Studio** instead of one long list, because an LLM's context window, a
TTS model's word chunks, an image model's steps and ratios, and a music model's length are not the
same kind of setting. Search still works across all of them.

Each model now shows its own facts, and where each fact came from:

- **Chat** — the context window and the file that states it, and the output limit **marked as the
  worker's own** rather than the model's, because this checkpoint ships no file that states one.
- **Audio** — the **word chunks** you could not edit before: `section_max_characters`, its real
  range, the hard cap, the pause between sections, and how speed is delivered.
- **Image** — the size table with the actual pixels per ratio, which tier the model is qualified
  at, and whether it ignores guidance (Z-Image does).
- **Music** — the song lengths it accepts, with the one that renders in sections marked.
- **Subtitle** — languages, timestamps, weight format.

And **you can set your own defaults for each model** — steps and size for one image model, tokens
and temperature for one chat model, section size and pace for one voice model — **per model, not
per Studio**, because each model is weighted differently. A value you set is used when a request
does not name one, it is remembered per Mac, and "Restore This Model's Defaults" puts it back.
Nothing a Studio does not accept is offered as a control.

### Fixes you won't see

- **Chat's memory default** and its tokens-per-second readout landed in the last build; this one
  adds the per-model defaults behind them.
- **Two things that were described in prose are now facts**: an image model saying "I ignore this
  setting" publishes a flag rather than a sentence a program had to match, and Subtitle publishes
  its weight format instead of leaving the app to work it out from file names.
- **A promise we broke**: the previous handoff described this build's fixes as though the running
  app already had them. It didn't. There is now a rule against that — a document names the build
  that has the behaviour — and the handoff says so at the top.

### You can generate on a Mac again while it is a node

If you switched a Mac to **node** and then found you could not make anything on it, that was a real
blocker and it is fixed: the Hub refused **every** job you submitted from that Mac's own window,
because a rule meant to stop a *customer* owning a node's work could not tell the difference
between a customer and you sitting at the machine. Your own Mac now always runs your own work; work
arriving from a controller is refused exactly as before.

### Still yours to do

The **8 GB Z-Image-Turbo 6B MLX 4-bit run**: your manual test proved it works (~5 minutes, against
166–236 s on the 32 GB Mac). The script on the drive is what records the peak memory and swap that
decide whether 8 GB becomes a *proven* tier. It is in `Technical/8gb-z-image-run.sh`.

## 0.6.63 — 2026-09-25

### Image gets the memory control you asked for

The Image page now has **Memory: Auto / Low RAM / Full**, and it is the real low-RAM lever rather
than a label. On **Auto**, each model decides from this Mac's memory — Z-Image-Turbo 6B MLX 4-bit
runs its bounded configuration below 24 GB and its full one at or above it. **Low RAM** forces the
bounded configuration on any model that has one, which is how you tell Image to stay small on the
32 GB Mac so Audio or Chat can keep a model loaded instead. **Full** forces the big configuration
back.

Each option says what it costs, in the model's own measured numbers: about **7.1 GB peak instead
of 17.7 GB**, and about **236 s instead of 166 s** for a 1280×720 nine-step image on the machine
it was measured on. FLUX.2 Klein 4B MLX 4-bit has no bounded mode at all — that is deliberate and
it says so on the page instead of showing you a control that would do nothing.

This is a **loading** setting and it lives on the Image page. The memory setting in Settings is a
different question — when a held model is let go — and Image's answer there is still the one
sentence it was.

### Auto — one memory setting that is right on every Mac

Every Studio's memory setting now has a fifth choice, **Auto**, and it is the new default:

- **16 GB and above:** hold the model for **10 idle minutes**, then release it.
- **8 GB:** release as soon as each job finishes.

That is the whole rule, and it means Chat and Music stop throwing their model away after every job
on the Macs that can afford to keep it — no more ~12-second reload before every Chat reply on your
16 GB and larger machines — while the two 8 GB Macs keep behaving exactly as they do today. The
setting tells you what it did: on Auto it reads *"Follows this Mac: releases after 10 idle
minutes here"*.

Nothing here can interrupt work. A held model is never released while a job is queued or running,
and if another Studio on that Mac needs the memory before it can start, the Hub already asks the
idle Studios to let go first — that is the *"unless another request is in"* part.

### Chat is now work you can see — and measure

**A Chat send is recorded as a job.** That means the Jobs page has a **Chat** tab listing every
question you asked and every answer, with the model, how long it took, and Copy Answer / Save….
Before this, a Chat send left no record anywhere.

**Chat rows show tokens per second.** You asked for a way to measure generation speed, and that
needs the model's own generation time rather than the whole round trip — a cold reply includes the
model load, so a rate taken from the round trip would have been wrong on exactly the turn you were
measuring. The worker now states its own generation time, and the row shows e.g.
`10 tokens · 1.4 tok/s · 7.2 s`. When the worker does not state a time, no rate is shown — there is
no invented number.

The Jobs page also has a **Video** tab now. It lists nothing and says so: Video is not in this
build yet, and an empty tab that explains itself is better than no tab at all.

Chat also ends with the same *"Model installation and storage are managed centrally"* line and
**Manage Chat Models** button the other four Studios have. And the message you send appears in the
transcript **immediately** now, instead of after the reply arrives — a slow reply used to look like
a dead button, and a failed one said nothing at all.

### 8 GB Chat now warns you, exactly like Image

On an 8 GB Mac, Chat tells you the model is qualified at 16 GB and that it will swap or fail,
especially with another Studio holding a model. It still lets you try — it is advice, not a wall,
the same as Image.

### Times always show their seconds

You asked for this and it was only half done. The run time was fixed last release; the line
directly under it was not. A 2 min 5 s clip read **"2.1 min"** on the row above **"2 min 5 s"**,
and a saved voice clip of 90 seconds read "1.5 min". Every duration in the app now goes through one
formatter that always states the seconds: `59.4 s`, `1 min 0 s`, `2 min 5 s`, `2 h 7 min 3 s`.

### Whisper small and base are actually usable now

They were listed as "catalog only" for the wrong reason. The Subtitle worker was supposed to reuse
the Voice Studio's snapshot builder — the one that can convert the `weights.npz` these two models
ship — and a one-word path error meant it never did in any installed build. The fallback kept the
catalogue looking healthy, so nobody could see it. Fixed and measured end to end: both models now
build a loadable folder, and a real transcription comes back correctly.

### The copy-models step now cleans up after a model is cut

`2 - Copy the models.command` still copies and updates exactly as before, and it is still safe to
run again on a Mac that is already set up. What is new is that it will also **offer to remove
models this kit no longer offers** — an old model you have stopped shipping just sits in
`~/StudioHubModels` forever otherwise. Nothing is deleted: they are **moved to
`~/StudioHubModels/_trash/`**, the list is shown before anything moves, and emptying that folder is
your call.

Two things it deliberately will not touch: the **Hugging Face cache** that lives inside the same
folder, and anything you put there yourself — including your **Yue2** download.

### Z-Image-Turbo 6B MLX 4-bit is now carried on 8 GB Macs

You asked for the override, so the copy step now carries it on 8 GB machines even though its
manifest's tier is 16 GB. It is recorded differently from FLUX — FLUX is in the "you have run it
there" list, Z-Image is in a new "you asked to try it there" list — because the 8 GB measurement is
still outstanding. The app still warns you it is below tier and still lets you run it, in low-RAM
mode.

### Fixes you won't see

- **A cancelled request no longer holds a Mac for six hours.** A cancel landed as an exception the
  proxy's cleanup didn't catch, so the machine stayed reserved. What a dropped connection does is
  now declared per route instead of guessed.
- **Chat's engine could wedge after a cancelled answer**, leaving every later question waiting
  behind it. Fixed, and measured: the next reply is served in about a tenth of a second.
- **A question too long for the model is refused, not silently truncated**, and the refusal tells
  you the measured sizes and the model's own limit.
- **Three imports in the Hub pointed at files that do not exist.** One of them made the cloud-model
  check report "nothing matched" when it had checked nothing at all.
- **Something now checks the bundle before it ships**: any module a worker imports that is not in
  the bundle stops the build. Run against the app you have installed today, it finds four.

### Still yours to do

The **8 GB Z-Image-Turbo test** is the one measurement outstanding, and it is now more useful than
ever: it decides whether that override becomes a proven tier. One command, instructions in
`qualification/README-8gb-z-image.md`.

## 0.6.62 — 2026-09-25

### The Clone tab works the moment you open it

You told me the Clone tab showed no voice picker until you went to the Voices page and clicked "Use
voice". That was a real bug and this is the fix: your saved voices now load while the app is
connecting, so the picker is there when you get to it. If the list is ever slow or empty, the tab
now tells you which it is — reading, empty, or unreadable — instead of claiming the engine is
broken.

### One place to add a voice

Cloning now only works from a **saved voice**, which is what you asked for. The "choose a clip file"
picker is gone from the Audio page, along with "Prepare Voice" and "Transcribe Clip". To add a
voice, press **Add Voice…** on the Audio page — it takes you to the Voices page, which is where
voices are added and deleted. Then pick it on Audio and generate. Same on every model.

### The tabs tell the truth

Each voice model now says which kinds of voice work it can do, so Audio offers only those tabs. No
more picking "Design" on a model that can't design. VoxCPM2 offers Clone and Design, the Khmer
voices offer Preset, Pocket TTS offers Clone and Preset.

### Fixes you won't see but would have felt

- **Choosing a voice to clone from no longer refuses.** The last release made some models demand a
  formal audited profile; if your model didn't have one, the button just failed. Every model now
  takes its settings from its own published row instead — VoxCPM2 4-bit was being guessed at 24 kHz
  and its own row says 48 kHz — and anything genuinely unknown is named rather than invented.
- **Two runtime numbers are gone rather than wrong.** We were publishing a couple of generation
  times that only existed in a code comment. If we can't point at the measurement, we don't publish
  the number. The rest now come from stored records.
- **The Hub was mislabelling design support.** It decided by looking for "voicedesign" in the
  model's name, so VoxCPM2 and OmniVoice were advertised without Design, and Pocket TTS was
  advertised with a Preset mode it doesn't offer in that sense. Fixed at the source.
- **The voice test harness could never pass, and now can** — it waited for a startup message the
  engine doesn't print.

### Still yours to do

The **8 GB Z-Image-Turbo test** is ready: one command, instructions in
`qualification/README-8gb-z-image.md`. Send back the block it prints and the 8 GB tier stops being
a guess. It's the last unclaimed measurement in the Image work.

## 0.6.61 — 2026-09-25

### The models that were there all along

If you looked for Khmer TTS in the Audio picker and could not find it, the app was wrong, not your
Mac. Four voice families that ship in every kit — Khmer MMS, Khmer female ONNX, Chatterbox (4-bit
and 8-bit) and Fish Audio (8-bit and bf16) — were being filtered out of the picker by a list inside
the app. Six of the thirteen voice packages were invisible. They are all there now, and the same
check has been applied to every other studio, so nothing that ships can be hidden again.

### Every model you have is now a model you can pick

- **Transcription** no longer insists a model's engine be called "Whisper". Anything the worker
  offers and the kit carries is listed.
- **Image and Music** no longer need a model to publish exactly the controls the first model
  published. A model with its own set of controls appears with its own controls, and anything it
  does not support is simply not sent.
- **Chat** uses the same model picker as the other four studios, and the page no longer names one
  model in its message whatever you have selected.
- **Installers** now check that every runtime the release says it contains is really there, not
  just Voice's — so a half-copied update is refused instead of installed.

### Offline kits really are offline

Pocket TTS 4-bit loads its tokenizer and its built-in voices through a model cache. The kit copied
the package, but nothing put it where the engine looks, so on a Mac that had never downloaded that
model the first generation would have failed — the kit's whole purpose. Installing a model now also
places its companion files where the engine reads them. Verified with the network switched off.

### Smaller, true things

- **Builds are named.** Every studio reports the version it was installed from. Before, four of the
  five answered the word "standalone", so a Mac's build could not be told apart from another's.
- **A failure says what failed.** "Could not reach the node: " with nothing after the colon now
  names what it tried and why. A credential refusal says which credential it wanted and which one
  arrived.
- **Numbers are the model's own.** The Chat page was showing a context window of 1,000,000 tokens
  from a generic default. Qwen3.5-9B 4-bit's own configuration says 262,144, and that is what is
  shown now — along with where each number came from.
- **One licence line, no advice.** Model notes state the licence name where it is known and say
  nothing about what you may do with it.

### For anything you add later

Adding a model no longer needs a change to the app. The catalog and the manifests are the list;
every screen, router and worker reads them. Where something genuinely cannot be derived, it now
fails with the file to edit named in the message instead of quietly leaving the model out.

## 0.6.60 — 2026-09-24

### Z-Image-Turbo on an 8 GB Mac

A fix so the 8 GB Macs can actually try Z-Image-Turbo 4-bit. When it shipped in 0.6.59 it asked
for 8 GB of *free* memory before starting — which an 8 GB Mac can never have, so the job would
have queued forever instead of running. It now asks for 3 GB free, which is a guard against
starting into a nearly-full Mac rather than a disguised "this machine is too small".

8 GB Macs are told the model is above their tier, and it runs in its low-memory mode. Whether it
belongs there is being measured; this release is what makes the measurement possible.

## 0.6.59 — 2026-09-24

### A second image model, and it spells properly

**Z-Image-Turbo** is now in the Image model list beside FLUX.2 Klein. The reason it is there is
text: asked for the title "THE GOLDEN LIGHT OF DREAMS", FLUX writes **"THE GOLEN LIGHT OF
DREAMS"** — and at more steps it gets worse, adding an extra E to DREAMS. Z-Image gets all five
words right.

It is the slower of the two — roughly 3 minutes an image against FLUX's 80 seconds — and it is
much easier on the machine, using about a third of the memory. On a 16 GB Mac the app
automatically runs it in its low-memory mode, which is the configuration that has been measured
to fit.

### What to expect

- Z-Image appears in the Image model list and can be chosen like any other model. Nothing else
  about the Image page changes.
- It runs in a lower-memory mode on smaller Macs automatically. That mode is slower; the speed
  difference is the price of fitting.
- **The 8 GB Macs are not yet measured for this model.** It may work there and it may not — the
  app does not claim either way, and it will tell you if a Mac is below the tier this model has
  been qualified on.

### FLUX.2 Klein under the hood

The image engine was updated to run Z-Image, and FLUX.2 Klein was re-checked on the new version
rather than assumed to be fine. Its pictures are marginally different in the way any engine
update changes them — same scene, same composition, same text — and nothing about how you use it
has changed.

## 0.6.58 — 2026-09-24

### Do not install 0.6.57

0.6.57 broke the app: every model list came up empty and the Hub showed as not answering. The
cause was a single missing entry in a lookup table — the new Chat model was added to the list of
models the app offers, but not to the table that says which worker runs it. Asking the Hub for the
model list then failed outright, and the app treats a failed model list as "the Hub is down".

Nothing was wrong with your Mac, your models or your network, and updating fixes it. If you
already installed 0.6.57, the app would have looked broken the moment it opened.

## 0.6.57 — 2026-09-24

### You can see your pool of Macs, from either end

Connecting a node and then looking for it did not tell you much: the Mac you had just connected
showed as "has not reported in yet" for a few seconds, and the node itself showed nothing about
the group it had just joined — no controller, no other Macs, and a line saying no Mac had joined
*it*, which is not the question a node is asking.

Now both ends show the whole group.

- **On the controller**, every Mac that has joined is listed the moment it joins, whether it is
  awake or asleep. A Mac that is switched off stays in the list, marked as off with the reason,
  instead of quietly disappearing — it is still part of your group.
- **On a node**, Fleet overview lists the controller and every other Mac in the group beside it.
  If the controller is not answering, it says so rather than showing you an empty group.
- **Each row says what the Mac is** — Controller or Node — and where it answers.
- **A node no longer offers "Send here".** A node does not choose where work runs; its controller
  does. The button is still on a controller and on a Mac running on its own.

Joining also records which controller you joined, on the Mac itself rather than only in the app's
settings, so a node can still name its group after a reinstall.

## 0.6.56 — 2026-09-24

### Image generation works again on 8 GB Macs

An 8 GB Mac was showing that FLUX.2 Klein needs a 16 GB Mac and refusing to generate. The
model still runs there — the app was treating the model's *recommended* memory tier as a hard
requirement. It now warns instead of refusing, the same way it already did for every other
below-tier model. 16 GB, 24 GB and 32 GB Macs were never affected.

### The model is named in full again

The Image model reads **FLUX.2 Klein 4B MLX 4-bit** — so you can see the size and the
quantization at a glance in the model list, instead of just "FLUX.2 Klein".

## 0.6.55 — 2026-09-24

### Two Khmer voices, on your Mac

Voice Studio now has two Khmer text-to-speech voices, both working fully offline:

- **Khmer TTS (MMS VITS)** — a clear single speaker at 16 kHz.
- **Khmer TTS Female (VITS/ONNX)** — a female voice at 22.05 kHz.

Both are small (about 332 MB and 115 MB) and run on any Apple Silicon Mac, including the 8 GB
ones. They speak Khmer only, each has one built-in voice, and they do not clone a reference clip —
for cloning, keep using OmniVoice or VoxCPM2. Choose one, type your Khmer text and generate; long
scripts are split and joined for you.

Both voices are already in the model library on every kit drive, so there is nothing to download.

## 0.6.54 — 2026-09-23

### Opening Studio Hub no longer opens a second one

The crash-restart feature added a bug: turning on "Start at login" also started a second copy of
the app while you were already using it, which then complained that it could not open. That is
fixed. Opening Studio Hub when it is already running now simply brings the running app to the
front, with no error.

## 0.6.53 — 2026-09-23

### It comes back by itself, including after a crash

Studio Hub now restarts itself if it crashes, and brings its services up on its own. Closing it
on purpose still keeps it closed - only a crash brings it back. A Mac that is switched back on
returns without anyone opening the app.

### A Mac that starts before its controller

If a node comes up before its controller is ready, it no longer stays out of the group: it keeps
trying - after 15 seconds, 30, a minute, then every five minutes - until the controller answers.
If the controller refuses its token, it says so once instead of retrying in silence.

### A service that will not start keeps trying

It used to give up after ten seconds. It now waits 1, 3, 10, 60 and 120 seconds and then keeps
trying every five minutes, telling you what failed last and when it will try again. Pressing
Start Services starts the ladder over.

## 0.6.52 — 2026-09-23

### It will not install an older version, and it will not re-download

If a controller was holding an older release than the one on your Mac, the app offered it as an
update. It now says so plainly - "Older release available" - and gives you no button, so a stale
controller cannot roll a Mac backwards. A rebuild of the same version is still offered.

And if a 1.2 GB download was interrupted, or the install failed after it finished, the app used
to start again from nothing. It now keeps what it already has, carries on from where it stopped,
and keeps the finished download until an install actually succeeds.

## 0.6.51 — 2026-09-23

### You can now set the key that allows app updates

Under Connections there is a small "App updates" section. It shows the controller's own key
fingerprint, and the key currently trusted for updates - or says plainly that none is installed
and this Mac cannot accept an app release. Paste the gateway's key, press Change, confirm, and
this Mac will accept releases signed by it. The confirmation is there on purpose: this setting
decides which Macs may update this one.

## 0.6.50 — 2026-09-23

### A Mac can now update itself from its controller

Nothing changes by itself: when your controller has no release to offer, the app says nothing.
Once GatewayKH publishes a build, each Mac shows an "Update available" banner on Connections
with the version, the size and who signed it, and an Install button. Installing verifies the
signature and the file, replaces the app the careful way, restarts the services and tells you
what happened.

It is always your click - never automatic, never on a timer - because installing restarts the
services and stops any work in progress. A Mac that has not yet been told which key to trust
says so instead of offering the update.

### A bug in the restart button

The "Restart Services" button added recently did nothing: it was disabled by a check that is
false whenever the services are running, which is exactly when you would press it. Fixed.

## 0.6.49 — 2026-09-23

### The activity page can say why a machine looks quiet

When the Hub could not read a machine's activity it said so in a sentence with no reason
behind it, and the gateway had nothing to act on. It now also publishes a short machine code,
when the condition started, and which fields are missing because of it - so a page can say
"for 12m" and tell a blip from a standing condition.

Underneath that, the real cause is fixed: only the Voice worker was reporting its activity, so
the Image, Subtitle and Music workers looked permanently unavailable and every machine with
more than one studio was reported as partly unreadable. All four report now.

## 0.6.48 — 2026-09-23

### Answering GatewayKH about what this Mac can run

GatewayKH routes Story Studio's work to your Studio Hub Macs, and it could not tell which Mac
could run which model: the Hub was reporting no models against its workers, and no per-model
capacity. That is fixed - the Hub now lists the models each studio can serve, the operations
it supports, and one health row per model per machine saying whether it is ready and, when it
is not, why. A batch also now reports the failed items with their reasons, and a failed item
can be retried without resubmitting the whole batch.

## 0.6.47 — 2026-09-23

### The sidebar numbers were counting the wrong thing

Under the sidebar, the line that says what this Mac offers was counting its *workers* rather than
its *models* - and every Mac runs exactly one worker per studio, so it always read "1" even
beside a voice picker offering eight models. It now counts the models this Mac can actually run,
so it matches what you see when you open the picker.

## 0.6.46 — 2026-09-23

### The sidebar tells you what this Mac is doing

Under the sidebar there is now a pill showing whether this Mac is a **Controller**, a **Node** or
**Standalone**, with a dot that is green when its group is answering. Below it you can see how
many of each generator this Mac is offering - images, voice, subtitles and music - followed by
whether its services are up, and the version you are running.

### The Connections page keeps showing whether you are connected

Joining used to leave only a message at the top of the page, so if you were looking at the join
form you could not tell whether it had worked. There is now a standing line beside the form: a
coloured dot, "Connected" or "Joined, but not answering", and the controller's own reply. The
result of a join is coloured too, instead of grey for both success and failure.

## 0.6.45 — 2026-09-23

### Joining a controller over Tailscale

The last version asked macOS for two network permissions at once, and the second quietly
cancelled the first - so joining a controller at its Tailscale address was refused with a
message about a secure connection, even though the controller was running and answering. The
app now asks for the permission it actually needs. If you were stuck at that message, this is
the fix.

## 0.6.44 — 2026-09-23

### A permissions change could stop Studio Hub starting

If the app's files ended up writable after an install, one of its workers would refuse to
start and the whole app would fail to come up - it showed "Automatic recovery stopped after
repeated service failures", nothing was listening, and no other Mac could connect to it. The
check that caused this was comparing file permissions as well as file contents; it now looks
only at the contents, so a copy or an install cannot break the app this way.

## 0.6.43 — 2026-09-22

### You can see the token, and test the address first

The Fleet token is no longer hidden, so you can check at a glance that you pasted the right
one. And nothing you paste can carry a stray space or newline into the address or the token:
they are removed as the text arrives, which is what made a pasted address look right and
behave wrong.

There is also a new **Test** button beside Join. It asks the controller whether it is there
and reports the answer, without sending your token at all - so if Test fails you know it is
the address or the network, and if Test succeeds but Join fails, the token is the thing to
look at. When a connection fails, the app now shows the failure's code as well as its
description.

## 0.6.42 — 2026-09-22

### Joining is just: the address and the token

You no longer have to type a port, and you no longer have to know the controller's numeric
address. Paste what Tailscale gives you - `100.x.y.z`, `mac.tailnet.ts.net`, even
`mac-mini.local` - with or without `http://`, and the app fills in the Hub's port for you.
If you do include a port, that is used instead.

The app still refuses an address that is not on your own network. It now works that out by
looking the name up, rather than by requiring you to type numbers.

## 0.6.41 — 2026-09-22

### The real reason a join was refused

macOS asks each app for permission to use your local network, and an app that does not ask
properly is simply refused - with a message about security that has nothing to do with your
token or the address you typed. Studio Hub now asks, so the first time it tries to reach
another Mac, macOS shows its normal prompt and the app appears in **System Settings >
Privacy & Security > Local Network**.

If a Mac still refuses after updating, open that setting, make sure **Studio Hub** is switched
on, and try the join again. Because the app is not signed with a developer certificate, macOS
may treat a new version as a new app and ask once more.

## 0.6.40 — 2026-09-22

### Joining a controller actually works now

Joining a Mac to a controller failed with a message about a secure connection - that is
macOS refusing a plain connection, and it had nothing to do with the token or the address
you typed. The app now declares the permission it needs for your own network and your
tailnet, which is what that message was asking for.

Three things that made the failure hard to read are fixed too. If the controller refuses,
the app repeats **the controller's own reason** instead of a bare number. If the Mac
registers but cannot take on the controller's token, it no longer says "Joined" - it says
that step failed, because otherwise that Mac would sit on the controller's list answering
nothing. And the answer to a join, a leave or a role change is now a **banner that stays
until you dismiss it**, instead of a line of small grey text that is easy to miss.

### The Connections page says which group you are in

It now states it plainly, right beside the role - "Joined to 100.x.y.z:47873" or "On its
own, not joined to a controller" - so you can see at a glance what this Mac is doing.

## 0.6.39 — 2026-09-22

### Two Macs can work together

Joining a Mac to a controller did not work in the last builds. The joining Mac offers
every kind of work it can do, and the controller refused the whole list because it did
not know about Music - so no Mac could connect. A Mac that did connect was then shown as
reachable while no work was ever sent to it, and work sent to another Mac could not be
confirmed when the connection blipped. All three are fixed: a controller and its nodes
now exchange real jobs, and a remote image or music job finishes and comes back.

### Your fleet is used the way you would use it

Voice, Music and video now prefer the Mac with the most memory, while images and
subtitles prefer the smaller ones. Your many 8 GB Macs keep working on images and
subtitles, and the 16 GB+ machines stay free for audio. If the preferred Mac is busy the
job simply goes to another one - nothing waits for a particular machine.

### Reachable on Tailscale from the start

A new install now answers on Tailscale and your local network, instead of only on the
Mac itself, so a controller can reach it without you hunting for a setting first. If you
do change which networks a Mac answers on, the switch moves immediately and the app
offers the one press that restarts the services and applies it - you no longer have to
stop everything before you are allowed to change it.

### You can tell one build from another

Every build now shows its own short build number beside the version, at the bottom of
the window and on the Connections page - for example `0.6.39 · 22e3539`. Two copies that
both said 0.6.38 used to be impossible to tell apart when you updated a Mac from a drive.

### The Connections page tells you which token to hand out

It now says in plain words that the **Hub token** is that Mac's own key and should not
leave it, and that the **Fleet token** is the one to give to GatewayKH and to any Mac
that joins - with what each one can and cannot do.

## 0.6.38 — 2026-09-20

### Cloning no longer fails because of how a number is written

Cloning a voice with the Qwen models checks its own work: it transcribes the audio back and compares
the words before handing you the file. That check was comparing *spelling*, and Whisper writes numbers
as digits — so "one two three" came back as "123" and a perfectly good recording was thrown away as
"missing, repeated, or unrelated generated speech". It only showed up on short scripts, which is why
it looked like the model being unreliable: on a nine-word script the same three-word difference is
33% and passes; on a seven-word script it is 43% and fails. Numbers are now compared as numbers, so a
correct recording is accepted however the digits are written, while genuinely wrong audio is still
refused — unrelated speech, a missing half and a repeated tail are all still rejected.

Two smaller causes of the same message are also fixed: the 1.7B 4-bit clone model failed every single
time for a missing internal value (it generated and transcribed fine, then died on a lookup), and a
model's own audit record was being read as a mismatch when it was correct.

### The check now covers our own engine tests

The app's Voice engine has its own suite of 722 tests — how a long script is split, how the speed
control reaches each engine, what the section limits are, how a task survives a restart. The release
gate never ran them: it only looked at the app-level tests, so this suite could fail silently. It runs
in the gate now (about 45 seconds), and it caught real problems the moment it was switched on.

### Music: what we will use, written down

`docs/music-studio-adoption.md` records the answer to the music-generator question: ACE-Step 1.5
(MIT-licensed, ten minutes per request, instrumental built in, Apple-Silicon support from the project
itself), why the old Music Studio's models cannot be reused (its MusicGen weights are
non-commercial-only and thirty seconds long, and nothing was ever downloaded), and exactly what it
takes to add a fourth studio to the Hub.

## 0.6.37 — 2026-09-20

### The model check was asking the wrong question

The tool that proves each local voice model actually produces audio was sending every model the same
request, so the three Qwen tiers — which need three different things (a preset speaker, a written
voice description, and a saved reference voice) — were all failing with a complaint about a missing
parameter. That read as "seven models are broken" when it was the test that was wrong. It now builds
each family's real request, skips the rows this Mac has no package for, and no longer tries to sing
through an image model. Qwen now shows **six of seven passing**; the seventh is a real finding, below.

### The speed control now says when it has been applied

Slowing or speeding a finished recording is a pitch-preserving pass applied after the model renders.
It logged that on one code path and was silent on the other — the one every job from the app actually
takes. So proving that a requested 2× had reached the file meant comparing output durations by hand
(12.44 s at 1×, 6.29 s at 2×). It logs either way now.

## 0.6.36 — 2026-09-20

### A finished job no longer reads as unfinished

A 15,000-character script rendered as six sections, produced a correct thirteen-minute file, and the
job still said **79%**. Nothing was wrong with the audio: the last progress update happened between
two of the Hub's polls, so the Hub kept the value from the second-to-last section. A finished job now
reports finished, whatever the last poll happened to catch.

### The Hub's own regression suite now runs before every release

The Hub keeps a suite of 54 cases covering the things that are hardest to see: a job that must not be
dispatched twice, a Mac that must stay reserved, a model that must stop being fed work, a result that
must survive a lost response. That suite was only ever run by hand — a person had to remember a
separate command, and nothing failed if nobody did. It now runs as part of the release gate, so a
regression there stops the release instead of waiting to be noticed in the app. Two cases were added
with this release, one of them the finished-progress bug above.

## 0.6.35 — 2026-09-20

### How long a script each voice model can actually take

0.6.34 gave every model a section budget. This release replaces the one placeholder in that set with
a measured number, and it settles the question the gateway raised.

Voxtral had no budget of its own, so it started at the conservative shared default. Measuring it —
rendering the same script and transcribing the result back to count the words that survived — gave
two answers that matter:

- A single call still reproduced **every** word at 1,162 characters, and began losing words by 1,685.
  So the loss was real and it started inside a normal paragraph, not at some far-away limit.
- Past **96 seconds** of speech the engine silently drops whatever is left: the 2,463- and
  3,205-character renders both came out at exactly 96.0000 s. The app was never passing this family a
  generation ceiling, so the library's own short default was in force instead of the model's far
  larger one.

Voxtral now sections at **800 characters** — about 36 seconds of speech, comfortably inside the
verified-perfect length and well clear of both the point where words start disappearing and the
96-second cut-off. The measurement script ships with the app's qualification tools so the same
evidence can be produced for any other family.

Nothing else changed. The rest of the budgets were already measured or derived from each engine's own
input limit, and every model still shows its section size, its adjustable range and its engine's cap
on the Models page.

## 0.6.34 — 2026-09-20

### Kokoro works, and the real reason it did not

Kokoro's last failure was a single field name. Every request the app and the gateway send names the
chosen voice `preset_speaker`; one check inside the worker looked only for `voice`, found nothing, and
refused the job *before the model was ever loaded*. It failed three times in five seconds, each time
with "Kokoro preset voice evidence is unavailable", and then the Hub did what it is designed to do: it
paused the model. Every retest after that was refused before it ran, so the same old message kept
coming back no matter what was fixed underneath it.

Both halves are fixed. The voice check accepts either name, and Kokoro now renders and is verified
end to end — the tenth and last of the local models adopted onto this Mac.

### A paused model is no longer a dead end

Pausing is automatic and correct: after repeated failures the Hub stops sending work to a model until
someone says the cause is fixed. But nothing in the app could *unpause* one — the Hub function that
clears a pause existed and was never called by anything, so in practice a pause was permanent and its
reason was invisible. The Models page now marks a paused model, says why the Hub paused it, and offers
Recheck, which re-verifies the model's files, runtime and cached companions and lets it take jobs
again. Pauses for models this build no longer ships are forgotten instead of showing forever.

### Long scripts, per model — the 15,000-character ceiling

Every local voice model now has a section budget, and none can be forgotten: a model with no measured
budget inherits a conservative one instead of being handed the whole script in one request. Seven
families had no budget at all, including Orpheus, which is why the gateway measured a ~170-character
ceiling for it — that is Orpheus's own input limit, and whole scripts were still being sent to it.

- Orpheus sections at 170 characters and Bark at 150: those are the points where the engines stop
  working, published as hard caps, and no setting can raise them.
- Everything else keeps its own measured budget, from OmniVoice's 288 up to Kokoro's 3000.
- A family with no evidence of its own uses 280 characters — the smallest budget any measured family
  uses — and says so.
- Every model now has an adjustable Section size with its default, its range and its engine cap
  visible on the Models page, so how a long script is cut up is a fact you can see rather than infer.

### Speed, for every model

The speed control is now supplied by one rule instead of a hand-kept list. Kokoro and KittenTTS apply
it themselves, because they predict their own durations. Every other engine either ignores the
argument or misreads it — Chatterbox documents speed as "Ignored", LongCat calls it "Unused", Bark
still carries the original "# TODO: adjust speed", and Spark-TTS would raise on any value but its five
fixed levels — so for those the app renders at the model's natural pace and applies one
pitch-preserving pass to the finished audio. Passing a speed that an engine silently discards was
worse than refusing it, because there was no way to tell.

Models page shows, per model, which of the two applies.

## 0.6.33 — 2026-09-19

### Kokoro, by naming the program rather than hoping to find it

Kokoro's speech still failed inside the part of the app that does the actual generating, even though
the same request succeeded when run by hand with everything in place. The difference is that
generation happens in a *separate process*, and that process did not resolve the espeak program from
the search path the way the rest of the app did. The app now hands the program's exact location to the
text-to-speech library instead of relying on it being found.

In plain terms: the app knows where its own espeak is, and now says so.

## 0.6.32 — 2026-09-19

### Kokoro, finished

The last thing Kokoro needed was a small English language model (12.8 MB) for preparing text before
espeak speaks it. Without it the preparation library tried to fetch the model at runtime — and the
app's own folder is read-only, so that attempt failed and the job stopped. The model is now **inside**
the app's runtime rather than in a user folder the app's worker deliberately ignores, so it travels
with the app to every Mac and nothing is downloaded at job time.

That completes Kokoro: espeak's program (vendored, signed, no Homebrew needed), espeak's data, and the
text model are all carried by the app.

## 0.6.31 — 2026-09-19

### Kokoro speaks

Kokoro needed two things this app did not carry, and both are now inside it, so nothing has to be
installed on your Macs:

- **espeak** — the program that turns text into sounds. Vendored into the app's runtime, with its
  libraries rewritten to load from beside themselves and signed so macOS runs them on a Mac that has
  never had Homebrew.
- **a small language model for English text** (12.8 MB) that the text-preparation library needs before
  espeak can be used. Its absence was the actual cause of the silent failure: the preparation library
  exits when it cannot find it, which is why every Kokoro job stopped with no explanation while the
  model itself loaded fine.

## 0.6.30 — 2026-09-19

### Kokoro works: the app now carries its own espeak

Kokoro needs espeak to turn text into sounds, and espeak's default is to call a program that the app
never shipped — so every Kokoro job loaded the model, found the voice, and then stopped with no
explanation. The app now carries that program itself: the espeak-ng binary and its two libraries live
in the app's runtime, with their library references rewritten so they load from beside themselves,
and signed so macOS will run them **on a Mac that has never had Homebrew installed**. Nothing needs to
be installed or downloaded on your Macs.

### F5-TTS has been removed

You don't need it, and its engine isn't part of this app — the Hub reported it as unavailable in the
packaged build, so its files could verify but no job could ever run. Its entry is gone from the
catalogue and the app will no longer offer it.

## 0.6.29 — 2026-09-19

### Kokoro works now — the app had everything it needed

Kokoro failed every job, and it looked like a broken model: the job started, the model loaded, the
voice file was found, and then the pipeline exited with no explanation. The reason is that espeak's
phonemizer looks for an espeak **command** by default, and this app ships no such command — what it
ships is espeak's **library**: `libespeak-ng.dylib` and 400 files of language data, right there in the
app's runtime, unused.

The app now points the phonemizer at the library it already carries. Nothing new was installed or
downloaded; the pieces were present all along.

## 0.6.28 — 2026-09-19

### A job that could not be written down now really is put back

When the Hub hands a job to a Mac it writes that down first. If that write fails — a full disk, an
unreadable database — the job must be returned to the queue and the Mac released, or the job sits
reading as "running" with nothing watching it and the Mac stays reserved for work that will never
happen.

That repair was made two releases ago and **was not actually in force**: the write it wrapped had
since been changed to one that reports failure by returning instead of raising, so the branch that
undoes the dispatch could never run. It runs now, the job returns to the queue with the attempt it
spent given back, the Mac is released, and it waits half a minute before trying again rather than
retrying immediately against a Hub whose disk is still failing.

## 0.6.27 — 2026-09-19

### Three models looked broken because of a field name

The worker has two names for the same thing: some model families ask for a `preset_speaker`, others
for a `voice`. Every client — the app, the gateway, the drive kit — sends `preset_speaker`, because
that is the field the app has always carried. So jobs for Kokoro, orpheus, VibeVoice and Voxtral were
refused with *"needs a preset voice"* while holding a perfectly good one under the other name. The
worker now accepts either, which fixes every client at once instead of teaching each of them a
per-family detail.

With that, **orpheus, VibeVoice and Voxtral generate audio** — verified by running real jobs.

## 0.6.26 — 2026-09-19

### The Hub now says whether a package is a model or a dependency

The list of installed packages described a codec, a tokenizer and a real model identically: same
engine, same "files verified", same ready runtime. Anything reading that list had to guess which was
which — and one guess put a tokenizer in a voice picker. Each row now states it:

- `type: model` — something a person can choose
- `type: dependency` — a package that exists only because another model needs it
- `dependencies: [...]` — what that model requires

The fact comes from the catalogue itself, where every model already lists its companions, so nothing
downstream has to infer it.

## 0.6.25 — 2026-09-19

### Fourteen more models, adopted from what was already on disk

The old launcher's cache is now described to Studio Hub: fourteen models, each with a manifest naming
its exact revision, every file it needs with its size and checksum, and the memory it requires. Five of
them also bring the companion files they depend on, so a model like Chatterbox or MOSS-TTS can finish
loading instead of sitting half-installed.

Nothing was downloaded. All of it was already on this Mac, and three of the models — Echo-TTS and both
Fish Audio S2 Pro builds — declare more memory than this Mac has. They are stored for a bigger Mac and
skipped here automatically: the copy step leaves behind anything that does not fit.

## 0.6.24 — 2026-09-19

### "This Mac cannot run that" is now something you see

The Models page shows what is actually on disk, grouped the way the worker groups it — a model together
with the companions it needs — and flags the ones this Mac cannot hold:

> Storage cache · 16 families on disk · 2 cannot run on this Mac
> ⚠️ Echo-TTS (MLX) needs 24 GB; this Mac has 16 GB
> ⚠️ Fish Audio S2 Pro (MLX) needs 32 GB; this Mac has 16 GB

That comes from the memory each model declares, not from its file size — the 6.7 GB 8-bit Fish model
is in a family whose declared floor is 32 GB, so size alone would have called it fine. This matters
most for models copied in from another install: they are present on disk and would otherwise look
runnable here.

## 0.6.23 — 2026-09-19

### The storage page can now say "this Mac cannot run that"

Every model in the catalogue already declares how much memory it needs. The storage page now joins
each family to its declared floor and reports it against this Mac's memory, so a model that cannot
run here says so instead of looking like any other download:

- fish-audio-s2-pro-bf16 — needs 32 GB, this Mac has 16 GB
- fish-audio-s2-pro-8bit and echo-tts-base — need 24 GB

That is measured, not guessed: file size would have called the 6.7 GB 8-bit model fine.

## 0.6.22 — 2026-09-19

### A rolled-back dispatch is left for the next pass

When a job cannot be written down as it is handed to a Mac, the Hub undoes it — releases the Mac and
puts the job back in the queue with its attempt returned. It then used to try that same Mac again
immediately, which fails the same way and produces a second, differently-broken attempt. It now
leaves the job for the next pass.

## 0.6.21 — 2026-09-19

### A disk that filled up could stop the whole scheduler

While chasing something else I found that the Hub writes its own bookkeeping — which Studio was
tried, why it was refused, a note for you — with no protection. If one of those writes failed (a full
disk, an unreadable database), the failure escaped the scheduler entirely: **no other job could be
dispatched**, and what you would have seen is the Hub appearing to stall rather than anything saying
"this Mac cannot write". Those writes now fail on their own, log why, and the scheduler carries on
with the work it can still do.

This is a fix that came out of the last piece of unfinished work: I was trying to reproduce a
different write-failure bug and the trace pointed here instead. Both are the same family — a durable
write failing somewhere it was assumed to succeed.

## 0.6.20 — 2026-09-19

### A Mac's health is now all of its Studios, not whichever one is listed first

The Fleet page reports one row per Mac, and it was taking that Mac's health from the **first** Studio
it happened to list. So a Mac running Voice and Image, with **Image down**, read as healthy because
Voice was fine — and a Mac with one stubborn Studio could read as down while everything else on it
was working. Nobody is asking "is Voice up" when they look at that row; they are asking whether the
Mac is well.

It now reads **degraded** when part of the Mac is broken, **down** only when nothing on it answers,
and says which Studio is the problem:

> Subtitle is not answering

## 0.6.19 — 2026-09-19

### A job that could not be recorded no longer sticks as "running"

When the Hub hands a job to a Mac it writes that down first, and then starts watching the job. If that
write failed — a full disk, an unreadable database — the job was left reading as **running** with
nothing watching it: it could never finish, never fail, and never be retried, while the Mac stayed
reserved for it and read as busy. The only way out was restarting the Hub. Both are now rolled back —
the job returns to the queue, the Mac is released, and the next pass tries again — and it says so:

> A job could not be recorded when it was handed to a Mac, so it is queued again.

## 0.6.18 — 2026-09-19

### The app stopped re-reading its entire job history every two seconds

Every two seconds the app asked the Hub for the full detail of **every job it had ever recorded** —
one request per job, over and over, for records that could not have changed. On a controller with
months of history that is the app working against itself, and against the Hub, all day.

A job that has finished is now read once and remembered. Before relying on that I checked the fear
that had held this back: that a job you retry could come back to life and be shown as permanently
finished. **The app has no retry button**, and the Hub's retry, remove and clear functions have **no
route** — nothing can re-open a finished job today. The states that *do* come back (an uncertain
transcript that gets re-queued) are not finished states, so they are never remembered. A test now
fails the moment a retry route is added, pointing at the cache, so this cannot rot into hiding a job
you restarted.

## 0.6.17 — 2026-09-19

### The model files loaded are now the ones the job says they are

When a voice model is loaded, the app can have more than one revision of it on disk: the one it
verified and the job records, and an older one left behind in the download cache. The loader used to
take whichever the filesystem happened to list first — so a job could run the **older** files while
writing the **newer** revision into its record. The record is what you'd trust when a result looks
wrong or needs reproducing, and it was describing files that were not used. The verified revision is
now loaded first, and when there is no verified package the newest cached one is chosen
deterministically instead of by chance.

## 0.6.16 — 2026-09-19

### A Mac that was switched off, and a Mac that was told no

The Fleet page had one way of saying "no answer": unreachable. So a node that *replied* and refused
this controller — which happens when a node is re-joined with its own token, or a controller's fleet
token is replaced — looked exactly like a Mac that was switched off. That sends you to the network,
and to the wrong Mac, when the fix is a token. It now says so plainly:

> the node answered but rejected this controller's credential (HTTP 401) — re-join it with the
> current Fleet Token

and it is counted separately from the Macs that are simply not answering.

## 0.6.15 — 2026-09-19

### The Fleet page no longer waits for every silent Mac in turn

Listing the joined Macs asked each one in sequence, and each silent one holds the line until it times
out. So a controller with several switched-off Macs took that many timeouts to answer a single page -
which the app reports as the Hub not answering at all. Every Mac is now asked at the same time, so the
wait is one timeout in total, however many are asleep.

## 0.6.14 — 2026-09-19

### Cancelling a Subtitle job now always cancels it

There was a small window — the moment a job finishes and the files are being written — where pressing
**Cancel** did nothing: the worker had already decided the job was finished, and it recorded "done"
over your cancel. The app then showed a completed job you had cancelled, with its files kept. The
window is the one you are most likely to hit, because it is exactly when a job *looks* finished. A
cancel that lands in it now wins, and the files written at that moment are removed with it.

## 0.6.13 — 2026-09-19

### A downloading model no longer looks stuck

The Downloads row drew a progress bar for anything in progress. For a model the Hub downloads, there
is no percentage to draw — the Hub hands that job to a separate process and only reports whether it
is running, and the app filled the gap with a zero. So the bar sat at **0%** for the entire download,
and the size read **0 bytes** for a 12 GB model: the row looked broken while everything was fine.

It now shows a spinner and the model's own size — *"Downloading in the background · 12.4 GB model"* —
which is the same figure the Models page shows. Downloads whose progress the app *does* know (the ones
a Studio reports) still show their real bar.

## 0.6.12 — 2026-09-19

### The duplicate voice clips were never cleaned up

When a voice is sent to a Studio to clone from, the Hub makes a **duplicate working copy** of that one
audio clip so the worker can read it. Those copies were given a 24-hour deadline, and the deadline was
only ever used to *refuse* the copy afterwards — nothing deleted it. So every duplicate ever made
stayed on disk for the life of the Mac. They are now swept up with everything else.

Your voices are untouched by this and always were: a voice in your library, and a voice shared across
the fleet, is permanent. This is only about the throwaway duplicate of a clip, which the Hub can make
again in a second.

## 0.6.11 — 2026-09-19

### A recording read into memory whole, with no ceiling

Transcribing a recording you upload — as opposed to one this Mac already generated — read the entire
file into the worker's memory before writing it to a temporary file. There was no limit on it at all:
a large enough upload, or a direct caller that ignores the app, could take the machine down by
exhausting memory while it was holding a model. It is now streamed to disk a piece at a time, with the
same per-file ceiling the Hub uses, and a refused upload costs one piece rather than the whole file.

## 0.6.10 — 2026-09-19

### A correction to 0.6.9

0.6.9 changed how Subtitle reads an upload so that an oversized file is refused as it arrives rather
than after it is in memory. That change referred to the size limit on the wrong object, which broke
submitting **any** Subtitle job — it would have failed immediately, and the test suite caught it
before anything was handed to you.

The limit is now read from where it is actually defined, and all 57 Subtitle tests pass. Nothing else
in 0.6.9 changed. If you installed 0.6.9 in the last few minutes, this replaces it; every other
release today is unaffected.

## 0.6.9 — 2026-09-19

### A huge upload can no longer exhaust the Mac's memory

Subtitle's per-file limit is 2 GB. The worker read the **entire** upload into memory first and
checked the size afterwards — so a file that was far too large was held in RAM before being refused,
on a Mac that is usually also holding a model in unified memory. That is an out-of-memory kill rather
than a clean refusal. The body is now read in pieces and refused the moment it passes the limit, so
the cost of refusing is one piece instead of the whole file.

## 0.6.8 — 2026-09-19

### An alert can no longer take the health check down with it

The Hub watches each Studio every few seconds and that watch is what tells the rest of the app
whether a Studio is alive — including whether a job may run at all. Sending a "Studio went down"
alert happened in the middle of that watch, unguarded: if the alert could not be sent (a webhook that
does not answer, an alert log that cannot be written), the exception ended the whole health pass. The
studios kept reporting whatever they last said while the app showed it as current, so a Mac could be
shown as ready while it was gone, or the other way round. Alerts now fail on their own.

## 0.6.7 — 2026-09-19

### A Mac can no longer forget who it is

The small file that holds this Mac's identity — its name, its role, the settings that keep it
contained — was written by overwriting it in place. Overwriting starts by emptying the file, so a
crash, a forced quit, or a disk that filled at the wrong moment left it half-written; the next start
reads that as unreadable and falls back to the defaults, and the Mac quietly lost its identity. It is
now written beside itself and swapped in one step, so either the new settings are there or the old
ones still are, and the file is private to your account (it holds identity, not just preferences).

## 0.6.6 — 2026-09-19

### A slow leak from cancelled image jobs

Cancelling an image job **while it was still waiting** left a small piece of bookkeeping behind, and
nothing ever removed it — the app runs for weeks, so a Mac that had cancelled a few hundred queued
image jobs held all of them in memory for as long as it stayed open. Cancelling a job now cleans up
after itself, whether it was already generating or still in the queue.

## 0.6.5 — 2026-09-19

### The ready-made voices were unreachable

**Voices → Ready-Made was always empty, and nothing said why.** The route that lists the curated
clips was registered *after* the route that fetches one voice by its id, and the framework picks the
first one that matches — so asking for the catalog was answered by "fetch the voice called
`seed-catalog`", which does not exist. The list was never wrong; it was never asked. Fixed, and the
test fails if the order is ever reversed again (checked by putting it back).

## 0.6.4 — 2026-09-19

### The Hub stops re-reading every file several times a second

Before accepting a job the Hub asks whether the storage rule allows it — and it answered that
question by **walking and hashing every managed output folder**. It asked it for every waiting job,
on every scheduling pass, several times a second, on the thread that answers the app. On a Mac with
the full model library and a busy history that is the Hub stalling itself, and it is why the app
could feel unresponsive while nothing was actually wrong.

The answer is now remembered for a few seconds, so a burst of scheduling reuses one reading. Nothing
about enforcement changes: files are still deleted on their own timer and whenever you ask for it,
and each machine's own limits are still asked about separately.

## 0.6.3 — 2026-09-19

### A job that nothing can run is refused, with the reason

If a Studio was not running — Voice, Image or Subtitle — a job sent to the Hub was still **accepted**.
The checks before acceptance covered the model's files, its runtime, your version of macOS, the chip
and the storage budget, and stopped there; nothing asked whether anything was running to do the work.
The job then sat in the queue for ever, shown in the app as accepted work, because dispatch needs a
live Studio and there was none. It is refused now, by name:

> no voice Studio is running on this Mac

or, when a Studio has stopped answering:

> the voice Studio is not answering; restart the local services

A Studio that is registered but has not been health-checked yet (the first moments after the app
starts) still accepts work, so nothing is refused while the checks are warming up. Another machine's
Studio still counts, so a controller with no local Studio can keep sending work to its nodes.

## 0.6.2 — 2026-09-19

### Video files work now

**You can hand Subtitle a screen recording.** The app offered .mp4 and .mov and then always failed:
the transcription decoder reads audio through a library that cannot open those containers, so the
file was accepted, queued, dispatched, and produced nothing — with no way to tell that the file was
the reason. The audio track is now extracted with the ffmpeg that already ships in the app, and the
recording is transcribed like any other file. A file whose audio genuinely cannot be read says so,
in the job, instead of failing silently.

That completes your three answers from the audit — storage now follows your 50 GB / 30 day rule, an
uncertain transcription recovers on its own after 15 minutes instead of holding its Mac, and video
files are transcribed.

## 0.6.1 — 2026-09-19

### Your three rules, two of them built

**Storage: 50 GB or 30 days, whichever comes first — now actually enforced.** The rule existed in the
app all along, at exactly those numbers, and was switched off in the version that ships: the cleanup
that runs it was only started in development. So a Mac left running kept every recording, every
transcript and every generated file until its disk filled and it began refusing work it could not
free. It now runs every five minutes, for every studio's files, and the saved recordings and
artifacts are removed as the rule says. Nothing disappears from **Jobs** — only the files.

**An uncertain transcription no longer holds its Mac.** When a response is lost, the Hub cannot tell
whether the worker is still transcribing, so it fences the item — and nothing ever released it: the
retry, remove and clear paths all refused it, and the Mac that held it was excluded from image, voice
*and* subtitle work, permanently. Now it is re-queued automatically after **15 minutes** and
transcribed again from the original recording, which is what you asked for. Transcripts are cheap to
repeat; a Mac out of the fleet is not.

**Video files: next release.** You approved extracting the audio track with the bundled ffmpeg, so
.mp4 and .mov work instead of always failing. That is a change to the subtitle worker's decoder path
rather than a switch, so it goes in next with its own test rather than at the end of a long day.

## 0.6.0 — 2026-09-19

### Two more from the audit, both silent

- **Correcting a voice's words left the old timings behind.** A long reference clip needs its
  transcript *and* the timings that say which part of the clip each word is in. Editing the words in
  **Voices** rewrote the transcript and kept the timings measured from the old words, so a clone then
  chose its window from timings that no longer matched the audio — a wrong-sounding result with
  nothing on screen to explain it. Correcting the words now drops the timings; **Transcribe Clip**
  measures them again. Renaming a voice, or saving without changing the words, leaves them alone.
- **The Hub kept every job it had ever seen in memory.** Not on disk — those records are kept — but
  the in-memory copy a running job is polled through was never cleared, so a controller left running
  for weeks grew without bound and every status read walked all of it. Finished jobs now leave memory
  once they are a quarter of an hour old, keeping the twenty most recent (they are what the
  "about N seconds" estimate is measured from). Nothing in **Jobs** changes: the listing reads the
  saved record as well.

## 0.5.9 — 2026-09-19

### The app no longer gives up on the whole screen

Two more from the audit, both about one small failure looking like a big one.

- **One unreadable job used to blank everything.** The app refreshes eight things every two seconds,
  and if any one of them failed — a dead Subtitle worker, a finished download the Hub has forgotten,
  one job whose record will not load — it abandoned the entire refresh and marked the Hub
  unreachable. The models, the jobs, the voices and the connections all went to their empty states
  with no explanation, and only pressing something else brought them back. Each piece now stands on
  its own: the Hub is only "unreachable" if the Hub itself does not answer, and anything that could
  not be read is said in one line at the top.
- **A damaged file of the app's own stopped it connecting at all.** If one of the app's saved files
  in Application Support was truncated or in an unexpected shape, starting the app cleared the Hub
  address and left every page on "Choose where your models live" — and it never recovered, because
  the same file was read again on every attempt. The only way out was to find and delete it in
  Finder. A file that will not parse is now set aside (nothing is lost, it is renamed with
  `.corrupt` at the end) and the app starts without it, saying what it did.

## 0.5.8 — 2026-09-19

### Fixes found by auditing the whole app

I went through every part of the app looking for things that are wrong rather than things that look
untidy, and checked each one against the code before touching it. This release is the first batch.

- **A restarted node never told its controller where it was.** The automatic re-join — the thing that
  makes a group survive a restart — silently did nothing on every Mac: the code that re-joins asks
  the Hub what kind of credential this Mac holds, and on the startup path nobody had asked the Hub
  yet, so it always saw "nothing" and stopped. Now fixed, and the test that used to hide it fails
  without the fix.
- **A voice you removed could not be added again.** Removing a shared voice leaves a marker so every
  node drops it, and that marker also refused the voice when it was pushed again — so a re-add
  reported success and installed nowhere. Pushing a voice again now clears the marker, which is what
  pushing it again means.
- **Re-pushing a voice could wipe its words and timings.** The gateway re-pushes a voice whenever it
  is edited, and a push that did not repeat the transcript was treated as "clear it" — which would
  have removed the transcript and the timing from every controller and then every node. A field that
  is not mentioned is now kept; sending an empty one still clears it.
- **Updating the app could leave a Mac with no app at all.** The installer deleted the working copy
  before copying the new one, so cancelling the password prompt left nothing behind. It now copies
  beside the old one and swaps only after the copy is complete and has a runtime in it.
- **Subtitle could not find ffprobe**, so it could not read the duration of a recording — the check
  that keeps transcript timings inside the file was skipped for m4a, aac and webm. The voice worker
  was told where the bundled tools are; the subtitle worker was not.
- **The startup diagnostic always printed a false error**: it passed a flag the Hub does not accept,
  so "see the real error" showed an argument error instead.
- **A test that could not fail.** The test that was supposed to protect the voice-reference key only
  matched a comment in the app's own source; I checked by breaking the key deliberately and the test
  still passed. It now fails, as it should.

## 0.5.7 — 2026-09-19

### A model that cannot run now says why

Somewhere in the app a model would just read **unavailable**, and nothing said what was wrong — so
the only way to guess was to wonder about memory. The Hub knows exactly which of five things is
missing, and checks all of them before it will accept a job: the model's **files**, its **runtime**,
your **version of macOS**, the **chip**, and whether its **Studio is running**. It has always sent
all five. The app was reading one of them and throwing the rest away.

Each reason now appears in orange, where you are looking: under the model list on Audio, Image and
Subtitle, on the Generate button's own explanation, and on the **Models** page. For example:

- *This Mac is on macOS 14.5; this model needs 15.0 or later.*
- *This Mac does not have enough memory for this model.*
- *Its files are not verified. Open Models and press Verify Files.*
- *Its Studio is not running. Restart the local services.*
- *This model needs an Apple Silicon Mac.*

A model that cannot run is also marked in the list itself (*"… — not available here"*) so it is
obvious before you pick it, rather than after you press Generate.

## 0.5.6 — 2026-09-19

### Name your Mac, and one button for settings that need a restart

**You can name this Mac now.** Connections → **Name** → *Rename*: that is what this Mac is called in
the fleet, in Jobs and to the gateway. It is a label, not an identity — renaming it never changes
routing, and never affects a node that has already joined.

**Changing the role, the network addresses or the port needs the services restarted**, and the
controls are switched off until they are — which quietly expected you to know to stop the services,
change the setting, and start them again. Those settings now say so, right above them, with one
button:

> ⟳ This change takes effect when the local services restart.
> **Restart Services** — they stop and start again on their own.

The role picker also explains itself in plain words: **Controller** runs its own group and sends
work to nodes, **Node** is sent work by a controller, **Standalone** is a Mac on its own.

## 0.5.5 — 2026-09-19

### You can see how long a generation takes

Every finished job now says how long it took, in the generator and in **Jobs**: *"Completed · took
52.2 s"*, or *"took 1 min 28 s"* once it is long enough to read that way. A generation still running
says *"running 12 s"* instead, so there is a clock either way.

This is a different number from the one already on the row: **4.6 s** is how long the clip plays
for, and **52.2 s** is how long it took to make. The Hub had been recording both all along; the app
was showing only the first, which is why the app appeared never to mention generation time at all.

## 0.5.4 — 2026-09-19

### The Audio page's controls can be read and clicked now

The **Generation** column on the right of Audio was narrower than the controls inside it, so macOS
cut them off at the column's edge: the tabs read "Design a Voi…", the buttons came out as "Manage
Voice", "Transcribe Cl…", part of every explanation was missing, and the Speed row ran into itself.
The Script box was the other half of the problem — it grew as wide as the window would let it,
which left the controls even less room.

Every workspace now has the same shape as the Image generator: **what you set up on the left, what
you made on the right**, both starting at the top. Audio puts the script and all the voice settings
in that left column with the recent audio on the right; Subtitle puts the file, format, **Transcribe**
and its settings there with the transcript on the right; Image already worked this way. The three
voice tabs say **Clone**, **Ready-Made** and **Design** so they fit, labels sit above their controls
instead of wrapping, and the Models page no longer pushes its note to the foot of the window.

Nothing about generating audio, images or transcripts changed — only where things sit.

## 0.5.3 — 2026-09-19

### A voice added once can now be shared with every Mac in a group

Adding a voice in the gateway can now put it on every Mac its controller owns, instead of copying
the clip from machine to machine. The controller does the distributing; the gateway only ever talks
to controllers, so it is one action per group rather than one per Mac.

A shared voice carries its **timings** as well as its words. That matters more than it sounds: a
long clip cannot be cloned without them, so a voice that arrived without them would look installed
on a hundred Macs and then refuse to clone on every one. The push is refused instead, at the moment
somebody can still fix it.

Two things were broken on the way to a node, and both were found by pushing a real voice to a real
joined Mac rather than by reading the code:

- A controller was not allowed to install a voice on a node at all — the request was refused, so
  nothing would ever have arrived.
- **Connections shows two tokens again, and they are not interchangeable.** *Hub token* is this
  Mac's own, for the gateway and your own tools. *Fleet token* is the one to give a Mac when it
  joins. Handing a node the Hub token would give whoever owns that Mac full authority over the
  controller; the Fleet token can send work and read results and nothing else. The join screen says
  which one it asks for.

## 0.5.2 — 2026-09-19

### A group of Macs now survives a restart

Two things made a fleet of Macs fragile. The Hub picked a **new port every time it started**, so
the address you wrote down for a controller stopped meaning anything the moment it restarted; and
a Mac that had joined one never re-joined on its own, so after a reboot somebody had to retype the
controller's address on every node.

- The Hub answers on the **same port every time** (47873), so an address stays true. If something
  else on that Mac is already using that port, the app says so before starting, instead of failing
  three times and reporting service failures.
- A Mac that has joined a controller **re-joins by itself** whenever its services start, which also
  tells the controller its own address again.
- **Connections** shows the port and the exact address to give a node, so setting up a group is:
  turn on a network address here, copy the token, paste the address and token there.

Both Macs need a network address turned on. A node has to be reachable for a controller to send it
work, and the Join form says so plainly if it is not.

### Leaving a group, and selling a Mac

A Mac that joined a controller now has **Leave this group** under **Connections**. It stops using
the group's token — this Mac mints a fresh one on the spot — and goes back to running on its own.
Use it before you retire, move or sell a Mac: the group's token is a file on that Mac, and without
this it travels with the machine.

Replacing a controller is the same idea from the other side: set the replacement to **Controller**
and it mints its own token, then each node joins it once with the new address and token. The old
machine's token stops working everywhere the moment that happens.

## 0.5.1 — 2026-09-19

### The app starts again, and the models have moved out of Downloads

Starting Studio Hub could wait forever on "Starting local services…", and all the app said was that
the services did not become ready. The reason was where the models were: the setup step put them in
**Downloads**, and macOS treats that folder as private. The first service to read its models was
stopped by macOS without being told why, so it waited inside that request instead of failing - no
error, no timeout, and no clue in the log.

The models now live in **~/StudioHubModels**, and the app starts in about two seconds. If you
already copied models into Downloads, the copy step moves them across the next time it runs, and a
library left in Downloads is still offered as a last resort. This replaces the note in 0.5.0 that
described a library in Downloads as the right place for it. If the app is pointed at a folder
macOS protects - Downloads, Desktop or Documents - it now says so before starting, instead of
waiting.

Nothing else changed.

## 0.5.0 — 2026-09-17

### The Transcribe button works, and you can pick a Whisper model again

Every time you pressed **Transcribe Clip**, the app was asking the Hub for a Studio that does not
exist, so the Hub refused it. The same mistake meant the app never learned which Whisper models
were installed, which is why there was nothing to choose on the Audio page.

Both are fixed. You can now press **Transcribe Clip** and pick **Whisper Turbo**, **Whisper Small**
or **Whisper Base**.

### Qwen3 TTS can actually clone your voice now

Four things were wrong at once, each one hiding the next. Pressing **Prepare Voice** and then
generating produced nothing, with a message about a missing reference or a "worker reference
invalid" error three layers away from the real cause. Fixed:

- The app was sending the reference clip under a name the Hub does not read, so the clip was
  quietly thrown away and the voice job ran with no voice attached.
- The smaller (4-bit) Qwen Base models had no approved reference-audio contract, so they refused
  every clone before it started. On an 8 GB Mac these are the only Qwen Base models that fit.
- When the voice engine refused a reference, the Hub ignored the refusal and ran the job anyway,
  which is why the error you saw never mentioned the real problem. It now shows the engine's own
  reason.
- A transcription of the reference clip is required for Qwen Base. **Transcribe Clip** fills it in
  for you, and now that it works, this step works too.

### Preparing a voice no longer needs timestamps typed by hand

A Qwen Base reference clip longer than 15 seconds needs a *timestamped* transcript so the engine can
choose the best 15 seconds of it. The app transcribed the clip and then threw the timings away, so
a long reference could not be prepared at all.

**Transcribe Clip** now keeps the timings from the same transcription and sends them along with the
reference. You do not have to do anything differently: record or choose a clip, press
**Transcribe Clip**, check the words, then press **Prepare Voice**.

### The app now uses the models you copied

The app was reading a different, older copy of the models, so the three new voice models read as
"not installed" and the Audio page's model list came up empty. It now uses
`Downloads/model-library`, which is the folder **2 - Copy the models** fills in.

If you ever see a model the app calls "not installed" when you know it is there, this is the first
thing to check: open **Settings** and look at **Model library**. It should say
`Downloads/model-library`.

### Studio Hub has an icon

It was shipping without one, so the Dock, the app switcher and the About panel all showed the blank
page macOS uses when a bundle has no artwork. It now has its own: a broadcast source above a
waveform, which is what the app is — a Hub with Studios around it, carrying sound and picture.

### Your finished audio and images open again after a restart

A finished generation could stop opening. Pressing **Play**, **Save** or **Open Transcript** on
something you made earlier gave:

> Local engine http 502: worker artifact reference is invalid

The cause was a stored address. Each time the app starts, its studios get new port numbers, but the
record of a finished job kept the *old* port — so after a restart the app was told to collect the
file from a door that no longer existed. The file itself was always safe on disk.

The app now records where the file is *inside* its studio rather than the full address, and works out
the address fresh each time. Everything you made before this update opens too — no need to re-make
anything.

### You can now change how fast the audio speaks

The engine has always been able to speed audio up or slow it down **without changing the voice** —
it has been doing the long-script splitting and joining all along too, automatically. What was
missing was any way to set the pace from the window.

There is now a **Speed** slider on the Audio page, next to Language: 0.5× to 2×, with **Normal** to
put it back. It applies to every voice — your saved voices, the ready-made ones, and designed ones.

Measured on this Mac:

| Setting | Length of the same sentence |
|---|---|
| 0.5× (slowest) | 12.3 s |
| 1× (normal) | 4.9 s |
| 2× (fastest) | 2.5 s |

### Long scripts already split and rejoin by themselves

Nothing to set. A script longer than the model can speak in one go is split at sentence boundaries,
each part is generated, and the parts are rejoined with a short pause — with the engine's own
cleanup of any silence the model adds at the end of a part. You see it as `section 3/7` progress.

### Whisper Base and Small work again

**Whisper Base** and **Whisper Small** refused to transcribe, saying the model's config was missing
even though it was sitting in the model folder. **Whisper Turbo** was unaffected, which is what
pointed at the cause.

The two broken ones are stored in a form the engine has to convert, and that conversion is cached
in a folder built from links into your model library. When the library moved, every link in that
cached folder broke — and the app kept trusting the folder because it looked finished. It now
checks that the cached copy actually points at real files, and rebuilds it when it does not.

All three Whisper models transcribe correctly now.

### The Subtitle page no longer offers to play a transcript

**Recent subtitles** was showing **Play** and **Save** for transcripts, which are text — and
**Open Transcript**, which did nothing except switch pages and leave the page empty. Both fixed:
the row now offers **Open Transcript** and it actually loads the transcript, so you can read it,
switch between Text / SRT / VTT and export it.

Finished transcripts also stopped carrying an old warning. Every one of them said *"model is not in
the packaged catalog"* — a note from a retry, left on a job that had since completed successfully.

### The Audio page is now three tabs

Choosing a voice is really three different jobs, and they used to share one form whose controls
changed under you depending on the model — with nothing saying why. Now each is a tab:

- **Clone a Voice** — use a voice you saved, or a clip from disk. Yours are all here.
- **Ready-Made Voices** — voices built into the model. Pick one from a list and type your script.
  Nothing to prepare. This needs the **Qwen3-TTS CustomVoice** model.
- **Design a Voice** — describe what you want in words, for example *"a deep, slow and warm
  narrator"*, and the model makes it. This needs the **Qwen3-TTS VoiceDesign** model.

Each tab lists only the models that do that job, and tells you which model to install if you do not
have one yet. The three model tiers that make this possible are now in the kit, so **2 - Copy the
models** can bring them over.

### Every voice plays now, and any of them can be picked in Audio

Two things were wrong, and both are fixed.

**Seven of your ten voices would not play.** They are MP3 files, and the player could not read an
MP3 handed to it in memory — it reported an "OSStatus error -4". Playing them from a file works, so
that is what the app does now. All ten play.

**The Audio page had no way to pick a saved voice.** It only ever offered a clip file, even though
the app keeps a voice library and the engine has always accepted a saved voice by name. So a voice
sitting in **Voices**, ready to clone, could not be used where you generate. The Audio page now
starts with a **Voice** list: choose one of your saved voices and type your script. There is nothing
to prepare — the clip, its words and its timing are already with the voice.

Choosing **Choose a clip file…** instead gives you the old behaviour, with **Transcribe Clip** and
**Prepare Voice** as before.

### Your ten saved voices come with the kit

**2 - Copy the models** now brings your voices too — Christian, Vincent, Leo, Samuel, Todd, Archer,
Aiden, Mark, Leona and Chris. Open the app, go to **Voices**, press **Refresh**, and they are all
there, ready to clone with.

A voice already on a Mac is never overwritten, so anything you have changed there is kept.

Clips longer than about 15 seconds already carry the timing the app needs, so they work straight
away instead of asking you to transcribe them first.

### Copying models keeps them up to date

**2 - Copy the models** is now safe and useful to run again at any time:

- models you do not have yet are copied;
- models that changed on the SSD are replaced in place;
- models that are already correct are left alone, so a second run takes about a second instead of
  several minutes;
- an older version of a model that was just updated is removed, so your Mac does not slowly fill
  up with copies you no longer use;
- the app's record is refreshed as it goes, so an updated model reads as installed instead of
  asking you to verify 29 GB by hand.

### The two setup files now work wherever they sit

**1 - Install the app** and **2 - Copy the models** used to fail when run from the top of the
drive, saying they could not find Studio Hub. Both now work from the top of the drive or from the
Technical folder.

### Opening the app for the first time is one click

The app used to ask you to find your model folder in a file panel. Now, when the models
have already been copied to your Mac, it says so and offers a single button:

> **Use the Models Already on This Mac**

Press it and everything starts. Before this, the only prominent button on a new Mac was
"Use Default Folder" — an empty folder inside the app that would have downloaded all
29 GB again.

### This screen

Open **What's New** from the sidebar, from the app menu, or from the menu bar icon. It opens by
itself the first time you run a new version, and then stays out of the way.

## 0.4.0 — 2026-09-15

- The app runs everything locally: a Hub with Image, Voice and Subtitle studios inside the app.
- Image, Subtitle, Qwen voice cloning and OmniVoice all produce real output.
- Per-page history, plus one Jobs page with a tab per studio.
- Models can be downloaded, imported from a folder, verified, and kept outside the app.
- Settings shows what each studio is holding in memory and lets you release it.
