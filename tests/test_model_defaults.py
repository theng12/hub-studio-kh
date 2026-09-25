"""The one rule for a per-model default, and the one place it is enforced.

A default is accepted only when the lane publishes that parameter and the value is inside the bound
the lane enforces. This pins that rule against the shape `lane_contract` actually publishes, taken
from a real worker rather than invented here.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from components import model_defaults


# The parameters Z-Image-Turbo 6B MLX 4-bit publishes, read from the lane contract itself so this
# fixture cannot drift from the product: see `image/app/backend/generation.lane_contract`.
Z_IMAGE = [
    {"name": "prompt", "type": "string", "required": True, "max_characters": 4000},
    {"name": "aspect_ratio", "type": "string", "required": False,
     "enum": ["16:9", "1:1", "9:16"], "default": "16:9"},
    {"name": "resolution", "type": "string", "required": False, "enum": ["1K"], "default": "1K"},
    {"name": "steps", "type": "integer", "required": False, "minimum": 2, "maximum": 100,
     "default": 9},
    {"name": "guidance", "type": "number", "required": False, "minimum": 0.0, "maximum": 30.0,
     "default": 1.0, "note": "accepted and ignored: this model runs without CFG"},
    {"name": "seed", "type": "integer", "required": False, "minimum": -1,
     "maximum": 4294967295, "default": -1},
]


class EditableSurfaceTests(unittest.TestCase):
    def test_a_required_parameter_is_not_defaultable(self) -> None:
        names = [parameter["name"] for parameter in model_defaults.editable(Z_IMAGE)]
        self.assertNotIn("prompt", names,
                         "a required parameter is per-request content; a stored default for one "
                         "would answer with something nobody wrote this time")

    def test_a_one_option_enum_is_not_a_choice(self) -> None:
        """Chat publishes `model` as `required: false` with one entry in its enum.

        That is honest in the contract — the worker serves one model — and pointless as a control.
        The rule is general rather than a chat special case: the moment a worker serves two models
        the enum grows and the control appears by itself.
        """
        # The exact shape chat publishes today, plus `resolution` from the real image lane, which
        # is also a one-value enum.
        parameters = [dict(parameter) for parameter in Z_IMAGE]
        parameters.insert(0, {"name": "model", "type": "string", "required": False,
                              "enum": ["mlx-community/Qwen3.5-9B-4bit"],
                              "default": "mlx-community/Qwen3.5-9B-4bit"})
        names = [parameter["name"] for parameter in model_defaults.editable(parameters)]
        self.assertNotIn("model", names)
        self.assertNotIn("resolution", names, "the image lane publishes exactly one tier today")
        # ...and the moment there is a second option, it is a control again.
        widened = [dict(parameter) for parameter in parameters]
        next(p for p in widened if p["name"] == "model")["enum"] = ["a", "b"]
        self.assertIn("model", [p["name"] for p in model_defaults.editable(widened)])

    def test_only_what_the_lane_publishes_is_offered(self) -> None:
        names = [parameter["name"] for parameter in model_defaults.editable(Z_IMAGE)]
        self.assertEqual(names, ["aspect_ratio", "steps", "guidance", "seed"],
                         "`resolution` is absent because this lane publishes one value for it")

    def test_the_lanes_own_defaults_are_the_floor(self) -> None:
        # `stated` deliberately covers MORE than `editable`: a parameter with no control is still
        # one the lane applies, so a job built from the effective map must still carry it.
        self.assertEqual(model_defaults.stated(Z_IMAGE),
                         {"aspect_ratio": "16:9", "resolution": "1K", "steps": 9,
                          "guidance": 1.0, "seed": -1})


class ValidationTests(unittest.TestCase):
    def test_a_value_inside_the_bound_is_accepted(self) -> None:
        accepted, refusals = model_defaults.validate(Z_IMAGE, {"steps": 12, "guidance": 2.5})
        self.assertEqual(refusals, {})
        self.assertEqual(accepted, {"steps": 12, "guidance": 2.5})

    def test_each_bad_shape_is_refused_by_name_with_a_reason(self) -> None:
        for values, expected in (
            ({"steps": 1}, "below the lane's minimum"),
            ({"steps": 101}, "above the lane's maximum"),
            ({"steps": "12"}, "expected integer, got str"),
            ({"steps": True}, "got a boolean"),
            ({"guidance": True}, "got a boolean"),
            ({"seed": 2 ** 32}, "above the lane's maximum"),
            ({"aspect_ratio": "7:3"}, "is not one of"),
            # `resolution` is a one-option enum, so it is not a settable parameter at all and is
            # refused for that reason rather than for the value.
            ({"resolution": "4K"}, "publishes no such settable parameter"),
            ({"cfg_scale": 3.5}, "publishes no such settable parameter"),
        ):
            with self.subTest(values=values):
                accepted, refusals = model_defaults.validate(Z_IMAGE, values)
                name = next(iter(values))
                self.assertEqual(accepted, {}, f"{values} must not be accepted")
                self.assertIn(name, refusals)
                self.assertIn(expected, refusals[name], refusals)

    def test_a_non_finite_number_is_refused_rather_than_stored(self) -> None:
        """`json.dumps` writes `Infinity`, which is not JSON.

        Worse than untidy: a stored `NaN` or `Infinity` makes **every later request that omits the
        parameter** fail the route's own validation, so one bad save breaks the lane until someone
        finds the file. It is refused at the door, where the operator can see why.
        """
        for value in (float("inf"), float("-inf"), float("nan")):
            with self.subTest(value=value):
                accepted, refusals = model_defaults.validate(Z_IMAGE, {"guidance": value})
                self.assertEqual(accepted, {})
                self.assertIn("not a finite number", refusals["guidance"])

    def test_an_exclusive_bound_is_enforced(self) -> None:
        """The lane saying "any value but this one" is a rule the store has to keep.

        `top_p` is the live case: the route refuses `0` and a store that accepted it would turn a
        saved default into a 422 on every request that omitted the parameter.
        """
        # The exact shape the chat lane publishes: a FLAG on `minimum`, not a bound of its own.
        parameters = [{"name": "top_p", "type": "number", "required": False, "minimum": 0.0,
                       "minimum_exclusive": True, "maximum": 1.0, "default": 0.95}]
        accepted, refusals = model_defaults.validate(parameters, {"top_p": 0.0})
        self.assertEqual(accepted, {})
        self.assertIn("must be greater than 0.0", refusals["top_p"])
        self.assertEqual(model_defaults.validate(parameters, {"top_p": 0.5})[1], {},
                         "the bound is exclusive at the minimum and inclusive at the maximum, "
                         "which is what the route enforces: `0.0 < top_p <= 1.0`")
        self.assertEqual(model_defaults.validate(parameters, {"top_p": 1.0})[1], {})

    def test_every_problem_is_reported_at_once(self) -> None:
        _accepted, refusals = model_defaults.validate(
            Z_IMAGE, {"steps": 1, "resolution": "4K", "nonsense": 1})
        self.assertEqual(sorted(refusals), ["nonsense", "resolution", "steps"],
                         "an operator who typed three bad values should not have to press Save "
                         "three times to find out")


class StoredDefaultsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.data = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)

    def test_saving_and_reading_back_is_per_model(self) -> None:
        model_defaults.save(self.data, "z-image", {"steps": 12}, Z_IMAGE)
        model_defaults.save(self.data, "flux", {"steps": 4}, Z_IMAGE)
        self.assertEqual(model_defaults.resolved(self.data, "z-image", Z_IMAGE)[0]["steps"], 12)
        self.assertEqual(model_defaults.resolved(self.data, "flux", Z_IMAGE)[0]["steps"], 4,
                         "a default belongs to one model; a blanket value for every image model "
                         "is what per-model defaults exist to prevent")
        self.assertIsNone(model_defaults.resolved(self.data, "other", Z_IMAGE)[1] or None)

    def test_a_refused_value_is_never_stored(self) -> None:
        with self.assertRaises(ValueError) as raised:
            model_defaults.save(self.data, "z-image", {"steps": 101}, Z_IMAGE)
        self.assertIn("above the lane's maximum", str(raised.exception))
        self.assertFalse(model_defaults.settings_file(self.data).exists())
        self.assertEqual(model_defaults.resolved(self.data, "z-image", Z_IMAGE)[0]["steps"], 9)

    def test_a_default_is_layered_over_the_lanes_own(self) -> None:
        model_defaults.save(self.data, "z-image", {"steps": 12}, Z_IMAGE)
        in_force, not_used = model_defaults.resolved(self.data, "z-image", Z_IMAGE)
        self.assertEqual(not_used, {})
        self.assertEqual(in_force["steps"], 12, "the stored value wins for its own parameter")
        self.assertEqual(in_force["aspect_ratio"], "16:9", "everything else stays the lane's")

    def test_a_bound_that_tightened_stops_a_stored_value_being_used(self) -> None:
        """A model update must not turn a stored number into a request the lane would reject."""
        model_defaults.save(self.data, "z-image", {"steps": 60}, Z_IMAGE)
        tightened = [dict(parameter) for parameter in Z_IMAGE]
        next(p for p in tightened if p["name"] == "steps")["maximum"] = 20
        in_force, not_used = model_defaults.resolved(self.data, "z-image", tightened)
        self.assertEqual(in_force["steps"], 9, "the lane's own default is used instead")
        self.assertIn("steps", not_used)
        self.assertIn("above the lane's maximum", not_used["steps"])

    def test_a_withdrawn_parameter_stops_being_used_and_is_not_silently_kept(self) -> None:
        model_defaults.save(self.data, "z-image", {"guidance": 3.0}, Z_IMAGE)
        without = [p for p in Z_IMAGE if p["name"] != "guidance"]
        in_force, not_used = model_defaults.resolved(self.data, "z-image", without)
        self.assertNotIn("guidance", in_force)
        self.assertIn("no such settable parameter", not_used["guidance"])

    def test_clearing_returns_the_model_to_what_the_lane_states(self) -> None:
        model_defaults.save(self.data, "z-image", {"steps": 12}, Z_IMAGE)
        model_defaults.clear(self.data, "z-image")
        self.assertEqual(model_defaults.resolved(self.data, "z-image", Z_IMAGE)[0]["steps"], 9)

    def test_an_unreadable_file_reads_as_no_defaults_rather_than_failing(self) -> None:
        model_defaults.settings_file(self.data).write_text("{ this is not json")
        self.assertEqual(model_defaults.load(self.data), {})
        self.assertEqual(model_defaults.resolved(self.data, "z-image", Z_IMAGE)[0]["steps"], 9)

    def test_the_write_leaves_either_the_old_file_or_the_new_one(self) -> None:
        model_defaults.save(self.data, "z-image", {"steps": 12}, Z_IMAGE)
        model_defaults.save(self.data, "z-image", {"steps": 20}, Z_IMAGE)
        self.assertEqual(
            sorted(p.name for p in self.data.iterdir()), [model_defaults.SETTINGS_NAME],
            "the .tmp sibling must not survive a completed write")
        self.assertEqual(json.loads(model_defaults.settings_file(self.data).read_text()),
                         {"z-image": {"steps": 20}})


class StatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.data = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)

    def test_status_separates_what_is_stated_from_what_the_operator_chose(self) -> None:
        model_defaults.save(self.data, "z-image", {"steps": 12}, Z_IMAGE)
        payload = model_defaults.status(self.data, [("z-image", Z_IMAGE), ("flux", Z_IMAGE)])
        rows = {row["repo"]: row for row in payload["models"]}
        self.assertEqual(rows["z-image"]["chosen"], {"steps": 12})
        self.assertEqual(rows["flux"]["chosen"], {},
                         "a model nobody has set is not reported as chosen")
        self.assertEqual(rows["flux"]["defaults"], rows["flux"]["stated"])
        self.assertIn("resolution", rows["flux"]["defaults"],
                      "a parameter with no control is still one the lane applies")
        # The offered surface is the lane's, so a consumer renders controls from data.
        self.assertEqual([p["name"] for p in rows["z-image"]["parameters"]],
                         ["aspect_ratio", "steps", "guidance", "seed"],
                         "`resolution` has one possible value, so it is not a control")


if __name__ == "__main__":
    unittest.main()


class RefusalReadingTests(unittest.TestCase):
    """One reader for the per-parameter reasons, so five Studios cannot parse them five ways."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.data = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)

    def test_the_reasons_come_back_keyed_by_parameter(self) -> None:
        with self.assertRaises(ValueError) as raised:
            model_defaults.save(self.data, "z-image",
                                {"steps": 101, "nonsense": 1}, Z_IMAGE)
        reasons = model_defaults.refusals(raised.exception)
        self.assertEqual(sorted(reasons), ["nonsense", "steps"])
        self.assertIn("above the lane's maximum", reasons["steps"])
        self.assertIn("no such settable parameter", reasons["nonsense"])

    def test_an_error_that_is_not_ours_reads_as_no_reasons(self) -> None:
        for error in (ValueError("a plain message"), RuntimeError("something else"),
                      ValueError("[1, 2, 3]")):
            with self.subTest(error=error):
                self.assertEqual(model_defaults.refusals(error), {},
                                 "a route must be able to fall back to the message it was given "
                                 "rather than raising while handling a refusal")
