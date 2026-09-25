"""The catalog registry's own invariants — the ones that catch the next family.

Every test here is stated over the registry, not over a list of names. The
2026-09-24 regression (`kittentts`, `vibevoice`, `omnivoice` published as
"PyTorch / MPS" and hidden by the MLX-only filter) and the live `KeyError` on a
mistyped family id both came from a second list in a consumer; these tests fail
by name when a family is added, renamed or removed without the rest of the
registry learning about it.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from backend import catalog, generation


def _synthetic_family(family_id: str, **overrides):
    """A family entry of the shape `_validate_registry` accepts."""
    values = {
        "id": family_id,
        "label": "Synthetic (MLX)",
        "engine": catalog.ENGINE_MLX_AUDIO,
        "requirements": ("mlx", "mlx_audio", "soundfile", "numpy"),
        "summary": "synthetic",
        "how_to_use": "synthetic",
        "text_guidance": catalog.TextGuidance(
            soft_max_chars=None, chunking="auto-split", note="synthetic"
        ),
        "section_max_characters": 280,
    }
    values.update(overrides)
    return catalog.Family(**values)


def test_every_catalog_row_names_a_registered_family() -> None:
    """A typo'd family id must not wait for the first `/api/catalog` request.

    `FAMILIES[m.family]` was an unchecked index: a row naming a family that was
    renamed raised a bare `KeyError` while the endpoint serialized the catalog,
    and because the endpoint builds every row in one loop the whole model list
    disappeared rather than one entry.
    """
    for model in catalog.CATALOG:
        assert model.family in catalog.FAMILIES, model.repo
        assert catalog.family_for(model) is catalog.FAMILIES[model.family]


def test_every_registered_family_is_used_by_a_model() -> None:
    """A family with no model is a picker section that can never fill."""
    used = {model.family for model in catalog.CATALOG}
    unused = sorted(set(catalog.FAMILIES) - used)
    assert not unused, f"FAMILIES entries with no CATALOG row: {unused}"


def test_registry_keys_engines_and_engines_have_workers() -> None:
    for family_id, family in catalog.FAMILIES.items():
        assert family_id == family.id
        assert family.engine in catalog.ALL_ENGINES, family_id
        assert generation.engine_for_family(family_id) == family.engine
        # Every engine has a worker and a runtime probe, which generation
        # asserts at import; this is the same check from the test side.
        assert family.engine in generation._WORKER_BY_ENGINE, family_id
        assert family.engine in generation._RUNTIME_PROBE_BY_ENGINE, family_id


def test_a_bad_family_id_fails_loudly_and_names_the_file(monkeypatch) -> None:
    model = catalog.ModelEntry(
        repo="example/new-model",
        label="New model",
        family="not-a-registered-family",
        size_gb=1.0,
        gated=False,
    )
    with pytest.raises(catalog.RegistryError) as error:
        catalog.family_for(model)
    message = str(error.value)
    assert "example/new-model" in message
    assert "not-a-registered-family" in message
    assert "catalog.py" in message

    monkeypatch.setattr(catalog, "CATALOG", (*catalog.CATALOG, model))
    with pytest.raises(catalog.RegistryError, match="not-a-registered-family"):
        catalog._validate_registry()

    # And `engine_of` in generation refuses an unregistered family by name too,
    # rather than answering with a default.
    with pytest.raises(ValueError, match="not-a-registered-family"):
        generation.engine_for_family("not-a-registered-family")


def test_a_new_mlx_family_is_labelled_mlx_and_survives_the_mlx_only_filter(monkeypatch) -> None:
    """The A6 invariant: the label and the filter read the family's own engine.

    This is the test that catches the *next* family. `serialize_model` used to
    keep its own tuple of MLX family ids beside generation's table, and when the
    two drifted the family was published as "PyTorch / MPS" and hidden by the
    "🍎 MLX only" filter (`components/voice/CHATANGELOG.md:3646` records that
    shipping once). A synthetic family declared `engine=mlx-audio` is now
    labelled and filtered correctly without any other edit.
    """
    family_id = "synthetic-mlx-family"
    monkeypatch.setitem(catalog.FAMILIES, family_id, _synthetic_family(family_id))
    model = catalog.ModelEntry(
        repo=f"example/{family_id}-4bit",
        label="Synthetic MLX 4-bit",
        family=family_id,
        size_gb=1.0,
        gated=False,
    )
    monkeypatch.setattr(catalog, "CATALOG", (*catalog.CATALOG, model))

    published = catalog.serialize_model(model)
    assert published["apple_optimized"] is True, (
        "an MLX family published as non-Apple-optimized is hidden by the MLX-only filter"
    )
    # The frontend's filter is exactly this flag.
    frontend = Path(__file__).resolve().parents[1] / "frontend" / "app.js"
    assert "f.mlxOnly && !m.apple_optimized" in frontend.read_text(encoding="utf-8")

    # A non-MLX family is not labelled MLX, and the label is the engine fact.
    khmer = catalog.serialize_model(
        next(model for model in catalog.CATALOG if model.family == "khmer-tts-onnx")
    )
    assert khmer["apple_optimized"] is False
    assert catalog.FAMILIES["khmer-tts-onnx"].engine == catalog.ENGINE_ONNX_VITS


def test_every_catalog_row_publishes_the_modes_it_offers() -> None:
    """A row a consumer cannot file into a tab is a model nobody can pick.

    The three names are the contract (`cloning`, `preset`, `design`), and they are the
    native Voice page's own tab vocabulary. Publishing nothing, publishing a name outside
    that set, or publishing the same name twice would each leave a consumer guessing -
    which is the shape this field exists to remove.
    """
    assert catalog.VOICE_MODES == ("cloning", "preset", "design")
    for model in catalog.CATALOG:
        modes = catalog.serialize_model(model)["modes"]
        assert modes, f"{model.repo} publishes no modes, so no picker could offer it"
        assert len(set(modes)) == len(modes), (model.repo, modes)
        assert set(modes) <= set(catalog.VOICE_MODES), (model.repo, modes)


def test_published_modes_agree_with_the_rows_capabilities() -> None:
    """Cloning is a capability fact, in both directions.

    A cloning model must offer cloning; a model that does not declare `voice-cloning`
    must not, because a consumer that offered it would send a reference the lane then
    refuses. The dispatch contract's reference parameter is the same fact stated for a
    scheduler, so it is cross-checked here too.
    """
    for model in catalog.CATALOG:
        row = catalog.serialize_model(model)
        declared = "voice-cloning" in row["capabilities"]
        assert ("cloning" in row["modes"]) is declared, (model.repo, row["modes"])
        offered = {parameter["name"] for parameter in row["lane_contract"]["parameters"]}
        assert ("voice_library_id" in offered) is declared, (model.repo, row["modes"])


def test_a_preset_only_family_is_never_offered_as_a_cloning_model() -> None:
    """The Khmer VITS voices have one built-in speaker and read no reference at all."""
    for repo in ("khmerttsopensource/khmer-tts", "sengtha/khmer-tts-female-v2"):
        row = catalog.serialize_model(catalog.get_model(repo))
        assert row["modes"] == ["preset"], (repo, row["modes"])
        assert "voice-cloning" not in row["capabilities"]


def test_a_model_that_both_clones_and_carries_presets_publishes_both() -> None:
    """Pocket TTS 4-bit (MLX 4-bit)'s own generate() takes a preset name or a reference.

    The native page could reach that model in one tab only, which is the bug the `modes`
    field exists to let it fix. Publishing both is the producer's half; offering it in
    both is the consumer's.
    """
    row = catalog.serialize_model(catalog.get_model("mlx-community/pocket-tts-4bit"))
    assert set(row["modes"]) == {"cloning", "preset"}


def test_design_is_offered_exactly_where_the_family_declares_the_input() -> None:
    """`design` is not a guess about quality: it is the family's own request field."""
    for model in catalog.CATALOG:
        family = catalog.family_for(model)
        row = catalog.serialize_model(model)
        assert ("design" in row["modes"]) is (
            "voice_design_prompt" in family.lane_parameters
        ), (model.repo, row["modes"])


def test_a_new_family_publishes_its_modes_without_a_consumer_edit(monkeypatch) -> None:
    """The invariant, not today's membership: a synthetic family lands in its own tabs.

    A family declaring a voice-design input is offered as design (and as cloning, when its
    model declares cloning) with no edit to any consumer; a family declaring cloning,
    design or a named preset input nowhere is the ready-made form, because its own
    built-in voice is the only voice it has.
    """
    family_id = "synthetic-voice-family"
    monkeypatch.setitem(
        catalog.FAMILIES,
        family_id,
        _synthetic_family(family_id, lane_parameters=("voice_design_prompt",)),
    )
    designing = catalog.ModelEntry(
        repo=f"example/{family_id}-4bit",
        label="Synthetic voice 4-bit",
        family=family_id,
        size_gb=1.0,
        gated=False,
        capabilities=("tts", "voice-cloning"),
    )
    assert catalog.voice_modes(designing) == ["cloning", "design"]

    monkeypatch.setitem(catalog.FAMILIES, family_id, _synthetic_family(family_id))
    speaking = catalog.ModelEntry(
        repo=f"example/{family_id}-fixed",
        label="Synthetic fixed voice",
        family=family_id,
        size_gb=1.0,
        gated=False,
        capabilities=("tts",),
    )
    assert catalog.voice_modes(speaking) == ["preset"], (
        "a family with no cloning, no design and no preset input speaks with its own "
        "built-in voice, which is the ready-made form"
    )


def test_the_wiring_table_and_the_registry_cannot_disagree(monkeypatch) -> None:
    """A new mlx-audio family without a config row fails at import, by name."""
    family_id = "synthetic-mlx-family"
    monkeypatch.setitem(catalog.FAMILIES, family_id, _synthetic_family(family_id))
    with pytest.raises(ValueError) as error:
        generation._validate_family_wiring()
    message = str(error.value)
    assert family_id in message
    assert "MLX_AUDIO_FAMILIES" in message
    assert "catalog.py" in message and "generation.py" in message

    # A stale config row for a family the catalog does not run on mlx-audio is
    # the same failure in the other direction.
    monkeypatch.setitem(generation.MLX_AUDIO_FAMILIES, "khmer-tts-onnx", {"mode": "qwen3"})
    with pytest.raises(ValueError, match="khmer-tts-onnx"):
        generation._validate_family_wiring()


def test_no_user_visible_catalog_string_gives_licensing_advice() -> None:
    """AGENTS.md, 'Model use scope': the question is settled and out of scope.

    A license is recorded once as a name on the model row; it is never advice,
    never a condition, and never repeated in `best_for` / `use_cases` /
    `how_to_use` / `summary` / `recommended_hardware`. This is the mechanical
    check that keeps it from coming back.
    """
    banned = ("commercial", "non-commercial", "noncommercial", "research license",
              "research licence", "commercial grant", "commercial license",
              "commercial licence")
    offenders: list[str] = []
    for model in catalog.CATALOG:
        published = catalog.serialize_model(model)
        for field, value in (
            ("best_for", published["best_for"]),
            ("recommended_hardware", published["recommended_hardware"]),
            ("label", published["label"]),
        ):
            low = str(value).lower()
            if any(word in low for word in banned):
                offenders.append(f"{model.repo}.{field}")
        for kind, text in published["use_cases"]:
            low = text.lower()
            if any(word in low for word in banned):
                offenders.append(f"{model.repo}.use_cases[{kind}]")
    for family_id, family in catalog.FAMILIES.items():
        low = f"{family.summary} {family.how_to_use} {family.section_note}".lower()
        if any(word in low for word in banned):
            offenders.append(f"FAMILIES[{family_id}]")
    assert not offenders, f"licensing advice in user-visible catalog copy: {offenders}"

    # The one recorded fact is a name and its source, and an unestablished
    # license is empty rather than invented.
    for model in catalog.CATALOG:
        published = catalog.serialize_model(model)["license"]
        assert set(published) == {"name", "source"}
        if published["name"]:
            assert published["source"], model.repo
        else:
            assert published["source"] == "", model.repo
