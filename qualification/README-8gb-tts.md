# The 8 GB text-to-speech run - double-click, walk away

Five voice models on an 8 GB Mac: **Qwen3-TTS 0.6B 8-bit** and **4-bit**, **VoxCPM2 4-bit**, **OmniVoice bf16** and **Breeze TTS 2 4-bit**
(in that order). Each reads the same short text (about 175 words) in the two owner voices, so the numbers
can be laid beside the 32 GB Mac's. Labels: **[M]** measured, **[I]** inference.

**On the 8 GB Mac** (Studio Hub 0.6.125 or later installed, models and voices put there by the kit's copy step):

1. Close your other apps, plug the Mac in, and open Studio Hub (leave it open).
2. On the kit drive, open `Video Generation Kit` > `Studio Hub KH` > `Technical` and double-click
   **`RUN-8GB-TTS.command`**.
3. Leave the Mac alone. When it says "Finished", read `summary.txt` in the new results folder beside
   the script (or send the whole folder back).

Keep `RUN-8GB-TTS.command`, `eight-gb-tts-run.sh`, `ssd_wear.py`, `tts-script-S2.txt` and `tts-script-S4.txt` together
(the kit's Technical folder has all five; the Fish test also needs `RUN-16GB-FISH.command` and `import_kit_packages.py`). If Finder refuses the double-click ("unidentified
developer"), right-click it > Open, once.

## What it does now (short version)

- **It does not wait for memory.** Studio Hub normally refuses to start a model when it thinks the Mac has too little free
  memory (for Qwen3-TTS: needs 3.2 GB free). That is what made earlier runs sit and wait. This run tells the Hub to
  **bypass that guard** for the model it is measuring, then just starts the clip.
- **It never stops a clip because the Mac is swapping.** It records the peak memory, the swap and the memory pressure and
  lets the clip run.
- **Every clip has a hard time limit: 8 minutes.** A clip that is still going after 8 minutes (the Mac is thrashing) is
  cancelled and written down as **timed out**.
- **A crash is a result, not a stop.** If the voice worker dies, runs out of memory or returns an error, that is written
  down with the error text. A model whose clip timed out or crashed has its other clips skipped, and the **next model
  goes on**.
- **It always puts the setting back.** The Hub's memory requirement is restored after each model, and on any exit
  (Ctrl-C, closing the window, an error). If the whole run was killed, the next run restores it first (what to put back is
  kept in `tts8-guard-restore.json` beside the Hub's `hub-ready.json`). The Hub's own default numbers are never edited.
- **The 3-minute text (S4) is off.** On 8 GB it ran 45 minutes without finishing. To add it anyway, open Terminal in the
  `Technical` folder and run `bash eight-gb-tts-run.sh --with-s4` (the S4 limit is 3 x 8 = 24 minutes per clip).

**How long:** usually **10 to 30 minutes** for the whole thing, plus 30 seconds at the start while the Mac sits idle (the SSD baseline below). The worst case is about 8 minutes per model (one
timed-out clip per model, then it moves on) plus loading, so about 25 minutes. Closing the window stops it;
whatever finished is already saved. It loads each model from cold, which is part of what is being measured.

## What it checks first (and says in plain words if it cannot go on)

- This Mac's memory (`hw.memsize`). Not an 8 GB Mac: it refuses, because a bigger Mac answers a
  different question. `--quick` and `--allow-non-target` still run, labelled "not 8 GB evidence".
- Studio Hub is running, version **0.6.125 or later** (the bypass route arrived in 0.6.125).
- The three packages are present and verified, by the Hub's own model view
  (`qwen3-tts-12hz-0-6b-base-8bit`, `qwen3-tts-12hz-0-6b-base-4bit`, `voxcpm2-4bit`, `omnivoice-bfloat16`, `breeze-tts-2-mlx-4bit`).
- The two voices are in the Hub: "Chris - Soft and Soothing" (`cd07b21a2ccf`) and "Christian - Slow &
  Calming" (`0616aec33b41`).
- The Mac is not busy with fleet work (no active Hub jobs). If it is, the script says so, waits (up to 30 minutes) and
  then stops; it never measures while someone else's job runs. It checks again before every clip.

A refusal writes nothing. **It never copies, repairs, installs or deletes anything**: if something is missing it names
the kit step to use (`2 - Copy the models` / `Update this Mac`). The only setting it touches is the temporary per-model
memory override above.

## How it measures

Sends each clip to the Hub on this Mac the way the Gateway and the Voice studio do (`POST /api/hub/jobs`, modality
`voice`, the saved voice by id), so the Hub's own scheduler, section splitting and joins apply as in production. One job
at a time. Per model: Chris S2, Christian S2 (plus S4 for each if asked). The Hub's token is read from its own file on this
Mac and is never printed or written down.

Before each model the script asks the Hub (`POST /api/hub/memory/release`) to make every idle Studio let go of its model
(the Hub never touches a running job), then records the free memory the Hub counts, what holds the rest, and what the
release answered (`free_gb_before`, `memory_released`, `memory_holders` in `results.csv`). Then it sets the bypass
(`PUT /api/hub/memory/admission`, free-memory requirement 0 GB for that model; the Voice worker's own memory check honours
the same number).

Per clip it records: completed / timed out / failed (with the error text), wall time, audio length, RTF (wall time /
audio time), the **peak memory of the voice worker** (macOS `footprint`, every second, over the worker and its children),
memory pressure (normal / warning / critical), swap before / after / peak, how many times the Hub had to restart the job
(`worker_restarts`: a job that keeps going back to "queued" after it started means the worker keeps dying), and whether the
Mac stayed responsive (a poll or sampling pass that stalls for 20 s+ counts as not responsive). A clip over 3x its 32 GB
time is flagged.

**SSD wear (how much each clip writes to this Mac's own SSD).** The point is to estimate wear from swap, so per clip it also
records, in `results.csv` (decimal GB, 1 GB = 1e9 bytes; read with no sudo and nothing installed):

- `ssd_written_gb` - bytes written to the **internal** boot disk during the clip (the "Bytes (Write)" counter that
  `ioreg -c IOBlockStorageDriver` shows), after minus before. The internal disk is the block device on **Apple Fabric**
  (the Apple NVMe) whose location is **Internal**. The external kit SSD the script lives on (USB or Thunderbolt: location
  External) and the built-in SD card reader are not counted. If the results folder lands on the Desktop (drive read-only),
  its few MB of audio count as internal writes.
- `swapouts_gb` - `vm_stat` "Swapouts" pages, after minus before, times the page size (`pagesize`, 16384 on Apple Silicon).
- `ssd_written_baseline_gb_per_min` - what this Mac writes to the internal SSD per minute while idle, from a 30 s sample after
  the "no active Hub jobs" check and before the first clip (same value on every row; `TTS8_BASELINE_S=0` skips it).
- `ssd_written_net_gb` - `ssd_written_gb` less baseline x the clip's duration, never below 0. This is the one to use.

Everything the Mac writes in that window counts, not only swap (the Hub's own files, logs, caches), which is why the baseline is
taken off; it is still not "swap only". `summary.txt` turns the completed clips into words, per model: *about X GB written to this
Mac's SSD per minute of audio*, and *at 8 hours of generation a day, about Y TB/year* (net GB per hour of generation x 8 h x 365).
That is a rough one-run estimate. If the internal disk cannot be found the columns stay empty and the summary says so.
The code is `ssd_wear.py`, beside the script (the Image runner reuses it).

## What you get

A folder named `TTS 8GB Run <date-time>` beside the script (on the drive; `~/Desktop` if the drive is read-only), holding:

- `summary.txt` - a few plain lines per model, facts only, e.g. `Qwen3-TTS 0.6B 8-bit: completed 2/2, peak 6.9 GB,
  swap +1.8 GB, memory pressure critical, slowest clip 2.7x the 32 GB time -> ...`. A model that finished every clip is
  "fits" only if pressure never reached critical, swap grew by 2 GB or less, no clip took over 3x its 32 GB time and the
  Mac kept answering; otherwise it says "completed but strained this Mac", "FITS WITH THE GUARD BYPASSED", "did not
  complete" or "DOES NOT FIT" and why. For a model that completed it also gives **Peak memory used** (what the worker used) and **Started with N GB free**
  (what the Hub saw just before each clip), labelled as measured on this Mac. The two are never one number: a free-memory floor is
  what a job needs before it starts, not its peak.
- `results.csv` - one row per clip: the same columns as the 32 GB `summary.csv`, plus status, error, memory pressure, swap
  before/after/peak, machine RAM, macOS and app version, chip, the 32 GB reference time and the ratio, `guard_relaxed`,
  `override_used_gb`, `measured_peak_gb`, `swap_delta_gb`, `worker_restarts`, `verdict`, the four SSD columns above and a few more.
- the WAVs, to listen to; `run.log`, a copy of what was printed.

**Two things to know when reading the memory.** The 32 GB Mac reported 17-22 GB for the Qwen and VoxCPM2 workers
**[M]**, partly because MLX grows its cache into whatever RAM is free, so those figures are not what an 8 GB Mac needs; the
8 GB run is the honest answer. And the first clip of each model includes loading it.

## Short check

```sh
bash eight-gb-tts-run.sh --quick
```

Runs S2 in Chris's voice for each model (three clips, a few minutes on a 32 GB Mac) into a folder named
`TTS 8GB Quick Check ...`. It is the script's own self-test and works on any Mac (the summary says it is not 8 GB
evidence). Other switches: `--models qwen3,qwen3-4bit,voxcpm2,omnivoice,breeze` picks models; `--with-s4` adds the 3-minute text.

Settings (all optional, set in Terminal before the command): `TTS8_TIMEOUT=480` seconds per short clip (the 3-minute text gets
3x); `TTS8_RELAX_FREE_GB=0` the free-memory requirement during a model; `TTS8_S4=1` same as `--with-s4`; `TTS8_BASELINE_S=30` seconds of idle SSD-write sampling before the first clip (0 skips it). `TTS8_MEM_WAIT=300`
only matters on a Hub older than 0.6.125 that cannot bypass: a job held for memory that long is recorded `did not start`.

Note: the Hub keeps the audio of each job like any other job and cleans it by its own retention rule.

## The 8 GB Breeze test (`RUN-8GB-BREEZE.command`)

Breeze TTS 2 4-bit alone, on an **8 GB Mac**, with the calibrated Breeze sampling (0.6.143 "Breeze B": temperature 0.6, top_p 0.9,
top_k 40, repetition penalty 1.05) sent in every request, so a Hub 0.6.142 (which takes these fields, with older defaults) samples the same way.

1. **Double-click `RUN-8GB-BREEZE.command`** (same `Technical` folder; Studio Hub 0.6.142+ open, other apps closed, on power). It refuses on a
   Mac that is not 8 GB, before copying anything.
2. **It first puts Breeze on that Mac.** Breeze is 16 GB-tier, so "2 - Copy the models" leaves it behind on 8 GB. `import_kit_packages.py`
   imports exactly `breeze-tts-2-mlx-4bit` (about 3 GB) from the kit's `model-library` through the Hub's own import code (as for Fish).
   A second run copies nothing. Breeze stays on the Mac afterwards. (From 0.6.145 Breeze is an 8 GB model - measured by this test on
   2026-10-04 - so "2 - Copy the models" puts it on 8 GB Macs too and the Hub sends them fleet work.)
3. **Then it runs `eight-gb-tts-run.sh --models breeze`**: S2 (175 words) in both owner voices, the Hub's free-memory guard *and* its
   16 GB total-memory tier bypassed for Breeze and put back afterwards. A Breeze clip gets 2.5x the time limit (20 minutes): on the 16 GB fleet
   Breeze runs at RTF 3.6 median (M1 16 GB: about 5), so the short text alone takes about 6 minutes there.

Besides the usual columns, `results.csv` records `params_sent` / `worker_accepted_params` and the Hub's own telemetry for each job
(`hub_max_used_gb`, `hub_min_available_gb`, `hub_peak_pressure`, `hub_swap_delta_gb`, `hub_worker_peak_rss_gb`) and the section checks
(`sections`: how many, how many accepted, rejection codes); `summary.txt` has one line per completed clip with them.

A clip the Hub never dispatches (queued for the whole limit) is recorded **did not start / NOT MEASURED**, not "timed out": on 2026-10-02
both 8 GB Breeze clips sat queued for 480 s behind the 16 GB total-memory floor (that runner only bypassed the free-memory guard; the
worker stayed idle at 0.8-1.1 GB), so that run measured nothing about Breeze on 8 GB.

## The 16 GB Fish test (`RUN-16GB-FISH.command`)

The same measurement for **Fish Audio S2 Pro 4-bit, 8-bit and bf16** on a **16 GB Mac** (the new M4 Mac mini). Same two voices, same
short text (S2), one clip at a time, in that order. Everything above applies (the Hub's free-memory guard is bypassed for each model and put
back afterwards, nothing is stopped for swap, peak memory used and "Started with N GB free" are reported, SSD writes are recorded); what differs:

1. **Double-click `RUN-16GB-FISH.command`** (same `Technical` folder, same prerequisites: Studio Hub 0.6.125+ open, other apps closed, on power).
   It refuses on a Mac that is not 16 GB (before copying anything).
2. **It first puts the three packages on that Mac.** Fish bf16 is shelved and 8-bit is 24 GB-tier, so the kit's copy step ("2 - Copy the models") does
   not carry them to a 16 GB Mac. `import_kit_packages.py` (beside it) imports exactly `fish-audio-s2-pro-4bit`, `-8bit` and `-bf16` from the kit's
   `model-library` into the library the running Hub uses, through the Hub's own import code (`model_packages.import_folder`: every file is checked against the
   manifest while it is copied), skips any the Hub already reads as verified, stops first if the disk would drop below 5 GB free, and prints what it
   copied (about 22 GB the first time; a second run copies nothing). It never starts a Hub and never touches another library.
3. **Fish gets 3x the time limit**: 24 minutes per clip instead of 8 (Fish runs at about 2-4x real time on the 32 GB Mac, slower on 16 GB). A clip that is still
   going then is cancelled and written down as "timed out", and the next model goes on. Expect 30 to 90 minutes.
4. **The labels say "16 GB Mac".** The results folder is `TTS 16GB Run <date-time>`, the summary says "fits this 16 GB Mac" and "Measured on this 16 GB Mac".
   On a Mac of another size the folder carries that size and the summary says it is not 16 GB evidence.
5. **The 3-minute text is opt-in**, as for 8 GB: in Terminal, `bash RUN-16GB-FISH.command --with-s4` (limit 72 minutes per clip; very slow).
6. **The Hub's own memory tier is bypassed too.** Fish 8-bit is declared 24 GB and bf16 32 GB, which a 16 GB Mac is below; the scheduler would not even try them. For
   each model the runner lowers both the free-memory requirement (to 0) and, when it is above this Mac's memory, the total-memory tier, then puts both back.

A Mac that is not marked "keep all models" moves a shelved model (Fish bf16) into the Hub's Trash Folder the next time Studio Hub **starts** (kept 15 days; nothing is
deleted), so run the test straight after the copy with the Hub left open, as the wrapper does. Without the wrapper, in Terminal in the `Technical` folder:
`bash eight-gb-tts-run.sh --models fish4,fish8,fishbf16 --target-gb 16` (the three packages must already be on the Mac). No 32 GB reference time is used for Fish
(the 32 GB samples were taken under heavy load), so no Fish clip is flagged "slow".

