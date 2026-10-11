"""A BERT sentence encoder in plain MLX: texts in, unit vectors out.

Written against the bundled `mlx` and `tokenizers` (both already in the shared Voice runtime), so
the Embeddings worker adds no package. The module names mirror Hugging Face's BertModel, so the
publisher's safetensors load by name. Pooling and normalisation are the model's own facts, read
from its manifest `defaults` (`pooling`, `normalize`), never assumed here.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import mlx.core as mx
import mlx.nn as nn

# Texts per forward pass. Short idea titles pad to ~40 tokens, so 64 rows is a few MB of
# activations; a longer text only makes its own batch longer (rows are sorted by length).
BATCH_SIZE = 64


class _SelfAttention(nn.Module):
    def __init__(self, hidden: int, heads: int) -> None:
        super().__init__()
        self.heads = heads
        self.query = nn.Linear(hidden, hidden)
        self.key = nn.Linear(hidden, hidden)
        self.value = nn.Linear(hidden, hidden)

    def __call__(self, x: mx.array, mask: mx.array) -> mx.array:
        batch, length, hidden = x.shape
        split = lambda t: t.reshape(batch, length, self.heads, -1).transpose(0, 2, 1, 3)
        q, k, v = split(self.query(x)), split(self.key(x)), split(self.value(x))
        out = mx.fast.scaled_dot_product_attention(
            q, k, v, scale=1.0 / math.sqrt(hidden // self.heads), mask=mask)
        return out.transpose(0, 2, 1, 3).reshape(batch, length, hidden)


class _DenseNorm(nn.Module):
    def __init__(self, inputs: int, hidden: int, eps: float) -> None:
        super().__init__()
        self.dense = nn.Linear(inputs, hidden)
        self.LayerNorm = nn.LayerNorm(hidden, eps=eps)

    def __call__(self, x: mx.array, residual: mx.array) -> mx.array:
        return self.LayerNorm(self.dense(x) + residual)


class _Attention(nn.Module):
    def __init__(self, hidden: int, heads: int, eps: float) -> None:
        super().__init__()
        setattr(self, "self", _SelfAttention(hidden, heads))
        self.output = _DenseNorm(hidden, hidden, eps)

    def __call__(self, x: mx.array, mask: mx.array) -> mx.array:
        return self.output(self["self"](x, mask), x)


class _Intermediate(nn.Module):
    def __init__(self, hidden: int, intermediate: int) -> None:
        super().__init__()
        self.dense = nn.Linear(hidden, intermediate)

    def __call__(self, x: mx.array) -> mx.array:
        return nn.gelu(self.dense(x))


class _Layer(nn.Module):
    def __init__(self, hidden: int, heads: int, intermediate: int, eps: float) -> None:
        super().__init__()
        self.attention = _Attention(hidden, heads, eps)
        self.intermediate = _Intermediate(hidden, intermediate)
        self.output = _DenseNorm(intermediate, hidden, eps)

    def __call__(self, x: mx.array, mask: mx.array) -> mx.array:
        x = self.attention(x, mask)
        return self.output(self.intermediate(x), x)


class _Encoder(nn.Module):
    def __init__(self, config: dict) -> None:
        super().__init__()
        self.layer = [_Layer(config["hidden_size"], config["num_attention_heads"],
                             config["intermediate_size"], config["layer_norm_eps"])
                      for _ in range(config["num_hidden_layers"])]


class _Embeddings(nn.Module):
    def __init__(self, config: dict) -> None:
        super().__init__()
        hidden = config["hidden_size"]
        self.word_embeddings = nn.Embedding(config["vocab_size"], hidden)
        self.position_embeddings = nn.Embedding(config["max_position_embeddings"], hidden)
        self.token_type_embeddings = nn.Embedding(config["type_vocab_size"], hidden)
        self.LayerNorm = nn.LayerNorm(hidden, eps=config["layer_norm_eps"])

    def __call__(self, ids: mx.array) -> mx.array:
        positions = mx.arange(ids.shape[1])[None, :]
        return self.LayerNorm(self.word_embeddings(ids) + self.position_embeddings(positions)
                              + self.token_type_embeddings(mx.zeros_like(ids)))


class BertModel(nn.Module):
    def __init__(self, config: dict) -> None:
        super().__init__()
        if config.get("model_type") != "bert" or config.get("hidden_act") != "gelu":
            raise ValueError(
                f"this encoder runs BERT with gelu; the checkpoint states model_type "
                f"{config.get('model_type')!r}, hidden_act {config.get('hidden_act')!r}")
        self.embeddings = _Embeddings(config)
        self.encoder = _Encoder(config)

    def __call__(self, ids: mx.array, attention: mx.array) -> mx.array:
        # Additive mask: padded keys get a large negative score, broadcast over heads and queries.
        mask = ((1.0 - attention.astype(mx.float32)) * -1e9)[:, None, None, :]
        x = self.embeddings(ids)
        for layer in self.encoder.layer:
            x = layer(x, mask)
        return x


class SentenceEncoder:
    """One loaded checkpoint: tokenizer, weights and the model's pooling rule."""

    def __init__(self, model_dir: Path, *, pooling: str, normalize: bool,
                 max_tokens: int) -> None:
        from tokenizers import Tokenizer

        if pooling not in ("cls", "mean"):
            raise ValueError(f"pooling must be cls or mean; the manifest states {pooling!r}")
        model_dir = Path(model_dir)
        config = json.loads((model_dir / "config.json").read_text(encoding="utf-8"))
        self.dimensions = int(config["hidden_size"])
        self.max_tokens = min(int(max_tokens), int(config["max_position_embeddings"]))
        self.pooling, self.normalize = pooling, normalize
        self.tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        self.tokenizer.enable_truncation(max_length=self.max_tokens)
        self.tokenizer.no_padding()
        self.model = BertModel(config)
        # bf16 on disk, float32 in use: the vectors are compared by cosine, and 134 MB is cheap.
        weights = {name: value.astype(mx.float32)
                   for name, value in mx.load(str(model_dir / "model.safetensors")).items()
                   if not name.startswith("pooler.")}
        self.model.load_weights(list(weights.items()), strict=True)
        mx.eval(self.model.parameters())

    def token_counts(self, texts: list[str]) -> list[int]:
        return [len(item.ids) for item in self.tokenizer.encode_batch(texts)]

    def encode(self, texts: list[str]) -> tuple[list[list[float]], int]:
        """Vectors in input order, and the number of tokens the model read."""
        encoded = self.tokenizer.encode_batch(texts)
        order = sorted(range(len(texts)), key=lambda i: len(encoded[i].ids))
        vectors: list[list[float] | None] = [None] * len(texts)
        for start in range(0, len(order), BATCH_SIZE):
            chunk = order[start:start + BATCH_SIZE]
            width = max(len(encoded[i].ids) for i in chunk)
            ids = mx.array([encoded[i].ids + [0] * (width - len(encoded[i].ids)) for i in chunk])
            attention = mx.array([[1] * len(encoded[i].ids) + [0] * (width - len(encoded[i].ids))
                                  for i in chunk])
            hidden = self.model(ids, attention)
            if self.pooling == "cls":
                pooled = hidden[:, 0, :]
            else:
                weight = attention.astype(mx.float32)[:, :, None]
                pooled = (hidden * weight).sum(axis=1) / mx.maximum(weight.sum(axis=1), 1.0)
            if self.normalize:
                pooled = pooled / mx.maximum(mx.linalg.norm(pooled, axis=-1, keepdims=True), 1e-12)
            for index, row in zip(chunk, pooled.tolist()):
                vectors[index] = row
        return vectors, sum(len(item.ids) for item in encoded)  # type: ignore[return-value]
