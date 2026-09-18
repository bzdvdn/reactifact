"""adaptive — hybrid scheduler (§26, §24): filter → rank → LLM tie-break → top-k.

Two competing artists could each summarize a `Task`. The runtime's
`scheduler=uncertainty_policy(...)` prunes and orders candidates *before* the
runtime dispatches them — this is not just cosmetic reordering:

- **filter (hard rules)** — a rule prunes candidates that don't fit the domain
  (capability 'b' never picks a tag-"x" task), so they don't even reach ranking;
- **rank (metric)** — the preferred artist is ranked first by the same metric
  that also drives the final decision (`_pick_best`/`_metric` agree by design:
  the scheduling metric *is* the business rule here);
- **LLM tie-break** — within `llm_tie_break`, one structured call orders the
  top pair (skipped offline);
- **rank_limit=1** — only the top-ranked artist is actually *dispatched*; the
  runtime drains its triggering event once per turn (`arun_once`), so the
  loser never gets a second chance to run. Watch the "candidates:" line: only
  one artist ever produces a `Summary`, even though both are eligible;
- **HITL approval** — an answered `PendingQuestion` is pinned to the front and
  never loses to ranking (§60).

    uv run python -m examples.adaptive.main [--tag x] [--text "..."]
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from pydantic import BaseModel
from reactifact import (
    Agent,
    Artifact,
    Consume,
    Context,
    Event,
    PendingQuestion,
    Produce,
    ProduceCall,
    Runtime,
    RuntimeResources,
)
from reactifact.providers import LLMProvider
from reactifact.scheduler import Rule, uncertainty_policy
from reactifact.structured import structured_llm


def build_llm() -> LLMProvider | None:
    """Explicit provider for this demo: OpenRouter (default) or a local
    OpenAI-compatible endpoint; `None` when no key is configured -> offline."""
    import os

    from reactifact.providers import openai_llm, openrouter_llm

    if os.getenv("OPENROUTER_API_KEY"):
        return openrouter_llm(max_tokens=2048)
    if os.getenv("OPENAI_BASE_URL"):
        return openai_llm(
            base_url=os.getenv("OPENAI_BASE_URL"),
            api_key=os.getenv("OPENAI_API_KEY"),
            model=os.getenv("OPENAI_MODEL"),
            max_tokens=2048,
        )
    return None


class Task(BaseModel):
    text: str
    tag: str = ""


class Summary(BaseModel):
    by: str
    text: str


class Final(BaseModel):
    by: str
    text: str


class _Text(BaseModel):
    text: str


def _task_art(context: Context, event: Event | None) -> Artifact[Task] | None:
    artifact = context.get(event.artifact_id) if event is not None else None
    return artifact if isinstance(getattr(artifact, "data", None), Task) else None


def _task_tag(context: Context, event: Event | None) -> str:
    task_art = _task_art(context, event)
    return task_art.data.tag if task_art is not None else ""


# --- hard filter rule: capability 'b' is never applied to tag "x" -----------


def _not_b_for_x(context: Context, agent: Agent, event: Event) -> bool:
    return not (_task_tag(context, event) == "x" and "b" in agent.capabilities)


RULES: list[Rule] = [_not_b_for_x]
LLM_TIE_BREAK = 1.0  # wide: any tie is broken by the model when available
RANK_LIMIT = 1  # top-k: only the top-ranked artist is dispatched at all


# --- deterministic metric: prefer 'b' when the task mentions money ------------


def _metric(context: Context, agent: Agent, event: Event) -> float:
    task_art = _task_art(context, event)
    if (
        "b" in agent.capabilities
        and task_art is not None
        and "money" in task_art.data.text
    ):
        return 1.0
    return 0.0


class _MakeSummary(Produce[Summary]):
    by = ""

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        task_art = _task_art(context, call.event)
        if task_art is None:
            return None
        task_id = task_art.id
        summary_id = f"summary:{task_id}:{self.__class__.by}"
        if context.get(summary_id) is not None:
            return None
        body = await structured_llm(
            context,
            schema=_Text,
            system=f"You summarize the task as artist '{self.__class__.by}'.",
            user=task_art.data.text,
        )
        text = (
            body.text
            if body is not None
            else f"({self.__class__.by}) {task_art.data.text[:60]}"
        )
        self.effects.create(Summary(by=self.__class__.by, text=text), id=summary_id)
        return None


class AProduce(_MakeSummary):
    by = "a"


class BProduce(_MakeSummary):
    by = "b"


class ArtistA(Agent):
    name = "artist_a"
    consumes = [Consume(Task)]
    produces = [AProduce()]
    capabilities = ("summarize", "a")


class ArtistB(Agent):
    name = "artist_b"
    consumes = [Consume(Task)]
    produces = [BProduce()]
    capabilities = ("summarize", "b")


# --- HITL approval + deterministic pick (§60) --------------------------------


def _pick_best(context: Context) -> str:
    """The artist with the highest metric, stable order on ties (§67)."""
    task = next((t for t in context.list_artifacts(Task)), None)
    have_b = any(s.data.by == "b" for s in context.list_artifacts(Summary))
    if have_b and task is not None and "money" in task.data.text:
        return "b"
    return (
        "a" if any(s.data.by == "a" for s in context.list_artifacts(Summary)) else "b"
    )


class Decide(Produce[Final]):
    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        summaries = context.list_artifacts(Summary)
        if len(summaries) < 1 or context.list_artifacts(Final):
            return None
        questions = [
            q
            for q in context.list_artifacts(PendingQuestion)
            if q.data.kind == "approval"
        ]
        if not questions:
            self.effects.ask("Approve the chosen summary?", kind="approval")
            return None
        question = questions[0]
        if not question.data.answered:
            return None
        answer = (question.data.resolution or "").strip().lower()
        self.effects.resume(question, answer)
        ok = (
            answer.startswith("да") or answer.startswith("yes") or answer in {"y", "ok"}
        )
        if ok:
            by_name = _pick_best(context)
            best = next(
                (
                    s.data.text
                    for s in context.list_artifacts(Summary)
                    if s.data.by == by_name
                ),
                "",
            )
            final = self.effects.create(Final(by=by_name, text=best))
        else:
            final = self.effects.create(Final(by="—", text="rejected"))
        for summary in summaries:
            final.link("considered", summary)
        return None


class DecisionAgent(Agent):
    name = "decision"
    consumes = [Consume(Summary), Consume(PendingQuestion)]
    produces = [Decide(), Produce(PendingQuestion), Produce(Final)]


def run(
    *,
    tag: str = "",
    text: str = "Summarize the room plan and the money estimate.",
    llm: LLMProvider | None = None,
) -> Context:
    ctx = Context(resources=RuntimeResources(llm=llm))
    task = ctx.create(Task(text=text, tag=tag))
    scheduler = uncertainty_policy(
        rules=RULES,
        metric=_metric,
        llm_tie_break=LLM_TIE_BREAK,
        llm_system=(
            "You are the adaptive scheduler. Among the two artists, decide "
            "which one should run first. Reply with the index only."
        ),
        rank_limit=RANK_LIMIT,
    )
    agents = [ArtistA(), ArtistB(), DecisionAgent()]
    _arun(Runtime(ctx, agents=agents, scheduler=scheduler))

    questions = [q for q in ctx.pending_questions() if q.data.kind == "approval"]
    if questions:
        # Simulates the human answering: `Context.resume` is the one-line HITL
        # idiom (same as `effects.resume` inside a produce, §60) — it does the
        # `answered/resolution/resolved_at` update for you.
        ctx.resume(questions[0].id, "yes")
        _arun(Runtime(ctx, agents=agents, scheduler=scheduler))
    _ = task
    return ctx


def _arun(runtime: Runtime) -> None:
    asyncio.run(runtime.arun())


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m examples.adaptive.main")
    parser.add_argument("--tag", default="")
    parser.add_argument(
        "--text", default="Summarize the room plan and the money estimate."
    )
    args = parser.parse_args()

    ctx = run(tag=args.tag, text=args.text, llm=build_llm())
    summaries = sorted(ctx.list_artifacts(Summary), key=lambda s: s.data.by)
    finals = ctx.list_artifacts(Final)
    print("adaptive · filter → rank → LLM tie-break → top-k → HITL approval")
    if args.tag == "x":
        print("  rule: capability 'b' excluded for tag 'x' (pruned before ranking)")
    print(
        f"  scheduler: rank_limit={RANK_LIMIT} — only the top-ranked artist "
        "below was ever dispatched; the other never ran"
    )
    print("  candidates:", ", ".join(f"{s.data.by}" for s in summaries) or "—")
    for f in finals:
        print(f"  final ({f.data.by}): {f.data.text[:70]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
