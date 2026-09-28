"""Trust & safety in the devops demo — runnable, no LLM or network needed.

Shows how a custom guardrail is written and wired:

    .venv/bin/python examples/devops/safety.py

It prints, in order: a custom domain guardrail (`ProductionChangeGuardrail`)
deciding on three requests, custom secret redaction, an end-to-end run that is
blocked, and the same run under a "flag" policy (allowed + a metric).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

if __package__ in (None, ""):  # running as a script — add src to sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from examples.devops.agents import RouteAgent
from examples.devops.guardrails import (
    ProductionChangeGuardrail,
    devops_guardrail_policy,
)
from examples.devops.models import K8sProblem, UserMsg
from reactifact import Context, Runtime, RuntimeResources
from reactifact.guardrails import GuardrailViolation
from reactifact.metrics import Metrics


def _show(label: str, value: str) -> None:
    print(f"  {label:<48} {value}")


def demo_custom_guardrail() -> None:
    print("1. A custom guardrail is just `name` + `check(data, context)`")
    guardrail = ProductionChangeGuardrail()
    context = Context(resources=RuntimeResources())
    for text in (
        "delete the prod pods",
        "delete the prod pods CHG-1042",
        "restart the staging cluster",
    ):
        decision = guardrail.check(K8sProblem(text=text), context)
        _show(f"{text!r}", f"{decision.action}  {decision.reason}".rstrip())


def demo_redaction() -> None:
    print("\n2. Custom + built-in redaction rewrite the data before commit")
    policy = devops_guardrail_policy()
    context = Context(resources=RuntimeResources())
    decision = policy.evaluate(
        UserMsg(text="db password=hunter2, ping ops@corp.com"), context
    )
    _show("redacted", decision.data.text)


def demo_block_end_to_end() -> None:
    print("\n3. End-to-end: the router's produced K8sProblem is blocked")
    context = Context(resources=RuntimeResources(guardrails=devops_guardrail_policy()))
    context.create(UserMsg(text="delete the prod pods"))
    try:
        asyncio.run(Runtime(context, agents=[RouteAgent()]).arun())
    except GuardrailViolation as exc:
        _show("GuardrailViolation", str(exc))
    _show("K8sProblem committed", str(bool(context.list_artifacts(K8sProblem))))


def demo_flag_metric() -> None:
    print("\n4. Same policy with on_violation='flag': allowed and counted")
    metrics = Metrics()
    context = Context(
        resources=RuntimeResources(
            guardrails=devops_guardrail_policy(on_violation="flag"),
            metrics=metrics,
        )
    )
    context.create(UserMsg(text="delete the prod pods"))
    asyncio.run(Runtime(context, agents=[RouteAgent()]).arun())
    _show("K8sProblem committed", str(bool(context.list_artifacts(K8sProblem))))
    for line in metrics.render().splitlines():
        if "guardrail" in line:
            _show("metric", line)


def main() -> None:
    print("devops trust & safety — custom guardrails\n")
    demo_custom_guardrail()
    demo_redaction()
    demo_block_end_to_end()
    demo_flag_metric()
    print(
        "\nWire it in one line:\n"
        "  RuntimeResources(guardrails=devops_guardrail_policy())"
    )


if __name__ == "__main__":
    main()
