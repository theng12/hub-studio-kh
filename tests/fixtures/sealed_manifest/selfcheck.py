#!/usr/bin/env python3
"""Standalone stdlib self-check for the sealed-manifest cross-app fixture.

No imports outside Python standard library (no Hub or Gateway dependencies).
Verifies:
  1. Valid sealed bundle closure (native binary and signature closure).
  2. Sidecar location vs legacy fallback lookup order.
  3. Stale 0.6.224 baseline detection, refusal, and wire probe fallback.
  4. Corrupted bytes detection and rejection.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path

FIXTURE_PATH = Path(__file__).resolve().parent / "sealed_manifest_fixture.json"


def load_fixture() -> dict:
    if not FIXTURE_PATH.is_file():
        raise RuntimeError(f"Fixture file missing: {FIXTURE_PATH}")
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def verify_manifest_hash_closure(fixture: dict) -> None:
    """Check that the manifest deterministically commits to all files in the app bundle."""
    valid = fixture["valid_sealed_release"]
    index = valid["manifest_index"]
    tsv_text = valid["manifest_tsv"]["core"]
    hashes = valid["hashes"]

    # 1. Uncompressed TSV hash
    tsv_bytes = tsv_text.encode("utf-8")
    actual_core_sha = hashlib.sha256(tsv_bytes).hexdigest()
    assert actual_core_sha == hashes["core_uncompressed_tsv_sha256"], (
        f"Core TSV sha256 mismatch: {actual_core_sha} != {hashes['core_uncompressed_tsv_sha256']}"
    )
    assert actual_core_sha == index["groups"]["core"]["sha256"], (
        "Index core sha256 does not match computed TSV sha256"
    )

    # 2. Gzipped TSV hash
    gz_bytes = base64.b64decode(valid["manifest_tsv_gz_b64"]["core"])
    actual_gz_sha = hashlib.sha256(gz_bytes).hexdigest()
    assert actual_gz_sha == hashes["core_tsv_gz_sha256"], (
        f"Gzip TSV sha256 mismatch: {actual_gz_sha} != {hashes['core_tsv_gz_sha256']}"
    )
    unpacked_tsv = gzip.decompress(gz_bytes).decode("utf-8")
    assert unpacked_tsv == tsv_text, "Decompressed TSV does not match original"

    # 3. Aggregate files_sha256 commitment
    group_shas = {name: item["sha256"] for name, item in index["groups"].items()}
    body = "".join(f"{name}\0{group_shas[name]}\n" for name in sorted(group_shas))
    computed_files_sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    assert computed_files_sha == index["files_sha256"], (
        f"files_sha256 mismatch: {computed_files_sha} != {index['files_sha256']}"
    )
    assert computed_files_sha == hashes["files_sha256"], (
        "Computed files_sha256 does not match fixture hashes"
    )

    # 4. Verify native and code signature closure
    app_files = valid["app_files"]
    assert "Contents/MacOS/StudioHub" in app_files, "Native executable missing from closure"
    assert "Contents/_CodeSignature/CodeResources" in app_files, (
        "Code signature CodeResources missing from closure"
    )
    assert app_files["Contents/MacOS/StudioHub"]["executable"] is True, (
        "Native executable must be flagged executable"
    )

    # 5. Verify every entry in TSV matches app_files
    lines = [line.split("\t") for line in tsv_text.splitlines() if line]
    assert len(lines) == len(app_files), "TSV line count does not match file count"
    for kind, x, size, digest, relpath in lines:
        assert relpath in app_files, f"Unlisted path in TSV: {relpath}"
        expected = app_files[relpath]
        assert kind == expected["kind"]
        assert (x == "1") == expected["executable"]
        assert int(size) == expected["size"]
        assert digest == expected["sha256"]


def test_consumer_lookup_and_closure(fixture: dict) -> None:
    """Simulate mounted DMG and test consumer manifest lookup logic."""
    valid = fixture["valid_sealed_release"]
    app_files = valid["app_files"]
    index = valid["manifest_index"]
    gz_bytes = base64.b64decode(valid["manifest_tsv_gz_b64"]["core"])

    with tempfile.TemporaryDirectory(prefix="sealed-manifest-test-") as temp_dir:
        mount_root = Path(temp_dir)
        app_dir = mount_root / "Studio Hub.app"
        sidecar_dir = mount_root / "file-manifest"

        # Materialize files in app bundle
        for rel, info in app_files.items():
            dest = app_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            content = base64.b64decode(info["content_b64"])
            dest.write_bytes(content)
            if info["executable"]:
                os.chmod(dest, 0o755)

        # Write sidecar outside app bundle (in DMG root)
        sidecar_dir.mkdir(parents=True, exist_ok=True)
        (sidecar_dir / "index.json").write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
        (sidecar_dir / "core.tsv.gz").write_bytes(gz_bytes)

        # Confirm app bundle itself contains NO file-manifest
        assert not (app_dir / "Contents/Resources/file-manifest").exists(), (
            "file-manifest must not be written inside the sealed bundle"
        )

        # Consumer lookup resolution function
        def resolve_manifest_dir(dmg_mount: Path) -> Path | None:
            sidecar = dmg_mount / "file-manifest"
            if (sidecar / "index.json").is_file():
                return sidecar
            legacy = dmg_mount / "Studio Hub.app/Contents/Resources/file-manifest"
            if (legacy / "index.json").is_file():
                return legacy
            return None

        # Check sidecar resolution (Priority 1)
        resolved = resolve_manifest_dir(mount_root)
        assert resolved == sidecar_dir, f"Expected sidecar resolution, got {resolved}"

        # Test legacy fallback (Priority 2)
        shutil.rmtree(sidecar_dir)
        legacy_dir = app_dir / "Contents/Resources/file-manifest"
        legacy_dir.mkdir(parents=True, exist_ok=True)
        (legacy_dir / "index.json").write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
        resolved_legacy = resolve_manifest_dir(mount_root)
        assert resolved_legacy == legacy_dir, f"Expected legacy resolution, got {resolved_legacy}"

        # Test missing manifest refusal (Priority 3)
        shutil.rmtree(legacy_dir)
        assert resolve_manifest_dir(mount_root) is None, "Should return None when no manifest found"


def test_stale_224_baseline_rejection(fixture: dict) -> None:
    """Verify that a 224 baseline with stale native hash and unlisted signature is refused."""
    stale_case = fixture["stale_224_baseline_case"]
    actual_disk = stale_case["installed_app_disk_hashes"]
    declared_manifest = stale_case["embedded_224_manifest_records"]

    mismatches = []
    # 1. Native executable check
    native_disk = actual_disk["Contents/MacOS/StudioHub"]
    native_decl = declared_manifest["Contents/MacOS/StudioHub"]["declared_sha256"]
    if native_disk != native_decl:
        mismatches.append({
            "path": "Contents/MacOS/StudioHub",
            "type": "digest_mismatch",
            "declared": native_decl,
            "actual": native_disk,
        })

    # 2. Code signature presence check
    sig_disk = actual_disk["Contents/_CodeSignature/CodeResources"]
    sig_decl = declared_manifest["Contents/_CodeSignature/CodeResources"]["declared_sha256"]
    if sig_decl is None and sig_disk is not None:
        mismatches.append({
            "path": "Contents/_CodeSignature/CodeResources",
            "type": "unlisted_in_manifest",
            "declared": None,
            "actual": sig_disk,
        })

    assert len(mismatches) == 2, f"Expected 2 mismatches on 224 baseline, found {len(mismatches)}"
    verdict = "UNTRUSTED_BASELINE" if mismatches else "TRUSTED_BASELINE"
    assert verdict == stale_case["evaluation_verdict"], f"Expected {stale_case['evaluation_verdict']}, got {verdict}"

    # 3. Verify wire response for untrusted baseline
    wire = stale_case["wire_probe_response"]
    assert wire["delta"] is None, "Untrusted baseline must publish delta: None"

    # 4. Gateway fallback evaluation
    capable = isinstance(wire.get("delta"), dict) and wire.get("delta", {}).get("schema") == fixture["schema"]
    assert capable is False, "Gateway probe must evaluate capable as False"

    planned_mode = "full" if not capable else "delta"
    assert planned_mode == "full", "Gateway must plan full mode when capable is False"


def test_corrupted_bytes_detection(fixture: dict) -> None:
    """Verify that corrupted bytes in any app file are caught by digest comparison."""
    corrupt = fixture["corrupted_bytes_case"]
    path = corrupt["target_file"]
    expected_sha = corrupt["expected_sha256"]
    corrupted_bytes = base64.b64decode(corrupt["corrupted_b64"])
    actual_sha = hashlib.sha256(corrupted_bytes).hexdigest()

    assert actual_sha == corrupt["corrupted_sha256"], "Corrupted bytes sha256 mismatch"
    assert actual_sha != expected_sha, "Corrupted bytes must differ from declared digest"


def main() -> int:
    print("Loading sealed-manifest fixture...")
    fixture = load_fixture()

    print("Checking hash closure of valid sealed release...")
    verify_manifest_hash_closure(fixture)
    print("  PASS: Hash closure and native/signature records verified.")

    print("Checking consumer lookup order (sidecar vs legacy fallback)...")
    test_consumer_lookup_and_closure(fixture)
    print("  PASS: Consumer lookup resolution verified.")

    print("Checking stale 0.6.224 baseline rejection & whole-DMG fallback...")
    test_stale_224_baseline_rejection(fixture)
    print("  PASS: Stale 224 baseline refused and Gateway fallback verified.")

    print("Checking corrupted bytes detection...")
    test_corrupted_bytes_detection(fixture)
    print("  PASS: Corrupted bytes detected and rejected.")

    print("\nALL CHECKS PASSED: Fixture satisfies cross-app sealed-manifest contract.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
