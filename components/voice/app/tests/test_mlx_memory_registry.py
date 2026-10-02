"""A model cannot be added without its memory discipline (docs/mlx-memory-discipline.md).

Needs no MLX: it reads the registries only.
"""
from __future__ import annotations

from backend import catalog, generation, mlx_memory


def test_every_mlx_engine_family_has_a_registered_memory_profile() -> None:
    missing = sorted(set(generation.MLX_AUDIO_FAMILIES) - set(mlx_memory.PROFILES))
    assert not missing, (
        f"MLX voice families {missing} have no entry in mlx_memory.PROFILES. Bound their codec decode "
        "and reference encode, or register them 'cache-only' with the reason (docs/mlx-memory-discipline.md)."
    )
    stale = sorted(set(mlx_memory.PROFILES) - set(generation.MLX_AUDIO_FAMILIES))
    assert not stale, f"mlx_memory.PROFILES lists families the worker no longer has: {stale}"


def test_each_profile_states_how_and_a_bounded_one_has_its_bounder() -> None:
    for family, (kind, how, bounder) in mlx_memory.PROFILES.items():
        assert kind in {"bounded", "cache-only"}, f"{family}: unknown profile kind {kind!r}"
        assert len(how.strip()) >= 20, f"{family}: say how memory is bounded, or why cache-only is enough"
        assert "PLACEHOLDER" not in how, f"{family}: unfinished memory profile"
        assert (bounder is not None) == (kind == "bounded"), f"{family}: 'bounded' needs a bounder, 'cache-only' has none"


def test_every_catalog_floor_names_the_measurement_it_came_from() -> None:
    for row in catalog.CATALOG:
        if row.min_free_memory_gb is None:
            continue
        note = row.min_free_memory_note.strip()
        assert note, (
            f"{row.repo}: min_free_memory_gb={row.min_free_memory_gb} has no min_free_memory_note. A floor is a "
            "measured peak footprint plus margin, never a formula: say on which Mac, build and date."
        )
        assert "measure" in note.lower(), f"{row.repo}: the note must say what was measured"
        assert 1.0 <= row.min_free_memory_gb <= 64.0, f"{row.repo}: implausible floor {row.min_free_memory_gb}"


def test_every_mlx_audio_catalog_family_is_in_the_worker_registry() -> None:
    families = {row.family for row in catalog.CATALOG if catalog.FAMILIES[row.family].engine == catalog.ENGINE_MLX_AUDIO}
    assert families <= set(mlx_memory.PROFILES), sorted(families - set(mlx_memory.PROFILES))
