"""The model loader must apply every bound in mlx_memory (it runs in the child, so assert on the source)."""
from pathlib import Path

SOURCE = (Path(__file__).resolve().parents[1] / "backend" / "generation.py").read_text(encoding="utf-8")


def test_loader_bounds_the_cache_before_loading_and_each_codec_after() -> None:
    body = SOURCE[SOURCE.index("def _mlx_audio_get_model"):SOURCE.index("def _generate_mlx_audio")]
    assert body.index("mlx_memory.bound_cache()") < body.index("load_model(snapshot_path)")
    for family, helper in (("qwen3-tts", "bound_qwen3_decoder"), ("qwen3-tts", "bound_qwen3_encoder"),
                           ("voxcpm-mlx", "bound_voxcpm_vae"), ("omnivoice", "bound_omnivoice")):
        assert f'entry.family == "{family}"' in body
        assert f"mlx_memory.{helper}(" in body
        assert body.index("load_model(snapshot_path)") < body.index(f"mlx_memory.{helper}(")
