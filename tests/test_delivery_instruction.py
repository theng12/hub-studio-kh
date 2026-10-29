"""A per-request delivery instruction, in each engine's own documented form (owner 2026-10-29).

The owner's words: *"the sad one works, add the emotion setting"* and *"use the instruction from
huggingface ... different model might offer different emotions."* He proved on this Mac that the
Breeze TTS 2 engine carries a direction through a cloned voice —
``model.generate(text=..., ref_audio=..., ref_text=..., instruct="Speak slowly and sadly, ...")``
— and the Hub dropped it: the clone branch returned before it ever read ``instruct``.

So there is ONE request field, and each family's engine has its OWN documented way of taking it:

* **Breeze TTS 2** (card: "Voice Direction") — the instruction is the engine's ``instruct``
  argument, passed TOGETHER with the reference clip and its transcript, and the card recommends
  ``cfg_scale`` 4 for a directed request. That number is published in the catalogue as the model's
  own default for direction; it is applied only while an instruction is present, and it is
  recorded on the job.
* **VoxCPM2** (card: "Controllable Cloning") — a ``(style)`` parenthetical prefix at the start of
  the text sent to the engine, ``(slightly faster, cheerful tone)This is a cloned voice...``.
  Parentheses inside the instruction are removed so they cannot break that construct, and the
  script the caller recorded is never altered.

What is pinned here: the engine kwargs each form produces, the directed ``cfg_scale``, the
refusal (never a silent drop) on a model whose engine has no such input, the 500-character bound,
the parentheses rule, the record on the job and its result, the broker's hop, and the published
capability and guidance. A real render is not possible on this machine (the weights' drive is not
mounted), so the engine *call* is what these tests assert — the same choice `tests/test_breeze_
family.py` makes, and for the same reason.
"""
from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from components import model_defaults  # noqa: E402
from components.hub.backend import broker, capabilities  # noqa: E402
from components.voice.app.backend import (  # noqa: E402
    catalog, generation, lane_contract, main,
)

BREEZE = "mlx-community/Breeze-TTS-2-mlx-4bit"
VOXCPM = "mlx-community/VoxCPM2-4bit"
# A model whose engine has no instruction input at all: Qwen3-TTS Base clones and samples, and
# takes no delivery direction.
NO_INSTRUCTION_INPUT = "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit"

SAD = "Speak slowly and sadly, voice close to breaking, full of longing."
REFERENCE = ROOT / "qualification" / "evidence" / "st04-reference-clip.wav"


class _Library:
    """The Voices library the resolvers read: one voice, with a transcript."""

    def __init__(self, transcript: str = "the reference words") -> None:
        self._transcript = transcript

    def get(self, voice_id: str):
        return {"id": voice_id}

    def reference_path(self, voice_id: str) -> Path:
        return REFERENCE

    def transcript(self, voice_id: str) -> str | None:
        return self._transcript


def _breeze_kwargs(params: dict) -> tuple[dict, str]:
    manager = object.__new__(generation.GenerationManager)
    gen_kwargs: dict = {"text": "hello"}
    label = manager._mlx_kwargs_design_clone_or_direct(
        params, gen_kwargs, SimpleNamespace(library=_Library()))
    return gen_kwargs, label


def _job_parameters(model, **fields) -> dict:
    """`_job_parameters` as an operator with no stored choices would get it."""
    in_force = model_defaults.stated(lane_contract.parameters_for(model))
    with mock.patch.object(main, "_defaults_in_force", return_value=in_force):
        return main._job_parameters(
            main.Txt2SpeechBody(repo=model.repo, text="hi", **fields), model)


class BreezeDirectionTests(unittest.TestCase):
    """Breeze: the instruction rides BESIDE the clone, not instead of it."""

    def test_a_clone_carries_the_instruction_beside_the_reference(self) -> None:
        kwargs, label = _breeze_kwargs({
            "voice_library_id": "a9aedc5c6bd3", "delivery_instruction": SAD})
        self.assertEqual(str(REFERENCE), kwargs["ref_audio"],
                         "a chosen voice must still reach the engine as a reference")
        self.assertEqual("the reference words", kwargs["ref_text"])
        self.assertEqual(SAD, kwargs["instruct"],
                         "the direction was dropped before it reached the engine")
        self.assertIn("clone + direction", label)

    def test_the_directed_cfg_scale_is_the_cards_number_and_is_recorded(self) -> None:
        model = catalog.get_model(BREEZE)
        guidance = catalog.delivery_instruction_for(model.family)
        self.assertIsNotNone(guidance)
        assert guidance is not None
        # The card's recommendation, published as data - not a literal in the worker.
        self.assertEqual(4.0, guidance.directed_cfg_scale)
        self.assertEqual("breeze_cfg_scale", guidance.directed_cfg_parameter)

        params = _job_parameters(model, delivery_instruction=SAD)
        self.assertEqual(4.0, params["breeze_cfg_scale"],
                         "a directed request must run at the card's guidance strength")
        self.assertEqual(SAD, params["delivery_instruction"])
        # ... and the levers table is what turns that recorded request value into the engine's
        # own `cfg_scale`, so the number that reaches Breeze is the one recorded here.
        self.assertEqual(4.0, catalog.sampling_kwargs(model.family, model.repo, params)["cfg_scale"])

    def test_a_request_that_chooses_its_own_cfg_scale_keeps_it(self) -> None:
        model = catalog.get_model(BREEZE)
        params = _job_parameters(model, delivery_instruction=SAD, breeze_cfg_scale=1.5)
        self.assertEqual(1.5, params["breeze_cfg_scale"])
        # An explicit null means "not chosen", exactly as omitting the field does - a client that
        # serialises every field as null must still get the card's directed value.
        self.assertEqual(4.0, _job_parameters(
            model, delivery_instruction=SAD, breeze_cfg_scale=None)["breeze_cfg_scale"])

    def test_no_instruction_leaves_the_plain_default_in_force(self) -> None:
        params = _job_parameters(catalog.get_model(BREEZE))
        self.assertNotIn("breeze_cfg_scale", params,
                         "an undirected request must keep the default from the levers table")
        self.assertNotIn("delivery_instruction", params,
                         "an omitted instruction must not be recorded, so a retried request "
                         "still compares equal to its stored one")

    def test_the_instruction_wins_over_a_design_prompt_with_no_reference(self) -> None:
        kwargs, label = _breeze_kwargs({
            "voice_design_prompt": "deep, slow and warm", "delivery_instruction": SAD})
        # Breeze has ONE `instruct` key, so with no reference exactly one of the two can be used.
        self.assertEqual(SAD, kwargs["instruct"])
        self.assertIn("direction", label)


class VoxCPMStylePrefixTests(unittest.TestCase):
    """VoxCPM2: the style is a parenthesised prefix in front of the text."""

    def test_the_prefix_goes_in_front_of_the_text(self) -> None:
        model = catalog.get_model(VOXCPM)
        sent = generation._delivery_engine_text(
            model, {"delivery_instruction": "slightly faster, cheerful tone"}, "Hello there.")
        self.assertEqual("(slightly faster, cheerful tone)Hello there.", sent)

    def test_the_script_the_caller_recorded_is_never_altered(self) -> None:
        params = {"delivery_instruction": "sad (quiet) tone", "text": "Hello (there)."}
        before = dict(params)
        generation._delivery_engine_text(catalog.get_model(VOXCPM), params, params["text"])
        self.assertEqual(before, params, "the prefix must be built, never written into the script")

    def test_the_record_keeps_the_callers_text_and_states_what_was_sent(self) -> None:
        record = generation._delivery_applied(
            catalog.get_model(VOXCPM), {"delivery_instruction": "sad (quiet) tone"}, {})
        assert record is not None
        self.assertEqual("sad (quiet) tone", record["text"])
        self.assertEqual("sad quiet tone", record["sent_text"])
        self.assertEqual("(sad quiet tone)", record["text_prefix"])
        self.assertEqual("text_prefix", record["engine_form"])
        self.assertIsNone(record["cfg_scale"], "VoxCPM2 publishes no directed cfg value")


class UnchangedWithoutAnInstructionTests(unittest.TestCase):
    def test_a_breeze_clone_without_an_instruction_is_what_it_was(self) -> None:
        kwargs, label = _breeze_kwargs({"voice_library_id": "a9aedc5c6bd3"})
        self.assertNotIn("instruct", kwargs)
        self.assertEqual({"text", "ref_audio", "ref_text"}, set(kwargs))
        self.assertEqual("clone (voice=a9aedc5c6bd3)", label)

    def test_a_voxcpm_text_without_an_instruction_is_untouched(self) -> None:
        model = catalog.get_model(VOXCPM)
        self.assertEqual("Hello.", generation._delivery_engine_text(model, {}, "Hello."))
        self.assertIsNone(generation._delivery_applied(model, {}, {}))


class RefusalTests(unittest.TestCase):
    """An engine with no instruction input refuses; it never accepts and drops it."""

    def test_the_catalogue_refuses_by_name(self) -> None:
        refusal = catalog.delivery_instruction_refusal(
            catalog.get_model(NO_INSTRUCTION_INPUT), SAD)
        self.assertIsNotNone(refusal)
        assert refusal is not None
        self.assertIn("delivery-instruction", refusal,
                      "the refusal must name the capability that is missing")
        self.assertIn("Remove the instruction", refusal,
                      "the refusal must say what the caller can do")

    def test_the_manager_refuses_before_a_job_exists(self) -> None:
        manager = object.__new__(generation.GenerationManager)
        with self.assertRaises(ValueError) as caught:
            manager.start_txt2speech({
                "repo": NO_INSTRUCTION_INPUT, "text": "hi", "delivery_instruction": SAD})
        self.assertIn("delivery-instruction", str(caught.exception))

    def test_the_plain_route_answers_400_with_the_reason(self) -> None:
        body = main.Txt2SpeechBody(repo=NO_INSTRUCTION_INPUT, text="hi",
                                   delivery_instruction=SAD)
        with self.assertRaises(main.HTTPException) as caught:
            main.start_txt2speech(body, None)
        self.assertEqual(400, caught.exception.status_code)
        self.assertIn("delivery-instruction", str(caught.exception.detail))

    def test_the_reference_route_refuses_too(self) -> None:
        # The reference route checks the same rule before it prepares any audio.
        body = main.Txt2SpeechBody(repo=NO_INSTRUCTION_INPUT, text="hi",
                                   delivery_instruction=SAD)
        with self.assertRaises(main.HTTPException) as caught:
            main._refuse_unsupported_delivery_instruction(body, catalog.get_model(
                NO_INSTRUCTION_INPUT))
        self.assertEqual(400, caught.exception.status_code)


class BoundTests(unittest.TestCase):
    def test_the_request_model_holds_the_published_bound(self) -> None:
        limit = lane_contract.DELIVERY_INSTRUCTION_MAX_CHARACTERS
        self.assertEqual(500, limit)
        main.Txt2SpeechBody(repo=BREEZE, text="hi", delivery_instruction="x" * limit)
        with self.assertRaises(ValueError):
            main.Txt2SpeechBody(repo=BREEZE, text="hi", delivery_instruction="x" * (limit + 1))

    def test_the_published_guidance_quotes_the_same_number(self) -> None:
        row = catalog.serialize_model(catalog.get_model(BREEZE))
        self.assertEqual(lane_contract.DELIVERY_INSTRUCTION_MAX_CHARACTERS,
                         row["delivery_instruction"]["max_characters"])
        parameter = next(p for p in row["lane_contract"]["parameters"]
                         if p["name"] == "delivery_instruction")
        self.assertEqual(lane_contract.DELIVERY_INSTRUCTION_MAX_CHARACTERS,
                         parameter["max_characters"])

    def test_a_longer_instruction_is_refused_by_the_catalogue_check_as_well(self) -> None:
        refusal = catalog.delivery_instruction_refusal(
            catalog.get_model(BREEZE), "x" * 501)
        self.assertIsNotNone(refusal)
        assert refusal is not None
        self.assertIn("501", refusal)


class ParenthesisTests(unittest.TestCase):
    """The style prefix's own delimiter must not be closable from inside it."""

    def test_parentheses_are_removed_from_the_prefix_form(self) -> None:
        model = catalog.get_model(VOXCPM)
        self.assertEqual(
            "sad quiet tone",
            catalog.delivery_instruction_for_engine(model, "sad (quiet) )tone("))

    def test_the_built_prefix_holds_exactly_one_pair(self) -> None:
        model = catalog.get_model(VOXCPM)
        sent = generation._delivery_engine_text(
            model, {"delivery_instruction": "sad)Hello. (quiet"}, "Body.")
        self.assertEqual(1, sent.count("("))
        self.assertEqual(1, sent.count(")"))
        self.assertTrue(sent.startswith("(sad Hello. quiet)Body."), sent)

    def test_an_instruction_that_is_only_parentheses_is_refused(self) -> None:
        refusal = catalog.delivery_instruction_refusal(catalog.get_model(VOXCPM), "()")
        self.assertIsNotNone(refusal)
        assert refusal is not None
        self.assertIn("empty", refusal)

    def test_the_instruct_form_keeps_the_callers_words_intact(self) -> None:
        # Parentheses are only a delimiter for the prefix form; Breeze's instruction is free text.
        self.assertEqual("(whispering) read it softly",
                         catalog.delivery_instruction_for_engine(
                             catalog.get_model(BREEZE), "(whispering) read it softly"))


class RelayTests(unittest.TestCase):
    """The field must survive the broker, the worker hop, and the staged-reference form."""

    def test_admission_keeps_a_shared_instruction(self) -> None:
        submitted = broker.submit_batch({
            "modality": "voice", "model": BREEZE,
            "sharedParams": {"delivery_instruction": SAD},
            "items": [{"text": "one"}],
        })
        # The queue is module-level and the gate runs every test in one process: a batch left
        # here is a row `/api/hub/jobs` shows to the next module (`test_hub_jobs` asserts the
        # listing holds exactly its own two completions).
        self.addCleanup(broker.batches.pop, submitted["batch_id"], None)
        self.assertIn("batch_id", submitted, submitted)
        batch = broker.batches[submitted["batch_id"]]
        self.assertEqual(SAD, batch["shared_params"]["delivery_instruction"],
                         "the Hub dropped a direction the caller submitted")

    def test_a_top_level_instruction_is_lifted_like_the_voice_id(self) -> None:
        shared = broker._shared_params_with_local_reference({
            "modality": "voice", "delivery_instruction": SAD,
            "sharedParams": {"voice_library_id": "v"}})
        self.assertEqual(SAD, shared["delivery_instruction"])

    def test_the_dispatch_post_carries_it_to_the_worker(self) -> None:
        posted: list[dict] = []

        class Stop(Exception):
            pass

        class Wire:
            async def post(self, url, *, json=None, **kwargs):
                posted.append(json)
                raise Stop("captured")

        studio = {"id": "voice-local", "modality": "voice", "machine": "local",
                  "host": "127.0.0.1", "port": 51101, "local": True}
        batch = {"id": "relay-1", "modality": "voice", "model": BREEZE,
                 "shared_params": {"delivery_instruction": SAD}, "cancelled": False,
                 "routing": "pool", "created_at": 0.0, "items": []}
        item = {"index": 0, "prompt": "one", "params": {}, "seed": None, "state": "queued",
                "tries": 0, "studio": None, "studio_job_id": None, "retry_at": None,
                "error": None}
        batch["items"] = [item]
        broker.batches[batch["id"]] = batch
        # Same rule as `test_admission_keeps_a_shared_instruction`: this process is the whole
        # unittest gate, so the batch is removed even when an assertion below fails.
        self.addCleanup(broker.batches.pop, batch["id"], None)
        with mock.patch.object(broker, "_recover_worker_job",
                               mock.AsyncMock(return_value=False)), \
             mock.patch.object(broker.ledger, "save_batch", lambda *a, **k: None), \
             mock.patch.object(broker, "_post_item_webhook", mock.AsyncMock()), \
             mock.patch.object(broker, "_maybe_finish", mock.AsyncMock()):
            asyncio.run(broker._run_item(Wire(), batch, item, studio))
        self.assertTrue(posted, "the broker never posted the item")
        self.assertEqual(SAD, posted[0].get("delivery_instruction"),
                         "the direction did not reach the worker request body")

    def test_the_staged_reference_request_form_carries_it(self) -> None:
        # The worker's reference route parses exactly this: `Txt2SpeechBody.model_validate`
        # over `request_json` from the multipart form the Hub posts.
        body = main.Txt2SpeechBody.model_validate(json.loads(json.dumps({
            "repo": BREEZE, "text": "one", "delivery_instruction": SAD,
            "voice_private_reference": True,  # an unknown key is ignored, as before
        })))
        self.assertEqual(SAD, body.delivery_instruction)
        self.assertEqual(SAD, _job_parameters(catalog.get_model(BREEZE),
                                              delivery_instruction=SAD)["delivery_instruction"])

    def test_the_workers_applied_record_reaches_the_result(self) -> None:
        applied = {"text": SAD, "sent_text": SAD, "engine_form": "instruct",
                   "engine_argument": "instruct", "text_prefix": None,
                   "cfg_parameter": "breeze_cfg_scale", "cfg_scale": 4.0}
        item = {"state": "done", "index": 0, "delivery_instruction": applied,
                "media_type": "audio/wav", "sha256": "a" * 64, "bytes": 8}
        result = broker.terminal_result({"id": "b"}, item)
        self.assertEqual(applied, result["delivery_instruction"])
        self.assertEqual(applied, broker.public_item({"id": "b"}, item)["delivery_instruction"])

    def test_a_job_without_one_publishes_nothing_new(self) -> None:
        item = {"state": "done", "index": 0, "media_type": "audio/wav",
                "sha256": "a" * 64, "bytes": 8}
        self.assertNotIn("delivery_instruction", broker.terminal_result({"id": "b"}, item))


class JobRecordTests(unittest.TestCase):
    """The job's stored parameters and its result both state what was asked and what ran."""

    def test_the_record_carries_the_instruction_and_the_cfg_scale(self) -> None:
        applied = generation._delivery_applied(
            catalog.get_model(BREEZE), {"delivery_instruction": SAD}, {"cfg_scale": 4.0})
        job = generation.GenerationJob(
            job_id="j1", mode="txt2speech",
            params={"repo": BREEZE, "text": "one", "delivery_instruction": SAD,
                    "breeze_cfg_scale": 4.0, "_delivery_instruction_applied": applied},
        )
        result = job.serialize()
        self.assertEqual(SAD, result["params"]["delivery_instruction"],
                         "the request's own text stays in the stored parameters")
        self.assertEqual(4.0, result["params"]["breeze_cfg_scale"],
                         "the guidance strength the run used is in the stored parameters")
        self.assertEqual(applied, result["delivery_instruction"])
        self.assertEqual(SAD, result["delivery_instruction"]["text"])
        self.assertEqual(4.0, result["delivery_instruction"]["cfg_scale"])
        self.assertNotIn("_delivery_instruction_applied", result["params"],
                         "an internal record is not published among the request's parameters")
        json.dumps(result)  # the whole result must stay JSON-serialisable

    def test_a_job_without_one_publishes_no_record(self) -> None:
        job = generation.GenerationJob(job_id="j2", mode="txt2speech",
                                       params={"repo": BREEZE, "text": "one"})
        self.assertNotIn("delivery_instruction", job.serialize())


class SectionDispatchTests(unittest.TestCase):
    """What one engine call receives, proved through the real section renderer."""

    def test_every_section_of_a_voxcpm_script_carries_the_style_prefix(self) -> None:
        import tempfile

        import numpy as np
        import soundfile as sf

        model = catalog.get_model(VOXCPM)
        chunks = ["First complete sentence.", "Second complete sentence."]
        sent: list[str] = []

        def fake_generate_audio(*, model, output_path, join_audio, **kwargs) -> None:
            sent.append(kwargs["text"])
            sf.write(Path(output_path) / "audio.wav",
                     np.full(400, 0.5, dtype=np.float32), 24000, subtype="PCM_16")

        manager = object.__new__(generation.GenerationManager)
        job = generation.GenerationJob(job_id="voxcpm-style", mode="txt2speech",
                                       params={"text": " ".join(chunks),
                                               "delivery_instruction": "sad (quiet) tone"})
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sections = root / "sections"
            sections.mkdir()
            manager._generate_mlx_long_form_sections(
                job, "voxcpm-mlx", "voxcpm-model", {"text": job.params["text"]},
                chunks, sections, root / "out.wav", fake_generate_audio,
                VOXCPM, model_entry=model)
        self.assertEqual(["(sad quiet tone)First complete sentence.",
                          "(sad quiet tone)Second complete sentence."], sent,
                         "the style must reach every engine call, not only the first")
        self.assertEqual(" ".join(chunks), job.params["text"],
                         "the script the caller recorded is never rewritten")

    def test_a_breeze_instruction_reaches_every_section_as_instruct(self) -> None:
        import tempfile

        import numpy as np
        import soundfile as sf

        model = catalog.get_model(BREEZE)
        instructions: list = []

        def fake_generate_audio(*, model, output_path, join_audio, **kwargs) -> None:
            instructions.append(kwargs.get("instruct"))
            sf.write(Path(output_path) / "audio.wav",
                     np.full(400, 0.5, dtype=np.float32), 24000, subtype="PCM_16")

        manager = object.__new__(generation.GenerationManager)
        job = generation.GenerationJob(job_id="breeze-direction", mode="txt2speech",
                                       params={"delivery_instruction": SAD})
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sections = root / "sections"
            sections.mkdir()
            manager._generate_mlx_long_form_sections(
                job, "breeze-tts", "breeze-model", {"text": "One. Two.", "instruct": SAD},
                ["One.", "Two."], sections, root / "out.wav", fake_generate_audio,
                BREEZE, model_entry=model)
        self.assertEqual([SAD, SAD], instructions)


class PublicationTests(unittest.TestCase):
    """The capability and the guidance, as data, for the Gateway and Story to build against."""

    def test_only_a_family_whose_engine_takes_it_publishes_the_capability(self) -> None:
        for repo in (BREEZE, VOXCPM):
            self.assertIn(catalog.DELIVERY_INSTRUCTION_CAPABILITY,
                          catalog.serialize_model(catalog.get_model(repo))["capabilities"])
        self.assertNotIn(catalog.DELIVERY_INSTRUCTION_CAPABILITY,
                         catalog.serialize_model(
                             catalog.get_model(NO_INSTRUCTION_INPUT))["capabilities"])

    def test_the_guidance_is_the_cards_own(self) -> None:
        breeze = catalog.serialize_model(catalog.get_model(BREEZE))["delivery_instruction"]
        self.assertEqual("instruct", breeze["engine_form"])
        self.assertEqual("free_text", breeze["kind"])
        self.assertLessEqual(len(breeze["examples"]), 3)
        self.assertTrue(breeze["examples"])
        self.assertIn("Match the instruction language", breeze["language_rule"])
        syntaxes = [event["syntax"] for event in breeze["inline_events"]]
        for tag in ("(sigh)", "(laugh)", "(cough)", "(clears throat)"):
            self.assertIn(tag, syntaxes)
        for event in breeze["inline_events"]:
            self.assertEqual("text", event["where"],
                             "Breeze performs these only when they are in the TEXT")
        self.assertEqual("https://huggingface.co/BreezeBlue/Breeze-TTS-2",
                         breeze["source_url"])
        self.assertEqual(4.0, breeze["directed_cfg_scale"])

        voxcpm = catalog.serialize_model(catalog.get_model(VOXCPM))["delivery_instruction"]
        self.assertEqual("text_prefix", voxcpm["engine_form"])
        self.assertEqual("https://huggingface.co/openbmb/VoxCPM2", voxcpm["source_url"])
        self.assertEqual([], voxcpm["inline_events"],
                         "the card documents no inline events, so none may be advertised")
        self.assertIsNone(voxcpm["language_rule"],
                          "the card states no language rule for the style text, so none is "
                          "published rather than an invented one")
        self.assertIsNone(voxcpm["directed_cfg_scale"])

    def test_the_lane_contract_publishes_the_request_field(self) -> None:
        for repo in (BREEZE, VOXCPM):
            row = catalog.serialize_model(catalog.get_model(repo))
            parameter = next(p for p in row["lane_contract"]["parameters"]
                             if p["name"] == "delivery_instruction")
            self.assertTrue(parameter["request_only"],
                            "a stored default for a per-line direction would recolour every job")
            self.assertNotIn("default", parameter)
        qwen = catalog.serialize_model(catalog.get_model(NO_INSTRUCTION_INPUT))
        self.assertNotIn("delivery_instruction",
                         [p["name"] for p in qwen["lane_contract"]["parameters"]])

    def test_the_hub_republishes_the_guidance_with_the_capability(self) -> None:
        row = catalog.serialize_model(catalog.get_model(BREEZE))
        controls = capabilities._controls(row, "voice")
        self.assertIn(catalog.DELIVERY_INSTRUCTION_CAPABILITY, controls["capabilities"])
        guidance = controls["delivery_instruction"]
        self.assertEqual("instruct", guidance["engine_form"])
        self.assertEqual(4.0, guidance["directed_cfg_scale"])
        self.assertEqual("breeze_cfg_scale", guidance["directed_cfg_parameter"])
        self.assertEqual(
            catalog.serialize_model(catalog.get_model(BREEZE))["delivery_instruction"]
            ["source_url"], guidance["source_url"])

    def test_a_guidance_the_hub_cannot_read_is_not_republished(self) -> None:
        self.assertIsNone(capabilities._delivery_instruction({"delivery_instruction": {
            "engine_form": "telepathy", "source_url": "https://example.invalid",
            "max_characters": 500}}))
        self.assertIsNone(capabilities._delivery_instruction({"delivery_instruction": {
            "engine_form": "instruct"}}))
        # The bound is the one number a caller needs before writing an instruction, and the Hub
        # publishes nothing in its place rather than a number of its own.
        self.assertIsNone(capabilities._delivery_instruction({"delivery_instruction": {
            "engine_form": "instruct", "source_url": "https://example.invalid",
            "max_characters": None}}))


if __name__ == "__main__":
    unittest.main()
