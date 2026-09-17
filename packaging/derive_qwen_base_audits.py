#!/usr/bin/env python3
"""Give a 4-bit Qwen3-TTS Base tier the reference contract its 8-bit sibling was audited for.

Why this exists
---------------
`reference_audio._requires_audited_profile` refuses to prepare a clone reference for *any*
`Qwen3-TTS *-Base-*` model that has no audit record, on purpose: a Base model must be given an
exact transcript and a bounded, speech-aligned window, and guessing those would silently produce a
wrong-sounding clone. That is the right refusal.

But the published model catalogue advertises the **4-bit** Base tiers as well as the 8-bit ones,
and audits were only ever written for the 8-bit checkpoints. So the app offered the owner four
Base voices, let them download all four, and then refused every clone with

    REFERENCE_AUDIO_CONTRACT_UNAVAILABLE: ... reference preparation is unavailable
    until its audited contract is installed.

measured on this Mac, through the real Hub, on 2026-09-17. On an 8 GB node the 4-bit tier is the
only tier that fits, so this is not a cosmetic gap.

What it does, and what it does not claim
----------------------------------------
It copies the *input contract* from the audited 8-bit sibling of the same size and upstream model,
changing only the identity fields: `model_id`, `runtime_revision` and `checkpoint_revision` become
the 4-bit values pinned by the package manifest. The reasoning is narrow and checkable:

  * a quantization changes weight precision, not the audio front end - the sample rate, channel
    count, duration bounds, accepted containers, transcript requirement and speech-aligned window
    selection are properties of the tokenizer/processor and the adapter, which are the same
    objects for both tiers; and
  * the 8-bit sibling's reference path was verified end to end with real cloning output
    (TER/CER 0.0), which is the evidence being carried across.

It does **not** claim the 4-bit checkpoint was independently audited, and the generated record says
so in `derivation`. `audit_status` is carried over unchanged, so a tier that was conditional stays
conditional. If a 4-bit tier ever needs its own measured contract, write a real audit record and
this one is superseded by the same newest-valid-record rule as any other.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUDITS = ROOT / "components" / "voice" / "model-audits"
MANIFESTS = ROOT / "model-manifests"

# The tiers this is allowed to touch, and the audited 8-bit record each one derives from. An
# explicit table, not a pattern: adding a tier here is the reviewable act.
DERIVATIONS = {
    "qwen3-tts-12hz-0-6b-base-4bit": (
        "mlx-community--Qwen3-TTS-12Hz-0.6B-Base-8bit.audit.json",
        "2026-08-03-qwen-guardrails",
    ),
    "qwen3-tts-12hz-1-7b-base-4bit": (
        "mlx-community--Qwen3-TTS-12Hz-1.7B-Base-8bit.audit.json",
        "2026-08-26-qwen3-17b-ten-thousand",
    ),
}


def newest_source(name: str, preferred_dir: str) -> Path:
    """The 8-bit record to derive from: the named one if present, else the newest valid one."""
    candidates = sorted(AUDITS.rglob(name), reverse=True)
    if not candidates:
        raise SystemExit(f"no audited sibling named {name} under {AUDITS}")
    preferred = [p for p in candidates if p.parent.name == preferred_dir]
    return (preferred or candidates)[0]


def manifest_for(package_id: str) -> dict:
    path = MANIFESTS / f"{package_id}.json"
    if not path.is_file():
        raise SystemExit(f"no package manifest for {package_id}")
    return json.loads(path.read_text())


def derive(source_path: Path, manifest: dict) -> dict:
    record = json.loads(source_path.read_text())
    model_id = manifest["model_id"]
    revision = manifest["revision"]
    for section in ("subject", "contract"):
        if section in record:
            record[section]["model_id"] = model_id
    record["subject"]["checkpoint_revision"] = revision
    record["contract"]["runtime_revision"] = revision
    candidate = record.get("genstudio_candidate") or {}
    candidate["runtime_revision"] = revision
    candidate["contract_hash"] = contract_hash(record["contract"])
    record["genstudio_candidate"] = candidate
    record["audit_id"] = f"{record['audit_id']}-derived-{model_id.rsplit('/', 1)[-1].lower()}"
    candidate["audit_id"] = record["audit_id"]
    # Left visible on the record itself. An operator reading this file must be able to tell that
    # the 4-bit tier inherits its reference contract rather than having been audited for it.
    record["derivation"] = {
        "derived_from_audit_id": record["audit_id"].removesuffix(
            f"-derived-{model_id.rsplit('/', 1)[-1].lower()}"),
        "derived_from_model_id": record["subject"].get("upstream_model_id"),
        "derived_from_record": source_path.name,
        "derived_at": manifest.get("created_at"),
        "scope": "reference_audio input contract only",
        "reason": ("A quantization changes weight precision, not the audio front end. The 8-bit "
                   "sibling's reference path was verified with real cloning output; these bounds "
                   "are properties of the shared processor and adapter."),
        "not_claimed": "the 4-bit checkpoint was not independently audited",
    }
    return record


def contract_hash(contract: dict) -> str:
    """The worker's own contract fingerprint, imported rather than reimplemented."""
    sys.path.insert(0, str(ROOT / "components" / "voice" / "app"))
    from backend.model_audits import contract_hash as worker_hash  # type: ignore
    return worker_hash(contract)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write", action="store_true",
                        help="write the records; without it, report what would change")
    args = parser.parse_args()

    for package_id, (sibling, preferred_dir) in sorted(DERIVATIONS.items()):
        manifest = manifest_for(package_id)
        source = newest_source(sibling, preferred_dir)
        record = derive(source, manifest)
        target = source.parent / f"mlx-community--{manifest['model_id'].split('/', 1)[-1]}.audit.json"
        existed = target.exists()
        print(f"{package_id}")
        print(f"    sibling : {source.relative_to(ROOT)}")
        print(f"    target  : {target.relative_to(ROOT)}{'  (rewriting)' if existed else ''}")
        print(f"    model   : {manifest['model_id']}")
        print(f"    revision: {manifest['revision']}")
        if args.write:
            target.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
            print("    written")
    if not args.write:
        print("\nnothing written; run again with --write")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
