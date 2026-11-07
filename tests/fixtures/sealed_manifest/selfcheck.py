#!/usr/bin/env python3
"""Stdlib self-check for the sealed-manifest cross-app fixture (v2).

Imports the real Hub file_manifest source by path (stdlib-only module; no Hub
or service app import). Acceptance cases are validated as DATA ONLY structure -
this script does not emulate Gateway fallback and does not claim a Gateway PASS.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURE_PATH = HERE / "sealed_manifest_fixture.json"
FM_PATH = HERE.parents[2] / "components" / "hub" / "backend" / "file_manifest.py"


def load_fixture() -> dict:
    if not FIXTURE_PATH.is_file():
        raise RuntimeError(f"Fixture file missing: {FIXTURE_PATH}")
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def load_fm():
    repo_root = str(HERE.parents[2])
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    spec = importlib.util.spec_from_file_location("hub_file_manifest", FM_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load real fm source: {FM_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_valid_bytes_and_closure(fixture: dict, fm) -> None:
    valid = fixture["valid_sealed_release"]
    app_files = valid["app_files"]
    # Every declared byte: size + sha256 from actual base64 content.
    with tempfile.TemporaryDirectory(prefix="sealed-fixture-app-") as tmp:
        app_root = Path(tmp) / "Studio Hub.app"
        for rel, row in app_files.items():
            data = base64.b64decode(row["content_b64"], validate=True)
            assert len(data) == row["size"], f"{rel}: size mismatch"
            assert hashlib.sha256(data).hexdigest() == row["sha256"], f"{rel}: sha mismatch"
            dest = app_root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            if row["executable"]:
                dest.chmod(0o755)
            else:
                dest.chmod(0o644)
        # Real Hub scan/build must reproduce the fixture index and TSVs.
        entries = fm.scan(app_root)
        index, tsv = fm.build(entries)
        assert index == valid["manifest_index"], "fm.build index != fixture index"
        assert tsv["core"] == valid["manifest_tsv"]["core"].encode(), "fm.build core TSV mismatch"
        gz = fm.gzip_bytes(tsv["core"])
        assert hashlib.sha256(gz).hexdigest() == valid["hashes"]["core_tsv_gz_sha256"], (
            "fm.gzip_bytes digest mismatch"
        )
        # Real check_index / check_group accept the fixture as stored.
        fm.check_index(json.loads(json.dumps(valid["manifest_index"])))
        fm.check_group(valid["manifest_index"], "core", tsv["core"])
        # Tampered native: reject by actual hash compare.
        native = app_root / "Contents/MacOS/StudioHub"
        bad_native = base64.b64decode(fixture["corrupted_bytes_case"]["corrupted_b64"], validate=True)
        assert hashlib.sha256(bad_native).hexdigest() == fixture["corrupted_bytes_case"]["corrupted_sha256"]
        assert hashlib.sha256(bad_native).hexdigest() != app_files["Contents/MacOS/StudioHub"]["sha256"]
        native.write_bytes(bad_native)
        tampered = fm.scan(app_root)
        assert tampered["Contents/MacOS/StudioHub"][3] != app_files["Contents/MacOS/StudioHub"]["sha256"], (
            "tampered native must not match manifest digest"
        )
        # Tampered signature: reject by actual hash compare.
        sig = app_root / "Contents/_CodeSignature/CodeResources"
        bad_sig = base64.b64decode(fixture["corrupted_signature_case"]["corrupted_b64"], validate=True)
        assert hashlib.sha256(bad_sig).hexdigest() == fixture["corrupted_signature_case"]["corrupted_sha256"]
        assert hashlib.sha256(bad_sig).hexdigest() != app_files["Contents/_CodeSignature/CodeResources"]["sha256"]
        sig.write_bytes(bad_sig)
        tampered = fm.scan(app_root)
        assert tampered["Contents/_CodeSignature/CodeResources"][3] != (
            app_files["Contents/_CodeSignature/CodeResources"]["sha256"]
        ), "tampered signature must not match manifest digest"
        # Corrupted group: real check_group refuses.
        try:
            fm.check_group(valid["manifest_index"], "core", b"wrong group\n")
        except fm.ManifestError:
            pass
        else:
            raise AssertionError("corrupted group must be refused by check_group")


def check_stale_224_unit_data(fixture: dict) -> None:
    stale = fixture["stale_224_baseline_case"]
    mismatches = []
    native_disk = stale["installed_app_disk_hashes"]["Contents/MacOS/StudioHub"]
    native_decl = stale["embedded_224_manifest_records"]["Contents/MacOS/StudioHub"]["declared_sha256"]
    if native_disk != native_decl:
        mismatches.append("native_digest_mismatch")
    sig_disk = stale["installed_app_disk_hashes"]["Contents/_CodeSignature/CodeResources"]
    sig_decl = stale["embedded_224_manifest_records"]["Contents/_CodeSignature/CodeResources"]["declared_sha256"]
    if sig_decl is None and sig_disk is not None:
        mismatches.append("signature_unlisted")
    assert len(mismatches) == 2, f"expected 2 stale mismatches, got {mismatches}"
    assert stale["evaluation_verdict"] == "UNTRUSTED_BASELINE"
    assert "wire_probe_response" not in stale, "fictional wire probe must be replaced"
    assert stale["unit_data_only"] is True
    wire = fixture["actual_224_wire"]
    assert wire["api_version_running"] == "0.6.224"
    assert wire["delta"] == {"schema": "studiohub.file-manifest.v1", "manifest": True, "artifact": True}
    assert wire["existing_probe_capable"] is True
    assert wire["contract_decision"].startswith("full")


def check_acceptance_cases_data_only(fixture: dict) -> None:
    cases = fixture["acceptance_cases"]
    ids = [c["id"] for c in cases]
    assert ids == [f"AC{i}" for i in range(1, 10)], f"unexpected case ids: {ids}"
    for case in cases:
        assert case["check_kind"] == "DATA_ONLY_STATIC", case["id"]
        assert case["gateway_test"].startswith("PENDING"), case["id"]
        assert case["expected_decision"] and case["basis"], case["id"]
    by_id = {c["id"]: c for c in cases}
    assert "224" in by_id["AC1"]["given"] or "0.6.224" in by_id["AC1"]["given"]
    assert "prerelease" in by_id["AC3"]["given"].lower()
    assert by_id["AC4"]["expected_decision"] == "delta eligible"
    assert by_id["AC6"]["expected_decision"].startswith("REFUSE")
    delta = fixture["delta_eligibility"]
    assert delta["first_shipped_fixed_release_threshold"]["version"] == "0.6.225"
    assert delta["baseline_identity"]["source"].startswith("strict actual running version")
    assert delta["baseline_identity"]["runtime_exact_build_allowlist"] is False
    assert delta["trust_gate"]["AppUpdateKey_untrusted"].startswith("publisher-key trust gate")
    obs = fixture["gateway_reader_observations"]
    assert obs["sidecar_only"]["accepted"] is False
    assert obs["corrupt_native_legacy"]["accepted"] is True  # recorded KNOWN BUG
    assert obs["corrupt_group_legacy"]["accepted"] is False


def main() -> int:
    print("Loading sealed-manifest fixture (v2)...")
    fixture = load_fixture()
    fm = load_fm()
    print(f"  Real fm source: {FM_PATH}")

    print("Checking valid bytes, hash closure, real fm scan/build/check...")
    check_valid_bytes_and_closure(fixture, fm)
    print("  PASS: byte closure + real Hub fm reproduce index/TSV; tampers rejected.")

    print("Checking stale 0.6.224 unit data (no fictional wire probe)...")
    check_stale_224_unit_data(fixture)
    print("  PASS: 2 unit mismatches retained; actual 224 wire shape recorded.")

    print("Checking acceptance cases (DATA ONLY; actual Gateway test PENDING)...")
    check_acceptance_cases_data_only(fixture)
    print("  PASS: AC1-AC9 structure and eligibility rules verified as data.")

    print("\nALL CHECKS PASSED (DATA ONLY - not a Gateway PASS).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
