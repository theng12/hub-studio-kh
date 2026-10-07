# Studio Hub MLX 4-bit LLM qualification

These three Finder commands test one exact pinned checkpoint at a time:

- `RUN-NEMOTRON-LLM.command` — NVIDIA Nemotron 3.5 Lightning 30B-A3B MLX 4-bit
- `RUN-QWEN36-LLM.command` — Qwen3.6 35B-A3B MLX 4-bit
- `RUN-GEMMA4-LLM.command` — Gemma 4 26B-A4B QAT MLX 4-bit

Quit Studio Hub and other memory-heavy work, then double-click only the command for the model you
intend to test. The default is the full audited English suite. A host-wide advisory lock refuses a
second LLM qualification even when it is launched from another SSD. Control-C or window termination
stops only the probe process group owned by that launch.

The launcher uses the installed Studio Hub Voice runtime first and the kit's `Studio Hub.app` as a
fallback. It never uses system Python, installs packages, downloads or copies weights, starts Studio
Hub, changes fleet state, or enables a model. Static check mode inspects package metadata and the
required `mlx_lm/models/*.py` source file without importing MLX, loading a tokenizer, or launching a
probe worker.

Each live run keeps `report.json`, every raw generated `.txt` file, and `run.log` under a unique
folder in `~/Desktop/StudioHub LLM Qualification/`. Read `SUMMARY.txt` first. Mechanical validation
and subjective writing review are separate: a mechanical pass leaves writing quality pending until
a person reads the chapter. Every result applies only to the physical RAM tier recorded for that
host; it does not qualify a smaller Mac.

The reported first model load is the first load in a fresh probe process. It is not guaranteed to be
an uncached SSD benchmark because macOS may already hold model files in its filesystem cache.

## Extended workload profile (version 2)

The same three `.command` files now run the full extended suite. No command typing or app update is
needed. Older dated results stay intact; their prompt hash identifies the original profile.

- One response containing **three complete chapters**, each targeting **1,200 body words**.
  All three exact headings must appear in order, with **1,080–1,320 body words per chapter**.
  The output allowance is **12,288 tokens**, providing room beyond the expected 3,600-word text.
  A large allowance cannot make a model finish: an early stop, missing chapter or short chapter fails.
- The original three 12-scene batches plus new **25- and 50-scene** batches test strict JSON,
  every exact scene ID, completeness and prompt length. Output allowances are **6,144**, **12,288**
  and **16,384 tokens**, respectively; the 50-scene case tests the current Hub output ceiling.
- Aim for roughly **500–900 characters per scene**, including the style, when useful. This is
  guidance, not a minimum. Naturally shorter prompts remain acceptable. **4,000 characters is a
  hard ceiling, not a length target**: prompts are measured after trimming, with any deterministic
  style prefix included, using the same UTF-16 character units as Story Studio. Nothing is truncated
  or repaired to turn a failure into a pass.

`SUMMARY.txt` shows the individual chapter word counts, scene character statistics, cap breaches,
the largest passing tested batch and any failed batch sizes, along with timing, tokens, memory,
pressure and swap. A passing 50-scene response is a first capacity result, not proof of reliable
374-scene production or a different RAM tier. Start by evaluating 25-scene batches; adopt 50 only
after the larger batch repeatedly preserves IDs and completes with useful prompts.
