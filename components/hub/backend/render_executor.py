"""Packaged final-render authority and the broker-owned execution lane."""
from __future__ import annotations

import asyncio
import asyncio.subprocess
from contextlib import ExitStack
from dataclasses import asdict, dataclass
from fractions import Fraction
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import sys
import time
from typing import Any, Callable
import uuid

import psutil

from components import host_memory
from . import render_authority_files
from .final_render_compiler import compile_final_render_packet
from .render_hash_v1 import decode_render_json
from .render_jobs import (
    decode_render_request,
    ProcessIdentity,
    QualificationPermit,
    RenderExecutionContext,
    RenderJobUnavailable,
    RuntimeQualification,
    StoppedProcessEvidence,
    ValidatedOutputReceipt,
)
from .render_profiles import (
    match_render_profile_dependencies,
    validate_render_compiler_descriptor,
    validate_render_profile_descriptor,
    validate_render_tool_descriptor,
)


@dataclass(frozen=True)
class ResourceBounds:
    max_rss_bytes: int
    max_wall_seconds: int
    min_available_memory_bytes: int
    output_limit_bytes: int


@dataclass(frozen=True)
class InstalledRenderAuthority:
    qualification: RuntimeQualification
    ffmpeg: Path
    ffprobe: Path
    selected_tier: str
    exact_encoder: str
    bounds: ResourceBounds
    font_support: bool
    profile_execution_bounds: dict[str, Any]


class _UncertainProcessOutcome(RuntimeError):
    """An invoked owned tool cannot yet be proved stopped."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _sha(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _asset_stream(reader: Any) -> Any:
    stream = reader.get("stream") if type(reader) is dict else reader
    if stream is None or not callable(getattr(stream, "fileno", None)):
        raise RuntimeError("render asset reader did not provide a fixed descriptor")
    return stream


def _input_options(entry: dict[str, Any], stream: Any) -> tuple[str, ...]:
    try:
        header = os.pread(stream.fileno(), 32, 0)
    except OSError as exc:
        raise RuntimeError("render asset header could not be verified") from exc
    role = entry["role"]
    suffix = Path(entry["safe_name"]).suffix.lower()
    if role == "base_video" and suffix in {".mp4", ".mov"} and header[4:8] == b"ftyp":
        return ("-f", "mov", "-enable_drefs", "0", "-use_absolute_path", "0",
                "-protocol_whitelist", "file,pipe")
    if role == "premixed_audio" and suffix == ".wav" and (
            header[:4] == b"RIFF" and header[8:12] == b"WAVE"):
        return ("-f", "wav", "-protocol_whitelist", "file,pipe")
    if role in {"logo", "frame_background", "overlay"} and suffix == ".png" and (
            header.startswith(b"\x89PNG\r\n\x1a\n")):
        return ("-f", "png_pipe", "-protocol_whitelist", "file,pipe")
    if role == "overlay" and suffix == ".webm" and header.startswith(b"\x1aE\xdf\xa3"):
        return ("-f", "matroska,webm", "-protocol_whitelist", "file,pipe")
    raise RuntimeError(f"render asset {entry.get('logical_id', '')} has an unsupported format")


def _confined_argv(
    argv: tuple[str, ...], packet: dict[str, Any],
    local_assets: dict[str, str], readers: dict[str, Any],
) -> tuple[str, ...]:
    manifest = {entry["logical_id"]: entry for entry in packet["manifest"]}
    by_path = {path: logical_id for logical_id, path in local_assets.items()}
    options = {
        logical_id: _input_options(manifest[logical_id], readers[logical_id])
        for logical_id in local_assets
    }
    result: list[str] = []
    seen: set[str] = set()
    index = 0
    while index < len(argv):
        value = argv[index]
        if value == "-i":
            if index + 1 >= len(argv) or argv[index + 1] not in by_path:
                raise RuntimeError("compiled render input escaped the accepted asset set")
            logical_id = by_path[argv[index + 1]]
            result.extend(options[logical_id])
            seen.add(logical_id)
        result.append(value)
        index += 1
    if seen != set(local_assets):
        raise RuntimeError("compiled render inputs do not match the accepted asset set")
    return tuple(result)


def _closed(document: Any, keys: set[str], name: str) -> dict[str, Any]:
    if type(document) is not dict or set(document) != keys:
        raise ValueError(f"{name} is not closed")
    return document


def _group_exists(process_group_id: int) -> bool:
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _hardware_class(identity: dict[str, Any]) -> str:
    explicit = identity.get("hardware_class")
    if isinstance(explicit, str) and explicit:
        return explicit
    chip = re.sub(r"[^a-z0-9]+", "-", str(identity.get("chip") or "").lower()).strip("-")
    chip = chip.removeprefix("apple-")
    memory = identity.get("memory_gb")
    if not chip or type(memory) is not int or memory < 1:
        return ""
    return f"{chip}-{memory}gb"


class InstalledRenderAuthorityLoader:
    """Installed tool evidence plus the operator's runtime authority records.

    ``<data root>/render-authority/enabled/<hardware_class>.json`` present for this Mac's
    own hardware class is what grants production. ``qualify.json`` is a time-windowed,
    one-use intent to run one qualification job. Absence or mismatch means no authority.
    """

    _RECEIPT_KEYS = {
        "format", "production_eligible", "profile_digest", "tools_digest",
        "compiler_digest", "qualification_digest", "hardware_class", "selected_tier",
        "exact_encoder", "font_support", "bounds", "files",
    }
    _ENABLED_KEYS = _RECEIPT_KEYS | {"intent_id", "measured"}
    _MEASURED_KEYS = {
        "peak_rss_bytes", "wall_seconds", "min_free_bytes", "output_bytes",
        "hub_job_id", "receipt_digest",
    }
    _INTENT_KEYS = {
        "format", "intent_id", "accept_before", "selected_tier", "exact_encoder",
        "font_support", "min_available_memory_bytes",
    }
    _MANIFEST_KEYS = {
        "format", "tool_version", "payload_format", "files", "production_eligible",
    }
    _DESCRIPTOR_NAMES = {
        "profile": "PROFILE.json", "tools": "TOOLS.json", "compiler": "COMPILER.json",
    }

    def __init__(self, resources_root: str | Path,
                 *, hardware_identity: Callable[[], dict[str, Any]],
                 data_root: str | Path | None = None,
                 clock: Callable[[], float] = time.time,
                 expected_uid: int | None = None) -> None:
        self.root = Path(resources_root).absolute() / "render-tools"
        self._hardware_identity = hardware_identity
        # Absolute but never resolved: the strict reader must walk and validate every
        # configured component, so a symlinked ancestor is refused rather than followed.
        self.data_root = (
            Path(os.path.abspath(data_root)) if data_root is not None else None)
        self._clock = clock
        self._uid = os.getuid() if expected_uid is None else expected_uid

    def _read(self, path: Path, keys: set[str], name: str) -> dict[str, Any] | None:
        try:
            raw = path.read_bytes()
            return _closed(decode_render_json(raw), keys, name)
        except (OSError, ValueError):
            return None

    def _record(self, keys: set[str], *names: str) -> dict[str, Any] | None:
        if self.data_root is None:
            return None
        try:
            raw = render_authority_files.read_record(
                self.data_root, *names, expected_uid=self._uid)
            return _closed(decode_render_json(raw), keys, "render authority record")
        except (OSError, ValueError):
            return None

    def _own_class(self) -> str | None:
        try:
            hardware_class = _hardware_class(self._hardware_identity())
        except (OSError, TypeError, ValueError, KeyError):
            return None
        return (hardware_class
                if re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,63}", hardware_class) else None)

    def _descriptors(self, document: dict[str, Any], packet: dict[str, Any] | None):
        try:
            raw = {
                name: (self.root / filename).read_bytes()
                for name, filename in self._DESCRIPTOR_NAMES.items()
            }
            profile = validate_render_profile_descriptor(raw["profile"])
            tools = validate_render_tool_descriptor(raw["tools"])
            compiler = validate_render_compiler_descriptor(raw["compiler"])
            if (
                profile.digest != document["profile_digest"]
                or tools.digest != document["tools_digest"]
                or compiler.digest != document["compiler_digest"]
                or profile.tools_artifact_sha256 != tools.digest
                or profile.compiler_artifact_sha256 != compiler.digest
            ):
                return None
            if packet is not None and not match_render_profile_dependencies(
                    _canonical(packet), profile, tools, compiler).descriptor_match:
                return None
            # The existing descriptor validators above own every descriptor key and value rule.
            # Decode only after they accept so execution never grows a second descriptor schema.
            tool_document = decode_render_json(raw["tools"])
            compiler_document = decode_render_json(raw["compiler"])
            profile_document = decode_render_json(raw["profile"])
            return tool_document, compiler_document, profile_document
        except (OSError, TypeError, ValueError, KeyError):
            return None

    def _manifest_matches(self, files: dict[str, Any]) -> bool:
        manifest = self._read(self.root / "MANIFEST.json", self._MANIFEST_KEYS, "tool manifest")
        if manifest is None or (
            manifest["format"] != "studiohub.render-tools.payload.v1"
            or manifest["payload_format"] != "macos-universal"
            or type(manifest["tool_version"]) is not str
            or not manifest["tool_version"]
            or manifest["production_eligible"] is not False
            or type(manifest["files"]) is not list
        ):
            return False
        expected = [
            {"name": name, "path": f"render-tools/{name}",
             "sha256": files[name]["sha256"], "size_bytes": files[name]["size_bytes"]}
            for name in ("ffmpeg", "ffprobe")
        ]
        return manifest["files"] == expected

    @classmethod
    def _record_digest(cls, document: dict[str, Any]) -> str:
        # intent_id and measured are evidence appended after the qualification run; they are
        # not part of the qualified authority, so a receipt copied into enabled/ keeps the
        # qualification job's digest.
        return hashlib.sha256(_canonical({
            key: value for key, value in document.items()
            if key in cls._RECEIPT_KEYS and key != "qualification_digest"
        })).hexdigest()

    def _measured_evidence(
        self, document: dict[str, Any], measured: Any,
    ) -> dict[str, Any] | None:
        """Validate evidence appended after the qualification run.

        ``intent_id`` and ``measured`` are deliberately outside
        ``qualification_digest``: the Hub appends them after the run and the
        operator's enabled/ copy keeps the qualification job's digest. Their
        types, declared bounds and job/output/receipt identity are therefore
        checked here so a malformed copy cannot stand in for a completed run.
        """
        try:
            bounds = _closed(
                document["bounds"],
                {"max_rss_bytes", "max_wall_seconds", "min_available_memory_bytes",
                 "output_limit_bytes"},
                "resource bounds",
            )
            if any(type(value) is not int or value < 1 for value in bounds.values()):
                return None
            value = _closed(measured, self._MEASURED_KEYS, "measured evidence")
            peak_rss_bytes = value["peak_rss_bytes"]
            wall_seconds = value["wall_seconds"]
            min_free_bytes = value["min_free_bytes"]
            output_bytes = value["output_bytes"]
            hub_job_id = value["hub_job_id"]
            receipt_digest = value["receipt_digest"]
            if (
                type(peak_rss_bytes) is not int
                or not 0 <= peak_rss_bytes <= bounds["max_rss_bytes"]
                or type(wall_seconds) is not int
                or not 1 <= wall_seconds <= bounds["max_wall_seconds"]
                or type(min_free_bytes) is not int
                or not 1 <= min_free_bytes <= 2**63 - 1
                or type(output_bytes) is not int
                or not 1 <= output_bytes <= bounds["output_limit_bytes"]
                or type(hub_job_id) is not str
                or not 0 < len(hub_job_id) <= 256
                or not hub_job_id.isascii()
                or not all(" " <= char <= "~" for char in hub_job_id)
                or type(receipt_digest) is not str
                or re.fullmatch(r"[0-9a-f]{64}", receipt_digest) is None
            ):
                return None
            return value
        except (KeyError, TypeError, ValueError):
            return None

    @staticmethod
    def _profile_allows(packet: dict[str, Any], bounds: dict[str, Any]) -> bool:
        try:
            recipe = packet["recipe"]
            canvas = recipe["canvas"]
            manifest = packet["manifest"]
            frame_count = math.ceil(
                recipe["target_duration_sec"] * canvas["working_fps"])
            return (
                {"width": canvas["width"], "height": canvas["height"]}
                    in bounds["canvas_pairs"]
                and canvas["working_fps"] in bounds["working_fps"]
                and recipe["target_duration_sec"] <= bounds["max_duration_sec"]
                and len(recipe["operations"]) <= bounds["max_operations"]
                and len(manifest) <= bounds["max_assets"]
                and all(row["size_bytes"] <= bounds["max_asset_bytes"] for row in manifest)
                and sum(row["size_bytes"] for row in manifest)
                    <= bounds["max_total_input_bytes"]
                and canvas["width"] * canvas["height"] * frame_count
                    <= bounds["max_pixel_frames"]
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            return False

    def _authority(
        self, document: dict[str, Any], packet: dict[str, Any] | None = None,
        *, production: bool = False,
    ) -> InstalledRenderAuthority | None:
        try:
            if (
                set(document) not in (self._RECEIPT_KEYS, self._ENABLED_KEYS)
                or document["format"] != "studio.render.qualification-receipt.v1"
                # Always false: it sits inside qualification_digest. Production comes only
                # from the record's placement in enabled/, never from this field.
                or document["production_eligible"] is not False
                or document["qualification_digest"] != self._record_digest(document)
                or type(document["font_support"]) is not bool
                or type(document["hardware_class"]) is not str
                or type(document["selected_tier"]) is not str
                or not document["selected_tier"]
                or type(document["exact_encoder"]) is not str
                or not document["exact_encoder"]
            ):
                return None
            if document["hardware_class"] != self._own_class():
                return None
            files = _closed(document["files"], {"ffmpeg", "ffprobe"}, "tool files")
            descriptors = self._descriptors(document, packet)
            if descriptors is None or not self._manifest_matches(files):
                return None
            tool_descriptor, compiler_descriptor, profile_descriptor = descriptors
            # V1 carries no hardware qualification or fidelity record. A non-null pin
            # would claim evidence this loader cannot read, so refuse it instead of
            # silently ignoring it; this does not require a fidelity record for V1.
            if (profile_descriptor["hardware_qualification_record_sha256"] is not None
                    or profile_descriptor["fidelity_record_sha256"] is not None):
                return None
            paths = {}
            for name in ("ffmpeg", "ffprobe"):
                expected = _closed(files[name], {"size_bytes", "sha256"}, name)
                if type(expected["size_bytes"]) is not int or expected["size_bytes"] < 1:
                    return None
                path = self.root / name
                info = path.lstat()
                if (not stat.S_ISREG(info.st_mode) or path.is_symlink()
                        or not os.access(path, os.X_OK)):
                    return None
                if _sha(path) != (expected["size_bytes"], expected["sha256"]):
                    return None
                paths[name] = path
            if (
                (tool_descriptor["ffmpeg_size_bytes"], tool_descriptor["ffmpeg_sha256"])
                    != (files["ffmpeg"]["size_bytes"], files["ffmpeg"]["sha256"])
                or (tool_descriptor["ffprobe_size_bytes"], tool_descriptor["ffprobe_sha256"])
                    != (files["ffprobe"]["size_bytes"], files["ffprobe"]["sha256"])
            ):
                return None
            source_hashes = {
                "python_runtime_sha256": _sha(Path(sys.executable))[1],
                "render_hash_sha256": _sha(Path(__file__).with_name("render_hash_v1.py"))[1],
                "render_contract_sha256": _sha(Path(__file__).with_name("render_contract.py"))[1],
                "final_render_compiler_sha256": _sha(
                    Path(__file__).with_name("final_render_compiler.py"))[1],
            }
            if any(compiler_descriptor[key] != value for key, value in source_hashes.items()):
                return None
            bounds_doc = _closed(
                document["bounds"],
                {"max_rss_bytes", "max_wall_seconds", "min_available_memory_bytes",
                 "output_limit_bytes"},
                "resource bounds",
            )
            if any(type(value) is not int for value in bounds_doc.values()):
                return None
            bounds = ResourceBounds(**bounds_doc)
            if min(asdict(bounds).values()) < 1:
                return None
            profile_bounds = profile_descriptor["execution_bounds"]
            if packet is not None and not self._profile_allows(packet, profile_bounds):
                return None
            if (
                profile_bounds["output_limit_bytes"] != bounds.output_limit_bytes
                or profile_bounds["process_tree_rss_limit_bytes"] != bounds.max_rss_bytes
                or profile_bounds["cumulative_wall_seconds"] != bounds.max_wall_seconds
            ):
                return None
            qualification = RuntimeQualification(
                profile_digest=document["profile_digest"], tools_digest=document["tools_digest"],
                compiler_digest=document["compiler_digest"],
                qualification_digest=document["qualification_digest"],
                hardware_class=document["hardware_class"],
                production_eligible=production,
                min_free_bytes=(profile_bounds["scratch_reserve_bytes"]
                                + profile_bounds["output_limit_bytes"]),
                scratch_reserve_bytes=profile_bounds["scratch_reserve_bytes"],
                output_limit_bytes=bounds.output_limit_bytes,
                document=json.loads(_canonical(document)),
            )
            return InstalledRenderAuthority(
                qualification=qualification, ffmpeg=paths["ffmpeg"], ffprobe=paths["ffprobe"],
                selected_tier=str(document["selected_tier"]),
                exact_encoder=str(document["exact_encoder"]), bounds=bounds,
                font_support=document["font_support"] is True,
                profile_execution_bounds=json.loads(_canonical(profile_bounds)),
            )
        except (OSError, TypeError, ValueError, KeyError):
            return None

    def _enabled(self) -> dict[str, Any] | None:
        hardware_class = self._own_class()
        if hardware_class is None:
            return None
        document = self._record(self._ENABLED_KEYS, "enabled", f"{hardware_class}.json")
        try:
            if (document is None or type(document["intent_id"]) is not str
                    or str(uuid.UUID(document["intent_id"])) != document["intent_id"]
                    or self._measured_evidence(document, document["measured"]) is None):
                return None
        except (ValueError, TypeError, AttributeError):
            return None
        return document

    def snapshot(self) -> InstalledRenderAuthority | None:
        document = self._enabled()
        return self._authority(document, production=True) if document is not None else None

    def _intent(self) -> dict[str, Any] | None:
        intent = self._record(self._INTENT_KEYS, "qualify.json")
        try:
            if (
                intent is None
                or intent["format"] != "studio.render.qualification-intent.v1"
                or type(intent["intent_id"]) is not str
                or str(uuid.UUID(intent["intent_id"])) != intent["intent_id"]
                or type(intent["accept_before"]) is not int or intent["accept_before"] < 1
                or any(type(intent[name]) is not str or not 0 < len(intent[name]) <= 256
                       for name in ("selected_tier", "exact_encoder"))
                or type(intent["font_support"]) is not bool
                or type(intent["min_available_memory_bytes"]) is not int
                or intent["min_available_memory_bytes"] < 1
            ):
                return None
        except (TypeError, ValueError, AttributeError):
            return None
        return intent

    def _intent_document(self, intent: dict[str, Any]) -> dict[str, Any] | None:
        """Hub, not the operator, derives everything but the run settings from installed files."""
        hardware_class = self._own_class()
        if hardware_class is None:
            return None
        try:
            raw = {
                name: (self.root / filename).read_bytes()
                for name, filename in self._DESCRIPTOR_NAMES.items()
            }
            profile = validate_render_profile_descriptor(raw["profile"])
            tools = validate_render_tool_descriptor(raw["tools"])
            compiler = validate_render_compiler_descriptor(raw["compiler"])
            tool_document = decode_render_json(raw["tools"])
            profile_bounds = decode_render_json(raw["profile"])["execution_bounds"]
            document = {
                "format": "studio.render.qualification-receipt.v1",
                "production_eligible": False,
                "profile_digest": profile.digest, "tools_digest": tools.digest,
                "compiler_digest": compiler.digest, "hardware_class": hardware_class,
                "selected_tier": intent["selected_tier"],
                "exact_encoder": intent["exact_encoder"],
                "font_support": intent["font_support"],
                "bounds": {
                    "max_rss_bytes": profile_bounds["process_tree_rss_limit_bytes"],
                    "max_wall_seconds": profile_bounds["cumulative_wall_seconds"],
                    "min_available_memory_bytes": intent["min_available_memory_bytes"],
                    "output_limit_bytes": profile_bounds["output_limit_bytes"],
                },
                "files": {
                    name: {"size_bytes": tool_document[f"{name}_size_bytes"],
                           "sha256": tool_document[f"{name}_sha256"]}
                    for name in ("ffmpeg", "ffprobe")
                },
            }
        except (OSError, TypeError, ValueError, KeyError):
            return None
        document["qualification_digest"] = self._record_digest(document)
        return document

    def _candidate(
        self, packet: dict[str, Any] | None = None, *, open_only: bool = True,
    ) -> tuple[dict[str, Any], InstalledRenderAuthority] | None:
        intent = self._intent()
        if intent is None or (open_only and self._clock() >= intent["accept_before"]):
            return None
        document = self._intent_document(intent)
        authority = self._authority(document, packet) if document is not None else None
        return (intent, authority) if authority is not None else None

    def qualification_candidates(self) -> list[tuple[dict[str, Any], InstalledRenderAuthority]]:
        candidate = self._candidate()
        return [candidate] if candidate is not None else []

    def resolve(self, packet: dict[str, Any]) -> RuntimeQualification | None:
        enabled = self._enabled()
        production = (self._authority(enabled, packet, production=True)
                      if enabled is not None else None)
        if (production is not None
                and production.qualification.profile_digest == packet["recipe"]["profile_digest"]):
            return production.qualification
        # A qualification job may claim after its acceptance window closes; the one-use
        # intent was spent at acceptance under the permit, so only the installed evidence
        # and the intent's own settings are re-checked here.
        candidate = self._candidate(packet, open_only=False)
        return candidate[1].qualification if candidate is not None else None

    def authority_for(self, job: dict[str, Any]) -> InstalledRenderAuthority | None:
        qualification = job.get("qualification")
        if getattr(qualification, "document", None) is not None:
            # A claimed job runs on the exact document stored on its row, so revoking or
            # deleting the operator file never orphans it.
            return self._authority(
                qualification.document, production=qualification.production_eligible)
        # A candidate and the enabled record built from its receipt share one digest, so the
        # source is chosen by purpose: a production row never runs on a qualify.json candidate.
        if job.get("purpose") == "qualification":
            candidate = self._candidate(open_only=False)
            authority = candidate[1] if candidate is not None else None
        else:
            authority = self.snapshot()
        if (authority is not None and job.get("qualification_digest")
                == authority.qualification.qualification_digest):
            return authority
        return None

    def qualification_permit(
        self, vector_id: str, owner_scope: str, raw_request: bytes,
        descriptors: dict[str, dict[str, Any]],
    ) -> QualificationPermit | None:
        # The service already matched every asset descriptor to the packet manifest.
        del descriptors
        try:
            prelim = decode_render_json(raw_request)
            if type(prelim) is not dict or type(prelim.get("dispatch")) is not dict:
                return None
            value, packet, _packet_json = decode_render_request(
                raw_request, prelim["dispatch"].get("dispatch_key"))
            dispatch = value["dispatch"]
            candidate = self._candidate(packet)
            if candidate is None or candidate[0]["intent_id"] != vector_id:
                return None
            authority = candidate[1]
            return QualificationPermit(
                vector_id=vector_id, owner_scope=owner_scope,
                exact_request_sha256=hashlib.sha256(raw_request).hexdigest(),
                qualification_digest=authority.qualification.qualification_digest,
                hardware_class=authority.qualification.hardware_class,
                story_uuid=dispatch["story_uuid"], intent_id=dispatch["intent_id"],
                attempt_no=dispatch["attempt_no"],
                gateway_attempt_key=dispatch["gateway_attempt_key"],
                destination_site=dispatch["destination_site"],
                destination_controller=dispatch["destination_controller"],
                destination_node=dispatch["destination_node"],
                dispatch_key=dispatch["dispatch_key"], dispatch_fence=dispatch["dispatch_fence"],
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    @staticmethod
    def _receipt_name(
        hardware_class: str, intent_id: str, qualification_digest: str,
    ) -> str:
        """One retained receipt per intent; never one editable slot per hardware class."""
        name = f"{hardware_class}.{intent_id}.{qualification_digest}.json"
        if len(name) > 255:
            raise ValueError("qualification receipt name is too long")
        return name

    def write_receipt(
        self, document: dict[str, Any], intent_id: str, measured: dict[str, Any],
    ) -> bool:
        """Record one qualification run; never replaces an existing receipt."""
        if self.data_root is None:
            return False
        try:
            # production_eligible stays false (it is inside qualification_digest): the
            # coordinator copying this receipt into enabled/ is what grants production.
            if (set(document) != self._RECEIPT_KEYS
                    or document["qualification_digest"] != self._record_digest(document)
                    or type(intent_id) is not str
                    or str(uuid.UUID(intent_id)) != intent_id
                    or self._authority(document) is None
                    or self._measured_evidence(document, measured) is None):
                return False
            receipt = {key: document[key] for key in self._RECEIPT_KEYS}
            receipt.update(intent_id=intent_id, measured=dict(measured))
            render_authority_files.write_record_once(
                self.data_root, "receipts",
                self._receipt_name(
                    receipt["hardware_class"], intent_id,
                    receipt["qualification_digest"]),
                _canonical(receipt))
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return False
        return True


batches: dict[str, dict[str, Any]] = {}
_configured_executor = None


def configure(executor) -> None:
    global _configured_executor
    _configured_executor = executor


def capability() -> dict[str, Any] | None:
    return _configured_executor.capability() if _configured_executor is not None else None


def activity_batches() -> dict[str, dict[str, Any]]:
    result = dict(batches)
    if _configured_executor is not None:
        result.update(_configured_executor.activity_batches())
    return result


class RenderExecutor:
    def __init__(
        self, runtime, assets, authority, *, machine: str, broker_api,
        compile_plan=compile_final_render_packet,
        spawn=asyncio.create_subprocess_exec,
        output_validator=None,
        process_identity=None,
        kill_group=os.killpg,
        group_exists=_group_exists,
        available_memory=None,
        monotonic=time.monotonic,
        free_bytes=None,
        cancel_grace_seconds: float = 10.0,
        process_interruptible=None,
        recorded_process_state=None,
        shutdown_timeout: float | None = None,
    ) -> None:
        self.runtime = runtime
        self.assets = assets
        self._authority = authority
        self.machine = machine
        self.broker = broker_api
        self._compile = compile_plan
        self._spawn = spawn
        self._uses_builtin_validator = output_validator is None
        self._validate = output_validator or self._validate_output
        self._process_identity = process_identity or self._identity
        self._kill_group = kill_group
        self._group_exists = group_exists
        self._available_memory = available_memory or (
            lambda: host_memory.available_bytes(psutil.virtual_memory()))
        self._monotonic = monotonic
        self._free_bytes = free_bytes or (lambda path: shutil.disk_usage(path).free)
        self._cancel_grace_seconds = cancel_grace_seconds
        # Only a real child returned by asyncio's subprocess transport may be signalled
        # during shutdown; injected fake processes in tests are never OS-signalled.
        self._process_interruptible = process_interruptible or (
            lambda process: isinstance(process, asyncio.subprocess.Process))
        self._recorded_state = recorded_process_state or self._recorded_process_state
        self._shutdown_timeout = (
            max(5.0, cancel_grace_seconds + 5.0)
            if shutdown_timeout is None else shutdown_timeout)
        self._accepting = True
        self._tasks: set[asyncio.Task] = set()
        self._active: dict[
            str, tuple[Any, ProcessIdentity, RenderExecutionContext, Path]
        ] = {}
        self._settle_locks: dict[str, asyncio.Lock] = {}
        self._release_hook = None

    def attach_release_hook(self, release) -> None:
        """Attach the service-owned job-scoped asset release, if one exists.

        The hook is best-effort and runs only after the durable settle/terminal state is
        visible; an exception is swallowed and cannot change lifecycle decisions.
        """
        self._release_hook = release if callable(release) else None

    def _authority_for(self, job: dict[str, Any]) -> InstalledRenderAuthority | None:
        if hasattr(self._authority, "authority_for"):
            return self._authority.authority_for(job)
        try:
            return self._authority(job)
        except TypeError:
            return self._authority()

    def recover_and_restore_physical_holds(self) -> None:
        recover = getattr(self.runtime, "recover_incomplete_with_proof", None)
        rows = (
            recover(self._recorded_process_cannot_run)
            if callable(recover) else self.runtime.recover_incomplete())
        for row in rows:
            if row.get("leases", {}).get("compute") and row.get("machine"):
                self.broker.restore_external_machine(
                    row["machine"], f"render:{row['id']}")

    @staticmethod
    def _boot_changed_since(created_at: float) -> bool:
        """A row created before the current boot cannot own a live process."""
        if (isinstance(created_at, bool) or not isinstance(created_at, (int, float))
                or not math.isfinite(created_at) or created_at <= 0):
            return False
        try:
            return psutil.boot_time() > created_at + 1.0
        except (OSError, psutil.Error):
            return False

    def _recorded_process_cannot_run(
        self, identity: ProcessIdentity | None, created_at: float,
    ) -> bool:
        if self._boot_changed_since(created_at):
            return True
        return identity is not None and self._recorded_state(identity) == "gone"

    def _recorded_process_state(self, identity: ProcessIdentity) -> str:
        """Classify the exact recorded process as gone, live, or unproved.

        Only a leader whose pid, create time, executable and process group all match the
        durable identity is ``live``. A missing leader is ``gone`` only when its process
        group no longer exists; a surviving group (or a recycled pid) stays ``unknown``.
        """
        try:
            boot_raw, create_raw, executable = identity.start_token.split(":", 2)
            recorded_boot = float(boot_raw)
            recorded_create = float(create_raw)
            if not (math.isfinite(recorded_boot) and math.isfinite(recorded_create)):
                return "unknown"
            current_boot = psutil.boot_time()
            if current_boot > recorded_boot + 1.0:
                return "gone"
            if recorded_boot > current_boot + 1.0:
                return "unknown"
            observed = psutil.Process(identity.pid)
            same = (
                abs(observed.create_time() - recorded_create) <= 1e-3
                and Path(observed.exe()).resolve() == Path(executable).resolve()
            )
            if same:
                try:
                    if os.getpgid(identity.pid) == identity.process_group_id:
                        return "live"
                except OSError:
                    pass
                return "unknown"
        except (ValueError, OSError, psutil.NoSuchProcess):
            pass
        except psutil.Error:
            return "unknown"
        try:
            return "unknown" if self._group_exists(identity.process_group_id) else "gone"
        except (OSError, psutil.Error):
            return "unknown"

    async def _stop_recovered_exact_group(self, identity: ProcessIdentity) -> bool:
        """Signal one exact recorded group and prove it empty; never a pattern kill."""
        if self._recorded_state(identity) != "live":
            return False
        try:
            self._kill_group(identity.process_group_id, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except (OSError, psutil.Error):
            return False
        if await self._await_group_empty(
                identity.process_group_id, self._cancel_grace_seconds):
            return True
        # The leader may have exited while descendants remain. Only kill the group when
        # the exact leader is still proved alive; otherwise keep the honest unknown state.
        if self._recorded_state(identity) != "live":
            return False
        try:
            self._kill_group(identity.process_group_id, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except (OSError, psutil.Error):
            return False
        return await self._await_group_empty(identity.process_group_id, 2.0)

    async def _reconcile_uncertain(self, record: dict[str, Any]) -> dict[str, Any]:
        """Bounded reconciliation for a row whose process handle did not survive."""
        owner_scope = record["owner_scope"]
        job_id = record["id"]
        owner = f"render:{job_id}"
        pending = self.runtime.request_cancel_unsettled(owner_scope, job_id)
        if record.get("stopped_evidence_digest") is not None:
            settled = self.runtime.settle_recovered(
                owner_scope, job_id,
                proof="recovered: durable stop evidence proves the recorded process stopped")
            self._release_if_durably_settled(owner_scope, job_id, owner)
            return settled
        if self._boot_changed_since(record.get("created_at", 0)):
            settled = self.runtime.settle_recovered(
                owner_scope, job_id,
                proof="recovered: attempt predates the current boot and cannot still run")
            self._release_if_durably_settled(owner_scope, job_id, owner)
            return settled
        identity = record.get("process")
        if identity is None:
            return pending
        state = self._recorded_state(identity)
        if state == "gone":
            settled = self.runtime.settle_recovered(
                owner_scope, job_id,
                proof="recovered: recorded process cannot still run")
            self._release_if_durably_settled(owner_scope, job_id, owner)
            return settled
        if state == "live":
            try:
                stopped = await self._stop_recovered_exact_group(identity)
            except asyncio.CancelledError:
                raise
            except Exception:
                stopped = False
            if stopped:
                settled = self.runtime.settle_recovered(
                    owner_scope, job_id,
                    proof="recovered: exact owned process group proved stopped")
                self._release_if_durably_settled(owner_scope, job_id, owner)
                return settled
        return self.runtime.get(owner_scope, job_id) or pending

    def _admission_allowed(self, owner: str | None = None) -> bool:
        if not self._accepting:
            return False
        gate = getattr(self.broker, "render_dispatch_allowed", None)
        return bool(gate(self.machine, owner)) if callable(gate) else False

    async def dispatchable_candidate(self, machine: str):
        if machine != self.machine or not self._admission_allowed():
            return None
        for row in self.runtime.dispatchable_jobs():
            authority = self._authority_for(row)
            if authority is not None and self._has_memory(authority):
                created_at = row.get("created_at")
                if (type(created_at) not in (int, float)
                        or not math.isfinite(created_at) or created_at <= 0):
                    continue
                return float(created_at), row["id"]
        return None

    def pending_for_machine(self, machine: str) -> bool:
        if machine != self.machine or not self._admission_allowed():
            return False
        return any(
            (authority := self._authority_for(row)) is not None
            and self._has_memory(authority)
            for row in self.runtime.dispatchable_jobs()
        )

    def _has_memory(self, authority: InstalledRenderAuthority) -> bool:
        try:
            available = self._available_memory()
        except (OSError, psutil.Error):
            return False
        return (type(available) is int
                and available >= authority.bounds.min_available_memory_bytes)

    async def dispatch_ready(self) -> bool:
        candidate = await self.dispatchable_candidate(self.machine)
        if candidate is None:
            return False
        created_at, job_id = candidate
        row = next(row for row in self.runtime.dispatchable_jobs() if row["id"] == job_id)
        if await self.broker.higher_priority_work_waiting(
                machine=self.machine, modality="render", created_at=created_at,
                identity=f"render:{job_id}"):
            return False
        owner = f"render:{job_id}"
        if not self.broker.acquire_external_machine(self.machine, owner):
            return False
        claimed = False
        claim = None
        try:
            if not self._admission_allowed(owner):
                self.broker.release_external_machine(self.machine, owner)
                return False
            try:
                claim = self.runtime.claim_launch(
                    row["owner_scope"], job_id, machine=self.machine,
                    dispatch_generation=max(1, time.monotonic_ns()),
                )
            except RenderJobUnavailable:
                # Nothing was claimed: this one row is not ready, and it must never stop
                # the broker loop from dispatching other work.
                self.broker.release_external_machine(self.machine, owner)
                return False
            claimed = True
            context = self.runtime.execution_context(
                row["owner_scope"], job_id, claim["attempt_id"], claim["launch_token"])
            authority = self._authority_for({
                **row, "qualification_digest": context.qualification.qualification_digest,
                "qualification": context.qualification,
            })
            if authority is None:
                raise RuntimeError("installed render authority changed after durable claim")
            if not self._admission_allowed(owner):
                self.runtime.record_execution_failed(
                    row["owner_scope"], job_id, claim["attempt_id"],
                    claim["launch_token"], "prelaunch_failed")
                self._release_if_durably_settled(row["owner_scope"], job_id, owner)
                return False
            task = asyncio.create_task(self._run(context, authority, owner))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            return True
        except BaseException:
            if not claimed:
                self.broker.release_external_machine(self.machine, owner)
            else:
                try:
                    self.runtime.record_execution_failed(
                        row["owner_scope"], job_id, claim["attempt_id"],
                        claim["launch_token"], "prelaunch_failed")
                except BaseException:
                    pass
                self._release_if_durably_settled(row["owner_scope"], job_id, owner)
            raise

    @staticmethod
    def _identity(process, executable: Path) -> ProcessIdentity:
        observed = psutil.Process(process.pid)
        if Path(observed.exe()).resolve() != executable.resolve():
            raise RuntimeError("render process executable identity changed")
        process_group_id = os.getpgid(process.pid)
        if process_group_id != process.pid:
            raise RuntimeError("render process does not own its isolated process group")
        return ProcessIdentity(
            pid=process.pid, process_group_id=process_group_id,
            start_token=f"{psutil.boot_time()}:{observed.create_time()}:{executable}",
        )

    def _same_process(
        self, process: Any, identity: ProcessIdentity, executable: Path,
    ) -> bool:
        try:
            return self._process_identity(process, executable) == identity
        except (OSError, RuntimeError, psutil.Error):
            return False

    async def _await_group_empty(self, process_group_id: int, timeout: float = 2.0) -> bool:
        deadline = time.monotonic() + timeout
        while self._group_exists(process_group_id) and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        return not self._group_exists(process_group_id)

    async def _stop_exact_group(
        self, process: Any, identity: ProcessIdentity, executable: Path,
    ) -> int | None:
        if getattr(process, "returncode", None) is not None:
            if self._group_exists(identity.process_group_id):
                return None
            return int(await process.wait())
        if not self._same_process(process, identity, executable):
            return None
        try:
            self._kill_group(identity.process_group_id, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            exit_code = await asyncio.wait_for(
                asyncio.shield(process.wait()), timeout=self._cancel_grace_seconds)
        except asyncio.TimeoutError:
            try:
                self._kill_group(identity.process_group_id, signal.SIGKILL)
            except ProcessLookupError:
                pass
            exit_code = await process.wait()
        if self._group_exists(identity.process_group_id):
            try:
                self._kill_group(identity.process_group_id, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if not await self._await_group_empty(identity.process_group_id):
            return None
        return int(exit_code)

    def _release_if_durably_settled(
        self, owner_scope: str, job_id: str, physical_owner: str,
    ) -> bool:
        try:
            public = self.runtime.get(owner_scope, job_id)
        except BaseException:
            return False
        releasable = (
            type(public) is dict
            and public.get("state") in {"cancelled", "failed", "output_ready"}
            and type(public.get("leases")) is dict
            and public["leases"].get("compute") is False
        )
        if releasable:
            self.broker.release_external_machine(self.machine, physical_owner)
            hook = self._release_hook
            if hook is not None:
                try:
                    hook(owner_scope, job_id)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # Asset-retirement bookkeeping never changes a durable lifecycle result.
                    pass
        return releasable

    async def _settle_shutdown_process(
        self, process: Any, identity: ProcessIdentity,
        context: RenderExecutionContext, executable: Path,
    ) -> bool:
        """Stop one exact owned group and record its stop before any release."""
        exit_code = await self._stop_exact_group(process, identity, executable)
        if exit_code is None:
            return False
        async with self._settle_locks.setdefault(
                context.hub_job_id, asyncio.Lock()):
            public = self.runtime.get(context.owner_scope, context.hub_job_id)
            if type(public) is not dict:
                return False
            if public.get("state") in {"cancelled", "failed", "output_ready", "stopped"}:
                return True
            if public.get("state") not in {"rendering", "cancel_requested", "uncertain"}:
                return False
            self.runtime.record_process_stopped(
                context.owner_scope, context.hub_job_id, context.attempt_id,
                StoppedProcessEvidence(
                    process=identity, observed_at=time.time(), exit_code=exit_code,
                    reason=(
                        "cancelled" if public.get("cancel_requested_at") is not None
                        else "failed")),
            )
        return True

    async def _run(self, context: RenderExecutionContext,
                   authority: InstalledRenderAuthority, owner: str) -> None:
        process = None
        identity = None
        spawn_invoked = False
        main_stopped = False
        started = self._monotonic()
        attempt_deadline = started + authority.bounds.max_wall_seconds
        measured: dict[str, Any] = {"peak_rss_bytes": 0, "min_free_bytes": None}
        try:
            with ExitStack() as stack:
                readers = {
                    logical_id: _asset_stream(stack.enter_context(
                        self.assets.open_reader(context.owner_scope, handle)))
                    for logical_id, handle in context.assets.items()
                }
                local_assets = {
                    logical_id: f"/dev/fd/{reader.fileno()}"
                    for logical_id, reader in readers.items()
                }
                packet_raw = _canonical(context.packet)
                plan = self._compile(packet_raw, local_assets, context.output_path)
                tier = next(
                    (tier for tier in plan.tiers if tier.label == authority.selected_tier), None)
                if tier is None or authority.exact_encoder not in tier.argv:
                    raise RuntimeError("qualified render tier is not in the compiled plan")
                if tier.drops_burned_in_features:
                    raise RuntimeError("qualified render tier would drop requested features")
                confined = _confined_argv(
                    tuple(tier.argv), context.packet, local_assets, readers)
                argv = (
                    str(authority.ffmpeg), "-nostdin", *confined,
                )
                command_digest = hashlib.sha256(_canonical(argv)).hexdigest()
                if not self._admission_allowed(owner):
                    raise RuntimeError("render admission closed before process launch")
                spawn_invoked = True
                process = await self._spawn(
                    *argv, start_new_session=True,
                    pass_fds=tuple(reader.fileno() for reader in readers.values()),
                    cwd=context.workspace,
                    env={"PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8"},
                )
                identity = self._process_identity(process, authority.ffmpeg)
                self.runtime.record_process_started(
                    context.owner_scope, context.hub_job_id, context.attempt_id,
                    context.launch_token, identity)
                self._active[context.hub_job_id] = (
                    process, identity, context, authority.ffmpeg)
                if not self._accepting and self._process_interruptible(process):
                    if not await self._settle_shutdown_process(
                            process, identity, context, authority.ffmpeg):
                        raise _UncertainProcessOutcome(
                            "render process could not be proved stopped during shutdown")
                    return
                public = self.runtime.get(context.owner_scope, context.hub_job_id)
                if type(public) is dict and public.get("cancel_requested_at") is not None:
                    await self.request_cancel(context.owner_scope, context.hub_job_id)
                    return
                exit_code, limit_failure = await self._wait_with_bounds(
                    process, identity, context, authority, attempt_deadline,
                    measured=measured)
                if exit_code is None:
                    return
                async with self._settle_locks.setdefault(
                        context.hub_job_id, asyncio.Lock()):
                    public = self.runtime.get(context.owner_scope, context.hub_job_id)
                    if type(public) is not dict:
                        return
                    if public.get("state") in {"cancelled", "failed", "output_ready"}:
                        return
                    cancelled = public.get("cancel_requested_at") is not None
                    reason = (
                        "cancelled" if cancelled else
                        "failed" if exit_code != 0 or limit_failure else "completed"
                    )
                    stopped = self.runtime.record_process_stopped(
                        context.owner_scope, context.hub_job_id, context.attempt_id,
                        StoppedProcessEvidence(
                            process=identity, observed_at=time.time(),
                            exit_code=exit_code, reason=reason),
                        validation_pending=reason == "completed",
                    )
                if stopped.get("state") != "stopped":
                    return
                main_stopped = True
                if not self._accepting and self._process_interruptible(process):
                    self.runtime.record_execution_failed(
                        context.owner_scope, context.hub_job_id, context.attempt_id,
                        context.launch_token, "output_validation_failed")
                    return
                receipt = (
                    self._validate(
                        context, authority, command_digest,
                        attempt_deadline=attempt_deadline)
                    if self._uses_builtin_validator
                    else self._validate(context, authority, command_digest)
                )
                if inspect.isawaitable(receipt):
                    receipt = await receipt
                if type(receipt) is not ValidatedOutputReceipt:
                    raise TypeError("render validator did not return a trusted receipt")
                public = self.runtime.get(context.owner_scope, context.hub_job_id)
                if type(public) is not dict:
                    return
                if public.get("cancel_requested_at") is not None:
                    self.runtime.record_execution_failed(
                        context.owner_scope, context.hub_job_id, context.attempt_id,
                        context.launch_token, "output_validation_failed")
                    return
                if public.get("state") != "stopped":
                    return
                if not self._record_qualification(context, receipt, measured, started):
                    # A consumed one-use intent must never end with a silently missing
                    # receipt: settle the row failed and do not publish output_ready.
                    self.runtime.record_execution_failed(
                        context.owner_scope, context.hub_job_id, context.attempt_id,
                        context.launch_token, "output_validation_failed")
                    return
                self.runtime.acquire_file_lease(
                    context.owner_scope, context.hub_job_id, "ack",
                    f"output:{context.attempt_id}")
                self.runtime.publish_output(context.owner_scope, context.hub_job_id, receipt)
        except BaseException as error:
            phase = (
                None if isinstance(error, _UncertainProcessOutcome) else
                "output_validation_failed" if main_stopped else
                "prelaunch_failed" if not spawn_invoked else None
            )
            if phase is not None:
                try:
                    self.runtime.record_execution_failed(
                        context.owner_scope, context.hub_job_id, context.attempt_id,
                        context.launch_token, phase)
                except BaseException:
                    pass
            raise
        finally:
            self._active.pop(context.hub_job_id, None)
            self._release_if_durably_settled(
                context.owner_scope, context.hub_job_id, owner)

    def _record_qualification(
        self, context: RenderExecutionContext, receipt: ValidatedOutputReceipt,
        measured: dict[str, Any], started: float,
    ) -> bool:
        """Leave one durable receipt before a qualification output is published."""
        permit = context.qualification_permit
        document = context.qualification.document
        writer = getattr(self._authority, "write_receipt", None)
        if context.purpose != "qualification":
            return True
        if permit is None or document is None or writer is None:
            return False
        wall_seconds = math.ceil(self._monotonic() - started)
        if wall_seconds > document["bounds"]["max_wall_seconds"]:
            # Never clamp measured evidence: a run whose post-validation wall exceeds its
            # declared bound is an honest failed qualification, not a shortened receipt.
            return False
        return writer(document, permit.vector_id, {
            "peak_rss_bytes": measured["peak_rss_bytes"],
            "wall_seconds": wall_seconds,
            "min_free_bytes": measured["min_free_bytes"],
            "output_bytes": receipt.output_size_bytes,
            "hub_job_id": context.hub_job_id,
            "receipt_digest": receipt.receipt_digest,
        }) is True

    @staticmethod
    def _process_tree_rss(process_id: int) -> int | None:
        try:
            root = psutil.Process(process_id)
            members = [root, *root.children(recursive=True)]
            return sum(member.memory_info().rss for member in members)
        except psutil.NoSuchProcess:
            return 0
        except (OSError, psutil.Error):
            return None

    async def _wait_with_bounds(
        self, process: Any, identity: ProcessIdentity,
        context: RenderExecutionContext, authority: InstalledRenderAuthority,
        attempt_deadline: float, *, measured: dict[str, Any] | None = None,
    ) -> tuple[int | None, bool]:
        wait = asyncio.create_task(process.wait())
        violation = False
        while True:
            await asyncio.wait({wait}, timeout=0.25)
            rss = self._process_tree_rss(identity.pid)
            try:
                output_size = Path(context.output_path).stat().st_size
            except FileNotFoundError:
                output_size = 0
            except OSError:
                output_size = authority.bounds.output_limit_bytes + 1
            try:
                free = self._free_bytes(Path(context.workspace))
            except OSError:
                free = -1
            if measured is not None and type(rss) is int and type(free) is int:
                measured["peak_rss_bytes"] = max(measured["peak_rss_bytes"], rss)
                measured["min_free_bytes"] = (
                    free if measured["min_free_bytes"] is None
                    else min(measured["min_free_bytes"], free))
            violation = (
                self._monotonic() >= attempt_deadline
                or rss is None or rss > authority.bounds.max_rss_bytes
                or output_size > authority.bounds.output_limit_bytes
                or type(free) is not int
                or free < authority.qualification.min_free_bytes
            )
            if violation:
                exit_code = await self._stop_exact_group(
                    process, identity, authority.ffmpeg)
                if exit_code is None:
                    return None, True
                return exit_code, True
            if wait.done():
                break
        exit_code = int(await wait)
        if self._group_exists(identity.process_group_id):
            try:
                self._kill_group(identity.process_group_id, signal.SIGKILL)
            except ProcessLookupError:
                pass
            if not await self._await_group_empty(identity.process_group_id):
                return None, True
            violation = True
        return exit_code, violation

    @staticmethod
    async def _read_probe_stream(stream: Any, limit: int, *, tail: bool) -> bytes:
        if stream is None or not callable(getattr(stream, "read", None)):
            raise RuntimeError("pinned ffprobe did not expose bounded output streams")
        collected = bytearray()
        while True:
            chunk = await stream.read(64 * 1024)
            if not chunk:
                return bytes(collected)
            if tail:
                collected.extend(chunk)
                if len(collected) > limit:
                    del collected[:-limit]
            else:
                if len(collected) + len(chunk) > limit:
                    raise RuntimeError("pinned ffprobe metadata exceeded its qualified bound")
                collected.extend(chunk)

    @staticmethod
    def _stream_duration(stream: dict[str, Any]) -> float:
        try:
            if stream.get("duration") is not None:
                duration = float(stream["duration"])
            elif stream.get("duration_ts") is not None and stream.get("time_base") is not None:
                duration = float(Fraction(str(stream["duration_ts"]))
                                 * Fraction(str(stream["time_base"])))
            elif stream.get("codec_type") == "video" and stream.get("nb_frames") is not None:
                duration = float(Fraction(str(stream["nb_frames"]))
                                 / Fraction(str(stream["avg_frame_rate"])))
            elif (stream.get("codec_type") == "audio"
                  and stream.get("nb_samples") is not None
                  and stream.get("sample_rate") is not None):
                duration = float(stream["nb_samples"]) / float(stream["sample_rate"])
            else:
                raise ValueError("missing duration")
        except (KeyError, TypeError, ValueError, ZeroDivisionError, OverflowError) as exc:
            raise RuntimeError("render output media lacks qualified timing evidence") from exc
        if not math.isfinite(duration) or duration <= 0:
            raise RuntimeError("render output media lacks qualified timing evidence")
        return duration

    async def _validate_output(
        self, context: RenderExecutionContext, authority: InstalledRenderAuthority,
        command_digest: str, *, attempt_deadline: float | None = None,
        metadata_limit_bytes: int = 1024 * 1024,
        diagnostic_tail_bytes: int = 64 * 1024,
    ) -> ValidatedOutputReceipt:
        output = Path(context.output_path)
        size, output_sha = _sha(output)
        if size < 1 or size > authority.bounds.output_limit_bytes:
            raise RuntimeError("render output size is outside qualified bounds")
        deadline = (
            self._monotonic() + authority.bounds.max_wall_seconds
            if attempt_deadline is None else attempt_deadline)
        try:
            probe = await self._spawn(
                str(authority.ffprobe), "-v", "error", "-show_streams", "-show_format",
                "-of", "json", "-protocol_whitelist", "file,pipe", str(output),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                start_new_session=True, cwd=context.workspace,
                env={"PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8"},
            )
        except BaseException as exc:
            raise _UncertainProcessOutcome(
                "pinned ffprobe spawn was invoked without a returned handle") from exc
        try:
            probe_identity = self._process_identity(probe, authority.ffprobe)
        except BaseException as exc:
            raise _UncertainProcessOutcome(
                "pinned ffprobe process identity is unproved") from exc
        self._active[context.hub_job_id] = (
            probe, probe_identity, context, authority.ffprobe)
        wait = asyncio.create_task(probe.wait())
        stdout_read = asyncio.create_task(self._read_probe_stream(
            getattr(probe, "stdout", None), metadata_limit_bytes, tail=False))
        stderr_read = asyncio.create_task(self._read_probe_stream(
            getattr(probe, "stderr", None), diagnostic_tail_bytes, tail=True))
        tasks = {wait, stdout_read, stderr_read}
        try:
            while not all(task.done() for task in tasks):
                await asyncio.wait(tasks, timeout=0.05)
                for task in tasks:
                    if task.done() and not task.cancelled() and task.exception() is not None:
                        raise task.exception()
                rss = self._process_tree_rss(probe_identity.pid)
                try:
                    free = self._free_bytes(Path(context.workspace))
                except OSError:
                    free = -1
                if self._monotonic() >= deadline:
                    raise RuntimeError("render attempt exceeded its cumulative wall time")
                if rss is None or rss > authority.bounds.max_rss_bytes:
                    raise RuntimeError("pinned ffprobe exceeded qualified memory")
                if type(free) is not int or free < authority.qualification.min_free_bytes:
                    raise RuntimeError("pinned ffprobe violated the qualified scratch reserve")
            stdout = stdout_read.result()
            exit_code = int(wait.result())
        except BaseException as exc:
            if await self._stop_exact_group(
                    probe, probe_identity, authority.ffprobe) is None:
                raise _UncertainProcessOutcome(
                    "pinned ffprobe process outcome is uncertain") from exc
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        if self._group_exists(probe_identity.process_group_id):
            try:
                self._kill_group(probe_identity.process_group_id, signal.SIGKILL)
            except ProcessLookupError:
                pass
            if not await self._await_group_empty(probe_identity.process_group_id):
                raise _UncertainProcessOutcome(
                    "pinned ffprobe descendants did not settle")
        if exit_code != 0:
            raise RuntimeError("pinned ffprobe rejected render output")
        try:
            document = json.loads(stdout)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("pinned ffprobe returned invalid bounded metadata") from exc
        streams = document.get("streams") if isinstance(document, dict) else None
        if (type(streams) is not list or len(streams) != 2
                or any(type(row) is not dict for row in streams)):
            raise RuntimeError("render output media has unsupported stream cardinality")
        video_rows = [row for row in streams if row.get("codec_type") == "video"]
        audio_rows = [row for row in streams if row.get("codec_type") == "audio"]
        if len(video_rows) != 1 or len(audio_rows) != 1:
            raise RuntimeError("render output media has unsupported stream cardinality")
        video, audio = video_rows[0], audio_rows[0]
        recipe = context.packet["recipe"]
        canvas = recipe["canvas"]
        try:
            fps = Fraction(str(video["avg_frame_rate"]))
            duration = float((document.get("format") or {})["duration"])
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            raise RuntimeError("render output media lacks qualified timing evidence")
        video_duration = self._stream_duration(video)
        audio_duration = self._stream_duration(audio)
        if not math.isfinite(duration) or duration <= 0:
            raise RuntimeError("render output media lacks qualified timing evidence")
        duration_tolerance = max(0.1, 2 / canvas["working_fps"])
        media_matches = (
            video.get("codec_name") == "h264"
            and audio.get("codec_name") == "aac"
            and video.get("width") == canvas["width"]
            and video.get("height") == canvas["height"]
            and fps == canvas["working_fps"]
            and video.get("pix_fmt") == "yuv420p"
            and abs(duration - recipe["target_duration_sec"]) <= duration_tolerance
            and abs(video_duration - recipe["target_duration_sec"]) <= duration_tolerance
            and abs(audio_duration - recipe["target_duration_sec"]) <= duration_tolerance
            and abs(video_duration - audio_duration) <= duration_tolerance
        )
        if not media_matches:
            raise RuntimeError("render output media does not match the qualified profile")
        if _sha(output) != (size, output_sha):
            raise RuntimeError("render output changed during validation")
        fields = {
            "owner_scope": context.owner_scope, "hub_job_id": context.hub_job_id,
            "attempt_id": context.attempt_id,
            "accepted_request_sha256": context.accepted_request_sha256,
            "output_sha256": output_sha, "output_size_bytes": size,
            "profile_digest": authority.qualification.profile_digest,
            "tools_digest": authority.qualification.tools_digest,
            "compiler_digest": authority.qualification.compiler_digest,
            "qualification_digest": authority.qualification.qualification_digest,
            "generated_command_digest": command_digest,
            "selected_tier": authority.selected_tier, "feature_losses": (),
        }
        receipt_digest = hashlib.sha256(_canonical({
            **fields, "feature_losses": [],
        })).hexdigest()
        return ValidatedOutputReceipt(receipt_digest=receipt_digest, **fields)

    async def request_cancel(self, owner_scope: str, job_id: str) -> dict[str, Any]:
        owner = f"render:{job_id}"
        active = self._active.get(job_id)
        if active is None:
            record = getattr(self.runtime, "recovery_record", None)
            recovery = record(owner_scope, job_id) if callable(record) else None
            if (type(recovery) is dict and recovery.get("compute_held")
                    and recovery.get("state") in {
                        "launch_pending", "rendering", "cancel_requested",
                        "uncertain", "stopped"}):
                return await self._reconcile_uncertain(recovery)
            row = self.runtime.request_cancel(owner_scope, job_id)
            self._release_if_durably_settled(owner_scope, job_id, owner)
            return row
        row = self.runtime.request_cancel_pending(owner_scope, job_id)
        process, identity, context, executable = active
        if not self._same_process(process, identity, executable):
            return row
        exit_code = await self._stop_exact_group(process, identity, executable)
        if exit_code is None:
            return row
        async with self._settle_locks.setdefault(job_id, asyncio.Lock()):
            current = self.runtime.get(owner_scope, job_id)
            if (type(current) is dict and current.get("leases", {}).get("compute")
                    and current.get("state") in {"rendering", "cancel_requested", "uncertain"}):
                if current.get("stopped_evidence_digest") is not None:
                    self.runtime.record_execution_failed(
                        owner_scope, job_id, context.attempt_id,
                        context.launch_token, "output_validation_failed")
                else:
                    self.runtime.record_process_stopped(
                        owner_scope, job_id, context.attempt_id,
                        StoppedProcessEvidence(
                            process=identity, observed_at=time.time(), exit_code=exit_code,
                            reason="cancelled"),
                    )
        self._release_if_durably_settled(owner_scope, job_id, owner)
        return self.runtime.get(owner_scope, job_id)

    def begin_shutdown(self) -> None:
        self._accepting = False

    async def stop(self) -> None:
        """Signal every exact owned process group, prove stop, then drain settlement.

        A shutdown never waits for the render wall-time bound. Each live owned group is
        signalled through its exact identity; its task records durable stop evidence and
        releases the physical hold only through the normal settlement path. A group that
        cannot be proved stopped stays uncertain and keeps its compute hold.
        """
        self.begin_shutdown()
        deadline = self._monotonic() + self._shutdown_timeout
        while True:
            for process, identity, _context, executable in tuple(self._active.values()):
                if (not self._process_interruptible(process)
                        or getattr(process, "returncode", None) is not None):
                    continue
                try:
                    await self._stop_exact_group(process, identity, executable)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # The task will observe the same unproved outcome and keep the hold.
                    continue
            if not self._tasks:
                return
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                return
            await asyncio.wait(
                tuple(self._tasks), timeout=min(0.1, max(0.0, remaining)))

    def activity_batches(self) -> dict[str, dict[str, Any]]:
        jobs = (
            self.runtime.activity_jobs()
            if hasattr(self.runtime, "activity_jobs") else self.runtime.dispatchable_jobs()
        )
        return {
            f"render:{row['id']}": {
                "id": row["id"], "model": "final-render", "modality": "render",
                "created_at": row.get("created_at", 0), "items": [{
                    "id": row["id"], "state": row["state"], "machine": row.get("machine"),
                    "operation": "video.final_render",
                }],
            }
            for row in jobs
        }

    def capability(self) -> dict[str, Any]:
        authority = (
            self._authority.snapshot()
            if hasattr(self._authority, "snapshot") else self._authority_for({})
        )
        admission_allowed = self._admission_allowed()
        busy = self.machine in self.broker.busy_machines()
        # An enabled authority record for this Mac's hardware class is production.
        ready = bool(
            authority
            and self._has_memory(authority)
            and admission_allowed
            and not busy
        )
        qualification_candidates = []
        consumed = getattr(self.runtime, "qualification_consumed", None)
        if hasattr(self._authority, "qualification_candidates") and consumed is not None:
            for intent, candidate in self._authority.qualification_candidates():
                # The loader only returns an intent still inside its accept_before window;
                # a spent intent is never advertised again.
                if consumed(intent["intent_id"]):
                    continue
                available = self._has_memory(candidate) and admission_allowed and not busy
                qualification_candidates.append({
                    "vector_id": intent["intent_id"],
                    "hardware_class": candidate.qualification.hardware_class,
                    "profile_digest": candidate.qualification.profile_digest,
                    "tools_digest": candidate.qualification.tools_digest,
                    "compiler_digest": candidate.qualification.compiler_digest,
                    "qualification_digest": candidate.qualification.qualification_digest,
                    "selected_tier": candidate.selected_tier,
                    "exact_encoder": candidate.exact_encoder,
                    "font_support": candidate.font_support,
                    "bounds": asdict(candidate.bounds),
                    "profile_execution_bounds": json.loads(
                        _canonical(candidate.profile_execution_bounds)),
                    "production_eligible": False,
                    "qualification_only": True,
                    "available_capacity": {
                        "slots": int(available), "slots_total": 1,
                    },
                })
        return {
            "operation": "video.final_render",
            "physical_machine_id": self.machine,
            "hardware_class": (
                authority.qualification.hardware_class if authority else None),
            "profile_digest": (
                authority.qualification.profile_digest if authority else None),
            "tools_digest": (
                authority.qualification.tools_digest if authority else None),
            "compiler_digest": (
                authority.qualification.compiler_digest if authority else None),
            "qualification_digest": (
                authority.qualification.qualification_digest if authority else None),
            "selected_tier": authority.selected_tier if authority else None,
            "exact_encoder": authority.exact_encoder if authority else None,
            "font_support": authority.font_support if authority else False,
            "bounds": asdict(authority.bounds) if authority else None,
            "profile_execution_bounds": (
                json.loads(_canonical(authority.profile_execution_bounds))
                if authority else None),
            "production_eligible": bool(
                authority and authority.qualification.production_eligible),
            "production_enabled": bool(authority),
            "available_capacity": {"slots": int(ready), "slots_total": 1},
            "qualification_candidates": qualification_candidates,
        }
