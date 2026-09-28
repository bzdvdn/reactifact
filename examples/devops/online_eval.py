"""Online-eval for the devops demo — score *served* runs, offline.

Offline eval needs a curated dataset; online eval samples the runs the
assistant actually served from a `TraceStore` and scores them with the same
`Evaluator`s. Because a trace is truncated, this demo writes two **trace-level**
evaluators (no `Context` needed) rather than `core_metrics`:

- `routed()` — the run produced a specialist problem or a help reply;
- `routed_to("K8sProblem")` — a stricter check that flags a mis-route.

`web.py` mounts `create_online_eval_router(...)` so the running app can score
its own traces (`POST /api/evals/run`), tagging passes `eval` and failures
`eval:failed` for the trace dashboard. Run this module for a no-LLM demo:

    .venv/bin/python examples/devops/online_eval.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # running as a script — add src to sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from examples.devops.agents import RouteAgent
from examples.devops.models import UserMsg
from reactifact import Context, Runtime, RuntimeResources
from reactifact.eval import (
    EvalInput,
    Evaluator,
    OnlineEvalConfig,
    OnlineEvaluator,
    no_errors,
    online_evaluate,
)
from reactifact.metrics import Metrics
from reactifact.tracing import Tracer, TraceStore

#: Artifact types a routed run may produce (the specialist problems + help).
_PROBLEMS = ("K8sProblem", "GitlabProblem", "AnsibleProblem", "ChatReply")


def _written_types(eval_input: EvalInput) -> set[str]:
    """The artifact type names a run wrote, read off the trace-derived outputs."""
    artifacts = eval_input.outputs.get("artifacts")
    return set(artifacts) if isinstance(artifacts, dict) else set()


def routed() -> Evaluator:
    """1.0 when the run produced a specialist problem or a help reply."""

    def evaluate(eval_input: EvalInput) -> float:
        return 1.0 if _written_types(eval_input) & set(_PROBLEMS) else 0.0

    evaluate.__name__ = "routed"
    return evaluate


def routed_to(*data_types: str) -> Evaluator:
    """1.0 only when one of `data_types` was written — a stricter route check."""

    def evaluate(eval_input: EvalInput) -> float:
        return 1.0 if _written_types(eval_input) & set(data_types) else 0.0

    evaluate.__name__ = "routed_to[" + ",".join(data_types) + "]"
    return evaluate


def devops_online_config(**overrides: Any) -> OnlineEvalConfig:
    """The ops online-eval config: route + no-errors, tagging passes and failures."""
    options: dict[str, Any] = {
        "evaluators": {"routed": routed(), "clean": no_errors()},
        "sample_rate": 1.0,
        "tag": "eval",
        "failure_tag": "eval:failed",
    }
    options.update(overrides)
    return OnlineEvalConfig(**options)


def build_evaluator(
    store: TraceStore,
    *,
    metrics: Metrics | None = None,
    **overrides: Any,
) -> OnlineEvaluator:
    """An `OnlineEvaluator` over `store` — what `web.py` mounts a router for."""
    return OnlineEvaluator(store, devops_online_config(**overrides), metrics=metrics)


def _run_offline(store: TraceStore, text: str) -> None:
    """One devops run with no LLM (the router's keyword fallback picks a specialist)."""
    context = Context(resources=RuntimeResources())
    context.create(UserMsg(text=text))
    asyncio.run(
        Runtime(context, agents=[RouteAgent()], tracer=Tracer(store=store)).arun()
    )


def main() -> None:
    print("devops online-eval — sampling the runs that were actually served\n")
    with tempfile.TemporaryDirectory() as tmp:
        store = TraceStore(str(Path(tmp) / "traces.db"))
        for text in (
            "pods are crashlooping",
            "gitlab pipeline failed",
            "run the ansible playbook",
        ):
            _run_offline(store, text)

        report = asyncio.run(online_evaluate(store, devops_online_config()))
        print(report.render())
        tagged = asyncio.run(store.query(tags=["eval"]))
        print(f"\nruns tagged 'eval': {tagged['total']}")

        print("\nA stricter route check flags the two non-k8s runs:\n")
        strict = devops_online_config(evaluators={"k8s_only": routed_to("K8sProblem")})
        report = asyncio.run(online_evaluate(store, strict))
        print(report.render())
        failed = asyncio.run(store.query(tags=["eval:failed"]))
        print(f"\nruns tagged 'eval:failed': {failed['total']}")

    print(
        "\nMount it on the app (web.py does this):\n"
        "  app.include_router(create_online_eval_router(build_evaluator(store)))\n"
        "  # then: POST /api/evals/run  -> scores the latest traces"
    )


if __name__ == "__main__":
    main()
