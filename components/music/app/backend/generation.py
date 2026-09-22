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


MODEL_ID = catalog.MAIN_REPO
MODEL_REVISION = catalog.MAIN_REVISION
PACKAGE_ID = catalog.PACKAGE_ID
ENGINE = catalog.ENGINE
DIT_MODEL = "acestep-v15-turbo"
LM_MODEL = "acestep-5Hz-lm-0.6B"
AUDIO_FORMAT = "wav"
MEDIA_TYPE = "audio/wav"

DEFAULTS = {
    "duration_seconds": 30.0,
    "steps": 8,
    "guidance_scale": 1.0,
    "shift": 3.0,
    "seed": -1,
    "instrumental": True,
    "lyrics": "[Instrumental]",
    "vocal_language": "unknown",
    "dit_model": DIT_MODEL,
    "lm_model": LM_MODEL,
}
# The engine's own absolute supported maximum (GPU_TIER_CONFIGS["unlimited"]).
MAX_DURATION_SECONDS = 600.0
MIN_DURATION_SECONDS = 1.0
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


def _validate_stable_audio(raw: dict) -> dict:
    """Stable Audio 3 parameters: instrumental only, up to 380 s."""
    prompt = raw.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4000:
        raise ValueError("Prompt must contain 1-4000 characters")
    duration = raw.get("duration_seconds", raw.get("duration", 30))
    duration = _finite_number(duration, name="duration", low=1.0,
                              high=float(catalog.SA3_MAX_DURATION_SECONDS))
    steps = raw.get("steps", raw.get("inference_steps", 8))
    if type(steps) is not int or not 1 <= steps <= 50:
        raise ValueError("Steps must be an integer between 1 and 50")
    guidance = _finite_number(
        raw.get("guidance_scale", raw.get("cfg", 1.0)),
        name="guidance_scale", low=0.0, high=30.0)
    seed = raw.get("seed", -1)
    if seed is None:
        seed = -1
    if type(seed) is not int or not -1 <= seed <= 2 ** 32 - 1:
        raise ValueError("Seed must be -1 or a 32-bit unsigned integer")
    return {
        "repo": catalog.STABLE_AUDIO_REPO,
        "model_revision": catalog.STABLE_AUDIO_REVISION,
        "prompt": prompt.strip(), "duration_seconds": duration, "steps": steps,
        "guidance_scale": guidance, "seed": seed,
        # The engine has no vocals; keep the shared job shape honest about that.
        "instrumental": True, "lyrics": "[Instrumental]", "vocal_language": "unknown",
        "dit_model": "dit_medium", "lm_model": "",
        "tier_cap_seconds": catalog.SA3_MAX_DURATION_SECONDS,
        "exceeds_single_pass": False,
    }


def validate_request(raw: dict, tier_cap_seconds: int = 480) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("Music request must be an object")
    repo = str(raw.get("repo") or MODEL_ID)
    if catalog.is_stable_audio(repo):
        return _validate_stable_audio(raw)
    if repo not in (catalog.MAIN_REPO, catalog.PACKAGE_ID):
        raise ValueError(
            f"Only {catalog.MAIN_REPO} or {catalog.STABLE_AUDIO_REPO} is available "
            f"on this worker; got {repo!r}")
    revision = raw.get("model_revision")
    if revision is not None and revision != MODEL_REVISION:
        raise ValueError("Only the pinned ACE-Step 1.5 revision is available")
    prompt = raw.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4000:
        raise ValueError("Prompt must contain 1-4000 characters")

    duration = raw.get("duration_seconds", raw.get("duration", DEFAULTS["duration_seconds"]))
    if duration is None:
        duration = DEFAULTS["duration_seconds"]
    duration = _finite_number(duration, name="duration", low=MIN_DURATION_SECONDS,
                              high=MAX_DURATION_SECONDS)

    steps = raw.get("steps", raw.get("inference_steps", DEFAULTS["steps"]))
    if type(steps) is not int or not 1 <= steps <= 50:
        raise ValueError("Steps must be an integer between 1 and 50")

    guidance = _finite_number(
        raw.get("guidance_scale", DEFAULTS["guidance_scale"]),
        name="guidance_scale", low=0.0, high=30.0)
    shift = _finite_number(raw.get("shift", DEFAULTS["shift"]),
                           name="shift", low=0.0, high=20.0)

    seed = raw.get("seed", DEFAULTS["seed"])
    if seed is None:
        seed = -1
    if type(seed) is not int or not -1 <= seed <= 2 ** 32 - 1:
        raise ValueError("Seed must be -1 or a 32-bit unsigned integer")

    instrumental = raw.get("instrumental", DEFAULTS["instrumental"])
    if not isinstance(instrumental, bool):
        raise ValueError("instrumental must be a boolean")
    lyrics = raw.get("lyrics", DEFAULTS["lyrics"])
    if not isinstance(lyrics, str) or len(lyrics) > 8000:
        raise ValueError("Lyrics must be a string of at most 8000 characters")
    if instrumental and not lyrics.strip():
        lyrics = DEFAULTS["lyrics"]
    language = raw.get("vocal_language", DEFAULTS["vocal_language"])
    if not isinstance(language, str) or len(language) > 32:
        raise ValueError("vocal_language must be a short string")

    dit_model = str(raw.get("dit_model") or DIT_MODEL)
    lm_model = str(raw.get("lm_model") or LM_MODEL)
    if dit_model != DIT_MODEL:
        raise ValueError(f"Only the {DIT_MODEL} checkpoint is installed")
    if lm_model != LM_MODEL:
        raise ValueError(f"Only the {LM_MODEL} checkpoint is installed")

    return {
        "repo": MODEL_ID, "model_revision": MODEL_REVISION, "prompt": prompt.strip(),
        "duration_seconds": duration, "steps": steps, "guidance_scale": guidance,
        "shift": shift, "seed": seed, "instrumental": instrumental,
        "lyrics": lyrics, "vocal_language": language,
        "dit_model": dit_model, "lm_model": lm_model,
        "tier_cap_seconds": int(tier_cap_seconds),
        "exceeds_single_pass": duration > tier_cap_seconds,
    }


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
        self.cache = cache_module.MusicCache(library=library_dir,
                                             manifest_path=manifest_path)
        self.sa3_cache = cache_module.MusicCache(
            library=library_dir, repo=catalog.STABLE_AUDIO_REPO)
        package = self.cache.status()
        self.runtime_sha256 = runtime_sha256 or runtime_identity()
        self.executor = EngineExecutor({
            "engine_root": self.engine_root,
            "state_root": str(self.engine_state),
            "checkpoints_dir": package.get("checkpoints_root", ""),
            "dit_model": DIT_MODEL,
            "lm_model": LM_MODEL,
            "backend": os.environ.get("STUDIOHUB_MUSIC_BACKEND", "mlx"),
            "device": os.environ.get("STUDIOHUB_MUSIC_DEVICE", "auto"),
            "parent_pid": os.getpid(),
        })
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

    def preflight(self, repo: str | None = None) -> dict:
        stable = catalog.is_stable_audio(repo or "")
        cache = self.sa3_cache if stable else self.cache
        try:
            status = cache.status()
        except (OSError, ValueError, KeyError) as exc:
            return {"state": "manifest_unavailable", "reason": str(exc)}
        if stable:
            # Stable Audio 3 ships its own vendored MLX runtime; it has no ACE
            # engine root to verify.
            return status
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

    # -- submit / cancel -----------------------------------------------------
    def submit(self, raw: dict) -> dict:
        tier_cap = _tier_cap_for_this_host()
        params = validate_request(raw, tier_cap_seconds=tier_cap)
        stable = catalog.is_stable_audio(params["repo"])
        files = self.preflight(params["repo"])
        if files["state"] != "cached":
            raise RuntimeError(f"MODEL_FILES_UNAVAILABLE: {files.get('reason') or files['state']}")
        if not stable:
            self.executor.engine["checkpoints_dir"] = files["checkpoints_root"]
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
            "model_revision": params.get("model_revision") or MODEL_REVISION,
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
        threading.Thread(target=(self._run_sa3 if stable else self._run),
                         args=(job_id,), daemon=True,
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
                outcome = self.executor.run(
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
                        "model": {"repo": MODEL_ID, "revision": MODEL_REVISION,
                                  "package_id": PACKAGE_ID, "engine": ENGINE,
                                  "dit_model": DIT_MODEL, "lm_model": params["lm_model"]},
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
                seed = params["seed"]
                if seed == -1:
                    seed = random.randint(0, 2 ** 32 - 1)
                with self.lock:
                    job["resolved_seed"] = seed
                    self._persist()
                files = self.preflight(catalog.STABLE_AUDIO_REPO)
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

    def close(self) -> None:
        self.executor.close()


class _JobPersistenceFailed(RuntimeError):
    pass


class _JobFailed(RuntimeError):
    def __init__(self, outcome: dict) -> None:
        super().__init__(str(outcome.get("error") or ""))
        self.outcome = outcome


def _tier_cap_for_this_host() -> int:
    """The engine's ``max_duration_with_lm`` for the tier this Mac lands in.

    The authoritative selection happens inside the engine from real GPU memory;
    this mirrors the same boundaries so the catalogue, the request validation
    and the job record agree on the number the probe observed (480 s at tier4).
    Note the engine measures MPS working-set memory (~11.8 GB here) while this
    reads total unified memory (16 GB here); both land on a 480 s cap, and the
    engine remains the one that enforces it.
    """
    try:
        raw = subprocess.check_output(["/usr/sbin/sysctl", "-n", "hw.memsize"],
                                      text=True, timeout=3).strip()
        total_gb = int(raw) / 1e9
    except (OSError, ValueError, subprocess.SubprocessError):
        return 480
    selected = catalog.TIERS[0]
    for tier in catalog.TIERS:
        if tier.min_unified_memory_gb is not None and total_gb >= tier.min_unified_memory_gb:
            selected = tier
    return selected.max_duration_with_lm_s
