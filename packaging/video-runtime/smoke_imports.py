#!/usr/bin/env python3
"""Model-free imports the Video worker's registered engines require.

The engines are read from the component's own registry
(``components/video/app/backend/generation.py``, ``VIDEO_ENGINES``) rather than copied here, so a
new engine added to that tuple fails this smoke until its runtime module is named. That is the
same shape as the Image runtime's smoke, and it exists for the same reason: a shipped runtime
missing a module passes a hand-written import list and fails only when the owner runs the model.

``video-lance`` deliberately maps to None: the lance-mlx source runner is not adopted into this
build, and the smoke says so instead of pretending the module exists.
"""
from __future__ import annotations

import argparse
import ast
from importlib import import_module
from pathlib import Path
import sys


COMPONENT_REGISTRY = Path("components/video/app/backend/generation.py")
BASE_MODULES = ("fastapi", "uvicorn", "multipart")
ENGINE_MODULES: dict[str, tuple[str, ...] | None] = {
    "video-ltx": ("mlx.core", "ltx_pipelines_mlx"),
    "video-lance": None,
}


def registry_path(explicit: Path | None) -> Path:
    if explicit is not None:
        candidates = [explicit]
    else:
        here = Path(__file__).resolve()
        candidates = [here.parents[index] / COMPONENT_REGISTRY
                      for index in (2, 3) if index < len(here.parents)]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise SystemExit(
        f"cannot find {COMPONENT_REGISTRY} (looked in "
        f"{', '.join(str(candidate) for candidate in candidates)}); pass --registry <path> so the "
        f"smoke imports the engines the Video worker actually registers.")


def registry_engines(source: Path) -> list[str]:
    tree = ast.parse(source.read_text(), filename=str(source))
    for node in tree.body:
        name = None
        if isinstance(node, ast.Assign):
            names = [target.id for target in node.targets if isinstance(target, ast.Name)]
            name = names[0] if len(names) == 1 else None
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
        if name == "VIDEO_ENGINES" and isinstance(node.value, (ast.Tuple, ast.List)):
            return [element.value for element in node.value.elts
                    if isinstance(element, ast.Constant) and isinstance(element.value, str)]
    raise SystemExit(f"{source} has no module-level VIDEO_ENGINES tuple; this smoke reads it to "
                     f"know which engines a shipped runtime must be able to import.")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path)
    args = parser.parse_args(argv)
    source = registry_path(args.registry)
    engines = registry_engines(source)
    missing = [engine for engine in engines if engine not in ENGINE_MODULES]
    if missing:
        raise SystemExit(
            f"{source} registers {missing}, which this smoke does not know; add each engine's "
            f"runtime modules to ENGINE_MODULES in {Path(__file__).name}")
    for module in BASE_MODULES:
        import_module(module)
    checked = []
    for engine in engines:
        modules = ENGINE_MODULES[engine]
        if modules is None:
            print(f"{engine}: no local runner in this build (recorded, not imported)")
            continue
        for module in modules:
            import_module(module)
        checked.append(engine)
    print(f"engines: {', '.join(engines)}")
    print(f"imported: {', '.join(BASE_MODULES + tuple(checked))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
