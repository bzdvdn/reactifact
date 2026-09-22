"""reactifact.replay — deterministic reproduction of runs (§55).

Two complementary halves:

1. `ReplayLLM` — a provider-level recording studio. `mode="record"` wraps a real
   provider and appends every `(request → response)` pair to a JSONL file;
   `mode="replay"` answers *exactly* the recorded calls and raises `ReplayMiss`
   when a call diverges from the recording — a divergent call must not be
   answered with a wrong result (§59, §55).

   Record once (real model), then re-run the runtime with the replaying LLM:
   because every deterministic path is unchanged, the run reproduces the same
   artifacts, and "why did the agent produce this answer?" (§55) can be answered
   by walking the reproduced state.

2. `replay_context` / `replay_summary` — reconstruct a saved session's state at
   a specific commit (the commit chain is deterministic, §14), i.e. a cheap,
   offline "what was the state when the agent said that".
"""

from __future__ import annotations

import hashlib
import itertools
import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel

from .audit import context_hash
from .context import Context
from .providers import LLMProvider, LLMRequest, LLMResponse, LLMResponseChunk

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from .resources import RuntimeResources
    from .session import SessionStore

logger = logging.getLogger(__name__)


class ReplayMiss(RuntimeError):
    """A replaying call did not match the recording (§55)."""


def _request_key(model: str, request: LLMRequest) -> str:
    payload = {
        "model": model,
        "temperature": request.temperature,
        "response_format": request.response_format,
        "messages": [{"role": m.role, "content": m.content} for m in request.messages],
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ReplayLLM(LLMProvider):
    """Records LLM calls to a JSONL file, or replays them exactly.

        recorder = ReplayLLM("calls.jsonl", mode="record", inner=real_llm)
        resources = RuntimeResources(llm=recorder)
        runtime.run()                      # record pass

        replay = ReplayLLM("calls.jsonl", mode="replay")
        resources = RuntimeResources(llm=replay)
        runtime.run()                      # deterministic reproduction (§55)

    A recording also answers the "why" question: every call carries the exact
    prompt and the exact response, so the product story is reproducible.
    """

    def __init__(
        self,
        recording: str | Path,
        *,
        mode: Literal["record", "replay"] = "replay",
        inner: LLMProvider | None = None,
        model: str = "",
    ):
        if mode == "record" and inner is None:
            raise ValueError("mode='record' requires an `inner` provider")
        self.recording = Path(recording)
        self.mode = mode
        self._inner = inner
        self.model = model
        self._cache: dict[str, dict[str, Any]] | None = None

    # -- LLMProvider ---------------------------------------------------------

    async def complete(self, request: LLMRequest) -> LLMResponse:
        if self.mode == "replay":
            return self._replay(request)
        assert self._inner is not None
        response = await self._inner.complete(request)
        self._append(request, response)
        return response

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMResponseChunk]:
        if self.mode == "record" and self._inner is not None:
            async for chunk in self._inner.stream(request):
                yield chunk
            return
        raise ReplayMiss(
            "stream() is not replayed; record stream calls or use complete()"
        )

    # -- recording -----------------------------------------------------------

    def _append(self, request: LLMRequest, response: LLMResponse) -> None:
        entry = {
            "key": _request_key(self.model, request),
            "model": self.model,
            "response": {
                "text": response.text,
                "finish_reason": response.finish_reason,
            },
            "usage": response.usage,
        }
        self.recording.parent.mkdir(parents=True, exist_ok=True)
        with self.recording.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # -- replaying -----------------------------------------------------------

    def _load(self) -> dict[str, dict[str, Any]]:
        if self._cache is not None:
            return self._cache
        cache: dict[str, dict[str, Any]] = {}
        if self.recording.exists():
            for line in self.recording.read_text(encoding="utf-8").splitlines():
                entry = json.loads(line)
                cache[entry["key"]] = entry
        self._cache = cache
        return cache

    def _replay(self, request: LLMRequest) -> LLMResponse:
        key = _request_key(self.model, request)
        entry = self._load().get(key)
        if entry is None:
            logger.warning("replay miss for %r", key)
            raise ReplayMiss(
                "the run diverged from the recording — a call was not recorded. "
                "Re-record with mode='record', or check whether the prompt "
                "changed since the recording was made."
            )
        response = entry["response"]
        return LLMResponse(
            text=response["text"],
            finish_reason=response.get("finish_reason"),
            usage=dict(entry.get("usage") or {}),
        )


async def replay_context(
    store: SessionStore,
    session_id: str,
    *,
    version: int | None = None,
) -> Context:
    """Reconstructs a saved session's state, optionally at a past commit (§55).

    The session checkpoint carries the full deterministic commit chain (§14), so
    replaying to a version needs no agent execution — it is pure state recovery.
    """
    context = await store.load_session(session_id)
    if context is None:
        raise KeyError(f"session {session_id!r} not found")
    if version is not None:
        context.checkout(version)
    return context


def counter_ids(start: int = 0) -> Callable[[str], str]:
    """A deterministic id factory: `Model:0000`, `Model:0001`, … per run.

    Assign it to `RuntimeResources(id_factory=…)` and every artifact created
    without an explicit id gets a stable, order-derived id instead of a
    `uuid4` — enough to make an otherwise-unmodified app's `context_hash`
    reproducible run to run. Pair with `ReplayLLM` (recorded model calls) for
    full reproducibility; `verify_run` wires both.
    """
    counter = itertools.count(start)

    def make(model_name: str) -> str:
        return f"{model_name.lower()}:{next(counter):04d}"

    return make


class ReproReport(BaseModel):
    """Outcome of `verify_run`: were repeated runs byte-identical?"""

    ok: bool
    hashes: list[str]
    repeat: int
    recording: str | None = None


#: A run builder: given resources, produce the finished `Context`.
RunBuilder = Callable[["RuntimeResources"], Awaitable["Context"]]


async def verify_run(
    build: RunBuilder,
    *,
    recording: str | Path | None = None,
    repeat: int = 2,
    resources_factory: Callable[[], RuntimeResources] | None = None,
) -> ReproReport:
    """Runs `build` `repeat` times and checks the `context_hash` is identical.

    Each run gets fresh resources with **deterministic ids**
    (`counter_ids()`) and — when `recording` is given — a `ReplayLLM` replaying
    it, so a difference between runs is real nondeterminism in the app (time,
    randomness, unstable ids, order), not model variance. `build(resources)`
    must build the app on the given resources and return the finished
    `Context` (it must not create resources itself).

        report = await verify_run(build, recording="calls.jsonl")
        assert report.ok, report.hashes

    Returns a `ReproReport`; `ok` is False (with every hash) when they differ.
    """
    if repeat < 2:
        raise ValueError("verify_run needs repeat >= 2 to compare runs")
    hashes: list[str] = []
    for _ in range(repeat):
        resources = (
            resources_factory() if resources_factory is not None else _resources()
        )
        resources.id_factory = counter_ids()
        if recording is not None:
            resources.llm = ReplayLLM(recording, mode="replay")
        context = await build(resources)
        hashes.append(context_hash(context))
    return ReproReport(
        ok=len(set(hashes)) == 1,
        hashes=hashes,
        repeat=repeat,
        recording=str(recording) if recording is not None else None,
    )


def _resources() -> RuntimeResources:
    from .resources import RuntimeResources

    return RuntimeResources()


def replay_summary(context: Context) -> dict[str, Any]:
    """A compact "state at this point" summary for the replay CLI."""
    artifacts = context.list_artifacts()
    by_type: dict[str, int] = {}
    for artifact in artifacts:
        tname = artifact.data.__class__.__name__
        by_type[tname] = by_type.get(tname, 0) + 1
    return {
        "version": context.version,
        "artifacts": len(artifacts),
        "by_type": by_type,
        "relations": len(context.relations()),
        "pending_questions": len(context.pending_questions()),
    }


__all__ = [
    "ReproReport",
    "ReplayLLM",
    "ReplayMiss",
    "counter_ids",
    "replay_context",
    "replay_summary",
    "verify_run",
]
