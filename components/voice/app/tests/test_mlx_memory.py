"""The bounded-memory codec passes must give the same audio as the originals (and fail soft)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

mx = pytest.importorskip("mlx.core")
pytest.importorskip("mlx_audio")

from backend import mlx_memory  # noqa: E402

# These tests run real MLX/Metal kernels. In the shared pytest process that leaves a Metal device that
# intermittently aborts the interpreter at exit ("recursive_mutex lock failed", exit -6) after every test
# has passed, which fails the product-suite gate. So the parent only runs them in a child process and
# judges the child by its test summary, not by its exit status.
_CHILD = os.environ.get("MLX_MEMORY_TESTS_CHILD") == "1"


def test_the_mlx_bounds_hold_in_a_child_process() -> None:
    if _CHILD:
        pytest.skip("this is the child run")
    child = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(Path(__file__)), "-k", "not child_process"],
        env={**os.environ, "MLX_MEMORY_TESTS_CHILD": "1"}, capture_output=True, text=True, timeout=240, check=False,
    )
    output = child.stdout + child.stderr
    summary = [line for line in output.splitlines() if " passed" in line or " failed" in line or " error" in line]
    assert summary and " failed" not in summary[-1] and " error" not in summary[-1] and " passed" in summary[-1], output[-3000:]



def _same(a, b, tolerance: float = 1e-5) -> None:
    a, b = np.array(a), np.array(b)
    assert a.shape == b.shape
    assert float(np.abs(a - b).max()) < tolerance


def test_bound_cache_sets_the_limit_and_honours_the_override(monkeypatch) -> None:
    previous = mx.set_cache_limit(mx.get_cache_memory() + 10**9)
    try:
        monkeypatch.delenv("STUDIOHUB_MLX_CACHE_LIMIT_MB", raising=False)
        mlx_memory.bound_cache()
        assert mx.set_cache_limit(previous) == mlx_memory.CACHE_LIMIT_MB * 1_000_000
        monkeypatch.setenv("STUDIOHUB_MLX_CACHE_LIMIT_MB", "64")
        mlx_memory.bound_cache()
        assert mx.set_cache_limit(previous) == 64_000_000
        monkeypatch.setenv("STUDIOHUB_MLX_CACHE_LIMIT_MB", "-1")   # keep MLX's own default
        mlx_memory.bound_cache()
        assert mx.set_cache_limit(previous) == previous
    finally:
        mx.set_cache_limit(previous)


def test_qwen3_slabbed_decode_matches_the_whole_chunk_decode(monkeypatch) -> None:
    from mlx_audio.tts.models.qwen3_tts.config import Qwen3TTSTokenizerDecoderConfig
    from mlx_audio.tts.models.qwen3_tts.speech_tokenizer import Qwen3TTSSpeechTokenizerDecoder as Decoder

    config = Qwen3TTSTokenizerDecoderConfig(
        latent_dim=16, codebook_dim=8, codebook_size=16, decoder_dim=16, hidden_size=16,
        intermediate_size=16, head_dim=8, num_attention_heads=2, num_key_value_heads=2,
        num_hidden_layers=1, num_quantizers=4, semantic_codebook_size=16,
        vector_quantization_hidden_dimension=8,
    )
    decoder = Decoder(config)
    mx.eval(decoder.parameters())
    codes = mx.array(np.random.RandomState(1).randint(0, 16, size=(1, 4, 61)))
    expected = decoder.chunked_decode(codes, chunk_size=40, left_context_size=25)
    mx.eval(expected)

    monkeypatch.setattr(Decoder, "chunked_decode", Decoder.chunked_decode)   # restored after the test
    monkeypatch.delattr(Decoder, "_studiohub_bounded", raising=False)
    monkeypatch.setattr(mlx_memory, "QWEN3_DECODE_SLAB", 7)
    assert mlx_memory.bound_qwen3_decoder() is True
    assert mlx_memory.bound_qwen3_decoder() is True            # idempotent
    _same(decoder.chunked_decode(codes, chunk_size=40, left_context_size=25), expected)


def _tiny_vae():
    from mlx_audio.tts.models.voxcpm2.audio_vae import AudioVAE
    from mlx_audio.tts.models.voxcpm2.config import AudioVAEConfig

    vae = AudioVAE(AudioVAEConfig(
        encoder_dim=8, encoder_rates=[2, 2], latent_dim=4, decoder_dim=16, decoder_rates=[2, 2],
        depthwise=False, cond_dim=8,
    ))
    mx.eval(vae.parameters())
    return vae


def test_voxcpm_slabbed_vae_decode_and_staged_encode_match_the_originals(monkeypatch) -> None:
    vae = _tiny_vae()
    z = mx.array(np.random.RandomState(2).randn(1, 50, 4).astype("float32"))
    expected = vae.decode(z)
    audio = mx.array(np.random.RandomState(3).randn(1, 640, 1).astype("float32"))
    expected_latent = vae.encoder(audio)
    mx.eval(expected, expected_latent)

    monkeypatch.setattr(mlx_memory, "VOXCPM_DECODE_SLAB", 9)
    monkeypatch.setattr(mlx_memory, "_VOXCPM_CONTEXT", 48)   # this toy decoder is slow-rate, so it needs more context
    _same(mlx_memory._voxcpm_decode(vae, z), expected)
    _same(mlx_memory._StagedVAEEncoder(vae.encoder)(audio), expected_latent)


def test_bound_helpers_refuse_models_they_do_not_recognise() -> None:
    assert mlx_memory.bound_qwen3_encoder(object()) is False
    assert mlx_memory.bound_voxcpm_vae(object()) is False


def test_voxcpm_staged_local_encoder_matches_the_original() -> None:
    from mlx_audio.tts.models.voxcpm2.config import LMConfig
    from mlx_audio.tts.models.voxcpm2.encoder import VoxCPMLocEnc

    config = LMConfig(
        hidden_size=32, num_hidden_layers=3, num_attention_heads=4, num_key_value_heads=2,
        intermediate_size=64, vocab_size=0, kv_channels=8, rope_short_factor=[1.0] * 4,
        rope_long_factor=[1.0] * 4, use_mup=False,
    )
    encoder = VoxCPMLocEnc(config, input_dim=8)
    mx.eval(encoder.parameters())
    x = mx.array(np.random.RandomState(4).randn(1, 11, 4, 8).astype("float32"))
    expected = encoder(x)
    mx.eval(expected)
    _same(mlx_memory._StagedLocalEncoder(encoder)(x), expected)


def test_staged_encoder_wrapper_forwards_attributes() -> None:
    class Inner:
        layers = [1, 2]

    assert mlx_memory._StagedLocalEncoder(Inner()).layers == [1, 2]
    assert mlx_memory._StagedEncoder(Inner()).layers == [1, 2]


def test_omnivoice_slabbed_codec_decode_matches_the_original(monkeypatch) -> None:
    from mlx_audio.codec.models.higgs_audio.config import HiggsAudioConfig
    from mlx_audio.codec.models.higgs_audio.higgs_audio import HiggsAudioTokenizer

    tokenizer = HiggsAudioTokenizer(HiggsAudioConfig())
    mx.eval(tokenizer.parameters())
    tokens = mx.array(np.random.RandomState(5).randint(0, 1024, size=(70, 8)).astype("int32"))
    expected = tokenizer.decode(tokens)
    mx.eval(expected)

    monkeypatch.setattr(mlx_memory, "OMNIVOICE_DECODE_SLAB", 16)
    _same(mlx_memory._omnivoice_decode(tokenizer, tokens), expected)
    monkeypatch.setattr(tokenizer, "decode", tokenizer.decode)
    model = type("Model", (), {"audio_tokenizer": tokenizer})()
    assert mlx_memory.bound_omnivoice(model) is True
    _same(tokenizer.decode(tokens), expected)


def _tiny_fish_dac():
    """The real Fish S1 DAC classes, shrunk (the shipped codec is 1.87 GB of fp32)."""
    from mlx_audio.codec.models.fish_s1_dac import fish_s1_dac as f

    def transformer(window):
        return f.WindowLimitedTransformer(causal=True, window_size=window, input_dim=16, config=f.ModelArgs(
            block_size=512, n_layer=2, n_head=2, dim=16, intermediate_size=32, head_dim=8, channels_first=True))

    quantizer = f.DownsampleResidualVectorQuantize(
        input_dim=16, n_codebooks=3, codebook_size=32, codebook_dim=4, semantic_codebook_size=64,
        downsample_factor=(2, 2), pre_module=transformer(8), post_module=transformer(8))
    dac = f.DAC(encoder_dim=4, encoder_rates=[4, 2], latent_dim=16, decoder_dim=32, decoder_rates=[4, 2],
                quantizer=quantizer, causal=True, encoder_transformer_layers=[0, 0], decoder_transformer_layers=[0, 0])
    mx.eval(dac.parameters())
    return dac


def test_fish_slabbed_codec_decode_matches_the_original(monkeypatch) -> None:
    dac = _tiny_fish_dac()
    indices = mx.array(np.random.RandomState(8).randint(0, 64, size=(1, 4, 150)).astype("int32"))
    lengths = mx.array([150], dtype=mx.int32)
    expected, expected_lengths = dac.decode(indices, lengths)
    mx.eval(expected)

    monkeypatch.setattr(mlx_memory, "FISH_DECODE_SLAB", 16)
    audio, audio_lengths = mlx_memory._fish_decode(dac, indices, lengths)
    _same(audio, expected)
    assert int(audio_lengths[0]) == int(expected_lengths[0]) == audio.shape[-1]
    # The context is load-bearing: one frame of it is not enough to reproduce the samples.
    monkeypatch.setattr(mlx_memory, "_FISH_CONTEXT", 1)
    short, _ = mlx_memory._fish_decode(dac, indices, lengths)
    assert float(np.abs(np.array(short) - np.array(expected)).max()) > 1e-4
    monkeypatch.undo()

    model = type("Model", (), {"codec": dac})()
    assert mlx_memory.bound_fish(model) is True
    _same(dac.decode(indices, lengths)[0], expected)
    audio_in = mx.array(np.random.RandomState(9).randn(1, 1, 4 * dac.frame_length).astype("float32"))
    first = dac.encode(audio_in)
    assert dac.encode(audio_in) is first    # the reference is encoded once per clip, not once per section


def test_breeze_staged_backbone_matches_the_original_for_prefill_and_steps() -> None:
    pytest.importorskip("mlx_audio.tts.models.breeze_tts")   # Breeze needs mlx-audio >= 0.5.7
    from mlx_audio.tts.models.breeze_tts.breeze_tts import _Backbone
    from mlx_audio.tts.models.breeze_tts.config import ModelConfig

    backbone_config = dict(hidden_size=32, num_hidden_layers=2, intermediate_size=64, num_attention_heads=4,
                           num_key_value_heads=2, head_dim=8, vocab_size=40)
    config = ModelConfig.from_dict(dict(
        model_type="breeze_tts", audio_num_codebooks=4, audio_vocab_size=40, audio_embed_size=32, hidden_size=32,
        num_hidden_layers=2, intermediate_size=64, num_attention_heads=4, num_key_value_heads=2, head_dim=8,
        vocab_size=40, text_vocab_size=50, max_position_embeddings=128, rope_theta=10000.0, num_codebooks=4,
        backbone_config=backbone_config))
    backbone = _Backbone(config)
    mx.eval(backbone.parameters())
    staged = mlx_memory._StagedBackbone(backbone)
    prompt = mx.array(np.random.RandomState(6).randn(1, 9, 32).astype("float32"))
    step = mx.array(np.random.RandomState(7).randn(1, 1, 32).astype("float32"))
    plain_cache, staged_cache = backbone.make_cache(), backbone.make_cache()
    _same(staged(input_embeddings=prompt, cache=staged_cache), backbone(input_embeddings=prompt, cache=plain_cache), 1e-6)
    _same(staged(input_embeddings=step, cache=staged_cache), backbone(input_embeddings=step, cache=plain_cache), 1e-6)


# Everything except the wrapper only runs in the child.
_only_in_child = pytest.mark.skipif(not _CHILD, reason="runs in the child process of test_the_mlx_bounds_hold_in_a_child_process")
for _name, _test in list(globals().items()):
    if _name.startswith("test_") and _name != "test_the_mlx_bounds_hold_in_a_child_process":
        globals()[_name] = _only_in_child(_test)
