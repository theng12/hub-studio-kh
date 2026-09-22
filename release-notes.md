# What's new in Studio Hub

Written to be read by the person using the app. Newest first.

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
