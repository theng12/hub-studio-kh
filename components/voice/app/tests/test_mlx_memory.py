"""The bounded-memory codec passes must give the same audio as the originals (and fail soft)."""
from __future__ import annotations

import numpy as np
import pytest

mx = pytest.importorskip("mlx.core")
pytest.importorskip("mlx_audio")

from backend import mlx_memory  # noqa: E402


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
