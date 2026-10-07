# Gemma 4 32 GiB owner-accepted qualification

Qualified for the measured physical 32 GiB M2 Max, with the owner accepting the observed 985/800/805-word chapters. The original 1,200-word strict test remains failed; chapter 8 and 08 parse identically in Story Studio source. All 50 compact scene prompts had exact IDs, valid JSON and final lengths under 4,000 UTF-16 units. Physical 24 GiB is untested and excluded from production admission.

`measurement.json` retains original report/prompt SHA256s, exact publisher identities, all measured per-case tokens/TPS/memory/pressure/swap, and the owner acceptance. Machine-local paths were omitted; source reports remain intact in the owner's Desktop results. The curated 32,768-token combined deployment budget is separate from the checkpoint's declared 262,144-token architecture limit. The worker's 16,384-token output allowance is not a measured 16k output result; the largest measured response is 4,139 tokens. No minimum-required-free-memory threshold is inferred from peak memory.

Original outputs are retained here for reproducibility and manual review. This is a qualification of the owner-accepted workload on the measured host, not a claim that every chapter reaches 1,200 words, that 50 scenes at 4,000 characters each fit one call, or that 374-scene autonomous reliability has been statistically established.
