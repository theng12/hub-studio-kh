| model | config | MLX peak GB | footprint peak GB | s/image (this Mac) | load avg | projected 8 GB: footprint GB | within the 4.5-5 GB target? | projected s/image |
|---|---|---|---|---|---|---|---|---|
| FLUX.2 Klein 4B 4-bit | before (what the worker did) | 9.79 | 9.41 | 81 (n=3) | 60 | 9.4 | no | 103 |
| FLUX.2 Klein 4B 4-bit | staged (8 GB tier, no streaming) | 4.69 | 4.98 | 84 (n=3) | 79 | 5.0 | borderline | 107 |
| FLUX.2 Klein 4B 4-bit | staged + streamed (8 GB tier) | 1.64 | 2.93 | 87 (n=3) | 49 | 2.9 | yes | 111 |
| Z-Image-Turbo 4-bit | before (what the worker did) | 6.26 | 7.46 | 294 (n=3) | 9 | 7.5 | no | 349 |
| Z-Image-Turbo 4-bit | staged (8 GB tier, no streaming) | 5.03 | 6.53 | 289 (n=3) | 12 | 6.5 | no | 343 |
| Z-Image-Turbo 4-bit | staged + streamed (8 GB tier) | 2.79 | 4.13 | 274 (n=3) | 11 | 4.1 | yes | 325 |

**MLX cache cap (seed 42)**

| model, configuration | cache cap GB | MLX peak GB | footprint peak GB | s/image |
|---|---|---|---|---|
| FLUX.2 Klein 4B 4-bit, bounded | 0.25 | 4.12 | 4.34 | 79 |
| FLUX.2 Klein 4B 4-bit, bounded | 0.5 | 4.00 | 4.55 | 80 |
| FLUX.2 Klein 4B 4-bit, bounded | 2.0 | 3.71 | 5.16 | 82 |
| FLUX.2 Klein 4B 4-bit, streamed | 0.25 | 1.61 | 2.32 | 85 |
| FLUX.2 Klein 4B 4-bit, streamed | 0.5 | 1.61 | 2.43 | 94 |
| Z-Image-Turbo 4-bit, streamed | 0.25 | 2.61 | 2.02 | 302 |
| Z-Image-Turbo 4-bit, streamed | 0.5 | 2.79 | 3.38 | 373 |

**Residency, FLUX.2 Klein 4-bit, lean (32 GB path)**

| model loaded fresh (n=4) | model already resident (n=2) | saved per image |
|---|---|---|
| 82.3 s | 79.2 s | 3.0 s |
