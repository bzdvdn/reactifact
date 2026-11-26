"""Devops, git-style: parallel investigations on branches, then a merge.

Run: ``python -m examples.devops.branches`` (no API key, no tools).

Two scenarios:

1. clean: one branch per problem type; the branches produce *different* report
   artifacts, so the three-way merge is conflict-free; a compose step then
   consumes all reports and produces the reply;
2. conflict: both branches update the same artifact id differently; `merge`
   raises `MergeConflict` and applies nothing, then an explicit policy resolves
   it — the same shape as resolving a git conflict by hand.

Traces: every runtime writes to the same `TraceStore`; branch runs carry
``metadata={"branch": ..., "parent_run_id": ...}``, so the dashboard can group
them under the planning run instead of showing unrelated turns.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from reactifact import (
    SUPPORTED_BY,
    Budget,
    Context,
    Emit,
    MergeConflict,
    Runtime,
    RuntimeResources,
    Step,
)
from reactifact.tracing import Tracer, TraceStore

from .models import (
    AnsibleProblem,
    AnsibleReport,
    ChatReply,
    GitlabProblem,
    GitlabReport,
    K8sProblem,
    K8sReport,
    UserMsg,
)


class Note(BaseModel):
    text: str


class MergeSignal(BaseModel):
    """An event that starts the compose step after the merge."""


class Plan(Step):
    """One question → three independent problem artifacts."""

    consumes = (UserMsg,)
    produces = (K8sProblem, GitlabProblem, AnsibleProblem)

    async def run(self, msg: UserMsg) -> list[Emit]:
        qid = f"q:{msg.text[:12]}"
        return [
            Emit(K8sProblem(text=msg.text, query_id=qid), id=f"k8s:{qid}"),
            Emit(GitlabProblem(text=msg.text, query_id=qid), id=f"gitlab:{qid}"),
            Emit(AnsibleProblem(text=msg.text, query_id=qid), id=f"ansible:{qid}"),
        ]


class InvestigateK8s(Step):
    consumes = (K8sProblem,)
    produces = (K8sReport,)

    async def run(self, problem: K8sProblem) -> K8sReport:
        return K8sReport(
            query_id=problem.query_id,
            text="k8s: worker-2f1 is OOMKilled; rollout is backing off",
        )


class InvestigateGitlab(Step):
    consumes = (GitlabProblem,)
    produces = (GitlabReport,)

    async def run(self, problem: GitlabProblem) -> GitlabReport:
        return GitlabReport(
            query_id=problem.query_id,
            text="gitlab: pipeline #4821 failed on deploy; MR !1842 merged",
        )


class InvestigateAnsible(Step):
    consumes = (AnsibleProblem,)
    produces = (AnsibleReport,)

    async def run(self, problem: AnsibleProblem) -> AnsibleReport:
        return AnsibleReport(
            query_id=problem.query_id,
            text="ansible: playbook dry-run failed on one host (role: web)",
        )


class Compose(Step):
    """After merge: all three reports are in one context, compose the reply."""

    consumes = (
        K8sReport,
        MergeSignal,
    )
    produces = (ChatReply,)
    provenance = SUPPORTED_BY

    async def run(
        self, k8s: K8sReport, gitlab: GitlabReport, ansible: AnsibleReport
    ) -> ChatReply:
        return ChatReply(
            query_id=k8s.query_id,
            text="\n".join([k8s.text, gitlab.text, ansible.text]),
        )


def _run(
    context: Context,
    steps: list[Step],
    tracer: Tracer,
    *,
    branch: str,
    parent: str = "",
) -> str:
    runtime = Runtime(
        context,
        steps=steps,
        budget=Budget(max_runs=10),
        tracer=tracer,
        trace_scope="devops",
    )
    runtime.run(request={"trace_metadata": {"branch": branch, "parent_run_id": parent}})
    return runtime.run_id


def reactive_scenario(tracer: Tracer) -> Context:
    """Same work, no branches: one run, steps fan out in one generation."""
    base = Context(resources=RuntimeResources())
    base.create(UserMsg(text="checkout is down: k8s, gitlab and ansible please"))
    runtime = Runtime(
        base,
        steps=[
            Plan(),
            InvestigateK8s(),
            InvestigateGitlab(),
            InvestigateAnsible(),
            Compose(),
        ],
        budget=Budget(max_runs=10),
        tracer=tracer,
        trace_scope="devops",
    )
    runtime.run(request={"trace_metadata": {"branch": "reactive"}})
    return base


def clean_scenario(tracer: Tracer) -> tuple[Context, str]:
    """Plan on the base, investigate on three forks, merge, compose."""
    base = Context(resources=RuntimeResources())
    base.create(UserMsg(text="checkout is down: k8s, gitlab and ansible please"))

    base_run = _run(base, [Plan()], tracer, branch="plan")

    forks: list[tuple[str, Context]] = []
    problem_types = {
        "k8s": K8sProblem,
        "gitlab": GitlabProblem,
        "ansible": AnsibleProblem,
    }
    for name, step in (
        ("k8s", InvestigateK8s()),
        ("gitlab", InvestigateGitlab()),
        ("ansible", InvestigateAnsible()),
    ):
        fork = base.branch(name=name)
        problem = fork.latest(problem_types[name])
        assert problem is not None
        fork.touch(problem.id)  # branch-local event
        _run(fork, [step], tracer, branch=name, parent=base_run)
        forks.append((name, fork))

    for name, fork in forks:
        base.merge(fork, message=f"merge {name}")
    base.create(MergeSignal())
    _run(base, [Compose()], tracer, branch="compose", parent=base_run)
    return base, base_run


def conflict_scenario(tracer: Tracer) -> Context:
    """Both forks edit the same id → MergeConflict, then an explicit policy."""
    base = Context(resources=RuntimeResources())
    base.create(Note(text="base"), id="note:1")

    left = base.branch(name="left")
    right = base.branch(name="right")
    left.update("note:1", Note(text="left edit"))
    right.update("note:1", Note(text="right edit"))

    base.merge(left)
    try:
        base.merge(right)
    except MergeConflict as conflict:
        message = conflict.conflicts[0]
        print(f"  conflict: {message}")
    else:  # pragma: no cover - the demo is supposed to conflict
        raise AssertionError("expected MergeConflict")

    base.merge(
        right,
        policies={Note: lambda mine, theirs: Note(text=f"{mine.text} + {theirs.text}")},
    )
    note = base.latest(Note)
    assert note is not None
    print(f"  resolved: {note.data.text!r}")
    return base


async def _print_traces(store: TraceStore, title: str) -> None:
    roots = (
        await store.query(parent_run_id="", sort="started_at", order="asc", limit=50)
    )["items"]
    print(f"\n{title} traces (tree):")

    async def emit(item: Any, depth: int = 0) -> None:
        trace = await store.get(item["id"])
        if trace is None:
            return
        indent = "  " * depth
        print(
            f"{indent}{trace.id[:8]} branch={trace.branch or '?':<8} "
            f"outcome={trace.outcome}"
        )
        for span in trace.spans:
            steps = ", ".join(run.name for run in span.produces)
            print(
                f"{indent}  {span.agent:<10} on "
                f"{span.event_artifact_type or '-':<14} → {steps or '-'}"
            )
        for child in await store.children(trace.id, limit=50):
            await emit(child, depth + 1)

    for root in roots:
        await emit(root)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        reactive_store = TraceStore(str(Path(tmp) / "reactive.db"))
        print("reactive fan-in (no branches):")
        context = reactive_scenario(Tracer(store=reactive_store))
        reply = context.latest(ChatReply)
        assert reply is not None
        print(f"  answer: {reply.data.text.splitlines()[0]} …")
        print("  provenance:")
        for link in context.why(reply):
            print("    " * link.depth, link.relation, type(link.artifact.data).__name__)
        asyncio.run(_print_traces(reactive_store, "reactive"))

        store = TraceStore(str(Path(tmp) / "traces.db"))
        tracer = Tracer(store=store)

        print("\nclean merge:")
        context, _ = clean_scenario(tracer)
        reply = context.latest(ChatReply)
        assert reply is not None
        print("  answer:")
        for line in reply.data.text.splitlines():
            print(f"    {line}")
        print(
            "  reports:",
            ", ".join(
                artifact.id
                for report in (K8sReport, GitlabReport, AnsibleReport)
                for artifact in context.list_artifacts(report)
            ),
        )
        print("  provenance:")
        for link in context.why(reply):
            print("    " * link.depth, link.relation, type(link.artifact.data).__name__)

        print("\nconflicting merge:")
        conflict_scenario(tracer)

        asyncio.run(_print_traces(store, "branch/merge"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
