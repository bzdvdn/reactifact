"""The recipes (reactive patterns) behave as documented when used directly."""

import asyncio
from pathlib import Path

from pydantic import BaseModel
from reactifact import Agent, Consume, Context, Produce, Runtime, RuntimeResources
from reactifact.artifacts import Artifact
from reactifact.recipes import (
    PrefixedEphemeralCleanup,
    StatusMachine,
    fan_out_sources,
    materialize_doc,
)
from reactifact.sources import FileSystemSource, SourceRef


class Job(BaseModel):
    query_id: str
    status: str = "pending"
    text: str = ""


class Doc(BaseModel):
    query_id: str
    path: str
    content: str


def build_pages(tmp_path) -> FileSystemSource:
    pages = Path(tmp_path) / "pages"
    pages.mkdir()
    (pages / "a.md").write_text(
        "vitamin D supplementation prevents colds.", encoding="utf-8"
    )
    (pages / "b.md").write_text("unrelated content about cars.", encoding="utf-8")
    return FileSystemSource(str(pages), source_id="papers")


def test_fan_out_sources_builds_owner_tagged_refs(tmp_path):
    """fan_out_sources writes idempotent refs into the produce's effects slot."""

    class Scout(Produce[SourceRef]):
        artifact_type = SourceRef

        async def produce(self, context, inputs, event=None):
            await fan_out_sources(
                context,
                "vitamin D nutritional supplementation",
                owner_id="job1",
                limit=2,
            )
            self.effects.create(Job(query_id="job1", text="scouted"), id="marker:job1")
            return None

    class Engine(Agent):
        consumes = [Consume(Job)]
        produces = [Scout(), Produce(Job)]

    ctx = Context(resources=RuntimeResources(sources={"papers": build_pages(tmp_path)}))
    runtime = Runtime(ctx, agents=[Engine()])
    ctx.create(Job(query_id="job1", status="pending"))
    asyncio.run(runtime.arun())

    refs = ctx.list_artifacts(SourceRef)
    assert refs
    assert all(r.data.metadata.get("owner_id") == "job1" for r in refs)
    assert all(r.data.query_id == "job1" for r in refs)
    assert all(r.id.startswith("ref:") for r in refs)
    # the effects slot compiled and committed both the refs and the marker
    assert ctx.get("marker:job1") is not None


def test_materialize_doc_builds_doc_with_provenance(tmp_path):
    """materialize_doc creates the doc + link in effects (resolved_from, §34)."""

    class Scout(Produce[SourceRef]):
        artifact_type = SourceRef

        async def produce(self, context, inputs, event=None):
            await fan_out_sources(context, "prevents colds", owner_id="q1", limit=1)
            return None

    class Resolver(Produce[Doc]):
        artifact_type = Doc

        async def produce(self, context, inputs, event=None):
            ref_art = context.get(event.artifact_id) if event is not None else None
            if ref_art is None or not isinstance(ref_art.data, SourceRef):
                return None

            def factory(_ctx: Context, _ref: Artifact[SourceRef], content: str) -> Doc:
                return Doc(
                    query_id=_ref.data.query_id,
                    path=_ref.data.locator,
                    content=content,
                )

            await materialize_doc(context, ref_art, factory, relation="resolved_from")
            return None

    class Engine(Agent):
        consumes = [Consume(Job), Consume(SourceRef)]
        produces = [Scout(), Resolver()]

    ctx = Context(resources=RuntimeResources(sources={"papers": build_pages(tmp_path)}))
    runtime = Runtime(ctx, agents=[Engine()])
    ctx.create(Job(query_id="q1", status="pending"))
    asyncio.run(runtime.arun())

    docs = ctx.list_artifacts(Doc)
    assert docs
    doc = docs[0]
    assert doc.data.query_id == "q1"
    assert len(ctx.related(doc.id, relation="resolved_from")) == 1


class Fill(Produce[Job]):
    artifact_type = Job

    async def produce(self, context, inputs, event=None):
        target = context.get(event.artifact_id)
        if target is None:
            return None
        self.effects.update(target, text="filled")
        return None


class Survey(StatusMachine[Job]):
    artifact_type = Job
    terminal = frozenset({"done"})

    def next_status(self, context, key):
        jobs = [j for j in context.list_artifacts(Job) if j.data.query_id == key]
        return "done" if jobs and any(j.data.text for j in jobs) else None


class Engine(Agent):
    consumes = [Consume(Job)]
    produces = [Fill(), Survey()]


def test_status_machine_advances_lifecycle():
    ctx = Context()
    runtime = Runtime(ctx, agents=[Engine()])
    ctx.create(Job(query_id="q1", status="pending"))
    asyncio.run(runtime.arun())
    job = ctx.list_artifacts(Job)[0]
    assert job.data.status == "done"


# --------------------------------------------------------------------------- #
# recipes.cleanup — delete scratch artifacts once a terminal one exists (§24, §42)
# --------------------------------------------------------------------------- #


class Question(BaseModel):
    text: str


class Scratch(BaseModel):
    query_id: str
    text: str = ""


class Final(BaseModel):
    text: str = ""


class MakeScratch(Produce[Scratch]):
    artifact_type = Scratch

    async def produce(self, context, inputs, event=None):
        question = context.get(event.artifact_id) if event is not None else None
        if question is None or not isinstance(question.data, Question):
            return None
        self.effects.create_once(
            Scratch(query_id=question.id, text=question.data.text),
            id=f"scratch:{question.id}",
        )


class MakeFinal(Produce[Final]):
    artifact_type = Final

    async def produce(self, context, inputs, event=None):
        if event is None:
            return None
        scratch = context.get(event.artifact_id)
        if scratch is None or not isinstance(scratch.data, Scratch):
            return None
        self.effects.create_once(
            Final(text=scratch.data.text.upper()), id=f"final:{scratch.data.query_id}"
        )


class Cleanup(PrefixedEphemeralCleanup[Final]):
    artifact_type = Final
    scratch_prefixes = ("scratch",)

    def correlation_of(self, terminal_artifact_id):
        if not terminal_artifact_id.startswith("final:"):
            return None
        return terminal_artifact_id[len("final:") :]


class CleanupEngine(Agent):
    consumes = [Consume(Question), Consume(Scratch), Consume(Final)]
    produces = [MakeScratch(), MakeFinal(), Cleanup()]


def test_ephemeral_cleanup_deletes_scratch_once_terminal_exists():
    ctx = Context()
    runtime = Runtime(ctx, agents=[CleanupEngine()])
    question = ctx.create(Question(text="hello"))
    asyncio.run(runtime.arun())

    assert ctx.get(f"scratch:{question.id}") is None
    final = ctx.list_artifacts(Final)
    assert len(final) == 1
    assert final[0].data.text == "HELLO"


def test_ephemeral_cleanup_is_idempotent_when_scratch_already_gone():
    """Deleting an already-absent scratch id must not raise — the terminal
    artifact created directly (skipping the scratch stage) is the simplest
    way to exercise that path."""
    ctx = Context()
    runtime = Runtime(ctx, agents=[CleanupEngine()])
    ctx.create(Final(text="DIRECT"), id="final:missing")
    asyncio.run(runtime.arun())

    assert ctx.get("scratch:missing") is None
    assert ctx.list_artifacts(Final)[0].data.text == "DIRECT"


# --------------------------------------------------------------------------- #
# recipes.identity — per-request data into a turn via a contextvar (§24, §42)
# --------------------------------------------------------------------------- #


class UserContext(BaseModel):
    uid: str


class RequestIdentity(BaseModel):
    """A richer per-request envelope — the shape `extract=` exists for."""

    session_id: str
    user: UserContext


def test_seed_identity_writes_the_contextvar_value_as_an_artifact():
    from contextvars import ContextVar

    from reactifact.recipes import SeedIdentity

    identity_var: ContextVar[UserContext | None] = ContextVar("identity", default=None)

    class IdentityAgent(Agent):
        consumes = [Consume(Question)]
        produces = [SeedIdentity(UserContext, identity_var, identity_id="user_context")]

    ctx = Context()
    runtime = Runtime(ctx, agents=[IdentityAgent()])
    token = identity_var.set(UserContext(uid="alice"))
    try:
        ctx.create(Question(text="hi"))
        asyncio.run(runtime.arun())
    finally:
        identity_var.reset(token)

    seeded = ctx.get("user_context")
    assert seeded is not None
    assert seeded.data.uid == "alice"


def test_seed_identity_extracts_from_a_richer_envelope():
    from contextvars import ContextVar

    from reactifact.recipes import SeedIdentity

    identity_var: ContextVar[RequestIdentity | None] = ContextVar("identity", default=None)

    class IdentityAgent(Agent):
        consumes = [Consume(Question)]
        produces = [
            SeedIdentity(
                UserContext,
                identity_var,
                identity_id="user_context",
                extract=lambda ri: ri.user,
            )
        ]

    ctx = Context()
    runtime = Runtime(ctx, agents=[IdentityAgent()])
    token = identity_var.set(RequestIdentity(session_id="s1", user=UserContext(uid="bob")))
    try:
        ctx.create(Question(text="hi"))
        asyncio.run(runtime.arun())
    finally:
        identity_var.reset(token)

    assert ctx.get("user_context").data.uid == "bob"


def test_seed_identity_uses_the_fallback_when_the_contextvar_is_unset():
    from contextvars import ContextVar

    from reactifact.recipes import SeedIdentity

    identity_var: ContextVar[UserContext | None] = ContextVar("identity", default=None)

    class IdentityAgent(Agent):
        consumes = [Consume(Question)]
        produces = [
            SeedIdentity(
                UserContext,
                identity_var,
                identity_id="user_context",
                fallback=lambda: UserContext(uid="unknown"),
            )
        ]

    ctx = Context()
    runtime = Runtime(ctx, agents=[IdentityAgent()])
    # No identity_var.set(...) — the contextvar keeps its default (None).
    ctx.create(Question(text="hi"))
    asyncio.run(runtime.arun())

    assert ctx.get("user_context").data.uid == "unknown"


def test_seed_identity_skips_the_write_when_unset_and_no_fallback():
    from contextvars import ContextVar

    from reactifact.recipes import SeedIdentity

    identity_var: ContextVar[UserContext | None] = ContextVar("identity", default=None)

    class IdentityAgent(Agent):
        consumes = [Consume(Question)]
        produces = [SeedIdentity(UserContext, identity_var, identity_id="user_context")]

    ctx = Context()
    runtime = Runtime(ctx, agents=[IdentityAgent()])
    ctx.create(Question(text="hi"))
    asyncio.run(runtime.arun())

    assert ctx.get("user_context") is None


# --------------------------------------------------------------------------- #
# recipes.text — deterministic keyword scoring (§67)
# --------------------------------------------------------------------------- #


def test_keyword_score_english_ignores_stopwords():
    from reactifact.recipes import EN_STOPWORDS, keyword_score

    text = "How to set up authentication and handle sessions securely"
    assert keyword_score(text, "set up authentication") == 1.0
    assert keyword_score(text, "authentication sessions", stopwords=EN_STOPWORDS) >= 0.5
    assert keyword_score("unrelated page", "authentication") == 0.0
    assert keyword_score(text, "") == 0.0  # empty query → no score


def test_keyword_score_russian_stems_match_inflections():
    from reactifact.recipes import keyword_score

    # «аутентификацию» and «аутентификация» share the stem «аутентификац»
    score = keyword_score("Установка аутентификации", "аутентификацию", use_stems=True)
    assert score == 1.0
    plain = keyword_score("Установка аутентификации", "аутентификацию", use_stems=False)
    assert plain == 0.0  # without stems the inflections do not match


def test_stem_words_splits_cyrillic_and_latin():
    from reactifact.recipes import stem_words

    stems = stem_words("Ремонт комнаты и kitchen")
    assert "ремонт" in stems
    assert "комнат" in stems
    assert "kitchen" in stems


# --------------------------------------------------------------------------- #
# recipes.rollback — change → rebuild (§22/§24)
# --------------------------------------------------------------------------- #

_FIELD_STAGES = {
    "room": "collect",
    "style": "design_choice",
    "area": "plan",
    "budget": "estimate",
}
_STAGE_ORDER = ("collect", "design_choice", "plan", "estimate")


class _Proj(BaseModel):
    room: str | None = None
    style: str | None = None
    area: float | None = None
    budget: float | None = None


def test_changed_fields_ignores_unknown_none():
    from reactifact.recipes import changed_fields

    old = _Proj(room="kitchen", budget=100)
    new = _Proj(room="bathroom", budget=None)  # budget unknown → not a change
    assert changed_fields(old, new) == {"room"}


def test_earliest_stage_routes_to_the_first_affected():
    from reactifact.recipes import earliest_stage

    assert (
        earliest_stage({"style"}, field_stages=_FIELD_STAGES, order=_STAGE_ORDER)
        == "design_choice"
    )
    assert (
        earliest_stage(
            {"area", "budget"}, field_stages=_FIELD_STAGES, order=_STAGE_ORDER
        )
        == "plan"
    )
    assert earliest_stage(set(), field_stages=_FIELD_STAGES, order=_STAGE_ORDER) is None


def test_downstream_fields_reset_inclusive_suffix():
    from reactifact.recipes import downstream_fields

    resets = downstream_fields("plan", field_stages=_FIELD_STAGES, order=_STAGE_ORDER)
    assert resets == frozenset(
        {"area", "budget"}
    )  # plan + estimate stay, upstream is kept
