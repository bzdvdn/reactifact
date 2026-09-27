"""Trajectory-match evaluator (§56): typed-path matching over a run trace."""

import pytest
from pydantic import BaseModel
from reactifact import Context, RuntimeResources
from reactifact.eval import (
    Dataset,
    EvalInput,
    Example,
    evaluate,
    trajectory_match,
)
from reactifact.tracing.models import AgentSpan, ArtifactRef, RunTrace


class Answer(BaseModel):
    text: str


class Run:
    def __init__(self, context, trace):
        self.context = context
        self.trace = trace


def _run(agents, *, writes=()) -> Run:
    context = Context(resources=RuntimeResources())
    context.create(Answer(text="done"))
    trace = RunTrace()
    for agent in agents:
        trace.add_span(
            AgentSpan(
                agent=agent,
                writes=[
                    ArtifactRef(artifact_id=f"{agent}:{t}", op_type=op, data_type=t)
                    for op, t in writes
                ],
            )
        )
    return Run(context, trace)


def _score(agents, expected, mode="strict", **kwargs):
    ds = Dataset.from_list(
        [{"inputs": {}, "reference_outputs": {"trajectory": expected}}]
    )
    report = evaluate(
        ds, lambda inputs: _run(agents), {"traj": trajectory_match(mode, **kwargs)}
    )
    return report.results[0].metrics[0].score if report.results[0].metrics else None


def test_strict_requires_same_order():
    assert (
        _score(["planner", "search", "answer"], ["planner", "search", "answer"]) == 1.0
    )
    assert (
        _score(["search", "planner", "answer"], ["planner", "search", "answer"]) == 0.0
    )


def test_unordered_ignores_order():
    assert (
        _score(
            ["search", "planner", "answer"],
            ["planner", "search", "answer"],
            mode="unordered",
        )
        == 1.0
    )


def test_subset_and_superset():
    # actual ⊆ expected: no unexpected steps
    assert _score(["planner"], ["planner", "search"], mode="subset") == 1.0
    assert _score(["planner", "extra"], ["planner", "search"], mode="subset") == 0.0
    # expected ⊆ actual: at least the required steps
    assert (
        _score(["planner", "search", "extra"], ["planner", "search"], mode="superset")
        == 1.0
    )
    assert _score(["planner"], ["planner", "search"], mode="superset") == 0.0


def test_failure_records_a_comment():
    ds = Dataset.from_list(
        [{"inputs": {}, "reference_outputs": {"trajectory": ["a", "b"]}}]
    )
    report = evaluate(ds, lambda inputs: _run(["a"]), {"traj": trajectory_match()})
    metric = report.results[0].metrics[0]
    assert metric.score == 0.0
    assert "actual=['a']" in metric.note


def test_missing_reference_is_skipped():
    ds = Dataset.from_list([{"inputs": {}}])
    report = evaluate(ds, lambda inputs: _run(["a"]), {"traj": trajectory_match()})
    assert report.results[0].skipped == ["traj"]


def test_write_steps_and_custom_extractor():
    # writes: "create:Answer"
    ds = Dataset.from_list(
        [{"inputs": {}, "reference_outputs": {"trajectory": ["create:Answer"]}}]
    )
    report = evaluate(
        ds,
        lambda inputs: _run(["answer"], writes=[("create", "Answer")]),
        {"traj": trajectory_match("strict", steps="writes")},
    )
    assert report.results[0].metrics[0].score == 1.0

    def first_step(ei: EvalInput) -> list[str]:
        return [ei.outputs.get("answer", "")]

    report = evaluate(
        Dataset.from_list(
            [{"inputs": {}, "reference_outputs": {"trajectory": ["done"]}}]
        ),
        lambda inputs: _run(["answer"]),
        {"traj": trajectory_match(steps=first_step)},
    )
    assert report.results[0].metrics[0].score == 1.0


def test_unknown_mode_and_steps_raise():
    dataset = Dataset.from_list(
        [{"inputs": {}, "reference_outputs": {"trajectory": ["a"]}}]
    )
    with pytest.raises(ValueError, match="unknown trajectory mode"):
        evaluate(dataset, lambda i: _run(["a"]), {"t": trajectory_match("nope")})
    with pytest.raises(ValueError, match="unknown trajectory steps"):
        evaluate(dataset, lambda i: _run(["a"]), {"t": trajectory_match(steps="nope")})


def test_no_trace_yields_empty_path():
    example = Example(inputs={}, reference_outputs={"trajectory": []})
    feedback = trajectory_match("strict")(EvalInput(example=example))
    assert feedback is not None
    assert feedback.score == 1.0
