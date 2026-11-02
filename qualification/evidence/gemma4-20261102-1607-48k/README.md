# Gemma 4: 1607 long-context evidence, 2026-11-02

Owner-run on the physical 32 GiB M2 Pro. Raw report and outputs are preserved.
The full suite failed: 48K passed 16/16 sections; 64K failed with 14/16.
The summary’s “last safe context” field describes memory only; it does not imply quality passed.

Owner approved a 49,152-token combined prompt and reserved-output deployment budget.
The 8,192 output ceiling is owner policy, not an inference measurement; no new output test was run.
The successful case measured 49,120 input + 1,915 completion = 51,035 actual tokens.
A request reserving 8,192 output therefore has at most 40,960 prompt tokens under this policy.
