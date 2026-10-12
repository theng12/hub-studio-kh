#!/usr/bin/env python3
"""Model-free imports required by the Image worker's registered mflux models.

The classes are read from the Image component's own registry
(``components/image/app/backend/generation.py``, ``MODELS``) rather than copied here. This smoke
used to import the FLUX.2 Klein class and nothing else, so a shipped runtime whose ``z_image``
module was missing passed the smoke and failed only when the owner ran Z-Image-Turbo 4-bit. A
model added to that registry is now imported here without editing this file, and a registry that
cannot be read is a failure rather than a short list that quietly passes.

Pass ``--registry <path>`` when running a copy of this file that has been staged away from the
repository (``packaging/image-runtime/build.sh`` does): the default looks for the component
relative to this file in both the repository and a staged app layout.
"""

from __future__ import annotations

import argparse
import ast
from importlib import import_module, metadata
import json
from pathlib import Path
import platform
import subprocess
import sys


COMPONENT_REGISTRY = Path("components/image/app/backend/generation.py")
BASE_MODULES = (
    "mflux",
    "fastapi",
    "uvicorn",
    "mlx.core",
    "PIL",
    "numpy",
    "safetensors",
    "transformers",
    "mflux.models.common.config.model_config",
)


def registry_path(explicit: Path | None) -> Path:
    """The Image component's model registry, from the argument or this file's own location."""
    if explicit is not None:
        candidates = [explicit]
    else:
        here = Path(__file__).resolve()
        # `parents` is a sequence without slicing on every Python this runs on, hence the index
        # loop: two levels up is the repository layout, three is a staged app's Resources root.
        candidates = [here.parents[index] / COMPONENT_REGISTRY
                      for index in (2, 3) if index < len(here.parents)]
    for candidate in candidates:
        if candidate.is_file():
            source = candidate.resolve()
            registry_root(source)
            return source
    raise SystemExit(
        f"cannot find {COMPONENT_REGISTRY} (looked in "
        f"{', '.join(str(candidate) for candidate in candidates)}); pass --registry <path> so the "
        f"smoke imports the model classes the Image worker actually registers.")


def registry_root(source: Path) -> Path:
    """Repository root implied by the resolved Image registry path."""
    suffix = COMPONENT_REGISTRY.parts
    if source.parts[-len(suffix):] != suffix:
        raise SystemExit(
            f"registry must end with {COMPONENT_REGISTRY}, got {source}; refusing to add an "
            f"unrelated directory to the isolated runtime's import path."
        )
    return source.parents[len(suffix) - 1]


def registry_classes(source: Path) -> list[str]:
    """The ``'<import path>:<ClassName>'`` entries ``MODELS`` is built from.

    Parsed, not imported: this script runs inside the shipped runtime, where the worker's own
    package tree is not on ``sys.path`` and importing it would pull in the whole FastAPI app just
    to read a table. Anything other than the registry's current shape is a failure naming the file
    and what was expected, because a partially-read registry is exactly the silent pass this smoke
    exists to prevent.
    """
    tree = ast.parse(source.read_text(), filename=str(source))
    assigned: dict[str, ast.expr] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assigned[target.id] = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value:
            assigned[node.target.id] = node.value
    models = assigned.get("MODELS")
    if models is None:
        raise SystemExit(f"{source} has no module-level MODELS registry; this smoke reads it to "
                         f"learn which model classes the Image worker can load.")
    classes: list[str] = []
    for name in _registry_names(models, source):
        entry = assigned.get(name)
        if not isinstance(entry, ast.Call):
            raise SystemExit(f"{source}: {name} is not a model entry call; MODELS must be built "
                             f"from registered ImageModel entries so this smoke can read them.")
        keyword = next((item for item in entry.keywords if item.arg == "model_class"), None)
        if keyword is None or not isinstance(keyword.value, ast.Constant) \
                or not isinstance(keyword.value.value, str):
            raise SystemExit(f"{source}: {name} has no 'model_class' string; this smoke reads it "
                             f"to import the class the worker will load.")
        # A native engine has no Python model class to import. Its empty value is deliberate and
        # is checked by ``native_leaf`` below instead of being turned into a fake Python import.
        if keyword.value.value:
            classes.append(keyword.value.value)
    if not classes:
        raise SystemExit(f"{source}: MODELS names no models, so this smoke would import nothing.")
    return classes


def _registry_names(models: ast.expr, source: Path) -> list[str]:
    """The entry names the MODELS expression iterates over."""
    if isinstance(models, ast.Dict):
        # `MODELS = {model.repo: model for model in (...)}` builds a comprehension, so a plain
        # dict here means the registry was restated - accept name values as well.
        return sorted({node.id for node in ast.walk(models) if isinstance(node, ast.Name)})
    names: list[str] = []
    for node in ast.walk(models):
        if isinstance(node, ast.comprehension):
            names.extend(item.id for item in ast.walk(node.iter) if isinstance(item, ast.Name))
    if not names:
        raise SystemExit(f"{source}: MODELS is not a model comprehension; this smoke cannot read "
                         f"the registered classes from it.")
    return names


def native_leaf(runtime: Path) -> dict[str, str]:
    """The pinned native leaf staged beside the macOS-14 Python runtime."""
    manifest_path = runtime / "manifest" / "mlx-serve.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise SystemExit(f"cannot read {manifest_path}: {error}") from error
    binary = runtime / str(manifest.get("path") or "") / "mlx-serve"
    if not binary.is_file():
        raise SystemExit(f"mlx-serve native leaf is missing: {binary}")
    result = subprocess.run(
        [str(binary), "--version"], capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin"}, timeout=30,
    )
    output = result.stdout + "\n" + result.stderr
    expected = str(manifest.get("version") or "")
    if result.returncode != 0 or f"mlx-serve {expected}" not in output.splitlines():
        raise SystemExit(
            f"mlx-serve native leaf did not report pinned version {expected!r}: "
            f"exit {result.returncode}"
        )
    return {"version": expected, "minimum_macos": str(manifest.get("minimum_macos"))}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path,
                        help=f"path to {COMPONENT_REGISTRY} (default: found next to this file)")
    arguments = parser.parse_args()
    source = registry_path(arguments.registry)
    classes = registry_classes(source)
    sys.path.append(str(registry_root(source)))
    runtime = Path(sys.executable).resolve().parents[1]
    native = native_leaf(runtime)
    for module in BASE_MODULES:
        import_module(module)
    for entry in classes:
        module_name, separator, class_name = entry.partition(":")
        if not separator or not module_name or not class_name:
            raise SystemExit(f"{source}: model_class {entry!r} is not '<import path>:<ClassName>'")
        module = import_module(module_name)
        # The class, not just its module: `import_module` succeeds for a module that no longer
        # defines the class the worker asks for, which is a real way this ships broken.
        getattr(module, class_name)
    print(
        json.dumps(
            {
                "machine": platform.machine(),
                "python": platform.python_version(),
                "registry": str(source),
                "models": classes,
                "mlx_serve": native,
                "packages": {
                    name: metadata.version(name)
                    for name in ("mflux", "mlx", "mlx-metal", "Pillow", "numpy", "safetensors", "transformers", "fastapi", "uvicorn")
                },
                "executable": sys.executable,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
