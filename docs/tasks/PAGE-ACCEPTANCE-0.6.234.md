# Page acceptance — Hub Studio KH 0.6.234

Owner rule (2026-11-13, "Completeness, not just correctness"): before a page's gate, every page
carries three things beside its visual review:

1. **Tasks with one end-to-end test each** — what a person is trying to finish, and the test that
   proves the whole path, not just that a control exists.
2. **Capability cross-check from live data** — every capability the Hub or a model declares for that
   page has a control, read from what the producers publish rather than a hand-written list.
3. **Standard-practice comparison** — one named reference product, with what is present here, what
   is deliberately absent, and what is missing.

A page is not accepted until the three are filled in below and the referenced tests pass. The UI
gate adds the measured 1440/768 layout, contrast and independent 4/4 review.

## Overview

- Tasks: (a) see what is running now and open Jobs; (b) see which studio needs a model and open it;
  (c) see the library state. End-to-end: fixture render with one job per state, plus
  `tests/native/view_checks.swift` (thumbnail, measurable facts) and the 1440/768 measure.
- Capabilities: the Hub publishes active jobs, downloads, model counts, work policy and fleet
  counts; each is rendered (`overviewStatus`, In progress, Downloads, Workspaces, Model library).
  Cross-check: `workspace.jobHistories`, `workspace.downloads`, `workspace.workPolicy`,
  `workspace.fleetOverview` — no page-local model list.
- Standard: **Xcode's Welcome/Organizer**. Present: live work, library state, next step. Deliberately
  absent: a second navigation list (sidebar owns it). Missing: none named.

## Image

- Tasks: (a) text-to-image generation with model, size, steps, guidance, seed; (b) reference-image
  edit up to three images; (c) preview/save/reveal a result; (d) cancel/retry/discard. End-to-end:
  `STUDIOHUB_RENDER_IMAGE_REFERENCE=1` offscreen interaction (mode switch, reference prepared,
  pixel bound) plus fixture render with running/failed/uncertain/done rows.
- Capabilities: model `capabilities` (`design`, `reference`), image settings (width/height/steps/
  guidance/seed), reference limit, loading mode. Cross-check: `NativeModel.capabilities`,
  `NativeImageSettings`, `workspace.imageReferences`, `imageLoadingControl`; the control is hidden
  with its published reason when the model does not offer it.
- Standard: **ComfyUI / Playground**. Present: prompt, model, size, steps, guidance, seed, reference,
  preview, history. Deliberately absent: node graphs, LoRA stacks. Missing: batch count (not
  requested; the lane publishes one image per job).

## Audio

- Tasks: (a) preset voice generation; (b) voice cloning from a saved reference; (c) play/save/reveal;
  (d) cancel/retry/discard. End-to-end: fixture render with both modes; voice-mode tabs follow the
  model's published roster; `tests/native/view_checks` pins the shared form shape.
- Capabilities: `voice_modes` (preset, clone, design), preset speaker roster, speed control, saved
  voices, transcript. Cross-check: `reportsVoiceModes`, `voiceModes`, `speakers`,
  `publishesPresetRoster`, `selectedLibraryVoice`; the page names the reason when a mode is
  unpublished.
- Standard: **ElevenLabs Studio**. Present: script, voice, cloning, speed, history, player.
  Deliberately absent: cloud voice marketplace, dubbing. Missing: none named.

## Subtitle (Transcription)

- Tasks: (a) choose a recording and format (Text/SRT/VTT); (b) transcribe with language; (c)
  review/export; (d) cancel. End-to-end: fixture render; `saveTranscript` verifies the artifact
  before acknowledging.
- Capabilities: formats, language auto-detect, word timestamps (deliberately not exposed because no
  viewer), model roster. Cross-check: `NativeTranscriptFormat`, `workspace.transcriptionModel`,
  `supportsWordTimestamps`; the unsupported control is absent with its reason in
  `docs/agent-rules-reference`/README, not silently dead.
- Standard: **Descript / Whisper desktop**. Present: file, format, language, transcript, export.
  Deliberately absent: word-level editor (not implemented). Missing: none named.

## Music

- Tasks: (a) prompt-to-music with duration, steps, guidance, shift, seed; (b) instrumental/lyrics;
  (c) play/save; (d) cancel/retry/discard. End-to-end: fixture render with done/failed rows;
  settings controls render only what the lane publishes.
- Capabilities: music configuration, duration, settings bounds, instrumental, lyrics, vocal
  language. Cross-check: `NativeMusicConfiguration`, `workspace.musicSettings`, selected model's
  published controls; when none are published the page says so and offers Model Defaults.
- Standard: **Suno / Udio**. Present: prompt, duration, musical settings, history, player.
  Deliberately absent: hosted generation, stem export. Missing: none named.

## Chat

- Tasks: (a) send a prompt to a local model; (b) read/copy/save the answer; (c) clear. End-to-end:
  fixture conversation render plus `NativeWorkspaceStore` chat contract tests (json/schema, tokens).
- Capabilities: model roster, chat metrics, JSON/system prompt from the lane, streamed turns.
  Cross-check: `workspace.chatModel`, `chatTurns`, `chatMetrics`; unsupported models are refused by
  name with a reason.
- Standard: **LM Studio / Ollama chat**. Present: conversation, model, metrics, save/copy.
  Deliberately absent: cloud providers, tools/agents. Missing: none named.

## Models

- Tasks: (a) find a model and read its state; (b) download or import; (c) verify/remove; (d) open a
  model's details/defaults. End-to-end: fixture render with Ready / Needs setup / Not downloaded /
  Downloading rows; inline Download; `STUDIOHUB_RENDER_MODEL` detail at 768.
- Capabilities: catalog facts, capabilities, size, memory floor, defaults, loading mode, removal.
  Cross-check: `NativeModel` fields and the model's lane contract; the details pane renders what the
  producer states and names what it does not.
- Standard: **LM Studio model browser / Hugging Face**. Present: search, filters with counts, state
  chips, download/import/verify/remove, details. Deliberately absent: arbitrary repo URLs (the
  catalog is pinned). Missing: none named.

## Set up this Mac

- Tasks: (a) choose a preset for this Mac; (b) tick individual models; (c) install and see the cost
  first. End-to-end: fixture plan render with presets, per-model install, refusal and free space;
  the selected preset's Install sits above the cards.
- Capabilities: memory, free space, presets, per-model sources (kit/LAN/internet), refusal,
  installed counts. Cross-check: `NativeInstallPlan` and `HUB_RELEASE_ARTIFACTS`; one size per copy
  (decimal GB) and one refusal sentence.
- Standard: **LM Studio's model downloader / Ollama pull**. Present: preset and manual selection,
  cost, free space, sources. Deliberately absent: shell/terminal install. Missing: none named.

## Voices

- Tasks: (a) add a voice with clip + transcript; (b) edit/remove; (c) use one for a clone; (d) search
  and filter by tag. End-to-end: fixture rows with ready/not-ready; editor sheet; `useVoice` path.
- Capabilities: tags, transcript, language, gender, fleet-managed flag, duration. Cross-check:
  `NativeVoice` fields and `voiceTagCounts`; every declared fact is shown.
- Standard: **ElevenLabs voice library**. Present: list, tags, transcript readiness, play/use,
  edit/remove. Deliberately absent: public sharing. Missing: none named.

## Jobs

- Tasks: (a) find a job by state or kind; (b) act on it (play/save/reveal/cancel/retry/discard);
  (c) see failures first. End-to-end: native segment action across all tabs
  (`STUDIOHUB_RENDER_JOBS_TABS=1`), Needs you section, Mac filter counts, search.
- Capabilities: every Studio's history, downloads, states, timestamps, machine/origin, errors.
  Cross-check: `workspace.jobHistories` and `HistoryTab.allCases` derived from `StudioFunction`.
- Standard: **Xcode Organizer / Activity Monitor**. Present: filters with counts, search, states,
  actions. Deliberately absent: a second queue editor. Missing: none named.

## Renders

- Tasks: (a) see final renders and failures; (b) open details; (c) refresh/ask a silent Mac again.
  End-to-end: fixture history with done/failed/cancelled/rendering and a non-answering Mac.
- Capabilities: totals, per-machine counts, encode tuple/grant, failure owner, output hash in
  details. Cross-check: `NativeRenderHistory`, `renderAuthority`, `RenderCopy`.
- Standard: **Final Cut Pro background tasks**. Present: totals, rows, failure reason, details.
  Deliberately absent: editing. Missing: none named.

## Connections

- Tasks: (a) read every Mac's health and act (Send Here, Details, Remove); (b) hand out the Fleet
  token; (c) join/leave/rotate/reset/hand over. End-to-end: fixture fleet with offline/refused/
  attention/working; Table/Cards switch; bottom-scroll proof; group-operation dialogs.
- Capabilities: role, group counts, reachability, disk/memory, startup, credential, addresses,
  per-Mac actions. Cross-check: `NativeFleetRow`, `fleetOverview`, `fleetNodes`, identity settings;
  the status line and table count the same rows.
- Standard: **Tailscale admin / macOS Sharing**. Present: health-first list, one action per Mac,
  details, group operations. Deliberately absent: a second fleet editor (Gateway owns fleet-wide
  changes). Missing: none named.

## Settings

- Tasks: (a) change appearance/startup/work policy/lite mode/storage; (b) test/save the HF token;
  (c) start/stop/force-stop services. End-to-end: Appearance segment persisted (interaction log);
  stop/force-stop dialogs; storage sheet.
- Capabilities: every setting the Hub publishes (`workPolicy`, `workstation`, `storage`,
  `developerMode`, `memory`). Cross-check: the page renders each published setting and names the
  ones it cannot change.
- Standard: **macOS System Settings**. Present: labelled sections, immediate save, defaults.
  Deliberately absent: a second settings store. Missing: none named.

## What's New

- Tasks: (a) read this build's notes; (b) open the Launcher when notes are absent. End-to-end:
  notes render with Technical notes behind a disclosure; no-notes state warns and offers the
  Launcher.
- Capabilities: release notes from the bundle, version. Cross-check: `ReleaseNotes.hasNotes/text`;
  the page never dresses the fallback up as notes.
- Standard: **App Store What's New / Sparkle release notes**. Present: versioned notes, disclosure.
  Deliberately absent: update installation (Studio Launcher owns it). Missing: none named.

## Retired — Video

Not a navigation page. The Video lane was retired; its page and clips remain reachable only through
Jobs and Renders, and it deliberately has no Create entry, no model picker promise and no install
invitation. It is excluded from the all-pages render set; the Jobs Video tab is its history.
