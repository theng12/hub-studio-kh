"""Per-model operator defaults in the Voice worker: stored, validated, applied, published.

The owner's rule (2026-09-25): *"it has to be per model because each model is weight
differently. we can't have a blanket default meant for one model but applied to all."* The Models
tab publishes each model's own lane parameters and lets the operator set their default for each
one; this module pins the worker half of that contract:

* the routes round-trip, and an unknown repo is refused by name rather than silently doing
  nothing;
* a value outside the model's **own published bound** is refused by name and not stored - the
  bound is read from the lane's parameters, so a bound change in the worker moves this test;
* a request that supplies a parameter is untouched by a stored default, and one that omits it gets
  the stored value;
* that value is the one ``GET /api/catalog`` publishes as the parameter's ``default``: both sides
  are read and compared, never asserted as two literals;
* a model that does not publish a parameter is not offered a default for it;
* and nothing that must not move did: the ``modes`` publication with its per-mode request fields,
  and the memory-policy contract.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend import catalog, generation, lane_contract, main, paths  # noqa: E402
from backend.main import FLEET_TOKEN  # noqa: E402
from components import model_defaults  # noqa: E402


# One model that publishes the section-size control the owner named ("tts has word chunks and i
# can't see/edit it"), and one that publishes no cloning-specific parameter at all.
SECTIONING = "mlx-community/VoxCPM2-4bit"
PRESET_ONLY = "khmerttsopensource/khmer-tts"
# The roster-publishing preset row the registered catalogue does not ship today: ST-13.B pruned
# the Qwen3-TTS CustomVoice tier and the owner left Pocket TTS 4-bit's roster unbuilt
# (docs/tasks/ST-20.md). Registering it in one test exercises the route's own resolution rather
# than a second copy of it, and the row publishes what its family states.
CUSTOM_VOICE = "mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit"


def _register_custom_voice(monkeypatch) -> catalog.ModelEntry:
    """The roster tier, registered for one test: a preset that is a real choice."""
    model = catalog.ModelEntry(
        repo=CUSTOM_VOICE,
        label="Qwen3-TTS 0.6B CustomVoice",
        family="qwen3-tts",
        size_gb=1.9,
        gated=False,
        capabilities=("tts", "multilingual", "expressive"),
    )
    monkeypatch.setattr(catalog, "CATALOG", (*catalog.CATALOG, model))
    return model


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """A private state directory: a test never reads or writes this Mac's own defaults."""
    monkeypatch.setenv("STUDIOHUB_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def client(data_dir):
    return TestClient(main.app, headers={"X-Studio-Token": FLEET_TOKEN})


@pytest.fixture
def queued(monkeypatch):
    """Every parameter dict the route hands to the generation manager."""
    captured: list[dict] = []

    def capture(params, **_kwargs):
        captured.append(params)
        return SimpleNamespace(serialize=lambda: {})

    monkeypatch.setattr(main.gen_manager, "is_available", lambda: True)
    monkeypatch.setattr(main.cache, "cache_state", lambda _repo: "cached")
    monkeypatch.setattr(main.gen_manager, "start_txt2speech", capture)
    return captured


def _row(client, repo: str) -> dict:
    return {row["repo"]: row for row in client.get("/api/model-defaults").json()["models"]}[repo]


def _parameter(model, name: str) -> dict:
    return next(p for p in lane_contract.parameters_for(model) if p["name"] == name)


def _model_publishing(name: str):
    """A catalog model whose lane publishes one parameter - the fixture finds it, never names it."""
    model = next((m for m in catalog.CATALOG
                  if name in _names(lane_contract.parameters_for(m))), None)
    assert model is not None, f"no catalog model publishes {name!r}"
    return model


def _inside(parameter: dict, current):
    """A value the lane's own published bound accepts, different from ``current``."""
    return parameter["minimum"] if current != parameter["minimum"] else parameter["maximum"]


def _published(client, repo: str, name: str):
    """What ``GET /api/catalog`` advertises as one parameter's ``default`` for one model."""
    row = next(m for m in client.get("/api/catalog").json()["models"] if m["repo"] == repo)
    return next(p for p in row["lane_contract"]["parameters"] if p["name"] == name)["default"]


def _names(parameters) -> list[str]:
    return [p["name"] for p in parameters]


class TestTheSurfaceIsTheLanesOwn:
    def test_get_before_anything_is_set_is_the_lanes_own_defaults(self, client):
        payload = client.get("/api/model-defaults").json()
        rows = {row["repo"]: row for row in payload["models"]}
        assert set(rows) == {model.repo for model in catalog.CATALOG}
        assert payload["settings_file"] == model_defaults.SETTINGS_NAME
        for model in catalog.CATALOG:
            published = lane_contract.parameters_for(model)
            row = rows[model.repo]
            assert _names(row["parameters"]) == _names(model_defaults.editable(published))
            assert row["defaults"] == row["stated"] == model_defaults.stated(published)
            assert row["chosen"] == {}
            assert row["not_used"] == {}

    def test_every_parameter_offered_is_one_that_model_publishes(self, client):
        """The surface is the model's own published list, per model, with nothing added."""
        rows = {row["repo"]: row for row in client.get("/api/model-defaults").json()["models"]}
        published = {model.repo: lane_contract.parameters_for(model) for model in catalog.CATALOG}
        # The fixture is only meaningful if the catalog really does differ per model.
        assert len({tuple(_names(names)) for names in published.values()}) > 1
        for repo, parameters in published.items():
            assert _names(rows[repo]["parameters"]) == _names(
                model_defaults.editable(parameters))
        for model in catalog.CATALOG:
            if "voice-cloning" not in model.capabilities:
                assert "voice_library_id" not in _names(rows[model.repo]["parameters"]), model.repo

    def test_a_preset_only_model_is_not_offered_a_cloning_default(self, client):
        """The brief's example: a model that cannot clone must not offer cloning's parameters."""
        model = catalog.get_model(PRESET_ONLY)
        assert catalog.voice_modes(model) == ["preset"]
        offered = _names(_row(client, model.repo)["parameters"])
        for cloning_only in ("voice_library_id", "ref_transcript", "voice_design_prompt",
                             "instruct", "cfg_value", "voice"):
            assert cloning_only not in offered, cloning_only
        refused = client.put("/api/model-defaults", json={
            "repo": model.repo, "values": {"voice_library_id": "some-voice"}})
        assert refused.status_code == 422, refused.text
        assert "publishes no such settable parameter" in (
            refused.json()["detail"]["voice_library_id"])
        assert model_defaults.load(paths.data_dir()) == {}

    def test_a_parameter_the_lane_withdraws_is_withdrawn_from_the_surface(self, client,
                                                                         monkeypatch):
        """Negative control: the surface follows the lane.

        A control the model no longer publishes leaves with its default, instead of staying on
        screen and storing a value nothing honours.
        """
        model = catalog.get_model(SECTIONING)
        assert "section_max_characters" in _names(lane_contract.parameters_for(model))
        monkeypatch.setattr(catalog, "section_size_control_for", lambda _repo: None)
        assert "section_max_characters" not in _names(lane_contract.parameters_for(model))
        row = _row(client, model.repo)
        assert "section_max_characters" not in _names(row["parameters"])
        assert "section_max_characters" not in row["defaults"]
        refused = client.put("/api/model-defaults", json={
            "repo": model.repo, "values": {"section_max_characters": 400}})
        assert refused.status_code == 422, refused.text
        assert "publishes no such settable parameter" in (
            refused.json()["detail"]["section_max_characters"])

    def test_a_parameter_with_nothing_to_choose_is_not_offered(self, client, monkeypatch):
        """A one-option enum is not a control, and the shared rule drops it from the surface.

        No voice model publishes one today, so the rule is driven by publishing one: a model pinned
        to a single language. The parameter is still applied and still published in ``defaults``
        (the lane defaults it), but it is not offered as an editable control - and the surface
        follows ``model_defaults.editable`` rather than this worker's own idea of what is settable.
        """
        model = catalog.get_model(SECTIONING)
        original = lane_contract.parameters_for

        def with_single_option(entry, defaults=None):
            return [*original(entry, defaults),
                    {"name": "language", "type": "string", "required": False,
                     "enum": ["en"], "default": "en"}]

        monkeypatch.setattr(lane_contract, "parameters_for", with_single_option)
        row = _row(client, model.repo)
        assert "language" not in _names(row["parameters"])
        assert row["defaults"]["language"] == "en", "the lane still applies what it states"
        refused = client.put("/api/model-defaults", json={
            "repo": model.repo, "values": {"language": "en"}})
        assert refused.status_code == 422, refused.text
        assert "publishes no such settable parameter" in refused.json()["detail"]["language"]


class TestRouteRoundTrip:
    def test_put_stores_get_reflects_and_delete_returns_the_model_to_the_lane(self, client):
        model = catalog.get_model(SECTIONING)
        parameter = _parameter(model, "speed")
        chosen = _inside(parameter, _row(client, model.repo)["stated"]["speed"])

        saved = client.put("/api/model-defaults",
                           json={"repo": model.repo, "values": {"speed": chosen}})
        assert saved.status_code == 200, saved.text
        row = _row(client, model.repo)
        assert row["chosen"] == {"speed": chosen}
        assert row["defaults"]["speed"] == chosen
        assert model_defaults.load(paths.data_dir()) == {model.repo: {"speed": chosen}}

        # DELETE with the repo in the body, as the brief names it.
        deleted = client.request("DELETE", "/api/model-defaults", json={"repo": model.repo})
        assert deleted.status_code == 200, deleted.text
        row = _row(client, model.repo)
        assert row["chosen"] == {}
        assert row["defaults"] == row["stated"]

        # And DELETE with it in the query string, which the same route accepts.
        client.put("/api/model-defaults",
                   json={"repo": model.repo, "values": {"speed": chosen}})
        assert client.request("DELETE", "/api/model-defaults",
                              params={"repo": model.repo}).status_code == 200
        assert _row(client, model.repo)["chosen"] == {}
        assert model_defaults.load(paths.data_dir()) == {}

    def test_a_stored_default_belongs_to_one_model_alone(self, client):
        """The owner's whole point: setting one model must not touch another."""
        first = catalog.get_model(SECTIONING)
        second = catalog.get_model(PRESET_ONLY)
        parameter = _parameter(first, "speed")
        chosen = _inside(parameter, _row(client, first.repo)["stated"]["speed"])
        client.put("/api/model-defaults",
                   json={"repo": first.repo, "values": {"speed": chosen}})

        assert _row(client, first.repo)["defaults"]["speed"] == chosen
        other = _row(client, second.repo)
        assert other["chosen"] == {}
        assert other["defaults"] == other["stated"]

    def test_an_unknown_repo_is_a_named_refusal_not_a_silent_no_op(self, client):
        for response in (
            client.put("/api/model-defaults", json={"repo": "example/not-a-model", "values": {}}),
            client.request("DELETE", "/api/model-defaults",
                           json={"repo": "example/not-a-model"}),
        ):
            assert response.status_code == 404, response.text
            assert "example/not-a-model" in str(response.json()["detail"])
        assert client.request("DELETE", "/api/model-defaults").status_code == 400
        assert model_defaults.load(paths.data_dir()) == {}


class TestRefusal:
    def test_a_value_outside_the_models_own_bound_is_refused_by_name_and_not_stored(self, client):
        """Driven against the REAL lane parameters, so a bound change moves this test."""
        model = catalog.get_model(SECTIONING)
        parameter = _parameter(model, "section_max_characters")
        assert parameter["minimum"] < parameter["maximum"]
        for outside, expected in ((parameter["maximum"] + 1, "above the lane's maximum"),
                                  (parameter["minimum"] - 1, "below the lane's minimum")):
            response = client.put("/api/model-defaults", json={
                "repo": model.repo, "values": {"section_max_characters": outside}})
            assert response.status_code == 422, response.text
            detail = response.json()["detail"]
            assert expected in detail["section_max_characters"], detail
            # Not stored, and the model is still on the lane's own value.
            assert model_defaults.load(paths.data_dir()) == {}
            row = _row(client, model.repo)
            assert row["chosen"] == {}
            assert row["defaults"]["section_max_characters"] == parameter["default"]

        # The control that proves this is a bound and not a blanket refusal: the same parameter
        # accepts the lane's own minimum, and GET shows it.
        accepted = client.put("/api/model-defaults", json={
            "repo": model.repo, "values": {"section_max_characters": parameter["minimum"]}})
        assert accepted.status_code == 200, accepted.text
        assert _row(client, model.repo)["defaults"]["section_max_characters"] == \
            parameter["minimum"]

    def test_every_bad_value_is_answered_at_once_by_name(self, client):
        model = catalog.get_model(SECTIONING)
        parameter = _parameter(model, "speed")
        response = client.put("/api/model-defaults", json={
            "repo": model.repo,
            "values": {"speed": parameter["maximum"] + 1, "seed": 7,
                       "section_max_characters": "long"},
        })
        assert response.status_code == 422, response.text
        refusals = response.json()["detail"]
        assert sorted(refusals) == ["section_max_characters", "seed", "speed"]
        assert "above the lane's maximum" in refusals["speed"]
        # `seed` is a real request field (`Txt2SpeechBody.seed`) this lane does not publish, so
        # there is nothing to default - a per-model default for it would be applied by nobody.
        assert "publishes no such settable parameter" in refusals["seed"]
        assert "expected integer" in refusals["section_max_characters"]


class TestAppliedToARequest:
    def test_a_request_that_supplies_a_parameter_is_unaffected_by_a_stored_default(
            self, client, queued):
        model = catalog.get_model(SECTIONING)
        parameter = _parameter(model, "speed")
        stored = _inside(parameter, _row(client, model.repo)["stated"]["speed"])
        supplied = _inside(parameter, stored)
        client.put("/api/model-defaults",
                   json={"repo": model.repo, "values": {"speed": stored}})

        response = client.post("/api/generate/txt2speech", json={
            "repo": model.repo, "text": "The caller's own speed wins.",
            "speed": supplied})

        assert response.status_code == 200, response.text
        assert queued[0]["speed"] == supplied, "a stored default overrode what the caller typed"

    def test_a_request_that_omits_it_gets_the_stored_default_the_catalog_publishes(
            self, client, queued):
        model = catalog.get_model(SECTIONING)
        parameter = _parameter(model, "section_max_characters")
        stored = _inside(parameter, parameter["default"])
        client.put("/api/model-defaults", json={
            "repo": model.repo, "values": {"section_max_characters": stored}})

        response = client.post("/api/generate/txt2speech", json={
            "repo": model.repo, "text": "An omitted section size takes the operator's default."})

        assert response.status_code == 200, response.text
        # The route canonicalizes the stored override exactly as it does a caller's own value.
        assert queued[0]["_resolved_section_max_characters"] == stored
        assert "section_max_characters" not in queued[0]
        # Read both sides: the number the request ran with is the number the catalog advertises.
        assert _published(client, model.repo, "section_max_characters") == \
            queued[0]["_resolved_section_max_characters"]
        assert _row(client, model.repo)["defaults"]["section_max_characters"] == \
            _published(client, model.repo, "section_max_characters")

    def test_an_omitted_plain_parameter_takes_the_stored_default(self, client, queued):
        model = catalog.get_model(SECTIONING)
        parameter = _parameter(model, "speed")
        stored = _inside(parameter, parameter["default"])
        client.put("/api/model-defaults",
                   json={"repo": model.repo, "values": {"speed": stored}})

        response = client.post("/api/generate/txt2speech", json={
            "repo": model.repo, "text": "An omitted speed takes the operator's default."})

        assert response.status_code == 200, response.text
        assert queued[0]["speed"] == stored
        assert _published(client, model.repo, "speed") == queued[0]["speed"]

    def test_the_lanes_own_default_beats_a_blanket_request_default(self, client, queued):
        """The owner's rule, applied to the request model this time.

        `Txt2SpeechBody.cfg_value = 2.0` is a blanket value meant for no model in particular; the
        lane publishes the per-model default (Chatterbox's exaggeration dial is its own number) and
        a request that omits the parameter must get that, not the blanket one.
        """
        model = _model_publishing("cfg_value")
        parameter = _parameter(model, "cfg_value")
        blanket = main.Txt2SpeechBody.model_fields["cfg_value"].default
        assert parameter["default"] != blanket, "the fixture must be a real disagreement"

        response = client.post("/api/generate/txt2speech", json={
            "repo": model.repo, "text": "An omitted dial takes the model's own default."})

        assert response.status_code == 200, response.text
        assert queued[0]["cfg_value"] != blanket
        assert queued[0]["cfg_value"] == parameter["default"]
        assert _published(client, model.repo, "cfg_value") == queued[0]["cfg_value"]

    def test_the_stored_section_size_reaches_the_engine_for_a_job_that_omits_it(
            self, client, data_dir, queued, monkeypatch):
        """'The route stored it' is not the same claim as 'the setting does something'."""
        model = catalog.get_model(SECTIONING)
        parameter = _parameter(model, "section_max_characters")
        stored = _inside(parameter, parameter["default"])
        client.put("/api/model-defaults", json={
            "repo": model.repo, "values": {"section_max_characters": stored}})
        long_text = ("A sentence long enough that the real chunker has to cut it. " * 60).strip()

        response = client.post("/api/generate/txt2speech", json={
            "repo": model.repo, "text": long_text})
        assert response.status_code == 200, response.text
        assert queued[0]["_resolved_section_max_characters"] == stored

        # Hand the route's own parameters to the engine seam that cuts the script, so the claim
        # is about the value the chunker is given and not about the route's own bookkeeping.
        seen: dict = {}
        monkeypatch.setattr(
            generation, "_internal_mlx_text_chunks",
            lambda family, repo, text, *, max_chars_override=None: seen.update(
                family=family, repo=repo, max_chars_override=max_chars_override) or ["one", "two"])
        monkeypatch.setattr(
            generation.GenerationManager, "_dispatch_mlx_long_form",
            lambda self, job, output_path, model, text, chunks: seen.update(
                dispatched=list(chunks)))
        job = generation.GenerationJob(job_id="stored-section", mode="txt2speech",
                                       params=dict(queued[0]))
        main.gen_manager._dispatch_txt2speech(job, data_dir / "out.wav")

        assert seen["family"] == catalog.family_for(model).id
        assert seen["max_chars_override"] == stored, "the stored size never reached the chunker"
        assert seen["dispatched"] == ["one", "two"]

        # And the real chunker cuts to that budget: the setting changes the work, not just a field.
        chunks = generation._internal_mlx_text_chunks(
            catalog.family_for(model).id, model.repo, long_text, max_chars_override=stored)
        assert len(chunks) > 1
        assert all(len(chunk) <= stored for chunk in chunks)

    def test_route_catalog_generation_defaults_and_the_engine_agree(
            self, client, data_dir, queued, monkeypatch):
        """One value read at four places must be the same number.

        The route's own parameters for the job, the lane contract's published ``default``, the
        row's ``generation_defaults`` (what the app pre-fills a form from) and the value the engine
        seam is handed - if any of the four can disagree, the Models tab is showing an operator a
        number the run does not use.
        """
        model = catalog.get_model(SECTIONING)
        parameter = _parameter(model, "section_max_characters")
        stored = _inside(parameter, parameter["default"])
        client.put("/api/model-defaults", json={
            "repo": model.repo, "values": {"section_max_characters": stored}})

        response = client.post("/api/generate/txt2speech", json={
            "repo": model.repo, "text": "An omitted section size, read four ways."})
        assert response.status_code == 200, response.text
        routed = queued[0]["_resolved_section_max_characters"]

        row = next(m for m in client.get("/api/catalog").json()["models"]
                   if m["repo"] == model.repo)
        published = next(p for p in row["lane_contract"]["parameters"]
                         if p["name"] == "section_max_characters")["default"]
        generation_defaults = row["generation_defaults"]["section_max_characters"]

        seen: dict = {}
        monkeypatch.setattr(
            generation, "_internal_mlx_text_chunks",
            lambda family, repo, text, *, max_chars_override=None: seen.update(
                max_chars_override=max_chars_override) or ["one", "two"])
        monkeypatch.setattr(generation.GenerationManager, "_dispatch_mlx_long_form",
                            lambda *args, **kwargs: None)
        job = generation.GenerationJob(job_id="four-way", mode="txt2speech",
                                       params=dict(queued[0]))
        main.gen_manager._dispatch_txt2speech(job, data_dir / "out.wav")

        assert routed == published == generation_defaults == stored
        assert seen["max_chars_override"] == routed

    def test_the_row_publishes_the_same_map_for_every_defaulted_parameter(self, client):
        """``generation_defaults`` and the lane contract's ``default`` fields are one answer."""
        for row in client.get("/api/catalog").json()["models"]:
            published = {p["name"]: p["default"] for p in row["lane_contract"]["parameters"]
                         if "default" in p}
            generation_defaults = row["generation_defaults"]
            assert set(generation_defaults) == set(published), row["repo"]
            assert generation_defaults == published, row["repo"]

    def test_every_row_publishes_a_seedable_default_map(self, client):
        """Not just the model a test happens to name: every registered row carries the object."""
        rows = client.get("/api/catalog").json()["models"]
        assert rows
        for row in rows:
            assert isinstance(row.get("generation_defaults"), dict), row["repo"]
            assert "speed" in row["generation_defaults"], row["repo"]

    def test_a_page_that_seeds_from_the_catalog_sends_the_stored_pace(self, client, queued):
        """A stored default the app cannot read is defeated by the app sending every field.

        The Audio page pre-fills its speed slider from the catalogue's ``generation_defaults`` and
        sends every field, so the row has to publish the resolved pace: seeded from anything else,
        the request arrives carrying that other number and wins over the operator's default.
        """
        model = catalog.get_model(SECTIONING)
        parameter = _parameter(model, "speed")
        stored = _inside(parameter, _row(client, model.repo)["stated"]["speed"])
        client.put("/api/model-defaults",
                   json={"repo": model.repo, "values": {"speed": stored}})

        row = next(m for m in client.get("/api/catalog").json()["models"]
                   if m["repo"] == model.repo)
        seeded = row["generation_defaults"]["speed"]
        assert seeded == stored
        assert seeded == next(p for p in row["lane_contract"]["parameters"]
                              if p["name"] == "speed")["default"]

        # The page sends every field, so the value it seeded with comes back as an explicit one.
        response = client.post("/api/generate/txt2speech", json={
            "repo": model.repo, "text": "The page seeded this pace.", "speed": seeded})
        assert response.status_code == 200, response.text
        assert queued[0]["speed"] == stored

    def test_an_omitted_preset_speaker_takes_the_stored_voice(self, client, queued, monkeypatch):
        """SH-16, end to end: which ready-made voice a model speaks with is a per-model default.

        No shipped row both offers a preset and publishes a roster today, so the row the roster
        belongs to is registered here. The route's own resolution runs; nothing about it is
        re-implemented in this test.
        """
        model = _register_custom_voice(monkeypatch)
        stored = catalog.family_for(model).preset_roster.speakers[-1]["id"]
        saved = client.put("/api/model-defaults", json={
            "repo": model.repo, "values": {"preset_speaker": stored}})
        assert saved.status_code == 200, saved.text

        response = client.post("/api/generate/txt2speech", json={
            "repo": model.repo, "text": "An omitted speaker takes the operator's voice."})
        assert response.status_code == 200, response.text
        assert queued[0]["preset_speaker"] == stored
        # Read both sides: the voice the request ran with is the one the catalogue advertises.
        assert _published(client, model.repo, "preset_speaker") == stored
        assert _row(client, model.repo)["defaults"]["preset_speaker"] == stored

    def test_a_request_that_supplies_a_speaker_keeps_it(self, client, queued, monkeypatch):
        model = _register_custom_voice(monkeypatch)
        speakers = catalog.family_for(model).preset_roster.speakers
        stored, supplied = speakers[-1]["id"], speakers[0]["id"]
        assert stored != supplied, "the fixture must be a real disagreement"
        client.put("/api/model-defaults", json={
            "repo": model.repo, "values": {"preset_speaker": stored}})

        response = client.post("/api/generate/txt2speech", json={
            "repo": model.repo, "text": "The caller's own voice wins.",
            "preset_speaker": supplied})
        assert response.status_code == 200, response.text
        assert queued[0]["preset_speaker"] == supplied, (
            "a stored default overrode the speaker the caller sent"
        )

    def test_a_speaker_the_roster_does_not_publish_is_refused_by_name(self, client, monkeypatch):
        """The enum the contract publishes is the bound the route enforces."""
        model = _register_custom_voice(monkeypatch)
        refused = client.put("/api/model-defaults", json={
            "repo": model.repo, "values": {"preset_speaker": "Ethan"}})
        assert refused.status_code == 422, refused.text
        assert "Ethan" in refused.json()["detail"]["preset_speaker"]
        assert model_defaults.load(paths.data_dir()) == {}

    def test_the_reference_route_applies_them_the_same_way(self, client, queued, monkeypatch):
        model = catalog.get_model(SECTIONING)
        parameter = _parameter(model, "speed")
        stored = _inside(parameter, parameter["default"])
        client.put("/api/model-defaults",
                   json={"repo": model.repo, "values": {"speed": stored}})
        monkeypatch.setattr(main.reference_audio, "prepare", lambda **_kwargs: {
            "path": "/private/reference.wav", "derived_sha256": "a" * 64,
            "source_sha256": "b" * 64, "preparation_revision": "test-v1",
            "duration_seconds": 3.0, "transcript": "Prepared reference.",
        })

        omitted = client.post(
            "/api/generate/txt2speech/reference",
            data={"request_json": json.dumps({"repo": model.repo, "text": "A clone."})},
            files={"audio": ("reference.wav", b"RIFF", "audio/wav")})
        assert omitted.status_code == 200, omitted.text
        assert queued[0]["speed"] == stored

        supplied = _inside(parameter, stored)
        explicit = client.post(
            "/api/generate/txt2speech/reference",
            data={"request_json": json.dumps({
                "repo": model.repo, "text": "A clone.", "speed": supplied})},
            files={"audio": ("reference.wav", b"RIFF", "audio/wav")})
        assert explicit.status_code == 200, explicit.text
        assert queued[1]["speed"] == supplied

    def test_with_nothing_stored_an_omitted_parameter_stays_exactly_as_the_lane_had_it(
            self, client, queued):
        """A stored default is the only thing that changes an omitted field.

        The worker's own behaviour for an omitted parameter - here, the section size is Auto -
        must survive the defaults surface existing at all.
        """
        model = catalog.get_model(SECTIONING)
        response = client.post("/api/generate/txt2speech", json={
            "repo": model.repo, "text": "Nothing is stored for this model."})

        assert response.status_code == 200, response.text
        assert queued[0]["section_max_characters"] is None
        assert "_resolved_section_max_characters" not in queued[0]
        # The catalog still publishes the number that path resolves to, so a scheduler that
        # sends it explicitly and one that omits it agree.
        assert _published(client, model.repo, "section_max_characters") == \
            _parameter(model, "section_max_characters")["default"]


class TestNothingThatMustNotMove:
    def test_modes_and_the_per_mode_request_fields_survive_a_stored_default(self, client):
        before = {m["repo"]: m for m in client.get("/api/catalog").json()["models"]}
        model = catalog.get_model(SECTIONING)
        client.put("/api/model-defaults",
                   json={"repo": model.repo, "values": {"speed": 1.5}})
        after = {m["repo"]: m for m in client.get("/api/catalog").json()["models"]}

        assert {repo: row["modes"] for repo, row in after.items()} == \
            {repo: row["modes"] for repo, row in before.items()}
        for repo, row in after.items():
            assert row["modes"] == catalog.voice_modes(catalog.get_model(repo)), repo
            assert _names(row["lane_contract"]["parameters"]) == \
                _names(before[repo]["lane_contract"]["parameters"]), repo
        preset = next(m for m in catalog.CATALOG if catalog.voice_modes(m) == ["preset"])
        assert after[preset.repo]["modes"] == ["preset"]

    def test_the_memory_policy_contract_is_untouched_by_the_defaults_surface(self, client,
                                                                           monkeypatch):
        monkeypatch.setattr(main.memory_policy, "SETTINGS_FILE",
                            paths.data_dir() / "memory_policy.json")
        policy = client.get("/api/memory-policy")
        assert policy.status_code == 200
        for field in ("mode", "resolved_mode", "idle_seconds", "options",
                      "loaded_models", "active_work", "busy"):
            assert field in policy.json(), field
        saved = client.put("/api/memory-policy", json={"mode": "memory_saver"})
        assert saved.status_code == 200
        assert saved.json()["mode"] == "memory_saver"
        # The defaults surface is its own file; the memory policy's is not touched by it.
        assert (paths.data_dir() / "memory_policy.json").is_file()
        assert not (paths.data_dir() / model_defaults.SETTINGS_NAME).exists()


# Named `Test*` so pytest collects it: a class the runner silently skips is a test
# that never runs, which is how this boundary went unguarded in the first place.
class TestLaneDecidesBoundary:
    """The one exception in the whole fleet, pinned as a rule rather than a comment.

    Voice's ``_job_parameters`` leaves a field alone when the request sent nothing **and** the value
    in force is the lane's own stated default: rewriting it would record an override the operator
    never chose, and the engine resolves that stated default itself. That reasoning holds *only*
    while the lane states the value. Widen the condition to "the request sent nothing" and every
    optional parameter silently loses its stored default — ``preset_speaker``, ``instruct``,
    ``cfg_value``, ``voice_design_prompt`` would all stop reaching the engine. That is the defect
    this release fixed three times over, so the boundary is worth a test of its own.

    Every other lane fills an omitted field from the stored map unconditionally (Image and Music in
    ``validate_request``, Subtitle in ``with_model_defaults``, Chat in ``_resolved_parameters``), so
    the exception is Voice's alone and this is where it is guarded.
    """

    @staticmethod
    def _body(**values):
        return main.Txt2SpeechBody(repo=SECTIONING, text="A sentence.", **values)

    @staticmethod
    def _run(monkeypatch, *, lane_states, in_force, body):
        model = catalog.get_model(SECTIONING)
        monkeypatch.setattr(main, "_defaults_in_force", lambda _model: dict(in_force))
        monkeypatch.setattr(
            main.lane_contract, "parameters_for",
            lambda _model: [{"name": name, **({"default": value} if value is not None else {})}
                            for name, value in lane_states.items()])
        return main._job_parameters(body, model)

    def test_a_stored_default_reaches_the_engine_when_the_lane_states_none(self, monkeypatch):
        params = self._run(
            monkeypatch,
            lane_states={"preset_speaker": None},
            in_force={"preset_speaker": "Sohee"},
            body=self._body())
        assert params["preset_speaker"] == "Sohee", (
            "the lane states no default, so there is nothing for the engine to resolve — the "
            "operator's stored voice has to be written into the request"
        )

    def test_the_lane_stated_default_is_left_for_the_engine_to_resolve(self, monkeypatch):
        params = self._run(
            monkeypatch,
            lane_states={"preset_speaker": "Ryan"},
            in_force={"preset_speaker": "Ryan"},
            body=self._body())
        assert params["preset_speaker"] is None, (
            "the request already resolves to the lane's own stated default; writing it in would "
            "record an override the operator never chose"
        )

    def test_a_stored_default_is_not_swallowed_by_a_lane_stated_one(self, monkeypatch):
        """The case the exception must not eat: the operator chose something else."""
        params = self._run(
            monkeypatch,
            lane_states={"preset_speaker": "Ryan"},
            in_force={"preset_speaker": "Sohee"},
            body=self._body())
        assert params["preset_speaker"] == "Sohee", (
            "the lane states a default and the operator stored a different one; the stored value "
            "is the one in force and has to reach the engine"
        )

    def test_a_value_the_request_supplies_always_wins(self, monkeypatch):
        params = self._run(
            monkeypatch,
            lane_states={"preset_speaker": None},
            in_force={"preset_speaker": "Sohee"},
            body=self._body(preset_speaker="Aiden"))
        assert params["preset_speaker"] == "Aiden", "a supplied value is the caller's own answer"


def test_the_operator_memory_floor_the_hub_forwards_reaches_the_job_params():
    """`memory_floor_gb` is a real request field, so it lands in `job.params` where
    `GenerationManager._memory_preflight` reads it; an unknown field would be dropped silently."""
    body = main.Txt2SpeechBody(repo=SECTIONING, text="A sentence.", memory_floor_gb=1.5)
    assert main._job_parameters(body, catalog.get_model(SECTIONING))["memory_floor_gb"] == 1.5
    assert main.Txt2SpeechBody(repo=SECTIONING, text="x").memory_floor_gb is None
