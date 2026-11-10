import asyncio
import contextlib
import copy
import sys
import time
import uuid
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, cast

from ..agents import Agent, begin_produce_runs, end_produce_runs, produce_runs
from ..budget import Budget, BudgetTracker, RunOutcome, RunStats
from ..commit import Commit, Read
from ..context import Context
from ..effects import Effects, current_effects, reset_effects, set_effects
from ..events import Event
from ..logging import get_logger, reset_context, set_context
from ..patches import Patch
from ..request import current_request, reset_request, set_request
from ..resources import RuntimeResources
from ..scheduler import Scheduler
from ..session import Session
from ..streaming import ProgressEvent
from ..tools import ToolRun, begin_tool_runs, end_tool_runs, tool_runs
from ..tracing.models import AgentSpan
from ..tracing.tracer import CompositeTracer, RunTracer, Tracer
from .budget import TurnBudget
from .loop import run_generations
from .outbox import (
    Dispatcher as Dispatcher,
)
from .outbox import (
    DispatchErrorHandler as DispatchErrorHandler,
)
from .outbox import (
    Outbox,
)
from .patch import PatchApplicator

logger = get_logger("reactifact.runtime")

#: A scheduled patch with its trigger reads and (optional) trace span.
PatchWork = tuple[Patch, Agent, list[Read], AgentSpan | None]
#: One agent execution result: patch (if any), the agent/event/reads that
#: produced it, latency, the exception if the produce raised and
#: `isolate_errors` was set, and the produces that ran (name/ops/latency).
AgentResult = tuple[
    Patch | None,
    Agent,
    Event,
    list[Read],
    float,
    BaseException | None,
    list[tuple[str, int, float]],
    list[ToolRun],
]


@dataclass
class RunInfo:
    """A snapshot of one in-flight turn (see `active_runs`)."""

    run_id: str
    session_id: str
    started_at: float
    request: Mapping[str, Any]
    runtime_id: int
    generation: int = 0


#: In-flight runs of this process, keyed by run id (single event loop; not
#: persisted, not cross-process — a readiness/ops view, not a task queue).
_ACTIVE_RUNS: dict[str, RunInfo] = {}
_RUN_TASKS: dict[str, "asyncio.Task[Any]"] = {}

#: How long `astream` waits for a cancelled runner to unwind before giving up
#: (a well-behaved turn stops immediately; this only guards a misbehaving agent
#: that swallows `CancelledError`).
_CANCEL_GRACE_SECONDS = 5.0


def active_runs() -> list[RunInfo]:
    """Snapshots of the turns currently executing in this process.

    A small ops surface: readiness (`len(active_runs())`), a "what is running"
    view, and the ids `cancel_run` accepts.
    """
    return list(_ACTIVE_RUNS.values())


def cancel_run(run_id: str) -> bool:
    """Cancel one in-flight turn by id; returns whether it was found.

    For graceful, all-of-them shutdown use `Runtime.ashutdown()`.
    """
    task = _RUN_TASKS.get(run_id)
    if task is None or task.done():
        return False
    task.cancel()
    return True


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
        session_save_policy: Literal["per_commit", "per_turn"] = "per_commit",
        dispatcher: Dispatcher | None = None,
        on_dispatch_error: DispatchErrorHandler | None = None,
    ):
        self.context = context
        self.agents = agents or []
        self.max_concurrency = max_concurrency
        self.session = session
        self.budget = budget
        self.scheduler = scheduler
        # Outbox (§42): when set, the runtime performs every committed-but-
        # undispatched `PendingAction` after the generation commits, once per
        # stable id, and records the outcome as state. Replay never runs the
        # runtime, so it never re-dispatches; retries/merges reuse a stable id
        # and produce no new intent. None (default) = no outbox.
        self.dispatcher = dispatcher
        #: Called when the dispatcher raises (the action is marked `failed` and
        #: the exception still propagates — fail-loud, §69). A place to alert or
        #: re-arm; retry policy/backoff is the application's.
        self.on_dispatch_error = on_dispatch_error
        self._outbox = Outbox(context, dispatcher, on_dispatch_error)
        self._patches = PatchApplicator(context)
        # "per_commit" (default): persist at every generation boundary, after
        # that generation's trigger batch is consumed — so the session survives
        # a crash between generations and the saved queue and artifacts commit
        # as one consistent snapshot (including settled generations, which
        # commit nothing but still consume a batch; see `_arun_once_impl`).
        # "per_turn": save once, after `arun()`/`astream()` (the whole run,
        # every generation) fully completes — trades that finer crash-resilience
        # granularity for one write per turn instead of one per generation (a
        # multi-stage pipeline easily produces several generations per turn,
        # each a full Context serialization through `session.save()`). Not read
        # by `arun_once()` on its own (a single generation has no well-defined
        # "turn" boundary) — only `arun()`/`astream()`'s own completion triggers
        # the deferred save.
        self.session_save_policy = session_save_policy
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
        self._turn_budget = TurnBudget()
        self._turn_started = False
        self._no_runs_warned = False
        # Not reentrant: `arun`/`arun_once`/`astream` all mutate shared,
        # instance-level turn state (`_runs_used`, `outcome`, `_deadline`,
        # and the per-turn copy of `context.resources`). Two concurrent calls
        # on the *same*
        # Runtime (e.g. `asyncio.gather(runtime.arun(), runtime.arun())`)
        # would race on that state — one call's budget/deadline silently
        # clobbers the other's mid-flight. Guarded in `_enter_turn`/
        # `_exit_turn`, checked only at the public entry points; `arun`'s own
        # internal loop calls `_arun_once_impl` directly (unguarded — it is
        # already inside the guarded region, not a second concurrent call).
        self._in_turn = False
        # Per-turn LLM usage counters and the wrapper installed to fill them
        # (see `_begin_turn`/`_end_turn_resources`). Only set when the active
        # budget tracks tokens/cost.
        self._tracker: BudgetTracker | None = None
        self._original_llm: Any | None = None
        #: The app's `RuntimeResources` before `_begin_turn` installs a
        #: per-turn shallow copy (budget/deadline/tracker/LLM wrappers live on
        #: that copy, so a `RuntimeResources` shared across sessions/turns is
        #: never mutated by concurrent turns). Restored on turn end.
        self._original_resources: RuntimeResources | None = None
        #: Reset token for the turn's log correlation context (see `_begin_turn`).
        self._log_token: Any | None = None
        #: Generation counter within the current turn (log correlation).
        self._generation = 0
        #: This turn's id (logs/registry); set in `_begin_turn`.
        self._run_id = ""
        #: The task running the current turn (for `ashutdown`).
        self._turn_task: asyncio.Task[Any] | None = None
        #: Set by `request_stop()`; checked between generations (§59/§69).
        self._stop_requested = asyncio.Event()

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

    def request_stop(self) -> None:
        """Ask the run to stop at the next generation boundary (§59).

        The in-flight generation finishes (its commit lands); no new generation
        starts, and the run ends with `RunOutcome.STOPPED`. For a bounded wait
        plus a forced cancel, use `ashutdown()`.
        """
        self._stop_requested.set()

    @property
    def in_flight(self) -> bool:
        """Whether this Runtime is currently running a turn."""
        return self._in_turn

    async def ashutdown(self, *, timeout: float | None = None) -> None:
        """Graceful shutdown: stop at a boundary, then wait for the turn to end.

        Calls `request_stop()` first, then awaits the in-flight turn (if any).
        With `timeout`, force-cancels the turn after that many seconds. Does
        **not** close shared `RuntimeResources` — close those yourself (a
        `ResourceScope`, or `await resources.aclose()` in your own lifespan).
        """
        self.request_stop()
        task = self._turn_task
        if task is None or task is asyncio.current_task() or task.done():
            return
        if timeout is None:
            with contextlib.suppress(Exception):
                await asyncio.shield(task)
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout)
        except TimeoutError:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    def _begin_turn(self, budget: Budget | None) -> None:
        # Per-turn budget/deadline/tracker and the LLM wrappers that fill them
        # are installed on a *copy* of the app's RuntimeResources. A single
        # RuntimeResources instance may be shared by several sessions (a
        # long-lived provider), and concurrent turns on different Contexts
        # would otherwise clobber each other's budget and stack LLM wrappers on
        # the shared object. The copy is discarded at turn end.
        if self._original_resources is None:
            self._original_resources = self.context.resources
            self.context.resources = copy.copy(self.context.resources)
        self.outcome = RunOutcome.COMPLETED
        self._turn_budget.begin(budget or self.budget, self.context.resources)
        self._turn_started = True
        self._trace.begin_turn(
            session_id=self.session.session_id if self.session is not None else ""
        )
        self._generation = 0
        # A per-turn id for logs/registry. Tracing mints its own only when a
        # tracer is configured; reuse it when present so both agree, and mint
        # one otherwise.
        self._run_id = self._trace.run_id or uuid.uuid4().hex
        # Correlation fields for every log line of this turn (inherited by the
        # child tasks a generation fans out to).
        self._log_token = set_context(
            run_id=self._run_id,
            session_id=self.session.session_id if self.session is not None else "",
        )
        self._turn_task = asyncio.current_task()
        _ACTIVE_RUNS[self._run_id] = RunInfo(
            run_id=self._run_id,
            session_id=self.session.session_id if self.session is not None else "",
            started_at=time.monotonic(),
            request=current_request(),
            runtime_id=id(self),
        )
        if self._turn_task is not None:
            _RUN_TASKS[self._run_id] = self._turn_task

    def _end_turn_resources(self) -> None:
        """Undoes `_begin_turn`'s accounting wrap and closes the turn.

        Called from the public turn entry points (`arun`/`arun_once`); idempotent
        if no wrapper was installed.
        """
        self._turn_budget.finish(self.context.resources)
        if self._original_resources is not None:
            # Drop the per-turn copy (and every wrapper installed on it) and
            # put the app's original instance back on the Context.
            self.context.resources = self._original_resources
            self._original_resources = None
        if self._log_token is not None:
            reset_context(self._log_token)
            self._log_token = None
        if self._run_id:
            _ACTIVE_RUNS.pop(self._run_id, None)
            _RUN_TASKS.pop(self._run_id, None)
        self._turn_task = None
        self._turn_started = False

    def _budget_exhausted(self) -> bool:
        exceeded = self._turn_budget.exhausted()
        if exceeded is None:
            return False
        self.outcome = exceeded
        return True

    async def arun_once(
        self,
        budget: Budget | None = None,
        *,
        request: Mapping[str, Any] | None = None,
    ) -> int:
        self._enter_turn()
        token = set_request(request) if request is not None else None
        try:
            return await self._arun_once_impl(budget)
        finally:
            if token is not None:
                reset_request(token)
            self._end_turn_resources()
            self._exit_turn()

    async def _arun_once_impl(self, budget: Budget | None = None) -> int:
        if not self._turn_started:
            self._begin_turn(budget)
        self._generation += 1
        info = _ACTIVE_RUNS.get(self._run_id)
        if info is not None:
            info.generation = self._generation
        self._outbox.warn_if_unconfigured()
        events = self.context.pending_events()
        # A settled turn can still have outbox work: a `PendingAction` committed
        # by an earlier generation (or adopted via `merge()`) with no events left
        # to drain. Proceed when either is non-empty.
        if not events and not self._outbox.has_pending_actions():
            return 0
        if self._budget_exhausted():
            return 0

        # Collect work (event, agent), accounting for priority: agents with lower
        # values run earlier, "finishers" last.
        work: list[tuple[Agent, Event, list[Read]]] = []
        # An agent whose *every* matching trigger for a given event asks for
        # `debounce` gets collapsed to at most one run per generation here —
        # the last matching event in this batch wins, replacing any earlier
        # one already staged for the same agent (see `Consume.debounce`'s
        # docstring for why this lives here and not on `Consume`/`Trigger`
        # themselves: collapsing needs to compare *other* events in the same
        # drained batch, which is state only `Runtime` has). An agent with a
        # mix of debounced and non-debounced matching triggers for the same
        # event is treated as non-debounced for that event — debouncing only
        # kicks in when nothing about the match demands immediacy.
        debounced: dict[Agent, tuple[Event, list[Read]]] = {}
        ordered_agents = sorted(self.agents, key=lambda a: a.priority)
        for event in events:
            if self._budget_exhausted():
                break
            for agent in ordered_agents:
                if self._budget_exhausted():
                    break
                triggers = agent.matching_triggers(event, self.context)
                if not triggers:
                    continue
                reads = self._collect_reads(agent, event)
                if all(trigger.debounce for trigger in triggers):
                    debounced[agent] = (event, reads)
                else:
                    work.append((agent, event, reads))
        if debounced:
            work.extend(
                (agent, event, reads) for agent, (event, reads) in debounced.items()
            )
            # `dict` insertion order is unrelated to agent priority once
            # debounced entries are appended after the loop — restore the
            # same "lower priority value runs earlier" invariant the loop
            # above already gave the non-debounced entries (stable sort
            # keeps relative order among equal priorities).
            work.sort(key=lambda item: item[0].priority)

        # Limit the number of runs by the max_runs budget. Set the budget_runs_exceeded
        # outcome only when the limit is actually reached, not when the event simply
        # has no subscribed agents.
        if self.scheduler is not None and work:
            work = await self.scheduler(self.context, work)

        active = self._turn_budget.active
        if active is not None and active.max_runs is not None:
            remaining = active.max_runs - self._turn_budget.runs_used
            if remaining <= 0:
                self.outcome = RunOutcome.BUDGET_RUNS_EXCEEDED
                work = []
            else:
                work = work[:remaining]

        results = await self._dispatch(work)
        patches_to_apply, runs = self._get_patches_to_apply(results)
        await self._commit_patches_to_apply(patches_to_apply)
        # Consume the triggers only now that the generation's patches are
        # committed: `pending_events()` above is a peek, so a raise anywhere in
        # dispatch/commit leaves the batch queued and a retry (another
        # `runtime.arun()`) re-runs the work the exception would otherwise have
        # silently dropped. Events appended by the commits themselves — the
        # next generation's triggers — follow the batch and are preserved.
        self.context.consume_events(events)
        # Outbox drain: perform committed intents *after* the commit, so a
        # produce never does the I/O itself. Replay reconstructs state without
        # running the runtime, so it never re-dispatches; a retried produce
        # re-derives the same stable id and creates no second intent.
        await self._outbox.drain_actions()
        # Persist at the generation boundary, *after* the consume, so the saved
        # queue is the at-rest one: this batch's triggers are gone and the next
        # generation's (emitted by this one's commits) remain. Saving before the
        # consume would persist already-processed triggers and replay them on
        # reload; skipping the save would lose the fact that they were consumed
        # — and a settled generation (matches nothing, commits nothing) still
        # has to record that, or an event its state-dependent `Consume`
        # condition didn't match yet would be replayed once the condition turns
        # true (a HITL `resume`, say), running its consumer twice.
        # (`session_save_policy="per_turn"` defers this to `_arun_impl`'s single
        # save after the whole run completes instead.)
        if self.session is not None and self.session_save_policy == "per_commit":
            # Shielded so a hard-deadline cancellation cannot interrupt a save
            # mid-write; the save still completes (marking the generation done).
            await asyncio.shield(self.session.save())
        logger.debug(
            "generation committed",
            extra={"generation": self._generation, "runs": runs},
        )
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
        for (
            patch,
            agent,
            event,
            reads,
            latency,
            error,
            produce_run_list,
            tool_run_list,
        ) in results:
            if self._budget_exhausted():
                break
            runs += 1
            self._turn_budget.count_run()
            if error is not None:
                self._turn_budget.count_error()
                self._trace.record_span(
                    agent,
                    event,
                    reads,
                    latency,
                    error=error,
                    produces=produce_run_list,
                    tools=tool_run_list,
                )
                continue
            if patch is None or patch.is_empty():
                continue
            span = self._trace.record_span(
                agent,
                event,
                reads,
                latency,
                produces=produce_run_list,
                tools=tool_run_list,
            )
            self._patches.validate_types(patch, agent)
            self._patches.enforce_authorization(patch)
            patch = self._patches.apply_guardrails(patch)
            patches_to_apply.append((patch, agent, reads, span))
        return patches_to_apply, runs

    async def _commit_patches_to_apply(self, patches_to_apply: list[PatchWork]) -> None:
        """Applies each patch as a commit: provenance and span writes.

        Persistence is deliberately not here: the session is saved once per
        generation in `_arun_once_impl`, after the trigger batch is consumed,
        so the saved snapshot is queue-consistent (see the note there).
        """
        for patch, agent, reads, span in patches_to_apply:
            commit = Commit(
                author=agent.name,
                message=f"Applied patch from agent '{agent.name}'",
                operations=patch.operations,
                reads=reads,
            )
            commit.writes = self._patches.apply(patch, commit)
            if span is not None:
                span.writes = self._trace.write_refs(patch, commit.writes)
                span.relations = self._trace.relation_refs(patch)
            self.context.log_commit(commit)

    def _has_pending_work(self) -> bool:
        """True while triggers or undispatched outbox actions remain (§42)."""
        return bool(self.context.pending_events()) or self._outbox.has_pending_actions()

    async def flush_pending_actions(self) -> int:
        """Dispatches committed-but-undispatched actions without a generation.

        The explicit outbox flush after `context.merge()` (merge is a pure
        state operation and never dispatches). Returns the number attempted.
        """
        if self.dispatcher is None:
            return 0
        self._enter_turn()
        try:
            pending = self._outbox.pending_count()
            if pending:
                await self._outbox.drain_actions()
                if self.session is not None:
                    await self.session.save()
            return pending
        finally:
            self._exit_turn()

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
        # Authz: a principal may be denied running a given agent. Denial is not
        # an agent bug, so it always propagates (not swallowed by isolate_errors).
        self.context.resources.require_authorized("run", agent.name)
        started = time.monotonic()
        task = asyncio.current_task()
        self._trace.register_task(task, agent.name)
        effects_token = set_effects(Effects(self.context))
        produce_runs_token = begin_produce_runs()
        tool_runs_token = begin_tool_runs(
            record_args=self.context.resources.record_tool_args,
            redactor=self.context.resources.redactor,
            truncate=self._trace.data_char_limit,
        )
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
            runs = produce_runs()
            tools = tool_runs()
            end_produce_runs(produce_runs_token)
            end_tool_runs(tool_runs_token)
            reset_effects(effects_token)
            self._trace.unregister_task(task)
        latency = (time.monotonic() - started) * 1000
        if error is not None:
            logger.warning(
                "agent failed",
                extra={
                    "agent": agent.name,
                    "latency_ms": round(latency, 2),
                    "error": repr(error),
                },
            )
            return None, agent, event, reads, latency, error, runs, tools
        logger.debug(
            "agent finished",
            extra={"agent": agent.name, "latency_ms": round(latency, 2)},
        )
        # Effects authored in produce() compile to the patch. A produce's own
        # effects happen *after* its returned patch (produce order), so an
        # update that captured the pre-effect state cannot regress later effects.
        if slot is not None and not slot.is_empty():
            combined = Patch()
            if patch is not None:
                combined.merge(patch)
            combined.merge(slot.to_patch())
            patch = combined
        return patch, agent, event, reads, latency, None, runs, tools

    async def arun(
        self,
        max_iterations: int = 100,
        budget: Budget | None = None,
        *,
        request: Mapping[str, Any] | None = None,
    ) -> int:
        self._enter_turn()
        token = set_request(request) if request is not None else None
        try:
            return await self._arun_impl(max_iterations, budget)
        finally:
            if token is not None:
                reset_request(token)
            self._end_turn_resources()
            self._exit_turn()

    async def _arun_impl(self, max_iterations: int, budget: Budget | None) -> int:
        self._begin_turn(budget)
        active = self._turn_budget.active
        limit = (
            active.max_iterations
            if active is not None and active.max_iterations is not None
            else max_iterations
        )
        logger.info("run started", extra={"max_iterations": limit})
        total_runs = 0
        remaining = self._turn_budget.remaining_seconds()
        try:
            if remaining is None:
                total_runs = await run_generations(self, limit)
            else:
                # Hard deadline: cancel the in-flight generation when wall-clock
                # runs out (the soft `_budget_exhausted` check only fires between
                # generations). The generation in flight is not committed and its
                # trigger batch is not consumed, so a resume re-dispatches it.
                async with asyncio.timeout(remaining):
                    total_runs = await run_generations(self, limit)
        except TimeoutError:
            self.outcome = RunOutcome.BUDGET_TIME_EXCEEDED
            logger.warning(
                "turn deadline exceeded",
                extra={
                    "max_seconds": active.max_seconds if active is not None else None
                },
            )
        tracker = self._turn_budget.tracker
        self.last_stats = RunStats(
            runs=total_runs,
            iterations=limit,
            outcome=self.outcome,
            duration=time.monotonic() - self._turn_budget.started_at,
            errors=self._turn_budget.errors_used,
            prompt_tokens=tracker.prompt_tokens if tracker is not None else 0,
            completion_tokens=tracker.completion_tokens if tracker is not None else 0,
            cost=tracker.cost if tracker is not None else 0.0,
        )
        if total_runs == 0:
            self._warn_no_runs()
        await self._trace.end_turn(
            session_id=self.session.session_id if self.session is not None else "",
            # `time.monotonic()` is seconds; the trace's `duration_ms` (and every
            # sink/UI reading it) is milliseconds — same unit as span latency.
            duration_ms=(time.monotonic() - self._turn_budget.started_at) * 1000,
            outcome=self.outcome.value,
        )
        if self.session is not None and self.session_save_policy == "per_turn":
            await self.session.save()
        stats = self.last_stats
        logger.info(
            "run finished",
            extra={
                "outcome": self.outcome.value,
                "runs": total_runs,
                "errors": self._turn_budget.errors_used,
                "duration_ms": round(stats.duration * 1000, 2) if stats else 0.0,
            },
        )
        return total_runs

    def run_once(self, *, request: Mapping[str, Any] | None = None) -> int:
        return asyncio.run(self.arun_once(request=request))

    def run(
        self,
        max_iterations: int = 100,
        budget: Budget | None = None,
        *,
        request: Mapping[str, Any] | None = None,
    ) -> int:
        return asyncio.run(self.arun(max_iterations, budget, request=request))

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
        *,
        request: Mapping[str, Any] | None = None,
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
                # request is installed on this task's context; the generation's
                # child tasks inherit it, so `call.request` works there too.
                await self.arun(
                    max_iterations=max_iterations, budget=budget, request=request
                )
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
                try:
                    finished, _ = await asyncio.wait(
                        {get_event, wait_done}, return_when=asyncio.FIRST_COMPLETED
                    )
                    if get_event in finished:
                        yield get_event.result()
                finally:
                    # Always cancel *and await* the losing future. A task that is
                    # cancelled but never awaited stays pending until the loop
                    # closes, producing "Task was destroyed but it is pending!"
                    # (`Queue.get`/`Event.wait`) on every early generator close
                    # (e.g. a dropped SSE client).
                    for fut in (get_event, wait_done):
                        if not fut.done():
                            fut.cancel()
                    await asyncio.gather(get_event, wait_done, return_exceptions=True)
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
            # Settle the runner too, so an early generator close (client
            # disconnect) does not leave the run task pending at loop shutdown.
            # Bounded: an agent that swallows `CancelledError` must never hang
            # the caller's disconnect/shutdown path.
            with contextlib.suppress(Exception):
                await asyncio.wait_for(
                    asyncio.gather(task, return_exceptions=True),
                    timeout=_CANCEL_GRACE_SECONDS,
                )
            self.context.unsubscribe(queue)
