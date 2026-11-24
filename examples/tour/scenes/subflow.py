"""Subflow: a child agent on its own Context, with a trace link and provenance."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from reactifact import (
    Artifact,
    Context,
    Runtime,
    Step,
    create_step_group,
    run_subflow,
)
from reactifact.tracing import Tracer


class Question(BaseModel):
    text: str


class Answer(BaseModel):
    text: str


class Final(BaseModel):
    text: str
    child_run_id: str
    parent_run_id: str
    child_outputs: int
    relations: int


class ChildStep(Step):
    consumes = (Question,)
    produces = (Answer,)

    async def run(self, question: Question) -> Answer:
        return Answer(text=question.text.upper())


class ParentStep(Step):
    consumes = (Question,)
    produces = (Final,)

    async def run(self, context: Context, trigger: Artifact[Question]) -> Final:
        result = await run_subflow(
            context,
            groups=[create_step_group("child", steps=[ChildStep()])],
            input=trigger,
            output_type=Answer,
            link="supported_by",
            source=trigger,
        )
        assert result.output is not None
        return Final(
            text=result.output.data.text,
            child_run_id=result.run_id,
            parent_run_id=result.parent_run_id,
            child_outputs=len(result.outputs),
            relations=len(result.context.relations()),
        )


async def run_subflow_scene() -> dict[str, Any]:
    context = Context()
    context.create(Question(text="hello from subflow"), id="question:1")
    runtime = Runtime(
        context,
        groups=[create_step_group("parent", steps=[ParentStep()])],
        tracer=Tracer(),
    )

    await runtime.arun()
    final = context.latest(Final)
    assert final is not None
    return {
        "final": final.data.text,
        "child_run_id": final.data.child_run_id,
        "parent_run_id": final.data.parent_run_id,
        "same_trace_link": final.data.parent_run_id == runtime.run_id,
        "child_outputs": final.data.child_outputs,
        "relations": final.data.relations,
    }
