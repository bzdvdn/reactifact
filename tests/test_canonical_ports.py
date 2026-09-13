"""Smoke tests for the canonical-pattern port examples (`examples/<name>/main.py`,
each runnable offline as `python -m examples.<name>.main` — see
docs/en/port-matrix.md). These were previously only eyeballed by hand; a
future core-API change could silently break one of them (the very thing
README/docs point newcomers at as reference code) without CI ever noticing.

Each test just runs the example's own `run()` (offline — no `llm=`) and
asserts its documented final artifact exists, mirroring what each example's
own `main()` prints."""

from examples.map_reduce.main import FinalSummary
from examples.map_reduce.main import run as run_map_reduce
from examples.plan_execute.main import FinalAnswer as PlanExecuteFinalAnswer
from examples.plan_execute.main import PlanStep, StepResult
from examples.plan_execute.main import run as run_plan_execute
from examples.reflection.main import Final as ReflectionFinal
from examples.reflection.main import run as run_reflection
from examples.summarize.main import Summary
from examples.summarize.main import run as run_summarize
from examples.supervisor.main import FinalReply
from examples.supervisor.main import run as run_supervisor
from examples.time_travel.main import Decision
from examples.time_travel.main import run as run_time_travel


def test_reflection_runs_offline_and_finalizes():
    ctx = run_reflection()
    assert ctx.list_artifacts(ReflectionFinal)


def test_map_reduce_runs_offline_and_combines():
    ctx = run_map_reduce()
    finals = ctx.list_artifacts(FinalSummary)
    assert len(finals) == 1
    assert len(finals[0].data.sources) == 3  # CHUNKS


def test_supervisor_runs_offline_and_replies():
    ctx = run_supervisor()
    assert ctx.list_artifacts(FinalReply)


def test_summarize_runs_offline_and_summarizes():
    seed = [
        "We need to finish the room by Friday.",
        "I'll take demolition and rough works in week one.",
        "Budget is 300k; how much is left for materials?",
        "The estimate came to 310k, trim the decor.",
    ]
    ctx = run_summarize(seed=seed)
    assert ctx.list_artifacts(Summary)


def test_time_travel_runs_offline_and_picks():
    ctx = run_time_travel()
    assert ctx.list_artifacts(Decision)


def test_plan_execute_runs_offline_and_finishes():
    ctx = run_plan_execute()
    steps = ctx.list_artifacts(PlanStep)
    results = ctx.list_artifacts(StepResult)
    assert steps
    assert len(results) == len(steps)
    assert ctx.list_artifacts(PlanExecuteFinalAnswer)
