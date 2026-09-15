# Whisper Base and Small stock

Completed 2026-09-15 after explicit owner download authorization. This is model-file
verification, not inference or original-M1/macOS-14 qualification.

| Package | Pinned revision | Verified files |
| --- | --- | --- |
| Whisper Base | `1e3e249fb8d01c655324bd6841b1deadffd6d04c` | 9 |
| Whisper Small | `45f3915923c7a79a5a5b5a7d909d39aeb0e5630e` | 9 |

Both packages were published to the project's ignored `model-library/` in the
original checkout, then passed the model-package CLI's full rehash against the
committed manifests. The persisted result for each is `files_verified`.

- Downloaded 628,667,821 bytes across nine unique files.
- Reused nine file placements; seven unique companion files (4,182,032 bytes).
- Completed in 423.76 seconds while Hub implementation continued independently.
- Existing Khmer stock was preserved; package staging was empty after publication.
- Existing Pinokio source and model caches were read-only reuse sources.
- The download agent also ran 43 model-package/catalog tests successfully.

Local detailed receipts and transfer log are retained in the original checkout:
`.build/whisper-download-2026-09-15.json` and `.build/whisper-download-2026-09-15.log`.
The coordinator inspected both package verification records and the transfer summary.
Later Subtitle work must prove actual model loading and transcription separately.
