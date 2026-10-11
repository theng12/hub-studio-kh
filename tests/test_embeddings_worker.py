"""The Embeddings Studio: the MLX encoder, the worker's route, and the Hub's unleased peer path."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import math
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import httpx

from components import model_packages
from components.hub import standalone
from components.hub.backend import auth, vocabulary

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "model-manifests/bge-small-en-v1-5-bf16.json").read_text())
MODEL = MANIFEST["model_id"]
_HAS = {name: importlib.util.find_spec(name) is not None
        for name in ("mlx", "tokenizers", "torch", "transformers", "fastapi")}


class _FakeEncoder:
    max_tokens = 8

    def token_counts(self, texts):
        return [len(text.split()) + 2 for text in texts]

    def encode(self, texts):
        return [[float(len(text)), 1.0] for text in texts], sum(self.token_counts(texts))


@unittest.skipUnless(_HAS["fastapi"], "fastapi is not installed")
class EmbeddingsRouteTests(unittest.TestCase):
    TOKEN = "embeddings-worker-token"

    def setUp(self) -> None:
        from fastapi.testclient import TestClient

        from components.chat.app.backend.memory_policy import MemoryPolicy
        from components.embeddings.app.backend.main import EmbeddingService, create_app

        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.loads = []
        self.service = EmbeddingService(
            [MANIFEST], Path(self.temporary.name),
            loader=lambda manifest: self.loads.append(manifest["model_id"]) or _FakeEncoder())
        self.installed = {"state": "files_verified"}
        self.service.files = lambda manifest: self.installed
        policy = MemoryPolicy(Path(self.temporary.name), self.service)
        self.client = TestClient(create_app(self.service, policy, self.TOKEN, "a" * 64))
        self.headers = {"X-Hub-Token": self.TOKEN}

    def post(self, body):
        return self.client.post("/v1/embeddings", json=body, headers=self.headers)

    def test_a_batch_comes_back_in_input_order_in_the_openai_shape(self) -> None:
        answer = self.post({"model": MODEL, "input": ["a", "bbb", "cc"]})
        self.assertEqual(answer.status_code, 200, answer.text)
        body = answer.json()
        self.assertEqual(body["model"], MODEL)
        self.assertEqual([row["index"] for row in body["data"]], [0, 1, 2])
        self.assertEqual([row["embedding"][0] for row in body["data"]], [1.0, 3.0, 2.0])
        self.assertEqual(body["usage"], {"prompt_tokens": 9, "total_tokens": 9})
        # One string is one input; the model defaults to the manifest marked default.
        single = self.post({"input": "hello"}).json()
        self.assertEqual((len(single["data"]), single["model"]), (1, MODEL))
        self.assertEqual(self.loads, [MODEL], "the encoder loads once and stays resident")

    def test_bad_requests_are_refused_by_name(self) -> None:
        for body, status, words in (
                ({"input": []}, 422, "input"),
                ({"input": ["ok", ""]}, 422, "input"),
                ({"input": [1, 2]}, 422, "input"),
                ({"input": ["ok"], "dimensions": 64}, 422, "dimensions"),
                ({"input": ["ok"], "encoding_format": "base64"}, 422, "encoding_format"),
                ({"input": ["x"] * 2049}, 422, "2048"),
                ({"input": ["one two three four five six"]}, 422, "tokens"),
                ({"model": "someone/else", "input": ["ok"]}, 404, "someone/else")):
            with self.subTest(body=str(body)[:60]):
                answer = self.post(body)
                self.assertEqual(answer.status_code, status, answer.text)
                self.assertIn(words, answer.text)

    def test_a_model_that_is_not_installed_is_not_ready(self) -> None:
        self.installed = {"state": "not_installed", "reason": "not on this Mac"}
        answer = self.post({"input": ["ok"]})
        self.assertEqual(answer.status_code, 503)
        self.assertIn("MODEL_NOT_READY", answer.text)
        self.assertEqual(self.loads, [])

    def test_only_the_hub_token_is_accepted(self) -> None:
        self.assertEqual(self.client.post("/v1/embeddings", json={"input": ["ok"]}).status_code, 401)
        self.assertEqual(self.client.get("/api/health", headers={"X-Hub-Token": "x"}).status_code, 401)

    def test_catalog_and_memory_contracts(self) -> None:
        row = self.client.get("/api/catalog", headers=self.headers).json()["models"][0]
        self.assertEqual((row["repo"], row["hub_modality"], row["capabilities"]),
                         (MODEL, "embeddings", ["embeddings"]))
        self.assertTrue(row["execution_ready"])
        self.assertEqual(row["lane_contract"]["operation"], "text.embedding")
        self.assertEqual((row["dimensions"], row["pooling"], row["normalized"]), (384, "cls", True))
        self.post({"input": ["ok"]})
        self.assertEqual(self.client.get("/api/memory-policy", headers=self.headers)
                         .json()["loaded_model"], MODEL)
        released = self.client.post("/api/memory/release", headers=self.headers).json()
        self.assertFalse(released["resident"])
        self.post({"input": ["ok"]})
        self.assertEqual(self.loads, [MODEL, MODEL], "a released model loads again on use")


@unittest.skipUnless(all(_HAS[name] for name in ("mlx", "tokenizers", "torch", "transformers")),
                     "mlx, tokenizers, torch and transformers are needed for the reference check")
class EncoderMatchesTransformersTests(unittest.TestCase):
    """The MLX BERT against Hugging Face's own BertModel on the same random weights, offline."""

    def test_cls_and_mean_pooling_match_the_reference(self) -> None:
        import numpy as np
        import torch
        from tokenizers import Tokenizer, models, normalizers, pre_tokenizers, processors
        from transformers import BertConfig, BertModel

        from components.embeddings.app.backend.encoder import SentenceEncoder

        words = ["the", "voynich", "manuscript", "opium", "wars", "book", "binder", "a", "of"]
        vocab = {token: index for index, token in enumerate(
            ["[PAD]", "[UNK]", "[CLS]", "[SEP]", *words])}
        tokenizer = Tokenizer(models.WordPiece(vocab, unk_token="[UNK]"))
        tokenizer.normalizer = normalizers.BertNormalizer(lowercase=True)
        tokenizer.pre_tokenizer = pre_tokenizers.BertPreTokenizer()
        tokenizer.post_processor = processors.TemplateProcessing(
            single="[CLS] $A [SEP]", special_tokens=[("[CLS]", 2), ("[SEP]", 3)])
        torch.manual_seed(7)
        config = BertConfig(vocab_size=len(vocab), hidden_size=32, num_hidden_layers=2,
                            num_attention_heads=4, intermediate_size=64,
                            max_position_embeddings=64, hidden_act="gelu")
        reference = BertModel(config).eval()
        texts = ["The Voynich manuscript", "the opium wars of a book binder binder", "a"]
        with tempfile.TemporaryDirectory() as folder:
            reference.save_pretrained(folder, safe_serialization=True)
            tokenizer.save(str(Path(folder) / "tokenizer.json"))
            encoded = tokenizer.encode_batch(texts)
            width = max(len(item.ids) for item in encoded)
            ids = torch.tensor([item.ids + [0] * (width - len(item.ids)) for item in encoded])
            mask = (ids != 0).long()
            with torch.no_grad():
                hidden = reference(input_ids=ids, attention_mask=mask).last_hidden_state
            for pooling in ("cls", "mean"):
                if pooling == "cls":
                    pooled = hidden[:, 0]
                else:
                    pooled = (hidden * mask[..., None]).sum(1) / mask.sum(1, keepdim=True)
                expected = torch.nn.functional.normalize(pooled, dim=-1).numpy()
                encoder = SentenceEncoder(Path(folder), pooling=pooling, normalize=True,
                                          max_tokens=64)
                vectors, tokens = encoder.encode(texts)
                self.assertEqual(tokens, sum(len(item.ids) for item in encoded))
                np.testing.assert_allclose(np.array(vectors), expected, atol=1e-5)


_INSTALLED = (Path.home() / "StudioHubModels" / MANIFEST["package_id"] / MANIFEST["revision"]
              / "model")


@unittest.skipUnless(_HAS["mlx"] and _HAS["tokenizers"] and (_INSTALLED / "model.safetensors").is_file(),
                     "the shipped embedding model is not installed on this Mac")
class ShippedModelTests(unittest.TestCase):
    """The real BGE small checkpoint, read in place (read-only) where this Mac has it."""

    def test_paraphrases_score_above_different_topics_and_a_thousand_texts_are_quick(self) -> None:
        from components.embeddings.app.backend.encoder import SentenceEncoder

        defaults = MANIFEST["defaults"]
        encoder = SentenceEncoder(_INSTALLED, pooling=defaults["pooling"],
                                  normalize=defaults["normalize"], max_tokens=defaults["max_tokens"])
        (voynich, reworded, opium), _tokens = encoder.encode([
            "The Mystery of the Voynich Manuscript",
            "Unlocking the secrets of the undeciphered Voynich codex",
            "How the Opium Wars reshaped China"])
        cosine = lambda a, b: sum(x * y for x, y in zip(a, b))
        self.assertAlmostEqual(math.sqrt(cosine(voynich, voynich)), 1.0, places=5)
        self.assertEqual(len(voynich), defaults["dimensions"])
        self.assertGreater(cosine(voynich, reworded), cosine(voynich, opium) + 0.2)
        started = time.monotonic()
        vectors, _tokens = encoder.encode([f"Idea {i}: a short story about topic {i % 37}"
                                           for i in range(1000)])
        self.assertEqual(len(vectors), 1000)
        self.assertLess(time.monotonic() - started, 10.0)


class EmbeddingsRegistrationTests(unittest.TestCase):
    def test_the_engine_routes_to_its_own_worker_and_modality(self) -> None:
        model_packages.validate_manifest(MANIFEST)
        self.assertEqual(standalone._engine_route("embed-mlx"), ("embeddings", "embeddings"))
        self.assertEqual(vocabulary.WORKER_FOR_MODALITY["embeddings"], "embeddings")
        self.assertEqual(standalone.modality_for_worker("embeddings"), "embeddings")

    def test_the_fleet_may_post_embeddings_and_nothing_else_there(self) -> None:
        allowed = lambda method, path: any(
            method == m and __import__("re").match(pattern, path) for m, pattern in auth.FLEET_ALLOWED)
        self.assertTrue(allowed("POST", "/studio/embeddings/v1/embeddings"))
        self.assertTrue(allowed("GET", "/studio/embeddings/api/catalog"))
        self.assertFalse(allowed("PUT", "/studio/embeddings/api/memory-policy"))
        self.assertFalse(allowed("POST", "/studio/embeddings/api/memory/release"))

    def test_embeddings_never_take_the_machine_lease(self) -> None:
        self.assertIsNone(standalone._peer_lease_policy("v1/embeddings"))
        self.assertFalse(standalone._peer_work_request("POST", "v1/embeddings"))
        self.assertIn("embeddings/v1/embeddings", standalone._PEER_UNLEASED_PATHS)


class EmbeddingsPeerSurfaceTests(unittest.TestCase):
    """`/studio/embeddings/v1/embeddings` runs beside a leased job, and still honours a pause."""

    def setUp(self) -> None:
        from tests.test_chat_hub_wiring import _PackagedHub, _StubWorker

        self.worker = _StubWorker(payload={"object": "list", "model": MODEL, "data": [
            {"object": "embedding", "index": 0, "embedding": [1.0]}], "usage": {}})
        self.addCleanup(self.worker.close)
        self.hub = _PackagedHub()
        self.hub.build_hub([{
            "id": "embeddings", "title": "Embeddings Studio", "modality": "embeddings",
            "host": "127.0.0.1", "port": self.worker.port, "machine": "node-e", "local": True,
            "runtime_identity": {"id": "embeddings-runtime"}, "hub_token": "worker-token",
            "managed_output_roots": []}])
        self.addCleanup(self.hub.tear_down_hub)

    def post(self):
        async def run():
            transport = httpx.ASGITransport(app=self.hub.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://hub") as client:
                return await client.post("/studio/embeddings/v1/embeddings",
                                         headers=self.hub.owner_headers(),
                                         json={"model": MODEL, "input": ["a"]})
        return asyncio.run(run())

    def test_a_busy_mac_still_answers_and_no_lease_is_taken(self) -> None:
        held = standalone._reserve_peer_machine("image", "node-e")
        self.assertIsNotNone(held)
        try:
            leases = dict(standalone._PEER_LEASES)
            # No model library configured: the worker is the readiness authority (as in a fixture).
            with patch.dict(os.environ, {"STUDIOHUB_MODELS_DIR": ""}):
                answer = self.post()
            self.assertEqual(answer.status_code, 200, answer.text)
            self.assertEqual(answer.json()["data"][0]["embedding"], [1.0])
            self.assertEqual(dict(standalone._PEER_LEASES), leases)
            self.assertEqual(self.worker.requests[-1]["token"], "worker-token")
        finally:
            standalone._release_peer_lease(held)

    def test_a_model_this_mac_has_not_installed_is_refused_at_the_door(self) -> None:
        with tempfile.TemporaryDirectory() as library, \
                patch.dict(os.environ, {"STUDIOHUB_MODELS_DIR": library}):
            answer = self.post()
        self.assertEqual(answer.status_code, 503, answer.text)
        self.assertEqual(answer.json()["code"], "MODEL_NOT_READY")
        self.assertEqual(self.worker.requests, [])

    def test_a_paused_mac_refuses_before_the_worker_sees_it(self) -> None:
        from components.hub.backend import registry

        with patch.object(registry, "machine_enabled", lambda machine: machine != "node-e"):
            answer = self.post()
        self.assertEqual(answer.status_code, 409, answer.text)
        self.assertEqual(answer.json()["code"], "MACHINE_DISABLED")
        self.assertEqual(self.worker.requests, [])


if __name__ == "__main__":
    unittest.main()
