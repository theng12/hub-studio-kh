"""Node-side image-generation developer test runner (studio.image-generation-test.v1).

This module is the packaged, reusable runner the Hub's Developer Mode ledger calls. It owns:

* the immutable runner/fixture/safety-policy identities (raw-byte SHA-256);
* exact Unicode prompt and ordered reference construction from a packaged fixture set;
* an isolated child process that runs the shipped ``GenerationManager`` in the shipped Image
  runtime (the same mechanism ``qualification/image_edit_probe.py`` integrates with);
* preflight/start-memory admission, sampled owned-process-tree RSS and system swap, monotonic
  workflow and inference clocks, and the request's runtime/RSS/swap budgets;
* exact owned-process identity (pid + process create time + process group), bounded stop proof,
  raw redacted log retention, and a receipt in the frozen ``studio.developer-test.v1`` shape.

It deliberately does **not** own the persisted setting/ledger, HTTP routes, production admission
or model installation. The parent passes an admission object and an installer callback; this
module never invents either. No wire field is added here.

Public API (small, stable):
    runner_identity() -> dict
    FixtureLibrary, SafetyPolicy, load_stop_proof
    build_model_capabilities(...), capabilities(...)
    construct_prompt(...), construct_input_refs(...), validate_pinned_request(...)
    ImageTestRunner(...).run_attempt(...), .stop_recorded(...), .read_log_chunk(...)
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import re
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

RUNNER_ID = "studio.image-generation-test.v1"
RUNNER_REVISION = "studio.image-generation-test.v1+2026-11-06.1"
KINDS = ("reference_counts", "prompt_length", "low_ram_mode", "memory_pressure", "new_model")
CHECKS = (
    "prompt_digest",
    "fixture_identity",
    "start_memory_floor",
    "runtime_budget",
    "rss_budget",
    "swap_budget",
    "output_decoded_dimensions",
    "resource_release",
)
REQUEST_BYTES_CEILING = 2 * 1024 * 1024
RESPONSE_BYTES_CEILING = 2 * 1024 * 1024
LOG_TOTAL_BYTES = 64 * 1024
LOG_CHUNK_BYTES = 16 * 1024
MAX_INPUT_REFS = 16
MAX_INT = 9007199254740991
ASSETS_DIR = Path(__file__).with_name("developer-test-assets")
SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
REASON_CODES = {
    "disabled", "paused", "production_busy", "offline_before_submit", "unknown_acceptance",
    "release_unknown", "cancel_requested", "owner_cancelled", "production_arrived",
    "authority_lost", "unsupported", "invalid_request", "unknown_key", "unknown_node",
    "unauthorized", "conflict", "stale_setting", "model_unavailable", "model_install_failed",
    "engine_refused", "not_enough_disk", "memory_unavailable", "metric_unavailable",
    "start_memory_below_floor", "runtime_budget", "rss_budget", "swap_budget", "oom",
    "worker_lost", "restarted", "output_invalid", "incomplete_evidence", "integrity_error",
    "transport_error", "internal_error",
}

REQUEST_KEYS = {
    "format", "run_id", "test_id", "runner_id", "model", "kind", "settings", "input_refs",
    "requested_limits", "runner_revision", "runner_sha256", "fixture_set_sha256",
    "node_setting_revision",
}
SETTINGS_KEYS = {
    "width_px", "height_px", "steps", "guidance", "seed", "memory_mode", "reference_count",
    "prompt_characters", "fixture_set_id", "prompt_sha256",
}
LIMIT_KEYS = {
    "max_runtime_seconds", "max_process_tree_rss_bytes", "max_system_swap_growth_bytes",
    "test_start_min_free_bytes",
}
HARDWARE_FIELDS = (
    "chip_family", "chip_model", "ram_bytes", "os_version", "os_build",
)
MEASUREMENT_FIELDS = (
    "elapsed_seconds", "generation_seconds", "peak_process_memory_bytes",
    "system_swap_baseline_bytes", "system_swap_peak_bytes", "swap_growth_bytes",
)


class RunnerRefusal(Exception):
    """A refusal in the contract's reason vocabulary; the parent maps it to HTTP."""

    def __init__(self, code: str, detail: str, *, status: int = 400):
        if code not in REASON_CODES:
            code = "internal_error"
        super().__init__(detail)
        self.code = code
        self.detail = str(detail)[:4096]
        self.status = status

    def reason(self) -> dict[str, str]:
        return {"code": self.code, "detail": self.detail}


def _admission_code(code: str | None) -> str:
    """Map developer_test_admission's refusal vocabulary onto the frozen reason codes."""
    return {"paused": "paused", "unverified": "release_unknown",
            "resource_unknown": "memory_unavailable", "production_busy": "production_busy",
            "busy": "production_busy", "invalid": "invalid_request"}.get(
                str(code or ""), "release_unknown")


class MetricUnavailable(Exception):
    """The named measurement cannot be read; never converted to zero."""


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _utcnow() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def _no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str):
    raise ValueError(f"non-finite JSON number: {value}")


def _load_json(raw: bytes) -> Any:
    return json.loads(raw, object_pairs_hook=_no_duplicates, parse_constant=_reject_constant)


def _read_json(path: Path) -> Any:
    return _load_json(Path(path).read_bytes())


def _atomic_write(path: Path, raw: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(raw)
    os.replace(tmp, path)


def _private_dir(path: Path) -> Path:
    path = Path(path)
    if path.is_symlink():
        raise RunnerRefusal("integrity_error", f"private directory is a symlink: {path}")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def _require(condition: bool, detail: str, code: str = "invalid_request") -> None:
    if not condition:
        raise RunnerRefusal(code, detail)


def _is_int(value: Any) -> bool:
    return type(value) is int and 0 <= value <= MAX_INT


def _is_number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= MAX_INT


def _segment(value: Any, name: str, *, maximum: int = 128) -> str:
    _require(isinstance(value, str) and len(value) <= maximum
             and SAFE_SEGMENT.fullmatch(value) is not None, f"{name} is not a safe segment")
    return value


def runner_identity() -> dict[str, str]:
    """The exact packaged runner identity: revision plus SHA-256 of this module's raw bytes."""
    return {"runner_revision": RUNNER_REVISION,
            "runner_sha256": _sha256(Path(__file__).read_bytes())}


# ── packaged fixtures and safety policy ───────────────────────────────────────

@dataclass(frozen=True)
class _Fixture:
    fixture_id: str
    file: str
    sha256: str
    size_bytes: int
    width_px: int
    height_px: int
    media_type: str

    def wire(self) -> dict:
        return {"fixture_id": self.fixture_id, "sha256": self.sha256,
                "size_bytes": self.size_bytes, "width_px": self.width_px,
                "height_px": self.height_px, "media_type": self.media_type}


class FixtureLibrary:
    """One or more packaged fixture sets, verified byte-for-byte from their raw manifest."""

    def __init__(self, directory: Path = ASSETS_DIR):
        self.directory = Path(directory)
        raw = (self.directory / "fixtures" / "manifest.json").read_bytes()
        self.sha256 = _sha256(raw)
        data = _load_json(raw)
        _require(isinstance(data, dict), "fixture manifest is not an object", "integrity_error")
        self._sets: dict[str, dict] = {}
        self._fixtures: dict[str, _Fixture] = {}
        self._paths: dict[str, Path] = {}
        entries = data.get("fixtures")
        _require(isinstance(entries, list) and entries, "fixture manifest has no fixtures",
                 "integrity_error")
        for entry in entries:
            _require(isinstance(entry, dict), "fixture entry is not an object", "integrity_error")
            fixture = _Fixture(
                fixture_id=_segment(entry.get("fixture_id"), "fixture_id"),
                file=str(entry.get("file") or ""),
                sha256=str(entry.get("sha256") or ""),
                size_bytes=entry.get("size_bytes"),
                width_px=entry.get("width_px"),
                height_px=entry.get("height_px"),
                media_type=str(entry.get("media_type") or ""),
            )
            _require(SHA256_HEX.fullmatch(fixture.sha256) is not None,
                     f"fixture {fixture.fixture_id} has no valid sha256", "integrity_error")
            _require(_is_int(fixture.size_bytes) and fixture.size_bytes > 0
                     and _is_int(fixture.width_px) and fixture.width_px > 0
                     and _is_int(fixture.height_px) and fixture.height_px > 0,
                     f"fixture {fixture.fixture_id} has invalid size/dimensions",
                     "integrity_error")
            _require(fixture.media_type in ("image/png", "image/jpeg"),
                     f"fixture {fixture.fixture_id} has an unsupported media type",
                     "integrity_error")
            path = self.directory / "fixtures" / fixture.file
            body = path.read_bytes()
            _require(len(body) == fixture.size_bytes and _sha256(body) == fixture.sha256,
                     f"fixture {fixture.fixture_id} bytes do not match the packaged digest",
                     "integrity_error")
            self._fixtures[fixture.fixture_id] = fixture
            self._paths[fixture.fixture_id] = path
        set_id = _segment(data.get("id"), "fixture set id")
        order = data.get("reference_order")
        _require(isinstance(order, list) and order and all(
            isinstance(item, str) and item in self._fixtures for item in order),
            "fixture reference_order is empty or names an unknown fixture", "integrity_error")
        pattern = data.get("prompt_pattern")
        _require(isinstance(pattern, str) and 1 <= len(pattern) <= 65536,
                 "fixture prompt_pattern is invalid", "integrity_error")
        _require(data.get("prompt_construction") == "repeat_pattern_then_codepoint_prefix_v1",
                 "fixture prompt_construction is not the frozen construction", "integrity_error")
        wire = {
            "id": set_id,
            "sha256": self.sha256,
            "fixtures": [self._fixtures[item].wire() for item in
                         sorted({item for item in order})],
            "reference_order": list(order),
            "prompt_construction": "repeat_pattern_then_codepoint_prefix_v1",
            "prompt_pattern": pattern,
        }
        self._sets[set_id] = wire
        self._pattern = pattern

    def wire(self) -> list[dict]:
        return [dict(value) for value in self._sets.values()]

    def set(self, set_id: str) -> dict:
        value = self._sets.get(set_id)
        _require(value is not None, f"unknown fixture set: {set_id}")
        return value

    def path(self, fixture_id: str) -> Path:
        value = self._paths.get(fixture_id)
        _require(value is not None, f"unknown fixture: {fixture_id}")
        return value

    def fixture(self, fixture_id: str) -> _Fixture:
        value = self._fixtures.get(fixture_id)
        _require(value is not None, f"unknown fixture: {fixture_id}")
        return value


class SafetyPolicy:
    """The packaged operator/test budget, identified by its retained raw bytes."""

    def __init__(self, directory: Path = ASSETS_DIR):
        self.directory = Path(directory)
        raw = (self.directory / "safety-policy.json").read_bytes()
        self.sha256 = _sha256(raw)
        data = _load_json(raw)
        _require(isinstance(data, dict) and isinstance(data.get("revision"), str)
                 and data["revision"], "safety policy has no revision", "integrity_error")
        limits = data.get("requested_limits")
        _require(isinstance(limits, dict) and set(limits) == LIMIT_KEYS,
                 "safety policy requested_limits are not the frozen four", "integrity_error")
        for name, value in limits.items():
            _require(isinstance(value, dict)
                     and set(value) == {"minimum", "maximum", "unit", "source", "revision",
                                        "provenance", "enforcement"},
                     f"safety policy range {name} is not the frozen shape", "integrity_error")
            _require(_is_number(value["minimum"]) and _is_number(value["maximum"])
                     and value["minimum"] <= value["maximum"],
                     f"safety policy range {name} is invalid", "integrity_error")
            _require(value["provenance"] == "packaged_safety_policy"
                     and value["enforcement"] in ("admission", "sampled", "deadline", "validation"),
                     f"safety policy range {name} has invalid provenance/enforcement",
                     "integrity_error")
        self.revision = data["revision"]
        self._limits = limits
        self._wire = {"revision": self.revision, "sha256": self.sha256,
                      "requested_limits": limits}

    def wire(self) -> dict:
        return json.loads(json.dumps(self._wire))

    def range(self, name: str) -> dict:
        value = self._limits.get(name)
        _require(value is not None, f"unknown safety policy range: {name}", "integrity_error")
        return value


def load_stop_proof(directory: Path = ASSETS_DIR) -> dict | None:
    """The packaged positive stop proof, or None when no bounded release is proved.

    A proof is only accepted when its own ``proof_sha256`` is the digest of the raw proof file
    and it states a positive latency. An absent, malformed or unverified proof is None, never a
    fabricated zero.
    """
    path = Path(directory) / "stop-proof.json"
    try:
        raw = path.read_bytes()
        data = _load_json(raw)
        if not isinstance(data, dict) or set(data) != {
                "stop_latency_seconds", "source", "revision", "proof_sha256"}:
            return None
        meta_path = Path(directory) / "stop-proof-meta.json"
        meta = {}
        if meta_path.exists():
            meta = _load_json(meta_path.read_bytes())
            if not isinstance(meta, dict):
                meta = {}
        evidence_file = meta.get("evidence_file", "stop-proof-evidence.json")
        scope = meta.get("scope", "process_tree_stop_primitive")
        evidence = (Path(directory) / str(evidence_file)).read_bytes()
        if (not isinstance(data["stop_latency_seconds"], (int, float))
                or not math.isfinite(data["stop_latency_seconds"])
                or data["stop_latency_seconds"] <= 0):
            return None
        if data["proof_sha256"] != _sha256(evidence):
            return None
        if not isinstance(data["source"], str) or not data["source"]:
            return None
        if not isinstance(data["revision"], str) or not data["revision"]:
            return None
        data["scope"] = scope
        return data
    except (OSError, ValueError, TypeError):
        return None


# ── prompt and reference construction ────────────────────────────────────────

def construct_prompt(pattern: str, characters: int) -> str:
    """Repeat the nonempty pattern, then take exactly ``characters`` Unicode code points.

    No normalization, newline insertion or template expansion happens here. Python string
    slicing counts code points, which is the frozen construction's unit.
    """
    _require(isinstance(pattern, str) and pattern, "prompt_pattern must be nonempty")
    _require(type(characters) is int and 0 <= characters <= MAX_INT,
             "prompt_characters must be a nonnegative integer")
    if characters == 0:
        return ""
    repeats = (characters + len(pattern) - 1) // len(pattern)
    return (pattern * repeats)[:characters]


def prompt_sha256(pattern: str, characters: int) -> str:
    return _sha256(construct_prompt(pattern, characters).encode("utf-8"))


def construct_input_refs(fixtures: FixtureLibrary, fixture_set: dict,
                         reference_count: int) -> list[dict]:
    """The exact first ``reference_count`` ordered references, with packaged digests."""
    _require(type(reference_count) is int and 0 <= reference_count <= MAX_INPUT_REFS,
             f"reference_count must be 0..{MAX_INPUT_REFS}")
    order = fixture_set["reference_order"]
    _require(reference_count <= len(order),
             f"reference_count {reference_count} exceeds the fixture order ({len(order)})")
    refs = []
    for fixture_id in order[:reference_count]:
        fixture = fixtures.fixture(fixture_id)
        refs.append({"fixture_id": fixture.fixture_id, "sha256": fixture.sha256,
                     "size_bytes": fixture.size_bytes})
    return refs


def reference_paths(fixtures: FixtureLibrary, fixture_set: dict,
                    reference_count: int) -> list[str]:
    return [str(fixtures.path(fixture_id))
            for fixture_id in fixture_set["reference_order"][:reference_count]]


# ── model capability projection (dynamic manifests/runtime, no invented defaults) ──

@dataclass(frozen=True)
class ModelCapability:
    wire: dict
    manifest: dict
    repo: str
    label: str
    reference_max: int | None
    prompt_max: int | None
    engine_version: str
    size_presets: dict

    def receipt_model(self) -> dict:
        return {"id": self.wire["model"]["id"], "revision": self.wire["model"]["revision"],
                "package_id": self.wire["package_id"],
                "manifest_sha256": self.wire["manifest_sha256"],
                "engine": self.wire["engine"], "engine_version": self.wire["engine_version"]}


def _engine_version(engine: str) -> str:
    try:
        if engine == "image-mflux":
            return importlib.metadata.version("mflux")
        if engine == "image-mlx-serve":
            return importlib.metadata.version("mlx")
    except Exception:
        pass
    return "unreported"


def _row_value(row: Any, name: str, default: Any = None) -> Any:
    return getattr(row, name, default) if not isinstance(row, dict) else row.get(name, default)


def _size_presets(row: Any) -> dict:
    presets = _row_value(row, "size_presets")
    if presets:
        return presets
    try:
        from components.image.app.backend import generation
        return generation.SIZE_PRESETS
    except Exception:
        return {}


def _settings_bounds(row: Any, *, reference_input_max: int | None = None) -> dict:
    limits = _row_value(row, "limits") or {}
    presets = _size_presets(row) or {}
    widths = [int(value[0]) for value in presets.values() if isinstance(value, (tuple, list))]
    heights = [int(value[1]) for value in presets.values() if isinstance(value, (tuple, list))]
    widths = widths or [1]
    heights = heights or [1]
    reference_limits = _row_value(row, "reference_limits") or {}
    # This is the *test input* range, not the production edit cap: zero references is the
    # txt2img vector, and a test may not exceed the packaged fixture order or the engine's
    # intrinsic cap (the row's own `max`). Production caps are left untouched.
    ref_min = 0
    ref_max = int(reference_limits.get("max", 0) or 0) if reference_limits else 0
    if reference_input_max is not None:
        ref_max = min(ref_max, max(0, int(reference_input_max)))
    ref_max = max(ref_min, ref_max)
    guidance = None
    if "min_guidance" in limits and "max_guidance" in limits:
        guidance = {"minimum": float(limits["min_guidance"]),
                    "maximum": float(limits["max_guidance"])}
    return {
        "width_px": {"minimum": min(widths), "maximum": max(widths)},
        "height_px": {"minimum": min(heights), "maximum": max(heights)},
        "dimension_multiple": int(_row_value(row, "dimension_multiple") or 1),
        "steps": {"minimum": int(limits.get("min_steps", 1)),
                  "maximum": int(limits.get("max_steps", 1))},
        "guidance": guidance,
        "seed": {"minimum": 0, "maximum": int(limits.get("max_seed", 0))},
        "reference_count": {"minimum": ref_min, "maximum": ref_max},
        "prompt_characters": {"minimum": 0, "maximum": int(limits.get("max_prompt", 0))},
    }


def _memory_modes(row: Any) -> list[str]:
    policies = (_row_value(row, "memory"), _row_value(row, "reference_memory"))
    for policy in policies:
        if policy is None:
            continue
        bounded = (policy.get("low_ram_below_bytes")
                   if isinstance(policy, dict)
                   else getattr(policy, "low_ram_below_bytes", None))
        if bounded is not None:
            return ["default", "bounded-staged"]
    return ["default"]


def _manifest_index(manifests_dir: Path) -> dict[str, tuple[dict, str]]:
    index: dict[str, tuple[dict, str]] = {}
    for path in sorted(Path(manifests_dir).glob("*.json")):
        raw = path.read_bytes()
        try:
            manifest = _load_json(raw)
        except (OSError, ValueError):
            continue
        if not isinstance(manifest, dict):
            continue
        if manifest.get("engine") not in ("image-mflux", "image-mlx-serve"):
            continue
        package_id = manifest.get("package_id")
        if isinstance(package_id, str) and package_id:
            index[package_id] = (manifest, _sha256(raw))
    return index


def _default_rows() -> list[Any]:
    from components.image.app.backend import generation
    return list(generation.MODELS.values())


def build_model_capabilities(
        manifests_dir: Path,
        *,
        library: Path | None = None,
        rows: Iterable[Any] | None = None,
        preflight: Callable[[dict], dict] | None = None,
        engine_version: str | None = None,
        total_memory_bytes: int | None = None,
        fixture_reference_max: int | None = None) -> list[ModelCapability]:
    """Project the build's own Image rows/manifests into modelCapability objects.

    ``installed`` is read from the shipped ``model_packages.preflight`` (or the injected
    ``preflight`` callback) against ``library``. Nothing here guesses a default, a limit or a
    version: a value the model/manifest does not state is null or ``unreported``.
    """
    manifests = _manifest_index(Path(manifests_dir))
    source_rows = list(rows) if rows is not None else _default_rows()
    result: list[ModelCapability] = []
    for row in source_rows:
        package_id = _row_value(row, "package_id")
        if not package_id or package_id not in manifests:
            continue
        manifest, manifest_sha = manifests[package_id]
        repo = _row_value(row, "repo")
        revision = _row_value(row, "revision")
        engine = _row_value(row, "engine") or manifest.get("engine") or "image-mflux"
        state = None
        state_reason = None
        if preflight is not None:
            try:
                state = preflight(manifest)
            except Exception as exc:  # noqa: BLE001 - a projection failure is not a pass
                state_reason = str(exc)[:400]
        elif library is not None:
            try:
                from components import model_packages
                state = model_packages.preflight(manifest, Path(library))
            except Exception as exc:  # noqa: BLE001
                state_reason = str(exc)[:400]
        installed = bool(isinstance(state, dict) and state.get("state") == "files_verified")
        pin_matches = (manifest.get("model_id") == repo
                       and manifest.get("revision") == revision)
        installable = pin_matches and manifest.get("engine") in ("image-mflux", "image-mlx-serve")
        reason = None
        if not pin_matches:
            reason = {"code": "model_unavailable",
                      "detail": f"{package_id} manifest is pinned to a different model/revision"}
        elif not installable:
            reason = {"code": "model_unavailable",
                      "detail": f"{package_id} has an unsupported engine"}
        elif not installed and state_reason:
            reason = {"code": "model_unavailable",
                      "detail": f"{package_id} installed state is unknown: {state_reason}"}
        elif not installed and state is None:
            reason = {"code": "model_unavailable",
                      "detail": f"{package_id} installed state is unknown (no library/preflight)"}
        version = (manifest.get("engine_version") or engine_version
                   or _engine_version(str(engine)))
        bounds = _settings_bounds(row, reference_input_max=fixture_reference_max)
        reference_limits = _row_value(row, "reference_limits") or {}
        intrinsic_max = int(reference_limits.get("max", 0) or 0) if reference_limits else 0
        published_max = intrinsic_max
        try:
            from components.image.app.backend import generation
            effective = generation.effective_reference_max(row, total_memory_bytes)
            published_max = effective if effective is not None else intrinsic_max
        except Exception:
            pass
        prompt_max = int((_row_value(row, "limits") or {}).get("max_prompt", 0) or 0)
        wire = {
            "model": {"id": str(repo), "revision": str(revision)},
            "package_id": str(package_id),
            "manifest_sha256": manifest_sha,
            "installed": installed,
            "installable": bool(installable),
            "reason": reason,
            "engine": str(engine),
            "engine_version": str(version),
            "memory_modes": _memory_modes(row),
            "settings_bounds": bounds,
            "intrinsic_reference_max": intrinsic_max if reference_limits else None,
            "intrinsic_reference_source": (
                f"components/image/app/backend/generation.py:{repo}.reference_limits"
                if reference_limits else None),
            "published_reference_max": published_max,
            "published_prompt_max": prompt_max,
            "published_limits_source": (
                f"components/image/app/backend/generation.py:{repo}.limits"),
        }
        result.append(ModelCapability(wire=wire, manifest=manifest, repo=str(repo),
                                      label=str(_row_value(row, "label") or repo),
                                      reference_max=published_max, prompt_max=prompt_max,
                                      engine_version=str(version),
                                      size_presets=dict(_size_presets(row))))
    return result


def capabilities(
        *,
        node_id: str,
        controller_id: str,
        setting: dict,
        manifests_dir: Path,
        library: Path | None = None,
        rows: Iterable[Any] | None = None,
        preflight: Callable[[dict], dict] | None = None,
        assets_dir: Path = ASSETS_DIR,
        server_time: str | None = None,
        engine_version: str | None = None,
        total_memory_bytes: int | None = None,
        models: list[ModelCapability] | None = None,
        runtime_available: bool = True) -> dict:
    """Build the closed capabilities object. ``supported`` is never invented."""
    fixtures = FixtureLibrary(assets_dir)
    policy = SafetyPolicy(assets_dir)
    proof = load_stop_proof(assets_dir)
    projected = models if models is not None else build_model_capabilities(
        manifests_dir, library=library, rows=rows, preflight=preflight,
        engine_version=engine_version, total_memory_bytes=total_memory_bytes,
        fixture_reference_max=max(
            [len(item["reference_order"]) for item in fixtures.wire()] or [0]))
    usable = any(item.wire["installed"] or item.wire["installable"] for item in projected)
    proof_scope = (proof or {}).get("scope")
    proof_wire = None if proof is None else {
        key: value for key, value in proof.items() if key != "scope"}
    supported = bool(proof and proof_scope == "full_runner"
                     and proof["stop_latency_seconds"] > 0 and KINDS and CHECKS
                     and fixtures.wire() and usable and runtime_available)
    reason = None
    if not supported:
        missing = []
        if not runtime_available:
            missing.append("no packaged Image runtime interpreter is configured")
        if not proof:
            missing.append("no verified positive stop proof is packaged")
        elif proof_scope != "full_runner":
            missing.append("the packaged stop proof covers only the process-tree stop primitive, "
                           "not model inference and test-owned installation")
        if not usable:
            missing.append("no usable model is projected")
        if not fixtures.wire():
            missing.append("no fixture set is packaged")
        reason = {"code": "unsupported",
                  "detail": "; ".join(missing) or "the runner is not supported"}
    identity = runner_identity()
    bounds = {
        "request_bytes": REQUEST_BYTES_CEILING,
        "response_bytes": RESPONSE_BYTES_CEILING,
        "log_total_bytes": LOG_TOTAL_BYTES,
        "log_chunk_bytes": LOG_CHUNK_BYTES,
        "input_refs": MAX_INPUT_REFS,
        "fixtures": max(1, sum(len(item["fixtures"]) for item in fixtures.wire())),
        "fixture_sets": max(1, len(fixtures.wire())),
        "models": max(1, len(projected)),
        "prompt_pattern_code_points": max(
            [len(item["prompt_pattern"]) for item in fixtures.wire()] or [1]),
    }
    return {
        "format": "studio.developer-test.capabilities.v1",
        "contract": "studio.developer-test.v1",
        "node_id": _segment(node_id, "node_id"),
        "controller_id": _segment(controller_id, "controller_id"),
        "server_time": server_time or _utcnow(),
        "supported": supported,
        "reason": reason,
        "runner_id": RUNNER_ID,
        "runner_revision": identity["runner_revision"],
        "runner_sha256": identity["runner_sha256"],
        "setting": setting,
        "test_kinds": list(KINDS),
        "checks": list(CHECKS),
        "concurrency": 1,
        "bounds": bounds,
        "safety_policy": policy.wire(),
        "measurement_method": {
            "start_memory": "psutil.virtual_memory().available",
            "start_memory_scope": "host_available_physical_unified_memory",
            "rss": "sampled_owned_process_tree_rss_sum",
            "swap": "sampled_system_swap_used",
            "sample_interval_ms": 250,
            "elapsed_clock": "monotonic",
        },
        "stop_proof": proof_wire,
        "fixture_sets": fixtures.wire(),
        "models": [item.wire for item in projected],
    }


# ── request validation ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class RequestPlan:
    request: dict
    fixture_set: dict
    prompt: str
    prompt_sha256: str
    references: list[tuple[str, Path]]
    model: ModelCapability
    limits: dict
    request_sha256: str


def _validate_settings(settings: dict, bounds: dict, memory_modes: list[str],
                       size_presets: dict) -> None:
    _require(isinstance(settings, dict) and set(settings) == SETTINGS_KEYS,
             "settings do not match the frozen shape")
    for name in ("width_px", "height_px", "steps", "seed", "reference_count",
                 "prompt_characters"):
        _require(_is_int(settings[name]), f"settings.{name} must be a nonnegative integer")
    _require(settings["guidance"] is None or _is_number(settings["guidance"]),
             "settings.guidance must be finite or null")
    _require(settings["memory_mode"] in memory_modes,
             f"memory_mode {settings['memory_mode']!r} is not offered by this model")
    _require(_segment(settings["fixture_set_id"], "settings.fixture_set_id") is not None,
             "settings.fixture_set_id is invalid")
    _require(SHA256_HEX.fullmatch(str(settings["prompt_sha256"])) is not None,
             "settings.prompt_sha256 is invalid")
    for name in ("width_px", "height_px", "steps", "reference_count", "prompt_characters"):
        cell = bounds[name]
        _require(cell["minimum"] <= settings[name] <= cell["maximum"],
                 f"settings.{name} is outside this model's published range")
    multiple = bounds["dimension_multiple"]
    _require(settings["width_px"] % multiple == 0 and settings["height_px"] % multiple == 0,
             f"dimensions must be multiples of {multiple}")
    _require((settings["width_px"], settings["height_px"]) in set(size_presets.values()),
             "dimensions are not a published preset for this model")
    guidance = bounds["guidance"]
    if guidance is None:
        _require(settings["guidance"] is None,
                 "this model publishes no guidance range, so guidance must be null")
    else:
        _require(settings["guidance"] is not None
                 and guidance["minimum"] <= settings["guidance"] <= guidance["maximum"],
                 "settings.guidance is outside this model's published range")
    seed = bounds["seed"]
    _require(seed["minimum"] <= settings["seed"] <= seed["maximum"],
             "settings.seed is outside this model's published range")


def _validate_limits(limits: dict, policy: SafetyPolicy) -> None:
    _require(isinstance(limits, dict) and set(limits) == LIMIT_KEYS,
             "requested_limits do not match the frozen shape")
    for name, value in limits.items():
        _require(_is_int(value), f"requested_limits.{name} must be a nonnegative integer")
        cell = policy.range(name)
        _require(cell["minimum"] <= value <= cell["maximum"],
                 f"requested_limits.{name} is outside the packaged safety policy")


def validate_pinned_request(
        request: Any,
        *,
        fixtures: FixtureLibrary,
        policy: SafetyPolicy,
        runner: dict | None = None,
        models: list[ModelCapability],
        node_setting_revision: str | None = None,
        request_bytes: bytes | None = None) -> RequestPlan:
    """Verify every pin and every packaged input before any child work starts."""
    _require(isinstance(request, dict) and set(request) == REQUEST_KEYS,
             "request does not match the frozen shape")
    _require(request["format"] == "studio.developer-test.request.v1",
             "request format is not studio.developer-test.request.v1")
    _require(request["runner_id"] == RUNNER_ID, "runner_id is not the image runner")
    identity = runner or runner_identity()
    _require(request["runner_revision"] == identity["runner_revision"],
             "runner_revision does not match the packaged runner", "stale_setting")
    _require(request["runner_sha256"] == identity["runner_sha256"],
             "runner_sha256 does not match the packaged runner", "stale_setting")
    _require(request["fixture_set_sha256"] == fixtures.sha256,
             "fixture_set_sha256 does not match the packaged fixture manifest", "stale_setting")
    if node_setting_revision is not None:
        _require(request["node_setting_revision"] == node_setting_revision,
                 "node_setting_revision is stale", "stale_setting")
    _segment(request["run_id"], "run_id")
    _segment(request["test_id"], "test_id")
    _segment(request["node_setting_revision"], "node_setting_revision")
    _require(request["kind"] in KINDS, "kind is not one of the frozen test kinds")
    model_request = request["model"]
    _require(isinstance(model_request, dict) and set(model_request) == {"id", "revision"},
             "model does not match the frozen shape")
    model = next((item for item in models
                  if item.wire["model"]["id"] == model_request["id"]
                  and item.wire["model"]["revision"] == model_request["revision"]), None)
    _require(model is not None, "model id/revision is not a packaged model", "model_unavailable")
    _require(model.wire["installable"] or model.wire["installed"],
             "model is neither installed nor installable", "model_unavailable")
    settings = request["settings"]
    _validate_settings(settings, model.wire["settings_bounds"], model.wire["memory_modes"],
                       model.size_presets)
    fixture_set = fixtures.set(settings["fixture_set_id"])
    prompt = construct_prompt(fixture_set["prompt_pattern"], settings["prompt_characters"])
    digest = _sha256(prompt.encode("utf-8"))
    _require(digest == settings["prompt_sha256"], "prompt_sha256 does not match the construction")
    count = settings["reference_count"]
    expected_refs = construct_input_refs(fixtures, fixture_set, count)
    refs = request["input_refs"]
    _require(isinstance(refs, list) and len(refs) == count,
             "input_refs length must equal settings.reference_count")
    _require(len(refs) <= MAX_INPUT_REFS, f"input_refs exceeds {MAX_INPUT_REFS}")
    for index, (value, expected) in enumerate(zip(refs, expected_refs)):
        _require(isinstance(value, dict)
                 and set(value) == {"fixture_id", "sha256", "size_bytes"},
                 f"input_refs[{index}] does not match the frozen shape")
        _require(value == expected,
                 f"input_refs[{index}] is not the packaged fixture at that position",
                 "integrity_error")
    _validate_limits(request["requested_limits"], policy)
    if request_bytes is not None:
        _require(len(request_bytes) <= REQUEST_BYTES_CEILING, "request exceeds the byte ceiling",
                 "invalid_request")
    return RequestPlan(
        request=request, fixture_set=fixture_set, prompt=prompt, prompt_sha256=digest,
        references=[(item["fixture_id"], fixtures.path(item["fixture_id"]))
                    for item in expected_refs],
        model=model, limits=request["requested_limits"],
        request_sha256=_sha256(request_bytes) if request_bytes is not None else _sha256(
            _json_bytes(request)))


# ── metrics, hardware and exact process identity ─────────────────────────────

class Metrics:
    """Thin adapter over psutil. Every unreadable value raises; it is never zero."""

    def __init__(self, module: Any | None = None):
        self._module = module
        self._import_error: str | None = None
        if module is None:
            try:
                import psutil as module  # type: ignore
                self._module = module
            except Exception as exc:  # noqa: BLE001
                self._import_error = str(exc)[:300]

    def _get(self):
        if self._module is None:
            raise MetricUnavailable(
                f"psutil is unavailable in this process: {self._import_error or 'not importable'}")
        return self._module

    def available_bytes(self) -> int:
        value = int(self._get().virtual_memory().available)
        if value < 0:
            raise MetricUnavailable("psutil reported negative available memory")
        return value

    def total_bytes(self) -> int:
        value = int(self._get().virtual_memory().total)
        if value <= 0:
            raise MetricUnavailable("psutil reported no total memory")
        return value

    def swap_used_bytes(self) -> int:
        value = int(self._get().swap_memory().used)
        if value < 0:
            raise MetricUnavailable("psutil reported negative swap use")
        return value

    def identity(self, pid: int) -> dict | None:
        try:
            process = self._get().Process(pid)
            return {"pid": int(process.pid), "create_time": float(process.create_time())}
        except Exception:
            return None

    def tree(self, pid: int) -> tuple[int, list[dict]]:
        module = self._get()
        try:
            process = module.Process(pid)
            processes = [process] + list(process.children(recursive=True))
        except Exception as exc:  # noqa: BLE001
            raise MetricUnavailable(f"owned process tree is unreadable: {exc}") from exc
        rss = 0
        identities = []
        for item in processes:
            try:
                identities.append({"pid": int(item.pid),
                                   "create_time": float(item.create_time())})
                rss += int(item.memory_info().rss)
            except Exception:
                continue
        return rss, identities

    def alive(self, identity: Mapping[str, Any]) -> bool | None:
        """True alive, False verified gone, None unknown (never treated as release proof)."""
        try:
            module = self._get()
        except MetricUnavailable:
            return None
        try:
            pid = int(identity["pid"])
            create_time = float(identity.get("create_time", 0.0))
        except (KeyError, TypeError, ValueError):
            return None
        if pid <= 1 or not math.isfinite(create_time) or create_time <= 0:
            return None
        try:
            process = module.Process(pid)
            if abs(float(process.create_time()) - create_time) > 0.001:
                return False
            try:
                if process.status() == "zombie":
                    return False
            except module.NoSuchProcess:
                return False
            except Exception:
                pass
            return bool(process.is_running())
        except module.NoSuchProcess:
            return False
        except Exception:
            return None


def _group_alive(pgid: int) -> bool | None:
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return None


def _signal_group(pgid: int, signum: int) -> None:
    try:
        os.killpg(pgid, signum)
    except ProcessLookupError:
        pass
    except PermissionError:
        pass


def _signal_pid(pid: int, signum: int) -> None:
    try:
        os.kill(pid, signum)
    except (ProcessLookupError, PermissionError):
        pass


def _stop_sampled_descendants(checked: list[dict], leader: dict,
                              state: Callable[[dict], bool | None]) -> None:
    """SIGKILL only sampled identities verified alive; never signals the group."""
    for item in checked:
        if item is leader:
            continue
        if state(item) is True:
            _signal_pid(int(item["pid"]), signal.SIGKILL)


def _effective_sample_interval_ms(configured: int, max_runtime: float) -> int:
    """The real interval: never below 250 ms, and small enough for 4000 samples."""
    return max(int(configured), 250, int(math.ceil(max_runtime * 1000 / 4000)))


def _reap_owned(pid: int) -> None:
    """Reap this process's own child so its zombie cannot keep the group visible."""
    try:
        os.waitpid(pid, os.WNOHANG)
    except (ChildProcessError, OSError):
        pass


class _BoundedLogSink:
    """Redact as bytes arrive and retain only a bounded redacted prefix.

    The child's stdout is drained continuously, so a chatty child cannot fill a pipe or grow an
    unbounded file; bytes past ``LOG_TOTAL_BYTES`` are discarded and only ``truncated`` records
    that. A held suffix keeps a redaction match that spans two reads from being split: only text
    that cannot be the start of a secret is emitted, and a complete match is never cut in half.
    """

    def __init__(self, secrets: Iterable[bytes], limit: int = LOG_TOTAL_BYTES):
        self._secrets = [value for value in secrets if value]
        self._secrets.sort(key=len, reverse=True)
        self._max_secret = max((len(value) for value in self._secrets), default=0)
        self._pending = b""
        self._body = bytearray()
        self._limit = limit
        self.truncated = False

    def feed(self, chunk: bytes) -> None:
        data = self._pending + chunk
        hold = 0
        for size in range(min(len(data), self._max_secret), 0, -1):
            suffix = data[-size:]
            if any(secret.startswith(suffix) for secret in self._secrets):
                hold = size
                break
        self._pending = data[len(data) - hold:] if hold else b""
        self._append(data[:len(data) - hold])

    def finish(self) -> None:
        pending, self._pending = self._pending, b""
        self._append(pending)

    def _append(self, data: bytes) -> None:
        for value in self._secrets:
            data = data.replace(value, b"<redacted>")
        room = self._limit - len(self._body)
        if len(data) > room:
            self.truncated = True
            data = data[:max(0, room)]
        self._body.extend(data)

    def body(self) -> bytes:
        return bytes(self._body)


def _drain_child_log(pipe: Any, sink: _BoundedLogSink) -> None:
    try:
        while True:
            chunk = pipe.read(4096)
            if not chunk:
                break
            sink.feed(chunk)
    except Exception:  # noqa: BLE001 - a closed pipe ends the drain, not the attempt
        sink.truncated = True  # coverage is incomplete, so the descriptor says so
    finally:
        sink.finish()
        try:
            pipe.close()
        except Exception:  # noqa: BLE001
            pass


def stop_exact_identity(identity: dict | None, *, metrics: Metrics,
                        clock: Callable[[], float], grace: float,
                        known_pids: Iterable[dict] = (),
                        reap: Callable[[], None] | None = None) -> ReleaseProof:
    """The one stop primitive: exact pid+create_time leader, its process group, its descendants.

    A guessed or reused pid is never signalled: an identity without a verified positive
    create_time sends no signal and never confirms release. Once the leader is verified gone,
    its pgid may be reused, so only sampled descendants still verified alive are signalled
    individually. Release is confirmed only when the leader, the process group and every
    sampled descendant are verified gone. An exited but
    unreaped child is collected before each check, because macOS reports its zombie pid as
    unaddressable (getpgid ESRCH) and its group as unreadable (killpg EPERM). A caller that owns
    a Popen passes ``reap`` so its exit status survives; the default uses WNOHANG.
    """
    started = clock()
    checked = [dict(item) for item in known_pids]
    if not identity:
        return ReleaseProof(False, None, {
            "code": "release_unknown",
            "detail": "no owned-process identity was recorded; no signal was sent"}, 0.0, checked)
    try:
        pid = int(identity.get("pid", -1))
        pgid = int(identity.get("pgid", pid))
        create_time = float(identity.get("create_time", 0.0))
    except (TypeError, ValueError):
        pid = pgid = -1
        create_time = 0.0
    if pid <= 1 or pgid <= 1 or not math.isfinite(create_time) or create_time <= 0:
        return ReleaseProof(False, None, {
            "code": "release_unknown",
            "detail": ("the recorded owned-process identity has no verified pid/create_time; "
                       "no signal was sent")}, 0.0, checked)
    if pgid != pid:
        return ReleaseProof(False, None, {
            "code": "release_unknown",
            "detail": ("the recorded pgid does not match the isolated child's own pid; "
                       "no signal was sent")}, 0.0, checked)
    leader = {"pid": pid, "create_time": create_time}
    checked = [leader] + [dict(item) for item in known_pids
                          if int(item.get("pid", -1)) != pid]
    collect = reap or (lambda: _reap_owned(pid))

    def state(item: dict) -> bool | None:
        try:
            return metrics.alive(item)
        except Exception:  # noqa: BLE001 - unreadable is unknown, never released
            return None

    def gone() -> bool:
        collect()
        return (state(leader) is False and _group_alive(pgid) is False
                and all(state(item) is False for item in checked[1:]))

    collect()
    leader_state = state(leader)
    if leader_state is not True:
        # Only a verified-dead leader plus a verified-empty group is a release. An unreadable
        # leader or a live group we cannot attribute is not proof, so no signal is guessed.
        confirmed = gone()
        if not confirmed and leader_state is False:
            # The leader already exited; its pgid may be reused, so stop only sampled
            # descendants still verified alive, then recheck once over a bounded window.
            _stop_sampled_descendants(checked, leader, state)
            deadline = clock() + 2.0
            while not confirmed and clock() < deadline:
                collect()
                confirmed = gone()
                if not confirmed:
                    time.sleep(0.05)
        if not confirmed and _group_alive(pgid) is None:
            # A leader in its exit transition can be briefly unreadable (macOS reports its pid
            # unaddressable and its group unsignalable before it is reapable). Re-verify over
            # the grace window; a still-unreadable identity holds after it.
            deadline = clock() + max(0.0, grace)
            while not confirmed and clock() < deadline:
                time.sleep(0.05)
                confirmed = gone()
        reason = None if confirmed else {
            "code": "release_unknown",
            "detail": (f"owned process group {pgid} cannot be verified as this attempt's "
                       f"(leader_alive={leader_state}, group_alive={_group_alive(pgid)})")}
        return ReleaseProof(confirmed, _utcnow() if confirmed else None, reason,
                            max(0.0, clock() - started), checked)
    try:
        live_pgid = os.getpgid(pid)
    except ProcessLookupError:
        live_pgid = None
    except Exception:  # noqa: BLE001 - unreadable is not a verified group
        live_pgid = -1
    if live_pgid != pgid:
        # ProcessLookupError means the pid is no longer addressable (typically a child that
        # exited between the liveness read and this check). Collect it and re-verify the exact
        # facts; a different or unreadable group is never signalled.
        confirmed = gone() if live_pgid is None else False
        reason = None if confirmed else {
            "code": "release_unknown",
            "detail": (f"the live process group for pid {pid} is {live_pgid}, not the "
                       f"recorded {pgid}; no signal was sent")}
        return ReleaseProof(confirmed, _utcnow() if confirmed else None, reason, 0.0, checked)
    _signal_group(pgid, signal.SIGTERM)
    limit = max(0.0, grace)
    while clock() - started < limit:
        collect()
        if gone():
            break
        time.sleep(0.05)
    if not gone():
        leader_state = state(leader)
        if leader_state is True:
            try:
                live_pgid = os.getpgid(pid)
            except ProcessLookupError:
                live_pgid = None
            except Exception:  # noqa: BLE001 - unreadable is not a verified group
                live_pgid = -1
            if live_pgid == pgid:
                _signal_group(pgid, signal.SIGKILL)
        elif leader_state is False:
            _stop_sampled_descendants(checked, leader, state)
        # An unknown leader never justifies a group signal: the pgid may be reused.
        deadline = clock() + 2.0
        while clock() < deadline:
            collect()
            if gone():
                break
            time.sleep(0.05)
    collect()
    group = _group_alive(pgid)
    leader_state = state(leader)
    stragglers = [item for item in checked if state(item) is not False]
    confirmed = (leader_state is False and group is False and not stragglers)
    latency = max(0.0, clock() - started)
    reason = None if confirmed else {
        "code": "release_unknown",
        "detail": (f"owned process group {pgid} is not confirmed gone "
                   f"(leader_alive={leader_state}, group_alive={group}, "
                   f"unverified={len(stragglers)})")}
    return ReleaseProof(confirmed, _utcnow() if confirmed else None, reason, latency, checked)


def run_stop_proof(target_dir: Path, *, grace: float = 5.0, metrics: Metrics | None = None,
                   clock: Callable[[], float] = time.monotonic) -> dict:
    """Measure the exact stop primitive on a harmless child and write proof/evidence.

    Scope is explicit: this proves the process-tree stop primitive (identity, process group,
    descendant verification and latency), not model inference or a test-owned installer. The
    packaged proof loader keeps ``scope`` out of the wire object; capabilities only advertise
    ``supported=true`` for a ``full_runner`` proof, so this evidence never fabricates positive
    model/hardware support.
    """
    metrics = metrics or Metrics()
    target = _private_dir(Path(target_dir))
    child = subprocess.Popen(
        [sys.executable, "-I", "-c", "import time; time.sleep(60)"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)
    identity = {"pid": int(child.pid), "pgid": int(os.getpgid(child.pid)), "create_time": 0.0}
    probed = metrics.identity(child.pid)
    if probed is not None:
        identity["create_time"] = probed["create_time"]
    started_at = _utcnow()
    proof = stop_exact_identity(identity, metrics=metrics, clock=clock, grace=grace)
    try:
        child.wait(timeout=1)
    except Exception:
        pass
    evidence = {
        "format": "studio.developer-test.stop-proof-evidence.v1",
        "scope": "process_tree_stop_primitive",
        "runner_revision": RUNNER_REVISION,
        "runner_sha256": runner_identity()["runner_sha256"],
        "method": "stop_exact_identity: SIGTERM process group, bounded wait, SIGKILL, "
                  "leader+group+sampled-descendant verification",
        "identity": identity,
        "confirmed": proof.confirmed,
        "latency_seconds": proof.latency_seconds,
        "checked_pids": proof.checked_pids,
        "started_at": started_at,
        "finished_at": _utcnow(),
        "limitations": ("harmless sleep child; does not cover image inference, model loading "
                        "or a test-owned installer"),
    }
    evidence_raw = _json_bytes(evidence)
    _atomic_write(target / "stop-proof-evidence.json", evidence_raw)
    wire = {
        "stop_latency_seconds": round(proof.latency_seconds, 6),
        "source": ("harmless subprocess through the runner's exact stop_exact_identity primitive; "
                   "process-tree release only"),
        "revision": RUNNER_REVISION,
        "proof_sha256": _sha256(evidence_raw),
    }
    _atomic_write(target / "stop-proof.json", _json_bytes(wire))
    _atomic_write(target / "stop-proof-meta.json",
                  _json_bytes({"scope": "process_tree_stop_primitive",
                               "evidence_file": "stop-proof-evidence.json"}))
    return {**wire, "scope": "process_tree_stop_primitive",
            "evidence_file": "stop-proof-evidence.json"}


@dataclass(frozen=True)
class ReleaseProof:
    confirmed: bool
    confirmed_at: str | None
    reason: dict | None
    latency_seconds: float
    checked_pids: list[dict]


def _hardware(metrics: Metrics) -> tuple[dict, dict[str, str]]:
    reasons: dict[str, str] = {}
    chip = None
    try:
        chip = subprocess.check_output(
            ["/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"],
            text=True, timeout=5).strip() or None
    except Exception as exc:  # noqa: BLE001
        reasons["hardware.chip_model"] = f"sysctl brand string unreadable: {exc}"
    chip_family = None
    if chip:
        chip_family = chip.split()[0] if chip.split() else None
    else:
        reasons["hardware.chip_family"] = "chip model is unknown, so chip family is unknown"
    try:
        ram = metrics.total_bytes()
    except MetricUnavailable as exc:
        ram = None
        reasons["hardware.ram_bytes"] = str(exc)
    try:
        os_version = platform.mac_ver()[0] or None
    except Exception:
        os_version = None
    if not os_version:
        reasons["hardware.os_version"] = "platform.mac_ver() did not report a macOS version"
    try:
        os_build = subprocess.check_output(
            ["/usr/bin/sw_vers", "-buildVersion"], text=True, timeout=5).strip() or None
    except Exception as exc:  # noqa: BLE001
        os_build = None
        reasons["hardware.os_build"] = f"sw_vers build unreadable: {exc}"
    hardware = {"chip_family": chip_family, "chip_model": chip, "ram_bytes": ram,
                "os_name": "macOS", "os_version": os_version, "os_build": os_build}
    return hardware, reasons


# ── receipt construction ─────────────────────────────────────────────────────

def _null_measurements() -> dict:
    return {name: None for name in MEASUREMENT_FIELDS}


def _metric_reasons(measurements: dict, meta: dict, hardware: dict,
                    reasons: dict[str, str]) -> list[dict]:
    output = []
    seen = set()
    fields = list(MEASUREMENT_FIELDS) + ["available_memory_before_start_bytes"]
    for name in fields:
        value = (meta.get(name) if name == "available_memory_before_start_bytes"
                 else measurements.get(name))
        if value is None:
            reason = reasons.get(name) or f"{name} was not observed"
            output.append({"field": name, "reason": reason[:1024]})
            seen.add(name)
    for name in HARDWARE_FIELDS:
        if hardware.get(name) is None:
            reason = reasons.get(f"hardware.{name}") or f"hardware.{name} was not observed"
            output.append({"field": f"hardware.{name}", "reason": reason[:1024]})
    return output


def _checks(values: dict[str, tuple]) -> list[dict]:
    output = []
    for name in CHECKS:
        value = values.get(name, (False, "incomplete_evidence", "not evaluated"))
        if len(value) == 2:  # a legacy (passed, detail) caller keeps working
            passed, detail = value
            code = "incomplete_evidence"
        else:
            passed, code, detail = value
        output.append({
            "name": name,
            "passed": bool(passed),
            "reason": None if passed else {
                "code": code if code in REASON_CODES else "incomplete_evidence",
                "detail": (detail or "not evaluated")[:4096]},
        })
    return output


def _build_receipt(
        *,
        plan: RequestPlan,
        attempt_id: str,
        idempotency_key: str,
        node_id: str,
        controller_id: str,
        started_at: str,
        finished_at: str,
        outcome: str,
        reason: dict | None,
        measurements: dict,
        metadata: dict,
        hardware: dict,
        hardware_reasons: dict[str, str],
        installation: dict,
        checks: dict[str, tuple[bool, str | None]],
        log: dict,
        output: dict | None,
        release: ReleaseProof,
        hub_version: str,
        metric_reasons: dict[str, str] | None = None) -> dict:
    model = plan.model
    published = {
        "reference_max": model.wire["published_reference_max"],
        "prompt_max": model.wire["published_prompt_max"],
        "intrinsic_reference_max": model.wire["intrinsic_reference_max"],
        "source": model.wire["published_limits_source"],
        "intrinsic_source": model.wire["intrinsic_reference_source"],
    }
    metadata = {**metadata, "published_limits": published,
                "requested_limits": plan.limits,
                "safety_policy": metadata["safety_policy"]}
    reasons = dict(metric_reasons or {})
    reasons.update(hardware_reasons)
    receipt = {
        "format": "studio.developer-test.receipt.v1",
        "run_id": plan.request["run_id"],
        "test_id": plan.request["test_id"],
        "attempt_id": attempt_id,
        "idempotency_key": idempotency_key,
        "request_sha256": plan.request_sha256,
        "node_id": node_id,
        "controller_id": controller_id,
        "node_setting_revision": plan.request["node_setting_revision"],
        "runner_id": RUNNER_ID,
        "model": model.receipt_model(),
        "hardware": hardware,
        "builds": {"hub": hub_version, "runner": RUNNER_REVISION},
        "runner_sha256": plan.request["runner_sha256"],
        "kind": plan.request["kind"],
        "settings": plan.request["settings"],
        "installation": installation,
        "started_at": started_at,
        "finished_at": finished_at,
        "outcome": outcome,
        "reason": reason,
        "measurements": measurements,
        "measurement_metadata": metadata,
        "metric_unavailable_reasons": _metric_reasons(measurements, metadata, hardware, reasons),
        "checks": _checks(checks),
        "log": log,
        "output": output,
        "resource_release": {
            "confirmed": release.confirmed,
            "confirmed_at": release.confirmed_at if release.confirmed else None,
            "reason": release.reason if not release.confirmed else None,
        },
        "runner_revision": RUNNER_REVISION,
        "fixture_set_sha256": plan.request["fixture_set_sha256"],
    }
    return receipt


def _not_run_receipt(plan: RequestPlan, attempt_id: str, idempotency_key: str,
                     node_id: str, controller_id: str, started_at: str, finished_at: str,
                     code: str, detail: str, hub_version: str, release: ReleaseProof,
                     metadata: dict, hardware: dict, hardware_reasons: dict[str, str],
                     log: dict, installation: dict, metrics: dict,
                     metric_reasons: dict[str, str], *, outcome: str = "not_run") -> dict:
    checks = {name: (False, code if code in REASON_CODES else "incomplete_evidence",
                     f"not evaluated: {code}") for name in CHECKS}
    checks["resource_release"] = (release.confirmed, "release_unknown",
                                  "release unconfirmed")
    return _build_receipt(
        plan=plan, attempt_id=attempt_id, idempotency_key=idempotency_key,
        node_id=node_id, controller_id=controller_id, started_at=started_at,
        finished_at=finished_at, outcome=outcome,
        reason={"code": code if code in REASON_CODES else "internal_error",
                "detail": detail[:4096]},
        measurements=_null_measurements(), metadata=metadata, hardware=hardware,
        hardware_reasons=hardware_reasons, installation=installation, checks=checks,
        log=log, output=None, release=release, hub_version=hub_version,
        metric_reasons=metric_reasons)


# ── the runner ───────────────────────────────────────────────────────────────

@dataclass
class RunnerOutcome:
    receipt: dict | None
    receipt_bytes: bytes | None
    receipt_sha256: str | None
    log_bytes: bytes
    log_descriptor: dict
    release: ReleaseProof
    terminal: bool
    process_identity: dict | None
    child_result: dict | None
    attempt_dir: Path
    reason: dict | None = None


class ImageTestRunner:
    """One node's packaged image test runner. The parent owns ledger, routes and admission."""

    def __init__(
            self,
            *,
            data_dir: Path,
            models_dir: Path,
            manifests_dir: Path,
            assets_dir: Path = ASSETS_DIR,
            runtime_python: Path | str | None = None,
            runtime_sha256: str | None = None,
            hub_version: str = "unknown",
            total_memory_bytes: int | None = None,
            models: list[ModelCapability] | None = None,
            rows: Iterable[Any] | None = None,
            preflight: Callable[[dict], dict] | None = None,
            metrics: Metrics | None = None,
            install_model: Callable[[str, str], dict] | None = None,
            machine: str = "local",
            sample_interval_ms: int = 250,
            stop_grace_seconds: float = 5.0,
            child_command: list[str] | None = None,
            clock: Callable[[], float] = time.monotonic):
        self.data_dir = Path(data_dir)
        self.models_dir = Path(models_dir)
        self.manifests_dir = Path(manifests_dir)
        self.assets_dir = Path(assets_dir)
        self.runtime_python = str(runtime_python) if runtime_python else None
        self.runtime_sha256 = runtime_sha256 or "unreported"
        self.hub_version = hub_version
        self.total_memory_bytes = total_memory_bytes
        self.metrics = metrics or Metrics()
        self.install_model = install_model
        self.machine = machine
        self.sample_interval_ms = max(1, int(sample_interval_ms))
        self.stop_grace_seconds = max(0.0, float(stop_grace_seconds))
        self.clock = clock
        if child_command is not None:
            self._child_command = list(child_command)
        elif self.runtime_python is not None:
            self._child_command = [self.runtime_python, "-I", "-B",
                                   str(Path(__file__).resolve())]
        else:
            # Never fall back to the Hub's own interpreter: only the packaged Image runtime
            # carries mflux/PIL, and a test must not run on a guessed interpreter.
            self._child_command = None
        self._runtime_available = self._child_command is not None
        self._active: dict[str, dict] = {}
        self.fixtures = FixtureLibrary(self.assets_dir)
        self.policy = SafetyPolicy(self.assets_dir)
        self._models = models
        self._models_explicit = models is not None
        self._rows = rows
        self._preflight = preflight
        self._attempt_root = self.data_dir / "developer-tests" / "attempts"

    # -- projection ---------------------------------------------------------
    def model_capabilities(self, *, refresh: bool = True) -> list[ModelCapability]:
        """Live install state on every call; an explicitly injected list is caller-owned."""
        if self._models is not None and (not refresh or self._models_explicit):
            return self._models
        self._models = build_model_capabilities(
            self.manifests_dir, library=self.models_dir, rows=self._rows,
            preflight=self._preflight, total_memory_bytes=self.total_memory_bytes,
            fixture_reference_max=max(
                [len(item["reference_order"]) for item in self.fixtures.wire()] or [0]))
        return self._models

    def capabilities(self, *, node_id: str, controller_id: str, setting: dict) -> dict:
        return capabilities(
            node_id=node_id, controller_id=controller_id, setting=setting,
            manifests_dir=self.manifests_dir, library=self.models_dir, rows=self._rows,
            preflight=self._preflight, assets_dir=self.assets_dir,
            total_memory_bytes=self.total_memory_bytes,
            models=self.model_capabilities(refresh=True),
            runtime_available=self._runtime_available)

    # -- exact process identity --------------------------------------------
    def process_identity(self, attempt_id: str) -> dict | None:
        active = self._active.get(attempt_id)
        if active is not None:
            return dict(active)
        path = self._attempt_dir(attempt_id) / "child-identity.json"
        try:
            value = _read_json(path)
            return value if isinstance(value, dict) else None
        except (OSError, ValueError):
            return None

    def active_attempt(self) -> str | None:
        return next(iter(self._active), None)

    def _attempt_dir(self, attempt_id: str) -> Path:
        _segment(attempt_id, "attempt_id")
        return _private_dir(self._attempt_root / attempt_id)

    # -- stop / recovery ----------------------------------------------------
    def _stop_identity(self, identity: dict | None, *, known_pids: Iterable[dict] = (),
                       grace: float | None = None,
                       reap: Callable[[], None] | None = None) -> ReleaseProof:
        return stop_exact_identity(identity, metrics=self.metrics, clock=self.clock,
                                   grace=self.stop_grace_seconds if grace is None else grace,
                                   known_pids=known_pids, reap=reap)

    def _stage(self, attempt_id: str) -> str | None:
        try:
            value = _read_json(self._attempt_dir(attempt_id) / "stage.json")
        except (OSError, ValueError):
            return None
        return value.get("stage") if isinstance(value, dict) else None

    def stop_recorded(self, attempt_id: str) -> ReleaseProof:
        """Stop only the exact persisted child identity; used on restart/recovery."""
        identity = self.process_identity(attempt_id)
        if identity is None:
            # No durable identity: release is provable only when the durable stage proves the
            # child never spawned. A ``running`` stage may have spawned before the identity was
            # persisted, so that gap stays recovery_hold instead of a guessed confirmation.
            stage = self._stage(attempt_id)
            if stage in ("waiting_idle", "accepted", "installing", "not_run"):
                proof = ReleaseProof(True, _utcnow(), None, 0.0, [])
            else:
                proof = ReleaseProof(False, None, {
                    "code": "release_unknown",
                    "detail": ("no persisted child identity and the durable stage is "
                               f"{stage!r}; the child may have started")}, 0.0, [])
        else:
            proof = self._stop_identity(identity)
        _atomic_write(self._attempt_dir(attempt_id) / "stop-proof.json",
                      _json_bytes({"confirmed": proof.confirmed,
                                   "latency_seconds": proof.latency_seconds,
                                   "checked_pids": proof.checked_pids,
                                   "reason": proof.reason}))
        self._active.pop(attempt_id, None)
        return proof

    # -- log ---------------------------------------------------------------
    def read_log(self, attempt_id: str) -> bytes:
        path = self._attempt_dir(attempt_id) / "log.txt"
        try:
            return path.read_bytes()
        except OSError:
            return b""

    def read_log_chunk(self, attempt_id: str, offset: int, length: int) -> dict:
        """A bounded chunk of the retained redacted log (parent's route can use this)."""
        raw = self.read_log(attempt_id)
        _require(type(offset) is int and type(length) is int and offset >= 0 and length >= 0,
                 "log offset/length must be nonnegative integers")
        _require(offset <= len(raw), "log offset is beyond the retained bytes")
        length = min(length, LOG_CHUNK_BYTES, len(raw) - offset)
        body = raw[offset:offset + length]
        descriptor = {"sha256": _sha256(raw), "size_bytes": len(raw), "truncated": False}
        try:
            receipt = _read_json(self._attempt_dir(attempt_id) / "receipt.json")
            if (isinstance(receipt, dict) and isinstance(receipt.get("log"), dict)
                    and receipt["log"].get("size_bytes") == len(raw)):
                descriptor = receipt["log"]
        except (OSError, ValueError):
            pass
        return {"data": body, "offset_bytes": offset, "length_bytes": len(body),
                "chunk_sha256": _sha256(body), "eof": offset + len(body) == len(raw),
                "log": descriptor}

    def _log_descriptor(self, body: bytes, truncated: bool) -> dict:
        return {"sha256": _sha256(body), "size_bytes": len(body), "truncated": bool(truncated)}

    # -- installation ------------------------------------------------------
    def _install_if_needed(self, model: ModelCapability, revision: str
                           ) -> tuple[dict, bool, bool]:
        """Run the parent's installer callback and report whether its work is released.

        The callback starts/reuses the existing model_downloads transfer and polls it; it must
        never cancel a shared download. ``release_confirmed`` is true only when no test-owned
        downloader remains (installed already, completed, or never started). A started transfer
        whose terminal state the callback cannot confirm leaves the attempt recovery_hold.
        """
        if model.wire["installed"]:
            return {"required": False, "started": False, "completed": False,
                    "download_id": None, "verified_manifest_sha256": None,
                    "reason": None}, True, True
        if self.install_model is None:
            return {"required": True, "started": False, "completed": False,
                    "download_id": None, "verified_manifest_sha256": None,
                    "reason": {"code": "model_unavailable",
                               "detail": "no installer callback is wired on this node"}}, False, True
        try:
            result = self.install_model(model.wire["package_id"], revision) or {}
        except Exception as exc:  # noqa: BLE001 - installer ownership stays with the parent
            return {"required": True, "started": True, "completed": False,
                    "download_id": None, "verified_manifest_sha256": None,
                    "reason": {"code": "model_install_failed",
                               "detail": str(exc)[:4096]}}, False, False
        started = bool(result.get("started", True))
        completed = bool(result.get("completed"))
        release_confirmed = bool(result.get("release_confirmed", completed or not started))
        return {"required": True, "started": started, "completed": completed,
                "download_id": result.get("download_id"),
                "verified_manifest_sha256": result.get("verified_manifest_sha256"),
                "reason": None if completed else {
                    "code": "model_install_failed",
                    "detail": str(result.get("reason") or "the installer did not complete")[:4096]}
                }, completed, release_confirmed

    # -- the attempt -------------------------------------------------------
    def _revalidate_pins(self, plan: RequestPlan, request: dict,
                         admission: Any | None, revalidate: Callable[[], None] | None) -> None:
        """Re-read every packaged pin and the live setting immediately before spawn.

        A queued attempt can wait through installation while the runner, fixtures, policy,
        model manifest or node setting change. Nothing is silently replaced: a mismatch refuses
        before any child work.
        """
        identity = runner_identity()
        _require(request["runner_revision"] == identity["runner_revision"]
                 and request["runner_sha256"] == identity["runner_sha256"],
                 "the packaged runner changed after acceptance", "stale_setting")
        manifest_raw = (self.assets_dir / "fixtures" / "manifest.json").read_bytes()
        _require(_sha256(manifest_raw) == self.fixtures.sha256
                 == request["fixture_set_sha256"],
                 "the packaged fixture set changed after acceptance", "stale_setting")
        for fixture_id, path in plan.references:
            fixture = self.fixtures.fixture(fixture_id)
            body = Path(path).read_bytes()
            _require(len(body) == fixture.size_bytes and _sha256(body) == fixture.sha256,
                     f"fixture {fixture_id} bytes changed after acceptance", "integrity_error")
        policy_raw = (self.assets_dir / "safety-policy.json").read_bytes()
        _require(_sha256(policy_raw) == self.policy.sha256,
                 "the packaged safety policy changed after acceptance", "stale_setting")
        model_manifest = self.manifests_dir / f"{plan.model.wire['package_id']}.json"
        raw = model_manifest.read_bytes()
        _require(_sha256(raw) == plan.model.wire["manifest_sha256"],
                 "the model manifest changed after acceptance", "stale_setting")
        manifest = _load_json(raw)
        _require(isinstance(manifest, dict)
                 and manifest.get("model_id") == plan.request["model"]["id"]
                 and manifest.get("revision") == plan.request["model"]["revision"],
                 "the model manifest no longer pins the accepted model/revision",
                 "stale_setting")
        if revalidate is not None:
            revalidate()
        elif admission is not None:
            getter = getattr(admission, "setting_revision", None)
            if callable(getter):
                _require(getter() == request["node_setting_revision"],
                         "node_setting_revision is stale", "stale_setting")

    def run_attempt(
            self,
            request: dict,
            request_bytes: bytes,
            *,
            attempt_id: str,
            idempotency_key: str,
            node_id: str,
            controller_id: str,
            admission: Any | None = None,
            cancel_event: Any | None = None,
            revalidate: Callable[[], None] | None = None) -> RunnerOutcome:
        """Run one finite vector. Admission/installation are parent callbacks, never invented."""
        started_mono = self.clock()
        started_at = _utcnow()
        attempt_dir = self._attempt_dir(attempt_id)
        _atomic_write(attempt_dir / "request.json", request_bytes)
        plan = validate_pinned_request(
            request, fixtures=self.fixtures, policy=self.policy,
            models=self.model_capabilities(refresh=True), request_bytes=request_bytes)
        if not self._runtime_available:
            raise RunnerRefusal(
                "unsupported",
                "no packaged Image runtime interpreter is configured on this node")
        model = plan.model
        policy_wire = self.policy.wire()
        metadata = {
            "method": {
                "start_memory": "psutil.virtual_memory().available",
                "start_memory_scope": "host_available_physical_unified_memory",
                "rss": "sampled_owned_process_tree_rss_sum",
                "swap": "sampled_system_swap_used",
                "sample_interval_ms": self.sample_interval_ms,
                "elapsed_clock": "monotonic",
            },
            "sample_count": 0,
            "sample_gaps_ms": [],
            "available_memory_before_start_bytes": None,
            "preflight_observed_at": None,
            "safety_policy": policy_wire,
            "published_limits": {},
            "requested_limits": plan.limits,
        }
        hardware, hardware_reasons = _hardware(self.metrics)
        metric_reasons: dict[str, str] = {}
        empty_log = self._log_descriptor(b"", False)

        # Preflight memory, sampled by the exact named method immediately before test work.
        available = None
        try:
            available = self.metrics.available_bytes()
        except MetricUnavailable as exc:
            metric_reasons["available_memory_before_start_bytes"] = str(exc)
        metadata["available_memory_before_start_bytes"] = available
        metadata["preflight_observed_at"] = _utcnow()
        floor = plan.limits["test_start_min_free_bytes"]
        swap_baseline = None
        try:
            swap_baseline = self.metrics.swap_used_bytes()
        except MetricUnavailable as exc:
            metric_reasons["system_swap_baseline_bytes"] = str(exc)
            metric_reasons["system_swap_peak_bytes"] = str(exc)
            metric_reasons["swap_growth_bytes"] = str(exc)
        resource_safe = (available is not None and available >= floor
                         and swap_baseline is not None)

        # Reserve the one physical test lease before the install callback or any child spawn.
        reserved = False
        if admission is not None:
            try:
                admission.reserve(attempt_id, machine=self.machine,
                                  resource_safe=resource_safe)
                reserved = True
            except Exception as exc:  # noqa: BLE001 - the parent's refusal vocabulary
                code = _admission_code(getattr(exc, "code", None))
                detail = getattr(exc, "detail", str(exc))
                proof = ReleaseProof(True, _utcnow(), None, 0.0, [])
                receipt = _not_run_receipt(
                    plan, attempt_id, idempotency_key, node_id, controller_id, started_at,
                    _utcnow(), code, str(detail), self.hub_version, proof, metadata,
                    hardware, hardware_reasons, empty_log, {"required": not model.wire["installed"],
                                                            "started": False, "completed": False,
                                                            "download_id": None,
                                                            "verified_manifest_sha256": None,
                                                            "reason": None},
                    _null_measurements(), metric_reasons)
                return self._settle(receipt, b"", empty_log, proof, None, None, attempt_dir,
                                    terminal=True, reason={"code": code,
                                                           "detail": str(detail)[:4096]})

        def prework_refusal(code: str, detail: str, outcome: str = "not_run") -> RunnerOutcome:
            proof = ReleaseProof(True, _utcnow(), None, 0.0, [])
            if reserved and not self._release(admission, attempt_id, True):
                proof = ReleaseProof(
                    False, None,
                    {"code": "release_unknown",
                     "detail": "admission did not confirm the physical test lease release"},
                    0.0, [])
            if not proof.confirmed:
                reason = {"code": "release_unknown",
                          "detail": "the test lease release is not confirmed"}
                _atomic_write(attempt_dir / "unsettled.json",
                              _json_bytes({"outcome": "recovery_hold", "reason": reason}))
                return RunnerOutcome(
                    receipt=None, receipt_bytes=None, receipt_sha256=None,
                    log_bytes=b"", log_descriptor=empty_log, release=proof,
                    terminal=False, process_identity=None, child_result=None,
                    attempt_dir=attempt_dir, reason=reason)
            receipt = _not_run_receipt(
                plan, attempt_id, idempotency_key, node_id, controller_id, started_at,
                _utcnow(), code, detail, self.hub_version, proof, metadata, hardware,
                hardware_reasons, empty_log, installation, _null_measurements(),
                metric_reasons, outcome=outcome)
            return self._settle(receipt, b"", empty_log, proof, None, None, attempt_dir,
                                terminal=True, reason={"code": code, "detail": detail[:4096]})

        installation = {"required": not model.wire["installed"], "started": False,
                        "completed": False, "download_id": None,
                        "verified_manifest_sha256": None, "reason": None}
        if available is None:
            return prework_refusal(
                "metric_unavailable",
                "psutil.virtual_memory().available could not be read before test work")
        if available < floor:
            return prework_refusal(
                "start_memory_below_floor",
                f"available memory {available} is below the requested floor {floor}")
        if swap_baseline is None:
            return prework_refusal(
                "metric_unavailable",
                "system swap baseline is unreadable, so the swap budget cannot be enforced")
        if admission is not None and admission.cancel_requested(attempt_id):
            return prework_refusal("cancel_requested", "cancelled before installation",
                                   outcome="cancelled")

        _atomic_write(attempt_dir / "stage.json",
                      _json_bytes({"stage": "installing", "attempt_id": attempt_id,
                                   "at": _utcnow()}))
        installation, installed, install_released = self._install_if_needed(
            model, model.wire["model"]["revision"])
        if not installed:
            if not install_released:
                proof = ReleaseProof(
                    False, None,
                    {"code": "release_unknown",
                     "detail": ("the installer reported a started transfer whose terminal "
                                "release is not confirmed")}, 0.0, [])
                if reserved:
                    self._release(admission, attempt_id, False)
                reason = {"code": "release_unknown",
                          "detail": "test-owned installation work is not confirmed released"}
                _atomic_write(attempt_dir / "unsettled.json",
                              _json_bytes({"outcome": "recovery_hold", "reason": reason,
                                           "installation": installation}))
                return RunnerOutcome(
                    receipt=None, receipt_bytes=None, receipt_sha256=None,
                    log_bytes=b"", log_descriptor=empty_log, release=proof,
                    terminal=False, process_identity=None, child_result=None,
                    attempt_dir=attempt_dir, reason=reason)
            return prework_refusal("model_install_failed",
                                   installation["reason"]["detail"], outcome="failed")
        if admission is not None and not admission.stage_allowed(attempt_id):
            return prework_refusal("cancel_requested", "cancelled before the image child started",
                                   outcome="cancelled")
        try:
            self._revalidate_pins(plan, request, admission, revalidate)
        except RunnerRefusal as exc:
            return prework_refusal(exc.code, exc.detail)
        except Exception as exc:  # noqa: BLE001 - never spawn on an unverified pre-spawn state
            return prework_refusal("internal_error",
                                   f"pre-spawn revalidation failed: {exc}")
        if cancel_event is None and admission is not None:
            try:
                cancel_event = admission.cancel_event(attempt_id)
            except Exception:
                cancel_event = None
        _atomic_write(attempt_dir / "stage.json",
                      _json_bytes({"stage": "running", "attempt_id": attempt_id,
                                   "at": _utcnow()}))

        child_request = {
            "request": request,
            "prompt": plan.prompt,
            "reference_paths": [str(path) for _, path in plan.references],
            "loading_mode": "low" if request["settings"]["memory_mode"] == "bounded-staged"
                            else "full",
        }
        child_request_path = attempt_dir / "child-request.json"
        _atomic_write(child_request_path, _json_bytes(child_request))
        result_path = attempt_dir / "child-result.json"
        output_path = attempt_dir / "output.png"
        command = self._child_command + [
            "--request-file", str(child_request_path),
            "--result-file", str(result_path),
            "--output", str(output_path),
            "--data-dir", str(attempt_dir / "image-data"),
            "--models-dir", str(self.models_dir),
            "--manifests-dir", str(self.manifests_dir),
            "--runtime-sha256", str(self.runtime_sha256),
            "--total-memory-bytes", str(self.total_memory_bytes or 0),
        ]
        sink = _BoundedLogSink((
            plan.prompt.encode("utf-8"), str(self.data_dir).encode(),
            str(self.models_dir).encode(), str(self._attempt_root).encode()))
        process = None
        log_reader = None
        try:
            process = subprocess.Popen(
                command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, start_new_session=True, close_fds=True,
                env=self._child_env())
            log_reader = threading.Thread(target=_drain_child_log,
                                          args=(process.stdout, sink), daemon=True)
            log_reader.start()
        except Exception as exc:  # noqa: BLE001
            proof = ReleaseProof(True, _utcnow(), None, 0.0, [])
            detail = f"the isolated image child could not start: {exc}"
            receipt = _not_run_receipt(
                plan, attempt_id, idempotency_key, node_id, controller_id, started_at,
                _utcnow(), "internal_error", detail, self.hub_version, proof, metadata,
                hardware, hardware_reasons, empty_log, installation, _null_measurements(),
                metric_reasons)
            if admission is not None:
                self._release(admission, attempt_id, True)
            return self._settle(receipt, b"", empty_log, proof, None, None, attempt_dir,
                                terminal=True, reason={"code": "internal_error",
                                                       "detail": detail})
        identity = {"pid": int(process.pid), "pgid": int(os.getpgid(process.pid)),
                    "create_time": 0.0}
        probed = self.metrics.identity(process.pid)
        for _ in range(3):
            if probed is not None:
                break
            time.sleep(0.05)
            probed = self.metrics.identity(process.pid)
        if probed is not None:
            identity["create_time"] = probed["create_time"]
        _atomic_write(attempt_dir / "child-identity.json", _json_bytes(identity))
        self._active[attempt_id] = dict(identity)

        max_runtime = float(plan.limits["max_runtime_seconds"])
        max_rss = int(plan.limits["max_process_tree_rss_bytes"])
        max_swap = int(plan.limits["max_system_swap_growth_bytes"])
        interval_ms = _effective_sample_interval_ms(self.sample_interval_ms, max_runtime)
        interval = interval_ms / 1000.0
        metadata["method"]["sample_interval_ms"] = interval_ms
        samples = 0
        gaps: list[int] = []
        peak_rss = None
        swap_peak = swap_baseline
        known: dict[tuple[int, float], dict] = {}
        stop_code: str | None = None
        stop_detail: str | None = None
        last_sample = self.clock()
        while True:
            returncode = process.poll()
            if returncode is not None:
                break
            now = self.clock()
            if cancel_event is not None and cancel_event.is_set():
                stop_code = getattr(cancel_event, "reason_code", "cancel_requested")
                stop_detail = getattr(cancel_event, "reason_detail",
                                      "the test was cancelled by the node")
            elif now - started_mono > max_runtime:
                stop_code = "runtime_budget"
                stop_detail = (f"elapsed {now - started_mono:.3f}s exceeds the requested "
                               f"{max_runtime:g}s runtime budget")
            else:
                try:
                    rss, tree = self.metrics.tree(process.pid)
                    samples += 1
                    peak_rss = rss if peak_rss is None else max(peak_rss, rss)
                    for item in tree:
                        known[(int(item["pid"]), float(item["create_time"]))] = item
                    swap_now = self.metrics.swap_used_bytes()
                    swap_peak = max(swap_peak, swap_now)
                    if rss > max_rss:
                        stop_code = "rss_budget"
                        stop_detail = (f"owned process tree RSS {rss} exceeds the requested "
                                       f"{max_rss} byte budget")
                    elif swap_now - swap_baseline > max_swap:
                        stop_code = "swap_budget"
                        stop_detail = (f"system swap grew {swap_now - swap_baseline} bytes, "
                                       f"beyond the requested {max_swap} byte budget")
                except MetricUnavailable as exc:
                    metric_reasons["peak_process_memory_bytes"] = str(exc)
                    metric_reasons["system_swap_peak_bytes"] = str(exc)
                    metric_reasons["swap_growth_bytes"] = str(exc)
                    stop_code = "metric_unavailable"
                    stop_detail = f"a required safety sample became unreadable: {exc}"
            if stop_code is not None:
                break
            time.sleep(interval)
            now2 = self.clock()
            gaps.append(max(0, int(round((now2 - last_sample) * 1000))))
            last_sample = now2
            if len(gaps) > 4096:
                gaps = gaps[-4096:]

        if float(identity.get("create_time", 0.0)) > 0:
            proof = self._stop_identity(identity, known_pids=list(known.values()),
                                        reap=lambda: process.poll())
        else:
            # The durable identity was never verified. The unreaped direct child handle is the
            # only safe stop; release stays unconfirmed because no exact identity exists.
            try:
                process.terminate()
            except Exception:  # noqa: BLE001 - it may already have exited
                pass
            try:
                process.wait(timeout=2)
            except Exception:  # noqa: BLE001
                pass
            proof = ReleaseProof(False, None, {
                "code": "release_unknown",
                "detail": ("the child identity could not be verified after spawn; "
                           "no exact-release proof is possible")}, 0.0, [])
        self._active.pop(attempt_id, None)
        if admission is not None:
            released_ok = self._release(admission, attempt_id, proof.confirmed)
            if proof.confirmed and not released_ok:
                proof = ReleaseProof(
                    False, None,
                    {"code": "release_unknown",
                     "detail": "admission did not confirm the physical test lease release"},
                    proof.latency_seconds, proof.checked_pids)
        try:
            returncode = process.wait(timeout=1)
        except Exception:
            returncode = None
        if log_reader is not None:
            log_reader.join(timeout=5)
            if log_reader.is_alive():
                sink.truncated = True
        log_body = sink.body()
        log_descriptor = self._log_descriptor(log_body, sink.truncated)
        _atomic_write(attempt_dir / "log.txt", log_body)
        child_result = None
        try:
            value = _read_json(result_path)
            child_result = value if isinstance(value, dict) else None
        except (OSError, ValueError):
            child_result = None

        measurements = _null_measurements()
        elapsed = max(0.0, self.clock() - started_mono)
        measurements["elapsed_seconds"] = round(elapsed, 6)
        if child_result and isinstance(child_result.get("generation_seconds"), (int, float)):
            generation = float(child_result["generation_seconds"])
            if math.isfinite(generation) and generation >= 0:
                measurements["generation_seconds"] = round(min(generation, elapsed), 6)
        if measurements["generation_seconds"] is None:
            metric_reasons.setdefault(
                "generation_seconds",
                str((child_result or {}).get("generation_seconds_reason")
                    or "the child reported no inference-only duration"))
        if peak_rss is not None:
            measurements["peak_process_memory_bytes"] = int(peak_rss)
        else:
            metric_reasons.setdefault("peak_process_memory_bytes",
                                      "no owned-process sample was taken")
        measurements["system_swap_baseline_bytes"] = int(swap_baseline)
        if swap_peak is not None:
            measurements["system_swap_peak_bytes"] = int(swap_peak)
            measurements["swap_growth_bytes"] = int(max(0, swap_peak - swap_baseline))
        metadata["sample_count"] = samples
        metadata["sample_gaps_ms"] = gaps

        output = None
        output_check = (False, "output_invalid", "no decoded output was produced")
        if output_path.exists():
            body = output_path.read_bytes()
            decoded = child_result.get("decoded") if child_result else None
            width = child_result.get("decoded_width") if child_result else None
            height = child_result.get("decoded_height") if child_result else None
            signature_ok = body.startswith(b"\x89PNG\r\n\x1a\n") and len(body) >= 33
            header_ok = False
            if signature_ok and body[12:16] == b"IHDR":
                import struct
                header_w, header_h = struct.unpack(">II", body[16:24])
                header_ok = (header_w == width and header_h == height)
            if (decoded is True and header_ok
                    and width == request["settings"]["width_px"]
                    and height == request["settings"]["height_px"]):
                output = {"sha256": _sha256(body), "size_bytes": len(body),
                          "width_px": width, "height_px": height,
                          "validation": "decoded_dimensions_match"}
                output_check = (True, None, None)
            else:
                output_check = (False, "output_invalid",
                                "the output did not decode to the requested dimensions")

        checks: dict[str, tuple[bool, str | None, str | None]] = {
            "prompt_digest": (True, None, None),
            "fixture_identity": (True, None, None),
            "start_memory_floor": (True, None, None),
            "runtime_budget": (stop_code != "runtime_budget",
                               None if stop_code != "runtime_budget" else "runtime_budget",
                               stop_detail),
            "rss_budget": (stop_code != "rss_budget",
                           None if stop_code != "rss_budget" else "rss_budget",
                           stop_detail),
            "swap_budget": (stop_code != "swap_budget",
                            None if stop_code != "swap_budget" else "swap_budget",
                            stop_detail),
            "output_decoded_dimensions": output_check,
            "resource_release": (proof.confirmed, "release_unknown",
                                 "owned resource release is unconfirmed"),
        }
        if stop_code == "metric_unavailable":
            for name in ("runtime_budget", "rss_budget", "swap_budget"):
                checks[name] = (False, "metric_unavailable", stop_detail)
        # Inspect the child's own result before the exit status: the isolated child exits 2 on
        # any refusal, so the exit code alone would hide ``engine_refused`` and its reason.
        if stop_code is None and child_result and child_result.get("ok") is False:
            engine_code = str(child_result.get("error_code") or "")
            stop_code = {"MEMORY_PRESSURE_STOPPED": "oom",
                         "SWAP_GROWTH_EXCEEDED": "oom",
                         "MODEL_FILES_UNAVAILABLE": "model_unavailable"}.get(
                             engine_code, "engine_refused")
            stop_detail = str(child_result.get("error") or
                              f"the image engine refused the job ({engine_code or 'no code'})")
        if stop_code is None and returncode not in (0, None) and output is None:
            stop_code = "interrupted"
            stop_detail = (f"the image child exited with status {returncode} without a "
                           f"valid decoded output")
        if stop_code is None and output is None:
            stop_code = "output_invalid"
            stop_detail = output_check[2] or "no valid output"
        if stop_code is None:
            missing = [name for name, value in measurements.items() if value is None]
            if missing or any(hardware.get(name) is None for name in HARDWARE_FIELDS):
                stop_code = "incomplete_evidence"
                stop_detail = "required measurement/hardware fields are unavailable: " + \
                              ", ".join(missing or [name for name in HARDWARE_FIELDS
                                                    if hardware.get(name) is None])
        outcome = "passed" if stop_code is None else (
            "cancelled" if stop_code in ("cancel_requested", "owner_cancelled",
                                         "production_arrived", "authority_lost", "paused")
            else "interrupted" if stop_code in ("worker_lost", "interrupted")
            else "failed")
        reason = None if stop_code is None else {
            "code": stop_code if stop_code in REASON_CODES else "internal_error",
            "detail": (stop_detail or "the test did not pass")[:4096]}
        if outcome != "passed":
            checks["resource_release"] = (proof.confirmed,
                                          None if proof.confirmed else
                                          "owned resource release is unconfirmed")

        if not proof.confirmed:
            unsettled = {"outcome": outcome, "reason": reason,
                         "release": {"confirmed": False, "reason": proof.reason}}
            _atomic_write(attempt_dir / "unsettled.json", _json_bytes(unsettled))
            return RunnerOutcome(
                receipt=None, receipt_bytes=None, receipt_sha256=None,
                log_bytes=log_body, log_descriptor=log_descriptor, release=proof,
                terminal=False, process_identity=identity, child_result=child_result,
                attempt_dir=attempt_dir, reason=reason)

        finished_at = _utcnow()
        receipt = _build_receipt(
            plan=plan, attempt_id=attempt_id, idempotency_key=idempotency_key,
            node_id=node_id, controller_id=controller_id, started_at=started_at,
            finished_at=finished_at, outcome=outcome, reason=reason,
            measurements=measurements, metadata=metadata, hardware=hardware,
            hardware_reasons=hardware_reasons, installation=installation, checks=checks,
            log=log_descriptor, output=output, release=proof,
            hub_version=self.hub_version, metric_reasons=metric_reasons)
        return self._settle(receipt, log_body, log_descriptor, proof, identity,
                            child_result, attempt_dir, terminal=True, reason=reason)

    def _release(self, admission: Any, attempt_id: str, confirmed: bool) -> bool:
        try:
            return bool(admission.release(attempt_id, confirmed=confirmed))
        except Exception:
            # The parent owns recovery_hold; a release-callback failure never becomes a pass.
            return False

    def _child_env(self) -> dict[str, str]:
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("PYTHON", "HF_", "HUGGINGFACE_", "STUDIOHUB_",
                                      "KH_STUDIO_", "PINOKIO_", "DYLD_"))}
        env.update({"PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
                    "HF_HUB_DISABLE_TELEMETRY": "1"})
        return env

    def _settle(self, receipt: dict, log_body: bytes, log_descriptor: dict,
                proof: ReleaseProof, identity: dict | None, child_result: dict | None,
                attempt_dir: Path, *, terminal: bool, reason: dict | None) -> RunnerOutcome:
        raw = _json_bytes(receipt)
        _atomic_write(attempt_dir / "receipt.json", raw)
        return RunnerOutcome(
            receipt=receipt, receipt_bytes=raw, receipt_sha256=_sha256(raw),
            log_bytes=log_body, log_descriptor=log_descriptor, release=proof,
            terminal=terminal, process_identity=identity, child_result=child_result,
            attempt_dir=attempt_dir, reason=reason)


# ── isolated child entry point (the shipped GenerationManager) ───────────────

def _child_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Isolated developer-test image child")
    parser.add_argument("--request-file", type=Path, required=True)
    parser.add_argument("--result-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--models-dir", type=Path, required=True)
    parser.add_argument("--manifests-dir", type=Path, required=True)
    parser.add_argument("--runtime-sha256", default="unreported")
    parser.add_argument("--total-memory-bytes", type=int, default=0)
    args = parser.parse_args(argv)
    result: dict[str, Any] = {"ok": False, "error": None, "generation_seconds": None}
    try:
        request = _read_json(args.request_file)
        prompt = request["prompt"]
        reference_paths = list(request.get("reference_paths") or [])
        loading = request.get("loading_mode") or "full"
        image_request = request["request"]
        settings = image_request["settings"]
        root = Path(__file__).resolve().parents[2]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from components.image.app.backend import generation, loading_mode
        model = generation.MODELS.get(image_request["model"]["id"])
        if model is None or model.revision != image_request["model"]["revision"]:
            raise ValueError("the request names a model/revision this runtime does not serve")
        manifest = json.loads((Path(args.manifests_dir)
                               / f"{model.package_id}.json").read_text())
        data = _private_dir(args.data_dir)
        loading_mode.save(data, loading_mode.MODE_LOW if loading == "low"
                          else loading_mode.MODE_FULL)
        total = args.total_memory_bytes or None
        manager = generation.GenerationManager(
            data, Path(args.models_dir), manifest, args.runtime_sha256,
            machine="developer-test", manifests=[manifest], total_memory_bytes=total)
        state = manager.preflight(model.repo)
        if state.get("state") != "files_verified":
            raise ValueError(f"model files are not verified: {state.get('reason') or state}")
        raw = {"repo": model.repo, "model_revision": model.revision, "prompt": prompt,
               "width": settings["width_px"], "height": settings["height_px"],
               "steps": settings["steps"], "guidance": settings["guidance"],
               "seed": settings["seed"], "output_format": "png"}
        if reference_paths:
            raw["reference_images"] = reference_paths
        job = manager.submit(raw, mode="edit" if reference_paths else "txt2img")
        while job.get("state") not in generation.TERMINAL:
            time.sleep(0.2)
            job = manager.get(job["id"]) or job
        # The shipped manager publishes no inference-only duration: its duration spans model
        # load, queueing and delivery. Never relabel that elapsed time as inference.
        result["generation_seconds"] = None
        result["generation_seconds_reason"] = (
            "the image engine reports no inference-only duration; its own duration spans "
            "model load, queueing and delivery")
        result["state"] = job.get("state")
        result["error"] = job.get("error")
        result["error_code"] = job.get("error_code")
        if job.get("state") != "done":
            raise RuntimeError(job.get("error") or f"image job ended {job.get('state')}")
        path = manager.final_path(job)
        body = Path(path).read_bytes()
        args.output.write_bytes(body)
        from PIL import Image
        with Image.open(args.output) as image:
            image.load()
            result["decoded"] = True
            result["decoded_width"], result["decoded_height"] = image.size
        result["ok"] = True
    except Exception as exc:  # noqa: BLE001 - the parent records the failure
        result["error"] = str(exc)[:4000]
    try:
        _atomic_write(args.result_file, _json_bytes(result))
    except Exception:
        pass
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(_child_main())
