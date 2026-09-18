# Morning summary — 2026-09-17

Everything below is done, committed and on the SSD. Read the top section first; the rest is
detail you can skip until you need it.

**Build on the SSD now:** `Studio Hub 0.4.0.app`, commit `5cd96818`, `development_build: false`.
Launch-verified from the SSD copy itself. The model library on the SSD was re-synced too.

---

## What was wrong with the models — the real answer

Your copy was correct. The **receipt** was wrong.

The app keeps a record of every model file so it can tell "already verified" from "needs
checking" without rehashing 29 GB on every launch. That record included `st_ino` and `st_dev` —
inode and device numbers. Those describe **one filesystem**. Copy the library anywhere and every
one of them changes, so on your Mac Studio all twelve packages were reported as *"File changed;
verify again"* — eight as `invalid`, and the four Qwen models as `dependency_missing` because
their companion `whisper-turbo` was also being refused.

It took three attempts because every timestamp failed in its own way, and I measured each one
rather than assuming:

| Recorded | Survives a copy? |
| --- | --- |
| `st_ino`, `st_dev` | No — different filesystem |
| `st_ctime_ns` | No — reset by the copy |
| `st_mtime_ns` | No — loses sub-microsecond digits between two APFS volumes |
| `st_size` | **Yes** |

The receipt now records **size and whole-second timestamps**, both of which survive the copy.
Whole seconds still catch a file rewritten later; size catches truncation and a half-finished
copy — the failure that actually happens. Real integrity was never at stake: **Verify** rehashes
every byte, and the Hub hashes every artifact it serves.

**So: the models were never broken, and nothing was lost.**

---

## Everything else you raised, and what I did

**Voice import (your "too lazy to create them").** One command, no clicking:

```sh
sh "/Volumes/KH-STUDIO-ATLAS/Studio Hub App/run-voice-import.sh" "/path/to/your/voices"
```

Add `--dry-run` to see what it would do first. It reads one folder per voice (a clip, plus a
transcript beside it if you have one), builds the library format the app reads, and skips
anything already saved so running it twice is safe. A voice with no transcript is still
imported — marked as needing words rather than thrown away. Verified by importing a fixture and
reading it back with the worker's own code.

**Transcribe on the spot.** Added **Transcribe Clip** in two places: the voice editor when
adding a voice, and beside the reference transcript on the Audio page. It uses the Subtitle
worker's synchronous route and fills the transcript in. No more transcribing elsewhere and
copying text back.

**No asset details.** Every finished asset now states its **length, file size and requested
dimensions**. The Hub was already publishing the duration; the app simply never decoded it.

**Audio saving.** Nothing was removed from it — saving was already implemented, with the
verify-then-acknowledge ordering. Generate a fresh clip and Save; if it misbehaves, tell me,
because that path is covered by tests but no automated run has driven the GUI.

**Window cropping in Split View.** This was a real bug and your description pinpointed it: the
sidebar was a fixed 210 pt and the page columns had fixed minimums, so when the window was
squeezed the row overflowed and macOS clipped the excess — the sidebar and first column looked
like they had vanished. The sidebar is now flexible, the content column compresses, and the
window minimum matches the layout floor instead of exceeding it.

---

## Defects found by an audit, and fixed

1. **A scoped listen address meant the services could never start.** Picking a LAN or Tailscale
   address made the app expect its Hub on that address, while the worker always reports loopback.
   The app rejected its own Hub's readiness record after two minutes and killed it. This is why
   the Fleet address toggles broke Start Services; only loopback or "any address" worked.
2. **Settings were discarded until a model library was chosen.** "Start services at login" and
   "Use imported models offline" appeared to save and reverted on the next launch.
3. **Two dictionaries were built with a trap-on-duplicate constructor** — a repeated id in a Hub
   payload would have crashed the app on every refresh and every two-second poll.
4. **The bind toggles flipped and silently reverted** while services ran. Now disabled with a
   sentence explaining why.

One audit finding was **wrong** and I checked rather than "fixing" it: it claimed the new
Transcribe Clip button calls a route the Hub does not serve. Probed against a real packaged Hub,
that route answers 200.

---

## Needs your attention

**Two controls that did not work, and I did not paper over them: both are now FIXED.**

> **Resolved 2026-09-18.** This section is kept as the record of what was wrong on 2026-09-17.
> Do not read either item as a current defect; both were fixed the next day.

- **Hugging Face token — Save and Test.** ~~Both are stubs: Save always fails and Test always
  returns nothing.~~ **Fixed.** `saveHFToken` and `testHFToken` now call
  `/studio/voice/api/settings` and `/settings/test-hf-token` and report the real answer.
- **"Use imported models offline"** ~~is not honoured by the Image and Subtitle workers, which
  force offline mode regardless~~. **Fixed.** Both workers set `HF_HUB_OFFLINE` only when
  `--offline` is passed, so turning the setting off now takes effect.

**The Audio page opens on a preset-speaker model** (Qwen CustomVoice) that is not in the shipped
library, so it reads as unsupported until you pick a Base model by hand. Given your clone-first
requirement, I would default it to a packaged Base model — but that changes what you see first,
so I left it.

**Still never tested by a real click:** the Voices page and the new Transcribe buttons. The
contracts are tested and the packaged Hub is driven end to end; the SwiftUI window itself has
never been automated in this project. Your eyes are the check.

**And the two long-standing ones, unchanged:** nothing has run on a second Mac yet (the Mac
Studio is the first machine other than the build M4), and VoxCPM2 has still never produced
output anywhere.
