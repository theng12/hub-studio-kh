#!/usr/bin/env python3
"""Capture the bounded Studio Hub model catalog from immutable HF snapshots.

This tool requests repository and tree metadata only.  It retrieves a file body
only when Hugging Face metadata lacks a digest and the file is at most 10 MiB;
model weights must expose their LFS SHA-256 metadata.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from fnmatch import fnmatch
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen


API_ROOT = "https://huggingface.co/api/models/"
WEB_ROOT = "https://huggingface.co/"
MAX_SMALL_FILE_BYTES = 10 * 1024 * 1024
MAX_METADATA_BYTES = 10 * 1024 * 1024
CAPTURE_DATE = "2026-09-15"
PROCESSOR_FILES = frozenset({
    "added_tokens.json", "merges.txt", "normalizer.json", "special_tokens_map.json",
    "tokenizer.json", "tokenizer_config.json", "vocab.json",
})


CHOICES: tuple[dict[str, Any], ...] = (
    {"package_id": "flux2-klein-4b-mlx-4bit", "model_id": "AITRADER/FLUX2-klein-4B-mlx-4bit", "revision": "7fd24828501390b67a92c8b66d2fc5a707d0ba1a", "engine": "image-mflux"},
    {"package_id": "qwen3-tts-12hz-0-6b-base-8bit", "model_id": "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit", "revision": "50f45ef0047cde7e84c2ef04326acb8ada2436a7", "engine": "voice-mlx", "requires_packages": ["whisper-turbo"]},
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
)

DETAILS: dict[str, dict[str, Any]] = {
    "flux2-klein-4b-mlx-4bit": {"defaults": {"size_pixels": 1024, "steps": 4, "guidance": 1.0, "parameters_source": "research/rescope-model-inventory-2026-09-15.md"}, "memory": (16, 16, "Historical 16 GB Apple-Silicon 1K text-to-image receipt; editing, reference-image and 2K are not qualified.")},
    "qwen3-tts-12hz-0-6b-base-8bit": {"defaults": {"parameters_source": "model/generation_config.json"}, "memory": (8, 16, "The 0.6B Base tier is the 8 GB cloning model, which is what the Voice catalog and the native app advertise; the earlier 16 GB figure was inventory guidance and disagreed with both.")},
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


def required_primary_paths(files: dict[str, dict[str, Any]], exclude: tuple[str, ...] = ()) -> list[str]:
    ignored = {".gitattributes", "README.md", "LICENSE", "LICENSE.md"}
    return sorted(path for path in files
                  if path.rsplit("/", 1)[-1] not in ignored
                  and not path.rsplit("/", 1)[-1].startswith("test_")
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


def capture(choice: dict[str, Any]) -> dict[str, Any]:
    model_id, revision = choice["model_id"], choice["revision"]
    primary = snapshot(model_id, revision)
    selected = [captured_file(model_id, revision, path, f"model/{path}", primary)
                for path in required_primary_paths(primary, tuple(choice.get("exclude") or ()))]
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
    manifest = {
        "schema_version": 1,
        "package_id": choice["package_id"],
        "model_id": model_id,
        "revision": revision,
        "engine": choice["engine"],
        "minimum_macos": "14.0",
        "files": sorted(selected, key=lambda item: item["path"]),
        "defaults": DETAILS[choice["package_id"]]["defaults"],
        "qualification": {
            "status": "unqualified",
            "minimum_unified_memory_gb": DETAILS[choice["package_id"]]["memory"][0],
            "recommended_unified_memory_gb": DETAILS[choice["package_id"]]["memory"][1],
            "memory_evidence": DETAILS[choice["package_id"]]["memory"][2],
            "reason": "Catalog metadata records expected bytes; local import and inference qualification remain required.",
        },
        "source": {"provider": "huggingface", "captured_at": CAPTURE_DATE, "sources": sources,
                   "layout": layout},
    }
    if choice.get("requires_packages"):
        manifest["requires_packages"] = choice["requires_packages"]
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("model-manifests"))
    parser.add_argument("--write", action="store_true", help="write the captured manifest files")
    args = parser.parse_args()
    # Choices do not share mutable state.  Four workers keep the bounded
    # metadata/small-file capture practical without issuing a broad crawl.
    with ThreadPoolExecutor(max_workers=4) as executor:
        manifests = list(executor.map(capture, CHOICES))
    if args.write:
        args.output.mkdir(parents=True, exist_ok=True)
        for manifest in manifests:
            (args.output / f"{manifest['package_id']}.json").write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"packages": [item["package_id"] for item in manifests], "written": args.write}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
