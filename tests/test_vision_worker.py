"""The Vision Studio: the MLX ports, the worker's route, and the Hub's unleased peer and relay paths."""
from __future__ import annotations

import asyncio
import base64
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import httpx

from components import model_packages
from components.hub import standalone
from components.hub.backend import auth, vocabulary
from _model_library import shipped_model_library

ROOT = Path(__file__).resolve().parents[1]
DEPTH = json.loads((ROOT / "model-manifests/depth-anything-v2-small.json").read_text())
BASE = json.loads((ROOT / "model-manifests/florence-2-base-ft.json").read_text())
LARGE = json.loads((ROOT / "model-manifests/florence-2-large-ft.json").read_text())
_HAS = {name: importlib.util.find_spec(name) is not None
        for name in ("mlx", "tokenizers", "torch", "transformers", "fastapi", "PIL", "numpy")}


def _jpeg(width=64, height=36) -> str:
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (40, 90, 160)).save(buffer, "JPEG")
    return base64.b64encode(buffer.getvalue()).decode()


async def _wait_until(predicate, message: str) -> None:
    deadline = time.monotonic() + 5
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError(message)
        await asyncio.sleep(0.01)


class _FakeDepth:
    def predict(self, image, width, height):
        import numpy as np
        return np.tile(np.linspace(0, 1, width, dtype=np.float32), (height, 1))


class _FakeFlorence:
    def __init__(self, owner):
        self.owner = owner

    def image_features(self, image):
        self.owner.features += 1
        return "features"

    def run(self, features, task, text="", max_new_tokens=1024):
        with self.owner.lock:
            self.owner.active += 1
            self.owner.peak = max(self.owner.peak, self.owner.active)
        time.sleep(0.01)
        with self.owner.lock:
            self.owner.active -= 1
        self.owner.runs.append((task, text))
        # The budget each task was actually run with, so a task carrying a number of its own
        # instead of the model's stated one is visible rather than inferred.
        self.owner.budgets.append((task, max_new_tokens))
        if task == "<DETAILED_CAPTION>":
            return {"text": self.owner.caption, "score": 0.9, "tokens": 9, "instances": []}
        if task == "<OD>":
            found = [{"label": "house", "box": [0.1, 0.1, 0.5, 0.6]},
                     {"label": "tree", "box": [0.6, 0.2, 0.7, 0.5]},
                     {"label": "house", "box": [0.1, 0.1, 0.5, 0.6]},
                     {"label": "everything", "box": [0.0, 0.0, 1.0, 1.0]}]
        elif "GROUNDING" in task:
            found = [{"label": "a lighthouse", "box": [0.4, 0.2, 0.6, 0.8]}] if "lighthouse" in text else []
        else:
            found = [{"label": "", "polygons": [[0, 0, 1, 0, 1, 0.5, 0, 0.5]]}] if text == "sky" else []
        return {"text": "", "score": 0.5, "tokens": 12, "instances": found}


@unittest.skipUnless(_HAS["fastapi"] and _HAS["PIL"] and _HAS["numpy"], "fastapi, Pillow, numpy needed")
class VisionRouteTests(unittest.TestCase):
    TOKEN = "vision-worker-token"

    def setUp(self) -> None:
        from fastapi.testclient import TestClient

        from components.chat.app.backend.memory_policy import MemoryPolicy
        from components.vision.app.backend.main import VisionService, create_app

        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.loads, self.runs, self.features = [], [], 0
        self.budgets = []
        self.caption = "A house under a cloudy sky, by the water."
        self.lock, self.active, self.peak = threading.Lock(), 0, 0

        def loader(manifest):
            self.loads.append(manifest["model_id"])
            return _FakeDepth() if manifest["defaults"]["kind"] == "depth" else _FakeFlorence(self)

        self.service = VisionService([BASE, DEPTH, LARGE], Path(self.temporary.name), loader=loader)
        self.addCleanup(self.service._thread.shutdown, wait=True)
        self.installed = {DEPTH["model_id"], BASE["model_id"]}
        self.service.files = lambda manifest: (
            {"state": "files_verified"} if manifest["model_id"] in self.installed
            else {"state": "not_installed", "reason": "not on this Mac"})
        policy = MemoryPolicy(Path(self.temporary.name), self.service)
        self.app = create_app(self.service, policy, self.TOKEN, "a" * 64)
        self.client = TestClient(self.app)
        self.headers = {"X-Hub-Token": self.TOKEN}

    def post(self, body):
        return self.client.post("/v1/vision/analyze", json=body, headers=self.headers)

    def test_one_picture_answers_every_task_in_normalised_coordinates(self) -> None:
        from PIL import Image

        answer = self.post({"tasks": ["depth", "subject", "points", "segments"],
                            "image": {"b64": _jpeg(1024, 576), "mime": "image/jpeg"},
                            "text": "a lighthouse on a cliff", "regions": ["sky", "water"]})
        self.assertEqual(answer.status_code, 200, answer.text)
        body = answer.json()
        self.assertEqual(body["models"], {"depth": DEPTH["model_id"], "subject": BASE["model_id"],
                                          "points": BASE["model_id"], "segments": BASE["model_id"]})
        self.assertEqual(body["revisions"][BASE["model_id"]], BASE["revision"])
        self.assertEqual((body["image"]["w"], body["image"]["h"]), (1024, 576))
        depth = body["depth"]
        self.assertEqual((depth["w"], depth["h"], depth["near"]), (518, 291, "bright"))
        png = Image.open(io.BytesIO(base64.b64decode(depth["png_b64"])))
        self.assertEqual((png.size, png.mode), ((518, 291), "L"))
        self.assertGreater(depth["range"], 0.8)
        self.assertEqual(body["subject"], {"box": [0.4, 0.2, 0.2, 0.6], "label": "a lighthouse",
                                           "score": 0.5, "source": "grounding"})
        # Largest first, duplicates once, as [x, y, w, h].
        self.assertEqual([p["label"] for p in body["points"]], ["everything", "house", "tree"])
        self.assertEqual(body["points"][1]["box"], [0.1, 0.1, 0.4, 0.5])
        # Named in the caption but not outlined: no mask, never an error.
        self.assertIsNone(body["segments"]["water"])
        self.assertEqual(body["caption"], self.caption)
        sky = body["segments"]["sky"]
        self.assertAlmostEqual(sky["coverage"], 0.5, delta=0.01)
        self.assertFalse(sky["truncated"])
        self.assertEqual(self.features, 1, "the picture is encoded once for every Florence task")
        self.assertEqual(sorted(self.loads), sorted([DEPTH["model_id"], BASE["model_id"]]))
        self.assertIn("total", body["timings_ms"])

    def test_without_a_phrase_the_subject_is_the_largest_sensible_detection(self) -> None:
        body = self.post({"tasks": ["subject", "points"], "image": {"b64": _jpeg()}}).json()
        # The whole-frame box is over 70% of the picture, so the house wins; detection runs once.
        self.assertEqual((body["subject"]["label"], body["subject"]["source"]), ("house", "detect"))
        self.assertEqual([task for task, _ in self.runs], ["<OD>"])
        missing = self.post({"tasks": ["subject"], "image": {"b64": _jpeg()}, "text": "a dragon"}).json()
        self.assertEqual(missing["subject"]["source"], "detect")

    def test_every_task_runs_with_the_models_own_stated_budget(self) -> None:
        """No task may carry an output budget of its own.

        The detailed caption was pinned to a hardcoded `256` while every sibling call in the same
        function used the manifest's stated `defaults.max_new_tokens` — so on Florence-2 base
        (1,024) the caption this worker returns, and the region matching that reads it, were cut
        to a quarter of what the model states. Read as a mechanism rather than a value: whatever
        the manifest states is what every task runs with.
        """
        self.post({"tasks": ["segments"], "image": {"b64": _jpeg()}, "regions": ["sky"]})
        stated = BASE["defaults"]["max_new_tokens"]
        captions = [budget for task, budget in self.budgets if task == "<DETAILED_CAPTION>"]
        self.assertEqual(captions, [stated], "the caption is asked for the model's stated budget")
        self.assertEqual({budget for _, budget in self.budgets}, {stated},
                         f"a task ran with a budget of its own: {sorted(self.budgets)}")

    def test_a_region_the_caption_does_not_name_is_never_outlined(self) -> None:
        self.caption = "A woman at a wooden table with bowls, a candle flame and a window."
        body = self.post({"tasks": ["segments"], "image": {"b64": _jpeg()},
                          "regions": ["sky", "fire", "window", "clouds"]}).json()
        self.assertEqual([text for task, text in self.runs if "SEGMENTATION" in task], ["fire", "window"])
        self.assertIsNone(body["segments"]["sky"])
        self.assertIsNone(body["segments"]["clouds"])

    def test_a_named_model_list_runs_each_task_on_the_model_that_publishes_it(self) -> None:
        answer = self.post({"tasks": ["depth"], "model": [BASE["model_id"], DEPTH["model_id"]],
                            "image": {"b64": _jpeg()}})
        self.assertEqual(answer.json()["models"], {"depth": DEPTH["model_id"]})
        refused = self.post({"tasks": ["depth", "segments"], "model": DEPTH["model_id"],
                             "image": {"b64": _jpeg()}})
        self.assertEqual(refused.status_code, 422, refused.text)
        self.assertIn("does depth", refused.text)

    def test_bad_requests_are_refused_by_name(self) -> None:
        image = {"b64": _jpeg()}
        for body, status, words in (
                ({"tasks": [], "image": image}, 422, "tasks"),
                ({"tasks": ["depth", "depth"], "image": image}, 422, "distinct"),
                ({"tasks": ["caption"], "image": image}, 422, "tasks"),
                ({"tasks": ["depth"], "image": image, "size": 9}, 422, "size"),
                ({"tasks": ["depth"]}, 422, "image"),
                ({"tasks": ["depth"], "image": {"b64": "x", "url": "http://a"}}, 422, "image"),
                ({"tasks": ["depth"], "image": {"b64": "%%%"}}, 422, "base64"),
                ({"tasks": ["depth"], "image": {"b64": base64.b64encode(b"nope").decode()}}, 422, "readable"),
                ({"tasks": ["depth"], "image": {"b64": _jpeg(), "mime": "image/gif"}}, 422, "mime"),
                ({"tasks": ["depth"], "image": {"url": "file:///etc/hosts"}}, 422, "http"),
                ({"tasks": ["subject"], "image": image, "text": "x" * 501}, 422, "text"),
                ({"tasks": ["segments"], "image": image, "regions": []}, 422, "regions"),
                ({"tasks": ["depth"], "image": image, "model": "someone/else"}, 404, "someone/else")):
            with self.subTest(body=str(body)[:80]):
                answer = self.post(body)
                self.assertEqual(answer.status_code, status, answer.text)
                self.assertIn(words, answer.text)

    def test_an_oversized_picture_is_refused_before_it_is_decoded(self) -> None:
        from components.vision.app.backend import main
        with patch.object(main, "MAX_IMAGE_BYTES", 100):
            answer = self.post({"tasks": ["depth"], "image": {"b64": _jpeg()}})
        self.assertEqual(answer.status_code, 422)
        self.assertIn("downscaled", answer.text)
        self.assertEqual(self.loads, [])

    def test_a_model_that_is_not_installed_is_not_ready(self) -> None:
        self.installed = {BASE["model_id"]}
        answer = self.post({"tasks": ["depth"], "image": {"b64": _jpeg()}})
        self.assertEqual(answer.status_code, 503)
        self.assertIn("MODEL_NOT_READY", answer.text)
        large = self.post({"tasks": ["points"], "model": LARGE["model_id"], "image": {"b64": _jpeg()}})
        self.assertEqual(large.status_code, 503)
        self.assertEqual(self.loads, [])
        activity = self.client.get("/api/fleet/activity", headers=self.headers).json()
        self.assertIsNone(activity["active"])
        self.assertIsNone(activity["latest"])

    def test_an_installed_large_model_serves_when_named_and_base_stays_the_default(self) -> None:
        self.installed.add(LARGE["model_id"])
        named = self.post({"tasks": ["points"], "model": LARGE["model_id"], "image": {"b64": _jpeg()}})
        self.assertEqual(named.json()["models"], {"points": LARGE["model_id"]})
        plain = self.post({"tasks": ["points"], "image": {"b64": _jpeg()}})
        self.assertEqual(plain.json()["models"], {"points": BASE["model_id"]})

    def test_requests_run_one_at_a_time(self) -> None:
        from concurrent.futures import ThreadPoolExecutor
        body = {"tasks": ["segments"], "image": {"b64": _jpeg()}, "regions": ["sky", "water"]}
        with ThreadPoolExecutor(4) as pool:
            codes = list(pool.map(lambda _: self.post(body).status_code, range(4)))
        self.assertEqual(codes, [200] * 4)
        self.assertEqual(self.peak, 1)

    def test_only_the_hub_token_is_accepted(self) -> None:
        self.assertEqual(self.client.post("/v1/vision/analyze", json={}).status_code, 401)
        self.assertEqual(self.client.get("/api/health", headers={"X-Hub-Token": "x"}).status_code, 401)

    def test_activity_route_is_authenticated_and_reports_no_private_content(self) -> None:
        from components.hub.backend import activity

        self.assertEqual(self.client.get("/api/fleet/activity").status_code, 401)
        idle = self.client.get("/api/fleet/activity", headers=self.headers).json()
        self.assertIsNone(idle["active"])
        self.assertIsNone(idle["latest"])
        self.assertIsNotNone(activity.validate_snapshot(idle, expected_studio="vision"))

        secret = "private lighthouse description"
        request = {"tasks": ["subject", "segments"], "image": {"b64": _jpeg()},
                   "text": secret, "regions": ["sky", "water"]}
        response = self.post(request)
        self.assertEqual(response.status_code, 200, response.text)
        snapshot = self.client.get("/api/fleet/activity", headers=self.headers).json()

        self.assertIsNone(snapshot["active"])
        self.assertEqual(snapshot["latest"]["state"], "done")
        self.assertEqual(snapshot["latest"]["model"], BASE["model_id"])
        self.assertIsNotNone(activity.validate_snapshot(snapshot, expected_studio="vision"))
        public = json.dumps(snapshot)
        for private in (secret, request["image"]["b64"], self.caption,
                        "png_b64", "segments", "subject"):
            self.assertNotIn(private, public)

    def test_health_and_activity_bypass_a_saturated_request_pool(self) -> None:
        import anyio

        from components.chat.app.backend.memory_policy import MemoryPolicy
        from components.hub.backend import activity
        from components.vision.app.backend import main

        identity = {"app_version": "9.8.7", "app_commit": "b" * 40,
                    "development_build": False}
        with patch.object(main.app_version, "version_fields", return_value=identity) as version_fields:
            app = main.create_app(
                self.service, MemoryPolicy(Path(self.temporary.name), self.service),
                self.TOKEN, "a" * 64)

            async def exercise() -> None:
                limiter = anyio.to_thread.current_default_thread_limiter()
                original_tokens = limiter.total_tokens
                started, release = threading.Event(), threading.Event()

                def hold_pool() -> None:
                    started.set()
                    if not release.wait(5):
                        raise TimeoutError("fixture pool holder was not released")

                limiter.total_tokens = 1
                holder = asyncio.create_task(anyio.to_thread.run_sync(hold_pool))
                try:
                    await _wait_until(started.is_set, "fixture did not occupy the request pool")
                    self.service._busy = 1
                    self.service.model_id = DEPTH["model_id"]
                    self.service._models = {
                        BASE["model_id"]: object(), DEPTH["model_id"]: object()}
                    transport = httpx.ASGITransport(app=app)
                    async with httpx.AsyncClient(
                            transport=transport, base_url="http://worker") as client:
                        requests = [asyncio.create_task(client.get(path, headers=headers))
                                    for path, headers in (
                                        ("/api/health", self.headers),
                                        ("/api/fleet/activity", self.headers),
                                        ("/api/health", {}),
                                        ("/api/fleet/activity", {}),
                                    )]
                        done, pending = await asyncio.wait(requests, timeout=0.5)
                        self.assertFalse(pending, "health/activity waited for the full thread pool")
                        health, snapshot, denied_health, denied_activity = [
                            request.result() for request in requests]
                        self.assertEqual((health.status_code, snapshot.status_code), (200, 200))
                        self.assertEqual((denied_health.status_code, denied_activity.status_code),
                                         (401, 401))
                        body = health.json()
                        self.assertEqual({key: body[key] for key in identity}, identity)
                        self.assertEqual(body["vision"], {
                            "available": True, "busy": True,
                            "loaded_model": DEPTH["model_id"],
                            "loaded_models": sorted((BASE["model_id"], DEPTH["model_id"])),
                            "runtime_revision": "a" * 64})
                        self.assertIsNotNone(activity.validate_snapshot(
                            snapshot.json(), expected_studio="vision"))
                finally:
                    release.set()
                    await holder
                    limiter.total_tokens = original_tokens
                    self.service._busy = 0
                    self.service.model_id = None
                    self.service._models = {}

            asyncio.run(exercise())
            version_fields.assert_called_once_with()

    def test_a_multi_model_activity_label_stays_inside_the_hub_contract(self) -> None:
        from components.hub.backend import activity
        from components.vision.app.backend.main import _activity_model

        model = _activity_model({
            "one": {"model_id": "a" * 300},
            "two": {"model_id": "b" * 300},
        })
        job = self.service.activity.queue(model)
        self.service.activity.submission_failed(job)
        snapshot = self.service.activity.snapshot()

        self.assertLessEqual(len(snapshot["latest"]["model"]), 500)
        self.assertIsNotNone(
            activity.validate_snapshot(snapshot, expected_studio="vision"))

    def test_activity_is_queued_during_accepted_image_preparation(self) -> None:
        from components.hub.backend import activity
        from components.vision.app.backend import main

        started, release = threading.Event(), threading.Event()
        original = main.read_image

        def blocked_read(value):
            started.set()
            if not release.wait(5):
                raise TimeoutError("fixture image preparation was not released")
            return original(value)

        async def exercise() -> None:
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://worker") as client:
                with patch.object(main, "read_image", blocked_read):
                    request = asyncio.create_task(client.post(
                        "/v1/vision/analyze", headers=self.headers,
                        json={"tasks": ["depth"], "image": {"b64": _jpeg()}}))
                    await _wait_until(started.is_set, "image preparation never started")
                    snapshot = (await client.get(
                        "/api/fleet/activity", headers=self.headers)).json()
                    self.assertEqual(snapshot["active"]["state"], "queued")
                    self.assertIsNone(snapshot["latest"])
                    self.assertIsNotNone(
                        activity.validate_snapshot(snapshot, expected_studio="vision"))
                    release.set()
                    self.assertEqual((await request).status_code, 200)

        asyncio.run(exercise())

    def test_cancellation_during_image_preparation_does_not_leave_stale_activity(self) -> None:
        from components.hub.backend import activity
        from components.vision.app.backend import main

        started, release = threading.Event(), threading.Event()
        original = main.read_image

        def blocked_read(value):
            started.set()
            if not release.wait(5):
                raise TimeoutError("fixture image preparation was not released")
            return original(value)

        async def exercise() -> None:
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://worker") as client:
                with patch.object(main, "read_image", blocked_read):
                    request = asyncio.create_task(client.post(
                        "/v1/vision/analyze", headers=self.headers,
                        json={"tasks": ["depth"], "image": {"b64": _jpeg()}}))
                    await _wait_until(started.is_set, "image preparation never started")
                    request.cancel()
                    try:
                        await asyncio.wait_for(asyncio.shield(request), 0.2)
                    except (asyncio.CancelledError, asyncio.TimeoutError):
                        pass
                    while_running = (await client.get(
                        "/api/fleet/activity", headers=self.headers)).json()
                    self.assertIn(while_running["active"]["state"], {"queued", "cancel_requested"})
                    self.assertIsNone(while_running["latest"])
                    release.set()
                    try:
                        await request
                    except asyncio.CancelledError:
                        pass
                    await _wait_until(
                        lambda: self.service.activity.snapshot()["active"] is None,
                        "cancelled preparation left a queued activity")

                snapshot = (await client.get(
                    "/api/fleet/activity", headers=self.headers)).json()
                self.assertIsNone(snapshot["active"])
                self.assertEqual(snapshot["latest"]["state"], "cancelled")
                self.assertEqual(self.loads, [], "cancelled preparation must not load a model")
                self.assertIsNotNone(
                    activity.validate_snapshot(snapshot, expected_studio="vision"))

        asyncio.run(exercise())

    def test_catalog_and_memory_contracts(self) -> None:
        rows = {row["repo"]: row for row in
                self.client.get("/api/catalog", headers=self.headers).json()["models"]}
        self.assertEqual(rows[DEPTH["model_id"]]["tasks"], ["depth"])
        base = rows[BASE["model_id"]]
        self.assertEqual((base["hub_modality"], base["capabilities"], base["hub_operation"]),
                         ("vision", ["vision"], "image.analysis"))
        self.assertEqual(base["tasks"], ["subject", "points", "segments"])
        self.assertEqual(base["lane_contract"]["tasks"], base["tasks"])
        self.assertTrue(base["default"] and base["execution_ready"])
        self.assertFalse(rows[LARGE["model_id"]]["default"] or rows[LARGE["model_id"]]["execution_ready"])
        self.post({"tasks": ["depth"], "image": {"b64": _jpeg()}})
        self.assertEqual(self.client.get("/api/memory-policy", headers=self.headers)
                         .json()["loaded_model"], DEPTH["model_id"])
        self.assertFalse(self.client.post("/api/memory/release", headers=self.headers).json()["resident"])
        self.post({"tasks": ["depth"], "image": {"b64": _jpeg()}})
        self.assertEqual(self.loads, [DEPTH["model_id"]] * 2, "a released model loads again on use")


@unittest.skipUnless(all(_HAS[n] for n in ("mlx", "torch", "transformers", "PIL")),
                     "mlx, torch and transformers are needed for the reference check")
class DepthMatchesTransformersTests(unittest.TestCase):
    """The MLX Depth Anything against transformers' own model on the same random weights, offline."""

    def test_a_small_random_checkpoint_matches(self) -> None:
        import numpy as np
        import torch
        from transformers import DepthAnythingConfig, DepthAnythingForDepthEstimation, Dinov2Config

        from components.vision.app.backend.depth import DepthAnything

        torch.manual_seed(3)
        backbone = Dinov2Config(hidden_size=32, num_hidden_layers=4, num_attention_heads=2,
                                intermediate_size=64, image_size=70, patch_size=14,
                                out_indices=[1, 2, 3, 4], reshape_hidden_states=False,
                                layerscale_value=0.5)
        config = DepthAnythingConfig(backbone_config=backbone, neck_hidden_sizes=[8, 16, 32, 32],
                                     fusion_hidden_size=16, head_hidden_size=8,
                                     reassemble_hidden_size=32, patch_size=14)
        reference = DepthAnythingForDepthEstimation(config).eval()
        pixels = np.random.default_rng(1).normal(size=(1, 56, 84, 3)).astype(np.float32)
        with torch.no_grad():
            expected = reference(pixel_values=torch.from_numpy(pixels).permute(0, 3, 1, 2)).predicted_depth.numpy()
        with tempfile.TemporaryDirectory() as folder:
            reference.save_pretrained(folder, safe_serialization=True)
            import mlx.core as mx
            got = np.array(DepthAnything(Path(folder)).forward(mx.array(pixels)))
        np.testing.assert_allclose(got, expected, rtol=1e-3, atol=1e-4 * float(np.abs(expected).max()))

    def test_the_input_size_follows_the_publisher_processor(self) -> None:
        from components.vision.app.backend.depth import DepthAnything
        model = DepthAnything.__new__(DepthAnything)
        model.short_side, model.patch = 518, 14
        self.assertEqual(model.input_size(768, 432), (924, 518))
        self.assertEqual(model.input_size(432, 768), (518, 924))


@unittest.skipUnless(all(_HAS[n] for n in ("mlx", "torch", "transformers", "PIL", "tokenizers")),
                     "mlx, torch, transformers and tokenizers are needed for the reference check")
class FlorenceMatchesTransformersTests(unittest.TestCase):
    """The MLX Florence-2 against transformers' Florence2ForConditionalGeneration on the same random
    weights, offline: the image tokens, and beam search token for token."""

    def test_a_small_random_checkpoint_generates_the_same_tokens(self) -> None:
        import numpy as np
        import torch
        from tokenizers import Tokenizer, models, pre_tokenizers
        from transformers import Florence2Config, Florence2ForConditionalGeneration

        from components.vision.app.backend.florence import Florence2

        torch.manual_seed(5)
        words = ["<s>", "<pad>", "</s>", "<unk>", "locate", "the", "objects", "with", "category",
                 "name", "in", "image.", "sky", "mask", "phrases", "caption:", "a", "tree"]
        image_token = len(words) + 4
        config = Florence2Config(
            vision_config={"embed_dim": [8, 16, 32, 64], "num_heads": [2, 2, 4, 4],
                           "num_groups": [2, 2, 4, 4], "depths": [1, 1, 2, 1], "window_size": 12,
                           "projection_dim": 32, "max_position_embeddings": 10,
                           "max_temporal_embeddings": 10, "drop_path_rate": 0.0},
            text_config={"vocab_size": image_token + 1, "d_model": 32, "encoder_layers": 2,
                         "decoder_layers": 2, "encoder_attention_heads": 4,
                         "decoder_attention_heads": 4, "encoder_ffn_dim": 64, "decoder_ffn_dim": 64,
                         "max_position_embeddings": 128},
            image_token_id=image_token)
        reference = Florence2ForConditionalGeneration(config).eval()
        tokenizer = Tokenizer(models.WordLevel({w: i for i, w in enumerate(words)}, unk_token="<unk>"))
        tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
        tokenizer.add_special_tokens(["<loc_0>", "<loc_1>", "<loc_2>", "<loc_3>", "<image>"])
        from PIL import Image
        image = Image.fromarray((np.random.default_rng(2).random((40, 60, 3)) * 255).astype(np.uint8))
        with tempfile.TemporaryDirectory() as folder:
            reference.save_pretrained(folder, safe_serialization=True)
            tokenizer.save(str(Path(folder) / "tokenizer.json"))
            folder = Path(folder)
            (folder / "generation_config.json").write_text(json.dumps({
                "bos_token_id": 0, "eos_token_id": 2, "decoder_start_token_id": 2,
                "forced_bos_token_id": 0, "num_beams": 3, "no_repeat_ngram_size": 3}))
            (folder / "processor_config.json").write_text(json.dumps({"post_processor_config": {}}))
            (folder / "preprocessor_config.json").write_text(json.dumps({
                "size": {"height": 64, "width": 64}, "image_seq_length": 5}))
            ours = Florence2(folder)
            pixels = ours.pixels(image)
            prompt = "locate the objects with category name in the image."
            ids = [image_token] * 5 + [0] + tokenizer.encode(prompt, add_special_tokens=False).ids + [2]
            torch_pixels = torch.from_numpy(pixels).permute(0, 3, 1, 2)
            with torch.no_grad():
                features = reference.model.get_image_features(torch_pixels).pooler_output.numpy()
                expected = reference.generate(input_ids=torch.tensor([ids]), pixel_values=torch_pixels,
                                              max_new_tokens=12, num_beams=3, no_repeat_ngram_size=3,
                                              forced_bos_token_id=0, forced_eos_token_id=2,
                                              early_stopping=True, decoder_start_token_id=2)[0].tolist()
            import mlx.core as mx
            got_features = ours.image_features(image)
            np.testing.assert_allclose(np.array(got_features.astype(mx.float32)), features, atol=1e-4)
            tokens, _score = ours.generate(ours.encode(got_features, prompt), max_new_tokens=12)
        self.assertEqual(tokens, expected)

    def test_location_tokens_parse_like_the_publisher_post_processor(self) -> None:
        from components.vision.app.backend.florence import _boxes, _polygons
        self.assertEqual(_boxes("house<loc_100><loc_200><loc_499><loc_799>tree<loc_0><loc_0><loc_9><loc_9>", set()),
                         [{"label": "house", "box": [0.1005, 0.2005, 0.4995, 0.7995]},
                          {"label": "tree", "box": [0.0005, 0.0005, 0.0095, 0.0095]}])
        self.assertEqual(_boxes("it<loc_1><loc_1><loc_2><loc_2>", {"it"}), [])
        polygons = _polygons("<loc_0><loc_0><loc_999><loc_0><loc_999><loc_499><loc_7>")
        self.assertEqual(len(polygons), 1)
        self.assertEqual(len(polygons[0]["polygons"][0]), 6, "an odd trailing coordinate is dropped")


_LIBRARY = shipped_model_library()
_INSTALLED = {m["package_id"]: _LIBRARY / m["package_id"] / m["revision"] / "model" for m in (DEPTH, BASE)}


@unittest.skipUnless(_HAS["mlx"] and _HAS["PIL"] and all(
    (path / "model.safetensors").is_file() for path in _INSTALLED.values()),
    "the shipped vision models are not installed on this Mac")
class ShippedModelTests(unittest.TestCase):
    """The real checkpoints, read in place (read-only) where this Mac has them."""

    def test_a_synthetic_scene_gets_a_sensible_depth_and_sky(self) -> None:
        import numpy as np
        from PIL import Image, ImageDraw

        from components.vision.app.backend.depth import DepthAnything
        from components.vision.app.backend.florence import Florence2

        image = Image.new("RGB", (768, 432), (120, 170, 230))
        draw = ImageDraw.Draw(image)
        draw.rectangle([0, 260, 768, 432], fill=(70, 120, 60))          # a field below the sky
        draw.rectangle([300, 160, 460, 330], fill=(150, 60, 40))        # a red house on it
        depth = DepthAnything(_INSTALLED[DEPTH["package_id"]]).predict(image, 518, 291)
        self.assertGreater(depth[280:, :].mean(), depth[:60, :].mean(), "the ground is nearer than the sky")
        florence = Florence2(_INSTALLED[BASE["package_id"]])
        features = florence.image_features(image)
        sky = florence.run(features, "<REFERRING_EXPRESSION_SEGMENTATION>", "sky")
        self.assertTrue(sky["instances"])
        top = [p for inst in sky["instances"] for p in inst["polygons"]][0]
        self.assertLess(np.mean(top[1::2]), 0.7, "the sky outline sits in the top part")


class VisionRegistrationTests(unittest.TestCase):
    def test_the_engine_routes_to_its_own_worker_and_modality(self) -> None:
        for manifest in (DEPTH, BASE, LARGE):
            model_packages.validate_manifest(manifest)
            self.assertEqual(manifest["engine"], "vision-mlx")
        self.assertEqual(standalone._engine_route("vision-mlx"), ("vision", "vision"))
        self.assertEqual(vocabulary.WORKER_FOR_MODALITY["vision"], "vision")
        self.assertEqual(standalone.modality_for_worker("vision"), "vision")

    def test_only_apache_or_mit_weights_and_only_the_small_depth_checkpoint(self) -> None:
        self.assertEqual(DEPTH["model_id"], "depth-anything/Depth-Anything-V2-Small-hf")
        self.assertEqual(DEPTH["license"], {"weights": "Apache-2.0"})
        self.assertEqual((BASE["license"], LARGE["license"]), ({"weights": "MIT"},) * 2)
        self.assertTrue(BASE.get("default"))
        self.assertFalse(LARGE.get("default"))
        for manifest in (DEPTH, BASE, LARGE):
            self.assertFalse(any(f["path"].endswith((".bin", ".pt", ".pth", ".pkl")) for f in manifest["files"]),
                             "no pickle is ever downloaded")

    def test_the_fleet_may_post_vision_and_release_idle_memory(self) -> None:
        allowed = lambda method, path: any(
            method == m and re.match(pattern, path) for m, pattern in auth.FLEET_ALLOWED)
        self.assertTrue(allowed("POST", "/studio/vision/v1/vision/analyze"))
        self.assertTrue(allowed("POST", "/api/hub/fleet/nodes/mac-0106-m2-8-256-hub/vision/analyze"))
        self.assertTrue(allowed("GET", "/studio/vision/api/catalog"))
        self.assertFalse(allowed("PUT", "/studio/vision/api/memory-policy"))
        self.assertTrue(allowed("POST", "/studio/vision/api/memory/release"))
        self.assertFalse(allowed("POST", "/studio/vision/api/settings"))
        self.assertFalse(allowed("POST", "/studio/vision/api/memory/release/all"))

    def test_vision_never_takes_the_machine_lease(self) -> None:
        self.assertIsNone(standalone._peer_lease_policy("v1/vision/analyze"))
        self.assertFalse(standalone._peer_work_request("POST", "v1/vision/analyze"))
        self.assertIn("vision/v1/vision/analyze", standalone._PEER_UNLEASED_PATHS)

    def test_a_model_list_is_read_for_admission(self) -> None:
        body = json.dumps({"model": ["a/b", " c/d ", ""], "tasks": ["depth"]}).encode()
        self.assertEqual(standalone._request_models(body, "application/json"), ["a/b", "c/d"])
        self.assertEqual(standalone._request_models(b'{"model": "a/b"}', "application/json"), [])
        self.assertEqual(standalone._request_models(b"--x", "multipart/form-data"), [])


class VisionPeerSurfaceTests(unittest.TestCase):
    """The peer and relay doors run beside a leased job, and still honour a pause."""

    ANSWER = {"models": {"depth": DEPTH["model_id"]}, "depth": {"w": 1}}

    def setUp(self) -> None:
        from tests.test_chat_hub_wiring import _PackagedHub, _StubWorker

        self.worker = _StubWorker(payload=self.ANSWER)
        self.addCleanup(self.worker.close)
        self.hub = _PackagedHub()
        self.hub.build_hub([{
            "id": "vision", "title": "Vision Studio", "modality": "vision",
            "host": "127.0.0.1", "port": self.worker.port, "machine": "node-v", "local": True,
            "runtime_identity": {"id": "vision-runtime"}, "hub_token": "worker-token",
            "managed_output_roots": []}])
        self.addCleanup(self.hub.tear_down_hub)

    def post(self, path="/studio/vision/v1/vision/analyze", model=None):
        async def run():
            transport = httpx.ASGITransport(app=self.hub.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://hub") as client:
                return await client.post(path, headers=self.hub.owner_headers(), json={
                    "model": model or [DEPTH["model_id"]], "tasks": ["depth"],
                    "image": {"b64": "eA=="}})
        return asyncio.run(run())

    def test_a_busy_mac_still_answers_and_no_lease_is_taken(self) -> None:
        held = standalone._reserve_peer_machine("image", "node-v")
        self.assertIsNotNone(held)
        try:
            leases = dict(standalone._PEER_LEASES)
            with patch.dict(os.environ, {"STUDIOHUB_MODELS_DIR": ""}):
                for path in ("/studio/vision/v1/vision/analyze", "/api/hub/fleet/nodes/local/vision/analyze"):
                    answer = self.post(path)
                    self.assertEqual(answer.status_code, 200, answer.text)
                    self.assertEqual(answer.json(), self.ANSWER)
            self.assertEqual(dict(standalone._PEER_LEASES), leases)
            self.assertEqual(self.worker.requests[-1]["path"], "/v1/vision/analyze")
            self.assertEqual(self.worker.requests[-1]["token"], "worker-token")
        finally:
            standalone._release_peer_lease(held)

    def test_every_listed_model_must_be_installed_here(self) -> None:
        with tempfile.TemporaryDirectory() as library, \
                patch.dict(os.environ, {"STUDIOHUB_MODELS_DIR": library}):
            answer = self.post(model=[DEPTH["model_id"], BASE["model_id"]])
        self.assertEqual(answer.status_code, 503, answer.text)
        self.assertEqual(answer.json()["code"], "MODEL_NOT_READY")
        self.assertEqual(self.worker.requests, [])

    def test_a_paused_mac_refuses_before_the_worker_sees_it(self) -> None:
        from components.hub.backend import registry

        with patch.object(registry, "machine_enabled", lambda machine: machine != "node-v"):
            answer = self.post()
        self.assertEqual(answer.status_code, 409, answer.text)
        self.assertEqual(answer.json()["code"], "MACHINE_DISABLED")
        self.assertEqual(self.worker.requests, [])

    def test_a_mac_that_is_not_a_controller_has_no_nodes_to_relay_to(self) -> None:
        answer = self.post("/api/hub/fleet/nodes/some-other-mac/vision/analyze")
        self.assertEqual(answer.status_code, 404, answer.text)
        self.assertEqual(self.worker.requests, [])


if __name__ == "__main__":
    unittest.main()
