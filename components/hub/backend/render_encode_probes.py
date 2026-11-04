"""Durable, bounded, exact-tuple encoder probe control for the Hub.

One probe answers one question: can this Mac, with these pinned tools, encode this exact
six-field tuple? The pure helper (render_encode_probe) owns the argv and the measurement;
this module owns everything around it - the durable record, the physical hold, the
admission policy, the bounds and the evidence.

The rules this module keeps:

* A probe is adopted by its identity before anything is spawned. The identity is the exact
  tuple plus the node, the hardware facts, the installed descriptor digests and the probe
  vector, so a changed tool, a changed compiler or a changed vector is a different probe.
* One POST never runs two probes for one identity. A second POST for an identity that is
  already recorded adopts that record instead.
* A crash keeps the record pending and keeps the physical hold. A probe whose outcome cannot
  be proved is never released, and no process is ever signalled on a guess.
* Evidence exists only after a real encode that the pinned ffprobe validated. Nothing here
  invents a measurement, and a failed probe is never support.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import threading
import time
from typing import Any, Callable, Mapping, Sequence

from . import render_authority_files
from .render_encode_probe import (
    EncodeProbeError,
    ProbeRun,
    UnsupportedEncodeProbe,
    plan_encode_probe,
    probe_encode_support,
)
from .render_hash_v1 import decode_render_json
from .render_jobs import ProcessIdentity

STATE_FORMAT = "studio.render.encode-probe-state.v1"
EVIDENCE_FORMAT = "studio.render.encode-probe-evidence.v1"
RECORD_FORMAT = "studio.render.encode-probe-record.v1"
RESULT_FORMAT = "studio.render.encode-probe-result.v1"
LAUNCH_FORMAT = "studio.render.encode-probe-launch.v1"
PROOF_FORMAT = "studio.render.encode-probe-proof.v1"

DIRECTORY = "probes"
EVIDENCE_DIRECTORY = "probe-evidence"
PURPOSE = "encode_support_only"

# The package-owned probe vector: fixed canvas, frame rate, duration and validation
# version. Its digest is part of the probe identity, so a different vector is a different
# probe and no earlier evidence carries over.
PROBE_VECTOR = {
    "canvas": [1920, 1080],
    "fps": 24,
    "duration_sec": 2.0,
    "validation_version": "studio.render.encode-probe-vector.v1",
}

# Bounded so a record always fits the strict reader's private record size.
MAX_RECORD_BYTES = render_authority_files.MAX_RECORD_BYTES

# Bounded process limits for one probe. The wall bound is the pinned profile's own
# cumulative wall time; the rest are probe-local and never exceed it.
_PROBE_OUTPUT_LIMIT_BYTES = 64 * 1024 * 1024
_PROBE_STDIO_LIMIT_BYTES = 64 * 1024
_PROBE_MAX_WALL_SECONDS = 600.0

_ENCODER_KEYS = (
    "container", "requested_codec", "requested_effort", "crf",
    "quality_cpu_preset", "bitrate_kbps",
)

_STATE_KEYS = (
    "format", "requested_encode", "state", "probe_key", "reason", "evidence",
    "authority_digest",
)
_EVIDENCE_KEYS = {
    "format", "requested_encode", "node_identity", "hardware_identity",
    "profile_digest", "tools_digest", "compiler_digest", "selected_tier",
    "exact_encoder", "probe_vector_digest", "probe_result_digest", "purpose",
}
_RECORD_KEYS = {
    "format", "probe_key", "requested_encode", "identity", "owner", "created_at",
}

_STATES = ("supported", "unsupported", "pending", "unmeasured")


# The reasons that are the encoder's own answer: this Mac cannot encode this tuple.
# Every other failure is unknown, and an unknown outcome is never a refusal.
_CAPABILITY_REFUSALS = frozenset((
    "the encoder exited with a non-zero code",
    "the encoder produced no regular output file",
    "the encoder produced an empty output file",
    "the encoded clip must have exactly two streams",
    "the encoded video is not the expected codec",
    "the encoded video is not the expected pixel format",
    "the encoded audio is not the expected codec",
    "the encoded video is not the expected canvas",
    "the encoded video has no usable frame rate",
    "the encoded video has no duration",
    "the encoded audio has no duration",
    "the encoded file is not in the expected container",
))


def _is_capability_refusal(reason: str) -> bool:
    """Whether this reason is the encoder's own refusal rather than an unknown."""
    return reason in _CAPABILITY_REFUSALS


def _canonical(value: Any) -> bytes:
    """The canonical bytes both apps hash: sorted keys, compact separators, UTF-8."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _is_digest(value: Any) -> bool:
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _is_text(value: Any, maximum: int = 256) -> bool:
    return (type(value) is str and 0 < len(value) <= maximum
            and value.isascii() and value.isprintable())


def _closed_encode(value: Any) -> dict[str, Any] | None:
    """The exact six-field encode, or None. Nothing else is ever a tuple here."""
    if type(value) is not dict or set(value) != set(_ENCODER_KEYS):
        return None
    for name in ("container", "requested_codec", "requested_effort",
                 "quality_cpu_preset"):
        if type(value[name]) is not str or not value[name]:
            return None
    if type(value["crf"]) is not int:
        return None
    if value["bitrate_kbps"] is not None and type(value["bitrate_kbps"]) is not int:
        return None
    return {name: value[name] for name in _ENCODER_KEYS}


class EncodeProbeControl:
    """Durable probe control for the exact tuples this node can encode.

    The control owns the durable record under ``<data root>/render-authority/probes/``,
    the physical hold on this Mac, and the evidence it publishes. It never writes
    anywhere else, and it never writes on a read.
    """

    def __init__(
        self,
        data_root: str | Path,
        *,
        loader: Any,
        executor: Any,
        node_id: Callable[[], str],
        hardware_identity: Callable[[], dict[str, Any]],
        machine: str = "local",
        admission_allowed: Callable[[], bool] | None = None,
        runner: Callable[[Sequence[str], float, Path,
                          Callable[[ProcessIdentity], None] | None], ProbeRun] | None = None,
        expected_uid: int | None = None,
        clock: Callable[[], float] = time.time,
        scratch_root: str | Path | None = None,
    ) -> None:
        self.data_root = Path(os.path.abspath(data_root))
        self.loader = loader
        self.executor = executor
        self._node_id = node_id
        self._hardware_source = hardware_identity
        self.machine = machine
        self._clock = clock
        self._uid = os.getuid() if expected_uid is None else expected_uid
        self._lock = threading.Lock()
        self._runner_callable = runner
        self._rss_limit: int | None = None
        self._output_limit: int | None = None
        self._scratch_root = (
            None if scratch_root is None else Path(os.path.abspath(scratch_root)))
        # The probe honours the real pause of this Mac, whatever the render exemption
        # says: a paused Mac runs no generation here, and this is generation.
        self._admission_allowed = admission_allowed

    # --- identity -----------------------------------------------------------------------

    def _node(self) -> str:
        try:
            value = self._node_id()
        except (OSError, TypeError, ValueError):
            return ""
        return value if _is_text(value) else ""

    def _hardware(self) -> dict[str, Any]:
        """The observed machine facts the evidence names. Never a caller assertion."""
        try:
            raw = self._hardware_source()
        except (OSError, TypeError, ValueError, KeyError):
            return {}
        if type(raw) is not dict:
            return {}
        chip = raw.get("chip")
        memory_gb = raw.get("memory_gb")
        facts = {
            "chip": chip if type(chip) is str and chip else "",
            "memory_bytes": 0,
        }
        try:
            import psutil
            facts["memory_bytes"] = int(psutil.virtual_memory().total)
        except (ImportError, OSError, Exception):  # noqa: BLE001 - a fact may be absent
            facts["memory_bytes"] = (
                int(memory_gb) * 1024 ** 3 if type(memory_gb) is int else 0)
        return facts

    def _hardware_identity(self) -> dict[str, Any]:
        """The closed hardware block the evidence carries."""
        facts = self._hardware()
        hardware_class = ""
        try:
            authority = self.loader.snapshot()
        except (OSError, TypeError, ValueError, KeyError):
            authority = None
        if authority is not None:
            hardware_class = getattr(authority.qualification, "hardware_class", "") or ""
        return {
            "hardware_class": hardware_class,
            "chip": facts["chip"],
            "memory_bytes": facts["memory_bytes"],
            "backend": "",
        }

    def _descriptor_digests(self) -> tuple[str, str, str] | None:
        """The installed descriptor identities, or None when there is no authority.

        The digests come from the loader's own authority: the probe runs the pinned
        tools, so it may only name the digests of the tools that are actually pinned.
        """
        try:
            authority = self.loader.snapshot()
        except (OSError, TypeError, ValueError, KeyError):
            return None
        if authority is None:
            return None
        qualification = getattr(authority, "qualification", None)
        digests = (
            getattr(qualification, "profile_digest", None),
            getattr(qualification, "tools_digest", None),
            getattr(qualification, "compiler_digest", None),
        )
        if not all(_is_digest(value) for value in digests):
            return None
        return digests  # type: ignore[return-value]

    def _tool_paths(self) -> tuple[Path, Path] | None:
        try:
            authority = self.loader.snapshot()
        except (OSError, TypeError, ValueError, KeyError):
            return None
        if authority is None:
            return None
        ffmpeg = getattr(authority, "ffmpeg", None)
        ffprobe = getattr(authority, "ffprobe", None)
        if ffmpeg is None or ffprobe is None:
            return None
        return Path(ffmpeg), Path(ffprobe)

    def _vector_digest(self) -> str:
        return _digest(PROBE_VECTOR)

    def _identity(self, encode: Mapping[str, Any]) -> dict[str, Any] | None:
        """The exact identity material a probe is adopted by, or None when unknown."""
        node = self._node()
        digests = self._descriptor_digests()
        if not node or digests is None:
            return None
        profile_digest, tools_digest, compiler_digest = digests
        return {
            "node_identity": {"physical_node_id": node},
            "hardware_identity": self._hardware_identity(),
            "profile_digest": profile_digest,
            "tools_digest": tools_digest,
            "compiler_digest": compiler_digest,
            "probe_vector_digest": self._vector_digest(),
        }

    def _probe_key(self, encode: Mapping[str, Any],
                   identity: Mapping[str, Any]) -> str:
        """The canonical key one probe is adopted by: tuple, node, tools, vector."""
        return _digest(self._key_material(encode, identity))

    def _key_material(self, encode: Mapping[str, Any],
                      identity: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "requested_encode": dict(encode),
            "node_identity": dict(identity["node_identity"]),
            "hardware_identity": dict(identity["hardware_identity"]),
            "profile_digest": identity["profile_digest"],
            "tools_digest": identity["tools_digest"],
            "compiler_digest": identity["compiler_digest"],
            "probe_vector_digest": identity["probe_vector_digest"],
        }

    def _evidence_key_material(self, evidence: Mapping[str, Any]) -> dict[str, Any]:
        """The key material of a stored evidence record, as it was measured.

        The backend is the encoder the probe used, not part of the identity, so it
        is left out here exactly as it was left out when the key was made.
        """
        hardware = {name: value for name, value in evidence["hardware_identity"].items()
                    if name != "backend"}
        return {
            "requested_encode": dict(evidence["requested_encode"]),
            "node_identity": dict(evidence["node_identity"]),
            "hardware_identity": hardware,
            "profile_digest": evidence["profile_digest"],
            "tools_digest": evidence["tools_digest"],
            "compiler_digest": evidence["compiler_digest"],
            "probe_vector_digest": evidence["probe_vector_digest"],
        }

    # --- durable records ----------------------------------------------------------------

    def _record_name(self, probe_key: str) -> str:
        return f"{probe_key}.json"

    def _read_record(self, probe_key: str) -> dict[str, Any] | None:
        try:
            raw = render_authority_files.read_record(
                self.data_root, DIRECTORY, self._record_name(probe_key),
                expected_uid=self._uid, max_bytes=MAX_RECORD_BYTES)
        except (OSError, ValueError):
            return None
        try:
            document = decode_render_json(raw)
        except ValueError:
            return None
        if type(document) is not dict or set(document) != _RECORD_KEYS:
            return None
        if document["format"] != RECORD_FORMAT:
            return None
        if document["probe_key"] != probe_key:
            return None
        if _closed_encode(document.get("requested_encode")) is None:
            return None
        return document

    def _write_record(self, document: Mapping[str, Any]) -> bool:
        """Publish the record once. An existing record is never replaced."""
        raw = _canonical(dict(document))
        if len(raw) > MAX_RECORD_BYTES:
            return False
        self._ensure_authority_directory()
        try:
            render_authority_files.write_record_once(
                self.data_root, DIRECTORY, self._record_name(document["probe_key"]),
                raw)
        except (OSError, ValueError):
            return False
        return True

    def _read_evidence(self, probe_key: str) -> dict[str, Any] | None:
        try:
            raw = render_authority_files.read_record(
                self.data_root, EVIDENCE_DIRECTORY, f"{probe_key}.json",
                expected_uid=self._uid, max_bytes=MAX_RECORD_BYTES)
        except (OSError, ValueError):
            return None
        try:
            document = decode_render_json(raw)
        except ValueError:
            return None
        if type(document) is not dict or set(document) != _EVIDENCE_KEYS:
            return None
        if document["format"] != EVIDENCE_FORMAT:
            return None
        if not _is_digest(document["probe_result_digest"]):
            return None
        # The stored evidence must still be the evidence of this exact probe: a record
        # whose contents were changed no longer hashes to its own key.
        try:
            if _digest(self._evidence_key_material(document)) != probe_key:
                return None
        except (KeyError, TypeError, ValueError):
            return None
        # The tier and the encoder are the pinned helper's own answer for this tuple.
        # A probe-only vector (an encoder the compiler cannot execute) publishes no
        # tier, and that is a capability fact and never a render tier.
        plan = self._plan_for(document["requested_encode"])
        if plan is None:
            return None
        stored_tier = document["selected_tier"]
        if stored_tier != (plan.tier if plan.compiler_args else None):
            return None
        if document["exact_encoder"] != plan.exact_encoder:
            return None
        if document["hardware_identity"].get("backend") != plan.exact_encoder:
            return None
        # The proof sidecar binds the evidence bytes: a changed evidence record no
        # longer matches the digest that was written with it.
        proof = self._read_proof(probe_key)
        if proof is None or proof["evidence_digest"] != _digest(document):
            return None
        return document

    def _read_proof(self, probe_key: str) -> dict[str, Any] | None:
        try:
            raw = render_authority_files.read_record(
                self.data_root, EVIDENCE_DIRECTORY, f"{probe_key}.proof.json",
                expected_uid=self._uid, max_bytes=MAX_RECORD_BYTES)
        except (OSError, ValueError):
            return None
        try:
            document = decode_render_json(raw)
        except ValueError:
            return None
        if type(document) is not dict or set(document) != {
                "format", "probe_key", "evidence_digest"}:
            return None
        if document["format"] != PROOF_FORMAT or document["probe_key"] != probe_key:
            return None
        if not _is_digest(document["evidence_digest"]):
            return None
        return document

    def _write_evidence(self, probe_key: str, evidence: Mapping[str, Any]) -> bool:
        """Publish the evidence and its proof once. Neither is ever replaced."""
        raw = _canonical(dict(evidence))
        if len(raw) > MAX_RECORD_BYTES:
            return False
        self._ensure_authority_directory()
        proof = {
            "format": PROOF_FORMAT,
            "probe_key": probe_key,
            "evidence_digest": _digest(dict(evidence)),
        }
        try:
            render_authority_files.write_record_once(
                self.data_root, EVIDENCE_DIRECTORY, f"{probe_key}.proof.json",
                _canonical(proof))
        except (OSError, ValueError):
            return False
        try:
            render_authority_files.write_record_once(
                self.data_root, EVIDENCE_DIRECTORY, f"{probe_key}.json", raw)
        except FileExistsError:
            return True
        except (OSError, ValueError):
            return False
        return True

    def _write_result(self, probe_key: str, state: str, reason: str) -> bool:
        """Publish the outcome of one probe once. The outcome is never rewritten."""
        document = {
            "format": RESULT_FORMAT,
            "probe_key": probe_key,
            "state": state,
            "reason": reason,
            "updated_at": int(self._clock()),
        }
        raw = _canonical(document)
        self._ensure_authority_directory()
        try:
            render_authority_files.write_record_once(
                self.data_root, DIRECTORY, f"{probe_key}.result.json", raw)
        except FileExistsError:
            return True
        except (OSError, ValueError):
            return False
        return True

    def _read_result(self, probe_key: str) -> dict[str, Any] | None:
        try:
            raw = render_authority_files.read_record(
                self.data_root, DIRECTORY, f"{probe_key}.result.json",
                expected_uid=self._uid, max_bytes=MAX_RECORD_BYTES)
        except (OSError, ValueError):
            return None
        try:
            document = decode_render_json(raw)
        except ValueError:
            return None
        if (type(document) is not dict or set(document) != {
                "format", "probe_key", "state", "reason", "updated_at"}):
            return None
        if document["format"] != RESULT_FORMAT or document["probe_key"] != probe_key:
            return None
        if document["state"] not in ("unsupported", "unmeasured"):
            return None
        return document

    def _read_launch(self, probe_key: str) -> dict[str, Any] | None:
        """The recorded launch: the exact process identity, written once after the spawn."""
        try:
            raw = render_authority_files.read_record(
                self.data_root, DIRECTORY, f"{probe_key}.launch.json",
                expected_uid=self._uid, max_bytes=MAX_RECORD_BYTES)
        except (OSError, ValueError):
            return None
        try:
            document = decode_render_json(raw)
        except ValueError:
            return None
        if type(document) is not dict or set(document) != {
                "format", "probe_key", "process", "started_at", "owner"}:
            return None
        if document["format"] != LAUNCH_FORMAT or document["probe_key"] != probe_key:
            return None
        process = document["process"]
        if type(process) is not dict or set(process) != {
                "pid", "process_group_id", "start_token"}:
            return None
        try:
            return {
                "process": ProcessIdentity(
                    pid=int(process["pid"]),
                    process_group_id=int(process["process_group_id"]),
                    start_token=str(process["start_token"])),
                "owner": str(document["owner"]),
                "started_at": int(document["started_at"]),
            }
        except (TypeError, ValueError):
            return None

    def _write_launch(self, probe_key: str, identity: ProcessIdentity,
                      owner: str) -> bool:
        document = {
            "format": LAUNCH_FORMAT,
            "probe_key": probe_key,
            "process": {
                "pid": identity.pid,
                "process_group_id": identity.process_group_id,
                "start_token": identity.start_token,
            },
            "started_at": int(self._clock()),
            "owner": owner,
        }
        self._ensure_authority_directory()
        try:
            render_authority_files.write_record_once(
                self.data_root, DIRECTORY, f"{probe_key}.launch.json",
                _canonical(document))
        except (OSError, ValueError):
            return False
        return True

    # --- states -------------------------------------------------------------------------

    def _state(self, encode: Mapping[str, Any], state: str, probe_key: str | None,
               reason: str | None, evidence: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """The closed state response. Evidence and its digest exist only for support."""
        supported = state == "supported" and evidence is not None
        return {
            "format": STATE_FORMAT,
            "requested_encode": dict(encode),
            "state": state if state in _STATES else "unmeasured",
            "probe_key": probe_key,
            "reason": reason,
            "evidence": dict(evidence) if supported else None,
            "authority_digest": _digest(evidence) if supported else None,
        }

    def _admission_ok(self, owner: str) -> tuple[bool, str | None]:
        """The admission policy of this Mac, and why when it is closed.

        The probe honours the real pause of this Mac even where the render exemption
        says a paused Mac may still assemble a final render: a probe runs a real
        encoder here, and a paused Mac runs no generation here.
        """
        if self._admission_allowed is not None:
            if not self._admission_allowed():
                return False, "this Mac is paused or not enabled"
        else:
            try:
                from . import registry
                if not registry.machine_enabled(self.machine):
                    return False, "this Mac is paused or not enabled"
            except (OSError, TypeError, ValueError):
                return False, "this Mac's own state could not be read"
        allowed = getattr(self.executor, "_admission_allowed", None)
        if callable(allowed):
            try:
                if not allowed(owner):
                    return False, "render admission is closed on this Mac"
            except Exception:  # noqa: BLE001 - a refused gate is a closed gate
                return False, "render admission is closed on this Mac"
        return True, None

    # --- reading ------------------------------------------------------------------------

    def inspect(self, encode: Mapping[str, Any]) -> dict[str, Any]:
        """What is known about this tuple right now. This never writes anything."""
        closed = _closed_encode(encode)
        if closed is None:
            return self._state(
                {"container": "", "requested_codec": "", "requested_effort": "",
                 "crf": 0, "quality_cpu_preset": "", "bitrate_kbps": None},
                "unmeasured", None,
                "the requested encode is not the closed six-field form")
        identity = self._identity(closed)
        if identity is None:
            return self._state(closed, "unmeasured", None,
                               "this Mac has no installed render authority to probe with")
        probe_key = self._probe_key(closed, identity)
        evidence = self._read_evidence(probe_key)
        if evidence is not None:
            return self._state(closed, "supported", probe_key, None, evidence)
        result = self._read_result(probe_key)
        if result is not None:
            return self._state(closed, result["state"], probe_key, result["reason"])
        if self._read_record(probe_key) is not None:
            return self._state(closed, "pending", probe_key,
                               "the probe is running or its outcome is unknown")
        return self._state(closed, "unmeasured", probe_key,
                           "no probe has been run for this tuple on this node")

    def supported_entries(self) -> list[dict[str, Any]]:
        """Every tuple this node has proved, with the evidence that is still current.

        Only exact validated evidence counts, and only while the tool, compiler,
        profile, hardware and vector identities still match what is installed now.
        """
        entries: list[dict[str, Any]] = []
        for probe_key in self._evidence_keys():
            evidence = self._read_evidence(probe_key)
            if evidence is None:
                continue
            identity = self._identity(evidence["requested_encode"])
            if identity is None:
                continue
            if self._probe_key(evidence["requested_encode"], identity) != probe_key:
                continue
            entries.append(self._state(
                evidence["requested_encode"], "supported", probe_key, None, evidence))
        return entries

    def _evidence_keys(self) -> list[str]:
        """The names of the evidence records on this node."""
        return self._keys_in(EVIDENCE_DIRECTORY)

    def _record_keys(self) -> list[str]:
        """The names of the durable probe records on this node."""
        directory = self.data_root / render_authority_files.AUTHORITY_DIRECTORY / DIRECTORY
        try:
            names = sorted(os.listdir(directory))
        except OSError:
            return []
        keys: list[str] = []
        for name in names:
            if not name.endswith(".json") or name.startswith("."):
                continue
            # Only the plain record names; the result and launch records are named after
            # the same key and are read through their own paths.
            if name.endswith(".result.json") or name.endswith(".launch.json"):
                continue
            key = name[:-len(".json")]
            if _is_digest(key):
                keys.append(key)
        return keys

    def _keys_in(self, directory_name: str) -> list[str]:
        directory = (self.data_root / render_authority_files.AUTHORITY_DIRECTORY
                     / directory_name)
        try:
            names = sorted(os.listdir(directory))
        except OSError:
            return []
        keys: list[str] = []
        for name in names:
            if not name.endswith(".json") or name.startswith("."):
                continue
            if name.endswith(".proof.json"):
                continue
            key = name[:-len(".json")]
            if _is_digest(key):
                keys.append(key)
        return keys

    # --- running ------------------------------------------------------------------------

    def _scratch_for(self, probe_key: str) -> Path:
        root = self._scratch_root
        if root is None:
            root = self.data_root / "render-probe-scratch"
        scratch = Path(root) / probe_key
        scratch.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(scratch, 0o700)
        except OSError:
            pass
        return scratch

    def _ensure_authority_directory(self) -> None:
        """The private authority directory, created once when it is absent."""
        directory = self.data_root / render_authority_files.AUTHORITY_DIRECTORY
        try:
            directory.mkdir(parents=True, exist_ok=True)
            os.chmod(directory, 0o700)
        except OSError:
            return

    def _default_runner(self, argv: Sequence[str], timeout_seconds: float,
                        workspace: Path,
                        on_spawn: Callable[[ProcessIdentity], None] | None = None,
                        ) -> ProbeRun:
        """One bounded process: its own group, a wall bound, capped streams and bytes.

        This runner fails closed. An identity it cannot prove is not a launch, and a
        leader that exited is not proof that its process group is empty: a descendant
        may still be running. Both are reported as an unknown outcome, never as a
        clean finish.
        """
        started = time.monotonic()
        process = subprocess.Popen(
            list(argv), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True, cwd=str(workspace),
            env={"PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8"})
        if on_spawn is not None:
            try:
                identity = self._identity_of(process, Path(argv[0]))
            except Exception as exc:  # noqa: BLE001 - an unproved identity is no identity
                self._kill_group(process)
                detail = f"the probe process identity could not be proved: {exc}"
                return ProbeRun(-1, b"",
                                detail.encode("utf-8", "replace")[:512],
                                time.monotonic() - started)
            on_spawn(identity)
        stdout = bytearray()
        stderr = bytearray()
        drainers = [
            threading.Thread(target=self._drain, args=(process.stdout, stdout), daemon=True),
            threading.Thread(target=self._drain, args=(process.stderr, stderr), daemon=True),
        ]
        for drainer in drainers:
            drainer.start()
        deadline = started + timeout_seconds
        killed = False
        while process.poll() is None:
            if time.monotonic() >= deadline:
                self._kill_group(process)
                killed = True
                break
            if self._rss_limit is not None:
                rss = self._tree_rss(process.pid)
                if rss is not None and rss > self._rss_limit:
                    self._kill_group(process)
                    killed = True
                    break
            if self._scratch_over_limit(workspace):
                self._kill_group(process)
                killed = True
                break
            time.sleep(0.05)
        if killed:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        else:
            process.wait()
        for drainer in drainers:
            drainer.join(timeout=2.0)
        # A leader that exited is not proof that its group is empty.
        group = self._group_of(process)
        if group is not None and self._group_exists(group):
            self._kill_group(process)
            return ProbeRun(-1, bytes(stdout),
                            bytes(stderr) + b"the probe process group did not settle",
                            time.monotonic() - started)
        return ProbeRun(
            returncode=int(process.returncode if process.returncode is not None else -1),
            stdout=bytes(stdout), stderr=bytes(stderr),
            wall_seconds=time.monotonic() - started)

    def _scratch_over_limit(self, workspace: Path) -> bool:
        """Whether the scratch already holds more bytes than the probe allows."""
        limit = self._output_limit
        if limit is None:
            return False
        total = 0
        try:
            for path in workspace.rglob("*"):
                if path.is_file() and not path.is_symlink():
                    total += path.stat().st_size
                    if total > limit:
                        return True
        except OSError:
            return False
        return False

    @staticmethod
    def _group_of(process: Any) -> int | None:
        try:
            return int(os.getpgid(process.pid))
        except OSError:
            return None

    @staticmethod
    def _group_exists(process_group_id: int) -> bool:
        try:
            os.killpg(process_group_id, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    def _identity_of(self, process: Any, executable: Path) -> ProcessIdentity:
        """The executor's own exact process identity for one spawned process."""
        identify = getattr(self.executor, "_identity", None)
        if callable(identify):
            return identify(process, executable)
        import psutil
        return ProcessIdentity(
            pid=int(process.pid), process_group_id=int(os.getpgid(process.pid)),
            start_token=f"{psutil.boot_time()}:{psutil.Process(process.pid).create_time()}:"
                        f"{executable}")

    @staticmethod
    def _tree_rss(process_id: int) -> int | None:
        """The resident memory of one process tree, or None when it cannot be read."""
        try:
            import psutil
            root = psutil.Process(process_id)
            members = [root, *root.children(recursive=True)]
            return sum(member.memory_info().rss for member in members)
        except ImportError:
            return None
        except Exception:  # noqa: BLE001 - an unreadable tree is no measurement
            return None

    @staticmethod
    def _drain(stream: Any, sink: bytearray) -> None:
        """Read one stream into a capped buffer; the rest is dropped, never grown."""
        try:
            while True:
                chunk = stream.read1(65536) if hasattr(stream, "read1") else stream.read(65536)
                if not chunk:
                    return
                if len(sink) < _PROBE_STDIO_LIMIT_BYTES:
                    sink.extend(chunk[:_PROBE_STDIO_LIMIT_BYTES - len(sink)])
        except (OSError, ValueError):
            return

    @staticmethod
    def _kill_group(process: Any) -> None:
        """Stop the exact group this runner started. Nothing else is ever signalled."""
        try:
            group = os.getpgid(process.pid)
        except OSError:
            return
        for number in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(group, number)
            except (ProcessLookupError, PermissionError, OSError):
                return
            time.sleep(0.5)
            if process.poll() is not None:
                return

    def _runner(self) -> Callable[..., ProbeRun]:
        return self._runner_callable or self._default_runner

    async def probe(self, encode: Mapping[str, Any]) -> dict[str, Any]:
        """Run one probe for this exact tuple, or answer with what is already known.

        This is the only thing that may start a process. A tuple that is already
        recorded is adopted, never run twice.
        """
        closed = _closed_encode(encode)
        if closed is None:
            return self._state(
                {"container": "", "requested_codec": "", "requested_effort": "",
                 "crf": 0, "quality_cpu_preset": "", "bitrate_kbps": None},
                "unmeasured", None,
                "the requested encode is not the closed six-field form")
        identity = self._identity(closed)
        if identity is None:
            return self._state(closed, "unmeasured", None,
                               "this Mac has no installed render authority to probe with")
        probe_key = self._probe_key(closed, identity)
        with self._lock:
            existing = self._read_record(probe_key)
            if existing is not None:
                return self.inspect(closed)
            owner = self._owner(probe_key)
            allowed, reason = self._admission_ok(owner)
            if not allowed:
                return self._state(closed, "unmeasured", probe_key, reason)
            authority = self._authority()
            if authority is None:
                return self._state(closed, "unmeasured", probe_key,
                                   "this Mac has no installed render authority to probe with")
            if not self._has_memory(authority):
                return self._state(closed, "unmeasured", probe_key,
                                   "this Mac does not have the memory this profile needs")
            tools = self._tool_paths()
            if tools is None:
                return self._state(closed, "unmeasured", probe_key,
                                   "the pinned tools are not installed")
            plan_error = self._plan_error(closed, tools)
            if plan_error is not None:
                self._write_result(probe_key, "unsupported", plan_error)
                return self.inspect(closed)
            # The physical hold comes first: it is the broker's in-memory lease, and a
            # crash loses it, so nothing durable depends on it having been taken.
            held = self._acquire(owner)
            if not held:
                return self._state(closed, "unmeasured", probe_key,
                                   "this Mac is busy and cannot hold the probe")
            # The record is written before the spawn: a crash after the spawn still
            # leaves a durable pending probe, and a second POST adopts it.
            record = {
                "format": RECORD_FORMAT,
                "probe_key": probe_key,
                "requested_encode": dict(closed),
                "identity": dict(identity),
                "owner": owner,
                "created_at": int(self._clock()),
            }
            if not self._write_record(record):
                self._release(owner)
                return self._state(closed, "unmeasured", probe_key,
                                   "the probe record could not be saved on this Mac")
        try:
            outcome = await asyncio.to_thread(
                self._run_probe, closed, probe_key, tools, owner)
        except BaseException:
            # The outcome is unknown: the record stays pending and the hold stays.
            raise
        if outcome["state"] == "supported":
            # The evidence is the record of a support; no second outcome is written.
            if not self._write_evidence(probe_key, outcome["evidence"]):
                self._release(owner)
                return self._state(closed, "unmeasured", probe_key,
                                   "the probe result could not be saved on this Mac")
        elif not self._write_result(probe_key, outcome["state"], outcome["reason"]):
            self._release(owner)
            return self._state(closed, "unmeasured", probe_key,
                               "the probe outcome could not be saved on this Mac")
        self._release(owner)
        return self.inspect(closed)

    def _plan_for(self, encode: Mapping[str, Any]) -> Any:
        """The pinned helper's plan for one tuple, or None when it refuses it."""
        tools = self._tool_paths()
        if tools is None:
            return None
        try:
            return plan_encode_probe(
                encode, ffmpeg=tools[0], ffprobe=tools[1],
                scratch_dir=self._scratch_root or (self.data_root / "render-probe-scratch"),
                canvas_width=PROBE_VECTOR["canvas"][0],
                canvas_height=PROBE_VECTOR["canvas"][1],
                fps=PROBE_VECTOR["fps"], duration_sec=PROBE_VECTOR["duration_sec"])
        except (UnsupportedEncodeProbe, OSError, TypeError, ValueError):
            return None

    def _plan_error(self, closed: Mapping[str, Any],
                    tools: tuple[Path, Path]) -> str | None:
        """Why the pinned compiler refuses this tuple, or None when it does not."""
        try:
            plan_encode_probe(
                closed, ffmpeg=tools[0], ffprobe=tools[1],
                scratch_dir=self._scratch_root or (self.data_root / "render-probe-scratch"),
                canvas_width=PROBE_VECTOR["canvas"][0],
                canvas_height=PROBE_VECTOR["canvas"][1],
                fps=PROBE_VECTOR["fps"], duration_sec=PROBE_VECTOR["duration_sec"])
        except UnsupportedEncodeProbe as exc:
            return str(exc)[:200]
        except (OSError, TypeError, ValueError) as exc:
            return f"the pinned compiler could not plan this tuple: {exc}"[:200]
        return None

    def _authority(self) -> Any:
        try:
            return self.loader.snapshot()
        except (OSError, TypeError, ValueError, KeyError):
            return None

    def _has_memory(self, authority: Any) -> bool:
        checker = getattr(self.executor, "_has_memory", None)
        if callable(checker):
            try:
                return bool(checker(authority))
            except Exception:  # noqa: BLE001 - an unreadable floor is no memory
                return False
        return True

    def _owner(self, probe_key: str) -> str:
        """The owner string of the physical hold. It is a render-lane lease."""
        return f"render:encode-probe-{probe_key[:16]}"

    def _acquire(self, owner: str) -> bool:
        acquire = getattr(self.executor.broker, "acquire_external_machine", None)
        if not callable(acquire):
            return False
        try:
            return bool(acquire(self.machine, owner))
        except Exception:  # noqa: BLE001 - a refused hold is no hold
            return False

    def _release(self, owner: str) -> None:
        release = getattr(self.executor.broker, "release_external_machine", None)
        if not callable(release):
            return
        try:
            release(self.machine, owner)
        except Exception:  # noqa: BLE001 - a failed release leaves the hold in place
            return

    def _restore_hold(self, owner: str) -> None:
        restore = getattr(self.executor.broker, "restore_external_machine", None)
        if not callable(restore):
            return
        try:
            restore(self.machine, owner)
        except Exception:  # noqa: BLE001 - another owner keeps the hold
            return

    def _run_probe(self, closed: Mapping[str, Any], probe_key: str,
                   tools: tuple[Path, Path], owner: str) -> dict[str, Any]:
        """The bounded run itself, off the event loop."""
        authority = self._authority()
        bounds = getattr(authority, "bounds", None)
        wall = _PROBE_MAX_WALL_SECONDS
        output_limit = _PROBE_OUTPUT_LIMIT_BYTES
        if bounds is not None:
            wall = min(wall, float(getattr(bounds, "max_wall_seconds", wall)))
            output_limit = min(output_limit, int(getattr(bounds, "output_limit_bytes",
                                                        output_limit)))
        scratch = self._scratch_for(probe_key)
        runner = self._runner()
        # The probe runs inside the profile's own process-tree memory bound, and its
        # scratch is capped while it runs, not only after it finished.
        self._rss_limit = (
            int(getattr(bounds, "max_rss_bytes", 0)) if bounds is not None else 0) or None
        self._output_limit = output_limit

        def bounded_runner(argv: Sequence[str], timeout_seconds: float) -> ProbeRun:
            budget = min(float(timeout_seconds), wall)
            def on_spawn(identity: ProcessIdentity) -> None:
                self._write_launch(probe_key, identity, owner)
            return runner(argv, budget, scratch, on_spawn)

        try:
            result = probe_encode_support(
                closed, ffmpeg=tools[0], ffprobe=tools[1], scratch_dir=scratch,
                runner=bounded_runner, tool_identity=self._tool_identity(),
                hardware_identity=self._hardware_identity(),
                canvas_width=PROBE_VECTOR["canvas"][0],
                canvas_height=PROBE_VECTOR["canvas"][1],
                fps=PROBE_VECTOR["fps"],
                duration_sec=PROBE_VECTOR["duration_sec"],
                timeout_seconds=wall)
        except UnsupportedEncodeProbe as exc:
            return {"state": "unsupported", "reason": str(exc)[:200], "evidence": None}
        except EncodeProbeError as exc:
            state = "unsupported" if _is_capability_refusal(exc.reason) else "unmeasured"
            return {"state": state, "reason": exc.reason[:200], "evidence": None}
        except Exception as exc:  # noqa: BLE001 - an unknown outcome is not support
            return {"state": "unmeasured",
                    "reason": f"the probe did not finish: {exc}"[:200], "evidence": None}
        oversized = self._oversized_output(scratch, output_limit)
        if oversized is not None:
            return {"state": "unsupported", "reason": oversized, "evidence": None}
        evidence = self._evidence_from(closed, result)
        if evidence is None:
            return {"state": "unmeasured",
                    "reason": "the probe produced no usable evidence", "evidence": None}
        return {"state": "supported", "reason": "", "evidence": evidence}

    @staticmethod
    def _oversized_output(scratch: Path, limit_bytes: int) -> str | None:
        """Why the probe wrote more than its bound allows, or None."""
        total = 0
        try:
            for path in scratch.rglob("*"):
                if path.is_file() and not path.is_symlink():
                    total += path.stat().st_size
        except OSError:
            return None
        if total > limit_bytes:
            return "the probe wrote more bytes than its bound allows"
        return None

    def _tool_identity(self) -> dict[str, Any]:
        """The pinned tool identities, as the helper records them."""
        tools = self._tool_paths()
        digests = self._descriptor_digests()
        identity: dict[str, Any] = {}
        if tools is not None:
            identity["ffmpeg"] = str(tools[0])
            identity["ffprobe"] = str(tools[1])
        if digests is not None:
            identity["tools_digest"] = digests[1]
            identity["compiler_digest"] = digests[2]
            identity["profile_digest"] = digests[0]
        return identity

    def _result_digest(self, result: Mapping[str, Any]) -> str:
        """The digest of the validated result, without the scratch path or the clock."""
        return _digest({
            "encode": result.get("encode"),
            "scope": result.get("scope"),
            "tier": result.get("tier"),
            "selected_tier": result.get("selected_tier"),
            "exact_encoder": result.get("exact_encoder"),
            "encoder_args": list(result.get("encoder_args") or []),
            "expected": result.get("expected"),
            "measured": result.get("measured"),
            "tool_identity": result.get("tool_identity"),
        })

    def _evidence_from(self, closed: Mapping[str, Any],
                       result: Mapping[str, Any]) -> dict[str, Any] | None:
        """The closed evidence of one successful probe, or None.

        Nothing here is invented: every field comes from the validated result or from
        the identities this node actually has.
        """
        exact_encoder = result.get("exact_encoder")
        scope = result.get("scope")
        if (type(exact_encoder) is not str or not exact_encoder
                or scope not in ("render_lane", "capability_only")):
            return None
        selected_tier = result.get("selected_tier")
        if selected_tier is not None and (
                type(selected_tier) is not str or not selected_tier):
            return None
        if selected_tier is None and result.get("compiler_args") is not False:
            # Only a probe-only vector may publish no tier.
            return None
        identity = self._identity(closed)
        if identity is None:
            return None
        hardware = dict(identity["hardware_identity"])
        hardware["backend"] = exact_encoder
        evidence = {
            "format": EVIDENCE_FORMAT,
            "requested_encode": dict(closed),
            "node_identity": dict(identity["node_identity"]),
            "hardware_identity": hardware,
            "profile_digest": identity["profile_digest"],
            "tools_digest": identity["tools_digest"],
            "compiler_digest": identity["compiler_digest"],
            "selected_tier": selected_tier,
            "exact_encoder": exact_encoder,
            "probe_vector_digest": identity["probe_vector_digest"],
            "probe_result_digest": self._result_digest(result),
            "purpose": PURPOSE,
        }
        if set(evidence) != _EVIDENCE_KEYS:
            return None
        return evidence

    # --- recovery -----------------------------------------------------------------------

    async def recover_and_restore_physical_holds(self) -> list[dict[str, Any]]:
        """Recover the durable probes after a restart.

        A probe whose process cannot still run is resolved as unmeasured and its hold
        is released. A probe whose process may still run keeps its hold and stays
        pending: an unknown outcome is never released, and no process is ever signalled
        on a guess.
        """
        recovered: list[dict[str, Any]] = []
        for probe_key in self._record_keys():
            record = self._read_record(probe_key)
            if record is None:
                continue
            if self._read_result(probe_key) is not None:
                continue
            if self._read_evidence(probe_key) is not None:
                # The probe already finished and proved its tuple; nothing is pending.
                continue
            launch = self._read_launch(probe_key)
            owner = str(record.get("owner") or self._owner(probe_key))
            if launch is None:
                # The spawn was never recorded: no process can be proved to be ours.
                self._write_result(probe_key, "unmeasured",
                                   "the probe did not finish and no process was recorded")
                self._release(owner)
                recovered.append({"probe_key": probe_key, "state": "unmeasured"})
                continue
            identity = launch["process"]
            state = self._recorded_state(identity)
            if state == "live":
                stopped = await self._stop_exact_group(identity)
                if not stopped:
                    self._restore_hold(owner)
                    recovered.append({"probe_key": probe_key, "state": "pending"})
                    continue
                self._write_result(probe_key, "unmeasured",
                                   "the probe did not finish and its process was stopped")
                self._release(owner)
                recovered.append({"probe_key": probe_key, "state": "unmeasured"})
                continue
            if state == "gone":
                self._write_result(probe_key, "unmeasured",
                                   "the probe did not finish and its process is gone")
                self._release(owner)
                recovered.append({"probe_key": probe_key, "state": "unmeasured"})
                continue
            self._restore_hold(owner)
            recovered.append({"probe_key": probe_key, "state": "pending"})
        return recovered

    def _recorded_state(self, identity: ProcessIdentity) -> str:
        """The executor's own classification of one exact recorded process."""
        checker = getattr(self.executor, "_recorded_process_state", None)
        if not callable(checker):
            return "unknown"
        try:
            return str(checker(identity))
        except Exception:  # noqa: BLE001 - an unreadable state is unknown
            return "unknown"

    async def _stop_exact_group(self, identity: ProcessIdentity) -> bool:
        """Stop one exact recorded group and prove it empty. Never a pattern kill."""
        stop = getattr(self.executor, "_stop_recovered_exact_group", None)
        if not callable(stop):
            return False
        try:
            return bool(await stop(identity))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - an unproved stop is no stop
            return False
