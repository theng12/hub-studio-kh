"""Installer-owned Music package resolution.

Music never guesses a checkpoint directory. The trusted model-package receipt
selects the installed revision folder, and the engine loads its model from it.
This keeps import, verification, Hub readiness, and inference on one identity.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from components import model_packages

from . import catalog


MANIFEST_NAME = catalog.manifest_name_for(catalog.DEFAULT_REPO)
LIBRARY_ENV = "STUDIOHUB_MUSIC_LIBRARY_DIR"
MANIFEST_ENV = "STUDIOHUB_MUSIC_MANIFEST"
ALLOW_DOWNLOAD_ENV = "STUDIOHUB_MUSIC_ALLOW_DOWNLOAD"
HF_ENDPOINT = "https://huggingface.co"

DEFAULT_LIBRARY = (Path.home() / "Library" / "Application Support"
                   / "StudioHub Standalone" / "models")


def _candidate_manifests(name: str = MANIFEST_NAME) -> list[Path]:
    here = Path(__file__).resolve()
    root = here.parents[4]
    return [root / "model-manifests" / name]


def resolve_manifest_path(explicit: Optional[os.PathLike | str] = None,
                          name: str = MANIFEST_NAME) -> Path:
    if explicit:
        candidates = [Path(explicit).expanduser()]
    elif name == MANIFEST_NAME and os.environ.get(MANIFEST_ENV):
        # The env override names the default engine's manifest; a secondary engine
        # resolves its own file by name so it cannot inherit the wrong package.
        candidates = [Path(os.environ[MANIFEST_ENV]).expanduser()]
    else:
        candidates = _candidate_manifests(name)
    for path in candidates:
        if path.is_file():
            return path
    raise FileNotFoundError(
        f"Music manifest {name} was not found next to the worker; "
        f"set {MANIFEST_ENV}")


def resolve_library_root(explicit: Optional[os.PathLike | str] = None) -> Path:
    value = explicit or os.environ.get(LIBRARY_ENV) or DEFAULT_LIBRARY
    return Path(value).expanduser()


class MusicCache:
    """View of one package installed and verified by model_packages."""

    def __init__(self, checkpoints: os.PathLike | str | None = None, *,
                 library: os.PathLike | str | None = None,
                 manifest_path: os.PathLike | str | None = None,
                 repo: str | None = None) -> None:
        if checkpoints is not None:
            raise ValueError(
                "External Music checkpoint roots are unsupported; install the "
                "package in the selected model library")
        self.repo = repo or catalog.DEFAULT_REPO
        self.library = resolve_library_root(library)
        self.manifest_path = resolve_manifest_path(
            manifest_path, catalog.manifest_name_for(self.repo))
        self.manifest = json.loads(self.manifest_path.read_text())
        model_packages.validate_manifest(self.manifest)
        if self.manifest["package_id"] != catalog.package_id_for(self.repo):
            raise ValueError("Music manifest package identity does not match the worker")
        expected_engine = catalog.manifest_engine_for(self.repo)
        if self.manifest["engine"] != expected_engine:
            raise ValueError(
                f"Music manifest engine does not match the model: {self.manifest_path} declares "
                f"{self.manifest['engine']!r}, and {self.repo} declares {expected_engine!r} "
                f"(catalog.ModelEntry.manifest_engine)")
        # Where this engine's files live inside the imported package folder.
        self.root_relative = catalog.package_root_for(self.repo)

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
        # With the catalog, because a package's companions are named by *other* manifests: without
        # it a companion check cannot see them and answers `dependency_missing` for a package whose
        # companion is installed and verified. Both packages here declare companions on this
        # engine's side (`yue2-3b-mlx` requires `yue2-vae`).
        catalog_map = model_packages.load_catalog(self.manifest_path.parent)
        if deep:
            return model_packages.verify_installed(self.manifest, self.library,
                                                    catalog=catalog_map)
        return model_packages.preflight(self.manifest, self.library, catalog=catalog_map)

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
        checkpoints = installed.joinpath(*self.root_relative.split("/"))
        try:
            if installed.is_symlink() or checkpoints.is_symlink() or not checkpoints.is_dir():
                raise OSError()
            checkpoints.resolve(strict=True).relative_to(installed.resolve(strict=True))
        except (OSError, ValueError):
            return {**base, "state": "invalid",
                    "reason": "Verified Music package has no safe model checkpoint root"}
        return {**base, "state": "cached", "checkpoints_root": str(checkpoints)}

    def companion_root(self, package_id: str) -> Optional[Path]:
        """The model directory of one companion package, or ``None`` when it is not verified here.

        A companion is named by *another* manifest and lives in its own folder of the library
        (``<library>/<package_id>/<revision>/<layout destination>``), so it is resolved the way
        this package's own folder is: through that package's preflight receipt, never through a
        key on this package's report. YuE2's decoder is the case - its files are installed and
        verified, and the port loads them by path.
        """
        catalog_map = model_packages.load_catalog(self.manifest_path.parent)
        companion = catalog_map.get(package_id)
        if companion is None:
            return None
        report = model_packages.preflight(companion, self.library, catalog=catalog_map)
        if report["state"] != "files_verified":
            return None
        layout = (companion.get("source") or {}).get("layout") or {}
        destination = str((layout.get("model") or {}).get("destination") or "").strip("/")
        root = Path(report["folder"]).joinpath(*destination.split("/")) if destination \
            else Path(report["folder"])
        return root if root.is_dir() and not root.is_symlink() else None

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
            repo = str(entry.get("repo") or (model.repo if model else catalog.DEFAULT_REPO))
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
