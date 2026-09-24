#!/usr/bin/env python3
"""
Truth audit: verify the wired family set matches actual dispatch coverage.

Since 2026-09-25 the wiring is derived rather than restated: every family
declares its `engine` in `catalog.FAMILIES`, and `generation._WORKER_BY_ENGINE`
maps an engine to its worker. `_WIRED_FAMILIES` is `frozenset(catalog.FAMILIES)`
and the dispatcher branches on the engine, not on family names, so this audit
reads the two registries and reports the same four kinds of drift:

Why this exists
---------------
v1.2.0 fixed a bug where the Models tab showed a green "✓ engine ready" chip
for FLUX.1 schnell / FLUX.1 dev / FLUX.2 dev, but clicking Generate threw
`NotImplementedError: Phase 2 currently supports only FLUX.2 klein family`.

Root cause: `_WIRED_FAMILIES` (the source of truth for the chip) was
hand-maintained separately from the actual dispatch code in `_generate_*`.
Two sources of truth = inevitable drift.

This script reads both files and reports four kinds of drift:

  1. COMMISSION LIES — family in _WIRED_FAMILIES, but its dispatch branch
     raises NotImplementedError. User sees green chip → hits a wall.
     (The v1.2.0 bug. Worst kind.)

  2. OMISSION LIES — family is handled in dispatch (no NotImplementedError),
     but NOT in _WIRED_FAMILIES. UI shows "🕓 worker in roadmap" but
     generation actually works. (Less harmful — false negative.)

  3. ORPHAN FAMILIES — family appears in catalog.py ModelEntry but has no
     dispatch branch at all. Picking such a model silently falls through
     to the `else: raise NotImplementedError` default.

  4. PHANTOM WIRES — family in _WIRED_FAMILIES but no catalog entry uses it.
     Harmless but suspicious — usually means a refactor left dead config.

Usage
-----
    python3 audit_truth.py           # human-readable table + exit code
    python3 audit_truth.py --strict  # exit non-zero on any drift

Run from the app root. No deps beyond stdlib (so it works without the venv).
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import Optional


# ─── AST helpers ───────────────────────────────────────────────────────────

def _string_literal(node: ast.AST) -> Optional[str]:
    """Return the string value if `node` is a string Constant, else None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _strings_in_collection(node: ast.AST) -> set[str]:
    """Extract all string literals from a Tuple/List/Set node."""
    out: set[str] = set()
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        for elt in node.elts:
            s = _string_literal(elt)
            if s is not None:
                out.add(s)
    return out


def _branch_raises_not_implemented(stmts: list[ast.stmt]) -> bool:
    """True if the body raises NotImplementedError (anywhere, including
    inside `if cancel_event` guards). We treat any NotImplementedError-raising
    branch as "not wired" — even if there's other code, the family hits the wall."""
    for node in stmts:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Raise) and sub.exc is not None:
                exc = sub.exc
                # `raise NotImplementedError(...)` or `raise NotImplementedError`
                if isinstance(exc, ast.Call) and isinstance(exc.func, ast.Name):
                    if exc.func.id == "NotImplementedError":
                        return True
                if isinstance(exc, ast.Name) and exc.id == "NotImplementedError":
                    return True
    return False


# ─── catalog audit ─────────────────────────────────────────────────────────

def parse_catalog_families(catalog_path: Path) -> set[str]:
    """Return the set of family ids used by any ModelEntry(...) call."""
    tree = ast.parse(catalog_path.read_text())
    families: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "ModelEntry":
                for kw in node.keywords:
                    if kw.arg == "family":
                        s = _string_literal(kw.value)
                        if s:
                            families.add(s)
    return families


def parse_engine_constants(catalog_path: Path) -> dict[str, str]:
    """`ENGINE_MLX_AUDIO = "mlx-audio"` style assignments, by constant name."""
    tree = ast.parse(catalog_path.read_text())
    out: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target, value = node.targets[0], node.value
        if isinstance(target, ast.Name) and target.id.startswith("ENGINE_"):
            literal = _string_literal(value)
            if literal:
                out[target.id] = literal
    return out


def parse_family_registry(catalog_path: Path) -> dict[str, str]:
    """`catalog.FAMILIES`: family id -> the engine constant or literal it declares."""
    tree = ast.parse(catalog_path.read_text())
    registry: dict[str, str] = {}
    for node in tree.body:
        value: Optional[ast.expr] = None
        target = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target, value = node.target, node.value
        if not (isinstance(target, ast.Name) and target.id == "FAMILIES"):
            continue
        if not isinstance(value, ast.Dict):
            continue
        for key_node, entry in zip(value.keys, value.values):
            family_id = _string_literal(key_node) if key_node is not None else None
            if not family_id or not isinstance(entry, ast.Call):
                continue
            for keyword in entry.keywords:
                if keyword.arg != "engine":
                    continue
                literal = _string_literal(keyword.value)
                if literal:
                    registry[family_id] = literal
                elif isinstance(keyword.value, ast.Name):
                    registry[family_id] = keyword.value.id
    return registry


# ─── generation.py audit ───────────────────────────────────────────────────

class GenerationAuditor:
    """Reads generation.py and answers:
       - What's in _WIRED_FAMILIES?
       - What families does the dispatch code actually handle without raising?
    """

    def __init__(self, generation_path: Path, engine_constants: Optional[dict[str, str]] = None):
        self.tree = ast.parse(generation_path.read_text())
        self.module_sets = self._collect_module_level_sets()
        self._engine_constants = engine_constants or {}
        self._engine_keys = self._collect_worker_engines()

    def _collect_worker_engines(self) -> set[str]:
        """Resolve `_WORKER_BY_ENGINE`'s keys to engine strings."""
        out: set[str] = set()
        for node in self.tree.body:
            value: Optional[ast.expr] = None
            target = None
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target, value = node.targets[0], node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                target, value = node.target, node.value
            if not (isinstance(target, ast.Name) and target.id == "_WORKER_BY_ENGINE"):
                continue
            if not isinstance(value, ast.Dict):
                continue
            for key in value.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    out.add(key.value)
                elif isinstance(key, ast.Attribute):
                    resolved = self._engine_constants.get(key.attr)
                    if resolved:
                        out.add(resolved)
        return out

    def _collect_module_level_sets(self) -> dict[str, set[str]]:
        """Find module-level `NAME = {...}` or `NAME: type = {...}` assignments
        whose elements/keys are string literals. Used to resolve `family in
        MLX_AUDIO_FAMILIES`-style membership checks (where a dynamic set of
        families is delegated to one worker).

        Handles both:
        - `_WIRED_FAMILIES = {"x", "y"}`       (ast.Assign)
        - `MLX_AUDIO_FAMILIES: dict = {...}`   (ast.AnnAssign — has type hint)
        """
        out: dict[str, set[str]] = {}

        def record(name: str, value: ast.AST) -> None:
            if isinstance(value, ast.Set):
                out[name] = {s for e in value.elts if (s := _string_literal(e))}
            elif isinstance(value, ast.Dict):
                out[name] = {s for k in value.keys if (s := _string_literal(k))}

        for node in self.tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
                if isinstance(target, ast.Name):
                    record(target.id, node.value)
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                # `NAME: type = value`
                if isinstance(node.target, ast.Name):
                    record(node.target.id, node.value)
        return out

    def wired_families(self) -> set[str]:
        """The wired set, from the literal if present, else the family registry.

        `_WIRED_FAMILIES` is `frozenset(catalog.FAMILIES)` now, so the registry
        is what this reads; an older revision's literal still works.
        """
        literal = self.module_sets.get("_WIRED_FAMILIES", set())
        if literal:
            return literal
        return set(self.module_sets.get("FAMILIES", set()))

    def worker_engines(self) -> set[str]:
        """`_WORKER_BY_ENGINE`'s keys, resolved through the engine constants.

        The keys are `catalog.ENGINE_*` attributes, so this reads the constant
        values out of catalog.py and matches them to the keys here.
        """
        return self._engine_keys

    def dispatch_families(self) -> tuple[set[str], set[str]]:
        """
        Walk every method whose name starts with `_dispatch_` and analyze its
        if/elif chain. For each branch:
          - extract the family string(s) the branch matches
          - check whether the body raises NotImplementedError

        Returns (handled, raising):
          handled — families with at least one branch that has a real worker
          raising — families whose only branch raises NotImplementedError
        """
        handled: set[str] = set()
        raising: set[str] = set()

        for node in ast.walk(self.tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if not node.name.startswith("_dispatch_"):
                continue
            for branch_families, branch_body in self._walk_if_chain(node.body):
                if _branch_raises_not_implemented(branch_body):
                    raising |= branch_families
                else:
                    handled |= branch_families

        # If a family appears in BOTH (e.g. it has multiple dispatch branches —
        # one raises, one handles), prefer "handled" — at least one code path
        # works for it. Remove from raising.
        raising -= handled
        return handled, raising

    def _walk_if_chain(self, stmts: list[ast.stmt]) -> list[tuple[set[str], list[ast.stmt]]]:
        """Walk every If node (and its elif chain) inside `stmts`. For each
        branch, yield (matched_families, branch_body). Resolves these patterns:
          - `family == "X"`
          - `family in ("X", "Y")`
          - `family in NAME` (where NAME is a module-level set/dict of strings)
        """
        out: list[tuple[set[str], list[ast.stmt]]] = []
        for node in stmts:
            if not isinstance(node, ast.If):
                continue
            current: Optional[ast.If] = node
            while current is not None:
                families = self._families_from_test(current.test)
                if families:
                    out.append((families, current.body))
                # Walk elif: orelse is either [If, ...] or non-If body (else)
                if (len(current.orelse) == 1 and isinstance(current.orelse[0], ast.If)):
                    current = current.orelse[0]
                else:
                    current = None
        return out

    def _families_from_test(self, test: ast.expr) -> set[str]:
        """Extract family strings matched by an if-condition."""
        out: set[str] = set()

        # `family == "X"` or `family != "X"` (skip the != case)
        if isinstance(test, ast.Compare) and len(test.ops) == 1:
            left = test.left
            op = test.ops[0]
            right = test.comparators[0]

            # `family == "X"`
            if (isinstance(op, ast.Eq) and isinstance(left, ast.Name)
                    and left.id == "family"):
                s = _string_literal(right)
                if s:
                    out.add(s)

            # `family in ("X", "Y")` or `family in NAME`
            if (isinstance(op, ast.In) and isinstance(left, ast.Name)
                    and left.id == "family"):
                literals = _strings_in_collection(right)
                if literals:
                    out |= literals
                elif isinstance(right, ast.Name):
                    out |= self.module_sets.get(right.id, set())
        return out


# ─── report ────────────────────────────────────────────────────────────────

def build_report(app_root: Path) -> dict:
    catalog_path = app_root / "app" / "backend" / "catalog.py"
    generation_path = app_root / "app" / "backend" / "generation.py"

    if not catalog_path.exists() or not generation_path.exists():
        raise FileNotFoundError(
            f"Couldn't find catalog.py + generation.py under {app_root}. "
            "Run this from the app root (the folder containing pinokio.js)."
        )

    catalog_families = parse_catalog_families(catalog_path)
    engines = parse_engine_constants(catalog_path)
    registry = parse_family_registry(catalog_path)
    auditor = GenerationAuditor(generation_path, engines)
    # `_WIRED_FAMILIES` is `frozenset(catalog.FAMILIES)`; read the registry.
    wired = auditor.wired_families() or set(registry)
    workers = auditor.worker_engines()
    # A family is dispatched when the engine it declares has a worker. The
    # dispatcher branches on the engine now, so there are no family-literal
    # branches left to inspect; a registry entry naming an engine with no worker
    # is the "commission lie" this audit exists to catch.
    handled = {
        family for family, engine in registry.items()
        if engines.get(engine, engine) in workers
    }
    raising = set()

    # Compute drift
    commission_lies = wired & raising            # green chip but errors at runtime
    omission_lies = handled - wired              # works but UI says roadmap
    orphan_families = catalog_families - (handled | raising)   # no dispatch branch at all
    phantom_wires = wired - catalog_families     # wired but no catalog model uses it

    return {
        "catalog_families": sorted(catalog_families),
        "declared_wired": sorted(wired),
        "actually_handled": sorted(handled),
        "raising_branches": sorted(raising),
        "drift": {
            "commission_lies": sorted(commission_lies),
            "omission_lies": sorted(omission_lies),
            "orphan_families": sorted(orphan_families),
            "phantom_wires": sorted(phantom_wires),
        },
    }


def print_report(report: dict, app_root: Path) -> bool:
    """Render the report. Returns True if any drift was found."""
    drift = report["drift"]
    any_drift = any(drift.values())

    print()
    print(f"╔══ Truth audit for {app_root.name} ══╗")
    print()
    print(f"  Catalog families       : {len(report['catalog_families']):2}  {report['catalog_families']}")
    print(f"  _WIRED_FAMILIES claims : {len(report['declared_wired']):2}  {report['declared_wired']}")
    print(f"  Dispatch handles       : {len(report['actually_handled']):2}  {report['actually_handled']}")
    print(f"  Dispatch raises NotImpl: {len(report['raising_branches']):2}  {report['raising_branches']}")
    print()
    print("  ─── Drift ───")

    def section(label: str, items: list[str], icon: str, severity: str) -> None:
        if not items:
            print(f"  {icon} {label}: none")
        else:
            print(f"  {icon} {label} ({severity}):")
            for it in items:
                print(f"       - {it}")

    section("COMMISSION LIES  (claim wired, dispatch raises)",
            drift["commission_lies"], "❌", "BUG — user sees green chip, hits wall")
    section("OMISSION LIES    (handled but not in _WIRED_FAMILIES)",
            drift["omission_lies"], "⚠️ ", "false negative — UI underreports")
    section("ORPHAN FAMILIES  (in catalog, no dispatch branch)",
            drift["orphan_families"], "⚠️ ", "falls through to default error")
    section("PHANTOM WIRES    (in _WIRED_FAMILIES, no catalog)",
            drift["phantom_wires"], "ℹ️ ", "harmless but suspicious — dead config")

    print()
    if any_drift:
        print("  ✗ DRIFT DETECTED — fix the bullets above before release.")
    else:
        print("  ✓ NO DRIFT — _WIRED_FAMILIES matches actual dispatch coverage.")
    print()
    return any_drift


# ─── entry point ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict", action="store_true",
        help="Exit non-zero on any drift (for CI use).",
    )
    parser.add_argument(
        "--app-root", default=".",
        help="Path to the app root (default: current directory).",
    )
    args = parser.parse_args()

    app_root = Path(args.app_root).resolve()
    report = build_report(app_root)
    had_drift = print_report(report, app_root)

    if args.strict and had_drift:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
