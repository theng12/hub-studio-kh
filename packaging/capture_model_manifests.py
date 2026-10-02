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
* two packages declaring ``"default": true`` is refused - at most one package in
  the catalog may be the default.

``requires_packages`` is written only when the package has companions, and the
optional ``subtitle`` and ``default`` blocks of a ``CHOICES`` entry are passed
through verbatim, so a hand-declared block survives the next ``--write``.
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
    {"package_id": "yue2-3b-mlx", "model_id": "vanch007/mlx-Yue2-3B",
     "revision": "fa66d203dd56d7e033e05ee8b32269768984c4cc", "engine": "music-yue2",
     "requires_packages": ("yue2-vae",),
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
    {"package_id": "qwen3-5-9b-4bit", "model_id": "mlx-community/Qwen3.5-9B-4bit", "revision": "8b2b98c00a6b4d291155e4890773ca8f769aee53", "engine": "chat-mlx"},
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
    # ── Video: the LTX-2.3 22B mixed-precision pack and its text encoder ──
    # The pack is the one that produced the first successful run on this Mac
    # (qualification/ltx-video-results-20260926-32gb-first-success): 704x448, 97 frames,
    # 4.04 s with audio, 298.5 s wall, peak process footprint 16.38 GB. Its weights are
    # quantized per-tensor across 2/3/4/5/6/8 bits and the pack ships its own loader in
    # `generate.py`; the manifest lists that file because the worker applies it before the
    # pipeline is constructed. Sample images and the publisher's README are furniture.
    # (LTX-2.3 22B retired 0.6.127: its capture row moved to _trash/retire-ltx/; its manifest lives dormant in components/video/retired/.)
    # The text encoder is a real dependency, not a convenience: the pipeline loads it for every
    # prompt and would otherwise fetch it over the network at generation time. It is declared
    # `video-ltx` so the Video worker resolves its installed folder and passes the local path,
    # and it is listed in the pack's `requires_packages` so the Hub marks it a dependency rather
    # than offering it in a picker as if it were a video model.
    # (Gemma 3 12B 4-bit, LTX's text encoder, retired 0.6.128: its capture row is gone; its manifest lives dormant in components/video/retired/.)
)

DETAILS: dict[str, dict[str, Any]] = {
    "flux2-klein-4b-mlx-4bit": {"defaults": {"size_pixels": 1024, "steps": 4, "guidance": 1.0, "parameters_source": "research/rescope-model-inventory-2026-09-15.md"}, "memory": (16, 16, "Historical 16 GB Apple-Silicon 1K text-to-image receipt; editing, reference-image and 2K are not qualified.")},
    "z-image-turbo-mflux-4bit": {"defaults": {"width": 1280, "height": 720, "steps": 9, "guidance_ignored": True, "guidance_note": "CFG is disabled on this distilled model; mflux ignores --guidance (and --negative-prompt) and warns.", "parameters_source": "qualification/z-image-turbo-2026-09-24.md"}, "memory": (8, 16, "Measured on two tiers. 32 GB M2 Max, 1280x720, 9 steps: 17.64-17.70 GB peak footprint at the engine default and 7.12-7.24 GB with --low-ram, 0 swaps. 8 GB Apple M1 (Mac mini Macmini9,1, macOS 26.6.2, 3.03 GB available at start), mflux 0.20.0, seed 42, 9 steps: 1024x1024 low-ram OK in 392 s at 5.70 GB peak MLX / 5.85 GB footprint, 1857 MB peak swap; 1280x720 low-ram OK in 336 s at 5.70 / 5.85 GB, 1274 MB peak swap; 1024x1024 low-ram + vae-tile 256 OK at 4.95 / 5.50 GB. So the 8 GB tier is MEASURED and PASSES, with low-RAM mode mandatory there - it still swaps 1.3-1.9 GB. The engine default also completed on 8 GB but ONLY by swapping 3443 MB with an 8.72 GB footprint on an 8.0 GB machine: it fits by paging, not by fitting, and must not be offered at this tier."), "qualification": {"status": "qualified", "reason": "Qualified by measurement on two tiers: a 32 GB M2 Max at 1280x720, 9 steps, and the owner's 8 GB Apple M1 Mac mini run of 2026-09-26 where low-RAM mode completed both 1024x1024 and 1280x720. Local import and inference qualification are no longer outstanding for this model, and low-RAM mode is mandatory below 24 GB."}},
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
    "khmer-tts-mms": {"defaults": {"parameters_source": "model/config.json"}, "memory": (8, 16, "An 83M-parameter VITS on torch; the Voice catalogue publishes an 8 GB floor and local inference measured a sub-1 GB footprint.")},
    "khmer-tts-female-onnx": {"defaults": {"parameters_source": "model/tts_meta.json"}, "memory": (8, 16, "A ~115 MB VITS ONNX graph on CPU; the Voice catalogue publishes an 8 GB floor.")},
    # `model/config.json`, not `model/generation_config.json`: the package ships no
    # generation_config.json, so the old pointer named a file nobody could read and the Chat
    # worker silently used its own defaults. The shipped manifest records the file the worker
    # actually reads.
    "qwen3-5-9b-4bit": {"defaults": {"parameters_source": "model/config.json"}, "memory": (16, 16, "Owner-chosen common fleet model, uniform 4-bit MLX. MEASURED on the 16 GB tier, 2026-09-25: three 16 GB Macs (M1, M2, M4) each completed a 256-token completion at 12.40-18.60 tok/s, peak MLX 5.23 GB (5226237062 bytes, identical on all three - it is the model's footprint, not the host's), load 9.08-13.69 s, swap 0.00 MB on two Macs and 0.25 MB on the third. Memory available at start was 7.70-8.85 GB against the checkpoint's own 5.98 GB plus 0.5 GB of working room. Source: qualification/chat-tier-run.sh on each Mac; verdict blocks recorded in the owner's 'StudioHub Chat Tier Run m{1,2,4}-16*' folders. 8 GB: EXCLUDED by the owner's decision, 2026-09-25. It does not run there - he tried it on his 8 GB Mac - so the tier harness refuses an 8 GB Mac immediately rather than probing, because an attempt costs up to 900 s and a swapping model load to rediscover that. This is a decision, not an inference from the weight size: the earlier note's '8 GB is not viable' was arithmetic, and the refusal that followed was about FREE memory on a busy Mac rather than about the tier. Nothing is claimed to have been measured on 8 GB; what is claimed is that this fleet does not run it there. This replaces the earlier catalog-arithmetic note, which read '8 GB is not viable' as an inference from the weight size and 'local import and inference qualification remain required'."),
                       "qualification": {"status": "qualified", "reason": 'Qualified on the 16 GB tier by measurement, 2026-09-25: three 16 GB Macs completed runs. The 8 GB tier is unmeasured - a run there did not start and the reason is not yet recorded.'}},
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
    "breeze-tts-2-mlx-4bit": {"defaults": {"parameters_source": "model/config.json"}, "memory": (16, 16, "Qualified 2026-09-29 on this project's staging Mac. Resident set size held flat at ~3.3 GB in all five modes (design, clone, direction, long-form, vocal events), which fits 8 GB; the kernel peak footprint reading ranged 5.41 to 23.93 GB and was never settled, so 16 GB is the floor this catalogue claims. Throughput measured 0.27x realtime — about 19.8 minutes per 1,000 words — which is the real cost of this model, not memory.")},
    "pocket-tts-tokenizer": {"defaults": {"parameters_source": "model/tokenizer.model"}, "memory": (8, 8, "A 58 KB sentencepiece model with no memory floor of its own; it is carried wherever pocket-tts-4bit is, because every generation loads it.")},
    "pocket-tts-preset-voices": {"defaults": {"parameters_source": "model/embeddings/alba.safetensors"}, "memory": (8, 8, "Eight preset voice prompts totalling about 4.4 MB, used only by the preset/zero-shot path when no reference clip is supplied.")},
    # ── Adopted from the local Voice Studio cache (2026-09-20) ──
    # `defaults` and `memory` are the values already recorded in each shipped manifest, so a
    # recapture writes the same floors rather than inventing new ones.
    "chatterbox-4bit": {"defaults": {"parameters_source": "model/Cangjie5_TC.json"}, "memory": (8, 16, "Memory floor taken from the Voice catalogue's declared minimum.")},
    "chatterbox-8bit": {"defaults": {"parameters_source": "model/CACHEDIR.TAG"}, "memory": (8, 16, "Memory floor taken from the Voice catalogue's declared minimum.")},
    "fish-audio-s2-pro-8bit": {"defaults": {"parameters_source": "model/codec.safetensors"}, "memory": (24, 24, "Memory floor taken from the Voice catalogue's declared minimum.")},
    "fish-audio-s2-pro-bf16": {"defaults": {"parameters_source": "model/LICENSE.md"}, "memory": (32, 32, "Memory floor taken from the Voice catalogue's declared minimum.")},
    "fish-audio-s2-pro-4bit": {"defaults": {"parameters_source": "model/config.json"}, "memory": (24, 24, "MEASURED here [M] 2026-10-02 on the 32 GB M2 Max through the voice child's own code path (mlx-audio 0.5.7, mlx_memory bounds): S2 (4 sections, 70-75 s of audio) peak process footprint 7.43 GB (Chris reference) / 8.10 GB (Christian), 14.61 GB before the slabbed DAC decode; Whisper WER 0 of 175 words. 24 GB is the tier claimed (owner); no 16 GB or 24 GB Mac has run it yet.")},
    "s3tokenizerv2": {"defaults": {"parameters_source": "model/config.json"}, "memory": (8, 16, "Memory floor taken from the Voice catalogue's declared minimum.")},
    # ── Music: the Stable Audio 3 Medium engine ──
    "stable-audio-3-medium": {"defaults": {"dit_model": "dit_medium", "decoder": "same-l", "inference_steps": 8, "guidance_scale": 1.0, "audio_format": "wav", "sample_rate_hz": 44100, "channels": 2, "max_duration_seconds": 380}, "memory": (8, 16, "M2 Max 32 GB: 30s clip 3.89 GB peak / 9.26s; 380s clip 5.05 GB peak / 61.58s.")},
    # ── Video: the LTX-2.3 22B pack and the text encoder it loads ──
    # MEASURED here [M] on the 32 GB M2 Max: 704x448, 97 frames, seed 42, 298.5 s wall,
    # 4.04 s of output with audio, peak process footprint 16.38 GB (ri_phys by the pack's own
    # runner), peak MLX Metal 13.19 GB, RSS 9.91 GB. The 16 GB tier is a vendor claim this
    # measurement contradicts for the 22B model, so the floor is 24 GB and a 16 GB machine is
    # not offered the model. `defaults` mirror the worker's registry entry, which is the
    # artefact that states them.
    # `qualified` in this catalog's vocabulary means "a run left a record", and two do: the
    # standalone qualification run (298.5 s) and the same configuration driven through the
    # worker's own job path (304.0 s, job 627c7fbc). `reason` states exactly what those runs do
    # *not* cover — no 24 GB run, text-to-video unmeasured — so the status is not read as more
    # than it is.
    # (LTX-2.3 22B retired 0.6.127: its capture row moved to _trash/retire-ltx/; its manifest lives dormant in components/video/retired/.)
    # A companion: it has no picker row of its own (the Hub marks any package another package
    # requires as a dependency) and no runtime of its own. The floor mirrors the model whose
    # pipeline loads it, because a machine that cannot hold the 22B model cannot use the encoder
    # either.
    # (Gemma 3 12B 4-bit retired 0.6.128: its manifest lives dormant in components/video/retired/.)
    "yue2-3b-mlx": {
        "defaults": {"mode": "off", "duration_seconds": 36, "seed": -1, "precision": "8bit",
                     "parameters_source": "components/music/app/backend/catalog.py"},
        "memory": (16, 24, "MEASURED here [M], three runs, by sampled `footprint phys_footprint_peak` in the process doing the work: 600 tokens -> 24.0 s of 48 kHz stereo audio, 58.6 s end to end, peak 10.79 GB; 900 tokens through the runner bridge -> 36.0 s, 103.2 s, peak 11.81 GB (receipt qualification/yue2-music-results-20260927/bridge-run.json); 3000 tokens -> 120.0 s, 429.8 s, peak 11.93 GB. The peak barely moves with length because the weights dominate: 2.66 GB 8-bit AR + 2.93 GB bf16 NAR + 0.51 GB VAE. A 16 GB Mac therefore has roughly 4 GB left for macOS, and the port's own monitor defaults to a 16 GiB process budget and fails closed on more than 64 MiB of new swap. An 8 GB Mac cannot run it in any configuration this port offers: the AR is the only quantizable part, and 4-bit would save about 1 GB against an 11.9 GB peak."),
        "qualification": {"status": "qualified", "reason": "MEASURED by three local runs on the owner's 32 GB M2 Max through the port's own pipeline at the pinned commit 9253ed133343406947bde7b67d43c7a63fb39d99, two of them through this app's runner bridge, with the peak taken by sampled `footprint` in the process doing the work rather than from MLX's own allocator (which reports about half). What is NOT covered and is not claimed: no 16 GB or 24 GB machine has run it - the tier is a floor from a host under no memory pressure, and the 16 GB verdict is the owner's run to make. The port's own README targets a 16 GiB process budget and states it was tested on an M3 Max (128 GB) and an M5 Air (32 GB); it publishes no memory table of its own, so the numbers above are the only measured ones that exist."},
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
        "minimum_macos": "14.0",
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
            "memory_evidence": details["memory"][2],
            "reason": details.get("qualification", {}).get(
                "reason",
                "Catalog metadata records expected bytes; local import and inference qualification remain required."),
        },
        "source": {"provider": "huggingface", "captured_at": CAPTURE_DATE, "sources": sources,
                   "layout": layout},
    }
    # The key is written only when the package has companions, which is the shape the shipped
    # catalog uses: the field is present exactly when there is a relationship to declare.
    if choice.get("requires_packages"):
        manifest["requires_packages"] = choice["requires_packages"]
    # Two optional per-model blocks are carried verbatim rather than derived here, because they
    # are declarations about the model (a subtitle contract; which package is the default) that
    # no publisher metadata can supply. A block the spec omits is a block the manifest does not
    # carry, which is why the catalog check refuses when a shipped manifest declares one and its
    # CHOICES entry does not.
    subtitle = subtitle_block(choice)
    if subtitle is not None:
        manifest["subtitle"] = subtitle
    if choice.get("default") is True:
        manifest["default"] = True
    return manifest


def duplicate_default_problems(manifests: list[dict[str, Any]]) -> list[str]:
    """At most one package in a catalog may declare ``"default": true``.

    The Subtitle worker reads that flag to pick its default model, so two of them is not a
    tie to be broken silently - it is a catalog that cannot say what its default is.
    """
    declared = sorted(manifest["package_id"] for manifest in manifests
                      if manifest.get("default") is True)
    if len(declared) > 1:
        return [f"{len(declared)} packages declare \"default\": true ({', '.join(declared)}); "
                f"at most one package in the catalog may be the default"]
    return []


def preserve_declared_blocks(manifest: dict[str, Any],
                             previous: dict[str, Any] | None) -> dict[str, Any]:
    """Carry the blocks only a person can declare over from the manifest already on disk.

    ``subtitle`` and ``default`` are declarations about a model that no publisher tree contains,
    so a capture cannot derive them: it can only keep what the shipped manifest already says.
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
    for key in ("subtitle", "default", "quantization", "quantization_source"):
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
