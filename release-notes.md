# What's new in Studio Hub

Written to be read by the person using the app. Newest first.

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
