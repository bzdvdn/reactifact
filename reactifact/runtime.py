import asyncio
import logging
import sys
import time
from collections.abc import AsyncIterator, Callable
from typing import cast

from .agents import Agent
from .budget import Budget, RunOutcome, RunStats
from .commit import Commit, Read, Write
from .context import Context
from .effects import Effects, current_effects, reset_effects, set_effects
from .events import Event
from .patches import Create, Delete, Link, Patch, Unlink, Update
from .scheduler import Scheduler
from .session import Session
from .streaming import ProgressEvent
from .tracing.models import AgentSpan
from .tracing.tracer import CompositeTracer, RunTracer, Tracer

logger = logging.getLogger(__name__)

#: A scheduled patch with its trigger reads and (optional) trace span.
PatchWork = tuple[Patch, Agent, list[Read], AgentSpan | None]
#: One agent execution result: patch (if any), the agent/event/reads that
#: produced it, latency, and the exception if the produce raised and
#: `isolate_errors` was set.
AgentResult = tuple[Patch | None, Agent, Event, list[Read], float, BaseException | None]


class Runtime:
    def __init__(
        self,
        context: Context,
        agents: list[Agent] | None = None,
        max_concurrency: int | None = None,
        session: "Session | None" = None,
        budget: Budget | None = None,
        tracer: Tracer | list[Tracer] | None = None,
        scheduler: Scheduler | None = None,
        isolate_errors: bool = False,
        on_agent_error: Callable[[Agent, Event, BaseException], None] | None = None,
    ):
        self.context = context
        self.agents = agents or []
        self.max_concurrency = max_concurrency
        self.session = session
        self.budget = budget
        self.scheduler = scheduler
        # §69 "make illegal states visible" default: an agent's exception still
        # propagates out of arun()/astream() unless isolate_errors=True — opt in
        # to resilience explicitly rather than silently swallowing bugs.
        self.isolate_errors = isolate_errors
        self.on_agent_error = on_agent_error
        self.tracer: Tracer | CompositeTracer | None = (
            tracer
            if isinstance(tracer, Tracer) or tracer is None
            else CompositeTracer(tracer)
        )
        # Tracing is fully delegated to RunTracer (§54): span/trace building,
        # the RecordingLLM wrap, and the task→agent attribution it needs all
        # live there — Runtime just calls into it at a few points below.
        self._trace = RunTracer(context, self.tracer)
        self.outcome: RunOutcome = RunOutcome.COMPLETED
        self.last_stats: RunStats | None = None
        self._runs_used = 0
        self._deadline: float | None = None
        self._active_budget: Budget | None = None
        self._turn_started = False
        self._turn_started_at = 0.0
        self._no_runs_warned = False
        self._errors_used = 0
        # Not reentrant: `arun`/`arun_once`/`astream` all mutate shared,
        # instance-level turn state (`_runs_used`, `outcome`, `_deadline`,
        # and `context.resources.budget`/`budget_deadline` — a resource
        # *shared* by the Context). Two concurrent calls on the *same*
        # Runtime (e.g. `asyncio.gather(runtime.arun(), runtime.arun())`)
        # would race on that state — one call's budget/deadline silently
        # clobbers the other's mid-flight. Guarded in `_enter_turn`/
        # `_exit_turn`, checked only at the public entry points; `arun`'s own
        # internal loop calls `_arun_once_impl` directly (unguarded — it is
        # already inside the guarded region, not a second concurrent call).
        self._in_turn = False

    def _enter_turn(self) -> None:
        if self._in_turn:
            raise RuntimeError(
                "Runtime.arun()/arun_once()/astream() is not reentrant: this "
                "Runtime is already processing a turn (a concurrent call on "
                "the same instance, e.g. via asyncio.gather). Use a separate "
                "Runtime per concurrent request — they can share "
                "Context.resources — or await the in-flight call first."
            )
        self._in_turn = True

    def _exit_turn(self) -> None:
        self._in_turn = False

    def register(self, agent: Agent) -> None:
        self.agents.append(agent)

    def _begin_turn(self, budget: Budget | None) -> None:
        self._runs_used = 0
        self._errors_used = 0
        self.outcome = RunOutcome.COMPLETED
        self._deadline = None
        self._turn_started_at = time.monotonic()
        self._active_budget = budget or self.budget
        if (
            self._active_budget is not None
            and self._active_budget.max_seconds is not None
        ):
            self._deadline = self._turn_started_at + self._active_budget.max_seconds
        # expose budget visibility to agents (LLM agent counts max_tool_calls;
        # a multi-step blocking loop like ToolUse._loop checks `budget_deadline`
        # between its own internal steps — the runtime only enforces max_seconds
        # *between* agent runs, so a produce with its own internal loop would
        # otherwise never see it until the whole produce() returns).
        self.context.resources.budget = self._active_budget
        self.context.resources.budget_deadline = self._deadline
        self._turn_started = True
        self._trace.begin_turn(
            session_id=self.session.session_id if self.session is not None else ""
        )

    def _budget_exhausted(self) -> bool:
        if self._deadline is not None and time.monotonic() >= self._deadline:
            self.outcome = RunOutcome.BUDGET_TIME_EXCEEDED
            return True
        if (
            self._active_budget is not None
            and self._active_budget.max_runs is not None
            and self._runs_used >= self._active_budget.max_runs
        ):
            self.outcome = RunOutcome.BUDGET_RUNS_EXCEEDED
            return True
        return False

    def _validate_patch_types(self, patch: Patch, agent: Agent) -> None:
        """Checks that all Create operations match the agent's produces."""
        if agent.produces is None:
            return  # no restrictions
        allowed_types = {
            p.artifact_type for p in agent.produces if p.artifact_type is not None
        }
        if not allowed_types:
            return
        for op in patch.operations:
            if isinstance(op, Create) and type(op.data) not in allowed_types:
                raise ValueError(
                    f"Agent '{agent.name}' created artifact of type {type(op.data).__name__}, "
                    f"which is not declared in produces: {[t.__name__ for t in allowed_types]}"
                )

    async def arun_once(self, budget: Budget | None = None) -> int:
        self._enter_turn()
        try:
            return await self._arun_once_impl(budget)
        finally:
            self._exit_turn()

    async def _arun_once_impl(self, budget: Budget | None = None) -> int:
        if not self._turn_started:
            self._begin_turn(budget)
        events = self.context.drain_events()
        if not events:
            return 0
        if self._budget_exhausted():
            return 0

        # Collect work (event, agent), accounting for priority: agents with lower
        # values run earlier, "finishers" last.
        work: list[tuple[Agent, Event, list[Read]]] = []
        ordered_agents = sorted(self.agents, key=lambda a: a.priority)
        for event in events:
            if self._budget_exhausted():
                break
            for agent in ordered_agents:
                if self._budget_exhausted():
                    break
                if agent.matches(event, self.context):
                    reads = self._collect_reads(agent, event)
                    work.append((agent, event, reads))

        # Limit the number of runs by the max_runs budget. Set the budget_runs_exceeded
        # outcome only when the limit is actually reached, not when the event simply
        # has no subscribed agents.
        if self.scheduler is not None and work:
            work = await self.scheduler(self.context, work)

        active = self._active_budget
        if active is not None and active.max_runs is not None:
            remaining = active.max_runs - self._runs_used
            if remaining <= 0:
                self.outcome = RunOutcome.BUDGET_RUNS_EXCEEDED
                work = []
            else:
                work = work[:remaining]

        results = await self._dispatch(work)
        patches_to_apply, runs = self._get_patches_to_apply(results)
        await self._commit_patches_to_apply(patches_to_apply)
        return runs

    async def _dispatch(
        self, work: list[tuple[Agent, Event, list[Read]]]
    ) -> list[AgentResult]:
        """Runs the generation's workers.

        Sequential when there is nothing to parallelize (no runtime cap and no
        per-agent limits); otherwise concurrent with: the global
        `max_concurrency` cap plus per-agent `concurrency_limit` tiers — so
        LLM-bound producers can be throttled separately from cheap I/O.
        Semaphores are acquired global-first (fixed order avoids deadlocks) and
        released in reverse.
        """
        if not work:
            return []
        limiters = {
            agent.concurrency_limit
            for agent, _, _ in work
            if agent.concurrency_limit is not None and agent.concurrency_limit > 0
        }
        if self.max_concurrency is None and not limiters:
            return [await self._execute(item) for item in work]

        global_semaphore = (
            asyncio.Semaphore(self.max_concurrency)
            if self.max_concurrency is not None
            else None
        )
        limit_semaphores = {limit: asyncio.Semaphore(limit) for limit in limiters}

        async def _worker(item: tuple[Agent, Event, list[Read]]) -> AgentResult:
            agent = item[0]
            acquired: list[asyncio.Semaphore] = []
            tier = agent.concurrency_limit
            if tier is not None and tier > 0:
                acquired.append(limit_semaphores[tier])
            if global_semaphore is not None:
                acquired.append(global_semaphore)
            for semaphore in acquired:
                await semaphore.acquire()
            try:
                return await self._execute(item, None)
            finally:
                for semaphore in reversed(acquired):
                    semaphore.release()

        # return_exceptions=True: without it, the first sibling to raise makes
        # `gather` propagate immediately while the other already-scheduled
        # tasks keep running unawaited in the background (a classic asyncio
        # gotcha) — with concurrent LLM-bound agents that means real,
        # in-flight API calls nobody is waiting on anymore, and whose result
        # (if it lands after this generation's effects slot is gone) has
        # nowhere safe to go. Collecting exceptions instead means `gather`
        # always waits for every sibling to actually finish before this
        # method returns; the first exception (if `isolate_errors=False`) is
        # then re-raised here, unwrapped — same type callers see today.
        results = await asyncio.gather(
            *(_worker(item) for item in work), return_exceptions=True
        )
        for result in results:
            if isinstance(result, BaseException):
                raise result
        return cast("list[AgentResult]", results)

    def _get_patches_to_apply(
        self, results: list[AgentResult]
    ) -> tuple[list[PatchWork], int]:
        """Turns agent results into the patches to apply (+ the run count).

        Executions that changed nothing are not applied and not traced (a
        monotonic flood of "checked, no work" spans would make traces
        unreadable, §54), but they still count toward the run budget.
        """
        patches_to_apply: list[PatchWork] = []
        runs = 0
        for patch, agent, event, reads, latency, error in results:
            if self._budget_exhausted():
                break
            runs += 1
            self._runs_used += 1
            if error is not None:
                self._errors_used += 1
                self._trace.record_span(agent, event, reads, latency, error=error)
                continue
            if patch is None or patch.is_empty():
                continue
            span = self._trace.record_span(agent, event, reads, latency)
            self._validate_patch_types(patch, agent)
            patches_to_apply.append((patch, agent, reads, span))
        return patches_to_apply, runs

    async def _commit_patches_to_apply(self, patches_to_apply: list[PatchWork]) -> None:
        """Applies each patch as a commit: provenance, span writes, persistence."""
        for patch, agent, reads, span in patches_to_apply:
            commit = Commit(
                author=agent.name,
                message=f"Applied patch from agent '{agent.name}'",
                operations=patch.operations,
                reads=reads,
            )
            commit.writes = self._apply_patch(patch, commit)
            if span is not None:
                span.writes = self._trace.write_refs(patch, commit.writes)
                span.relations = self._trace.relation_refs(patch)
            self.context.log_commit(commit)
            if self.session is not None:
                # git-like persist after each commit: the session survives a crash
                # at the boundary of any agent generation. Session backends are
                # async-native (checkpoints.py) — a slow file/SQLite write yields
                # to other concurrent agent runs instead of blocking a thread.
                await self.session.save()

    def _collect_reads(self, agent: Agent, event: Event) -> list[Read]:
        """Records consumed artifacts: the trigger event + inputs per consumes.

        This is the actual link of an agent to its ancestors (git-like provenance),
        built by the runtime rather than by the graph author.
        """
        reads: list[Read] = []
        seen: set[str] = set()
        trigger_artifact = self.context.get(event.artifact_id)
        if trigger_artifact is not None:
            reads.append(Read(trigger_artifact.id, trigger_artifact.version))
            seen.add(trigger_artifact.id)
        for artifact in agent.collect_inputs(self.context):
            if artifact.id not in seen:
                reads.append(Read(artifact.id, artifact.version))
                seen.add(artifact.id)
        return reads

    async def _execute(
        self,
        item: tuple[Agent, Event, list[Read]],
        semaphore: asyncio.Semaphore | None = None,
    ) -> AgentResult:
        """Runs a single agent (parallel section of a generation).

        Agents in the same generation work on the same snapshot:
        patches are applied only after all runs finish, so a parallel
        fan-out is safe for provenance (§42, §34).

        By default an exception raised by a produce propagates out of this
        call (and from `arun`/`astream`) — a bug in one agent is not hidden.
        With `isolate_errors=True`, the exception is caught here instead: the
        agent contributes no patch this generation, `on_agent_error` (if set)
        is called, and the run continues so unrelated agents still make
        progress.
        """
        agent, event, reads = item
        started = time.monotonic()
        task = asyncio.current_task()
        self._trace.register_task(task, agent.name)
        effects_token = set_effects(Effects(self.context))
        slot: Effects | None = None
        patch: Patch | None = None
        error: BaseException | None = None
        try:
            try:
                if semaphore is not None:
                    async with semaphore:
                        patch = await agent.run(event, self.context)
                else:
                    patch = await agent.run(event, self.context)
                slot = current_effects()
            except Exception as exc:
                if not self.isolate_errors:
                    raise
                error = exc
                logger.warning(
                    "Agent %r raised %r; isolated (isolate_errors=True)",
                    agent.name,
                    exc,
                )
                if self.on_agent_error is not None:
                    self.on_agent_error(agent, event, exc)
        finally:
            reset_effects(effects_token)
            self._trace.unregister_task(task)
        latency = (time.monotonic() - started) * 1000
        if error is not None:
            return None, agent, event, reads, latency, error
        # Effects authored in produce() compile to the patch. A produce's own
        # effects happen *after* its returned patch (produce order), so an
        # update that captured the pre-effect state cannot regress later effects.
        if slot is not None and not slot.is_empty():
            combined = Patch()
            if patch is not None:
                combined.merge(patch)
            combined.merge(slot.to_patch())
            patch = combined
        return patch, agent, event, reads, latency, None

    async def arun(
        self,
        max_iterations: int = 100,
        budget: Budget | None = None,
    ) -> int:
        self._enter_turn()
        try:
            return await self._arun_impl(max_iterations, budget)
        finally:
            self._exit_turn()

    async def _arun_impl(self, max_iterations: int, budget: Budget | None) -> int:
        self._begin_turn(budget)
        active = self._active_budget
        limit = (
            active.max_iterations
            if active is not None and active.max_iterations is not None
            else max_iterations
        )
        total_runs = 0
        for _ in range(limit):
            if self._budget_exhausted():
                break
            runs = await self._arun_once_impl()
            total_runs += runs
            if runs == 0:
                break
        else:
            if self.outcome == RunOutcome.COMPLETED:
                self.outcome = RunOutcome.ITERATIONS_EXHAUSTED
        self.last_stats = RunStats(
            runs=total_runs,
            iterations=limit,
            outcome=self.outcome,
            duration=time.monotonic() - self._turn_started_at,
            errors=self._errors_used,
        )
        if total_runs == 0:
            self._warn_no_runs()
        await self._trace.end_turn(
            session_id=self.session.session_id if self.session is not None else "",
            duration_ms=time.monotonic() - self._turn_started_at,
            outcome=self.outcome.value,
        )
        return total_runs

    def run_once(self) -> int:
        return asyncio.run(self.arun_once())

    def run(self, max_iterations: int = 100, budget: Budget | None = None) -> int:
        return asyncio.run(self.arun(max_iterations, budget))

    def _warn_no_runs(self) -> None:
        if self._no_runs_warned:
            return
        self._no_runs_warned = True
        if not self.agents:
            return
        consumed = sorted(
            {
                c.artifact_type.__name__
                for agent in self.agents
                for c in (agent.consumes or [])
                if c.artifact_type is not None
            }
        )
        hint = (
            f" None of the {len(self.agents)} agents ran — nothing consumed the"
            " artifacts present."
        )
        if consumed:
            hint += f" Agents consume: {', '.join(consumed)}."
        hint += " Check your Consume(...) target types or the create()'d artifact type."
        print(hint, file=sys.stderr)

    async def astream(
        self,
        budget: Budget | None = None,
        max_iterations: int = 1000,
    ) -> AsyncIterator[ProgressEvent]:
        """Stream of a run: run_start → status (agent announces) → run_end.

        Agents publish statuses via `context.announce(...)`; the app
        re-renders them in the chat ("Thinking…", "Searching docs…", "Found N…").
        At the end a run_end with a summary (outcome/runs/duration) is emitted.
        """
        queue = self.context.subscribe()
        done = asyncio.Event()

        async def _runner() -> None:
            try:
                await self.arun(max_iterations=max_iterations, budget=budget)
            finally:
                done.set()

        task = asyncio.create_task(_runner())
        try:
            yield ProgressEvent(kind="run_start", message="Processing started")
            while True:
                if done.is_set() and queue.empty():
                    break
                get_event = asyncio.ensure_future(queue.get())
                wait_done = asyncio.ensure_future(done.wait())
                finished, _ = await asyncio.wait(
                    {get_event, wait_done}, return_when=asyncio.FIRST_COMPLETED
                )
                if get_event in finished:
                    yield get_event.result()
                else:
                    get_event.cancel()
            # Re-raise any agent/runtime exception instead of silently dropping it:
            # an error inside a run must reach the caller, not hide in the task.
            await task
            stats = self.last_stats
            yield ProgressEvent(
                kind="run_end",
                message="Processing finished",
                data={
                    "outcome": stats.outcome.value if stats is not None else None,
                    "runs": stats.runs if stats is not None else 0,
                    "duration": stats.duration if stats is not None else 0.0,
                },
            )
        finally:
            task.cancel()
            self.context.unsubscribe(queue)

    def _apply_patch(self, patch: Patch, commit: Commit) -> list[Write]:
        writes: list[Write] = []
        for op in patch.operations:
            if isinstance(op, Create):
                if op.id is not None and self.context.get(op.id) is not None:
                    # create-or-refresh: after re-derivation update the same logical
                    # entity (new revision) rather than creating a duplicate (§42, §43)
                    upserted = self.context.update(op.id, op.data)
                    assert upserted is not None
                    op.artifact_id = op.id
                    writes.append(Write(op.id, upserted.version, "update"))
                else:
                    created = self.context.create(op.data, id=op.id)
                    op.artifact_id = created.id
                    created.created_by_commit = commit.id
                    writes.append(Write(op.artifact_id, created.version, "create"))
            elif isinstance(op, Update):
                updated = self.context.update(op.artifact_id, op.new_data)
                if updated is not None:
                    writes.append(Write(op.artifact_id, updated.version, "update"))
            elif isinstance(op, Delete):
                removed = self.context.get(op.artifact_id)
                version = removed.version if removed is not None else 0
                self.context.delete(op.artifact_id)
                writes.append(Write(op.artifact_id, version, "delete"))
            elif isinstance(op, Link):
                self.context.link(op.artifact_id, op.relation, op.target_id)
            elif isinstance(op, Unlink):
                self.context.unlink(op.artifact_id, op.relation, op.target_id)
            else:
                raise ValueError(f"Unknown operation: {op}")
        return writes
