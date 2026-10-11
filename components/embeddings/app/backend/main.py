"""Private standalone Embeddings API: a list of texts in, a list of unit vectors out.

One owning Hub credential, one loopback port, one resident encoder. It serves the
OpenAI-compatible `POST /v1/embeddings` the Hub's peer surface forwards to
(`/studio/embeddings/v1/embeddings`), plus the health, catalog and memory-policy contracts every
Studio publishes. Like Chat it has no job queue: a batch is one short synchronous request. Models
are the bundled `embed-mlx` manifests, read as data; nothing here names one.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hmac
import json
import os
from pathlib import Path
import threading
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from components import app_version, model_packages

from .memory_policy import MemoryPolicy

ENGINE = "embed-mlx"
LANE_CONTRACT_SCHEMA = "studiohub.lane-contract.v1"
# OpenAI's own ceiling for one request; a campaign's whole history (~600 ideas) fits in one call.
MAX_INPUTS = 2048
SUPPORTED_FIELDS = frozenset({"model", "input", "encoding_format", "user"})


def read_manifests(catalog: Path) -> list[dict]:
    """Every bundled `embed-mlx` manifest, validated; the default (if any) first."""
    found = []
    for path in sorted(Path(catalog).glob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(value, dict) and value.get("engine") == ENGINE:
            model_packages.validate_manifest(value)
            found.append(value)
    return sorted(found, key=lambda manifest: manifest.get("default") is not True)


def _bound_mlx_cache() -> None:
    """The shared MLX cache bound (docs/mlx-memory-discipline.md), from checkout or staged app."""
    try:
        from components.voice.app.backend.mlx_memory import bound_cache
    except ModuleNotFoundError:
        from voice.app.backend.mlx_memory import bound_cache
    bound_cache()


class EmbeddingService:
    """The installed embedding models and the one encoder held resident.

    All MLX work runs on one dedicated thread (MLX streams are per thread), which also makes the
    requests strictly one at a time. The interface is what `MemoryPolicy` reads from an engine.
    """

    def __init__(self, manifests: list[dict], models_dir: Path, loader=None) -> None:
        self.manifests = {manifest["model_id"]: manifest for manifest in manifests}
        self.models_dir = Path(models_dir)
        self._loader = loader or self._load_encoder
        self._thread = ThreadPoolExecutor(max_workers=1, thread_name_prefix="embeddings-mlx")
        self._lock = threading.Lock()
        self._encoder = None
        self._loaded_id: str | None = None
        self._busy = 0
        self._last_activity: float | None = None
        self.model_id = None  # the resident model, as MemoryPolicy publishes it

    def default_model(self) -> str | None:
        return next(iter(self.manifests), None)

    def files(self, manifest: dict) -> dict:
        catalog = {item["package_id"]: item for item in self.manifests.values()}
        try:
            return model_packages.preflight(manifest, self.models_dir, catalog=catalog)
        except (OSError, ValueError) as exc:
            return {"state": "not_installed", "reason": str(exc)}

    def model_dir(self, manifest: dict) -> Path:
        return self.models_dir / manifest["package_id"] / manifest["revision"] / "model"

    def _load_encoder(self, manifest: dict):
        from .encoder import SentenceEncoder

        _bound_mlx_cache()
        defaults = manifest.get("defaults") or {}
        return SentenceEncoder(self.model_dir(manifest), pooling=defaults["pooling"],
                               normalize=defaults["normalize"],
                               max_tokens=defaults["max_tokens"])

    def _encode_on_thread(self, manifest: dict, texts: list[str]):
        if self._loaded_id != manifest["model_id"]:
            self._encoder = None
            self._encoder = self._loader(manifest)
            self._loaded_id = self.model_id = manifest["model_id"]
        too_long = [(index, count) for index, count in enumerate(self._encoder.token_counts(texts))
                    if count >= self._encoder.max_tokens]
        if too_long:
            index, count = too_long[0]
            raise ValueError(
                f"input[{index}] is {count} tokens or more; this model reads at most "
                f"{self._encoder.max_tokens - 1} and a cut text would get the wrong vector. "
                f"Shorten it ({len(too_long)} input(s) are too long).")
        return self._encoder.encode(texts)

    def encode(self, manifest: dict, texts: list[str]):
        with self._lock:
            self._busy += 1
        try:
            return self._thread.submit(self._encode_on_thread, manifest, texts).result()
        finally:
            with self._lock:
                self._busy -= 1
                self._last_activity = time.time()

    # ---- the engine interface MemoryPolicy reads ---------------------------
    def loaded(self) -> bool:
        return self._encoder is not None

    def last_activity_at(self) -> float | None:
        return self._last_activity

    def status(self) -> dict:
        return {"busy": self._busy > 0, "loaded_model": self._loaded_id}

    def release(self, *, reason: str = "requested") -> dict:
        def drop():
            was = self._encoder is not None
            self._encoder, self._loaded_id, self.model_id = None, None, None
            try:
                import mlx.core as mx
                mx.clear_cache()
            except ImportError:
                pass
            return was
        with self._lock:
            if self._busy:
                raise RuntimeError("an embedding batch is running; release was refused")
        return {"released": self._thread.submit(drop).result(), "reason": reason}


def _lane_contract(manifest: dict) -> dict:
    defaults = manifest.get("defaults") or {}
    return {
        "schema": LANE_CONTRACT_SCHEMA, "modality": "embeddings", "operation": "text.embedding",
        "parameters": [
            {"name": "model", "type": "string", "required": False, "default": manifest["model_id"]},
            {"name": "input", "type": "array", "required": True, "max_items": MAX_INPUTS,
             "max_tokens_per_item": defaults.get("max_tokens", 512) - 1,
             "note": "a string or a list of non-empty strings; a longer text is refused, never cut"},
            {"name": "encoding_format", "type": "string", "required": False, "enum": ["float"]},
        ],
        "artifact": {"modality": "embeddings", "media_type": "application/json",
                     "extension": "json", "per_item": 1,
                     "dimensions": defaults.get("dimensions"),
                     "normalized": defaults.get("normalize") is True},
    }


def create_app(service: EmbeddingService, policy: MemoryPolicy, token: str,
               runtime_sha256: str) -> FastAPI:
    if not token:
        raise ValueError("Private Embeddings worker requires its owning Hub credential")
    app = FastAPI(title="Studio Hub Embeddings", version="1")

    @app.middleware("http")
    async def require_owner(request: Request, call_next):
        offered = (request.headers.get("x-studio-token") or request.headers.get("x-hub-token")
                   or request.headers.get("authorization", "").removeprefix("Bearer "))
        if not hmac.compare_digest(offered, token):
            return JSONResponse({"detail": "Owner credential required: this worker accepts only "
                                           "the Hub token it was launched with"}, status_code=401)
        return await call_next(request)

    @app.get("/api/health")
    def health() -> dict:
        state = service.status()
        return {"ok": True, **app_version.version_fields(), "embeddings": {
            "available": True, "busy": state["busy"], "loaded_model": state["loaded_model"],
            "runtime_revision": runtime_sha256}}

    @app.get("/api/catalog")
    def catalog() -> dict:
        rows = []
        for manifest in service.manifests.values():
            files = service.files(manifest)
            defaults = manifest.get("defaults") or {}
            allowed = model_packages.production_allowed(manifest)
            ready = allowed and files.get("state") == "files_verified"
            floor = (manifest.get("qualification") or {}).get("minimum_unified_memory_gb")
            label = manifest.get("display_name") or manifest["model_id"]
            rows.append({
                "label": label, "name": label, "repo": manifest["model_id"],
                "model_id": manifest["model_id"], "package_id": manifest["package_id"],
                "family": "bert", "modality": "embeddings", "hub_modality": "embeddings",
                "capabilities": ["embeddings"], "hub_operation": "text.embedding",
                "quantization": manifest.get("quantization"),
                "quantization_source": manifest.get("quantization_source"),
                "model_revision": manifest["revision"], "runtime_revision": runtime_sha256,
                "runtime_compatible": True, "runtime_ready": True, "memory_eligible": True,
                "min_unified_memory_gb": floor, "min_total_memory_gb": floor,
                "dimensions": defaults.get("dimensions"), "max_tokens": defaults.get("max_tokens"),
                "pooling": defaults.get("pooling"), "normalized": defaults.get("normalize") is True,
                "cache": {"state": "cached" if files.get("state") == "files_verified"
                          else files.get("state")},
                "readiness": {"ready": ready, "files": files, "production_allowed": allowed,
                              "reason": None if ready else files.get("reason")},
                "execution_ready": ready, "available": ready,
                "lane_contract": _lane_contract(manifest),
                "default": manifest["model_id"] == service.default_model(),
                "production_allowed": allowed,
            })
        return {"models": rows}

    @app.get("/api/memory-policy")
    def memory_policy_status() -> dict:
        return policy.status()

    @app.put("/api/memory-policy")
    def memory_policy_save(body: dict | None = None) -> dict:
        try:
            return policy.save((body or {}).get("mode"))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/memory/release")
    def memory_release() -> dict:
        try:
            return policy.release("manual")
        except Exception as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/v1/embeddings")
    async def embeddings(body: dict) -> dict:
        unsupported = sorted(set(body) - SUPPORTED_FIELDS)
        if unsupported:
            raise HTTPException(422, f"{', '.join(unsupported)}: not supported by this worker; "
                                     f"it accepts {', '.join(sorted(SUPPORTED_FIELDS))}")
        if body.get("encoding_format", "float") != "float":
            raise HTTPException(422, "encoding_format: only float is served")
        texts = body.get("input")
        texts = [texts] if isinstance(texts, str) else texts
        if (not isinstance(texts, list) or not texts
                or not all(isinstance(text, str) and text.strip() for text in texts)):
            raise HTTPException(422, "input: must be a non-empty string or a non-empty list of "
                                     "non-empty strings")
        if len(texts) > MAX_INPUTS:
            raise HTTPException(422, f"input: {len(texts)} items; at most {MAX_INPUTS} per request")
        requested = body.get("model") or service.default_model()
        manifest = service.manifests.get(requested)
        if manifest is None:
            raise HTTPException(404, f"unknown embedding model {requested!r}; this worker serves "
                                     f"{', '.join(service.manifests) or 'none'}")
        files = service.files(manifest)
        if files.get("state") != "files_verified" or not model_packages.production_allowed(manifest):
            raise HTTPException(503, {"code": "MODEL_NOT_READY", "detail": files.get("reason") or
                                      f"{manifest['package_id']} is not installed on this Mac"})
        try:
            vectors, tokens = await run_in_threadpool(service.encode, manifest, texts)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"object": "list", "model": manifest["model_id"],
                "data": [{"object": "embedding", "index": index, "embedding": vector}
                         for index, vector in enumerate(vectors)],
                "usage": {"prompt_tokens": tokens, "total_tokens": tokens}}

    return app


def from_environment() -> FastAPI:
    data = Path(os.environ["STUDIOHUB_EMBEDDINGS_DATA_DIR"])
    service = EmbeddingService(read_manifests(Path(os.environ["STUDIOHUB_MODEL_MANIFESTS"])),
                               Path(os.environ["STUDIOHUB_MODELS_DIR"]))
    release = json.loads(Path(os.environ["STUDIOHUB_RELEASE_MANIFEST"]).read_text())
    identity = release["components"]["embeddings"]["runtime_sha256"]
    if not isinstance(identity, str) or len(identity) != 64:
        raise ValueError("Release manifest lacks exact Embeddings runtime identity")
    policy = MemoryPolicy(data, service)
    policy.start()
    return create_app(service, policy, os.environ["STUDIOHUB_FLEET_TOKEN"], identity)
