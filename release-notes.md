# What's new in Studio Hub

Written to be read by the person using the app. Newest first.

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
