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

## Gemma 4 Long-Context Probe (`RUN-GEMMA4-LONG-CONTEXT.command`)

A dedicated Finder-runnable probe evaluates extended context scaling for **Gemma 4 26B-A4B QAT MLX 4-bit** beyond the current 32K deployment budget.

- Finder launcher: `RUN-GEMMA4-LONG-CONTEXT.command`
- Evaluates: **48K** (`input-48k`, 49,152 prompt tokens) and **64K** (`input-64k`, 65,536 prompt tokens) context windows using the model's own tokenizer.
- Output workload: Each size asks for a **~4K-token answer** (16 structured sections) requiring sustained generation and needle recall (`HARBOR LANTERN`).
- Completeness check: Mechanically verifies all 16 numbered sections, presence of the needle token, clean finish reason (`stop`), and lack of thinking spill.
- Stop rule: Stops before the next size if swap grows (`swap_delta > 0`) or peak process footprint exceeds **26.0 GiB** (default threshold printed at launch, overridable via `--max-footprint-gb`).
- Published limit unchanged: Studio Hub's published context window (`model-manifests/gemma4-26b-a4b-qat-4bit.json`, `context_window_tokens=32768`) is not modified by this qualification probe.

### How to run on Mac 1607 (M2 Pro 32 GB)

1. **Quit Studio Hub and other applications:** Ensure Studio Hub is completely closed before running to avoid memory contention and ensure the maximum clean physical RAM headroom. The host-wide advisory lock (`moe-llm-qualification.lock`) will prevent the probe from starting if another qualification run is active.
2. **Double-click `RUN-GEMMA4-LONG-CONTEXT.command` in Finder.** A terminal window will open, verify the installed Voice runtime and model files, and execute the `long-context` suite.
3. The probe runs `input-48k`. If memory footprint remains <= 26 GB and swap does not grow, it proceeds to `input-64k`. If limits are breached, it stops safely before the next size and records the exact reason.
4. **What to send back:** After the run finishes, send back the entire timestamped results folder created on the Desktop under `~/Desktop/StudioHub LLM Qualification/gemma4-26b-a4b-qat-4bit-<host>-<timestamp>/`, including:
   - `SUMMARY.txt` (the primary human-readable summary stating the last safe size, stop reason, and per-size metrics)
   - `report.json` (machine-readable measurements: prompt/completion tokens, prefill & generate times, MLX peak, footprint peak, swap delta, memory pressure, answer completeness)
   - `input-48k.txt` and `input-64k.txt` (raw generated output texts)
   - `run.log` (complete terminal transcript)

