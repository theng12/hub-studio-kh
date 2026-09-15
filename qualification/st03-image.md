# ST-03 Image worker/runtime receipt — partial implementation proof

Source Image Studio 1.32.2 at exact commit
`36f88a0bc0abc07e3d7a8cf04424ab5336501827` is read-only. Selected
tracked `generation.py`, `main.py` and `sizes.py` SHA-256 inputs and the
narrow adopted behavior are recorded in `research/image-adoption.json`.
The FLUX package is `AITRADER/FLUX2-klein-4B-mlx-4bit` at immutable revision
`7fd24828501390b67a92c8b66d2fc5a707d0ba1a`. The complete source
snapshot was imported separately, then registered in place through the ST-01
helper at `/build/Projects/studiohub-standalone/model-library`;
`preflight` returns `files_verified` and manifest SHA-256
`e1f40ce0c9bd1303277ee5a943c2abac3d973eeeabd9cae7c58f6f7c23487e66`.
Existing Khmer, Whisper Base and Whisper Small folders remain in that library.

The ST-01 inference-only Image lock SHA-256 was
`6809919c3ec817d7de27d03cc7f2adfe2f52f283d842ea74098a45696cc07c51`.
Seven exact HTTP server distributions were added without changing any of its
66 package versions. Final lock SHA-256 is
`5fd5e79db7f8bec7dfa5c3ebc0fd1495ba7460138b44caf06bb6c0346c186b4f`.
`bash packaging/image-runtime/build.sh .build/st03-image-runtime-worker`
exited 0; log: `.build/st03-image-runtime-worker-build.log`. It rebuilt
CPython 3.12.14 from the SHA-verified archive, installed exact cached hashes,
rewrote external wheel install IDs/rpaths using the existing Voice packaging
helper, ad hoc signed rewritten Mach-O files, and wrote an immutable runtime
file manifest. Its macOS 14.0 arm64 audit passed all 194 binaries with zero
failures, resolving ST-01's 112 external IDs, two rpaths and unresolved
`libjaccl.dylib` path. Audit SHA-256:
`f603d0665c385bdecf88d2be8db5808d641cecdd8cf23eb3d4b945ce804617d5`;
runtime-files manifest SHA-256:
`461a3e6bffe7f52e81e7b3278ea5c96d335bae9bc8c23998fda681ae15f24175`.
Relocated imports succeeded twice under `python3 -I -B`, `env -i`,
`HF_HUB_OFFLINE=1`, including mflux 0.17.5, MLX/Metal 0.31.2,
FastAPI 0.141.1 and Uvicorn 0.52.4. Final runtime tree SHA-256:
`c9cd56a6d1cde6b72dee8ba22cf45fcbd640538207038087261cae91ec67f67e`.

Focused Image test command:
`/build/Projects/studiohub-standalone/.build/voice-runtime/bin/python3 -B -m unittest tests.test_image_standalone tests.test_image_bundle -v`
initially exited 0: five tests passed. Cases cover all-path owner token (including
loopback), missing `text_encoder/0.safetensors` refusing dispatch, invalid
model/mode/control requests, cancellation without final PNG, PNG dimensions,
bytes/hash/time, idempotent worker ACK, restart identity and same-size tamper
detection. The actual `-I -B` bundle-relative wrapper process fixture passed
two tests, including private loopback Uvicorn, absent-file catalog truth,
owner token, dispatch refusal and readiness cleanup after SIGTERM. Log:
`.build/st03-image-bundle-tests.log`. The test fixture uses a link to the
already audited runtime, so its server test proves process wiring rather than
another relocation claim.

After native-style dimensions and durable receipt edge cases were added, the
same focused command exited 0 with eleven tests. It also verifies editable
steps/guidance reach mflux, square preset inference, failed submit rollback,
failed running/final receipt stops or discards generation, ACK rollback, and
restart with an unreceipted final PNG. The dedicated Hub Image uncertainty
regression command
`PYTHONPATH=/build/Projects/studiohub-standalone/.build/test-deps /build/Projects/studiohub-standalone/.build/voice-runtime/bin/python3 -m pytest -q tests/hub_regression/cases/test_image_uncertainty.py`
initially exited 0: five cases. The final dedicated suite passed fifteen cases,
and the full Hub regression suite passed 47. The Image cases cover lost/malformed submission acceptance,
accepted worker poll loss, worker final-receipt uncertainty, known HTTP
rejection retry, restart identity, physical-machine fencing and same-ID replay
without another job. Broker persists the Image attempt before POST and the
accepted worker ID before poll. An uncertain attempt keeps its worker, machine,
attempt count and job ID, and is not queued for automatic retry. The narrow
authenticated reconciliation helper checks only that original worker job,
compares accepted job/model/runtime/worker/machine/request identity, and
adopts a completed output only with a complete hash/byte/URL receipt through
the existing Hub success path. Active, unreadable, missing, mismatched or
worker-persistence-uncertain receipts remain fenced. Expired GenStudio leases
and user cancellation after Image acceptance request worker cancellation but
keep the original attempt fenced until its real result is checked. Verified cancelled or
known terminal errors release that one machine's fence without resubmission.
Sol's bounded Broker review identified an additional malformed-status risk.
The Hub now checks the exact accepted Image job ID, state and revisions before
updating identity/progress on every normal and recovery poll, and requires a
complete final PNG receipt before success. Four new cases prove wrong/missing
ID, unknown state and missing PNG hash remain uncertain with the accepted
identity intact and zero adopted assets. The adapted model-file failure fixture
was updated to return the real worker revision/identity contract; its original
lost-poll retry assertion passes. The isolated regression wrapper no longer
assumes the historic literal `32 passed` count; `pytest` exit 0 proves its
current cases were collected and passed. Direct cases exited 0 with 47 passed;
the wrapper exited 0 with one passing wrapper test and wrote
`.build/st02-hub-regression.log`.

Current proof host: Apple M4 Mac mini, 16 GiB RAM, macOS 26.6.2. The historical
Image audit approves one 1K text-to-image workload at four steps and guidance
1.0 on a 16 GB Apple Silicon worker. Backend bounds remain editable 2–100 steps,
finite guidance 0–30 and the twelve source 1K aspect-ratio presets; settings
outside that historical workload are not qualified by its receipt. Editing,
reference-image, LoRA, negative prompt and 2K are not exposed. Real inference
through the assembled Hub/native bundle, export, bounded retry counts and
physical original-M1/Sonoma checks remain open. No generated-output cleanup
is claimed before ST-07's shared protected policy is integrated.

## Native and Hub integration checks

The coordinator added the native Image form, backend-provided presets/defaults,
durable pending request identity, original-result reconciliation, authenticated
preview and verified PNG export. Hub owns the separate Image process and audited
runtime. The private Image health request now includes the existing owner token;
the catalog decoder accepts the existing string-or-list language representation.
The Voice managed output root remains `voice/output` under the node data folder.

`python3 -m unittest discover -s tests -p 'test_*.py'` using the project's bundled
Voice interpreter ran 112 tests in 56.684 seconds: 111 passed and one skipped.
Log: `.build/st03-final-project-tests.log`. `bash native/build.sh` passed boundary
checks, API checks and full arm64/macOS-14-target compilation; log:
`.build/st03-native-reconcile-tests.log`. Sol independently reviewed the final
native reconciliation/route changes and found no remaining blocker; Terra's
managed Voice output-root finding was corrected and the process tests passed.
Real inference, output registration, preview/export and restart proof remain pending.
