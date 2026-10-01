# The 8 GB text-to-speech run — double-click and wait

Three voice models, on an 8 GB Mac: **Qwen3-TTS 0.6B 8-bit**, **VoxCPM2 4-bit** and **OmniVoice
bf16** (in that order). Each reads the same two texts in the two owner voices, so the numbers can be
laid beside the 32 GB Mac's. Labels: **[M]** measured, **[I]** inference.

**On the 8 GB Mac** (Studio Hub 0.6.122 or later installed, models and voices put there by the kit's
copy step):

1. Close your other apps, plug the Mac in, and open Studio Hub (leave it open).
2. On the kit drive, open `Video Generation Kit` > `Studio Hub KH` > `Technical` and double-click
   **`RUN-8GB-TTS.command`**.
3. Leave the Mac alone. When it says "Finished", read `summary.txt` in the new results folder beside
   the script (or send the whole folder back).

Keep `RUN-8GB-TTS.command`, `eight-gb-tts-run.sh`, `tts-script-S2.txt` and `tts-script-S4.txt` together
(the kit's Technical folder has all four). If Finder refuses the double-click ("unidentified
developer"), right-click it > Open, once.

**How long:** the 32 GB M2 Max needed about 28 minutes of generation for the whole test **[M]**; on an
8 GB Mac allow 1 to 2 hours, more if it swaps **[I]** (the script prints its own estimate first). It
loads each model from cold, which is part of what is being measured. A model the Hub will not start for lack of
memory costs 5 minutes, not hours. Closing the window stops it;
whatever finished is already saved.

## What it checks first (and says in plain words if it cannot go on)

- This Mac's memory (`hw.memsize`). Not an 8 GB Mac: it refuses, because a bigger Mac answers a
  different question. `--quick` (below) and `--allow-non-target` still run, labelled "not 8 GB evidence".
- Studio Hub is running, version 0.6.122 or later.
- The three packages are present and verified, by the Hub's own model view
  (`qwen3-tts-12hz-0-6b-base-8bit`, `voxcpm2-4bit`, `omnivoice-bfloat16`).
- The two voices are in the Hub: "Chris - Soft and Soothing" (`cd07b21a2ccf`) and "Christian - Slow &
  Calming" (`0616aec33b41`).
- The Mac is not busy with fleet work (no active Hub jobs). If it is, the script says so, waits (checking
  again every few seconds, up to 30 minutes) and then stops; it never measures while someone else's
  job runs. It checks again before every clip.

A refusal writes nothing. **It never copies, repairs, installs, changes a setting or deletes anything**:
if something is missing it names the kit step to use (`2 - Copy the models` / `Update this Mac`).

## What it does

Sends each clip to the Hub on this Mac the way the Gateway and the Voice studio do (`POST
/api/hub/jobs`, modality `voice`, the saved voice by id), so the Hub's own scheduler, section
splitting and joins apply as in production. One job at a time. Per model: Chris S2, Chris S4,
Christian S2, Christian S4 (S2 is about 175 words, S4 about 3 minutes — `tts-script-S2.txt` and
`tts-script-S4.txt`, the same text as the 32 GB test). The Hub's token is read from its own file on this
Mac and is never printed or written down.

**Memory, before each model.** The script asks the Hub (`POST /api/hub/memory/release`) to make every idle
Studio let go of its model - the Hub never touches a running job - then records the free memory the Hub counts
(macOS free + inactive pages: the number behind "needs 3.2 GB, 3.0 GB free"), what holds the rest (wired and
compressed memory, the biggest processes, each Studio worker named) and what the release answered. These go to
`run.log`, `summary.txt` and the `free_gb_before` / `memory_released` / `memory_holders` columns of `results.csv`.

**If the Hub will not start a model for lack of memory.** The Hub starts a job only when the free memory it
counts is at least the model's floor (Qwen3-TTS 0.6B 8-bit: 3.2 GB). A job it holds back shows the reason on the
job (`governor_note`, the red "waiting for memory" note in the app). The script reads that reason while the clip
is queued; if the Hub has waited on memory for more than **5 minutes** (`TTS8_MEM_WAIT=300`) it records the clip as
**`did not start` - needs X GB free, had Y GB** (the Hub's own numbers) and cancels that job through the Hub.
Then it **measures the model anyway** (Hub 0.6.125 or later): it lowers that one model's free-memory requirement to
0.5 GB (`TTS8_RELAX_FREE_GB`) through the Hub's operator override (`PUT /api/hub/memory/admission`; the Voice worker's
own memory check honours the same number) and runs the clip again with a safety net - it is cancelled if swap grows by
more than 1.5 GB or memory pressure reaches urgent, recorded as "needs more than this Mac can give: peak X GB, swap +Y GB",
and the next model goes on. The requirement is put back exactly as it was after that model, on any exit (Ctrl-C, closing
the window) and, if a run was killed, at the start of the next run (what to put back is kept in `tts8-guard-restore.json`
beside the Hub's `hub-ready.json`). `results.csv` has `guard_relaxed`, `override_used_gb`, `measured_peak_gb`,
`swap_delta_gb`, `hub_free_floor_gb` and `verdict` (`fits at default guard` / `fits only with relaxed guard - real need
about X GB` / `does not fit`); `summary.txt` gives each model a **recommended requirement = measured peak + 0.25 GB**,
labelled as measured on this Mac. The Hub's own default numbers are never edited. On an older Hub the model is skipped as before.

Per clip it records: completed or failed (with the error text), wall time, audio length, RTF (wall
time / audio time), the **peak memory of the voice worker** (macOS `footprint`, every second, over the
worker and its children), memory pressure (normal / warning / critical), swap before / after / peak,
and whether the Mac stayed responsive (a Hub poll or a sampling pass that stalls for 20 s+ counts as
not responsive). A clip over 3x its 32 GB time is flagged.

## What you get

A folder named `TTS 8GB Run <date-time>` beside the script (on the drive; `~/Desktop` if the drive is
read-only), holding:

- `summary.txt` — a few plain lines per model, facts only, e.g. `Qwen3-TTS 0.6B 8-bit: completed 4/4, peak
  3.1 GB, swap +0.2 GB, memory pressure normal, slowest clip 1.4x the 32 GB time -> fits 8 GB`. "Fits"
  means every clip completed, pressure never critical, swap grew by 2 GB or less, no clip over 3x its
  32 GB time and the Mac kept answering; anything else is stated ("completed but strained this Mac",
  "did not complete").
- `results.csv` — one row per clip: the same columns as the 32 GB `summary.csv`, plus status, error,
  memory pressure, swap before/after/peak, machine RAM, macOS and app version, chip, the 32 GB reference
  time and the ratio, and a few more.
- the WAVs, to listen to; `run.log`, a copy of what was printed.

**Two things to know when reading the memory.** The 32 GB Mac reported 17-22 GB for the Qwen and
VoxCPM2 workers **[M]**, partly because MLX grows its cache into whatever RAM is free, so those figures
are not what an 8 GB Mac needs; the 8 GB run is the honest answer. And the first clip of each model
includes loading it.

## Short check

```sh
bash eight-gb-tts-run.sh --quick
```

Runs S2 in Chris's voice for each model (three clips, a few minutes on a 32 GB Mac) into a folder named
`TTS 8GB Quick Check ...`. It is the script's own self-test and works on any Mac. `--models
qwen3,voxcpm2,omnivoice` picks models; `TTS8_TIMEOUT=2700` is the per-clip limit in seconds (a clip that
runs over is cancelled and recorded as failed); `TTS8_MEM_WAIT=300` is how long a job may wait for memory
before it is recorded as `did not start` (above).

Note: the Hub keeps the audio of each job like any other job and cleans it by its own retention rule.
