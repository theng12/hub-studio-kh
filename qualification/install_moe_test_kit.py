#!/usr/bin/env python3
"""Copy only the standalone LLM test helpers into an existing SSD Technical folder."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import uuid

ROOT = Path(__file__).resolve().parents[1]
FILES = (
    "RUN-NEMOTRON-LLM.command", "RUN-QWEN36-LLM.command", "RUN-GEMMA4-LLM.command",
    "moe-qualification.sh", "moe_qualification.py", "moe_probe.py",
    "moe-prompts.json", "moe_probe_selfcheck.py", "README-MOE-LLM-QUALIFICATION.md",
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def install(technical: Path, source: Path = ROOT) -> dict:
    technical = technical.absolute()
    if technical.name != "Technical" or not technical.is_dir() or technical.is_symlink():
        raise ValueError("choose an existing, real kit Technical folder")
    if technical.resolve() != technical:
        raise ValueError("the Technical path must not contain symlinks")
    payload = {name: source / "qualification" / name for name in FILES}
    payload["moe-support/components/host_memory.py"] = source / "components/host_memory.py"
    # Validate the full source and all target ancestry before changing any file.
    for relative, original in payload.items():
        if not original.is_file() or original.is_symlink():
            raise ValueError(f"missing regular source helper: {original}")
        target = technical / relative
        for ancestor in (target, *target.parents):
            if ancestor == technical:
                break
            if ancestor.is_symlink():
                raise ValueError(f"refusing target symlink: {ancestor}")
        if target.exists() and not target.is_file():
            raise ValueError(f"target is not a regular file: {target}")
    receipt_path = technical / "moe-test-kit.json"
    if receipt_path.is_symlink() or (receipt_path.exists() and not receipt_path.is_file()):
        raise ValueError("invalid existing test-kit receipt")
    archive = technical / "_trash" / "moe-test-kit" / uuid.uuid4().hex
    for directory in (technical / "_trash", technical / "_trash/moe-test-kit"):
        if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
            raise ValueError("refusing invalid archive directory")

    def preserve(path: Path) -> None:
        destination = archive / path.relative_to(technical)
        destination.parent.mkdir(parents=True, exist_ok=True)
        path.rename(destination)

    rows = []
    for relative, original in payload.items():
        target = technical / relative
        expected = digest(original)
        if target.exists() and digest(target) != expected:
            preserve(target)
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, target)
        actual = digest(target)
        if actual != expected:
            raise ValueError(f"copied helper hash mismatch: {target}")
        if target.suffix in (".command", ".sh"):
            target.chmod(target.stat().st_mode | 0o111)
        rows.append({"file": relative, "bytes": target.stat().st_size, "sha256": actual})
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=source,
                            capture_output=True, text=True, check=True).stdout.strip()
    receipt = {"schema": "studiohub.moe-test-kit", "source_commit": commit,
               "inference_run": False, "files": rows,
               "scope": "Standalone test helpers only; app, models and production inventory untouched"}
    if receipt_path.exists():
        previous = json.loads(receipt_path.read_text())
        if previous == receipt:
            return receipt
        preserve(receipt_path)
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("technical", type=Path)
    args = parser.parse_args()
    print(json.dumps(install(args.technical), indent=2))


if __name__ == "__main__":
    main()
