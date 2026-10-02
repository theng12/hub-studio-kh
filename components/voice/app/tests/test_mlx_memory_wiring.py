"""The model loader must apply the registered bounds (it runs in the child, so assert on the source)."""
from pathlib import Path

SOURCE = (Path(__file__).resolve().parents[1] / "backend" / "generation.py").read_text(encoding="utf-8")


def test_loader_bounds_the_cache_before_loading_and_the_model_after() -> None:
    body = SOURCE[SOURCE.index("def _mlx_audio_get_model"):SOURCE.index("def _generate_mlx_audio")]
    assert body.index("mlx_memory.bound_cache()") < body.index("load_model(snapshot_path)")
    assert body.index("load_model(snapshot_path)") < body.index("mlx_memory.bound_model(entry.family, model)")
