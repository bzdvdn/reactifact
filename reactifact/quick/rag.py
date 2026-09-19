"""The `quick.rag` entry point: retrieval → materialize → answer, with provenance."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, Generic, TypeVar, overload

from pydantic import BaseModel

from ..agents import Agent, create_agent
from ..artifacts import Artifact
from ..budget import Budget
from ..consume import Consume
from ..context import Context
from ..produce import Produce, produce
from ..providers import LLMProvider
from ..recipes import fan_out_sources, find_all, keyword_score, materialize_doc
from ..resources import RuntimeResources
from ..sources import CSVSource, FileSystemSource, Source, SourceRef
from ..structured import llm_reply
from ._shared import _arun, _default_resources, _question_of, _text_of
from .models import Answer, Doc, Question

#: Builds a materialized document from a resolved source: `(context, ref, content)`.
#: Same shape `materialize_doc` takes, so a factory written for it drops in here.
DocBuilder = Callable[[Context, Artifact[SourceRef], Any], BaseModel]
#: Builds the final answer: `(answer_text, used_docs)`. `used_docs` are the
#: document artifacts, so a custom answer can read any field it needs.
AnswerBuilder = Callable[[str, list[Artifact[Any]]], BaseModel]

#: The answer artifact type; `QuickRAG` is generic in it so `.ask()` returns it.
TAnswer = TypeVar("TAnswer", bound=BaseModel)

RAG_ANSWER_SYSTEM = (
    "Answer the question using only the provided sources. Cite sources by "
    "their locator when you use them. If the sources do not contain the "
    "answer, say so plainly — do not invent facts."
)


def _path_scorer(text: str, query: str) -> float:
    """Stop-word- and light-plural-aware scorer for plain-path sources.

    `keyword_score` with `fold_plurals=True`: a query "refund policy" matches a
    document that says "Refunds are available…" — the kind of near-miss that
    otherwise makes a lexical RAG feel broken.
    """
    return keyword_score(text, query, fold_plurals=True)


def _source_for_path(source_id: str, path: str | Path) -> Source:
    p = Path(path)
    if p.suffix.lower() == ".csv":
        return CSVSource(root=str(p.parent), source_id=source_id, scorer=_path_scorer)
    return FileSystemSource(root=str(p), source_id=source_id, scorer=_path_scorer)


def _normalize_sources(
    sources: Source | str | Path | dict[str, Source | str | Path],
) -> dict[str, Source]:
    """Accepts a `Source`, a path, or a `{id: Source|path}` mapping.

    `source_id` is forced to match the dict key (or the source's own id when a
    bare `Source`/path is passed) — the single most common footgun in the
    hand-written version: a `SourceRef` can only be resolved back when the
    registered key and `source_id` agree.
    """
    if isinstance(sources, Source):
        return {sources.source_id: sources}
    if isinstance(sources, (str, Path)):
        source_id = Path(sources).name or "source"
        return {source_id: _source_for_path(source_id, sources)}
    normalized: dict[str, Source] = {}
    for key, value in sources.items():
        if isinstance(value, Source):
            value.source_id = key
            normalized[key] = value
        else:
            normalized[key] = _source_for_path(key, value)
    return normalized


def _content_text(content: Any) -> str:
    return (
        content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    )


def _default_doc_builder(doc_type: type[BaseModel]) -> DocBuilder:
    """Builds `doc_type(text=..., locator=..., title=...)` — or asks for a factory.

    A custom `doc_type` that doesn't accept those three fields gets a clear
    `TypeError` telling the caller to pass `doc_factory=`, instead of a bare
    pydantic `ValidationError` from deep inside a produce.
    """

    def build(_ctx: Context, ref: Artifact[SourceRef], content: Any) -> BaseModel:
        text = _content_text(content)
        if doc_type is Doc:
            return Doc(text=text, locator=ref.data.locator, title=ref.data.title)
        try:
            return doc_type(text=text, locator=ref.data.locator, title=ref.data.title)
        except Exception as exc:
            raise TypeError(
                f"doc_type {doc_type.__name__} must accept text/locator/title, "
                "or pass doc_factory= to build it yourself"
            ) from exc

    return build


def _default_doc_text(doc: BaseModel) -> str:
    return _text_of(doc)


def _default_doc_locator(doc: BaseModel) -> str:
    locator = getattr(doc, "locator", "")
    if isinstance(locator, str) and locator:
        return locator
    title = getattr(doc, "title", "")
    return title if isinstance(title, str) else ""


def _default_answer_builder(
    answer_type: type[BaseModel],
    doc_locator: Callable[[BaseModel], str],
) -> AnswerBuilder:
    def build(text: str, docs: list[Artifact[Any]]) -> BaseModel:
        sources = [doc_locator(doc.data) for doc in docs]
        sources = [s for s in sources if s]
        if answer_type is Answer:
            return Answer(text=text, sources=sources)
        try:
            return answer_type(text=text, sources=sources)
        except Exception as exc:
            raise TypeError(
                f"answer_type {answer_type.__name__} must accept text/sources, "
                "or pass answer_factory= to build it yourself"
            ) from exc

    return build


def _search_produce(limit: int, question_type: type[BaseModel]) -> Produce[Any]:
    @produce(SourceRef)
    async def _search(call: Any) -> None:
        question = _question_of(call, question_type)
        if question is None:
            return None
        await fan_out_sources(
            call.context, _text_of(question.data), owner_id=question.id, limit=limit
        )
        return None

    return _search


def _resolve_produce(
    doc_type: type[BaseModel], doc_builder: DocBuilder
) -> Produce[Any]:
    @produce(doc_type)
    async def _resolve(call: Any) -> None:
        refs = [a for a in call.inputs if isinstance(a.data, SourceRef)]
        trigger = call.trigger
        if trigger is not None and isinstance(trigger.data, SourceRef):
            refs = [trigger]
        for ref_artifact in refs:
            await materialize_doc(call.context, ref_artifact, doc_builder)
        return None

    return _resolve


def _answer_produce(
    system: str,
    *,
    question_type: type[BaseModel],
    doc_type: type[BaseModel],
    answer_type: type[BaseModel],
    answer_builder: AnswerBuilder,
    doc_text: Callable[[BaseModel], str],
    doc_locator: Callable[[BaseModel], str],
) -> Produce[Any]:
    @produce(answer_type)
    async def _answer(call: Any) -> None:
        question = call.context.latest(question_type) or _question_of(
            call, question_type
        )
        docs = find_all(call.inputs, doc_type)
        if question is None or not docs:
            return None
        prompt = "\n\n".join(
            f"[{doc_locator(doc.data)}]\n{doc_text(doc.data)}" for doc in docs
        )
        text = await llm_reply(
            call.context,
            system=system,
            user=f"Question: {_text_of(question.data)}\n\nSources:\n{prompt}",
        )
        if not text:
            return None
        handle = call.effects.create_once_from(question, answer_builder(text, docs))
        if handle is None:
            return None
        for doc in docs:
            handle.link("supported_by", doc)
        return None

    return _answer


class QuickRAG(Generic[TAnswer]):
    """Retrieval → materialize → answer, wired and provenance-aware.

    Built by `rag(...)`. Three plain reactive agents (search / resolve / answer);
    every answer is linked `supported_by` each document it used, and each
    document is linked `materialized_from` its `SourceRef` — the same provenance
    the hand-written `examples/knowledge` pipeline builds, without the
    boilerplate.

    Generic in the answer type: `rag(...)` infers `TAnswer` from `answer_type=`
    (defaulting to `Answer`), so `.ask()` returns `TAnswer | None` and your
    editor knows the concrete model. Works with the generic `Question`/`Doc`/
    `Answer` models, or your own via `question_type=`/`doc_type=`/`answer_type=`
    (plus `doc_factory=`/`answer_factory=` when your models don't follow the
    default field names).
    """

    def __init__(
        self,
        sources: dict[str, Source],
        *,
        answer_type: type[TAnswer],
        answer_system: str = RAG_ANSWER_SYSTEM,
        limit: int = 5,
        question_type: type[BaseModel] = Question,
        doc_type: type[BaseModel] = Doc,
        doc_factory: DocBuilder | None = None,
        answer_factory: AnswerBuilder | None = None,
        doc_text: Callable[[BaseModel], str] | None = None,
        doc_locator: Callable[[BaseModel], str] | None = None,
        llm: LLMProvider | None = None,
        resources: RuntimeResources | None = None,
        budget: Budget | None = None,
        tracer: Any = None,
    ):
        self.sources = sources
        self.limit = limit
        self.question_type = question_type
        self.doc_type = doc_type
        self.answer_type = answer_type
        self.budget = budget
        self.tracer = tracer
        self.resources = (
            resources
            if resources is not None
            else _default_resources(llm=llm, sources=sources)
        )
        self.context: Context | None = None
        resolved_doc_locator = doc_locator or _default_doc_locator
        resolved_doc_text = doc_text or _default_doc_text
        doc_builder = doc_factory or _default_doc_builder(doc_type)
        answer_builder = answer_factory or _default_answer_builder(
            answer_type, resolved_doc_locator
        )
        self.agents: list[Agent] = [
            create_agent(
                name="search",
                consumes=[Consume(question_type)],
                produces=[_search_produce(limit, question_type)],
            ),
            create_agent(
                name="resolve",
                consumes=[Consume(SourceRef)],
                produces=[_resolve_produce(doc_type, doc_builder)],
            ),
            create_agent(
                name="answer",
                consumes=[Consume(doc_type), Consume(question_type, wakes=False)],
                produces=[
                    _answer_produce(
                        answer_system,
                        question_type=question_type,
                        doc_type=doc_type,
                        answer_type=answer_type,
                        answer_builder=answer_builder,
                        doc_text=resolved_doc_text,
                        doc_locator=resolved_doc_locator,
                    )
                ],
            ),
        ]

    async def ask(self, text: str, *, context: Context | None = None) -> TAnswer | None:
        """Runs the retrieval pipeline for `text`; returns the answer model or None."""
        ctx = context if context is not None else Context(resources=self.resources)
        ctx.create(self.question_type(text=text))
        await _arun(ctx, self.agents, budget=self.budget, tracer=self.tracer)
        self.context = ctx
        artifact = ctx.latest(self.answer_type)
        return artifact.data if artifact is not None else None


@overload
def rag(
    sources: Source | str | Path | dict[str, Source | str | Path],
    *,
    answer_type: type[TAnswer],
    answer_system: str = RAG_ANSWER_SYSTEM,
    limit: int = 5,
    question_type: type[BaseModel] = Question,
    doc_type: type[BaseModel] = Doc,
    doc_factory: DocBuilder | None = None,
    answer_factory: AnswerBuilder | None = None,
    doc_text: Callable[[BaseModel], str] | None = None,
    doc_locator: Callable[[BaseModel], str] | None = None,
    llm: LLMProvider | None = None,
    resources: RuntimeResources | None = None,
    budget: Budget | None = None,
    tracer: Any = None,
) -> QuickRAG[TAnswer]: ...


@overload
def rag(
    sources: Source | str | Path | dict[str, Source | str | Path],
    *,
    answer_type: None = None,
    answer_system: str = RAG_ANSWER_SYSTEM,
    limit: int = 5,
    question_type: type[BaseModel] = Question,
    doc_type: type[BaseModel] = Doc,
    doc_factory: DocBuilder | None = None,
    answer_factory: AnswerBuilder | None = None,
    doc_text: Callable[[BaseModel], str] | None = None,
    doc_locator: Callable[[BaseModel], str] | None = None,
    llm: LLMProvider | None = None,
    resources: RuntimeResources | None = None,
    budget: Budget | None = None,
    tracer: Any = None,
) -> QuickRAG[Answer]: ...


def rag(
    sources: Source | str | Path | dict[str, Source | str | Path],
    *,
    answer_type: type[BaseModel] | None = None,
    answer_system: str = RAG_ANSWER_SYSTEM,
    limit: int = 5,
    question_type: type[BaseModel] = Question,
    doc_type: type[BaseModel] = Doc,
    doc_factory: DocBuilder | None = None,
    answer_factory: AnswerBuilder | None = None,
    doc_text: Callable[[BaseModel], str] | None = None,
    doc_locator: Callable[[BaseModel], str] | None = None,
    llm: LLMProvider | None = None,
    resources: RuntimeResources | None = None,
    budget: Budget | None = None,
    tracer: Any = None,
) -> QuickRAG[Any]:
    """Retrieval-augmented answering over your own sources.

    `sources` is a `Source`, a path (a directory → keyword `FileSystemSource`;
    a `.csv` file → `CSVSource`), or a `{name: Source|path}` mapping. Plain
    paths get a stop-word- and light-plural-aware scorer, so they avoid the
    "matches on 'is' alone" footgun of the bare default (and a singular query
    still matches a plural in the text).

    Bring your own models: `question_type` (needs a `text` field),
    `doc_type`/`answer_type`. If a custom model doesn't accept the default
    fields (`Doc(text, locator, title)`, `Answer(text, sources)`), pass
    `doc_factory(context, ref, content)` / `answer_factory(text, docs)` to build
    it — and, for a doc model with a different body/label field, `doc_text`/
    `doc_locator` so the prompt and citations still read it. The returned
    `QuickRAG` is generic in the answer type, so `.ask()` preserves it.

    Pass `resources=` to fully own the `RuntimeResources` (e.g. a vector
    `EmbeddingSource` you constructed with your own embedder) — in that case
    `sources` is still used to build the pipeline, so register the same
    sources there.
    """
    resolved_answer_type: type[BaseModel] = (
        answer_type if answer_type is not None else Answer
    )
    normalized = _normalize_sources(sources)
    return QuickRAG(
        normalized,
        answer_type=resolved_answer_type,
        answer_system=answer_system,
        limit=limit,
        question_type=question_type,
        doc_type=doc_type,
        doc_factory=doc_factory,
        answer_factory=answer_factory,
        doc_text=doc_text,
        doc_locator=doc_locator,
        llm=llm,
        resources=resources,
        budget=budget,
        tracer=tracer,
    )


__all__ = ["QuickRAG", "RAG_ANSWER_SYSTEM", "rag"]
