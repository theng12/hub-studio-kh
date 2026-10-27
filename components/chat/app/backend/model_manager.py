"""Manifest-driven Chat model sessions with exactly one resident engine."""
from __future__ import annotations

from contextlib import contextmanager
import gc
import inspect
import json
from pathlib import Path
import re
import threading
from typing import Any, Callable, Iterator

from components import model_packages

from .llm_engine import ChatEngine, ChatModelUnavailable, resolve_model_dir


class ManifestCatalogError(ValueError):
    pass


def _clear_mlx_cache() -> None:
    try:
        import mlx.core as mx

        mx.clear_cache()
    except (ImportError, AttributeError):
        pass


def _accepts_keyword(method: Callable[..., Any], name: str) -> bool:
    """Inspect compatibility before a call; generation is never retried after TypeError."""
    try:
        parameters = inspect.signature(method).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(parameter.kind == inspect.Parameter.VAR_KEYWORD or parameter.name == name
               for parameter in parameters)


def _pending_reason(manifest: dict) -> str:
    qualification = manifest.get("qualification")
    reason = qualification.get("reason") if isinstance(qualification, dict) else None
    if isinstance(reason, str) and reason.strip():
        return reason.strip()
    return "this checkpoint is not qualified for production inference"


def read_chat_manifests(catalog: Path) -> list[tuple[Path, dict]]:
    """Read and validate every bundled ``chat-mlx`` manifest."""
    found: list[tuple[Path, dict]] = []
    for path in sorted(Path(catalog).glob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ManifestCatalogError(f"Model manifest {path.name} cannot be read: {exc}") from exc
        if not isinstance(value, dict) or value.get("engine") != "chat-mlx":
            continue
        try:
            model_packages.validate_manifest(value)
        except (TypeError, ValueError) as exc:
            raise ManifestCatalogError(f"Invalid Chat manifest {path.name}: {exc}") from exc
        found.append((path, value))
    if not found:
        raise ManifestCatalogError(
            f"this bundle carries no chat-mlx manifest in {Path(catalog).name}/")

    package_ids: dict[str, str] = {}
    model_ids: dict[str, str] = {}
    for path, manifest in found:
        package_id, model_id = manifest["package_id"], manifest["model_id"]
        if "default" in manifest and type(manifest["default"]) is not bool:
            raise ManifestCatalogError(
                f"Chat manifest {path.name} has a non-boolean default: "
                f"{manifest['default']!r}")
        if "vision" in manifest and type(manifest["vision"]) is not bool:
            raise ManifestCatalogError(
                f"Chat manifest {path.name} has a non-boolean vision: "
                f"{manifest['vision']!r}")
        if package_id in package_ids:
            raise ManifestCatalogError(
                f"duplicate package_id {package_id!r} in {package_ids[package_id]} and {path.name}")
        if model_id in model_ids:
            raise ManifestCatalogError(
                f"duplicate model_id {model_id!r} in {model_ids[model_id]} and {path.name}")
        revision = manifest.get("revision")
        if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ManifestCatalogError(
                f"Chat manifest {path.name} needs one full immutable revision")
        package_ids[package_id] = path.name
        model_ids[model_id] = path.name

    select_default_manifest(found)
    return found


def select_default_manifest(entries: list[tuple[Path, dict]]) -> tuple[Path, dict]:
    """Use one explicit eligible default, or the sole production model for legacy catalogs."""
    defaults = [(path, manifest) for path, manifest in entries
                if manifest.get("default") is True]
    if len(defaults) > 1:
        names = ", ".join(path.name for path, _manifest in defaults)
        raise ManifestCatalogError(
            f"Chat manifests need one default; found {len(defaults)} explicit defaults ({names})")
    if defaults:
        if not model_packages.production_allowed(defaults[0][1]):
            raise ManifestCatalogError(
                f"Chat default {defaults[0][0].name} is not allowed for production")
        return defaults[0]
    eligible = [(path, manifest) for path, manifest in entries
                if model_packages.production_allowed(manifest)]
    if len(eligible) == 1:
        return eligible[0]
    raise ManifestCatalogError(
        "Chat manifests need one default: true, unless exactly one model is allowed for "
        f"production; found {len(eligible)} production models and no explicit default")


class ChatModelProxy:
    """One model's immutable metadata and manager-owned generation sessions."""

    def __init__(self, manager: "ChatModelManager", engine: ChatEngine,
                 manifest: dict, manifest_path: Path) -> None:
        self._manager = manager
        self._engine = engine
        self.manifest = manifest
        self.manifest_path = manifest_path
        self.production_allowed = model_packages.production_allowed(manifest)
        self.production_refusal = None if self.production_allowed else _pending_reason(manifest)
        # The checkpoint's own answer (a `vision_config` in its `config.json`), via the engine
        # that read that file — not a hand-written manifest flag. `__getattr__` would forward
        # this too; it is spelled out so a reader can see where the capability comes from.
        self.supports_vision = bool(getattr(engine, "supports_vision", False))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._engine, name)

    @property
    def model_id(self) -> str:
        return str(self._engine.model_id)

    @property
    def revision(self) -> str:
        return str(self._engine.revision)

    def loaded(self) -> bool:
        return self._engine.loaded()

    def _callback(self, callback: Callable[[dict], None] | None) -> Callable[[dict], None] | None:
        if callback is None:
            return None

        def bound(event: dict) -> None:
            value = dict(event) if isinstance(event, dict) else {}
            value.setdefault("attempt", 1)
            value["model"] = self.model_id
            callback(value)

        return bound

    def _refuse_pending(self) -> None:
        if not self.production_allowed:
            raise ChatModelUnavailable(
                f"{self.model_id} is not qualified for production inference: "
                f"{self.production_refusal}")

    def check_prompt_fits(self, messages: list[dict], *, on_progress=None,
                          **kwargs: Any) -> int | None:
        self._refuse_pending()
        callback = self._callback(on_progress)
        if callback is not None:
            callback({"phase": "waiting", "attempt": 1})
        with self._manager.session(self):
            if callback is not None and not self._engine.loaded():
                callback({"phase": "loading", "attempt": 1})
            return self._engine.check_prompt_fits(messages, **kwargs)

    def _usage(self, messages: list[dict], text: str, prompt_tokens=None) -> dict:
        if not isinstance(prompt_tokens, int) or isinstance(prompt_tokens, bool):
            prompt_tokens = self._engine.prompt_length(messages)
        prompt = (prompt_tokens if isinstance(prompt_tokens, int)
                  and not isinstance(prompt_tokens, bool) and prompt_tokens >= 0 else None)
        counted = self._engine.count_tokens(text)
        completion = (counted if isinstance(counted, int)
                      and not isinstance(counted, bool) and counted >= 0 else None)
        total = prompt + completion if prompt is not None and completion is not None else None
        return {"prompt_tokens": prompt, "completion_tokens": completion,
                "total_tokens": total}

    def complete(self, messages: list[dict], *, on_progress=None, **kwargs: Any) -> dict:
        self._refuse_pending()
        callback = self._callback(on_progress)
        if callback is not None:
            callback({"phase": "waiting", "attempt": 1})
        with self._manager.session(self):
            completed = None

            def relay(event: dict) -> None:
                nonlocal completed
                if event.get("phase") == "completed":
                    completed = dict(event)
                elif callback is not None:
                    callback(event)

            if callback is not None and _accepts_keyword(self._engine.complete, "on_progress"):
                outcome = self._engine.complete(messages, on_progress=relay, **kwargs)
            else:
                outcome = self._engine.complete(messages, **kwargs)
            usage = outcome.get("usage")
            if not isinstance(usage, dict):
                usage = self._usage(messages, str(outcome.get("text") or ""),
                                    outcome.get("prompt_tokens"))
                outcome["usage"] = usage
            if callback is not None:
                event = completed or {"phase": "completed", "attempt": 1}
                event["usage"] = usage
                if "telemetry" not in event and isinstance(outcome.get("telemetry"), dict):
                    event["telemetry"] = outcome["telemetry"]
                callback(event)
            return outcome

    def stream(self, messages: list[dict], *, on_progress=None, **kwargs: Any) -> Iterator[str]:
        self._refuse_pending()
        callback = self._callback(on_progress)

        def run() -> Iterator[str]:
            if callback is not None:
                callback({"phase": "waiting", "attempt": 1})
            with self._manager.session(self):
                completed = None

                def relay(event: dict) -> None:
                    nonlocal completed
                    if event.get("phase") == "completed":
                        completed = dict(event)
                    elif callback is not None:
                        callback(event)

                if callback is not None and _accepts_keyword(self._engine.stream, "on_progress"):
                    inner = self._engine.stream(messages, on_progress=relay, **kwargs)
                else:
                    inner = self._engine.stream(messages, **kwargs)
                pieces: list[str] = []
                try:
                    while True:
                        try:
                            piece = next(inner)
                        except StopIteration as stop:
                            usage = ((completed or {}).get("usage")
                                     if isinstance(completed, dict) else None)
                            if not isinstance(usage, dict):
                                usage = self._usage(messages, "".join(pieces))
                            if callback is not None:
                                event = completed or {"phase": "completed", "attempt": 1}
                                event["usage"] = usage
                                callback(event)
                            return stop.value
                        pieces.append(piece)
                        yield piece
                finally:
                    inner.close()

        return run()


class ChatModelManager:
    """All Chat metadata, one plain global lock, and at most one resident engine."""

    def __init__(self, entries: list[tuple[Path, dict]], models_dir: Path, *,
                 engine_factory: Callable[..., ChatEngine] = ChatEngine,
                 collect: Callable[[], Any] = gc.collect,
                 clear_cache: Callable[[], Any] = _clear_mlx_cache) -> None:
        self._lock = threading.Lock()
        self._collect = collect
        self._clear_cache = clear_cache
        self._resident: ChatModelProxy | None = None
        proxies: list[ChatModelProxy] = []
        for path, manifest in entries:
            qualification = manifest.get("qualification") or {}
            engine = engine_factory(
                model_dir=resolve_model_dir(models_dir, manifest["package_id"], manifest["revision"]),
                model_id=manifest["model_id"],
                revision=manifest["revision"],
                max_output_tokens=manifest.get("max_output_tokens"),
                context_window_tokens=qualification.get("context_window_tokens"),
                manifest=manifest,
                manifest_path=path,
            )
            proxies.append(ChatModelProxy(self, engine, manifest, path))
        self.models = tuple(proxies)
        self._by_id = {model.model_id: model for model in self.models}
        default_path, _manifest = select_default_manifest(entries)
        self.default = next(model for model in self.models if model.manifest_path == default_path)

    @classmethod
    def from_catalog(cls, catalog: Path, models_dir: Path, **kwargs: Any) -> "ChatModelManager":
        return cls(read_chat_manifests(catalog), Path(models_dir), **kwargs)

    def for_model(self, model_id: str | None) -> ChatModelProxy:
        if not model_id:
            return self.default
        try:
            return self._by_id[model_id]
        except KeyError as exc:
            raise KeyError(f"unknown Chat model {model_id!r}") from exc

    def _discard(self, model: ChatModelProxy, reason: str) -> dict:
        result = model._engine.release(reason=reason, force=True)
        self._collect()
        self._clear_cache()
        if self._resident is model:
            self._resident = None
        return result

    def _switch(self, selected: ChatModelProxy) -> None:
        current = self._resident
        if current is selected:
            return
        if current is not None:
            self._discard(current, "model_switch")
        self._resident = selected

    @contextmanager
    def session(self, selected: ChatModelProxy):
        self._lock.acquire()
        try:
            self._switch(selected)
            try:
                yield
            except BaseException:
                # A load that never established residency must not leave partial engine state.
                if not selected.loaded():
                    self._discard(selected, "load_failed")
                raise
        finally:
            self._lock.release()

    def loaded(self) -> bool:
        current = self._resident
        return current is not None and current.loaded()

    @property
    def model_id(self) -> str:
        current = self._resident
        return current.model_id if current is not None and current.loaded() else self.default.model_id

    def status(self) -> dict:
        current = self._resident
        busy = self._lock.locked()
        if busy:
            # Do not wait on the selected engine's own generation lock: health and activity are
            # what expose a running session while that lock is intentionally held.
            return {"loaded": bool(current and current.loaded()), "busy": True,
                    "model_id": current.model_id if current and current.loaded() else None,
                    "active_memory_bytes": None, "peak_memory_bytes": None}
        state = current.status() if current is not None else self.default.status()
        return {**state, "busy": False,
                "model_id": current.model_id if current and current.loaded() else None}

    def last_activity_at(self) -> float | None:
        current = self._resident
        return current.last_activity_at() if current is not None else None

    def release(self, *, reason: str = "requested", force: bool = False) -> dict:
        del force  # the manager lock guarantees no generation is active once acquired
        with self._lock:
            current = self._resident
            if current is None:
                return {"released": False, "reason": reason}
            return self._discard(current, reason)


__all__ = ["ChatModelManager", "ChatModelProxy", "ManifestCatalogError",
           "read_chat_manifests", "select_default_manifest"]
