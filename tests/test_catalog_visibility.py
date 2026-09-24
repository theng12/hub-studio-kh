"""Every shipped model package, offered - the systematic "does every model show" check.

The owner asked for this sweep by name after khmer-tts did not appear in the Audio picker:

    "help me check why khmer-tts isn't available or shown or copied across the nodes today…
     so test all models to see if they show."

The answer at the time was a hardcoded family allowlist in the app that hid six of the thirteen
voice packages. The lesson recorded in `AGENTS.md` is that the *membership* check would have missed
it — a test asserting "the picker equals Qwen and VoxCPM2" was green while the bug was live. So this
sweep is written as an invariant over the catalog itself, and it is deliberately blind to names:

* every manifest in `model-manifests/` reaches the package route as exactly one row;
* a package some other manifest declares as its companion is typed a **dependency** and is not
  offered to a person as a model;
* every other package is offered, names its repo, and is routed to a studio — the three facts the
  app's picker needs before it can list it.

Adding a model is covered by existing: the new manifest is read from the directory, and the only
way to fail is for it to be missing from what the Hub publishes.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

WORKTREE = Path(__file__).resolve().parents[1]
CATALOG = WORKTREE / "model-manifests"

if str(WORKTREE) not in sys.path:
    sys.path.insert(0, str(WORKTREE))

from components import model_packages  # noqa: E402
from components.hub.backend.vocabulary import WORKER_FOR_MODALITY  # noqa: E402


def _runtime() -> str:
    candidates = [
        os.environ.get("STUDIOHUB_TEST_RUNTIME"),
        str(WORKTREE / ".build/voice-runtime/bin/python3"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    return sys.executable


CHILD_PUBLISHED_ROWS = r'''
"""What the packaged Hub publishes for every manifest, from a library with nothing installed."""
import json

from components.hub import standalone
from components.hub.backend.monitor import StudioMonitor

catalog = standalone._model_catalog()
monitor = StudioMonitor()
rows = {manifest["package_id"]: standalone._model_row(manifest, catalog, monitor)
        for manifest in catalog.values()}
print("RESULT:" + json.dumps({
    "rows": {package_id: {
        "type": row.get("type"),
        "model_id": row.get("model_id"),
        "engine": row.get("engine"),
        "worker_id": row.get("worker_id"),
        "dependencies": row.get("dependencies"),
    } for package_id, row in rows.items()},
}))
'''


def published_rows() -> dict[str, dict]:
    """The package route's rows, read the way the app reads them: from the real Hub code."""
    with tempfile.TemporaryDirectory(prefix="studiohub-visibility-") as root:
        root_path = Path(root)
        library = root_path / "library"
        library.mkdir()
        workers = root_path / "private-workers.json"
        workers.write_text(json.dumps([{
            "id": "voice", "title": "Voice Studio", "modality": "voice",
            "host": "127.0.0.1", "port": 51102, "machine": "local", "local": True,
            "hub_token": "fixture-token",
            "runtime_identity": {"id": "r", "compatible": True},
            "managed_output_roots": [],
        }]))
        os.chmod(workers, 0o600)
        release = root_path / "release.json"
        release.write_text(json.dumps({"version": "0.0.0", "components": {}}))
        env = os.environ.copy()
        env.update({
            "STUDIOHUB_DATA_DIR": str(root_path / "state"),
            "STUDIOHUB_WORKERS_FILE": str(workers),
            "STUDIOHUB_MODELS_DIR": str(library),
            "STUDIOHUB_PACKAGED": "1",
            "STUDIOHUB_RELEASE_MANIFEST": str(release),
            "PYTHONPATH": os.pathsep.join(
                [str(WORKTREE), os.environ.get("PYTHONPATH", "")]).rstrip(os.pathsep),
        })
        completed = subprocess.run(
            [_runtime(), "-c", textwrap.dedent(CHILD_PUBLISHED_ROWS)],
            cwd=WORKTREE, env=env, text=True, capture_output=True, check=False,
        )
    if completed.returncode != 0:
        raise AssertionError(
            "isolated visibility fixture failed:\nstdout:\n%s\nstderr:\n%s"
            % (completed.stdout, completed.stderr))
    line = next((row for row in reversed(completed.stdout.splitlines())
                 if row.startswith("RESULT:")), None)
    if line is None:
        raise AssertionError(completed.stdout)
    return json.loads(line[len("RESULT:"):])["rows"]


class EveryShippedModelIsOfferedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifests = {path.stem: json.loads(path.read_text(encoding="utf-8"))
                         for path in sorted(CATALOG.glob("*.json"))}
        # Derived from the manifests, so the set of "not a model" packages cannot go stale.
        cls.companions = {dependency for manifest in cls.manifests.values()
                          for dependency in model_packages.required_packages(manifest)}
        cls.rows = published_rows()

    def test_every_manifest_reaches_the_route_as_exactly_one_row(self) -> None:
        self.assertEqual(
            set(self.rows), set(self.manifests),
            "the package route and the manifest directory disagree; a manifest the route does "
            "not publish is a model no picker can ever show")

    def test_exactly_the_declared_companions_are_withheld(self) -> None:
        """Both directions: nothing needed is offered, and nothing offered is needed."""
        self.assertTrue(self.companions, "no shipped manifest declares a companion at all")
        withheld = {package_id for package_id, row in self.rows.items()
                    if row["type"] == "dependency"}
        self.assertEqual(
            withheld, self.companions,
            "the packages typed as dependencies are not the packages the manifests declare; a "
            "package in the left-hand set is hidden from every picker, and one in the "
            "right-hand set can be chosen as if it were a model")

    def test_every_other_package_is_offered_to_a_studio(self) -> None:
        studios = set(WORKER_FOR_MODALITY.values())
        offered = [package_id for package_id in sorted(self.manifests)
                   if package_id not in self.companions]
        self.assertTrue(offered, "the catalog offers no model at all")
        for package_id in offered:
            with self.subTest(package=package_id):
                row = self.rows[package_id]
                self.assertEqual(
                    row["type"], "model",
                    f"{package_id} is nobody's companion but is withheld from the pickers")
                self.assertTrue(row["model_id"],
                                f"{package_id} publishes no model id, so no picker can select it")
                self.assertEqual(
                    row["engine"], self.manifests[package_id]["engine"],
                    f"{package_id} is published under a different engine from its manifest, so "
                    f"the app routes it to the wrong studio")
                self.assertIn(
                    row["worker_id"], studios,
                    f"{package_id} is offered but not routed to a studio the Hub knows, so no "
                    f"page will list it")


# The largest unified-memory tier in the owner's fleet, from the table in
# `research/owner-hardware.md`. An external fact about his machines, not a model list: if he adds
# a bigger Mac this constant moves with the document, and if a model is published above it the
# failure below says why that matters.
LARGEST_FLEET_TIER_GB = 32


class EveryShippedModelCanBeCarriedTests(unittest.TestCase):
    """The other half of "does every model show": can a kit carry it at all?

    A model whose published floor is above the largest machine in the fleet is never selected by
    `qualification/copy_models_for_this_mac.py`, which picks by the manifest's **own**
    `minimum_unified_memory_gb`. It would then be absent from every drive, and the only way to get
    it would be a download on a Mac that cannot run it. Nothing else in the suite notices, because
    every Hub-side check passes: the package is published, the picker lists it, and the model is
    simply never there.
    """

    def setUp(self) -> None:
        self.manifests = {path.stem: json.loads(path.read_text(encoding="utf-8"))
                          for path in sorted(CATALOG.glob("*.json"))}

    def test_no_model_is_published_above_the_largest_fleet_machine(self) -> None:
        stranded = []
        for package_id, manifest in sorted(self.manifests.items()):
            floor = (manifest.get("qualification") or {}).get("minimum_unified_memory_gb")
            if isinstance(floor, int) and floor > LARGEST_FLEET_TIER_GB:
                stranded.append(f"{package_id} claims {floor} GB")
        self.assertEqual(
            stranded, [],
            "these packages can never be carried by a kit, because no machine in the fleet meets "
            "the floor their own manifest publishes:\n  " + "\n  ".join(stranded))

    def test_the_smallest_tier_still_has_models_to_run(self) -> None:
        """An 8 GB Mac must not be a fleet member with an empty catalog."""
        smallest = [package_id for package_id, manifest in self.manifests.items()
                    if ((manifest.get("qualification") or {}).get("minimum_unified_memory_gb")
                        or 0) <= 8]
        self.assertGreaterEqual(
            len(smallest), 5,
            f"only {len(smallest)} packages are within the smallest fleet tier; a Mac at the "
            f"bottom of the fleet would have almost nothing to run: {sorted(smallest)}")

    def test_every_companion_is_within_reach_of_the_model_that_needs_it(self) -> None:
        """A companion the copy script will not carry is the offline failure it exists to prevent.

        `copy_models_for_this_mac.py` carries a declared companion even when the companion would
        not have been picked on its own — but only if the companion is in the catalog it was given.
        """
        for package_id, manifest in sorted(self.manifests.items()):
            for dependency in model_packages.required_packages(manifest):
                with self.subTest(package=package_id, companion=dependency):
                    self.assertIn(
                        dependency, self.manifests,
                        f"{package_id} requires {dependency!r}, which is not in the catalog, so a "
                        f"kit copying {package_id} cannot copy what it needs")


if __name__ == "__main__":
    unittest.main()
