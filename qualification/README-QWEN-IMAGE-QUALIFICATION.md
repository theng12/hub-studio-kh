# Qwen Image 2.1 4-bit physical qualification

These commands answer whether the two pinned Qwen Image 2.1 4-bit packages run safely on the
owner's physical 24 GB and 16 GB Macs. They do not change production admission. Until the stored
receipts pass review, both packages retain the conservative 32 GB eligibility gate.

The low-memory package completed four packaged 1K cases on the physical 32 GB M2 Max, peaking
at 5.85 GB with normal pressure and no swap growth. This does not qualify the physical 16 GB tier.

| Physical test | Package | Exact revision | Runtime |
| --- | --- | --- | --- |
| `RUN-QWEN-IMAGE-24GB.command` | `qwen-image-2-1-mlx-serve-4bit` — Qwen Image 2.1 MLX affine 4-bit | `88eb1b3bb5591ed59b68a6e1a1c2d9baade73e38` | packaged mlx-serve 26.10.1; macOS 26.2+ |
| `RUN-QWEN-IMAGE-16GB.command` | `qwen-image-2-1-mflux-q4` — Qwen Image 2.1 MLX affine Q4 | `746a58556820933a2df5c75887a2570f1ad200c0` | packaged mflux low-memory path; macOS 14+ |

The two packages are distinct. The 16 GB mflux run does not reinterpret or substitute the normal
mlx-serve weights. Both must be present in the kit's `model-library` at their pinned revisions.

## Run it

1. Use the command that matches the Mac's physical memory.
2. Quit Studio Hub and finish ordinary Image work. The command refuses while another Image worker
   or mlx-serve process is present, and it checks again immediately before starting its private
   worker in case Studio Hub restarted. It never stops someone else's process.
3. Double-click the command in the kit's `Technical` folder and leave the Mac on power.
4. Review `summary.txt`, `measurement.json`, `results.csv`, `worker.log`, and the generated PNGs in
   the dated result folder beside the command.

The default run uses the app stored in the same kit, the model package stored in that kit, and the
app's bundled Voice Python for its existing psutil measurement dependency. It launches the packaged `image_worker.py` offline with an owner-only
state folder and an APFS copy-on-write clone of only the selected package. It does not read the live
Hub token, change the installed Hub's settings, register ownership over the kit's real library, or
send prompts or images over the network. The private package clone is kept under the kit's
`_qualification-private` folder and is not part of the receipt to share.

Before starting, the runner verifies `manifest/runtime-files.json` against every file, mode and
symlink in the packaged Image runtime. It also computes the staging pipeline's content-only tree
digest and requires it to match the Image runtime SHA-256 in `release-manifest.json`. This catches a
corrupt or rerouted SSD runtime while allowing a valid app copy whose harmless outer metadata
differs.

Each default run executes, one at a time:

- process-cold 1024×1024, 16 steps;
- process-warm 1024×1024, 16 steps;
- process-warm 1280×720, 16 steps;
- process-warm 720×1280 portrait, 16 steps.

Pass `--stress-40` to add square, landscape, and portrait 40-step cases. “Cold” means a newly
started worker with no resident model; the receipt does not claim that macOS's filesystem cache was
cold. The worker's own `model_reused` field records whether a later case actually reused a model.

Per case, the receipt records the pinned model and runtime identities, wall and worker duration,
the packaged worker/native-child process-tree footprint sampled each second, MLX's own peak when
the worker reports it, admission headroom in GB at start/minimum/end, memory pressure, peak and
end swap growth,
free-memory percentage, responsiveness, internal-SSD writes net of an idle baseline, artifact
dimensions, bytes and SHA-256. Admission headroom uses the packaged app's exact
`components/host_memory.py` arithmetic over `psutil.virtual_memory()`. The `memory_pressure -Q`
percentage remains a pressure signal and is not treated as memory available to admit a job.
The outcome cannot pass RAM qualification unless the process-tree peak is positive and pressure,
swap before/peak/end, and admission headroom start/minimum/end were all sampled. Missing telemetry
produces `NOT MEASURED`; swap grading uses peak growth even if swap falls again before the case ends.

An unsupported host or missing prerequisite is **NOT MEASURED**. In particular, 8 GB remains NOT
MEASURED, and the normal native package is NOT MEASURED below macOS 26.2. A timeout or crash on a
target Mac is a measured failure for that exact host and pinned package; it is not generalized into
a claim about an untested tier.

The generated lettering prompt makes visual inspection convenient. Its language does not restrict
which model the owner selects or where the model is available; RAM availability follows reviewed
physical qualification receipts.

Optional command-line controls:

```sh
qwen-image-qualification.sh --tier 24 --stress-40
qwen-image-qualification.sh --tier 16 --prompt 'your review prompt'
```

`--allow-non-target` is a harness check only. Its measurements describe that Mac and do not qualify
the requested 16 GB or 24 GB tier.

A developer trial with `--allow-non-target` records `target_tier_verified: false` and cannot qualify
the requested physical tier. The ordinary SSD command accepts only the actual 16 GB or 24 GB tier.
