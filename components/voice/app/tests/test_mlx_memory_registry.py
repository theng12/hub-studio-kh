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
            "measured free memory needed before a job starts (not its peak), never a formula: say on which Mac, build and date."
        )
        assert "measure" in note.lower(), f"{row.repo}: the note must say what was measured"
        assert 1.0 <= row.min_free_memory_gb <= 64.0, f"{row.repo}: implausible floor {row.min_free_memory_gb}"


def test_every_mlx_audio_catalog_family_is_in_the_worker_registry() -> None:
    families = {row.family for row in catalog.CATALOG if catalog.FAMILIES[row.family].engine == catalog.ENGINE_MLX_AUDIO}
    assert families <= set(mlx_memory.PROFILES), sorted(families - set(mlx_memory.PROFILES))


def test_a_published_floor_is_the_only_floor_this_worker_enforces() -> None:
    """One model, one admission floor: the row's own `min_free_memory_gb`.

    The Hub reads the strictest of `min_free_memory_gb` and the `required_free_memory_gb` this
    worker publishes (`workload_policy.required_free_memory_gb`), so a floor computed beside a
    measured one is a second, stricter gate — and it is the one that wins. VoxCPM2 4-bit was 2.8
    (its row, owner's choice 2026-10-02 from 8 GB runs that started cleanly at 3.25 GB free)
    against 3.55 (size 2.3 + 1.25), and the 3.55 refused 8 GB Macs the measurement qualifies:
    live 2026-10-19 as "needs about 3.5 GB free and has about 3.4 GB free" on
    mac-0107-m2-8-256-hub, job 97bdce60, 3,068 INSUFFICIENT_MEMORY hand-backs.
    """
    for row in catalog.CATALOG:
        if row.min_free_memory_gb is None:
            continue
        published = round(float(row.min_free_memory_gb), 2)
        assert generation._required_free_memory_gb(row, loaded=False) == published, (
            f"{row.repo} publishes a {published} GB free-before-start floor and the worker "
            f"enforces {generation._required_free_memory_gb(row, loaded=False)}. A floor must "
            "never be a formula (AGENTS.md, MLX memory discipline)."
        )
        assert generation._required_free_memory_gb(row, loaded=True) == published, row.repo


def test_a_row_with_no_floor_keeps_the_size_estimate() -> None:
    """The estimate survives only where nobody has measured a floor yet."""
    measured = 0
    for row in catalog.CATALOG:
        if row.min_free_memory_gb is not None:
            measured += 1
            continue
        model_gb = max(0.5, float(row.size_gb or 0.5))
        assert generation._required_free_memory_gb(row, loaded=False) == round(
            model_gb + generation.MEMORY_HEADROOM_GB, 2), row.repo
        assert generation._required_free_memory_gb(row, loaded=True) == round(
            max(1.5, model_gb * 0.45) + generation.MEMORY_HEADROOM_GB, 2), row.repo
    assert measured, "every row publishing no floor would make this test vacuous"
