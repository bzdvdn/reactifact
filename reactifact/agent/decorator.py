"""The `@agent` decorator: a one-function agent, without a Step class."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, overload

from ..context import Context
from ..events import Event
from ..policy import RetryPolicy
from .agent import Agent
from .step.decorator import step


@overload
def agent(func: Callable[..., Any], /) -> Agent: ...


@overload
def agent(
    func: None = None,
    *,
    name: str | None = None,
    consumes: Any | None = None,
    produces: Any | None = None,
    retry: RetryPolicy | None = None,
    timeout: float | None = None,
    fallback: Any | None = None,
    priority: int | None = None,
    concurrency_limit: int | None = None,
    capabilities: tuple[str, ...] | None = None,
    wakes: bool | Callable[[Event, Context | None], bool] = True,
    scope: Any | None = None,
) -> Callable[[Callable[..., Any]], Agent]: ...


def agent(
    func: Callable[..., Any] | None = None,
    *,
    name: str | None = None,
    consumes: Any | None = None,
    produces: Any | None = None,
    retry: RetryPolicy | None = None,
    timeout: float | None = None,
    fallback: Any | None = None,
    priority: int | None = None,
    concurrency_limit: int | None = None,
    capabilities: tuple[str, ...] | None = None,
    wakes: bool | Callable[[Event, Context | None], bool] = True,
    scope: Any | None = None,
) -> Agent | Callable[[Callable[..., Any]], Agent]:
    """Turn one function into an `Agent` with a single step.

    ```python
    @agent
    async def classify(question: Question) -> Route:
        return Route(kind="billing" if ... else "general")

    pipeline = Pipeline(agents=[classify], seed_type=Question)
    ```

    The function's typed parameters are its reads; the return annotation is its
    `produces` (or pass `produces=`). Its `consumes` are inferred from the
    parameters, or set explicitly:

    ```python
    @agent(consumes=[Consume(Question), Consume(Ticket, debounce=True)])
    async def triage(question: Question, ticket: Ticket) -> Route: ...
    ```

    For several steps, build the agent explicitly:
    `Agent("answer", consumes=[...], steps=[Scout(), Answerer()])`.
    """
    if func is not None and not callable(func):
        raise TypeError("@agent must wrap a function")

    def decorate(fn: Callable[..., Any]) -> Agent:
        function_step = step(
            produces=produces,
            consumes=consumes,
            name=name or fn.__name__,
            scope=scope,
        )(fn)
        specs = function_step.read_specs() or ()
        if not specs:
            raise TypeError(
                f"@agent({fn.__name__!r}) needs at least one consumed artifact: "
                "annotate a parameter with the artifact type or pass consumes=..."
            )
        result = Agent(
            name or fn.__name__,
            consumes=list(specs),
            steps=[function_step],
            retry=retry,
            timeout=timeout,
            fallback=fallback,
            priority=priority,
            concurrency_limit=concurrency_limit,
            capabilities=capabilities,
            wakes=wakes,
        )
        # Keep help()/introspection useful; the returned object is the agent.
        result.__doc__ = fn.__doc__
        return result

    return decorate if func is None else decorate(func)


__all__ = ["agent"]
