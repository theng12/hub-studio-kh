#!/usr/bin/env python3
"""Capture the bounded Studio Hub model catalog from immutable HF snapshots.

This tool requests repository and tree metadata only.  It retrieves a file body
only when Hugging Face metadata lacks a digest and the file is at most 10 MiB;
model weights must expose their LFS SHA-256 metadata.

Nothing about a model is implied.  Every package the catalog ships needs an
explicit ``CHOICES`` entry (what to fetch) and a matching ``DETAILS`` entry (the
defaults and memory floors), and this tool refuses to run when the two disagree
with ``model-manifests/``:

* a manifest with no ``CHOICES`` entry is named as a package this tool cannot
  reproduce, because a re-capture would silently drop it;
* a ``CHOICES`` entry with no ``DETAILS`` entry is named with the file to edit,
  instead of raising a bare ``KeyError`` that names nothing;
* every field a spec supplies is compared with the shipped manifest, so a stale
  revision, a wrong engine or a changed memory floor is a failure rather than a
  quiet rewrite;
* two packages for the same engine declaring ``"default": true`` are refused - each
  engine may name one default.

``requires_packages`` is written only when the package has companions, and the
optional ``subtitle``, ``default`` and ``production_allowed`` declarations of a
``CHOICES`` entry are passed through verbatim, so hand-declared policy survives
the next ``--write``.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
# `sys` was used five times and never imported, so every path that reaches the withhold branch —
# the branch that refuses to replace a manifest, which is the one protecting a package from being
# silently regenerated — died with a NameError instead of saying why it would not write.
import sys
from fnmatch import fnmatch
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen


API_ROOT = "https://huggingface.co/api/models/"
WEB_ROOT = "https://huggingface.co/"
MAX_SMALL_FILE_BYTES = 10 * 1024 * 1024
MAX_METADATA_BYTES = 10 * 1024 * 1024
# The capture date every manifest records. It is a tool-level constant rather than the wall
# clock on purpose: a re-capture of unchanged pinned revisions must reproduce byte-identical
# manifests, so `--write` over `model-manifests/` stays a no-op for packages nothing changed.
# (The Khmer VITS and ST-16 captures shipped under this same value.)
CAPTURE_DATE = "2026-09-15"
# The file a person edits when a check below names a missing CHOICES or DETAILS entry.
SPEC_FILE = "packaging/capture_model_manifests.py"
PROCESSOR_FILES = frozenset({
    "added_tokens.json", "merges.txt", "normalizer.json", "special_tokens_map.json",
    "tokenizer.json", "tokenizer_config.json", "vocab.json",
})
# The optional per-model subtitle contract a CHOICES entry may carry. It is passed through
# verbatim; every key is required when the block is present, because a half-declared contract
# is worse than none - the Subtitle worker reads these fields to decide what to advertise.
SUBTITLE_FIELDS: dict[str, type] = {
    "family": str, "languages": list, "segment_timestamps": bool,
    "word_timestamps": bool, "long_form": bool,
}


CHOICES: tuple[dict[str, Any], ...] = (
    {"package_id": "flux2-klein-4b-mlx-4bit", "model_id": "AITRADER/FLUX2-klein-4B-mlx-4bit", "revision": "7fd24828501390b67a92c8b66d2fc5a707d0ba1a", "engine": "image-mflux"},
    {"package_id": "qwen-image-2-1-mlx-serve-4bit", "model_id": "ddalcu/Qwen-Image-2.1-MLX-Serve-4bit", "revision": "88eb1b3bb5591ed59b68a6e1a1c2d9baade73e38", "engine": "image-mlx-serve", "minimum_macos": "26.2", "captured_at": "2026-10-06", "quantization": "MLX 4-bit", "quantization_source": "the publisher model id states it: ddalcu/Qwen-Image-2.1-MLX-Serve-4bit", "license": {"weights": "Apache-2.0"}},
    # The weights are Qwen's own (qwen-research), not Apache-2.0: the previous record said
    # Apache-2.0 for this pack, which is the licence of the mlx-community *conversion* tooling,
    # not of the model it converted (owner finding, 2026-10-26). The Turbo row below already
    # recorded `qwen-research`, so the two Qwen packs disagreed about one upstream model.
    {"package_id": "qwen-image-2-1-mflux-q4", "model_id": "mlx-community/Qwen-Image-2.1-mflux-q4", "revision": "746a58556820933a2df5c75887a2570f1ad200c0", "engine": "image-mflux", "captured_at": "2026-10-06", "quantization": "MLX 4-bit", "quantization_source": "the publisher model id states q4: mlx-community/Qwen-Image-2.1-mflux-q4", "license": {"weights": "qwen-research"}},
    # Qwen Image 2.1 Turbo is a distinct official eight-step checkpoint, not the Viggle LoRA.
    # The pinned model_index.json declares its eight sample_sigmas and pipeline components.
    {"package_id": "qwen-image-2-1-turbo-mflux-q4", "model_id": "mflux-community/qwen-image-2-1-turbo-mflux-q4", "revision": "bda8951ec6b8cc288a961432fe15c22433521a1d", "engine": "image-mflux", "captured_at": "2026-10-12", "production_allowed": True, "display_name": "Qwen Image 2.1 Turbo Low-RAM MLX 4-bit", "quantization": "MLX 4-bit", "quantization_source": "the publisher checkpoint is the Qwen Image 2.1 Turbo quantized MLX 4-bit conversion", "license": {"weights": "qwen-research"}},
    # ── ST-16: the second Image model, Z-Image-Turbo on mflux 0.20.0 ──
    # The publisher snapshot's root *is* the model directory: `transformer/`,
    # `text_encoder/`, `vae/` and `tokenizer/` sit at the top level with no
    # intervening `model/`. The capture keeps the library's uniform destination
    # (`path` = `model/<publisher path>`) and records the flat publisher path as the
    # `source_path` alias, which is what `model_packages._file_path` falls back to
    # when the installed `model/` path is absent - so the raw pack imports, or
    # registers in place, without a worker special case. `.DS_Store` and the three
    # example images are publisher furniture, not inference data; `README.md` and
    # `.gitattributes` are dropped by `required_primary_paths` already.
    {"package_id": "z-image-turbo-mflux-4bit", "model_id": "filipstrand/Z-Image-Turbo-mflux-4bit", "revision": "b3a8f31115a11f2f9e2fa0bfbc8d78dcc3e6568b", "engine": "image-mflux", "exclude": (".DS_Store", "z_image_example.png", "z_image_example_4bit.png", "z_image_turbo_example.jpg")},
    {"package_id": "qwen3-tts-12hz-0-6b-base-8bit", "model_id": "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit", "revision": "50f45ef0047cde7e84c2ef04326acb8ada2436a7", "engine": "voice-mlx", "requires_packages": ["whisper-turbo"]},
    {"package_id": "qwen3-tts-12hz-0-6b-base-4bit", "model_id": "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-4bit", "revision": "0d6bb6fe33f92d47a507e23b9148940e8366ab5b", "engine": "voice-mlx", "requires_packages": ["whisper-turbo"]},
    {"package_id": "qwen3-tts-12hz-1-7b-base-4bit", "model_id": "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-4bit", "revision": "37e955a1deb861c088ae5f3a67043185f3d1a60c", "engine": "voice-mlx", "requires_packages": ["whisper-turbo"]},
    {"package_id": "voxcpm2-4bit", "model_id": "mlx-community/VoxCPM2-4bit", "revision": "dc9e5c187858da5f4a13dc4c247e297339216381", "engine": "voice-mlx"},
    {"package_id": "voxcpm2-8bit", "model_id": "mlx-community/VoxCPM2-8bit", "revision": "d52725898a0675703f7f9ddc5a4d1a3cdbb99032", "engine": "voice-mlx"},
    {"package_id": "voxcpm2-bf16", "model_id": "mlx-community/VoxCPM2-bf16", "revision": "a7fb80d9056983135a54ca3f71fcc5a637ccddf5", "engine": "voice-mlx"},
    {"package_id": "omnivoice-bfloat16", "model_id": "mlx-community/OmniVoice-bfloat16", "revision": "6119f7072580cc30c3cbdf496269216b03ad9742", "engine": "voice-mlx"},
    {"package_id": "whisper-turbo", "model_id": "mlx-community/whisper-large-v3-turbo", "revision": "a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb", "engine": "subtitle-mlx", "processor": ("openai/whisper-large-v3-turbo", "41f01f3fe87f28c78e2fbf8b568835947dd65ed9")},
    {"package_id": "whisper-small", "model_id": "mlx-community/whisper-small-mlx", "revision": "45f3915923c7a79a5a5b5a7d909d39aeb0e5630e", "engine": "subtitle-mlx", "processor": ("openai/whisper-small", "973afd24965f72e36ca33b3055d56a652f456b4d")},
    {"package_id": "whisper-base", "model_id": "mlx-community/whisper-base-mlx", "revision": "1e3e249fb8d01c655324bd6841b1deadffd6d04c", "engine": "subtitle-mlx", "processor": ("openai/whisper-base", "e37978b90ca9030d5170a5c07aadb050351a65bb")},
    # ── Phonon-2 (owner decision 2026-10-05): optional English transcription next to Whisper ──
    # FermionResearch/Phonon-2 is NVIDIA Parakeet TDT 0.6B v3 with a five-value (~2.1-bit) encoder
    # and int6 tables. The official repo ships one `phonon-2.bps.tar.zst` container (needs
    # zstandard, which the voice runtime does not carry) in HF-Transformers tensor names. This
    # community conversion is the same bits verbatim in mlx-audio's Parakeet names plus that
    # family's own config.json, so `transcription.load_phonon2` only unpacks the blocks.
    # ATTRIBUTION.md and both licence texts ship: CC-BY-4.0 requires the attribution to travel.
    {"package_id": "phonon-2-mlx", "model_id": "selcukkubur/Phonon-2-mlx", "revision": "71936ba3bf6479d299b97fdfe2d53e36d9375fe5", "engine": "subtitle-mlx",
     "subtitle": {"family": "phonon2", "languages": ["en"], "segment_timestamps": True, "word_timestamps": True, "long_form": True},
     "license": {"weights": "CC-BY-4.0", "code": "Apache-2.0",
                 "attribution": "Phonon-2 by FermionResearch (huggingface.co/FermionResearch/Phonon-2), derived from NVIDIA Parakeet TDT 0.6B v3 (huggingface.co/nvidia/parakeet-tdt-0.6b-v3), CC-BY-4.0; MLX-layout conversion by selcukkubur (huggingface.co/selcukkubur/Phonon-2-mlx).",
                 "files": ["model/ATTRIBUTION.md", "model/LICENSE-WEIGHTS-CC-BY-4.0.txt", "model/LICENSE-CODE-Apache-2.0.txt"]}},
    # ── Khmer VITS families (ST-12, owner request 2026-09-24) ──
    # Two non-MLX, single-speaker Khmer engines under the `voice-vits` packaging
    # engine. Both are payload-trimmed: the MMS release's benchmark/training
    # folders and the ONNX repo's training checkpoint + alternate exports are not
    # needed for inference and would multiply the kit size for no benefit.
    {"package_id": "khmer-tts-mms", "model_id": "khmerttsopensource/khmer-tts", "revision": "c9380a9d1c817f2b21980d54f29f7a61b0d626f5", "engine": "voice-vits", "exclude": ("eval/*", "training/*", "UPLOAD.md")},
    {"package_id": "khmer-tts-female-onnx", "model_id": "sengtha/khmer-tts-female-v2", "revision": "c6fde511a839c4f20cdce96e2a35735d493030d2", "engine": "voice-vits", "exclude": ("*.pth", "khmer_tts.onnx", "khmer_tts_ls100.onnx", "khmer_tts_ls115.onnx", "config.json")},
    # ── YuE2: the Music lane's third engine, and its decoder companion ──
    # The generator pack is the 8-bit AR, the bf16 NAR and the tokenizer. **`ar-bf16.safetensors`
    # is captured even though this lane runs 8-bit**, because the port's `verify_conversion`
    # refuses a converted tree that does not carry it — the bf16 file is what the 8-bit one was
    # quantized from, and the port uses it to prove the tree is the one it produced. Measured
    # 2026-09-27: the three weight files plus the two licence notices are what a run needs.
    # `LICENSE` is kept (0.6.168): the port refuses a tree without it ("Converted directory has
    # missing or unexpected files: missing=['LICENSE']", every fleet Mac, 2026-10-12), and the
    # furniture rule had dropped it, so YuE2 had never sung on the fleet.
    {"package_id": "yue2-3b-mlx", "model_id": "vanch007/mlx-Yue2-3B",
     "revision": "fa66d203dd56d7e033e05ee8b32269768984c4cc", "engine": "music-yue2",
     "requires_packages": ("yue2-vae",), "keep": ("LICENSE",),
     "exclude": ("assets/*", "*.png", "*.pdf", "*.svg", "__pycache__/*")},
    # The decoder. The port's own README installs it from this repository and passes it as
    # `--vae`; the generator pack ships without it, so a run without this companion fails at load
    # rather than producing quiet audio.
    {"package_id": "yue2-vae", "model_id": "m-a-p/YuE2-Vae",
     "revision": "9a94e1d0ea9f8087e98f77fa88df4a4068104d2a", "engine": "music-yue2",
     "include": ("config.json", "model.safetensors", "modeling_vae.py", "weights_manifest.json",
                 "LICENSE", "THIRD_PARTY_NOTICES.md", "licenses/*")},

    # ── ST-14: the local Chat Studio's MLX text model ──
    # Owner-selected Qwen3.5-9B, uniform 4-bit, text-only accepted. The OptiQ build was
    # rejected: its index requires an `optiq_vision` shard, making the snapshot 8.22 GB.
    {"package_id": "qwen3-5-9b-4bit", "model_id": "mlx-community/Qwen3.5-9B-4bit", "revision": "8b2b98c00a6b4d291155e4890773ca8f769aee53", "engine": "chat-mlx", "default": True},
    # Gemma 4 MoE QAT, qualified only on the owner's physical 32 GiB M2 Max. The verified
    # SSD stock keeps all eleven canonical files; runtime capture omits only README.md.
    {"package_id": "gemma4-26b-a4b-qat-4bit",
     "model_id": "mlx-community/gemma-4-26B-A4B-it-qat-4bit",
     "revision": "0e3cbab38ce568cf6e23543010d08d03b731910c", "engine": "chat-mlx",
     "captured_at": "2026-10-07", "production_allowed": True,
     "max_output_tokens": 8192,
     "display_name": "Gemma 4 26B-A4B QAT MLX 4-bit",
     "quantization": "QAT MLX 4-bit",
     "quantization_source": "model/config.json quantization: affine 4-bit, group size 64; preserved publisher per-tensor precision overrides"},
    # Qwen3.6 MoE. The full verified SSD stock has sixteen canonical files; runtime capture omits
    # only README.md. The checkpoint config identifies qwen3_5_moe and keeps its per-tensor 8-bit
    # gate overrides. Admitted 2026-10-27 with the rest of the name blocks removed (owner: "We no
    # longer block or lock any model"), so its memory floor is the only gate - and it is
    # PROVISIONAL: 24 GB is the next published fleet tier above the 19.03 GiB this package
    # actually ships, not a measured peak. Replace it with a measurement before calling it
    # qualified.
    {"package_id": "qwen36-35b-4bit",
     "model_id": "mlx-community/Qwen3.6-35B-A3B-4bit",
     "revision": "38740b847e4cb78f352aba30aa41c76e08e6eb46", "engine": "chat-mlx",
     "captured_at": "2026-10-07", "production_allowed": True,
     "display_name": "Qwen 3.6 35B-A3B MLX 4-bit",
     "quantization": "MLX 4-bit",
     "quantization_source": "model/config.json quantization; preserved per-tensor 8-bit gate overrides"},
    # Qwen3.8 dense 27B, owner-selected by name (2026-11-21). The only 24 GB-class model in the
    # research note that keeps a long context (hybrid GatedDeltaNet/GatedAttn, 16 of 64 layers
    # full attention), so it is the one dense model worth 16 GB of every Mac's disk. Runtime
    # capture follows the established policy: inference files only. The publisher's
    # `preprocessor_config.json`, `video_preprocessor_config.json` and `processor_config.json`
    # describe a vision path this chat engine never calls, and `README.md`/`.gitattributes` are
    # publisher furniture - all five are excluded rather than shipped as dead weight.
    {"package_id": "qwen38-27b-4bit",
     "model_id": "lmstudio-community/Qwen3.8-27B-MLX-4bit",
     "revision": "6067b15cf581666a4aecf6af3afaba4bb5efc20c", "engine": "chat-mlx",
     "captured_at": "2026-11-21", "production_allowed": True,
     "display_name": "Qwen 3.8 27B MLX 4-bit",
     "quantization": "MLX 4-bit",
     "quantization_source": "model/config.json quantization; preserved per-tensor overrides",
     "exclude": ("README.md", ".gitattributes", "preprocessor_config.json",
                 "video_preprocessor_config.json", "processor_config.json")},
    # Owner-selected MoE. The full stock package keeps all eleven publisher-verified files; the
    # runtime manifest follows the established capture policy and omits the README, leaving ten
    # inference files. Admitted 2026-10-27 like qwen36. Its usable context is still unknown, and
    # its memory floor is PROVISIONAL: 24 GB is the next published fleet tier above the 16.57 GiB
    # this package ships, not a measured peak.
    {"package_id": "nemotron-lightning-4bit",
     "model_id": "mlx-community/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-4bit",
     "revision": "55ac8c89261109b36c04371cd3f479a4594208c8", "engine": "chat-mlx",
     "captured_at": "2026-10-07", "production_allowed": True,
     "display_name": "Nemotron 3.5 Lightning 30B-A3B MLX 4-bit",
     "quantization": "MLX 4-bit affine",
     "quantization_source": "model/config.json quantization; preserved checkpoint tensor overrides"},
    # Gemma 3 12B Instruct. Formerly LTX's text encoder under retired video; admitted 2026-10-27
    # as an independent chat-mlx model with the rest of the name blocks lifted (owner: "We no
    # longer block or lock any model"). Multimodal checkpoint whose vision tower/processor
    # is preserved, but chat worker currently serves text-only. Memory floor is PROVISIONAL:
    # 16 GB is the next published fleet tier above the 7.51 GiB (8.07 GB) weights this package
    # ships (8 GB cannot hold them); 24 GB is recommended.
    {"package_id": "gemma-3-12b-it-4bit",
     "model_id": "mlx-community/gemma-3-12b-it-4bit",
     "revision": "86cc6a8dedbc456dd0e4af01a9d09f396f77e558", "engine": "chat-mlx",
     "captured_at": "2026-09-15", "production_allowed": True,
     "display_name": "Gemma 3 12B Instruct MLX 4-bit",
     "quantization": "MLX 4-bit",
     "quantization_source": "the publisher's model id states it: mlx-community/gemma-3-12b-it-4bit"},
    # ── ST-13.A: Kyutai Pocket TTS 4-bit, the English cloning engine ──
    # The 4-bit MLX conversion is the whole executable model: 4-bit weights, the flow-LM
    # config and the mimi codec live in one 89 MB safetensors file. It carries no tokenizer
    # and no voice prompts, because its config.json names both by `hf://` URL against a
    # DIFFERENT repository (kyutai/pocket-tts-without-voice-cloning @ d4fdd22a). Those two
    # companions are what make a generation possible at all, and on an offline kit node
    # nothing can fetch them, so both are packaged and both are declared as dependencies.
    {"package_id": "pocket-tts-4bit", "model_id": "mlx-community/pocket-tts-4bit", "revision": "59592598c532759d2b66a3d8905a612dcbe8419c", "engine": "voice-mlx", "requires_packages": ["pocket-tts-tokenizer", "pocket-tts-preset-voices"]},
    # ── Breeze TTS 2 4-bit (owner request 2026-09-29) ──
    # The publisher ships three files that are not inference data: `.gitattributes`, its own
    # `README.md`, and a demo video. `LICENSE` and `NOTICE` are deliberately NOT excluded — the
    # pack's licence is the one fact about a model that has to travel with it.
    #
    # `Breeze-TTS-2-demo.mp4` is why `exclude` matters here rather than being tidiness: it is a
    # video file inside a voice package, and the kit copies what the manifest lists. Left in, it
    # would ride on all four drives and into every install for no inference value at all.
    {"package_id": "breeze-tts-2-mlx-4bit", "model_id": "mlx-community/Breeze-TTS-2-mlx-4bit", "revision": "3a06d26b172ea4ae1da2f42d708383e9c79d5526", "engine": "voice-mlx", "exclude": (".gitattributes", "README.md", "Breeze-TTS-2-demo.mp4")},
    # `tokenizer.model` is loaded on EVERY generation: the flow-LM's text conditioner calls
    # mlx_audio's `download_if_necessary("hf://kyutai/pocket-tts-without-voice-cloning/
    # tokenizer.model@d4fdd22a…")`, which resolves through `hf_hub_download` against the
    # Hugging Face cache. Without these exact bytes in that cache the engine cannot encode
    # text, online or not.
    {"package_id": "pocket-tts-tokenizer", "model_id": "kyutai/pocket-tts-without-voice-cloning", "revision": "d4fdd22ae8c8e1cb3634e150ebeff1dab2d16df3", "engine": "voice-mlx", "include": ("tokenizer.model",)},
    # The eight preset voice prompts come from the same repository at the same revision and
    # are fetched the same way, but only when no reference clip is supplied: `generate()`
    # falls back to `DEFAULT_AUDIO_PROMPT = "alba"`, so the zero-shot path downloads
    # `embeddings/alba.safetensors` and fails offline without it. 4.4 MB of prompts is worth
    # carrying so "clone or preset" is true on a node with no network, as the catalogue says.
    {"package_id": "pocket-tts-preset-voices", "model_id": "kyutai/pocket-tts-without-voice-cloning", "revision": "d4fdd22ae8c8e1cb3634e150ebeff1dab2d16df3", "engine": "voice-mlx", "include": ("embeddings/*.safetensors",)},
    # ── Adopted from the local Voice Studio cache (2026-09-20) ──
    # These five shipped before they had a capture spec, so `--write` could not reproduce them
    # and the catalog check below had nothing to compare. Repository, revision, engine,
    # companion and memory values are read from `model-manifests/<package_id>.json`.
    # `exclude` carries `.gitignore` because the cache importer that wrote these manifests
    # skipped it while the publisher tree would otherwise bring it back on the next capture.
    {"package_id": "chatterbox-4bit", "model_id": "mlx-community/chatterbox-4bit", "revision": "f1d7b9696e1b6242e64eb8c4a823b6d1a50425a8", "engine": "voice-mlx", "requires_packages": ["s3tokenizerv2"], "exclude": ("*.gitignore",)},
    {"package_id": "chatterbox-8bit", "model_id": "mlx-community/chatterbox-8bit", "revision": "9617d61b596a03d1bed766a28c341680e993a1b9", "engine": "voice-mlx", "requires_packages": ["s3tokenizerv2"], "exclude": ("*.gitignore",)},
    {"package_id": "fish-audio-s2-pro-8bit", "model_id": "mlx-community/fish-audio-s2-pro-8bit", "revision": "c8d4481b3f7cbfe64d855c8b7cda7739502fc3ff", "engine": "voice-mlx", "exclude": ("*.gitignore",)},
    # `LICENSE.md` is publisher furniture the selection rule drops by default, but this package
    # ships it - `keep` is the per-package override, so the next capture does not lose the file.
    {"package_id": "fish-audio-s2-pro-bf16", "model_id": "mlx-community/fish-audio-s2-pro-bf16", "revision": "eccd57bf5c1ebc13cb2f993df867f4e49931a36a", "engine": "voice-mlx", "exclude": ("*.gitignore",), "keep": ("LICENSE.md",)},
    # ── Fish S2 Pro 4-bit (owner request 2026-10-02): a community conversion with a WRONG codec ──
    # The publisher ships the codec as `codec.pth` (PyTorch). mlx-audio 0.5.7's Fish DAC loader looks
    # for `codec.safetensors`, then `model.safetensors`, and so loads the 4-bit LLM as codec weights
    # (strict=False, no error): every job "succeeds" with noise (Whisper WER 1.0, measured
    # 2026-10-01, `_orchestra/audit-20260930/fish-4bit/`). The codec is the same 1.87 GB fp32 network
    # in every Fish S2 Pro tier: `codec.pth` converted with the DAC's own `sanitize()` is bit-identical,
    # tensor for tensor (541 of 541), to `codec.safetensors` in mlx-community/fish-audio-s2-pro-8bit.
    # So this package drops `codec.pth` and takes that file, at the 8-bit package's pinned revision,
    # from the 8-bit repository: `foreign_files` records it as a second source with its own
    # `source.layout` companion entry, so the manifest lists exactly the bytes that ship.
    # `LICENSE.md` and `NOTICE` ship with it: the Fish Audio Research License requires the NOTICE.
    {"package_id": "fish-audio-s2-pro-4bit", "model_id": "majentik/fishaudio-s2-pro-MLX-4bit", "revision": "dda28aa6e33fe946826792e3feb643e9c463b75f", "engine": "voice-mlx", "exclude": ("codec.pth",), "keep": ("LICENSE.md",),
     "foreign_files": ({"repo": "mlx-community/fish-audio-s2-pro-8bit", "revision": "c8d4481b3f7cbfe64d855c8b7cda7739502fc3ff", "path": "codec.safetensors",
                        "purpose": "Fish S1 DAC codec in the safetensors form mlx-audio loads; replaces the publisher's codec.pth (bit-identical tensors)"},)},
    {"package_id": "s3tokenizerv2", "model_id": "mlx-community/S3TokenizerV2", "revision": "e0c9886f0e1c35ae85b1f27277416fb19fc72bec", "engine": "voice-mlx", "exclude": ("*.gitignore",)},
    # ── Music: the engines the Music Studio ships (Stable Audio 3 and YuE2; ACE-Step 1.5 was
    # retired in 0.6.116) ──
    # The publisher keeps this bundle under `MLX/`; the shipped manifest records the
    # lower-case `model/mlx/` destination, which is the same file on macOS's
    # case-insensitive volume and is why the file-selection check compares case-insensitively.
    {"package_id": "stable-audio-3-medium", "model_id": "stabilityai/stable-audio-3-optimized", "revision": "da6edc54ddba10bfd79a077102ded687f80e882b", "engine": "music-mlx", "exclude": ("*.gitignore",)},
    # ── Video: no pack is catalogued. The local video pack and its text encoder were retired
    # (2026-10-01 and 2026-10-02): their capture rows are gone, an installed copy moves to the
    # Trash Folder by `components/retired_models.py`, and their manifests live dormant in
    # `components/video/retired/` where no catalogue reads them. ──
    # ── Embeddings (owner decision 2026-10-11): Story's meaning-based idea duplicate check ──
    # BGE small en v1.5 in mlx-community's bf16 MLX build: a plain BERT encoder, 384 dimensions,
    # MIT. Run by the Embeddings worker in pure MLX on the shared Voice runtime (no new package).
    {"package_id": "bge-small-en-v1-5-bf16", "model_id": "mlx-community/bge-small-en-v1.5-bf16",
     "revision": "0e415031434cdf5f1b89d584e11be33b82abfc8d", "engine": "embed-mlx",
     "captured_at": "2026-10-11", "default": True,
     "display_name": "BGE small en v1.5 MLX bf16",
     "quantization": "MLX bf16",
     "quantization_source": "the publisher model id states it: mlx-community/bge-small-en-v1.5-bf16",
     "license": {"weights": "MIT"}},
    # ── Vision (owner decisions 2026-10-11): Story's scene analysis for the E2-E4 effects ──
    # Depth Anything V2 *Small* only: Base and Large are CC-BY-NC-4.0 and the channels are monetised.
    # Run by the Vision worker in plain MLX (components/vision/app/backend/depth.py), no new package.
    {"package_id": "depth-anything-v2-small", "model_id": "depth-anything/Depth-Anything-V2-Small-hf",
     "revision": "5426e4f0f36572d16453bbda7a8389317b1bef99", "engine": "vision-mlx",
     "captured_at": "2026-10-11", "display_name": "Depth Anything V2 Small",
     "quantization": "float32",
     "quantization_source": "the publisher's model.safetensors stores F32 tensors",
     "license": {"weights": "Apache-2.0"}},
    # Florence-2 in the transformers-native layout (florence-community, MIT; safetensors, no remote
    # code). Base is the default (owner: "start with BASE"); Large is the same code and switches in
    # by installing its package and naming it, with no release (components/vision/app/backend/florence.py).
    {"package_id": "florence-2-base-ft", "model_id": "florence-community/Florence-2-base-ft",
     "revision": "0b03b6f15a4a211370fb204aee4e7dd48887ea37", "engine": "vision-mlx",
     "captured_at": "2026-10-11", "default": True, "display_name": "Florence-2 base (ft)",
     "quantization": "float16",
     "quantization_source": "the publisher's config.json states dtype float16; the worker computes in float32",
     "license": {"weights": "MIT"}},
    {"package_id": "florence-2-large-ft", "model_id": "florence-community/Florence-2-large-ft",
     "revision": "26b734a54fdfbf9c398351eedfabb7f27fc470b7", "engine": "vision-mlx",
     "captured_at": "2026-10-11", "display_name": "Florence-2 large (ft)",
     "quantization": "float16",
     "quantization_source": "the publisher's config.json states dtype float16; the worker computes in float32",
     "license": {"weights": "MIT"}},
)

DETAILS: dict[str, dict[str, Any]] = {
    "flux2-klein-4b-mlx-4bit": {"defaults": {"size_pixels": 1024, "steps": 4, "guidance": 1.0, "parameters_source": "research/rescope-model-inventory-2026-09-15.md"}, "memory": (8, 16, "MEASURED by fleet production use rather than by a stored peak-memory receipt: the owner has produced tens of thousands of images with this pack on his 8 GB Apple-silicon Macs, which is why the fleet's own admission table (components/hub/backend/memory_admission.py, FLUX.2 Klein 4-bit) has enforced an 8 GB total floor since qualification. No peak footprint was recorded for those runs, so `status` stays `unqualified` - the 8 GB is a production-use floor, not a lab receipt - and editing, reference-image and 2K remain unqualified. The floor moved 16 -> 8 in the manifest on 2026-10-28 so the one number the kit and the scheduler both use is published in one place; the kit's PROVEN_SMALLER table that carried it is gone."), "qualification": {"status": "unqualified", "reason": "Runs on the owner's 8 GB Apple-silicon Macs - tens of thousands of images produced there, and the Hub's admission table has enforced 8 GB since the fleet was qualified. No peak-memory receipt was stored for those runs, so this tier is not reported as `qualified`. Editing, reference-image and 2K are not qualified at any tier."}},
    "qwen-image-2-1-mlx-serve-4bit": {
        "defaults": {"width": 1024, "height": 1024, "steps": 20, "guidance": 1.0,
                     "seed": -1,
                     "parameters_source": "docs/superpowers/plans/2026-10-06-qwen-image-21.md"},
        "memory": (24, 24, "MEASURED on the owner's physical 24 GiB Apple M4 (macOS 27.0.1, Studio Hub 0.6.152 packaged Image worker, mlx-serve 26.10.1, pinned MLX affine 4-bit pack), 2026-10-08: four 16-step cases (1024x1024 cold and warm, 1280x720, 720x1280) completed in 440.6-500.3 s wall; sampled worker-tree peak 9.235-9.787 GB; available memory 19.01-19.88 GB at start and never below 9.24 GB; pressure normal; zero swap growth; SSD net writes at most 0.05 GB. Evidence: qualification/evidence/qwen-image-21-20261008-24gb/measurement.json. 16 GB is not claimed for this package."),
        "qualification": {
            "status": "qualified",
            "reason": "Qualified on 24 GB and above by the owner's physical 24 GiB M4 run of 2026-10-08: four packaged 16-step cases completed with normal pressure and no swap growth (qualification/evidence/qwen-image-21-20261008-24gb/measurement.json). Earlier standalone 32 GB evidence agrees.",
            # ponytail: peak demand + 2 GB, not the lowest recorded start (19.01 GB): the fleet's
            # only 24 GB Mac idles near 17.9 GB. Ceiling: no run started below 19.01 GB; raise this
            # if a lower-headroom start ever shows pressure or swap.
            "min_free_memory_gb": 12.5,
            "min_free_memory_note": "Measured peak demand plus 2 GB, rounded up to 0.5 GB: the largest drop in available memory during the physical 24 GiB M4 run of 2026-10-08 was 10.35 GB (cold-square 1024x1024, 19.88 to 9.53 GB; worker-tree peak 9.79 GB), so a job admitted at 12.5 GB keeps at least 2 GB available at its peak. The Hub runs one job per Mac and releases idle resident models before it waits on this floor. Every measured start had 19.01 GB or more; qualification/evidence/qwen-image-21-20261008-24gb/measurement.json.",
        },
    },
    "qwen-image-2-1-mflux-q4": {
        "defaults": {"width": 1024, "height": 1024, "steps": 20, "guidance": 1.0,
                     "seed": -1,
                     "parameters_source": "docs/superpowers/plans/2026-10-06-qwen-image-21.md"},
        "memory": (16, 16, "MEASURED on the owner's physical 16 GiB Apple M4 (macOS 27.0.1, Studio Hub 0.6.152 packaged Image worker, pinned MLX affine Q4 pack), 2026-10-08: four 16-step cases (1024x1024 cold and warm, 1280x720, 720x1280) completed in 462.9-538.3 s wall; sampled worker-tree peak 5.487-6.032 GB; available memory 9.31-10.65 GB at start and never below 6.05 GB; pressure normal; zero swap growth. Evidence: qualification/evidence/qwen-image-21-20261008-16gb/measurement.json. Earlier: the physical 32 GiB M2 Max, 2026-10-06, four 1K 16-step cases, peak 5.513-5.853 GB (qualification/evidence/qwen-image-21-20261006/measurement.json). 8 GB is not claimed."),
        "qualification": {
            "status": "qualified",
            "reason": "Qualified on 16 GB and above by the owner's physical 16 GiB M4 run of 2026-10-08: four packaged 16-step cases completed with peak 6.03 GB, normal pressure and no swap growth (qualification/evidence/qwen-image-21-20261008-16gb/measurement.json). Earlier 32 GiB M2 Max evidence agrees (qualification/evidence/qwen-image-21-20261006/measurement.json). 8 GB is not qualified.",
            # ponytail: peak + 2 GB (32 GiB run); the 16 GiB M4 run started at 9.31 GB or more and
            # dropped at most 4.47 GB. Ceiling: raise this if a lower-headroom start shows pressure
            # or swap.
            "min_free_memory_gb": 8.0,
            "min_free_memory_note": "Measured peak plus 2 GB, rounded up to 0.5 GB: the worker-tree peak was 5.853 GB on the physical 32 GiB M2 Max on 2026-10-06 (qualification/evidence/qwen-image-21-20261006/measurement.json), so a job admitted at 8.0 GB keeps at least 2 GB available at its peak. The physical 16 GiB M4 run of 2026-10-08 supports it: every start had 9.31-10.65 GB available, the largest drop in available memory was 4.47 GB, available memory never fell below 6.05 GB and swap did not grow (qualification/evidence/qwen-image-21-20261008-16gb/measurement.json). The Hub runs one job per Mac and releases idle resident models before it waits on this floor.",
        },
    },
    "qwen-image-2-1-turbo-mflux-q4": {
        "defaults": {"width": 1024, "height": 1024, "steps": 8, "guidance": 1.0,
                     "seed": -1,
                     "parameters_source": ".planning/2026-09-10-standalone-app-feasibility/qwen-image-turbo-20261012.md",
                     "schedule_source": "model/model_index.json sample_sigmas"},
        "memory": (16, 16, "Owner-adopted production floor, not a Turbo measurement: normal production is enabled on 16 GB and larger Macs by the owner's decision of 2026-10-13. Turbo-specific peak memory, speed and output results remain unmeasured; 8 GB is not claimed."),
        "qualification": {
            "status": "unqualified",
            "reason": "Owner adopted Qwen Image 2.1 Turbo MLX 4-bit for normal production on 16 GB and larger Macs on 2026-10-13, waiving separate physical Turbo memory-fit qualification because regular Qwen Image 2.1 Low-RAM MLX 4-bit operates on 16 GB. Turbo-specific peak memory, speed and output results remain unmeasured; 8 GB is not claimed.",
            "adoption_basis": "owner decision 2026-10-13",
            "turbo_measurements": "unmeasured",
            "min_free_memory_gb": 8.0,
            "min_free_memory_note": "Provisional inherited operational assumption, not a Turbo measurement: use the regular Qwen Image 2.1 Low-RAM MLX 4-bit free-before-start floor of 8.0 GB until Turbo-specific operations establish a different floor.",
        },
    },
    "z-image-turbo-mflux-4bit": {"defaults": {"width": 1280, "height": 720, "steps": 9, "guidance_ignored": True, "guidance_note": "CFG is disabled on this distilled model; mflux ignores --guidance (and --negative-prompt) and warns.", "parameters_source": "qualification/z-image-turbo-2026-09-24.md"}, "memory": (8, 16, "Measured on two tiers. 32 GB M2 Max, 1280x720, 9 steps: 17.64-17.70 GB peak footprint at the engine default and 7.12-7.24 GB with --low-ram, 0 swaps. 8 GB Apple M1 (Mac mini, macOS 26.6.2, 3.03 GB available at start), mflux 0.20.0, seed 42, 9 steps: 1024x1024 low-ram OK in 392 s at 5.70 GB peak MLX / 5.85 GB footprint, 1857 MB peak swap; 1280x720 low-ram OK in 336 s at 5.70 / 5.85 GB, 1274 MB peak swap; 1024x1024 low-ram + vae-tile 256 OK at 4.95 / 5.50 GB. So the 8 GB tier is MEASURED and PASSES, with low-RAM mode mandatory there - it still swaps 1.3-1.9 GB. The engine default also completed on 8 GB but ONLY by swapping 3443 MB with an 8.72 GB footprint on an 8.0 GB machine: it fits by paging, not by fitting, and must not be offered at this tier."), "qualification": {"status": "qualified", "reason": "Qualified by measurement on two tiers: a 32 GB M2 Max at 1280x720, 9 steps, and the owner's 8 GB Apple M1 Mac mini run of 2026-09-26 where low-RAM mode completed both 1024x1024 and 1280x720. Local import and inference qualification are no longer outstanding for this model, and low-RAM mode is mandatory below 24 GB."}},
    "qwen3-tts-12hz-0-6b-base-8bit": {"defaults": {"parameters_source": "model/generation_config.json"}, "memory": (8, 16, "The 0.6B Base tier is the 8 GB cloning model, which is what the Voice catalog and the native app advertise; the earlier 16 GB figure was inventory guidance and disagreed with both.")},
    "qwen3-tts-12hz-0-6b-base-4bit": {"defaults": {"parameters_source": "model/generation_config.json"}, "memory": (8, 16, "The lighter 4-bit twin of the 8 GB cloning model: same speech tokenizer (byte-identical codec files), 1.0 GB of weights instead of 1.3 GB. 8 GB tier by the same reasoning as the 8-bit; peak footprint measured on a 32 GB Mac only, awaiting the 8 GB runner.")},
    "qwen3-tts-12hz-1-7b-base-4bit": {"defaults": {"parameters_source": "model/generation_config.json"}, "memory": (12, 16, "Weight size sits between the 0.6B and 1.7B 8-bit tiers; the floor is inferred from file size and awaits a local measurement.")},
    "voxcpm2-4bit": {"defaults": {"parameters_source": "model/config.json"}, "memory": (8, 16, "Owner report: the 4-bit build runs on 8 GB Apple-silicon Macs. The 8-bit and bf16 builds stay at 16 GB. Speed and peak memory are still unmeasured here.")},
    "voxcpm2-8bit": {"defaults": {"parameters_source": "model/config.json"}, "memory": (16, 24, "Catalog guidance only; no local speed or quality benchmark is recorded.")},
    "voxcpm2-bf16": {"defaults": {"parameters_source": "model/config.json"}, "memory": (16, 24, "16 GB is a known-not-below floor, not a measured safe budget; 24 GB remains required for qualification.")},
    "omnivoice-bfloat16": {"defaults": {"parameters_source": "model/config.json"}, "memory": (8, 16, "Historical receipt reports a 3.5 GB peak, and the Voice catalog publishes 8 GB as the floor.")},
    "whisper-turbo": {"defaults": {"parameters_source": "model/config.json"}, "memory": (8, 8, "Adopted registry default is 8 GB; this is the only standalone subtitle model exercised end-to-end.")},
    "whisper-small": {"defaults": {"parameters_source": "model/config.json"}, "memory": (8, 8, "Adopted registry default is 8 GB; model-specific subtitle accuracy and telemetry are unqualified.")},
    "whisper-base": {"defaults": {"parameters_source": "model/config.json"}, "memory": (8, 8, "Adopted registry default is 8 GB; model-specific subtitle accuracy and telemetry are unqualified.")},
    # The free floor is the owner's requested "small" one and is NOT a measurement yet: replace it
    # with the 8 GB run's "started with N GB free" (_orchestra/audit-20261001/phonon2/QUALIFICATION-PLAN.md).
    "phonon-2-mlx": {"defaults": {"parameters_source": "model/config.json"}, "memory": (8, 8, "Not measured. 189 MB package (2.1-bit encoder) decoded at load to ~1.2 GB of bf16 weights, transcribed in 60 s windows; 8 GB is the requested tier pending the fleet run in QUALIFICATION-PLAN.md."),
                     "qualification": {"min_free_memory_gb": 1.5, "min_free_memory_note": "Provisional small floor requested by the owner 2026-10-05; not yet measured. Set it from the 8 GB qualification run's free memory at job start."}},
    "khmer-tts-mms": {"defaults": {"parameters_source": "model/config.json"}, "memory": (8, 16, "An 83M-parameter VITS on torch; the Voice catalogue publishes an 8 GB floor and local inference measured a sub-1 GB footprint.")},
    "khmer-tts-female-onnx": {"defaults": {"parameters_source": "model/tts_meta.json"}, "memory": (8, 16, "A ~115 MB VITS ONNX graph on CPU; the Voice catalogue publishes an 8 GB floor.")},
    # `model/config.json`, not `model/generation_config.json`: the package ships no
    # generation_config.json, so the old pointer named a file nobody could read and the Chat
    # worker silently used its own defaults. The shipped manifest records the file the worker
    # actually reads.
    "qwen3-5-9b-4bit": {"defaults": {"parameters_source": "model/config.json"}, "memory": (16, 16, "Owner-chosen common fleet model, uniform 4-bit MLX. MEASURED on the 16 GB tier, 2026-09-25: three 16 GB Macs (M1, M2, M4) each completed a 256-token completion at 12.40-18.60 tok/s, peak MLX 5.23 GB (5226237062 bytes, identical on all three - it is the model's footprint, not the host's), load 9.08-13.69 s, swap 0.00 MB on two Macs and 0.25 MB on the third. Memory available at start was 7.70-8.85 GB against the checkpoint's own 5.98 GB plus 0.5 GB of working room. Source: qualification/chat-tier-run.sh on each Mac; verdict blocks recorded in the owner's 'StudioHub Chat Tier Run m{1,2,4}-16*' folders. 8 GB: EXCLUDED by the owner's decision, 2026-09-25. It does not run there - he tried it on his 8 GB Mac - so the tier harness refuses an 8 GB Mac immediately rather than probing, because an attempt costs up to 900 s and a swapping model load to rediscover that. This is a decision, not an inference from the weight size: the earlier note's '8 GB is not viable' was arithmetic, and the refusal that followed was about FREE memory on a busy Mac rather than about the tier. Nothing is claimed to have been measured on 8 GB; what is claimed is that this fleet does not run it there. This replaces the earlier catalog-arithmetic note, which read '8 GB is not viable' as an inference from the weight size and 'local import and inference qualification remain required'."),
                       "qualification": {"status": "qualified", "reason": 'Qualified on the 16 GB tier by measurement, 2026-09-25: three 16 GB Macs completed runs. The 8 GB tier is unmeasured - a run there did not start and the reason is not yet recorded.'}},
    "gemma4-26b-a4b-qat-4bit": {
        "defaults": {"parameters_source": "model/generation_config.json"},
        "memory": (32, 32, "MEASURED on the owner's physical 32 GiB M2 Max, 2026-10-07, across two Gemma 4 26B-A4B QAT MLX 4-bit runs. The first seven-case suite passed all seven mechanical checks; its largest observed process footprint was 19,813,822,128 bytes (18.45 GiB). In the extended run, the three-chapter case took 72.209 s at 43.4297 decode tok/s with a 16,486,903,472-byte (15.35 GiB) worker peak; the 50-scene case took 103.764 s at 41.7067 decode tok/s with a 16,678,416,048-byte (15.53 GiB) worker peak. Pressure remained normal with no positive new swap growth in the extended run. Physical 24 GiB and 16 GiB were not tested, so 32 GiB is both the minimum and recommended measured tier. Repository evidence: qualification/evidence/gemma4-20261007-32gb/measurement.json."),
        "qualification": {
            "status": "qualified",
            "reason": "Qualified on the owner's physical 32 GiB M2 Max and M2 Pro. The original extended strict 3x1,200-word test remains FAIL: it produced 985, 800 and 805 words; the owner accepted those observed lengths. The 50-scene case returned all 50 exact IDs as strict JSON. On 2026-11-02, Mac 1607 (32 GiB M2 Pro) passed input-48k: 49,120 measured prompt tokens plus 1,915 completion tokens, all 16 sections and the needle recalled, 21,150,477,952-byte process peak (19.70 GiB), normal pressure and no new swap. input-64k failed quality (14/16 sections), despite normal memory. qualification.context_window_tokens=49,152 is the owner-approved curated hardware budget for combined input plus reserved output, not a model claim; the checkpoint retains its model-declared text_config.max_position_embeddings=262,144. tested_usable_context_tokens=51,035 records the successful combined token count, not a qualified 49,152-token prompt plus 8,192 output. The 8,192 output cap is owner policy, not a new output-length measurement. Physical 24 GiB and 16 GiB were not tested. Evidence: qualification/evidence/gemma4-20261102-1607-48k/report.json.",
            "context_window_tokens": 49152,
            "tested_usable_context_tokens": 51035,
        },
    },
    "qwen38-27b-4bit": {
        "defaults": {"parameters_source": "model/generation_config.json"},
        "memory": (24, 32, "PROVISIONAL, NOT MEASURED. Derived from the package this manifest ships: its ten runtime files total 16,081,496,454 bytes (14.98 GiB), so the smallest published fleet tier that can hold them is 24 GB and 16 GB cannot. 32 GB is recommended because a dense 27B checkpoint's resident footprint adds KV cache and activations on top of its weights and no peak has been observed on any Mac. This is a weight-size floor, not a measured tier, and no inference, TPS, script or scene-quality run has happened. Replace it with a measurement (qualification/model-readings.json) before treating the model as qualified."),
        "qualification": {
            "status": "unqualified",
            "reason": 'Downloaded and publisher-hash verified only. Installable and dispatchable: the RAM floor above is the only gate (owner decision, 2026-10-27 - no model is blocked or locked by name). Dense 27B English import, inference, TPS, script/scene quality, usable context and a MEASURED RAM tier have not yet been run.',
            "tested_usable_context_tokens": None,
        },
    },
    "qwen36-35b-4bit": {
        "defaults": {"parameters_source": "model/generation_config.json"},
        "memory": (24, 32, "PROVISIONAL, NOT MEASURED. Derived from the package this manifest ships: its fifteen runtime files total 20,429,166,969 bytes (19.03 GiB), so the smallest published fleet tier that can hold the weights at all is 24 GB; 16 GB cannot. 32 GB is recommended because a sparse 35B-A3B checkpoint's resident footprint exceeds its weight bytes and no peak has been observed on any Mac. This is a weight-size floor, not a measured tier, and no inference, TPS, script or scene-quality run has happened. Replace it with a measurement (qualification/model-readings.json) before treating the model as qualified."),
        "qualification": {
            "status": "unqualified",
            "reason": 'Downloaded and publisher-hash verified only. Installable and dispatchable: the RAM floor above is the only gate (owner decision, 2026-10-27 - no model is blocked or locked by name). English import, inference, TPS, script/scene quality, usable context and a MEASURED RAM tier have not yet been run.',
            "tested_usable_context_tokens": None,
        },
    },
    "nemotron-lightning-4bit": {
        "defaults": {"parameters_source": "model/generation_config.json"},
        "memory": (24, 32, "PROVISIONAL, NOT MEASURED. Derived from the package this manifest ships: its ten runtime files total 17,792,581,692 bytes (16.57 GiB), so the smallest published fleet tier that can hold the weights at all is 24 GB; 16 GB cannot. 32 GB is recommended because a sparse 30B-A3B checkpoint's resident footprint exceeds its weight bytes and no peak has been observed on any Mac. This is a weight-size floor, not a measured tier. Replace it with a measurement (qualification/model-readings.json) before treating the model as qualified."),
        "qualification": {
            "status": "unqualified",
            "reason": 'Downloaded and publisher-hash verified only. Installable and dispatchable: the RAM floor above is the only gate (owner decision, 2026-10-27 - no model is blocked or locked by name). No inference, TPS, script/scene quality or MEASURED RAM tier has been qualified.',
            "tested_usable_context_tokens": None,
        },
    },
    "gemma-3-12b-it-4bit": {
        "defaults": {"parameters_source": "model/generation_config.json"},
        "memory": (16, 24, "PROVISIONAL, NOT MEASURED. Derived from the package this manifest ships: its thirteen runtime files total 8,068,018,787 bytes (7.51 GiB), so the smallest published fleet tier that can hold the weights at all is 16 GB; 8 GB cannot. 24 GB is recommended because a 12B checkpoint's resident footprint and KV cache can exceed the available memory on a 16 GB Mac and no peak has been observed on any Mac. This is a weight-size floor, not a measured tier, and no inference, TPS, script or scene-quality run has happened. Replace it with a measurement (qualification/model-readings.json) before treating the model as qualified."),
        "qualification": {
            "status": "unqualified",
            "reason": 'Downloaded and publisher-hash verified only. Installable and dispatchable: the RAM floor above is the only gate (owner decision, 2026-10-27 - no model is blocked or locked by name). English import, inference, TPS, script/scene quality, usable context and a MEASURED RAM tier have not yet been run.',
            "tested_usable_context_tokens": None,
        },
    },
    # ── ST-13.A: Pocket TTS 4-bit and the two companions it cannot run without ──
    # The owner's listening review on 2026-09-24 measured a 1.9 GB peak and judged this the
    # best-sounding English cloning model on the menu. A local render through the app's own
    # worker on 2026-09-24 re-measured the peak and is what the evidence line records.
    "pocket-tts-4bit": {"defaults": {"parameters_source": "model/config.json"}, "memory": (8, 16, "Owner listening review 2026-09-24: 1.9 GB peak, one of only two engines with a credible 8 GB path. Local render through the app's own worker on a 32 GB M2 Max measured 1.93 GB peak for the cloning path (6.64 s of audio in 0.30 s of inference) and 0.25 GB for the preset path, so 8 GB is the floor and 16 GB the comfortable tier.")},
    # 16 GB rather than the 8 GB the resident size would allow, and the reason is a disagreement
    # between two measurements rather than caution for its own sake: [M] resident set size held flat
    # at ~3.3 GB in every mode, which fits 8 GB, while [M] the kernel's peak footprint reading ranged
    # 5.41 to 23.93 GB across runs and was never pinned down. Until the second is settled, 8 GB is
    # plausible and not demonstrated, so the claim is the tier that is not at risk.
    "breeze-tts-2-mlx-4bit": {"defaults": {"parameters_source": "model/config.json"}, "memory": (8, 16, "MEASURED on an 8 GB Apple M1 (Hub 0.6.143) on 2026-10-04: 2/2 S2 clips completed, every section accepted, worker peak 4.69 GB, Mac max used 5.2-5.3 GB, min free 1.19-1.32 GB, memory pressure warning but never critical, swap <= +0.54 GB, RTF 4.1 against 4.9-5.1 on a 16 GB M1, max stall 0.6 s. Each clip started with 3.31-3.46 GB free; the 3.2 GB free-before-start floor is the model's own catalog row (min_free_memory_gb), measured on that same run. The earlier 2026-09-29 staging run held a flat ~3.3 GB resident set across all five modes, but its kernel peak footprint reading (5.41-23.93 GB) was never settled, which is why the floor is stated from the 2026-10-04 run. The floor moved 16 -> 8 in the manifest on 2026-10-28 so one number is published in one place; the kit's PROVEN_SMALLER table that carried it is gone. Throughput 0.27x realtime - about 19.8 minutes per 1,000 words - which is the real cost of this model, not memory."), "qualification": {"status": "qualified", "reason": "Measured on an 8 GB Apple M1 on 2026-10-04: 2/2 clips completed, worker peak 4.69 GB, swap <= +0.54 GB, memory pressure never critical. The 3.2 GB free-before-start figure is the model's own catalog row, from the same run."}},
    "pocket-tts-tokenizer": {"defaults": {"parameters_source": "model/tokenizer.model"}, "memory": (8, 8, "A 58 KB sentencepiece model with no memory floor of its own; it is carried wherever pocket-tts-4bit is, because every generation loads it.")},
    "pocket-tts-preset-voices": {"defaults": {"parameters_source": "model/embeddings/alba.safetensors"}, "memory": (8, 8, "Eight preset voice prompts totalling about 4.4 MB, used only by the preset/zero-shot path when no reference clip is supplied.")},
    # ── Adopted from the local Voice Studio cache (2026-09-20) ──
    # `defaults` and `memory` are the values already recorded in each shipped manifest, so a
    # recapture writes the same floors rather than inventing new ones.
    "chatterbox-4bit": {"defaults": {"parameters_source": "model/Cangjie5_TC.json"}, "memory": (8, 16, "Memory floor taken from the Voice catalogue's declared minimum.")},
    "chatterbox-8bit": {"defaults": {"parameters_source": "model/CACHEDIR.TAG"}, "memory": (8, 16, "Memory floor taken from the Voice catalogue's declared minimum.")},
    "fish-audio-s2-pro-8bit": {"defaults": {"parameters_source": "model/codec.safetensors"}, "memory": (24, 24, "Memory floor taken from the Voice catalogue's declared minimum.")},
    "fish-audio-s2-pro-bf16": {"defaults": {"parameters_source": "model/LICENSE.md"}, "memory": (32, 32, "Memory floor taken from the Voice catalogue's declared minimum.")},
    "fish-audio-s2-pro-4bit": {"defaults": {"parameters_source": "model/config.json"}, "memory": (16, 24, "MEASURED here [M] 2026-10-02 on the 32 GB M2 Max through the voice child's own code path (mlx-audio 0.5.7, mlx_memory bounds): S2 (4 sections, 70-75 s of audio) peak process footprint 7.43 GB (Chris reference) / 8.10 GB (Christian), 14.61 GB before the slabbed DAC decode; Whisper WER 0 of 175 words. 16 GB tier approved by the owner 2026-10-02 from these measurements; confirm with a real 16 GB run.")},
    "s3tokenizerv2": {"defaults": {"parameters_source": "model/config.json"}, "memory": (8, 16, "Memory floor taken from the Voice catalogue's declared minimum.")},
    # ── Music: the Stable Audio 3 Medium engine ──
    "stable-audio-3-medium": {"defaults": {"dit_model": "dit_medium", "decoder": "same-l", "inference_steps": 8, "guidance_scale": 1.0, "audio_format": "wav", "sample_rate_hz": 44100, "channels": 2, "max_duration_seconds": 380}, "memory": (8, 16, "M2 Max 32 GB: 30s clip 3.89 GB peak / 9.26s; 380s clip 5.05 GB peak / 61.58s.")},
    # ── Video: no pack is catalogued (see the capture table above; both packages were retired
    # and nothing here reads their dormant manifests). ──
    "yue2-3b-mlx": {
        "defaults": {"mode": "off", "duration_seconds": 36, "seed": -1, "precision": "8bit",
                     "parameters_source": "components/music/app/backend/catalog.py"},
        "memory": (16, 24, "MEASURED here [M], three runs, by sampled `footprint phys_footprint_peak` in the process doing the work: 600 tokens -> 24.0 s of 48 kHz stereo audio, 58.6 s end to end, peak 10.79 GB; 900 tokens through the runner bridge -> 36.0 s, 103.2 s, peak 11.81 GB (receipt qualification/yue2-music-results-20260927/bridge-run.json); 3000 tokens -> 120.0 s, 429.8 s, peak 11.93 GB. The peak barely moves with length because the weights dominate: 2.66 GB 8-bit AR + 2.93 GB bf16 NAR + 0.51 GB VAE. A 16 GB Mac therefore has roughly 4 GB left for macOS, and the port's own monitor defaults to a 16 GiB process budget and fails closed on more than 64 MiB of new swap. An 8 GB Mac cannot run it in any configuration this port offers: the AR is the only quantizable part, and 4-bit would save about 1 GB against an 11.9 GB peak."),
        "qualification": {"status": "qualified", "reason": "MEASURED by three local runs on the owner's 32 GB M2 Max through the port's own pipeline at the pinned commit 9253ed133343406947bde7b67d43c7a63fb39d99, two of them through this app's runner bridge, with the peak taken by sampled `footprint` in the process doing the work rather than from MLX's own allocator (which reports about half). What is NOT covered and is not claimed: no 16 GB or 24 GB machine has run it - the tier is a floor from a host under no memory pressure, and the 16 GB verdict is the owner's run to make. The port's own README targets a 16 GiB process budget and states it was tested on an M3 Max (128 GB) and an M5 Air (32 GB); it publishes no memory table of its own, so the numbers above are the only measured ones that exist."},
    },
    # Pooling and normalisation are the model's own facts (BAAI/bge-small-en-v1.5
    # 1_Pooling/config.json: pooling_mode_cls_token; modules.json: Normalize). The MLX build
    # drops the 1_Pooling folder, so they are recorded here rather than assumed by the worker.
    # Vision: `kind` picks the code, `tasks` is what the model publishes (the Gateway validates
    # requests against it), dtypes and the token budget are the measured parity settings.
    "depth-anything-v2-small": {
        "defaults": {"kind": "depth", "tasks": ["depth"], "input_short_side": 518,
                     "parameters_source": "model/preprocessor_config.json (DPT: keep aspect, 518, multiple of 14) and model/config.json"},
        "memory": (8, 8, "24.8M parameters held as float32 (~99 MB). One 768x432 picture: 0.06 s on an M2 Max; parity with transformers' DepthAnythingForDepthEstimation within 1e-4 relative (vision/hub/parity, 2026-10-11)."),
    },
    "florence-2-base-ft": {
        "defaults": {"kind": "florence", "tasks": ["subject", "points", "segments"],
                     "text_dtype": "float32", "vision_dtype": "float32", "max_new_tokens": 1024,
                     "parameters_source": "model/generation_config.json (3 beams, no repeated 3-grams, forced BOS/EOS) and model/processor_config.json"},
        "memory": (8, 8, "0.23B parameters held as float32 (~0.93 GB): float16 drifted from transformers' float32 reference, float32 is token-identical (vision/hub/parity, 2026-10-11)."),
    },
    "florence-2-large-ft": {
        "defaults": {"kind": "florence", "tasks": ["subject", "points", "segments"],
                     "text_dtype": "float32", "vision_dtype": "float32", "max_new_tokens": 1024,
                     "parameters_source": "model/generation_config.json (3 beams, no repeated 3-grams, forced BOS/EOS) and model/processor_config.json"},
        "memory": (16, 16, "0.77B parameters held as float32 (~3.1 GB); the 16 GB floor leaves room beside a generator. Not installed by default: the owner switches it in if Base is not precise enough."),
    },
    "bge-small-en-v1-5-bf16": {
        "defaults": {"pooling": "cls", "normalize": True, "dimensions": 384, "max_tokens": 512,
                     "parameters_source": "BAAI/bge-small-en-v1.5@5c38ec7c 1_Pooling/config.json and modules.json; model/config.json hidden_size and max_position_embeddings"},
        "memory": (8, 8, "A 67 MB bf16 BERT encoder (33M parameters) held as float32 (~134 MB). Peak and speed are measured in the Embeddings worker tests and recorded in the release notes."),
    },
    "yue2-vae": {
        "defaults": {"parameters_source": "model/config.json"},
        "memory": (16, 24, "Companion decoder of the YuE2 generator, and not a model: 0.51 GB of FP32 weights with no text tower. MEASURED inside the generator's own runs — it is loaded beside the generator, so it is part of every peak recorded there (10.79 GB at 600 tokens, 11.81 GB through the runner bridge, 11.93 GB at 3000 tokens)."),
        "qualification": {"status": "qualified", "reason": "Required companion of yue2-3b-mlx: the port loads it as `--vae` and the generator pack ships without it. Captured at the revision the three measured runs used, and its single weight file is verified against the `weights_manifest.json` the repository publishes."},
    },
}


def api_url(repo: str, revision: str) -> str:
    return f"{API_ROOT}{quote(repo, safe='/')}/revision/{revision}"


def tree_url(repo: str, revision: str) -> str:
    # The Hub paginates /tree at 50 entries by default, and refuses a page
    # larger than 100 when `expand=true`. A repo with benchmark audio or many
    # alternate checkpoints silently lost everything past the first page, so the
    # manifest looked complete while omitting the weights. `snapshot` follows the
    # Link header to walk every page; the capture is still metadata-only.
    return f"{API_ROOT}{quote(repo, safe='/')}/tree/{revision}?recursive=true&expand=true&limit=100"


def resolve_url(repo: str, revision: str, path: str) -> str:
    return f"{WEB_ROOT}{repo}/resolve/{revision}/{quote(path, safe='/')}"


def raw_url(repo: str, revision: str, path: str) -> str:
    return f"{WEB_ROOT}{repo}/raw/{revision}/{quote(path, safe='/')}"


def request(url: str, max_bytes: int = MAX_METADATA_BYTES) -> bytes:
    with urlopen(Request(url, headers={"User-Agent": "StudioHub-ST01-metadata/1.0"}), timeout=30) as response:
        body = response.read(max_bytes + 1)
    if len(body) > max_bytes:
        raise ValueError(f"response exceeds {max_bytes} byte limit: {url}")
    return body


def request_json(url: str) -> Any:
    return json.loads(request(url))


def request_json_with_link(url: str) -> tuple[Any, str]:
    """Return one JSON page and its ``Link`` response header."""
    with urlopen(Request(url, headers={"User-Agent": "StudioHub-ST01-metadata/1.0"}), timeout=30) as response:
        body = response.read(MAX_METADATA_BYTES + 1)
        link = response.headers.get("Link", "") if response.headers else ""
    if len(body) > MAX_METADATA_BYTES:
        raise ValueError(f"response exceeds {MAX_METADATA_BYTES} byte limit: {url}")
    return json.loads(body), link


def next_page_url(link_header: str) -> str | None:
    """Extract the ``rel="next"`` target from a Link header, if any."""
    for part in (link_header or "").split(","):
        segments = part.split(";")
        if len(segments) < 2:
            continue
        target = segments[0].strip().strip("<>")
        if any('rel="next"' in segment for segment in segments[1:]):
            return target or None
    return None


def snapshot(repo: str, revision: str) -> dict[str, dict[str, Any]]:
    metadata = request_json(api_url(repo, revision))
    if metadata.get("sha") != revision:
        raise ValueError(f"{repo}: expected revision {revision}, got {metadata.get('sha')}")
    entries: list[dict[str, Any]] = []
    url: str | None = tree_url(repo, revision)
    pages = 0
    while url:
        page, link = request_json_with_link(url)
        entries.extend(page)
        pages += 1
        if pages > 64:
            raise ValueError(f"{repo}@{revision}: tree pagination exceeded the page bound")
        url = next_page_url(link)
    files = {entry["path"]: entry for entry in entries if entry.get("type") == "file"}
    if not files:
        raise ValueError(f"{repo}@{revision}: tree has no files")
    return files


def required_primary_paths(files: dict[str, dict[str, Any]], exclude: tuple[str, ...] = (),
                           include: tuple[str, ...] = (), keep: tuple[str, ...] = ()) -> list[str]:
    """Select the publisher files one package ships.

    ``include`` is the allow-list counterpart of ``exclude`` and exists because most of what a
    companion repository publishes is not the companion: `kyutai/pocket-tts-without-voice-cloning`
    is a 27 GB repository whose 235 MB training weights and 8 MB of unrelated code are irrelevant
    to the one 58 KB sentencepiece model the engine asks for by name. Naming the wanted files
    directly means a future capture of a different revision cannot quietly pull the rest in,
    which an exclude-list of "everything else" could not promise.

    ``keep`` is the narrow per-package exception to the publisher-furniture rule below: a
    package that genuinely ships a `LICENSE.md` (fish-audio-s2-pro-bf16 does) names it here,
    because the alternative is a capture that silently drops a file the manifest still lists.
    """
    ignored = {".gitattributes", "README.md", "LICENSE", "LICENSE.md"}.difference(keep)
    return sorted(path for path in files
                  if path.rsplit("/", 1)[-1] not in ignored
                  and not path.rsplit("/", 1)[-1].startswith("test_")
                  and (not include or any(fnmatch(path, pattern) for pattern in include))
                  and not any(fnmatch(path, pattern) for pattern in exclude))


def file_digest(repo: str, revision: str, path: str, entry: dict[str, Any]) -> str:
    lfs = entry.get("lfs") or {}
    # The Hub API calls the Git-LFS SHA-256 object id ``oid``.  Older
    # responses exposed the same value as ``sha256``.
    digest = lfs.get("sha256") or lfs.get("oid")
    if digest:
        return digest.lower()
    size = int(entry["size"])
    if size > MAX_SMALL_FILE_BYTES:
        raise ValueError(f"{repo}@{revision}:{path} is {size} bytes but lacks an LFS SHA-256")
    # ``raw`` avoids redirects to the Xet/LFS payload service.  This branch is
    # deliberately limited to small non-weight assets; package URLs remain the
    # normal immutable ``resolve`` URLs consumers use.
    body = request(raw_url(repo, revision, path), MAX_SMALL_FILE_BYTES)
    if len(body) != size:
        raise ValueError(f"{repo}@{revision}:{path} returned {len(body)} bytes, expected {size}")
    git_oid = entry.get("oid")
    if git_oid:
        blob_oid = hashlib.sha1(f"blob {len(body)}\0".encode() + body).hexdigest()
        if blob_oid != git_oid.lower():
            raise ValueError(f"{repo}@{revision}:{path} Git blob SHA-1 mismatch")
    return hashlib.sha256(body).hexdigest()


def captured_file(repo: str, revision: str, source_path: str, destination: str,
                  files: dict[str, dict[str, Any]], *, source_alias: bool = True) -> dict[str, Any]:
    entry = files.get(source_path)
    if entry is None:
        raise ValueError(f"{repo}@{revision}: missing required {source_path}")
    captured = {
        "path": destination,
        "size": int(entry["size"]),
        "sha256": file_digest(repo, revision, source_path, entry),
        "urls": [resolve_url(repo, revision, source_path)],
    }
    if source_alias:
        captured["source_path"] = source_path
    return captured


def details_for(package_id: str) -> dict[str, Any]:
    """The captured defaults and memory floors for one package, or a named failure.

    A bare ``KeyError`` from ``DETAILS[...]`` named nothing: not the package, not the file to
    edit, not what was missing. Adding a model therefore failed in a way the person could only
    diagnose by reading this module. The error below names all three.
    """
    details = DETAILS.get(package_id)
    if details is None:
        raise ValueError(
            f"DETAILS has no entry for package {package_id!r}; add one in {SPEC_FILE} with its "
            f"\"defaults\" and its \"memory\" floors before capturing it")
    return details


def subtitle_block(choice: dict[str, Any]) -> dict[str, Any] | None:
    """The optional per-model subtitle contract, validated and passed through verbatim."""
    block = choice.get("subtitle")
    if block is None:
        return None
    package_id = choice.get("package_id")
    if not isinstance(block, dict):
        raise ValueError(f"{package_id}: \"subtitle\" must be an object, not {type(block).__name__}")
    missing = sorted(key for key in SUBTITLE_FIELDS if key not in block)
    wrong = sorted(key for key, kind in SUBTITLE_FIELDS.items()
                   if key in block and not isinstance(block[key], kind))
    if missing or wrong:
        raise ValueError(
            f"{package_id}: the \"subtitle\" block in {SPEC_FILE} needs "
            f"{sorted(SUBTITLE_FIELDS)}; missing {missing or 'none'}, wrong type {wrong or 'none'}")
    return block


def capture(choice: dict[str, Any]) -> dict[str, Any]:
    details = details_for(choice["package_id"])
    model_id, revision = choice["model_id"], choice["revision"]
    primary = snapshot(model_id, revision)
    selected = [captured_file(model_id, revision, path, f"model/{path}", primary)
                for path in required_primary_paths(primary, tuple(choice.get("exclude") or ()),
                                                   tuple(choice.get("include") or ()),
                                                   tuple(choice.get("keep") or ()))]
    sources = [{"role": "model", "repo": model_id, "revision": revision,
                "snapshot_api_url": api_url(model_id, revision), "tree_api_url": tree_url(model_id, revision)}]
    layout: dict[str, Any] = {"model": {"destination": "model", "repo": model_id, "revision": revision}}
    processor = choice.get("processor")
    if processor:
        processor_repo, processor_revision = processor
        processor_tree = snapshot(processor_repo, processor_revision)
        missing = PROCESSOR_FILES.difference(processor_tree)
        if missing:
            raise ValueError(f"{processor_repo}@{processor_revision}: missing processor files {sorted(missing)}")
        selected.extend(captured_file(processor_repo, processor_revision, path,
                                      f"companions/processor/{path}", processor_tree, source_alias=False)
                        for path in sorted(PROCESSOR_FILES))
        sources.append({"role": "processor", "repo": processor_repo, "revision": processor_revision,
                        "snapshot_api_url": api_url(processor_repo, processor_revision),
                        "tree_api_url": tree_url(processor_repo, processor_revision)})
        layout["companions"] = [{"destination": "companions/processor", "repo": processor_repo,
                                  "revision": processor_revision, "purpose": "Whisper processor/tokenizer"}]
    # A file the publisher's tree gets wrong is taken from another pinned repository instead, and
    # recorded as such: its own source, its own layout entry, its own immutable URL.
    for foreign in choice.get("foreign_files") or ():
        repo, foreign_revision, path = foreign["repo"], foreign["revision"], foreign["path"]
        tree = snapshot(repo, foreign_revision)
        selected.append(captured_file(repo, foreign_revision, path, f"model/{path}", tree))
        sources.append({"role": "replacement", "repo": repo, "revision": foreign_revision,
                        "snapshot_api_url": api_url(repo, foreign_revision),
                        "tree_api_url": tree_url(repo, foreign_revision)})
        layout.setdefault("companions", []).append(
            {"destination": f"model/{path}", "repo": repo, "revision": foreign_revision,
             "purpose": foreign["purpose"]})
    manifest = {
        "schema_version": 1,
        "package_id": choice["package_id"],
        "model_id": model_id,
        "revision": revision,
        "engine": choice["engine"],
        "minimum_macos": choice.get("minimum_macos", "14.0"),
        "files": sorted(selected, key=lambda item: item["path"]),
        "defaults": details["defaults"],
        # A measured tier says so; everything else keeps the honest catalog-metadata wording. The
        # table above carries the override, so a qualification is recorded where the measurement is
        # recorded rather than by editing the manifest, which is a generated artefact and is tested
        # for reproducibility by this very tool.
        "qualification": {
            "status": details.get("qualification", {}).get("status", "unqualified"),
            "minimum_unified_memory_gb": details["memory"][0],
            "recommended_unified_memory_gb": details["memory"][1],
            **({"memory_evidence": details["memory"][2]}
               if details["memory"][2] is not None else {}),
            "reason": details.get("qualification", {}).get(
                "reason",
                "Catalog metadata records expected bytes; local import and inference qualification remain required."),
            # An optional free-memory floor travels with its note (AGENTS.md: a floor names where it came from).
            **{key: details["qualification"][key] for key in ("min_free_memory_gb", "min_free_memory_note")
               if key in details.get("qualification", {})},
            **{key: details["qualification"][key]
               for key in ("target_unified_memory_gb", "target_evidence",
                           "context_window_tokens", "tested_usable_context_tokens")
               if key in details.get("qualification", {})},
        },
        "source": {"provider": "huggingface", "captured_at": choice.get("captured_at", CAPTURE_DATE), "sources": sources,
                   "layout": layout},
    }
    # The key is written only when the package has companions, which is the shape the shipped
    # catalog uses: the field is present exactly when there is a relationship to declare.
    if choice.get("requires_packages"):
        manifest["requires_packages"] = choice["requires_packages"]
    # Optional per-model policy is carried verbatim rather than derived here, because these are
    # declarations about the model (subtitle contract, default, production admission) that
    # no publisher metadata can supply. A block the spec omits is a block the manifest does not
    # carry, which is why the catalog check refuses when a shipped manifest declares one and its
    # CHOICES entry does not.
    subtitle = subtitle_block(choice)
    if subtitle is not None:
        manifest["subtitle"] = subtitle
    if choice.get("default") is True:
        manifest["default"] = True
    if "production_allowed" in choice:
        manifest["production_allowed"] = choice["production_allowed"]
    # Licence/attribution a person declares (CC-BY packages must carry it); passed through verbatim.
    if choice.get("license"):
        manifest["license"] = choice["license"]
    for field in ("display_name", "quantization", "quantization_source", "max_output_tokens"):
        if field in choice:
            manifest[field] = choice[field]
    return manifest


def duplicate_default_problems(manifests: list[dict[str, Any]]) -> list[str]:
    """At most one package per engine may declare ``"default": true``.

    Each worker reads the flag only among the models for its engine. Two defaults for one engine
    are an ambiguous tie; Chat and Subtitle each naming their own default is the intended shape.
    """
    by_engine: dict[object, list[str]] = {}
    for manifest in manifests:
        if manifest.get("default") is True:
            by_engine.setdefault(manifest.get("engine"), []).append(manifest["package_id"])
    return [
        f"{len(declared)} packages for engine {engine!r} declare \"default\": true "
        f"({', '.join(sorted(declared))}); at most one package per engine may be the default"
        for engine, declared in by_engine.items() if len(declared) > 1
    ]


def preserve_declared_blocks(manifest: dict[str, Any],
                             previous: dict[str, Any] | None) -> dict[str, Any]:
    """Carry the blocks only a person can declare over from the manifest already on disk.

    ``subtitle``, ``default`` and ``production_allowed`` are declarations about a model that no
    publisher tree contains, so a capture cannot derive them from the snapshot.
    ``CHOICES`` remains the place to declare them for a package being captured for the first
    time, and a block supplied there wins; a block the shipped manifest carries and the spec does
    not restate survives unchanged instead of being silently dropped by ``--write``.

    ``quantization`` and ``quantization_source`` are in the same class for the same reason. They
    are read from the identifier the **publisher** chose — ``mlx-community/chatterbox-4bit``
    states 4-bit in its own repo name — and a re-capture must not wipe a fact the owner's
    standing rule requires on every model mention ("not 'Z-Image-Turbo' but 'Z-Image-Turbo 6B
    MLX 4-bit'"). A package whose publisher states no precision keeps ``null`` **and** the
    sentence saying so, because absence and "we did not look" must not publish the same value.
    """
    if not isinstance(previous, dict):
        return manifest
    for key in ("subtitle", "default", "production_allowed", "display_name",
                "quantization", "quantization_source", "license"):
        if key not in manifest and key in previous:
            manifest[key] = previous[key]
    return manifest


def manifest_problems(choice: dict[str, Any], manifest: dict[str, Any]) -> list[str]:
    """Every way one ``CHOICES`` entry disagrees with the manifest it is supposed to produce.

    This is the offline half of "the capture tool can reproduce the catalog": it cannot re-fetch
    a publisher tree here, but it can compare the fields the spec supplies with the manifest that
    ships, and it can prove that no shipped file would be filtered out by the spec's own
    selection rules. A disagreement is a failure naming the package, the field and the file to
    edit - never a quiet rewrite on the next ``--write``.
    """
    package_id = choice["package_id"]
    problems: list[str] = []
    for field in ("model_id", "revision", "engine"):
        if choice.get(field) != manifest.get(field):
            problems.append(
                f"{package_id}: CHOICES says {field}={choice.get(field)!r} but "
                f"model-manifests/{package_id}.json says {manifest.get(field)!r}")
    minimum_macos = choice.get("minimum_macos", "14.0")
    if minimum_macos != manifest.get("minimum_macos"):
        problems.append(
            f"{package_id}: CHOICES says minimum_macos={minimum_macos!r} but "
            f"model-manifests/{package_id}.json says {manifest.get('minimum_macos')!r}")
    for field in ("production_allowed", "display_name", "quantization",
                  "quantization_source", "license"):
        if field in choice and choice[field] != manifest.get(field):
            problems.append(
                f"{package_id}: CHOICES says {field}={choice[field]!r} but "
                f"model-manifests/{package_id}.json says {manifest.get(field)!r}")
    captured_at = choice.get("captured_at")
    if captured_at is not None and captured_at != (manifest.get("source") or {}).get("captured_at"):
        problems.append(
            f"{package_id}: CHOICES says captured_at={captured_at!r} but "
            f"model-manifests/{package_id}.json says "
            f"{(manifest.get('source') or {}).get('captured_at')!r}")
    declared = list(manifest.get("requires_packages") or [])
    if list(choice.get("requires_packages") or []) != declared:
        problems.append(
            f"{package_id}: CHOICES declares requires_packages="
            f"{list(choice.get('requires_packages') or [])} but "
            f"model-manifests/{package_id}.json declares {declared}")
    details = DETAILS.get(package_id)
    if details is None:
        problems.append(f"{package_id}: no DETAILS entry in {SPEC_FILE}; add one with its "
                        f"\"defaults\" and its \"memory\" floors")
    else:
        if details["defaults"] != manifest.get("defaults"):
            problems.append(f"{package_id}: DETAILS defaults {details['defaults']!r} but "
                            f"model-manifests/{package_id}.json has {manifest.get('defaults')!r}")
        qualification = manifest.get("qualification") or {}
        recorded = (qualification.get("minimum_unified_memory_gb"),
                    qualification.get("recommended_unified_memory_gb"),
                    qualification.get("memory_evidence"))
        if tuple(details["memory"]) != recorded:
            problems.append(
                f"{package_id}: DETAILS memory {tuple(details['memory'])!r} but "
                f"model-manifests/{package_id}.json records (minimum, recommended, evidence)="
                f"{recorded!r}")
        for field in ("min_free_memory_gb", "min_free_memory_note",
                      "target_unified_memory_gb", "target_evidence",
                      "context_window_tokens", "tested_usable_context_tokens"):
            if field in details.get("qualification", {}) \
                    and details["qualification"][field] != qualification.get(field):
                problems.append(
                    f"{package_id}: DETAILS qualification {field}="
                    f"{details['qualification'][field]!r} but model-manifests/{package_id}.json "
                    f"says {qualification.get(field)!r}")
    # A `subtitle` contract or a `default` flag is a person's declaration, not publisher
    # metadata, so `preserve_declared_blocks` carries a shipped manifest's block forward when
    # the spec does not restate it - see the docstring there. Only a malformed spec block is a
    # problem here, because that one would be written into the catalog.
    try:
        subtitle_block(choice)
    except ValueError as exc:
        problems.append(str(exc))
    if "default" in choice and choice["default"] is not True:
        problems.append(f"{package_id}: \"default\" in {SPEC_FILE} must be true when present, "
                        f"not {choice['default']!r}")
    if "production_allowed" in choice and type(choice["production_allowed"]) is not bool:
        problems.append(
            f"{package_id}: \"production_allowed\" in {SPEC_FILE} must be a boolean, "
            f"not {choice['production_allowed']!r}")
    exclude = tuple(choice.get("exclude") or ())
    include = tuple(choice.get("include") or ())
    keep = tuple(choice.get("keep") or ())
    for entry in manifest.get("files") or []:
        source_path = entry.get("source_path")
        if source_path is None or entry.get("provider") == "github":
            continue
        if entry.get("repo") not in (None, manifest.get("model_id")):
            continue
        # macOS volumes are case-insensitive and the Stable Audio bundle records a lower-case
        # destination for the publisher's `MLX/` directory, so a case-only difference is the
        # same file rather than a selection defect.
        if entry["path"].casefold() != f"model/{source_path}".casefold():
            continue
        if not required_primary_paths({source_path: {}}, exclude, include, keep):
            problems.append(
                f"{package_id}: model-manifests/{package_id}.json ships {entry['path']} but the "
                f"CHOICES exclude/include/keep rules would drop {source_path}")
    return problems


def catalog_problems(output: Path) -> list[str]:
    """Compare ``CHOICES``/``DETAILS`` with the shipped catalog before touching the network.

    The tool used to cover a silent subset: a manifest the author never added to ``CHOICES`` was
    simply not captured, and nothing said so. Every manifest in ``output`` must now have a spec,
    and every field that spec supplies must match the manifest, so `--write` can only ever
    reproduce the catalog it was written against.
    """
    problems: list[str] = []
    specs: dict[str, dict[str, Any]] = {}
    for choice in CHOICES:
        package_id = str(choice.get("package_id") or "")
        if package_id in specs:
            problems.append(f"CHOICES lists {package_id!r} twice; one entry per package")
        specs[package_id] = choice
        if package_id not in DETAILS:
            problems.append(f"CHOICES lists {package_id} but DETAILS in {SPEC_FILE} has no entry "
                            f"for it; add one with its \"defaults\" and its \"memory\" floors")
    shipped: list[dict[str, Any]] = []
    for path in sorted(output.glob("*.json")):
        try:
            manifest = json.loads(path.read_text())
        except ValueError as exc:
            problems.append(f"model-manifests/{path.name} is not readable JSON: {exc}")
            continue
        shipped.append(manifest)
        package_id = str(manifest.get("package_id") or path.stem)
        choice = specs.get(package_id)
        if choice is None:
            problems.append(
                f"model-manifests/{path.name} has no CHOICES entry in {SPEC_FILE}: this tool "
                f"cannot reproduce it, so a re-capture would silently drop the package")
            continue
        problems.extend(manifest_problems(choice, manifest))
    if not shipped:
        problems.append(f"{output} holds no manifests to capture; check --output")
    problems.extend(duplicate_default_problems(shipped))
    return problems


def unreproducible_problems(manifests: list[dict[str, Any]],
                            output: Path) -> list[tuple[str, str]]:
    """Shipped manifests whose *file set* this capture would change, as (package_id, why).

    `catalog_problems` compares the fields a spec declares before anything is fetched; this
    compares what the capture actually selected against what ships. They are not the same check:
    a package like `stable-audio-3-medium`, whose lower-case `model/mlx/` destinations name the
    publisher's `MLX/` directory, ships a manifest this single-source capture cannot rebuild file
    for file. Naming them and leaving their manifests untouched is the
    only honest outcome: replacing them would quietly delete files the catalog still lists, and
    silently writing a different catalog is the defect this whole check exists to stop.
    """
    problems: list[tuple[str, str]] = []
    for manifest in manifests:
        package_id = str(manifest["package_id"])
        path = output / f"{package_id}.json"
        if not path.is_file():
            continue
        try:
            previous = json.loads(path.read_text())
        except ValueError:
            continue
        before = [(entry.get("path"), entry.get("sha256")) for entry in previous.get("files") or []]
        after = [(entry.get("path"), entry.get("sha256")) for entry in manifest.get("files") or []]
        if before != after:
            problems.append((
                package_id,
                f"this capture selects {len(after)} file(s) but model-manifests/{package_id}.json "
                f"lists {len(before)}; see the comment above its CHOICES entry for the sources "
                f"this single-snapshot capture does not fetch"))
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("model-manifests"))
    parser.add_argument("--write", action="store_true", help="write the captured manifest files")
    args = parser.parse_args()
    # Checked before the first request: a spec that disagrees with the shipped catalog has to be
    # fixed first, so a metadata-only run never spends requests proving a manifest it should not
    # write, and `--write` cannot quietly replace the catalog.
    problems = catalog_problems(args.output)
    if problems:
        print("The capture specs and the shipped catalog disagree; nothing was captured.",
              file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 2
    # Choices do not share mutable state.  Four workers keep the bounded
    # metadata/small-file capture practical without issuing a broad crawl.
    with ThreadPoolExecutor(max_workers=4) as executor:
        manifests = list(executor.map(capture, CHOICES))
    # A hand-declared block survives a re-capture: `--write` may never drop what only a person
    # can state. Read before the write loop, and after the pre-flight check above has already
    # proved every manifest on disk has a spec.
    previous = {path.stem: json.loads(path.read_text())
                for path in args.output.glob("*.json")} if args.output.is_dir() else {}
    manifests = [preserve_declared_blocks(manifest, previous.get(manifest["package_id"]))
                 for manifest in manifests]
    problems = duplicate_default_problems(manifests)
    if problems:
        print("Refusing to write a catalog whose default package is ambiguous.", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 2
    withheld = unreproducible_problems(manifests, args.output)
    for package_id, why in withheld:
        print(f"not replacing model-manifests/{package_id}.json: {why}", file=sys.stderr)
    blocked = {package_id for package_id, _why in withheld}
    written = 0
    if args.write:
        args.output.mkdir(parents=True, exist_ok=True)
        for manifest in manifests:
            if manifest["package_id"] in blocked:
                continue
            (args.output / f"{manifest['package_id']}.json").write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            written += 1
    print(json.dumps({"packages": [item["package_id"] for item in manifests],
                      "written": written if args.write else args.write,
                      "not_replaced": sorted(blocked)}))
    # A partial write is a failure, not a success with a footnote: the operator asked for the
    # catalog to be reproduced and two packages were left alone.
    return 3 if withheld and args.write else 0


if __name__ == "__main__":
    raise SystemExit(main())
