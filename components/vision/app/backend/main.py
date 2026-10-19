"""Private standalone Vision API: one picture in, its depth map, subject, points and outlines out.

One owning Hub credential, one loopback port, the analysis models held resident. It serves
`POST /v1/vision/analyze`, which the Hub's peer surface forwards to
(`/studio/vision/v1/vision/analyze`), plus the health, catalog and memory-policy contracts every
Studio publishes. Like Chat and Embeddings it has no job queue: one picture is one short synchronous
request, strictly one at a time on one MLX thread. Models are the bundled `vision-mlx` manifests,
read as data; nothing here names one. Each manifest's `defaults.kind` says which code runs it
(`depth`: Depth Anything; `florence`: Florence-2) and `defaults.tasks` what it publishes.

Every coordinate in an answer is normalised to 0..1 of the picture, and every map (depth, outlines)
is an 8-bit PNG at most `MAP_LONG_SIDE` pixels on its long side, so the answer stays a few tens of
kilobytes whatever the caller sent.
"""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import hmac
import io
import json
import os
from pathlib import Path
import re
import threading
import time
import urllib.request

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from components import app_version, model_packages
from components.synchronous_activity import SynchronousActivity

from .memory_policy import MemoryPolicy

ENGINE = "vision-mlx"
LANE_CONTRACT_SCHEMA = "studiohub.lane-contract.v1"
TASKS = ("depth", "subject", "points", "segments")
DEFAULT_REGIONS = ("sky", "water", "clouds", "fire")
# Florence-2's referring segmentation outlines *something* for any word, present or not (measured
# 2026-10-11: "sky" on an indoor still life covered 86%, "fire" on a desert 50%, with scores that do
# not separate them). So a region is outlined only when the picture's own detailed caption (~0.2 s)
# names it; these are the words that count for the default regions, any other region counts by its
# own word. ponytail: a word list, not a classifier; widen it when a real region is missed.
REGION_WORDS = {
    "sky": r"sky|skies",
    "water": r"water\w*|rivers?|lakes?|seas?|oceans?|ponds?|pools?|streams?|waves?|puddles?|creeks?|bays?|harbou?rs?",
    "clouds": r"cloud\w*",
    "fire": r"fire\w*|flames?|flaming|candles?|torch\w*|bonfires?|campfires?|blaze|embers?|hearth",
}
SUPPORTED_FIELDS = frozenset({"model", "tasks", "image", "text", "regions"})
MIMES = frozenset({"image/jpeg", "image/png", "image/webp"})
MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_PIXELS = 40_000_000
MAX_TEXT = 500
MAX_REGIONS = 8
MAX_POINTS = 8
MAP_LONG_SIDE = 518
URL_TIMEOUT_S = 20


def read_manifests(catalog: Path) -> list[dict]:
    """Every bundled `vision-mlx` manifest, validated; defaults first."""
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


def tasks_of(manifest: dict) -> list[str]:
    return list((manifest.get("defaults") or {}).get("tasks") or [])


def _png(values, mode: str = "L") -> str:
    from PIL import Image
    buffer = io.BytesIO()
    Image.fromarray(values, mode).save(buffer, "PNG", optimize=True)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _xywh(box: list[float]) -> list[float]:
    x0, y0, x1, y1 = box
    return [round(x0, 4), round(y0, 4), round(max(x1 - x0, 0.0), 4), round(max(y1 - y0, 0.0), 4)]


def _area(box: list[float]) -> float:
    return max(box[2] - box[0], 0.0) * max(box[3] - box[1], 0.0)


class VisionService:
    """The installed analysis models and the ones held resident.

    All MLX work runs on one dedicated thread (MLX streams are per thread), which also makes the
    requests strictly one at a time. The interface is what `MemoryPolicy` reads from an engine.
    """

    def __init__(self, manifests: list[dict], models_dir: Path, loader=None) -> None:
        self.manifests = {manifest["model_id"]: manifest for manifest in manifests}
        self.models_dir = Path(models_dir)
        self._loader = loader or self._load_model
        self._thread = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vision-mlx")
        self._lock = threading.Lock()
        self._models: dict[str, object] = {}
        self._busy = 0
        self._last_activity: float | None = None
        self.model_id = None  # the model loaded last, as MemoryPolicy publishes it
        self.activity = SynchronousActivity("vision")

    def files(self, manifest: dict) -> dict:
        catalog = {item["package_id"]: item for item in self.manifests.values()}
        try:
            return model_packages.preflight(manifest, self.models_dir, catalog=catalog)
        except (OSError, ValueError) as exc:
            return {"state": "not_installed", "reason": str(exc)}

    def ready(self, manifest: dict) -> bool:
        return (self.files(manifest).get("state") == "files_verified"
                and model_packages.production_allowed(manifest))

    def default_for(self, task: str) -> dict | None:
        """The model a request without `model` gets for one task: an installed default first."""
        publishing = [m for m in self.manifests.values() if task in tasks_of(m)]
        ranked = sorted(publishing, key=lambda m: (not self.ready(m), m.get("default") is not True))
        return ranked[0] if ranked else None

    def model_dir(self, manifest: dict) -> Path:
        return self.models_dir / manifest["package_id"] / manifest["revision"] / "model"

    def _load_model(self, manifest: dict):
        import mlx.core as mx

        _bound_mlx_cache()
        defaults = manifest.get("defaults") or {}
        if defaults.get("kind") == "depth":
            from .depth import DepthAnything
            return DepthAnything(self.model_dir(manifest))
        from .florence import Florence2
        return Florence2(self.model_dir(manifest),
                         dtype=getattr(mx, defaults.get("text_dtype") or "float32"),
                         vision_dtype=getattr(mx, defaults.get("vision_dtype") or "float32"))

    def _model(self, manifest: dict):
        if manifest["model_id"] not in self._models:
            self._models[manifest["model_id"]] = self._loader(manifest)
            self.model_id = manifest["model_id"]
        return self._models[manifest["model_id"]]

    def _analyze_on_thread(self, plan: dict, image, text: str, regions: list[str]) -> dict:
        import numpy as np
        from PIL import Image, ImageDraw

        timings: dict[str, int] = {}
        clock = time.perf_counter
        width, height = image.size
        scale = min(1.0, MAP_LONG_SIDE / max(width, height))
        map_w, map_h = max(1, round(width * scale)), max(1, round(height * scale))
        out: dict = {}
        if "depth" in plan:
            started = clock()
            from .depth import to_png_values
            values, spread = to_png_values(self._model(plan["depth"]).predict(image, map_w, map_h))
            out["depth"] = {"png_b64": _png(values), "w": map_w, "h": map_h, "near": "bright",
                            "range": spread}
            timings["depth"] = round((clock() - started) * 1000)
        florence_tasks = [task for task in ("subject", "points", "segments") if task in plan]
        if not florence_tasks:
            return {**out, "timings_ms": timings}
        manifests = {plan[task]["model_id"]: plan[task] for task in florence_tasks}
        features, detected = {}, {}
        limit = int((next(iter(manifests.values())).get("defaults") or {}).get("max_new_tokens") or 1024)

        def model_and_features(manifest):
            model = self._model(manifest)
            if manifest["model_id"] not in features:
                started = clock()
                features[manifest["model_id"]] = model.image_features(image)
                timings["image_features"] = timings.get("image_features", 0) + round((clock() - started) * 1000)
            return model, features[manifest["model_id"]]

        def detect(manifest):
            if manifest["model_id"] not in detected:
                model, feats = model_and_features(manifest)
                detected[manifest["model_id"]] = model.run(feats, "<OD>", max_new_tokens=limit)
            return detected[manifest["model_id"]]

        if "subject" in plan:
            started = clock()
            model, feats = model_and_features(plan["subject"])
            subject = None
            if text:
                found = model.run(feats, "<CAPTION_TO_PHRASE_GROUNDING>", text, max_new_tokens=limit)
                if found["instances"]:
                    first = found["instances"][0]
                    subject = {"box": _xywh(first["box"]), "label": first["label"],
                               "score": found["score"], "source": "grounding"}
            if subject is None:
                found = detect(plan["subject"])
                boxes = [item for item in found["instances"] if 0.02 <= _area(item["box"]) <= 0.7]
                boxes = boxes or found["instances"]
                if boxes:
                    best = max(boxes, key=lambda item: _area(item["box"]))
                    subject = {"box": _xywh(best["box"]), "label": best["label"],
                               "score": found["score"], "source": "detect"}
            out["subject"] = subject
            timings["subject"] = round((clock() - started) * 1000)
        if "points" in plan:
            started = clock()
            seen, points = set(), []
            for item in sorted(detect(plan["points"])["instances"], key=lambda item: -_area(item["box"])):
                key = (item["label"], tuple(round(v, 3) for v in item["box"]))
                if key not in seen:
                    seen.add(key)
                    points.append({"box": _xywh(item["box"]), "label": item["label"]})
            out["points"] = points[:MAX_POINTS]
            timings["points"] = round((clock() - started) * 1000)
        if "segments" in plan:
            started = clock()
            model, feats = model_and_features(plan["segments"])
            # The model's own stated budget (`limit`, from the manifest's `defaults.max_new_tokens`),
            # exactly as every sibling call in this function uses. This one was pinned to a
            # hardcoded 256 — a quarter of what the manifest states — so the caption, and the
            # region matching that reads it below, were cut short for no stated reason.
            caption = model.run(feats, "<DETAILED_CAPTION>", max_new_tokens=limit)["text"].strip()
            out["caption"] = caption
            segments = {}
            for region in regions:
                words = REGION_WORDS.get(region.lower(), re.escape(region.lower()) + r"s?")
                if not re.search(rf"\b(?:{words})\b", caption.lower()):
                    segments[region] = None
                    continue
                found = model.run(feats, "<REFERRING_EXPRESSION_SEGMENTATION>", region, max_new_tokens=limit)
                canvas = Image.new("L", (map_w, map_h), 0)
                draw = ImageDraw.Draw(canvas)
                for instance in found["instances"]:
                    for polygon in instance["polygons"]:
                        points = [(polygon[i] * map_w, polygon[i + 1] * map_h)
                                  for i in range(0, len(polygon) - 1, 2)]
                        if len(points) >= 3:
                            draw.polygon(points, fill=255)
                values = np.asarray(canvas)
                coverage = float((values > 0).mean())
                segments[region] = None if coverage == 0 else {
                    "png_b64": _png(values), "w": map_w, "h": map_h,
                    "coverage": round(coverage, 4), "score": found["score"],
                    "tokens": found["tokens"], "truncated": found["tokens"] >= limit}
            out["segments"] = segments
            timings["segments"] = round((clock() - started) * 1000)
        return {**out, "timings_ms": timings}

    def analyze(self, plan: dict, image, text: str, regions: list[str], activity_job=None) -> dict:
        with self._lock:
            self._busy += 1
        model = _activity_model(plan)
        job = activity_job or self.activity.queue(model)
        try:
            try:
                future = self._thread.submit(
                    self.activity.run, job, self._analyze_on_thread, plan, image, text, regions)
            except Exception:
                self.activity.submission_failed(job)
                raise
            return future.result()
        finally:
            with self._lock:
                self._busy -= 1
                self._last_activity = time.time()

    # ---- the engine interface MemoryPolicy reads ---------------------------
    def loaded(self) -> bool:
        return bool(self._models)

    def last_activity_at(self) -> float | None:
        return self._last_activity

    def status(self) -> dict:
        return {"busy": self._busy > 0, "loaded_model": self.model_id,
                "loaded_models": sorted(self._models)}

    def release(self, *, reason: str = "requested") -> dict:
        def drop():
            was = bool(self._models)
            self._models, self.model_id = {}, None
            try:
                import mlx.core as mx
                mx.clear_cache()
            except ImportError:
                pass
            return was
        with self._lock:
            if self._busy:
                raise RuntimeError("a picture is being analysed; release was refused")
        return {"released": self._thread.submit(drop).result(), "reason": reason}


def _lane_contract(manifest: dict) -> dict:
    tasks = tasks_of(manifest)
    return {
        "schema": LANE_CONTRACT_SCHEMA, "modality": "vision", "operation": "image.analysis",
        "tasks": tasks,
        "parameters": [
            {"name": "model", "type": "string|array", "required": False,
             "note": "one id or a list; each task runs on the listed model that publishes it"},
            {"name": "tasks", "type": "array", "required": True, "enum": tasks},
            {"name": "image", "type": "object", "required": True,
             "note": "{b64, mime} or {url}; send a downscaled ~518-768 px JPEG, at most "
                     f"{MAX_IMAGE_BYTES // (1024 * 1024)} MB"},
            *([{"name": "text", "type": "string", "required": False, "max_length": MAX_TEXT,
                "note": "the subject phrase to ground; without it the largest detection is used"}]
              if "subject" in tasks else []),
            *([{"name": "regions", "type": "array", "required": False, "max_items": MAX_REGIONS,
                "default": list(DEFAULT_REGIONS)}] if "segments" in tasks else []),
        ],
        "artifact": {"modality": "vision", "media_type": "application/json", "extension": "json",
                     "per_item": 1, "coordinates": "normalised 0..1",
                     "map_long_side": MAP_LONG_SIDE},
    }


def _bad(detail: str):
    raise HTTPException(422, detail)


def read_image(value) -> tuple[object, int]:
    """The request's picture as an RGB PIL image, and the bytes it arrived as."""
    from PIL import Image
    if not isinstance(value, dict) or sorted(set(value) & {"b64", "url"}) not in (["b64"], ["url"]):
        _bad("image: must be {b64, mime} or {url}")
    unknown = sorted(set(value) - {"b64", "url", "mime"})
    if unknown:
        _bad(f"image: {', '.join(unknown)} not supported; it accepts b64, mime or url")
    if value.get("mime") is not None and value["mime"] not in MIMES:
        _bad(f"image.mime: one of {', '.join(sorted(MIMES))}")
    if "b64" in value:
        if not isinstance(value["b64"], str) or len(value["b64"]) > MAX_IMAGE_BYTES * 4 // 3 + 4:
            _bad(f"image.b64: a base64 string of at most {MAX_IMAGE_BYTES // (1024 * 1024)} MB; "
                 "send a downscaled ~768 px JPEG")
        try:
            data = base64.b64decode(value["b64"], validate=True)
        except ValueError:
            _bad("image.b64: not valid base64")
    else:
        url = value["url"]
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            _bad("image.url: an http or https address")
        try:
            with urllib.request.urlopen(url, timeout=URL_TIMEOUT_S) as response:
                data = response.read(MAX_IMAGE_BYTES + 1)
        except (OSError, ValueError) as exc:
            _bad(f"image.url: this Mac could not fetch it ({type(exc).__name__})")
    if len(data) > MAX_IMAGE_BYTES:
        _bad(f"image: {len(data)} bytes; at most {MAX_IMAGE_BYTES} - send a downscaled ~768 px JPEG")
    try:
        image = Image.open(io.BytesIO(data))
        if image.size[0] * image.size[1] > MAX_PIXELS:
            _bad(f"image: {image.size[0]}x{image.size[1]} is over {MAX_PIXELS:,} pixels")
        image = image.convert("RGB")
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - any undecodable picture is the caller's input
        _bad(f"image: not a readable picture ({type(exc).__name__})")
    return image, len(data)


def plan_request(service: VisionService, body: dict) -> dict:
    """{task: manifest} for this request, or the refusal that explains why not."""
    unsupported = sorted(set(body) - SUPPORTED_FIELDS)
    if unsupported:
        _bad(f"{', '.join(unsupported)}: not supported by this worker; it accepts "
             f"{', '.join(sorted(SUPPORTED_FIELDS))}")
    tasks = body.get("tasks")
    if (not isinstance(tasks, list) or not tasks or len(set(map(str, tasks))) != len(tasks)
            or not all(task in TASKS for task in tasks)):
        _bad(f"tasks: a non-empty list of distinct tasks from {', '.join(TASKS)}")
    named = body.get("model")
    named = [named] if isinstance(named, str) else named
    if named is not None:
        if not isinstance(named, list) or not named or not all(isinstance(m, str) and m for m in named):
            _bad("model: a model id or a non-empty list of model ids")
        unknown = [model for model in named if model not in service.manifests]
        if unknown:
            raise HTTPException(404, f"unknown vision model {unknown[0]!r}; this worker serves "
                                     f"{', '.join(service.manifests) or 'none'}")
    plan = {}
    for task in tasks:
        if named is None:
            manifest = service.default_for(task)
        else:
            manifest = next((service.manifests[m] for m in named if task in tasks_of(service.manifests[m])), None)
            if manifest is None:
                _bad(f"no listed model does {task!r}: " + "; ".join(
                    f"{m} does {', '.join(tasks_of(service.manifests[m])) or 'nothing'}" for m in named))
        if manifest is None:
            raise HTTPException(503, {"code": "MODEL_NOT_READY",
                                      "detail": f"no vision model on this Mac does {task!r}"})
        plan[task] = manifest
    return plan


def _activity_model(plan: dict) -> str:
    models = list(dict.fromkeys(manifest["model_id"] for manifest in plan.values()))
    joined = ", ".join(models)
    return joined if len(joined) <= 500 else f"{len(models)} vision models"


def create_app(service: VisionService, policy: MemoryPolicy, token: str,
               runtime_sha256: str) -> FastAPI:
    if not token:
        raise ValueError("Private Vision worker requires its owning Hub credential")
    app = FastAPI(title="Studio Hub Vision", version="1")
    build_identity = dict(app_version.version_fields())

    @app.middleware("http")
    async def require_owner(request: Request, call_next):
        offered = (request.headers.get("x-studio-token") or request.headers.get("x-hub-token")
                   or request.headers.get("authorization", "").removeprefix("Bearer "))
        if not hmac.compare_digest(offered, token):
            return JSONResponse({"detail": "Owner credential required: this worker accepts only "
                                           "the Hub token it was launched with"}, status_code=401)
        return await call_next(request)

    @app.get("/api/health")
    async def health() -> dict:
        state = service.status()
        return {"ok": True, **build_identity, "vision": {
            "available": True, "busy": state["busy"], "loaded_model": state["loaded_model"],
            "loaded_models": state["loaded_models"], "runtime_revision": runtime_sha256}}

    @app.get("/api/fleet/activity")
    async def fleet_activity() -> dict:
        return service.activity.snapshot()

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
                "family": defaults.get("kind"), "kind": defaults.get("kind"),
                "modality": "vision", "hub_modality": "vision",
                "capabilities": ["vision"], "hub_operation": "image.analysis",
                "tasks": tasks_of(manifest),
                "quantization": manifest.get("quantization"),
                "quantization_source": manifest.get("quantization_source"),
                "model_revision": manifest["revision"], "runtime_revision": runtime_sha256,
                "runtime_compatible": True, "runtime_ready": True, "memory_eligible": True,
                "min_unified_memory_gb": floor, "min_total_memory_gb": floor,
                "cache": {"state": "cached" if files.get("state") == "files_verified"
                          else files.get("state")},
                "readiness": {"ready": ready, "files": files, "production_allowed": allowed,
                              "reason": None if ready else files.get("reason")},
                "execution_ready": ready, "available": ready,
                "lane_contract": _lane_contract(manifest),
                "default": manifest.get("default") is True,
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

    @app.post("/v1/vision/analyze")
    async def analyze(body: dict) -> dict:
        started = time.perf_counter()
        plan = plan_request(service, body)
        text = body.get("text") or ""
        if not isinstance(text, str) or len(text) > MAX_TEXT:
            _bad(f"text: a string of at most {MAX_TEXT} characters")
        regions = body.get("regions")
        regions = list(DEFAULT_REGIONS) if regions is None else regions
        if (not isinstance(regions, list) or not 1 <= len(regions) <= MAX_REGIONS
                or not all(isinstance(r, str) and r.strip() and len(r) <= 40 for r in regions)):
            _bad(f"regions: a list of 1-{MAX_REGIONS} short names, like {', '.join(DEFAULT_REGIONS)}")
        for task, manifest in plan.items():
            if not service.ready(manifest):
                raise HTTPException(503, {"code": "MODEL_NOT_READY", "detail": service.files(manifest).get("reason")
                                          or f"{manifest['package_id']} is not installed on this Mac"})
        model = _activity_model(plan)
        job = service.activity.queue(model)
        decode_started = time.perf_counter()
        try:
            image, received = await run_in_threadpool(
                service.activity.prepare, job, read_image, body.get("image"))
        except Exception:
            service.activity.discard(job)
            raise
        except BaseException:
            service.activity.cancel_queued(job)
            raise
        decode_ms = round((time.perf_counter() - decode_started) * 1000)
        try:
            result = await run_in_threadpool(
                service.activity.prepare, job, service.analyze, plan, image, text.strip(),
                [r.strip() for r in regions], job)
        except BaseException:
            service.activity.cancel_queued(job)
            raise
        timings = {"read_image": decode_ms, **result.pop("timings_ms"),
                   "total": round((time.perf_counter() - started) * 1000)}
        return {"models": {task: manifest["model_id"] for task, manifest in plan.items()},
                "revisions": {m["model_id"]: m["revision"] for m in plan.values()},
                "image": {"w": image.size[0], "h": image.size[1], "bytes": received},
                **result, "timings_ms": timings}

    return app


def from_environment() -> FastAPI:
    data = Path(os.environ["STUDIOHUB_VISION_DATA_DIR"])
    service = VisionService(read_manifests(Path(os.environ["STUDIOHUB_MODEL_MANIFESTS"])),
                            Path(os.environ["STUDIOHUB_MODELS_DIR"]))
    release = json.loads(Path(os.environ["STUDIOHUB_RELEASE_MANIFEST"]).read_text())
    identity = release["components"]["vision"]["runtime_sha256"]
    if not isinstance(identity, str) or len(identity) != 64:
        raise ValueError("Release manifest lacks exact Vision runtime identity")
    policy = MemoryPolicy(data, service)
    policy.start()
    return create_app(service, policy, os.environ["STUDIOHUB_FLEET_TOKEN"], identity)
