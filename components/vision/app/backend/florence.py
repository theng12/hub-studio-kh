"""Florence-2 in plain MLX: a picture and a task prompt in, boxes or outlines out.

DaViT image encoder, the multimodal projector, and the BART encoder-decoder with Hugging Face's
beam search, written against the bundled `mlx`, `numpy`, `Pillow` and `tokenizers` (all already in
the shared Voice runtime), so the Vision worker adds no package. It reads the publisher's own
`model.safetensors` in the transformers-native layout (`florence-community/Florence-2-*`) by its
Hugging Face names; nothing is converted, and no pickle is ever loaded. Base and Large share this
code: every size is read from the checkpoint's `config.json`.

The prompts, the `<loc_*>` parsing and the generation settings (3 beams, no repeated 3-grams,
forced BOS/EOS) are the publisher's own (`processing_florence2.py`, `generation_config.json`).
Coordinates come back normalised to 0..1, so any picture size or aspect works.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import re

import mlx.core as mx
import numpy as np
from PIL import Image
from tokenizers import Tokenizer

MEAN = np.array([0.485, 0.456, 0.406], np.float32)
STD = np.array([0.229, 0.224, 0.225], np.float32)
BINS = 1000
NEG = -1.0e9

PROMPTS = {
    "<OD>": "Locate the objects with category name in the image.",
    "<DETAILED_CAPTION>": "Describe in detail what is shown in the image.",
    "<CAPTION_TO_PHRASE_GROUNDING>": "Locate the phrases in the caption: {input}",
    "<REFERRING_EXPRESSION_SEGMENTATION>": "Locate {input} in the image with mask",
}
_TEXT = r"^\s*(.*?)(?=<od>|</od>|<box>|</box>|<bbox>|</bbox>|<loc_)"
_BOX = r"<loc_(\d+)><loc_(\d+)><loc_(\d+)><loc_(\d+)>"


def _gelu(x: mx.array) -> mx.array:
    return x * (1 + mx.erf(x * 0.7071067811865476)) / 2


class Florence2:
    """One loaded checkpoint. `image_features` once per picture, then `run(task, ...)` per prompt."""

    def __init__(self, model_dir: Path, dtype=mx.float32, vision_dtype=mx.float32) -> None:
        model_dir = Path(model_dir)
        config = json.loads((model_dir / "config.json").read_text())
        generation = json.loads((model_dir / "generation_config.json").read_text())
        processor = json.loads((model_dir / "processor_config.json").read_text())
        image = json.loads((model_dir / "preprocessor_config.json").read_text())
        self.vision = config["vision_config"]
        self.text = config["text_config"]
        self.image_token = int(config["image_token_id"])
        self.size = (int(image["size"]["width"]), int(image["size"]["height"]))
        self.image_tokens = int(image["image_seq_length"])
        self.beams = int(generation.get("num_beams", 3))
        self.no_repeat = int(generation.get("no_repeat_ngram_size", 3))
        self.bos, self.eos = int(generation["bos_token_id"]), int(generation["eos_token_id"])
        self.start = int(generation["decoder_start_token_id"])
        self.forced_bos = generation.get("forced_bos_token_id")
        self.banned_phrases = set(((processor.get("post_processor_config") or {})
                                   .get("phrase_grounding") or {}).get("banned_grounding_tokens") or [])
        self.tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        added = json.loads((model_dir / "tokenizer.json").read_text())["added_tokens"]
        self.literal = {int(token["id"]): token["content"] for token in added}
        self.dtype = dtype
        embeddings = ("shared.weight", "embed_positions.weight", "column_embeddings.weight",
                      "row_embeddings.weight")
        self.w = {}
        for name, value in mx.load(str(model_dir / "model.safetensors")).items():
            name = name.removeprefix("model.")
            if value.ndim == 4:
                value = value.transpose(0, 2, 3, 1)       # Conv2d (O, I, kh, kw) -> (O, kh, kw, I)
            elif value.ndim == 2 and name.endswith(".weight") and not name.endswith(embeddings):
                value = value.T                              # Linear -> x @ W
            # float16 drifts ~20% at the image encoder's output after 12 DaViT blocks, and a
            # float16 BART changed some outlines' tokens (measured against transformers' float32
            # reference), so both halves default to float32: token-identical, ~25% slower.
            self.w[name] = value.astype(vision_dtype if name.startswith(("vision_tower.", "multi_modal_projector."))
                                       else dtype)
        mx.eval(self.w)

    # ---- image -----------------------------------------------------------------------------
    def pixels(self, image: Image.Image) -> np.ndarray:
        array = np.asarray(image.convert("RGB").resize(self.size, Image.BICUBIC), np.float32)
        return ((array / 255.0 - MEAN) / STD)[None]

    def _ln(self, x, name, eps=1e-5):
        return mx.fast.layer_norm(x, self.w[name + ".weight"], self.w[name + ".bias"], eps)

    def _lin(self, x, name):
        out = x @ self.w[name + ".weight"]
        bias = self.w.get(name + ".bias")
        return out if bias is None else out + bias

    def _conv(self, x, name, stride=1, padding=0, groups=1):
        out = mx.conv2d(x, self.w[name + ".weight"], stride=stride, padding=padding, groups=groups)
        return out + self.w[name + ".bias"]

    def _window_attention(self, x, name, heads):
        batch, height, width, dim = x.shape
        size = int(self.vision["window_size"])
        pad_h, pad_w = (-height) % size, (-width) % size
        if pad_h or pad_w:
            x = mx.pad(x, [(0, 0), (0, pad_h), (0, pad_w), (0, 0)])
        rows, cols = x.shape[1] // size, x.shape[2] // size
        x = x.reshape(batch, rows, size, cols, size, dim).transpose(0, 1, 3, 2, 4, 5)
        x = x.reshape(-1, size * size, dim)
        qkv = self._lin(x, name + ".qkv").reshape(x.shape[0], size * size, 3, heads, dim // heads)
        q, k, v = (qkv[:, :, i].transpose(0, 2, 1, 3) for i in range(3))
        x = mx.fast.scaled_dot_product_attention(q, k, v, scale=(dim // heads) ** -0.5)
        x = self._lin(x.transpose(0, 2, 1, 3).reshape(-1, size * size, dim), name + ".proj")
        x = x.reshape(batch, rows, cols, size, size, dim).transpose(0, 1, 3, 2, 4, 5)
        return x.reshape(batch, rows * size, cols * size, dim)[:, :height, :width, :]

    def _channel_attention(self, x, name, groups):
        batch, tokens, dim = x.shape
        qkv = self._lin(x, name + ".qkv").reshape(batch, tokens, 3, groups, dim // groups)
        q, k, v = (qkv[:, :, i].transpose(0, 2, 3, 1) for i in range(3))   # (B, g, C/g, N)
        # The sum runs over every pixel of the stage (36,864 at the first one): float16 overflows.
        q, k = q.astype(mx.float32), k.astype(mx.float32)
        weights = mx.softmax((q * tokens ** -0.5) @ k.swapaxes(-1, -2), axis=-1)
        x = (weights.astype(v.dtype) @ v).transpose(0, 3, 1, 2).reshape(batch, tokens, dim)
        return self._lin(x, name + ".proj")

    def _block(self, x, name, stage, spatial):
        batch, height, width, dim = x.shape
        x = self._conv(x, name + ".conv1", padding=1, groups=dim) + x
        h = self._ln(x, name + ".norm1")
        if spatial:
            h = self._window_attention(h, name + ".window_attn", int(self.vision["num_heads"][stage]))
        else:
            h = self._channel_attention(h.reshape(batch, height * width, dim), name + ".channel_attn",
                                        int(self.vision["num_groups"][stage]))
            h = h.reshape(batch, height, width, dim)
        x = x + h
        x = self._conv(x, name + ".conv2", padding=1, groups=dim) + x
        h = self._lin(_gelu(self._lin(self._ln(x, name + ".norm2"), name + ".ffn.fc1")), name + ".ffn.fc2")
        return x + h

    def image_features(self, image: Image.Image) -> mx.array:
        """(1, 577, d_model) projected image tokens for one picture."""
        x = mx.array(self.pixels(image)).astype(self.w["vision_tower.convs.0.conv.weight"].dtype)
        v = self.vision
        for stage in range(len(v["embed_dim"])):
            name = f"vision_tower.convs.{stage}"
            if v["patch_prenorm"][stage]:
                x = self._ln(x, name + ".norm")
            x = self._conv(x, name + ".conv", stride=v["patch_stride"][stage],
                           padding=v["patch_padding"][stage])
            if not v["patch_prenorm"][stage]:
                x = self._ln(x, name + ".norm")
            for block in range(v["depths"][stage]):
                base = f"vision_tower.blocks.{stage}.{block}"
                x = self._block(x, base + ".spatial_block", stage, True)
                x = self._block(x, base + ".channel_block", stage, False)
        batch, height, width, dim = x.shape
        p = "multi_modal_projector"
        position = mx.concatenate([
            mx.broadcast_to(self.w[p + ".image_position_embed.column_embeddings.weight"][None, :width],
                            (height, width, dim // 2)),
            mx.broadcast_to(self.w[p + ".image_position_embed.row_embeddings.weight"][:height, None],
                            (height, width, dim - dim // 2))], axis=-1)
        tokens = (x + position).reshape(batch, height * width, dim)
        tokens = tokens + self.w[p + ".visual_temporal_embed.pos_idx_to_embed"][0]
        tokens = mx.concatenate([tokens.mean(axis=1, keepdims=True), tokens], axis=1)
        out = self._ln(self._lin(tokens, p + ".image_projection"), p + ".image_proj_norm")
        mx.eval(out)
        return out

    # ---- BART ------------------------------------------------------------------------------
    def _attention(self, q_in, kv, name, cache=None, position=0):
        """`kv` is either the keys/values source or (k, v) already projected. With `cache`, the
        new key/value is written at `position` of a buffer grown 256 steps at a time."""
        heads = int(self.text["encoder_attention_heads"])
        batch, length, dim = q_in.shape
        split = lambda t: t.reshape(t.shape[0], -1, heads, dim // heads).transpose(0, 2, 1, 3)
        q = split(self._lin(q_in, name + ".q_proj"))
        if isinstance(kv, tuple):
            k, v = kv
        else:
            k, v = split(self._lin(kv, name + ".k_proj")), split(self._lin(kv, name + ".v_proj"))
            if cache is not None:
                buffers = cache.get(name)
                if buffers is None or buffers[0].shape[2] <= position:
                    grow = lambda: mx.zeros((batch, heads, 256, dim // heads), k.dtype)  # noqa: E731
                    buffers = [grow(), grow()] if buffers is None else [
                        mx.concatenate([buffers[0], grow()], axis=2),
                        mx.concatenate([buffers[1], grow()], axis=2)]
                buffers[0][:, :, position:position + 1] = k
                buffers[1][:, :, position:position + 1] = v
                cache[name] = buffers
                k, v = buffers[0][:, :, :position + 1], buffers[1][:, :, :position + 1]
        out = mx.fast.scaled_dot_product_attention(q, k, v, scale=(dim // heads) ** -0.5)
        return self._lin(out.transpose(0, 2, 1, 3).reshape(batch, length, dim), name + ".out_proj")

    def _ffn(self, x, name):
        return self._lin(_gelu(self._lin(x, name + ".fc1")), name + ".fc2")

    def encode(self, features: mx.array, prompt: str) -> mx.array:
        ids = self.tokenizer.encode(prompt, add_special_tokens=False).ids
        ids = [self.bos] + ids + [self.eos]
        lm = "language_model"
        text = self.w[lm + ".shared.weight"][mx.array(ids)][None]
        x = mx.concatenate([features.astype(self.dtype), text], axis=1)
        positions = mx.arange(x.shape[1]) + 2
        x = self._ln(x + self.w[lm + ".encoder.embed_positions.weight"][positions],
                     lm + ".encoder.layernorm_embedding")
        for layer in range(int(self.text["encoder_layers"])):
            name = f"{lm}.encoder.layers.{layer}"
            x = self._ln(x + self._attention(x, x, name + ".self_attn"), name + ".self_attn_layer_norm")
            x = self._ln(x + self._ffn(x, name), name + ".final_layer_norm")
        mx.eval(x)
        return x

    def _decode_step(self, tokens: mx.array, position: int, cache: dict, cross: list) -> mx.array:
        lm = "language_model"
        x = self.w[lm + ".shared.weight"][tokens][:, None]
        x = self._ln(x + self.w[lm + ".decoder.embed_positions.weight"][position + 2],
                     lm + ".decoder.layernorm_embedding")
        for layer in range(int(self.text["decoder_layers"])):
            name = f"{lm}.decoder.layers.{layer}"
            x = self._ln(x + self._attention(x, x, name + ".self_attn", cache, position),
                         name + ".self_attn_layer_norm")
            x = self._ln(x + self._attention(x, cross[layer], name + ".encoder_attn"),
                         name + ".encoder_attn_layer_norm")
            x = self._ln(x + self._ffn(x, name), name + ".final_layer_norm")
        return (x[:, 0] @ self.w[lm + ".shared.weight"].T).astype(mx.float32)

    def generate(self, encoded: mx.array, max_new_tokens: int = 1024) -> tuple[list[int], float]:
        """Hugging Face beam search as `generation_config.json` sets it (3 beams, early stopping,
        length penalty 1, no repeated 3-grams, forced BOS then EOS). Returns the best finished
        token ids (decoder start included) and its length-normalised log-probability."""
        lm, beams = "language_model", self.beams
        heads = int(self.text["decoder_attention_heads"])
        dim = encoded.shape[-1]
        split = lambda t: t.reshape(1, -1, heads, dim // heads).transpose(0, 2, 1, 3)
        cross = []
        for layer in range(int(self.text["decoder_layers"])):
            name = f"{lm}.decoder.layers.{layer}.encoder_attn"
            k, v = split(self._lin(encoded, name + ".k_proj")), split(self._lin(encoded, name + ".v_proj"))
            cross.append((mx.repeat(k, beams, axis=0), mx.repeat(v, beams, axis=0)))
        max_length = 1 + max_new_tokens
        running = [[self.start] for _ in range(beams)]
        running_scores = np.array([0.0] + [NEG] * (beams - 1), np.float64)
        finished: list[tuple[float, list[int]]] = []
        cache: dict = {}
        cur_len = 1
        vocab = self.w[lm + ".shared.weight"].shape[0]
        keep = 2 * beams
        while cur_len < max_length:
            logits = self._decode_step(mx.array([seq[-1] for seq in running]), cur_len - 1, cache, cross)
            forced = (int(self.forced_bos) if self.forced_bos is not None and cur_len == 1
                      else self.eos if cur_len == max_length - 1 else None)
            if forced is not None:
                log_probs = mx.where(mx.arange(vocab) == forced, 0.0, -mx.inf)[None].astype(mx.float32)
                log_probs = mx.broadcast_to(log_probs, logits.shape)
            else:
                log_probs = logits - mx.logsumexp(logits, axis=-1, keepdims=True)
                if self.no_repeat and cur_len + 1 >= self.no_repeat:
                    rows, cols = [], []
                    for beam, seq in enumerate(running):
                        banned = self._banned(seq)
                        rows += [beam] * len(banned)
                        cols += banned
                    if rows:
                        log_probs[mx.array(rows), mx.array(cols)] = -mx.inf
            total = (log_probs + mx.array(running_scores, mx.float32)[:, None]).reshape(-1)
            top = mx.argpartition(-total, keep - 1)[:keep]
            values = total[top]
            mx.eval(top, values)
            top, values = np.array(top), np.array(values, np.float64)
            order = np.argsort(-values, kind="stable")
            candidates = [(float(values[i]), int(top[i] // vocab), int(top[i] % vocab)) for i in order]
            stops = [token == self.eos or cur_len + 1 >= max_length for _, _, token in candidates]
            full = len(finished) >= beams
            for rank, ((score, beam, token), stop) in enumerate(zip(candidates, stops)):
                if stop and rank < beams and not full:
                    finished.append((score / cur_len, running[beam] + [token]))
            finished = sorted(finished, key=lambda item: -item[0])[:beams]
            alive = sorted(((score + (NEG if stop else 0.0), beam, token)
                            for (score, beam, token), stop in zip(candidates, stops)),
                           key=lambda item: -item[0])[:beams]
            if all(stops) or len(finished) >= beams:
                break
            order = mx.array([beam for _, beam, _ in alive])
            for buffers in cache.values():
                buffers[0], buffers[1] = mx.take(buffers[0], order, axis=0), mx.take(buffers[1], order, axis=0)
            running = [running[beam] + [token] for _, beam, token in alive]
            running_scores = np.array([score for score, _, _ in alive], np.float64)
            cur_len += 1
        best = finished[0] if finished else (float(running_scores[0]) / cur_len, running[0])
        return best[1], best[0]

    def _banned(self, seq: list[int]) -> list[int]:
        n = self.no_repeat
        array = np.asarray(seq)
        if len(array) < n:
            return []
        tail = array[len(array) - n + 1:]
        hits = np.ones(len(array) - n + 1, bool)
        for offset in range(n - 1):
            hits &= array[offset:len(array) - n + 1 + offset] == tail[offset]
        return array[n - 1:][hits].tolist()

    # ---- text ------------------------------------------------------------------------------
    def text_of(self, ids: list[int]) -> str:
        """Decoded the way the publisher's post-processor reads it: added tokens verbatim."""
        out, run = [], []
        for token in ids:
            if token in self.literal:
                if run:
                    out.append(self.tokenizer.decode(run, skip_special_tokens=False))
                    run = []
                out.append(self.literal[token])
            else:
                run.append(token)
        if run:
            out.append(self.tokenizer.decode(run, skip_special_tokens=False))
        return "".join(out).replace("<s>", "").replace("</s>", "").replace("<pad>", "")

    def run(self, features: mx.array, task: str, text: str = "", max_new_tokens: int = 1024) -> dict:
        prompt = PROMPTS[task].format(input=text.strip())
        ids, score = self.generate(self.encode(features, prompt), max_new_tokens)
        decoded = self.text_of(ids)
        parsed = (_polygons(decoded) if task == "<REFERRING_EXPRESSION_SEGMENTATION>"
                  else [] if task == "<DETAILED_CAPTION>"
                  else _boxes(decoded, self.banned_phrases if "GROUNDING" in task else set()))
        return {"text": decoded, "score": round(math.exp(score), 4), "tokens": len(ids) - 1,
                "instances": parsed}


def _unit(bins: list[int]) -> list[float]:
    return [(b + 0.5) / BINS for b in bins]


def _boxes(text: str, banned: set) -> list[dict]:
    """`description_with_bboxes` / `phrase_grounding`: [{label, box: [x0, y0, x1, y1] in 0..1}]."""
    out = []
    for phrase_text in re.findall(r"([^<]+(?:<loc_\d+>){4,})", text):
        phrase_text = phrase_text.replace("<ground>", "", 1).replace("<obj>", "", 1)
        match = re.search(_TEXT, phrase_text)
        if not phrase_text or not match:
            continue
        phrase = match.group().strip()
        if phrase in banned:
            continue
        phrase = phrase.encode("ascii", "ignore").decode("ascii")
        for found in re.finditer(_BOX, phrase_text):
            out.append({"label": phrase, "box": _unit([int(found.group(j)) for j in range(1, 5)])})
    return out


def _polygons(text: str) -> list[dict]:
    """`polygons` (empty phrase allowed): [{label, polygons: [[x, y, x, y, ...] in 0..1]}]."""
    out = []
    for phrase_text in re.findall(r"(?:(?:<loc_\d+>|<sep>|<poly>|</poly>){4,})", text):
        stripped = re.sub(r"^<loc_\d+>", "", phrase_text, count=1)
        match = re.search(r"^\s*(.*?)(?=<od>|</od>|<box>|</box>|<bbox>|</bbox>|<loc_|<poly>)", stripped)
        if not match:
            continue
        if "<poly>" in phrase_text and "</poly>" in phrase_text:
            pieces = [m.group(1) for m in re.finditer(r"<poly>(.*?)</poly>", phrase_text)]
        else:
            pieces = [phrase_text]
        for piece in pieces:
            polygons = []
            for found in re.finditer(r"((?:<loc_\d+>)+)(?:<sep>|$)", piece):
                bins = [int(m.group(1)) for m in re.finditer(r"<loc_(\d+)>", found.group(1))]
                polygons.append(_unit(bins[:len(bins) - len(bins) % 2]))
            if polygons:
                out.append({"label": match.group().strip(), "polygons": polygons})
    return out
