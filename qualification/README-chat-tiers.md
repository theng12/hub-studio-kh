# The Qwen3.5-9B 4-bit tier run — one command

The shipped model is **`mlx-community/Qwen3.5-9B-4bit` (uniform 4-bit MLX, 5.98 GB)** and its
manifest qualifies it at **16 GB**. Labels: **[M]** measured, **[V]** vendor claim, **[I]** inference.

**The 16 GB tier is measured and QUALIFIED [M].** Three 16 GB Macs — M1, M2, M4 — each completed a
256-token completion: 12.40–12.48, 14.14–14.02 and 18.33–18.60 tokens/second, peak MLX 5.23 GB on
all three, load 9.08–13.69 s. Nothing on this page needs re-running to establish that.

**The 8 GB tier is a documented exclusion and is not an open question.** The model does not run
there — the owner tried it on his 8 GB Mac — so **the harness refuses an 8 GB Mac immediately**
rather than probing. An attempt would cost up to 900 s and a swapping model load to rediscover an
answer already known. There is no 8 GB run to make, and the refusal says so in those words. On that
tier the kit's Voice, Subtitle and Image models are what run; Chat's job on this fleet is the 16 GB
and larger Macs.

**To re-measure the 16 GB tier**, on a 16 GB Mac with the kit's app installed and the model in
`~/StudioHubModels`: close every other app (browsers first), plug in power, leave the Mac alone
while it runs, and get the script there first (AirDrop `qualification/chat-tier-run.sh`, or copy it
off the kit drive — or double-click `Technical/RUN-CHAT-TIER.command`). Then in Terminal paste:

```sh
bash ~/Desktop/chat-tier-run.sh
```

That is the whole command. It finds the 4-bit model through the bundle's own catalog, finds the app
and its bundled runtime (installed app first, then any mounted kit drive), checks the tier, runs the
packaged Chat worker against the real weights and prints one block.

**How long:** 3–10 minutes **[I]** on 16 GB — about 10 s to load, then two 256-token completions.
One request stops at 900 s and a run whose system swap passes 6 GB is stopped, both recorded
`ABORTED` and the verdict `INCONCLUSIVE`, never "failed".

**Send back:** the block between `COPY FROM HERE` and `TO HERE`, verbatim. It is also saved as
`~/Desktop/StudioHub Chat Tier Run/VERDICT.txt`. It carries the chip, memory and macOS; the app
build; the model with its quantization, repo and revision; the engine; the prompt and its token
counts; wall time, load time, tokens/second; peak MLX memory; swap peak; the answer; and the
verdict. Everything else (`results.tsv`, `logs/`) stays on the Mac.

**What the verdict means** — the script prints this line itself:

- **QUALIFIED** — the 16 GB tier completed both runs; the model is usable there.
- **COMPLETED BELOW THE PUBLISHED TIER** — an 8 GB Mac finished anyway. It ran; that is not
  comfortable, and it does not move the model's 16 GB qualification.
- **INCONCLUSIVE** — the harness stopped a run at the swap cap or the time limit; the tier is
  neither proven nor disproven. Re-run with `CHAT_MAX_SWAP_GB=10` or a longer `CHAT_TIMEOUT`.
- **NOT QUALIFIED** — a run ended in an out-of-memory/GPU abort: this tier cannot run it.
- **NOT TIER EVIDENCE** — it ran on a 24/32 GB Mac; the numbers are real but answer no tier
  question. **It never estimates a tier it did not run on.**

**It refuses rather than guess, and writes nothing when it does** (a refusal is not a result): on a
Mac that is not the 8 or 16 GB tier (override `CHAT_ALLOW_NON_TARGET=1`); when no packaged app or no
complete model is found; or when available memory is below the checkpoint's own 5.98 GB plus 0.5 GB
of working room. That last refusal names the available memory, the floor and the override — and on
an 8 GB Mac it **is** the answer. To see what such a Mac does when forced:

```sh
CHAT_IGNORE_FREE_FLOOR=1 bash ~/Desktop/chat-tier-run.sh
```

Harness self-check (loads the model, one 8-token completion into a temp folder, no verdict):

```sh
CHAT_SMOKE=1 bash ~/Desktop/chat-tier-run.sh
```

Note: double-clicking a `.sh` file opens it in an editor on macOS, so use the Terminal line above.
