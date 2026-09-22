# R08 click-through checklist (owner)

This is the manual half of packet R08. Everything a machine can check is already
verified: every page renders at minimum (1040×680) and normal (1920×1053) sizes
without clipping, and the save/verify/ACK logic is covered by the native API
tests. What remains needs a person at the machine, because only a human can click
a real Open/Save panel and judge focus and feel.

Tick each box. If something fails, note the studio, what you clicked, and the
message on screen — that is enough to act on.

## Before you start
- [ ] The app is open and the bottom-left shows **Local services ready**.
- [ ] The bottom-right shows **Studio Hub 0.6.38** (and no What's New button beside it).
- [ ] The sidebar reads: Create (Image, Audio, Subtitle, Music) · Library (Models,
      Voices, Jobs) · Fleet (Connections) · App (What's New, Settings) · Coming later
      (Video, Chat).

## 1. Image
- [ ] Pick **FLUX.2 Klein 4B 4-bit**, click **Surprise Me** (a prompt appears).
- [ ] **Generate Image** — the rainbow aura/working indicator is visible and the
      state is clearly different from idle.
- [ ] **Save Image…** — a normal macOS save panel appears; save to the Desktop.
- [ ] The saved file opens as an image at the size shown in the app.
- [ ] The row in **Recent images** shows a small thumbnail.
- [ ] The job still appears in **Jobs**.

## 2. Audio
- [ ] Pick **VoxCPM2 4-bit**. With **no** reference voice, type a sentence and
      **Generate Audio** — it completes (this is zero-shot).
- [ ] Pick **Qwen3-TTS 0.6B Base**, choose a saved voice **with** a transcript,
      generate — it completes.
- [ ] Choose a saved voice **without** a transcript and generate — it must still
      complete (this used to fail offline).
- [ ] **Save Audio…** writes a file that plays.

## 3. Subtitle
- [ ] Pick **Whisper Turbo**, **Choose File…** a short clip.
- [ ] **Transcribe** with **Text**, then again with **SRT** and **VTT**.
- [ ] **Export…** saves each format; the files open and the words are correct.
- [ ] The transcript survives switching away and back to the page.

## 4. Music (both models)
- [ ] Pick **ACE-Step 1.5 Turbo**, generate a 30 s clip, **Save Audio…**.
- [ ] Pick **Stable Audio 3 Medium** — the **Lyrics** and **Vocal language**
      controls are gone and a short note says it is instrumental only.
- [ ] Generate a 30 s SA3 clip — the progress bar **moves through stages** rather
      than jumping from 0 to done.
- [ ] While an SA3 clip is running, press **Cancel** — the job goes to
      **cancelled** (not error) and no partial file appears in the folder.
- [ ] The music preview plays: **Play** becomes **Pause**, there is a **Stop**,
      a scrub bar, and elapsed/total time.
- [ ] Music rows show the clip length.

## 5. Keyboard, focus and window
- [ ] Press **Tab** through a form — focus moves in a sensible order and stays visible.
- [ ] The main button activates with **Space/Enter**.
- [ ] **Escape** closes anything it should (menus, dialogs).
- [ ] Drag the window to its smallest: nothing clips or overlaps and the version
      footer stays visible.

## 6. Stop / restart
- [ ] Press **Stop Services**, then open each studio: Generate is disabled and the
      page says services are stopped (it must not claim to be ready).
- [ ] Start Services again — the studios come back with their model choices kept.
- [ ] Quit the app completely and reopen it: prompts, model choices and **Jobs/
      history** are still there.

## 7. The uncertain-job escape hatch
- [ ] If a job ever shows **Uncertain**, the row offers **Discard** and the
      confirmation explains it abandons the attempt.
- [ ] After discarding, other queued jobs start running (the Mac is released).

## Result

Owner click-through 2026-09-22 on build `333f4de`: **no issues found**
(the owner reported no failures against this checklist).

## What to send back
One line per box that failed, with the studio, the click, and the on-screen
message. Anything that passed needs no comment.
