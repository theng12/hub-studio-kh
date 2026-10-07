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
