# Studio Hub model manifests

This catalog contains **27** bounded model packages. The files describe
**78,024,484,788 expected bytes** across the packages; Qwen Base
packages reference the shared `whisper-turbo` package for their internal
quality check and do not copy its weights again.

Both numbers are measured, not maintained by hand: `tests/test_document_inventory.py` recomputes
them from the manifests in this directory and fails if this paragraph disagrees with them.

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
