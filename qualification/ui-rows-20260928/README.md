# The rows, after the six fixes — 2026-09-28

Rendered offscreen against the **live Hub 0.6.86** with the 0.6.88 sources, by
`STUDIOHUB_RENDER_LIVE=1 bash native/render-page.sh <pages> <dir> 1920x1053`. The renderer compiles
the app's own sources and draws the real pages, so these are the pages that ship, not mock-ups.

| File | What it is evidence for |
| --- | --- |
| `video-1920x1053.png` | Video rows now carry a still (the box at the left of each finished row), the request line (`448p · 11:7`), and the clip's own facts (`4.0 s · 1.6 MB · 704×448 · 97 frames · 24 fps · with audio`) — and **Preview / Save… / Show in Folder** in one fixed column. |
| `audio-1920x1053.png` | Audio rows state the script's length and the pace (`3465 characters · speed 1×`), which no row showed before, with the same action column. |
| `jobs-1920x1053.png` | The Jobs tab: the same three buttons at the same x on every row of every Studio, and the same facts lines as the Studio pages, because both read one derivation. |
| `music-1920x1053.png` | Music rows: `10.0 s · 8 steps · seed 5` above the result facts, and the four-button column. |
| `image-1920x1053.png` | Image rows: steps/guidance/seed in the request line, canvas in the result line, three buttons. |

**What the renders do not show.** The video stills draw as a placeholder here: the offscreen renderer
snapshots before an asynchronous artifact fetch resolves, so the frame itself is not in the picture.
The fetch path is exercised by `tests/native/api_main.swift` (the fixture serves real bytes) and by
the live app.

**Not in these pages.** The Audio transport (play/pause, stop, scrub bar, elapsed time, speed) appears
only once a clip is loaded, which requires a click; it is driven by `loadedAudioJobID` and mirrors the
Music transport that was already shipped. `Show in Folder` is drawn disabled in every render because
the offscreen harness has no connection to act on — in the running app it is disabled only when the
artifact is not a file on this Mac, which is the case it is meant to explain.
