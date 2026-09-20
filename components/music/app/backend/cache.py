"""Installer-owned ACE-Step package resolution.

Music never guesses a checkpoint directory. The trusted model-package receipt
selects the installed revision folder, and the engine loads its model child.
This keeps import, verification, Hub readiness, and inference on one identity.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from components import model_packages

from . import catalog


MANIFEST_NAME = "ace-step-1-5-turbo.json"
LIBRARY_ENV = "STUDIOHUB_MUSIC_LIBRARY_DIR"
MANIFEST_ENV = "STUDIOHUB_MUSIC_MANIFEST"
ALLOW_DOWNLOAD_ENV = "STUDIOHUB_MUSIC_ALLOW_DOWNLOAD"
HF_ENDPOINT = "https://huggingface.co"

DEFAULT_LIBRARY = (Path.home() / "Library" / "Application Support"
                   / "StudioHub Standalone" / "models")


def _candidate_manifests() -> list[Path]:
    here = Path(__file__).resolve()
    root = here.parents[4]
    return [root / "model-manifests" / MANIFEST_NAME]


def resolve_manifest_path(explicit: Optional[os.PathLike | str] = None) -> Path:
    if explicit:
        candidates = [Path(explicit).expanduser()]
    elif os.environ.get(MANIFEST_ENV):
        candidates = [Path(os.environ[MANIFEST_ENV]).expanduser()]
    else:
        candidates = _candidate_manifests()
    for path in candidates:
        if path.is_file():
            return path
    raise FileNotFoundError(
        f"Music manifest {MANIFEST_NAME} was not found next to the worker; "
        f"set {MANIFEST_ENV}")


def resolve_library_root(explicit: Optional[os.PathLike | str] = None) -> Path:
    value = explicit or os.environ.get(LIBRARY_ENV) or DEFAULT_LIBRARY
    return Path(value).expanduser()


class MusicCache:
    """View of one package installed and verified by model_packages."""

    def __init__(self, checkpoints: os.PathLike | str | None = None, *,
                 library: os.PathLike | str | None = None,
                 manifest_path: os.PathLike | str | None = None) -> None:
        if checkpoints is not None:
            raise ValueError(
                "External Music checkpoint roots are unsupported; install the "
                "package in the selected model library")
        self.library = resolve_library_root(library)
        self.manifest_path = resolve_manifest_path(manifest_path)
        self.manifest = json.loads(self.manifest_path.read_text())
        model_packages.validate_manifest(self.manifest)
        if self.manifest["package_id"] != catalog.PACKAGE_ID:
            raise ValueError("Music manifest package identity does not match the worker")
        if self.manifest["engine"] != catalog.ENGINE:
            raise ValueError("Music manifest engine does not match the worker")

    @property
    def package_id(self) -> str:
        return str(self.manifest["package_id"])

    @property
    def revision(self) -> str:
        return str(self.manifest["revision"])

    def entries(self) -> list[dict]:
        return list(self.manifest["files"])

    def total_bytes(self) -> int:
        return sum(int(entry["size"]) for entry in self.entries())

    def _package_status(self, *, deep: bool) -> dict:
        if deep:
            return model_packages.verify_installed(self.manifest, self.library)
        return model_packages.preflight(self.manifest, self.library)

    def status(self, *, deep: bool = False) -> dict:
        report = self._package_status(deep=deep)
        base = {
            **report,
            "manifest_revision": self.revision,
            "library_root": str(self.library),
            "manifest": str(self.manifest_path),
            "file_count": len(self.entries()),
            "total_bytes": self.total_bytes(),
        }
        if report["state"] != "files_verified":
            return {**base, "state": ("missing" if report["state"] == "not_installed"
                                      else report["state"])}
        installed = Path(report["folder"])
        checkpoints = installed / "model"
        try:
            if installed.is_symlink() or checkpoints.is_symlink() or not checkpoints.is_dir():
                raise OSError()
            checkpoints.resolve(strict=True).relative_to(installed.resolve(strict=True))
        except (OSError, ValueError):
            return {**base, "state": "invalid",
                    "reason": "Verified Music package has no safe model checkpoint root"}
        return {**base, "state": "cached", "checkpoints_root": str(checkpoints)}

    @property
    def checkpoints(self) -> Path:
        status = self.status()
        if status["state"] != "cached":
            raise RuntimeError(status.get("reason") or status["state"])
        return Path(status["checkpoints_root"])

    def status_for_model(self, model: catalog.ModelEntry, *, deep: bool = False) -> dict:
        status = self.status(deep=deep)
        return {
            "repo": model.repo,
            "checkpoint": model.checkpoint,
            "state": status["state"],
            "checkpoints": [
                {"name": item.name,
                 "state": "cached" if status["state"] == "cached" else status["state"]}
                for item in model.checkpoints
            ],
        }

    def download_plan(self, model: Optional[catalog.ModelEntry] = None) -> dict:
        by_repo: dict[str, dict] = {}
        for entry in self.entries():
            repo = str(entry.get("repo") or (model.repo if model else catalog.MAIN_REPO))
            bucket = by_repo.setdefault(
                repo, {"repo": repo, "files": 0, "bytes": 0,
                       "revision": entry.get("revision")})
            bucket["files"] += 1
            bucket["bytes"] += int(entry["size"])
        repos = []
        for bucket in by_repo.values():
            revision = bucket["revision"] or self.revision
            repos.append({
                **bucket,
                "snapshot_url": f"{HF_ENDPOINT}/{bucket['repo']}/tree/{revision}",
            })
        return {
            "package_id": self.package_id,
            "library_root": str(self.library),
            "repos": repos,
            "total_bytes": self.total_bytes(),
        }

    def download(self, model: Optional[catalog.ModelEntry] = None, *,
                 allow: bool = False) -> dict:
        if not allow or os.environ.get(ALLOW_DOWNLOAD_ENV) != "1":
            raise RuntimeError(
                "Music checkpoint download is disabled; use the Hub's explicit "
                "model-package download flow")
        folder = model_packages.download_package(self.manifest, self.library)
        return {"ok": True, "folder": str(folder), "status": self.status()}
