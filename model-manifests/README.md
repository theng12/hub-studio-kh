# Studio Hub model manifests

This catalog contains **40** bounded model packages. The files describe
**177,026,562,705 expected bytes** across the packages; Qwen Base
packages reference the shared `whisper-turbo` package for their internal
quality check and do not copy its weights again.

Both numbers are measured, not maintained by hand: `tests/test_document_inventory.py` recomputes
them from the manifests in this directory and fails if this paragraph disagrees with them.

The source catalog includes Qwen Image 2.1 Turbo MLX 4-bit, owner-adopted for normal
production on 16 GB and larger Macs on 2026-10-13. Its Turbo-specific peak memory, speed
and output results remain unmeasured, and 8 GB is not claimed. Production eligibility does
not say whether a particular SSD or Mac already has the weights; its kit/install receipt does.

Each JSON manifest is schema version 1 and records the exact Hugging Face
commit, package paths, byte counts, SHA-256 values and immutable resolve URLs.
`qualification.status` is deliberately `unqualified`: an upstream LFS digest
proves the expected bytes, not a locally imported package, runnable runtime or
model result.

## Installed layout

Primary assets use `model/<upstream path>`. Their `source_path` is the original
publisher path, allowing an importer to recognize a complete, validated model
folder already in the selected model library.

Whisper models include their required tokenizer/processor assets only at
`companions/processor/<upstream path>`. Companion files intentionally have no
root-folder fallback: a root `config.json` is ambiguous and must not satisfy a
processor requirement. The `source.layout` mapping identifies every source
repository/revision. The Music packages are Stable Audio 3 Medium (its MLX
weights beneath the installer-owned `model/mlx/`) and YuE2 (a generator and its `yue2-vae`
decoder). ACE-Step 1.5 was retired in 0.6.116.

The catalog excludes model cards, Git attributes, licenses and example audio;
it keeps only required generation weights, indexes, configuration and
tokenizer/codec assets. Full source URLs and the metadata/tree endpoints used
for the capture remain in each manifest's `source` object.

One package takes a file from a second repository. `fish-audio-s2-pro-4bit`
(`majentik/fishaudio-s2-pro-MLX-4bit`) publishes its codec as `codec.pth`, which
the pinned mlx-audio cannot read: it silently loads the 4-bit LLM as the codec
and produces noise. The package omits `codec.pth` and ships `codec.safetensors`
from `mlx-community/fish-audio-s2-pro-8bit` at that package's pinned revision
(the converted tensors are bit-identical). The capture spec's `foreign_files`
records it as a second `source`, a `source.layout.companions` entry and the
file's own immutable URL, so the manifest lists exactly the bytes that ship.
The Fish 4-bit package also keeps the publisher's `LICENSE.md` and `NOTICE`
(Fish Audio Research License).

## Refreshing metadata

Run from the repository root:

```sh
python3 packaging/capture_model_manifests.py --write --output model-manifests
python3 -m unittest tests/test_model_manifest_catalog.py -v
```

The capture tool reads only fixed snapshot metadata. When the Hugging Face tree
lacks an LFS object SHA-256, it reads a required non-weight file no larger than
10 MiB to hash it and refuses a larger unknown-digest file. It never downloads
model-weight payloads.
