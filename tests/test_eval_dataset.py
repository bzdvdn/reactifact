"""Dataset evaluation layer (§56): datasets, target adapters, gates, summary."""

import asyncio
import json

import pytest
from pydantic import BaseModel
from reactifact import Context, Produce, RuntimeResources
from reactifact.eval import (
    Dataset,
    EvalFailure,
    Example,
    Feedback,
    RunResult,
    answer_coverage,
    answer_present,
    assert_eval,
    default_outputs,
    evaluate,
    from_metric,
    summary_mean,
    summary_pass_rate,
)


class Answer(BaseModel):
    text: str
    sources: list[str] = []


def _context(answer_text: str) -> Context:
    ctx = Context(resources=RuntimeResources())
    ctx.create(Answer(text=answer_text))
    return ctx


def _target(inputs):
    return _context(str(inputs.get("q", "")))


def _dataset() -> Dataset:
    return Dataset.from_list(
        [
            {
                "id": "ex1",
                "inputs": {"q": "alpha"},
                "reference_outputs": {"answer": "alpha"},
            },
            {
                "id": "ex2",
                "inputs": {"q": "beta"},
                "reference_outputs": {"answer": "beta"},
            },
        ]
    )


# --------------------------------------------------------------------------- #
# Dataset / Example
# --------------------------------------------------------------------------- #


def test_example_id_is_content_derived_and_stable():
    first = Example(inputs={"q": "a"})
    same = Example(inputs={"q": "a"})
    other = Example(inputs={"q": "b"})
    assert first.id == same.id
    assert first.id != other.id
    assert Example(inputs={"q": "a"}, id="explicit").id == "explicit"


def test_example_from_dict_accepts_bare_inputs_and_aliases():
    bare = Example.from_dict({"q": "a"})
    assert bare.inputs == {"q": "a"}
    aliased = Example.from_dict(
        {"inputs": {"q": "a"}, "outputs": {"answer": "a"}, "metadata": {"split": "t"}}
    )
    assert aliased.reference_outputs == {"answer": "a"}
    assert aliased.metadata == {"split": "t"}


def test_dataset_version_tracks_content():
    ds = _dataset()
    assert len(ds) == 2
    assert [e.id for e in ds] == ["ex1", "ex2"]
    assert ds["ex1"].inputs == {"q": "alpha"}
    before = ds.version
    ds.examples[0].inputs["q"] = "changed"
    assert ds.version != before


def test_dataset_json_roundtrip(tmp_path):
    path = tmp_path / "qa.json"
    written = _dataset().to_json(path)
    assert json.loads(written)["examples"][0]["id"] == "ex1"
    loaded = Dataset.from_file(path)
    assert len(loaded) == 2
    assert loaded.examples[0].reference_outputs == {"answer": "alpha"}


def test_dataset_jsonl_roundtrip(tmp_path):
    path = tmp_path / "qa.jsonl"
    path.write_text('{"inputs": {"q": "a"}}\n{"inputs": {"q": "b"}}\n')
    loaded = Dataset.from_file(path)
    assert [e.inputs["q"] for e in loaded] == ["a", "b"]


# --------------------------------------------------------------------------- #
# evaluate / target adapters
# --------------------------------------------------------------------------- #


def test_evaluate_scores_every_example():
    report = evaluate(
        _dataset(),
        _target,
        {
            "present": from_metric("answer_present", answer_present),
            "coverage": from_metric("coverage", answer_coverage()),
        },
    )
    assert len(report.results) == 2
    assert report.overall() == 1.0
    assert report.aggregate() == {"answer_present": 1.0, "coverage": 1.0}


def test_evaluate_skips_metric_without_ground_truth():
    ds = Dataset.from_list([{"inputs": {"q": "a"}}])
    report = evaluate(
        ds, _target, {"coverage": from_metric("coverage", answer_coverage())}
    )
    assert report.results[0].skipped == ["coverage"]
    assert report.results[0].metrics == []


def test_target_may_return_outputs_mapping():
    report = evaluate(
        Dataset.from_list([{"inputs": {"q": "hi"}}]),
        lambda inputs: {"answer": inputs["q"]},
        {"has_answer": lambda ei: Feedback("has_answer", 1.0 if ei.outputs else 0.0)},
    )
    assert report.aggregate() == {"has_answer": 1.0}


def test_target_may_be_async():
    async def target(inputs):
        await asyncio.sleep(0)
        return {"answer": str(inputs["q"])}

    report = evaluate(
        Dataset.from_list([{"inputs": {"q": "x"}}]),
        target,
        {"has_answer": lambda ei: 1.0 if ei.outputs else 0.0},
    )
    assert report.overall() == 1.0


def test_target_duck_types_run_object_with_context_and_trace():
    class RunObject:
        def __init__(self, context):
            self.context = context
            self.trace = None

    report = evaluate(
        Dataset.from_list([{"inputs": {"q": "z"}}]),
        lambda inputs: RunObject(_context("z")),
        {"present": from_metric("answer_present", answer_present)},
    )
    assert report.overall() == 1.0


def test_target_tuple_and_bad_return():
    report = evaluate(
        Dataset.from_list([{"inputs": {}}]),
        lambda inputs: (_context("x"), None),
        {"present": from_metric("answer_present", answer_present)},
    )
    assert report.overall() == 1.0

    with pytest.raises(TypeError):
        evaluate(Dataset.from_list([{"inputs": {}}]), lambda inputs: 42, {})


def test_max_concurrency_preserves_order():
    report = evaluate(
        _dataset(),
        _target,
        {"present": from_metric("answer_present", answer_present)},
        max_concurrency=4,
    )
    assert [r.case for r in report.results] == ["ex1", "ex2"]


def test_default_outputs_falls_back_to_artifacts():
    ctx = Context(resources=RuntimeResources())
    ctx.create(Answer(text="x"))
    assert default_outputs(ctx) == {"answer": "x"}
    empty = default_outputs(Context(resources=RuntimeResources()))
    assert empty == {"artifacts": {}}


# --------------------------------------------------------------------------- #
# summary evaluators + gate
# --------------------------------------------------------------------------- #


def test_summary_evaluators():
    report = evaluate(
        _dataset(),
        _target,
        {"present": from_metric("answer_present", answer_present)},
        summary=[
            summary_pass_rate(1.0),
            summary_mean("answer_present"),
            summary_mean("nope"),
        ],
    )
    summary = {m.name: m.score for m in report.summary}
    assert summary["pass_rate"] == 1.0
    assert summary["mean_answer_present"] == 1.0
    assert "nope" not in summary


def test_passed_and_assert_eval_gate():
    good = evaluate(
        _dataset(), _target, {"present": from_metric("answer_present", answer_present)}
    )
    good.assert_passed(1.0)
    good.assert_passed({"answer_present": 0.9})
    assert_eval(good, overall=0.5)

    def empty_target(inputs):
        return Context(resources=RuntimeResources())

    bad = evaluate(
        _dataset(),
        empty_target,
        {"present": from_metric("answer_present", answer_present)},
    )
    assert not bad.passed(0.5)
    # a threshold for an unmeasured key is never met
    assert not good.passed({"unmeasured": 0.1})
    with pytest.raises(EvalFailure):
        assert_eval(bad, {"answer_present": 0.5})


def test_runresult_can_be_returned_directly():
    report = evaluate(
        Dataset.from_list([{"inputs": {}}]),
        lambda inputs: RunResult(outputs={"answer": "ok"}),
        {"has": lambda ei: 1.0 if ei.outputs.get("answer") else 0.0},
    )
    assert report.overall() == 1.0


# --------------------------------------------------------------------------- #
# End-to-end: a ScenarioResult (reactifact.testing) drops in as a target
# --------------------------------------------------------------------------- #


class Number(BaseModel):
    value: int


class Bump(Produce[Number]):
    artifact_type = Number

    async def produce(self, call):
        if call.trigger is not None and call.trigger.data.value < 1:
            call.effects.create(Number(value=call.trigger.data.value + 1))


def test_evaluate_with_scenario_lab_target():
    from reactifact import Consume, create_agent
    from reactifact.eval import trajectory_match
    from reactifact.testing import ScenarioLab

    agent = create_agent("bump", consumes=[Consume(Number)], produces=[Bump()])
    lab = ScenarioLab([agent])
    ds = Dataset.from_list(
        [
            {
                "inputs": {"value": 0},
                "reference_outputs": {"trajectory": ["bump"]},
            }
        ]
    )
    report = evaluate(
        ds,
        lambda inputs: lab.run(Number(value=inputs["value"])),
        {"traj": trajectory_match("strict", steps="agents")},
    )
    assert report.overall() == 1.0
