"""The Music job runner: state, one spawned engine child, and the WAV artifact.

The parent owns everything durable — job records, output files, cancellation
intent.  The engine runs in a separate *spawned* child (``engine_child.py``) so
that:

* a cancel can actually stop a native call that will not return;
* a crash or a fatal MLX/torch abort in the engine cannot take the HTTP worker
  down with it.

The child is kept resident between jobs because loading the 2B DiT + 0.6B LM
takes ~3 minutes on this class of Mac; it is destroyed only when a cancel
requires a forced stop, and the next submit starts a fresh one.
"""
from __future__ import annotations

from hashlib import sha256
import importlib.metadata
import importlib.util
import json
import math
import multiprocessing
import os
from pathlib import Path
import platform
import queue
import random
import resource
import signal
import subprocess
import threading
import time
from uuid import uuid4
import wave

try:
    import psutil
except ImportError:  # pragma: no cover - psutil ships with the engine venv
    psutil = None

from . import catalog, cache as cache_module, engine_child, stable_audio


# The worker's default model and the engine it keeps resident.  Every value is read
# from that model's own catalog entry, so the default engine's identity and the
# contract this module enforces cannot drift apart.  A request naming another model
# is validated and run from *that* model's entry (_VALIDATORS, _runner_for).
DEFAULT_MODEL = catalog.require_model(catalog.MAIN_REPO)
MODEL_ID = DEFAULT_MODEL.repo
ENGINE = catalog.ENGINE
AUDIO_FORMAT = "wav"
MEDIA_TYPE = "audio/wav"

# Single-pass ceiling a caller gets when it does not name the host's tier cap.  The
# Hub canonicalises a Music request before a worker sees it, so both sides need one
# agreed number; ``submit`` always passes the model's own cap for this host
# (_tier_cap_for_this_host).  This is a documented conservative default, not a claim
# about any model's maximum.
DEFAULT_TIER_CAP_SECONDS = 480
TERMINAL = {"done", "error", "cancelled"}
# How long a cooperatively-cancelled child gets to notice the shared event
# before the parent stops being polite.  Native diffusion steps on this class
# of Mac take ~8 s, so the grace has to exceed one step or every cancel becomes
# a forced kill.
CANCEL_GRACE_SECONDS = 12.0
# Model load is ~3 minutes here; leave generous headroom for a cold disk.
LOAD_TIMEOUT_SECONDS = 1800.0


def runtime_identity() -> str:
    """Deterministic digest of the exact engine packages actually installed."""
    facts = {"python": platform.python_version()}
    for name in ("ace-step", "mlx", "mlx-lm", "torch", "transformers"):
        try:
            facts[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            facts[name] = None
    return sha256(json.dumps(facts, sort_keys=True).encode()).hexdigest()


def _finite_number(value, *, name: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError(f"{name} must be finite")
    if not low <= number <= high:
        raise ValueError(f"{name} must be between {low:g} and {high:g}")
    return number


def _default(model: catalog.ModelEntry, key: str):
    """One model's own generation default, refused by name when it omits it.

    The alternative — falling back to the worker's default model — is how a newly
    added model used to be published with ACE-Step's numbers.
    """
    if key not in model.defaults:
        raise ValueError(
            f"{model.repo} publishes no {key!r} generation default; add it to its "
            "ModelEntry in components/music/app/backend/catalog.py")
    return model.defaults[key]


def _limit(model: catalog.ModelEntry, key: str):
    """One model's own generation limit, refused by name when it omits it."""
    if key not in model.limits:
        raise ValueError(
            f"{model.repo} publishes no {key!r} generation limit; add it to its "
            "ModelEntry in components/music/app/backend/catalog.py")
    return model.limits[key]


def _validate_stable_audio(raw: dict, model: catalog.ModelEntry, *,
                           tier_cap_seconds: int = DEFAULT_TIER_CAP_SECONDS) -> dict:
    """Stable Audio 3 parameters: instrumental only, up to this model's own cap.

    ``tier_cap_seconds`` is accepted for the shared validator signature and ignored:
    this engine has no hardware tier table, so its single-pass cap is its own.
    """
    prompt = raw.get("prompt")
    if (not isinstance(prompt, str) or not prompt.strip()
            or len(prompt) > PROMPT_MAX_CHARACTERS):
        raise ValueError(f"Prompt must contain 1-{PROMPT_MAX_CHARACTERS} characters")
    duration = raw.get("duration_seconds",
                       raw.get("duration", _default(model, "duration_seconds")))
    duration = _finite_number(duration, name="duration",
                              low=float(_limit(model, "min_duration_seconds")),
                              high=float(_limit(model, "max_duration_seconds")))
    steps = raw.get("steps", raw.get("inference_steps", _default(model, "steps")))
    if (type(steps) is not int or not _limit(model, "min_steps")
            <= steps <= _limit(model, "max_steps")):
        raise ValueError(
            f"Steps must be an integer between {_limit(model, 'min_steps')} and "
            f"{_limit(model, 'max_steps')}")
    guidance = _finite_number(
        raw.get("guidance_scale", raw.get("cfg", _default(model, "guidance_scale"))),
        name="guidance_scale", low=float(_limit(model, "min_guidance")),
        high=float(_limit(model, "max_guidance")))
    seed = raw.get("seed", _default(model, "seed"))
    if seed is None:
        seed = -1
    if (type(seed) is not int
            or not _limit(model, "min_seed") <= seed <= _limit(model, "max_seed")):
        raise ValueError("Seed must be -1 or a 32-bit unsigned integer")
    return {
        "repo": model.repo,
        "model_revision": model.revision,
        "prompt": prompt.strip(), "duration_seconds": duration, "steps": steps,
        "guidance_scale": guidance, "seed": seed,
        # The engine has no vocals; keep the shared job shape honest about that.
        "instrumental": True, "lyrics": "[Instrumental]", "vocal_language": "unknown",
        "dit_model": _default(model, "dit_model"), "lm_model": "",
        "tier_cap_seconds": int(_limit(model, "single_pass_max_seconds")),
        "exceeds_single_pass": False,
    }


def _validate_ace_step(raw: dict, model: catalog.ModelEntry, *,
                       tier_cap_seconds: int = DEFAULT_TIER_CAP_SECONDS) -> dict:
    """ACE-Step parameters, bounded by this model's own defaults and limits."""
    revision = raw.get("model_revision")
    if revision is not None and revision != model.revision:
        raise ValueError(f"Only the pinned {model.label} revision is available")
    prompt = raw.get("prompt")
    if (not isinstance(prompt, str) or not prompt.strip()
            or len(prompt) > PROMPT_MAX_CHARACTERS):
        raise ValueError(f"Prompt must contain 1-{PROMPT_MAX_CHARACTERS} characters")

    duration = raw.get("duration_seconds",
                       raw.get("duration", _default(model, "duration_seconds")))
    if duration is None:
        duration = _default(model, "duration_seconds")
    duration = _finite_number(duration, name="duration",
                              low=float(_limit(model, "min_duration_seconds")),
                              high=float(_limit(model, "max_duration_seconds")))

    steps = raw.get("steps", raw.get("inference_steps", _default(model, "steps")))
    if (type(steps) is not int or not _limit(model, "min_steps")
            <= steps <= _limit(model, "max_steps")):
        raise ValueError(
            f"Steps must be an integer between {_limit(model, 'min_steps')} and "
            f"{_limit(model, 'max_steps')}")

    guidance = _finite_number(
        raw.get("guidance_scale", _default(model, "guidance_scale")),
        name="guidance_scale", low=float(_limit(model, "min_guidance")),
        high=float(_limit(model, "max_guidance")))
    # The shift control is the ACE-Step engine's own parameter, not a value this
    # catalogue publishes per model; its bounds are the engine's.
    shift = _finite_number(raw.get("shift", _default(model, "shift")),
                           name="shift", low=0.0, high=20.0)

    seed = raw.get("seed", _default(model, "seed"))
    if seed is None:
        seed = -1
    if (type(seed) is not int
            or not _limit(model, "min_seed") <= seed <= _limit(model, "max_seed")):
        raise ValueError("Seed must be -1 or a 32-bit unsigned integer")

    instrumental = raw.get("instrumental", _default(model, "instrumental"))
    if not isinstance(instrumental, bool):
        raise ValueError("instrumental must be a boolean")
    lyrics = raw.get("lyrics", _default(model, "lyrics"))
    if not isinstance(lyrics, str) or len(lyrics) > LYRICS_MAX_CHARACTERS:
        raise ValueError("Lyrics must be a string of at most 8000 characters")
    if instrumental and not lyrics.strip():
        lyrics = _default(model, "lyrics")
    language = raw.get("vocal_language", _default(model, "vocal_language"))
    if not isinstance(language, str) or len(language) > 32:
        raise ValueError("vocal_language must be a short string")

    dit_model = str(raw.get("dit_model") or _default(model, "dit_model"))
    lm_model = str(raw.get("lm_model") or _default(model, "lm_model"))
    if dit_model != _default(model, "dit_model"):
        raise ValueError(
            f"Only the {_default(model, 'dit_model')} checkpoint is installed")
    if lm_model != _default(model, "lm_model"):
        raise ValueError(
            f"Only the {_default(model, 'lm_model')} checkpoint is installed")

    return {
        "repo": model.repo, "model_revision": model.revision, "prompt": prompt.strip(),
        "duration_seconds": duration, "steps": steps, "guidance_scale": guidance,
        "shift": shift, "seed": seed, "instrumental": instrumental,
        "lyrics": lyrics, "vocal_language": language,
        "dit_model": dit_model, "lm_model": lm_model,
        "tier_cap_seconds": int(tier_cap_seconds),
        "exceeds_single_pass": duration > tier_cap_seconds,
    }


# runtime -> request validator.  A model whose runtime is missing here is refused by
# name in validate_request instead of being validated as ACE-Step.
_VALIDATORS = {
    catalog.RUNTIME_ACE_STEP: _validate_ace_step,
    catalog.RUNTIME_STABLE_AUDIO: _validate_stable_audio,
}

LANE_CONTRACT_SCHEMA = "studiohub.lane-contract.v1"
PROMPT_MAX_CHARACTERS = 4000
LYRICS_MAX_CHARACTERS = 8000

# runtime -> the parameters that runtime's validator accepts *in addition to* the shared ones.
# Keyed the same way `_VALIDATORS` is, and `tests/test_music_lane_contract.py` asserts the two
# tables cover the same runtimes, so a runtime added to one and not the other fails loudly rather
# than publishing a contract that silently omits half its surface.
_CONTRACT_EXTRAS: dict[str, tuple[dict, ...]] = {
    catalog.RUNTIME_ACE_STEP: (
        {"name": "shift", "type": "number", "required": False, "minimum": 0.0, "maximum": 20.0,
         "note": "the engine's own control; its bounds are the engine's, not this model's"},
        {"name": "instrumental", "type": "boolean", "required": False, "default": True,
         "note": "false asks for sung lyrics; this engine is the only one that can sing"},
        {"name": "lyrics", "type": "string", "required": False,
         "max_characters": LYRICS_MAX_CHARACTERS},
        {"name": "vocal_language", "type": "string", "required": False, "max_characters": 32},
    ),
    # Stable Audio 3 can only produce instrumentals: `_validate_stable_audio` forces
    # `instrumental=True` and `lyrics="[Instrumental]"` whatever is sent, so offering those
    # parameters would be offering controls the lane discards.
    catalog.RUNTIME_STABLE_AUDIO: (),
}


def lane_contract(model: catalog.ModelEntry) -> dict:
    """The Music lane's dispatch contract for one model.

    What a scheduler needs in order to *ask for* this lane's work. Every bound is the one
    ``validate_request`` enforces, read from this model's own ``limits``/``defaults`` through the
    same ``_limit``/``_default`` helpers the validators use — so the contract cannot drift from
    the code that accepts or refuses the request, and a newly adopted music model publishes a
    correct contract with no edit here.

    The per-runtime split is real, not cosmetic: ACE-Step accepts lyrics, a vocal language and a
    shift control, and Stable Audio 3 accepts none of them. A contract that listed the union would
    invite a scheduler to send parameters one of the two silently discards.
    """
    parameters = [
        {"name": "prompt", "type": "string", "required": True,
         "max_characters": PROMPT_MAX_CHARACTERS},
        {"name": "duration_seconds", "type": "number", "required": False,
         "minimum": _limit(model, "min_duration_seconds"),
         "maximum": _limit(model, "max_duration_seconds"),
         "default": _default(model, "duration_seconds"),
         "note": "a duration beyond this host's single-pass cap is rendered in sections"},
        {"name": "steps", "type": "integer", "required": False,
         "minimum": _limit(model, "min_steps"), "maximum": _limit(model, "max_steps"),
         "default": _default(model, "steps")},
        {"name": "guidance_scale", "type": "number", "required": False,
         "minimum": _limit(model, "min_guidance"), "maximum": _limit(model, "max_guidance"),
         "default": _default(model, "guidance_scale")},
        {"name": "seed", "type": "integer", "required": False,
         "minimum": _limit(model, "min_seed"), "maximum": _limit(model, "max_seed"),
         "default": _default(model, "seed"), "note": "-1 draws a random seed"},
        *_CONTRACT_EXTRAS.get(model.runtime, ()),
    ]
    contract = {
        "schema": LANE_CONTRACT_SCHEMA,
        "modality": "music",
        # The canonical token, not this worker's own `music.text_to_music` label: the Hub
        # normalises `hub_operation` to `OPERATION_BY_MODALITY` and keeps the worker's wording in
        # `hub_reported_operation`, but it passes this contract through verbatim, so the canonical
        # spelling is the one a reader can join on.
        "operation": "music.generation",
        "parameters": parameters,
        "artifact": {"modality": "music", "media_type": MEDIA_TYPE,
                     "extension": AUDIO_FORMAT, "per_item": 1},
        "states": {
            "accepted": ["queued"],
            "active": ["running", "cancel_requested"],
            "terminal": ["done", "error", "cancelled"],
            "uncertain": "uncertain",
            "uncertain_is_terminal": False,
            "discard_supported": True,
        },
        "cancel": {
            "supported": True,
            "request": "DELETE /api/hub/jobs/{batch_id}",
            "semantics": ("stops the render at the next safe point; a long-form job is rendered in "
                          "sections, so sections already written are kept and a cancelled item is "
                          "not evidence that no audio was produced"),
        },
    }
    if model.measured_runtime:
        contract["runtime_seconds"] = model.measured_runtime
    return contract



def validate_request(raw: dict, tier_cap_seconds: int = DEFAULT_TIER_CAP_SECONDS) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("Music request must be an object")
    model = catalog.require_model(str(raw.get("repo") or MODEL_ID))
    validator = _VALIDATORS.get(model.runtime)
    if validator is None:
        raise ValueError(
            f"{model.repo} declares runtime {model.runtime!r}, which this worker has no "
            "request validator for; add one in "
            "components/music/app/backend/generation.py")
    return validator(raw, model, tier_cap_seconds=tier_cap_seconds)


def _atomic_json(path: Path, value: object) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            os.chmod(temporary, 0o600)
            json.dump(value, stream, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _activity_time(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _activity_progress(value: object) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = 0.0
    if not math.isfinite(parsed):
        parsed = 0.0
    return max(0.0, min(1.0, parsed))


def _activity_text(value: object, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip()[:limit] or None


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_tree_digest(root: Path) -> str:
    """Content identity for a staged ACE-Step source tree."""
    root = Path(root)
    digest = sha256()
    for path in sorted(root.rglob("*")):
        if path.is_dir() and not path.is_symlink():
            continue
        relative = path.relative_to(root).as_posix()
        if relative == "studiohub-engine.json":
            continue
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"ACE-Step source contains a non-regular file: {relative}")
        size = path.stat().st_size
        digest.update(relative.encode() + b"\0" + str(size).encode() + b"\0")
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def engine_contract_reason(engine_root: str) -> str | None:
    """Return why the supplied checkout is not the exact runnable engine."""
    root = Path(engine_root)
    required = (
        root / "acestep" / "handler.py",
        root / "acestep" / "inference.py",
        root / "acestep" / "llm_inference.py",
        *(root / relative for relative in catalog.ENGINE_MODEL_CODE_SHA256),
    )
    if not all(path.is_file() and not path.is_symlink() for path in required):
        return "pinned ACE-Step source is incomplete"
    provenance = root / "studiohub-engine.json"
    if provenance.is_file() and not provenance.is_symlink():
        try:
            identity = json.loads(provenance.read_text())
            observed_source = source_tree_digest(root)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return f"bundled ACE-Step source could not be verified: {exc}"
        expected_identity = {
            "schema": "studiohub.music-engine-source", "schema_version": 1,
            "repo": catalog.ENGINE_REPO, "revision": catalog.ENGINE_REVISION,
            "source_sha256": catalog.ENGINE_SOURCE_SHA256,
        }
        if identity != expected_identity:
            return "bundled ACE-Step provenance does not match the pinned source"
        if observed_source != catalog.ENGINE_SOURCE_SHA256:
            return "bundled ACE-Step source digest does not match its pinned identity"
    else:
        git = Path("/usr/bin/git")
        if not git.is_file():
            return "system Git is unavailable for ACE-Step source verification"
        try:
            revision = subprocess.run(
                [str(git), "-C", str(root), "rev-parse", "HEAD"],
                text=True, capture_output=True, timeout=10, check=True).stdout.strip()
            changed = subprocess.run(
                [str(git), "-C", str(root), "status", "--porcelain",
                 "--untracked-files=all", "--", "acestep"],
                text=True, capture_output=True, timeout=10, check=True).stdout.strip()
        except (OSError, subprocess.SubprocessError) as exc:
            return f"ACE-Step source revision could not be verified: {exc}"
        if revision != catalog.ENGINE_REVISION:
            return f"ACE-Step source revision is {revision or 'unknown'}, not the pinned revision"
        if changed:
            return "ACE-Step source has tracked or untracked changes"
    for relative, expected in catalog.ENGINE_MODEL_CODE_SHA256.items():
        try:
            observed = _file_sha256(root / relative)
        except OSError as exc:
            return f"ACE-Step model code could not be hashed: {exc}"
        if observed != expected:
            return f"ACE-Step model code digest mismatch: {relative}"
    try:
        spec = importlib.util.find_spec("acestep")
        if spec is None:
            return "ACE-Step is not installed in the Music runtime"
    except (ImportError, AttributeError, ValueError) as exc:
        return f"ACE-Step runtime could not be inspected: {exc}"
    locations = list(spec.submodule_search_locations or ())
    if spec.origin:
        locations.append(spec.origin)
    try:
        if not locations or any(
                not Path(location).resolve().is_relative_to(root.resolve())
                for location in locations):
            return "ACE-Step runtime import resolves outside the pinned engine root"
    except OSError as exc:
        return f"ACE-Step runtime import path could not be verified: {exc}"
    return None


def inspect_wav(path: Path) -> dict:
    """Read the real PCM facts out of the artifact, not the request."""
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("engine did not produce a WAV file")
    data = path.read_bytes()
    if not data.startswith(b"RIFF") or data[8:12] != b"WAVE":
        raise RuntimeError("engine artifact is not a RIFF/WAVE file")
    try:
        with wave.open(str(path), "rb") as audio:
            if audio.getcomptype() != "NONE":
                raise RuntimeError("compressed WAV is not supported")
            frames = audio.getnframes()
            sample_rate = audio.getframerate()
            channels = audio.getnchannels()
            sample_width = audio.getsampwidth()
            payload = audio.readframes(frames)
    except (EOFError, wave.Error) as exc:
        raise RuntimeError(f"invalid WAV artifact: {exc}") from exc
    if frames <= 0 or sample_rate <= 0 or channels not in (1, 2):
        raise RuntimeError("WAV has invalid duration, sample rate, or channel count")
    expected_payload = frames * channels * sample_width
    if len(payload) != expected_payload:
        raise RuntimeError(
            f"WAV payload is truncated: expected {expected_payload} PCM bytes, "
            f"found {len(payload)}")
    return {"bytes": len(data), "sha256": sha256(data).hexdigest(),
            "frames": frames, "sample_rate_hz": sample_rate, "channels": channels,
            "sample_width_bytes": sample_width,
            "audio_duration_s": round(frames / sample_rate, 6)}


def final_matches(job: dict, path: Path) -> bool:
    try:
        if path.is_symlink() or not path.is_file():
            return False
        if job.get("output_path") != str(path):
            return False
        return (path.stat().st_size == job.get("bytes")
                and sha256(path.read_bytes()).hexdigest() == job.get("sha256"))
    except OSError:
        return False


class EngineExecutor:
    """One spawned engine child, reused until a cancel forces a restart."""

    def __init__(self, engine: dict, *, load_timeout: float = LOAD_TIMEOUT_SECONDS,
                 cancel_grace: float = CANCEL_GRACE_SECONDS) -> None:
        self.engine = dict(engine)
        self.load_timeout = load_timeout
        self.cancel_grace = cancel_grace
        self._ctx = multiprocessing.get_context("spawn")
        self._process = None
        self._commands = None
        self._events = None
        self._cancel = None
        self._generation = 0
        self._loaded = False
        self._blocked_reason: str | None = None
        # Peak RSS observed for the engine child during the most recent run.
        self.peak_rss_bytes = 0
        self._rss_process = None
        self._rss_sampled_at = 0.0

    # -- child lifecycle -----------------------------------------------------
    @property
    def child_pid(self) -> int | None:
        return self._process.pid if self._process is not None and self._process.is_alive() else None

    def _discard(self) -> bool:
        process = self._process
        self._loaded = False
        if process is None:
            return True
        if process.is_alive() and os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                process.terminate()
        elif process.is_alive():
            process.terminate()
        process.join(5.0)
        if process.is_alive():
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    process.kill()
            else:
                process.kill()
            process.join(5.0)
        dead = not process.is_alive()
        if not dead:
            return False
        process.close()
        self._process = None
        self._commands = None
        self._events = None
        self._cancel = None
        return dead

    def _spawn(self) -> None:
        self._generation += 1
        self._commands = self._ctx.Queue()
        self._events = self._ctx.Queue()
        self._cancel = self._ctx.Event()
        self._rss_process = None
        self._rss_sampled_at = 0.0
        self._process = self._ctx.Process(
            target=engine_child.main,
            args=(self._commands, self._events, self._cancel, self.engine),
            name=f"music-engine-{self._generation}",
            daemon=True,
        )
        self._process.start()

    def _sample_rss(self, force: bool = False) -> None:
        """Track the engine child's peak resident size during a run."""
        if psutil is None or self._process is None or not self._process.is_alive():
            return
        now = time.monotonic()
        if not force and now - self._rss_sampled_at < 1.0:
            return
        self._rss_sampled_at = now
        try:
            if self._rss_process is None or self._rss_process.pid != self._process.pid:
                self._rss_process = psutil.Process(self._process.pid)
            rss = self._rss_process.memory_info().rss
            for child in self._rss_process.children(recursive=True):
                try:
                    rss += child.memory_info().rss
                except psutil.Error:
                    pass
            self.peak_rss_bytes = max(self.peak_rss_bytes, rss)
        except psutil.Error:
            return

    def _await_ready(self, cancel_event: threading.Event | None = None) -> bool:
        """Wait for the child to finish loading.  False means load was cancelled."""
        deadline = time.monotonic() + self.load_timeout
        while True:
            if cancel_event is not None and cancel_event.is_set():
                if not self._discard():
                    self._blocked_reason = "engine child exit was not verified"
                    raise RuntimeError(self._blocked_reason)
                return False
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._discard()
                raise RuntimeError("engine did not finish loading before the timeout")
            try:
                event = self._events.get(timeout=min(2.0, remaining))
            except queue.Empty:
                event = None
            self._sample_rss()
            if event is not None:
                if event.get("kind") == "ready":
                    self._loaded = True
                    return True
                if event.get("kind") == "error":
                    detail = event.get("error") or "engine failed to load"
                    trace = event.get("traceback") or ""
                    self._discard()
                    raise RuntimeError(f"{detail}\n{trace.strip()}" if trace else detail)
            if self._process is None or not self._process.is_alive():
                self._discard()
                raise RuntimeError("engine process exited during model load")

    def _ensure_child(self, cancel_event: threading.Event | None = None) -> bool:
        if self._blocked_reason is not None:
            raise RuntimeError(self._blocked_reason)
        if self._process is not None and self._process.is_alive() and self._loaded:
            return True
        if self._process is not None:
            self._discard()
        self._spawn()
        return self._await_ready(cancel_event)

    # -- job execution -------------------------------------------------------
    def run(self, job_id: str, payload: dict, cancel_event: threading.Event,
            on_progress=None) -> dict:
        """Run one job.  Returns a dict with ``state`` done/error/cancelled.

        A cancel always destroys the child, even when the engine notices the
        shared event and stops by itself: after an interrupted diffusion the
        sampler/MLX state is not something the next job should inherit, and the
        caller asked for the process to stop.  The price is that the next submit
        pays the model load again.
        """
        self.peak_rss_bytes = 0
        self._rss_process = None
        self._rss_sampled_at = 0.0

        def result(state: str, **extra) -> dict:
            return {"state": state, "peak_rss_bytes": self.peak_rss_bytes, **extra}

        def stop_for_cancel() -> dict:
            dead = self._discard()
            if not dead:
                self._blocked_reason = "engine child exit was not verified"
                return result("error", error=self._blocked_reason,
                              error_code="ENGINE_CHILD_EXIT_UNVERIFIED",
                              terminated=False, resumed=False)
            return result("cancelled", error=None, terminated=True, resumed=False)

        try:
            if cancel_event.is_set():
                return result("cancelled", error=None, terminated=False, resumed=False)
            if not self._ensure_child(cancel_event):
                return result("cancelled", error=None, terminated=True, resumed=False)
            assert (self._commands is not None and self._events is not None
                    and self._cancel is not None)
            # Drain anything the previous job or the load phase left behind so a
            # stale terminal event can never be mistaken for this job's result.
            while True:
                try:
                    self._events.get_nowait()
                except queue.Empty:
                    break
            self._cancel.clear()
            self._commands.put({"job_id": job_id, "payload": payload})
            cancel_at: float | None = None
            while True:
                if cancel_event.is_set() and cancel_at is None:
                    cancel_at = time.monotonic()
                    self._cancel.set()
                try:
                    event = self._events.get(timeout=0.2)
                except queue.Empty:
                    event = None
                self._sample_rss()
                if event is not None and event.get("job_id") == job_id:
                    kind = event.get("kind")
                    if kind == "progress":
                        if on_progress is not None:
                            on_progress(event.get("ratio"), event.get("desc"))
                    elif kind == "done":
                        if cancel_at is not None:
                            return stop_for_cancel()
                        return result("done", path=event.get("path"),
                                      sample_rate=event.get("sample_rate"),
                                      engine_seconds=event.get("engine_seconds"),
                                      terminated=False, resumed=True)
                    elif kind == "cancelled":
                        return stop_for_cancel()
                    elif kind == "error":
                        detail = event.get("error") or "engine error"
                        trace = event.get("traceback") or ""
                        if cancel_at is not None:
                            return stop_for_cancel()
                        return result("error",
                                      error=f"{detail}\n{trace.strip()}" if trace else detail,
                                      terminated=False, resumed=True)
                if cancel_event.is_set() and cancel_at is not None:
                    if time.monotonic() - cancel_at >= self.cancel_grace:
                        self._sample_rss(force=True)
                        dead = self._discard()
                        if not dead:
                            self._blocked_reason = "engine child exit was not verified"
                        return result("cancelled" if dead else "error",
                                      error=None if dead else self._blocked_reason,
                                      error_code=(None if dead else
                                                  "ENGINE_CHILD_EXIT_UNVERIFIED"),
                                      terminated=dead, resumed=False)
                if self._process is None or not self._process.is_alive():
                    self._discard()
                    return result("error",
                                  error="engine process exited without a result",
                                  terminated=True, resumed=False)
        except BaseException as exc:  # noqa: BLE001 - never kill the job thread
            dead = self._discard()
            if not dead:
                self._blocked_reason = "engine child exit was not verified"
                return result("error", error=self._blocked_reason,
                              error_code="ENGINE_CHILD_EXIT_UNVERIFIED",
                              terminated=False, resumed=False)
            if cancel_event.is_set() and self._blocked_reason is None:
                return result("cancelled", error=None, terminated=True, resumed=False)
            return result("error", error=f"{type(exc).__name__}: {exc}",
                          terminated=True, resumed=False)

    def close(self) -> None:
        self._discard()


class GenerationManager:
    """Durable job state for one Music worker."""

    def __init__(self, data: Path, *, outputs: Path | None = None,
                 engine_root: str | None = None,
                 manifest_path: str | None = None, library_dir: str | None = None,
                 runtime_sha256: str | None = None, machine: str = "local") -> None:
        self.data = Path(data)
        self.data.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.outputs = Path(outputs) if outputs else self.data / "outputs"
        self.outputs.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.outputs.is_symlink():
            raise ValueError("Music output directory cannot be a symlink")
        self.staging = self.outputs / ".staging"
        self.staging.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.engine_state = self.data / "engine-state"
        if self.engine_state.is_symlink():
            raise ValueError("Music engine state cannot be a symlink")
        self.engine_state.mkdir(exist_ok=True, mode=0o700)
        self.state_file = self.data / "jobs.json"
        self.machine = machine
        env_root = os.environ.get("STUDIOHUB_MUSIC_ENGINE_ROOT")
        self.engine_root = str(engine_root or env_root or "").strip()
        self.manifest_path = manifest_path
        self.library_dir = library_dir
        # The worker's default engine is built eagerly, so a broken default
        # package still fails the worker at startup exactly as it always did.
        self.cache = cache_module.MusicCache(library=library_dir,
                                             manifest_path=manifest_path)
        # Every other model's package view is built from its own catalog entry on
        # first use (see _cache_for).  One broken package is recorded against its
        # own model instead of raised, because it must not stop the others: a
        # missing Stable Audio package used to raise here and take the whole
        # Music worker, ACE-Step included, down with it.
        self._model_caches: dict[str, object] = {DEFAULT_MODEL.repo: self.cache}
        self._model_cache_errors: dict[str, str] = {}
        package = self.cache.status()
        self.runtime_sha256 = runtime_sha256 or runtime_identity()
        self._executors: dict[str, EngineExecutor] = {
            DEFAULT_MODEL.repo: self._build_executor(
                DEFAULT_MODEL, package.get("checkpoints_root", "")),
        }
        self.jobs: dict[str, dict] = {}
        self.cancel_events: dict[str, threading.Event] = {}
        self.lock = threading.RLock()
        self.inference_lock = threading.Lock()
        self._restore()

    # -- persisted state -----------------------------------------------------
    def _restore(self) -> None:
        try:
            saved = json.loads(self.state_file.read_text())
            if not isinstance(saved, dict):
                raise ValueError("Music job history is malformed")
        except FileNotFoundError:
            saved = {}
        except (OSError, ValueError) as exc:
            raise RuntimeError(
                "Music job history is unreadable; original recovery evidence was preserved"
            ) from exc
        for job_id, job in saved.items():
            if not isinstance(job, dict) or job_id != job.get("id"):
                raise RuntimeError(
                    "Music job history is malformed; original recovery evidence was preserved")
            if job.get("state") not in TERMINAL:
                job.update(state="error",
                           error="Worker restarted before completion",
                           error_code="WORKER_RESTART_UNCERTAIN", finished_at=time.time())
            if job.get("state") == "done" and not final_matches(job, self.outputs / f"{job_id}.wav"):
                job.update(state="error", error="Final WAV is unavailable after restart",
                           error_code="OUTPUT_UNAVAILABLE", output_path=None, output_url=None)
            self.jobs[job_id] = job
        self._persist()

    def _persist(self) -> None:
        _atomic_json(self.state_file, self.jobs)

    # -- readiness / cache ---------------------------------------------------
    def _engine_contract_reason(self) -> str | None:
        return engine_contract_reason(self.engine_root)

    @property
    def executor(self) -> EngineExecutor:
        """The default model's resident engine child; ``/api/health`` reports it."""
        return self._executors[DEFAULT_MODEL.repo]

    def _build_executor(self, model: catalog.ModelEntry,
                        checkpoints_root: str) -> EngineExecutor:
        """One child, configured from one model's own catalog entry."""
        return EngineExecutor({
            "repo": model.repo,
            "engine_root": self.engine_root,
            "state_root": str(self.engine_state),
            "checkpoints_dir": checkpoints_root,
            "dit_model": _default(model, "dit_model"),
            "lm_model": _default(model, "lm_model"),
            "backend": os.environ.get("STUDIOHUB_MUSIC_BACKEND", "mlx"),
            "device": os.environ.get("STUDIOHUB_MUSIC_DEVICE", "auto"),
            "parent_pid": os.getpid(),
        })

    def _executor_for(self, model: catalog.ModelEntry) -> EngineExecutor:
        """This model's own resident child, built on first use.

        One child holds one model's weights, so a model that shares the ACE-Step
        runtime with the default engine gets its own checkpoint directory, DiT and
        LM instead of inheriting the ones already loaded.
        """
        if model.runtime != catalog.RUNTIME_ACE_STEP:
            raise ValueError(
                f"{model.repo} declares runtime {model.runtime!r}, which this worker "
                "has no resident engine for; add one in "
                "components/music/app/backend/generation.py")
        executor = self._executors.get(model.repo)
        if executor is not None:
            return executor
        files = self.preflight(model.repo)
        if files["state"] != "cached":
            raise RuntimeError(
                f"MODEL_FILES_UNAVAILABLE: {files.get('reason') or files['state']}")
        # Two submits can race here; the second must join the first's child rather
        # than spawn one nobody closes.
        with self.lock:
            executor = self._executors.get(model.repo)
            if executor is None:
                executor = self._build_executor(model, files["checkpoints_root"])
                self._executors[model.repo] = executor
        return executor

    def _runner_for(self, model: catalog.ModelEntry):
        """The job runner for this model's runtime, refused by name when unknown."""
        runners = {
            catalog.RUNTIME_ACE_STEP: self._run,
            catalog.RUNTIME_STABLE_AUDIO: self._run_sa3,
        }
        try:
            return runners[model.runtime]
        except KeyError:
            raise ValueError(
                f"{model.repo} declares runtime {model.runtime!r}, which this worker "
                "has no runner for; add one in "
                "components/music/app/backend/generation.py") from None

    def _cache_for(self, model: catalog.ModelEntry):
        """``(cache, error)`` for one model, resolved from its own catalog entry.

        The manifest name, package id and package root all come from that entry, so
        a model added later can never be verified or loaded from ACE-Step's package.
        A package that cannot be read is recorded against its own model rather than
        raised, so one broken package cannot stop the others.
        """
        if (model.repo not in self._model_caches
                and model.repo not in self._model_cache_errors):
            try:
                # The explicit manifest names the worker's default engine only;
                # every other model resolves its own <package_id>.json.
                explicit = (self.manifest_path if model.repo == DEFAULT_MODEL.repo
                            else None)
                self._model_caches[model.repo] = cache_module.MusicCache(
                    library=self.library_dir, manifest_path=explicit, repo=model.repo)
            except (OSError, ValueError, KeyError) as exc:
                self._model_cache_errors[model.repo] = f"{type(exc).__name__}: {exc}"
        return (self._model_caches.get(model.repo),
                self._model_cache_errors.get(model.repo))

    def cache_for(self, repo: str):
        """The verified package view for one catalog model, or a named refusal."""
        model = catalog.require_model(repo)
        cache, error = self._cache_for(model)
        if cache is None:
            raise ValueError(error or f"Music package {model.package_id} is unavailable")
        return cache

    def preflight(self, repo: str | None = None) -> dict:
        model = catalog.require_model(repo or DEFAULT_MODEL.repo)
        cache, error = self._cache_for(model)
        if cache is None:
            return {"state": "manifest_unavailable",
                    "reason": error or f"{model.package_id} is unavailable"}
        try:
            status = cache.status()
        except (OSError, ValueError, KeyError) as exc:
            return {"state": "manifest_unavailable", "reason": str(exc)}
        if model.runtime == catalog.RUNTIME_STABLE_AUDIO:
            # Stable Audio 3 ships its own vendored MLX runtime; it has no ACE
            # engine root to verify.
            return status
        if model.runtime != catalog.RUNTIME_ACE_STEP:
            return {"state": "runtime_unsupported",
                    "reason": (f"{model.repo} declares runtime {model.runtime!r}, which "
                               "this worker has no executor for; add one in "
                               "components/music/app/backend/generation.py")}
        if not self.engine_root:
            return {**status, "state": "engine_root_missing",
                    "reason": "STUDIOHUB_MUSIC_ENGINE_ROOT is not set"}
        if not Path(self.engine_root).is_dir():
            return {**status, "state": "engine_root_missing",
                    "reason": f"engine root does not exist: {self.engine_root}"}
        engine_reason = self._engine_contract_reason()
        if engine_reason:
            return {**status, "state": "engine_source_invalid", "reason": engine_reason}
        return status

    # -- queries -------------------------------------------------------------
    def get(self, job_id: str) -> dict | None:
        with self.lock:
            return dict(self.jobs[job_id]) if job_id in self.jobs else None

    def list(self) -> list[dict]:
        with self.lock:
            return [dict(job) for job in sorted(self.jobs.values(),
                    key=lambda value: value["created_at"], reverse=True)]

    @staticmethod
    def _activity_projection(job: dict, *, observed_at: float) -> dict:
        """Bound one Music job to the Hub's allowlisted activity contract.

        Only job-store fields are used.  The worker cannot tell a Hub-dispatched
        job from a direct one, so it reports ``source: direct`` and the Hub's
        broker ownership reconciliation remains authoritative.
        """
        params = job.get("params") if isinstance(job.get("params"), dict) else {}
        model = _activity_text(params.get("repo"), 500)
        state = str(job.get("state") or "")
        created_at = _activity_time(job.get("created_at"))
        started_at = _activity_time(job.get("started_at"))
        finished_at = _activity_time(job.get("finished_at"))
        projection = {
            "id": str(job.get("id") or "")[:160], "state": state, "model": model,
            "operation": "music", "progress": _activity_progress(job.get("progress")),
            "source": "direct",
        }
        if created_at is not None:
            projection["created_at"] = created_at
        if started_at is not None:
            projection["started_at"] = started_at
        if state in {"queued", "running"}:
            projection["updated_at"] = observed_at
        else:
            if finished_at is not None:
                projection["finished_at"] = finished_at
            if started_at is not None and finished_at is not None:
                projection["runtime_s"] = max(0.0, finished_at - started_at)
            code = _activity_text(job.get("error_code"), 80)
            if code:
                projection["error_code"] = code
        return projection

    def activity_snapshot(self, observed_at: float | None = None) -> dict:
        """Return the private, bounded activity contract the Hub polls.

        The Music job store is the only source.  An empty store yields a valid
        empty snapshot rather than a 404, so an idle worker still answers.
        """
        observed = float(time.time() if observed_at is None else observed_at)
        with self.lock:
            jobs = [dict(job) for job in self.jobs.values()]
        running = [job for job in jobs if job.get("state") == "running"]
        queued = [job for job in jobs if job.get("state") == "queued"]
        terminal = [job for job in jobs if job.get("state") in TERMINAL]
        active = max(
            running or queued,
            key=lambda job: (job.get("state") == "running",
                             _activity_time(job.get("started_at"))
                             or _activity_time(job.get("created_at")) or 0.0),
            default=None,
        )
        latest = max(
            terminal,
            key=lambda job: (_activity_time(job.get("finished_at"))
                             or _activity_time(job.get("created_at")) or 0.0,
                             _activity_time(job.get("created_at")) or 0.0),
            default=None,
        )
        return {
            "schema": "kh-studio.activity.v1", "studio": "music",
            "observed_at": observed,
            "active": self._activity_projection(active, observed_at=observed) if active else None,
            "latest": self._activity_projection(latest, observed_at=observed) if latest else None,
        }

    # -- submit / cancel -----------------------------------------------------
    def submit(self, raw: dict) -> dict:
        # validate_request owns the request shape; the model only has to be known
        # early enough to pick its own duration cap for this host.
        model = (catalog.require_model(str(raw.get("repo") or MODEL_ID))
                 if isinstance(raw, dict) else None)
        tier_cap = (_tier_cap_for_this_host(model) if model is not None
                    else DEFAULT_TIER_CAP_SECONDS)
        params = validate_request(raw, tier_cap_seconds=tier_cap)
        model = catalog.require_model(params["repo"])
        files = self.preflight(model.repo)
        if files["state"] != "cached":
            raise RuntimeError(f"MODEL_FILES_UNAVAILABLE: {files.get('reason') or files['state']}")
        runner = self._runner_for(model)
        if model.runtime == catalog.RUNTIME_ACE_STEP:
            self._executor_for(model).engine["checkpoints_dir"] = files["checkpoints_root"]
        job_id = uuid4().hex
        job = {
            "id": job_id, "mode": "txt2music", "params": params, "state": "queued",
            "progress": 0.0, "created_at": time.time(), "started_at": None,
            "finished_at": None, "generated_at": None, "duration_seconds": None,
            "engine_seconds": None, "output_path": None, "output_url": None,
            "sidecar_path": None, "sha256": None, "bytes": None, "media_type": None,
            "sample_rate_hz": None, "channels": None, "audio_duration_s": None,
            "frames": None, "resolved_seed": None, "dit_model": params["dit_model"],
            "lm_model": params["lm_model"],
            "model_revision": params.get("model_revision") or model.revision,
            "runtime_revision": self.runtime_sha256, "worker_id": "music",
            "machine_id": self.machine, "acknowledged": False,
            "error": None, "error_code": None,
        }
        with self.lock:
            self.jobs[job_id] = job
            self.cancel_events[job_id] = threading.Event()
            try:
                self._persist()
            except Exception:
                self.jobs.pop(job_id, None)
                self.cancel_events.pop(job_id, None)
                raise
        threading.Thread(target=runner, args=(job_id,), daemon=True,
                         name=f"music-{job_id[:8]}").start()
        return dict(job)

    def cancel(self, job_id: str) -> dict | None:
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                return None
            if job["state"] not in TERMINAL:
                event = self.cancel_events.get(job_id)
                if event is not None:
                    event.set()
                if job["state"] == "queued":
                    job.update(state="cancelled", finished_at=time.time())
                    self._persist()
            return dict(job)

    def ack(self, job_id: str) -> dict | None:
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None or job["state"] != "done":
                return None
            if not final_matches(job, self.outputs / f"{job_id}.wav"):
                return None
            if not job["acknowledged"]:
                previous_ack = job["acknowledged"]
                previous_time = job.get("receipt_acked_at")
                job["acknowledged"] = True
                job["receipt_acked_at"] = time.time()
                try:
                    self._persist()
                except Exception:
                    job["acknowledged"] = previous_ack
                    if previous_time is None:
                        job.pop("receipt_acked_at", None)
                    else:
                        job["receipt_acked_at"] = previous_time
                    raise
            return {"ok": True, "job_id": job_id, "sha256": job["sha256"]}

    # -- the run -------------------------------------------------------------
    def _run(self, job_id: str) -> None:
        staging = self.staging / job_id
        final = self.outputs / f"{job_id}.wav"
        sidecar = self.outputs / f"{job_id}.json"
        with self.inference_lock:
            job = None
            try:
                with self.lock:
                    event = self.cancel_events.get(job_id)
                    job = self.jobs.get(job_id)
                    if job is None:
                        return
                    if event is not None and event.is_set():
                        job.update(state="cancelled", finished_at=time.time())
                        self._persist()
                        return
                    job.update(state="running", progress=0.01, started_at=time.time())
                    try:
                        self._persist()
                    except Exception as exc:
                        raise _JobPersistenceFailed(str(exc)) from exc
                staging.mkdir(parents=True, exist_ok=True, mode=0o700)
                params = job["params"]
                model = catalog.require_model(params["repo"])
                seed = params["seed"]
                if seed == -1:
                    seed = random.randint(0, 2 ** 32 - 1)
                with self.lock:
                    job["resolved_seed"] = seed
                    self._persist()
                payload = {
                    "prompt": params["prompt"], "lyrics": params["lyrics"],
                    "instrumental": params["instrumental"],
                    "vocal_language": params["vocal_language"],
                    "duration": params["duration_seconds"], "steps": params["steps"],
                    "guidance_scale": params["guidance_scale"], "shift": params["shift"],
                    "seed": seed, "staging_dir": str(staging),
                }
                outcome = self._executor_for(model).run(
                    job_id, payload, self.cancel_events[job_id],
                    on_progress=lambda ratio, desc: self._progress(job_id, ratio, desc))
                if outcome["state"] != "done":
                    raise _JobFailed(outcome)
                produced = Path(str(outcome["path"]))
                try:
                    produced.resolve().relative_to(staging.resolve())
                except ValueError as exc:
                    raise RuntimeError("engine wrote outside the staging directory") from exc
                facts = inspect_wav(produced)
                if facts["sample_rate_hz"] != outcome.get("sample_rate"):
                    raise RuntimeError(
                        f"engine reported {outcome.get('sample_rate')} Hz but wrote "
                        f"{facts['sample_rate_hz']} Hz")
                if facts["sample_width_bytes"] != 2:
                    raise RuntimeError(
                        f"expected 16-bit PCM, got {facts['sample_width_bytes'] * 8}-bit")
                with self.lock:
                    os.replace(produced, final)
                    now = time.time()
                    created_at = job["created_at"]
                    sidecar_value = {
                        "schema": "studiohub.music-artifact", "schema_version": 1,
                        "job_id": job_id, "created_at": created_at,
                        "generated_at": now,
                        "request": {
                            "repo": params["repo"], "prompt": params["prompt"],
                            "lyrics": params["lyrics"],
                            "instrumental": params["instrumental"],
                            "vocal_language": params["vocal_language"],
                            "duration_seconds": params["duration_seconds"],
                            "steps": params["steps"],
                            "guidance_scale": params["guidance_scale"],
                            "shift": params["shift"],
                            "seed": params["seed"],
                            "resolved_seed": seed,
                            "tier_cap_seconds": params["tier_cap_seconds"],
                            "exceeds_single_pass": params["exceeds_single_pass"],
                        },
                        "model": {"repo": model.repo, "revision": model.revision,
                                  "package_id": model.package_id, "engine": ENGINE,
                                  "dit_model": params["dit_model"],
                                  "lm_model": params["lm_model"]},
                        "engine_seconds": outcome.get("engine_seconds"),
                        "runtime_seconds": round(now - job["started_at"], 3),
                        "artifact": {
                            "file": final.name, "bytes": facts["bytes"],
                            "sha256": facts["sha256"], "media_type": MEDIA_TYPE,
                            "format": "wav", "sample_width_bytes": facts["sample_width_bytes"],
                            "sample_rate_hz": facts["sample_rate_hz"],
                            "channels": facts["channels"], "frames": facts["frames"],
                            "audio_duration_s": facts["audio_duration_s"],
                        },
                    }
                    _atomic_json(sidecar, sidecar_value)
                    if self.cancel_events[job_id].is_set():
                        discarded, remaining = self._discard_published((final, sidecar))
                        if discarded:
                            job.update(state="cancelled", finished_at=time.time())
                        else:
                            job.update(
                                state="error", finished_at=time.time(), output_url=None,
                                unverified_artifact_paths=remaining,
                                error="Cancelled Music artifacts could not be removed; "
                                      "reconcile before retry",
                                error_code="JOB_PERSISTENCE_UNCERTAIN")
                    else:
                        job.update(
                            state="done", progress=1.0, finished_at=now, generated_at=now,
                            duration_seconds=now - job["started_at"],
                            engine_seconds=outcome.get("engine_seconds"),
                            output_path=str(final),
                            output_url=f"/api/generate/jobs/{job_id}/audio",
                            sidecar_path=str(sidecar), sha256=facts["sha256"],
                            bytes=facts["bytes"], media_type=MEDIA_TYPE,
                            sample_rate_hz=facts["sample_rate_hz"],
                            channels=facts["channels"],
                            audio_duration_s=facts["audio_duration_s"],
                            frames=facts["frames"],
                            engine_peak_rss_bytes=outcome.get("peak_rss_bytes"),
                            peak_process_rss_bytes=resource.getrusage(
                                resource.RUSAGE_SELF).ru_maxrss)
                    self._persist()
            except _JobFailed as failure:
                self._finish_failure(job, failure.outcome)
            except _JobPersistenceFailed as exc:
                self._finish_failure(job, {
                    "state": "error", "error": f"Music job state could not be persisted: {exc}",
                    "error_code": "JOB_PERSISTENCE_FAILED",
                })
            except Exception as exc:  # noqa: BLE001 - report the engine's own text
                published = [path for path in (final, sidecar)
                             if path.exists() or path.is_symlink()]
                if published:
                    discarded, remaining = self._discard_published(published)
                    outcome = {
                        "state": "error",
                        "error": ("Final Music artifact could not be persisted; "
                                  "artifacts were discarded" if discarded else
                                  "Final Music artifact could not be persisted; "
                                  "reconcile before retry"),
                        "error_code": ("JOB_PERSISTENCE_FAILED" if discarded else
                                       "JOB_PERSISTENCE_UNCERTAIN"),
                        "clear_artifacts": discarded,
                        "unverified_artifact_paths": remaining,
                    }
                else:
                    outcome = {"state": "error",
                               "error": f"{type(exc).__name__}: {exc}"}
                self._finish_failure(job, outcome)
            finally:
                self._cleanup_staging(staging)
                self.cancel_events.pop(job_id, None)

    def _run_sa3(self, job_id: str) -> None:
        """Render one Stable Audio 3 clip; a short-lived MLX subprocess per job."""
        final = self.outputs / f"{job_id}.wav"
        sidecar = self.outputs / f"{job_id}.json"
        with self.inference_lock:
            job = None
            try:
                with self.lock:
                    event = self.cancel_events.get(job_id)
                    job = self.jobs.get(job_id)
                    if job is None:
                        return
                    if event is not None and event.is_set():
                        job.update(state="cancelled", finished_at=time.time())
                        self._persist()
                        return
                    job.update(state="running", progress=0.05, started_at=time.time())
                    self._persist()
                params = job["params"]
                model = catalog.require_model(params["repo"])
                seed = params["seed"]
                if seed == -1:
                    seed = random.randint(0, 2 ** 32 - 1)
                with self.lock:
                    job["resolved_seed"] = seed
                    self._persist()
                files = self.preflight(model.repo)
                if files["state"] != "cached":
                    raise _JobFailed({
                        "state": "error",
                        "error": ("MODEL_FILES_UNAVAILABLE: "
                                  + str(files.get("reason") or files["state"])),
                        "error_code": "MODEL_FILES_UNAVAILABLE"})
                facts = stable_audio.generate(
                    prompt=params["prompt"], seconds=params["duration_seconds"],
                    steps=params["steps"], seed=seed,
                    cfg=params["guidance_scale"],
                    weights=Path(files["checkpoints_root"]), output=final,
                    cancel_event=self.cancel_events.get(job_id),
                    on_progress=lambda ratio, label: self._progress(
                        job_id, ratio, label))
                with self.lock:
                    now = time.time()
                    _atomic_json(sidecar, {
                        "schema": "studiohub.music-artifact", "schema_version": 1,
                        "job_id": job_id, "created_at": job["created_at"],
                        "generated_at": now,
                        "request": {
                            "repo": params["repo"], "prompt": params["prompt"],
                            "duration_seconds": params["duration_seconds"],
                            "steps": params["steps"], "seed": seed,
                            "guidance_scale": params["guidance_scale"]},
                        "artifact": {key: facts[key] for key in (
                            "bytes", "sha256", "audio_duration_s", "sample_rate_hz",
                            "channels", "frames")},
                    })
                    job.update(
                        state="done", progress=1.0, finished_at=now, generated_at=now,
                        duration_seconds=now - job["started_at"],
                        engine_seconds=facts["engine_seconds"],
                        output_path=str(final),
                        output_url=f"/api/generate/jobs/{job_id}/audio",
                        sidecar_path=str(sidecar), sha256=facts["sha256"],
                        bytes=facts["bytes"], media_type=MEDIA_TYPE,
                        sample_rate_hz=facts["sample_rate_hz"],
                        channels=facts["channels"],
                        audio_duration_s=facts["audio_duration_s"],
                        frames=facts["frames"],
                        peak_process_rss_bytes=resource.getrusage(
                            resource.RUSAGE_SELF).ru_maxrss)
                    self._persist()
            except stable_audio.Sa3Cancelled:
                final.unlink(missing_ok=True)
                sidecar.unlink(missing_ok=True)
                self._finish_failure(job, {"state": "cancelled"})
            except _JobFailed as failure:
                self._finish_failure(job, failure.outcome)
            except Exception as exc:  # noqa: BLE001 - report the engine's own text
                final.unlink(missing_ok=True)
                sidecar.unlink(missing_ok=True)
                self._finish_failure(job, {
                    "state": "error", "error": f"{type(exc).__name__}: {exc}"})
            finally:
                self.cancel_events.pop(job_id, None)

    def _progress(self, job_id: str, ratio, desc) -> None:
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None or job["state"] not in ("queued", "running"):
                return
            job["progress"] = round(float(ratio), 4)
            if desc:
                job["progress_stage"] = str(desc)[:200]
            job["last_progress_at"] = time.time()

    def _finish_failure(self, job: dict, outcome: dict) -> None:
        job_id = job["id"]
        with self.lock:
            event = self.cancel_events.get(job_id)
            explicit_error = bool(outcome.get("error") or outcome.get("error_code")
                                  or outcome.get("state") == "error")
            cancelled = bool(outcome.get("state") == "cancelled"
                             or (not explicit_error and event is not None and event.is_set()))
            job["engine_peak_rss_bytes"] = outcome.get("peak_rss_bytes")
            if cancelled:
                job.update(state="cancelled", error=None, error_code=None)
            else:
                text = str(outcome.get("error") or "music generation failed")
                job.update(state="error", error=text[:4000],
                           error_code=(outcome.get("error_code")
                                       or ("ENGINE_CHILD_EXIT_UNVERIFIED"
                                           if "not verified" in text
                                           else "MUSIC_GENERATION_FAILED")))
                if outcome.get("clear_artifacts"):
                    job.update(output_path=None, output_url=None, sidecar_path=None,
                               sha256=None, bytes=None, media_type=None)
                if outcome.get("unverified_artifact_paths"):
                    job["unverified_artifact_paths"] = list(
                        outcome["unverified_artifact_paths"])
                    job["output_url"] = None
            job["finished_at"] = time.time()
            if job.get("started_at"):
                job["duration_seconds"] = job["finished_at"] - job["started_at"]
            try:
                self._persist()
            except Exception:
                pass

    @staticmethod
    def _cleanup_staging(staging: Path) -> None:
        try:
            if staging.is_dir() and not staging.is_symlink():
                for child in staging.iterdir():
                    child.unlink(missing_ok=True)
                staging.rmdir()
        except OSError:
            pass

    @staticmethod
    def _discard_published(paths) -> tuple[bool, list[str]]:
        remaining = []
        for path in paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            if path.exists() or path.is_symlink():
                remaining.append(str(path))
        return not remaining, remaining

    # -- memory policy -------------------------------------------------------
    # `memory_policy.py` reads the manager through these three methods and releases
    # through the fourth, the way the Voice and Subtitle workers' policies do.  They are
    # the single definition of "active" and "resident" for this worker, so a status probe
    # and a release can never disagree about what is being held.
    def has_active_jobs(self) -> bool:
        """Is a job queued or running? A release must never preempt one."""
        with self.lock:
            return any(job.get("state") in ("queued", "running")
                       for job in self.jobs.values())

    def loaded_model_keys(self) -> list[tuple[str, str]]:
        """Every model this worker is holding, as ``(repo, runtime)`` pairs.

        One spawned engine child holds one model's weights, so a live child *is* resident
        memory - during its load as much as after it.  Stable Audio 3 never appears here:
        it runs a fresh subprocess per job and exits, so it has no residency to report.
        """
        loaded: list[tuple[str, str]] = []
        with self.lock:
            executors = list(self._executors.items())
        for repo, executor in executors:
            if getattr(executor, "child_pid", None) is None:
                continue
            model = catalog.get_model(repo)
            loaded.append((repo, model.runtime if model is not None else catalog.ENGINE))
        return loaded

    def has_loaded_model(self) -> bool:
        """Is any engine child resident right now? One definition, used by both paths."""
        return bool(self.loaded_model_keys())

    def release_memory_locked(self, reason: str = "manual") -> dict:
        """Discard every resident engine child, refusing while work is active.

        This is the only lifecycle that exists: ``EngineExecutor._discard()`` stops the
        child, and the next submit's ``_ensure_child`` spawns a fresh one that loads the
        model again.  A child whose exit was not verified stays resident and is reported
        as ``released: False`` for its own model - a release that did not happen is never
        reported as one.
        """
        if self.has_active_jobs():
            raise RuntimeError("music generation is active; memory was not released")
        released = False
        models: list[list[str]] = []
        actions: list[dict] = []
        with self.lock:
            executors = list(self._executors.items())
        for repo, executor in executors:
            pid = getattr(executor, "child_pid", None)
            if pid is None:
                continue
            model = catalog.get_model(repo)
            models.append([repo, model.runtime if model is not None else catalog.ENGINE])
            action = {"model": repo, "engine_child_pid": pid}
            try:
                discarded = bool(executor._discard())
            except Exception as exc:  # noqa: BLE001 - report the failure, not a release
                discarded = False
                action["error"] = f"{type(exc).__name__}: {exc}"
            action["released"] = discarded
            actions.append(action)
            released = released or discarded
        return {"released": released, "models": models, "actions": actions,
                "reason": reason}

    def close(self) -> None:
        for executor in self._executors.values():
            executor.close()


class _JobPersistenceFailed(RuntimeError):
    pass


class _JobFailed(RuntimeError):
    def __init__(self, outcome: dict) -> None:
        super().__init__(str(outcome.get("error") or ""))
        self.outcome = outcome


def _tier_cap_for_this_host(model: catalog.ModelEntry) -> int:
    """The model's own single-pass cap for the tier this Mac lands in.

    The authoritative selection happens inside the engine from real GPU memory;
    this mirrors the same boundaries from the model's own tier table, so the
    catalogue, the request validation and the job record agree on the number the
    probe observed (480 s at tier4 for ACE-Step).  Note the engine measures MPS
    working-set memory (~11.8 GB here) while this reads total unified memory (16 GB
    here); both land on a 480 s cap, and the engine remains the one that enforces it.

    A model that publishes no tier table falls back to its own single-pass limit —
    never to ACE-Step's host tier.
    """
    try:
        raw = subprocess.check_output(["/usr/sbin/sysctl", "-n", "hw.memsize"],
                                      text=True, timeout=3).strip()
        total_gb = int(raw) / 1e9
    except (OSError, ValueError, subprocess.SubprocessError):
        return int(_limit(model, "single_pass_max_seconds"))
    selected = model.tiers[0] if model.tiers else None
    for tier in model.tiers:
        if tier.min_unified_memory_gb is not None and total_gb >= tier.min_unified_memory_gb:
            selected = tier
    if selected is None:
        return int(_limit(model, "single_pass_max_seconds"))
    return selected.max_duration_with_lm_s
