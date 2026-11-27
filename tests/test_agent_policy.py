"""Per-agent execution policy: retry, timeout and effect isolation."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel
from reactifact import (
    Agent,
    Consume,
    Context,
    Patch,
    RetryPolicy,
    Runtime,
    Step,
    step,
)
from reactifact.effects import current_effects
from reactifact.tracing import Tracer


class Question(BaseModel):
    text: str


class Answer(BaseModel):
    text: str


class FlakyAgent(Agent):
    consumes = [Consume(Question)]

    def __init__(self, failures: int, **kwargs):
        super().__init__(**kwargs)
        self.failures = failures
        self.calls = 0

    async def run(self, event, context):
        self.calls += 1
        if self.calls <= self.failures:
            # A failed attempt's effects must be discarded, not committed.
            current_effects().create(Answer(text=f"partial {self.calls}"))
            raise RuntimeError(f"fail {self.calls}")
        return Patch().create(Answer(text="ok"))


class SlowAgent(Agent):
    consumes = [Consume(Question)]

    async def run(self, event, context):
        await asyncio.sleep(0.2)
        return Patch().create(Answer(text="late"))


def _runtime(agent: Agent, **kwargs) -> Runtime:
    context = Context()
    context.create(Question(text="q"))
    return Runtime(context, groups=[agent], **kwargs)


def test_retry_succeeds_and_discards_failed_effects():
    agent = FlakyAgent(2, retry=RetryPolicy(max_attempts=3, backoff=0))
    runtime = _runtime(agent)

    runs = asyncio.run(runtime.arun())

    assert runs == 1
    assert agent.calls == 3
    answers = runtime.context.list_artifacts(Answer)
    assert len(answers) == 1
    assert answers[0].data.text == "ok"


def test_retry_exhausted_raises_by_default():
    agent = FlakyAgent(5, retry=RetryPolicy(max_attempts=2, backoff=0))
    runtime = _runtime(agent)

    with pytest.raises(RuntimeError, match="fail 2"):
        asyncio.run(runtime.arun())

    assert agent.calls == 2
    assert not runtime.context.list_artifacts(Answer)


def test_retry_exhausted_isolated_reports_one_error():
    agent = FlakyAgent(5, retry=RetryPolicy(max_attempts=2, backoff=0))
    errors: list[BaseException] = []
    runtime = _runtime(
        agent,
        isolate_errors=True,
        on_agent_error=lambda _agent, _event, exc: errors.append(exc),
    )

    runs = asyncio.run(runtime.arun())

    assert runs == 1
    assert agent.calls == 2
    assert len(errors) == 1
    assert runtime.last_stats is not None
    assert runtime.last_stats.errors == 1
    assert not runtime.context.list_artifacts(Answer)


def test_timeout_raises_timeout_error():
    agent = SlowAgent(timeout=0.01)
    runtime = _runtime(agent)

    with pytest.raises(TimeoutError):
        asyncio.run(runtime.arun())


class SlowOnceAgent(Agent):
    consumes = [Consume(Question)]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.calls = 0

    async def run(self, event, context):
        self.calls += 1
        if self.calls == 1:
            await asyncio.sleep(0.2)
        return Patch().create(Answer(text="ok"))


def test_timeout_is_retried_by_default():
    agent = SlowOnceAgent(
        timeout=0.01,
        retry=RetryPolicy(max_attempts=2, backoff=0),
    )
    runtime = _runtime(agent)

    assert asyncio.run(runtime.arun()) == 1
    assert agent.calls == 2
    assert runtime.context.list_artifacts(Answer)[0].data.text == "ok"


def test_non_retryable_error_is_not_retried():
    agent = FlakyAgent(
        5,
        retry=RetryPolicy(max_attempts=3, backoff=0, retry_on=(ValueError,)),
    )
    runtime = _runtime(agent)

    with pytest.raises(RuntimeError, match="fail 1"):
        asyncio.run(runtime.arun())

    assert agent.calls == 1


def test_no_policy_means_one_attempt():
    agent = FlakyAgent(1)
    runtime = _runtime(agent)

    with pytest.raises(RuntimeError, match="fail 1"):
        asyncio.run(runtime.arun())

    assert agent.calls == 1


class RetryTracer(Tracer):
    def __init__(self):
        super().__init__()
        self.retries: list[tuple[str, int, str]] = []

    def on_agent_retry(self, agent, attempt, error):
        self.retries.append((agent.name, attempt, type(error).__name__))


def test_retry_hook_reports_each_failed_attempt():
    tracer = RetryTracer()
    agent = FlakyAgent(2, retry=RetryPolicy(max_attempts=3, backoff=0))
    runtime = _runtime(agent, tracer=tracer)

    asyncio.run(runtime.arun())

    assert tracer.retries == [
        ("FlakyAgent", 1, "RuntimeError"),
        ("FlakyAgent", 2, "RuntimeError"),
    ]


def test_Agent_accepts_policy_and_validates_timeout():
    policy = RetryPolicy(max_attempts=2, backoff=0)
    agent = Agent("a", retry=policy, timeout=1.0)

    assert agent.retry is policy
    assert agent.timeout == 1.0

    with pytest.raises(ValueError, match="timeout"):
        Agent("a", timeout=0)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_attempts": 0},
        {"backoff": -1},
        {"max_backoff": -1},
    ],
)
def test_retry_policy_validates_its_limits(kwargs):
    with pytest.raises(ValueError):
        RetryPolicy(**kwargs)


def test_retry_delay_is_exponential_and_capped():
    policy = RetryPolicy(backoff=0.5, max_backoff=1.5)

    assert policy.delay(1) == 0.5
    assert policy.delay(2) == 1.0
    assert policy.delay(3) == 1.5
    assert policy.delay(4) == 1.5


# ---- step timeout ---------------------------------------------------------- #


class Draft(BaseModel):
    text: str


class FirstStep(Step):
    consumes = (Question,)
    produces = (Draft,)

    async def run(self, context: Context, question: Question) -> Draft:
        return Draft(text="draft")


class SlowStep(Step):
    consumes = (Question,)
    produces = (Answer,)
    timeout = 0.01

    async def run(self, context: Context, question: Question) -> Answer:
        await asyncio.sleep(0.2)
        return Answer(text="late")


def test_step_timeout_fails_agent_and_discards_prior_steps():
    context = Context()
    context.create(Question(text="q"))
    agent = Agent("a", steps=[FirstStep(), SlowStep()])
    runtime = Runtime(context, groups=[agent])

    with pytest.raises(TimeoutError):
        asyncio.run(runtime.arun())

    assert not context.list_artifacts(Draft)
    assert not context.list_artifacts(Answer)


def test_decorator_step_timeout():
    @step(produces=Answer, consumes=Question, timeout=0.01)
    async def slow(call):
        await asyncio.sleep(0.2)
        return Answer(text="late")

    context = Context()
    context.create(Question(text="q"))
    runtime = Runtime(context, groups=[Agent("a", steps=[slow])])

    with pytest.raises(TimeoutError):
        asyncio.run(runtime.arun())

    assert not context.list_artifacts(Answer)


class SlowOnceStep(Step):
    consumes = (Question,)
    produces = (Answer,)
    timeout = 0.01

    def __init__(self):
        super().__init__()
        self.calls = 0

    async def run(self, context: Context, question: Question) -> Answer:
        self.calls += 1
        if self.calls == 1:
            await asyncio.sleep(0.2)
        return Answer(text="ok")


def test_agent_retry_reruns_the_whole_agent_after_step_timeout():
    step_instance = SlowOnceStep()
    agent = Agent(
        "a",
        steps=[step_instance],
        retry=RetryPolicy(max_attempts=2, backoff=0),
    )
    context = Context()
    context.create(Question(text="q"))
    runtime = Runtime(context, groups=[agent])

    assert asyncio.run(runtime.arun()) == 1
    assert step_instance.calls == 2
    assert context.list_artifacts(Answer)[0].data.text == "ok"


def test_step_timeout_must_be_positive():
    with pytest.raises(ValueError, match="Step.timeout"):
        Step(Answer, timeout=0)

    with pytest.raises(ValueError, match="Step.timeout"):

        @step(produces=Answer, timeout=-1)
        async def bad(call):
            return Answer(text="bad")
