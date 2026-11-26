"""The recipes (reactive patterns) behave as documented when used directly."""

import asyncio
from pathlib import Path

from pydantic import BaseModel
from reactifact import (
    Consume,
    Context,
    Effects,
    Runtime,
    RuntimeResources,
    Step,
    StepCall,
    StepGroup,
    create_step_group,
)
from reactifact.artifacts import Artifact
from reactifact.recipes import (
    PrefixedEphemeralCleanup,
    StatusMachine,
    fan_out_sources,
    materialize_doc,
    search_sources,
    source_ref_id,
)
from reactifact.sources import FileSystemSource, Source, SourceRef


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

    class Scout(Step[SourceRef]):
        artifact_type = SourceRef

        async def run(self, call: StepCall):
            await fan_out_sources(
                call.context,
                "vitamin D nutritional supplementation",
                owner_id="job1",
                limit=2,
            )
            call.effects.create(Job(query_id="job1", text="scouted"), id="marker:job1")
            return None

    class Engine(StepGroup):
        consumes = (Consume(Job),)
        produces = [Scout(), Step(Job)]

    ctx = Context(resources=RuntimeResources(sources={"papers": build_pages(tmp_path)}))
    runtime = Runtime(ctx, groups=[Engine()])
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

    class Scout(Step[SourceRef]):
        artifact_type = SourceRef

        async def run(self, call: StepCall):
            await fan_out_sources(
                call.context, "prevents colds", owner_id="q1", limit=1
            )
            return None

    class Resolver(Step[Doc]):
        artifact_type = Doc

        async def run(self, call: StepCall):
            ref_art = call.trigger
            if ref_art is None or not isinstance(ref_art.data, SourceRef):
                return None

            def factory(_ctx: Context, _ref: Artifact[SourceRef], content: str) -> Doc:
                return Doc(
                    query_id=_ref.data.query_id,
                    path=_ref.data.locator,
                    content=content,
                )

            await materialize_doc(
                call.context, ref_art, factory, relation="resolved_from"
            )
            return None

    class Engine(StepGroup):
        consumes = [Consume(Job), Consume(SourceRef)]
        produces = [Scout(), Resolver()]

    ctx = Context(resources=RuntimeResources(sources={"papers": build_pages(tmp_path)}))
    runtime = Runtime(ctx, groups=[Engine()])
    ctx.create(Job(query_id="q1", status="pending"))
    asyncio.run(runtime.arun())

    docs = ctx.list_artifacts(Doc)
    assert docs
    doc = docs[0]
    assert doc.data.query_id == "q1"
    assert len(ctx.related(doc.id, relation="resolved_from")) == 1


class Fill(Step[Job]):
    artifact_type = Job

    async def run(self, call: StepCall):
        target = call.trigger
        if target is None:
            return None
        call.effects.update(target, text="filled")
        return None


class Survey(StatusMachine[Job]):
    terminal = frozenset({"done"})

    def next_status(self, state):
        jobs = state.all(Job)
        return "done" if jobs and any(j.data.text for j in jobs) else None


class Engine(StepGroup):
    consumes = [Consume(Job)]
    produces = [Fill(), Survey()]


def test_status_machine_advances_lifecycle():
    ctx = Context()
    runtime = Runtime(ctx, groups=[Engine()])
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


class MakeScratch(Step[Scratch]):
    artifact_type = Scratch

    async def run(self, call: StepCall):
        question = call.trigger
        if question is None or not isinstance(question.data, Question):
            return None
        call.effects.create_once(
            Scratch(query_id=question.id, text=question.data.text),
            id=f"scratch:{question.id}",
        )


class MakeFinal(Step[Final]):
    artifact_type = Final

    async def run(self, call: StepCall):
        if call.event is None:
            return None
        scratch = call.trigger
        if scratch is None or not isinstance(scratch.data, Scratch):
            return None
        call.effects.create_once(
            Final(text=scratch.data.text.upper()), id=f"final:{scratch.data.query_id}"
        )


class Cleanup(PrefixedEphemeralCleanup[Final]):
    artifact_type = Final
    scratch_prefixes = ("scratch",)

    def correlation_of(self, terminal_artifact_id):
        if not terminal_artifact_id.startswith("final:"):
            return None
        return terminal_artifact_id[len("final:") :]


class CleanupEngine(StepGroup):
    consumes = [Consume(Question), Consume(Scratch), Consume(Final)]
    produces = [MakeScratch(), MakeFinal(), Cleanup()]


def test_ephemeral_cleanup_deletes_scratch_once_terminal_exists():
    ctx = Context()
    runtime = Runtime(ctx, groups=[CleanupEngine()])
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
    runtime = Runtime(ctx, groups=[CleanupEngine()])
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

    class IdentityAgent(StepGroup):
        consumes = [Consume(Question)]
        produces = [SeedIdentity(UserContext, identity_var, identity_id="user_context")]

    ctx = Context()
    runtime = Runtime(ctx, groups=[IdentityAgent()])
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

    identity_var: ContextVar[RequestIdentity | None] = ContextVar(
        "identity", default=None
    )

    class IdentityAgent(StepGroup):
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
    runtime = Runtime(ctx, groups=[IdentityAgent()])
    token = identity_var.set(
        RequestIdentity(session_id="s1", user=UserContext(uid="bob"))
    )
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

    class IdentityAgent(StepGroup):
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
    runtime = Runtime(ctx, groups=[IdentityAgent()])
    # No identity_var.set(...) — the contextvar keeps its default (None).
    ctx.create(Question(text="hi"))
    asyncio.run(runtime.arun())

    assert ctx.get("user_context").data.uid == "unknown"


def test_seed_identity_skips_the_write_when_unset_and_no_fallback():
    from contextvars import ContextVar

    from reactifact.recipes import SeedIdentity

    identity_var: ContextVar[UserContext | None] = ContextVar("identity", default=None)

    class IdentityAgent(StepGroup):
        consumes = [Consume(Question)]
        produces = [SeedIdentity(UserContext, identity_var, identity_id="user_context")]

    ctx = Context()
    runtime = Runtime(ctx, groups=[IdentityAgent()])
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


def test_keyword_score_folds_english_plurals():
    from reactifact.recipes import keyword_score

    text = "Refunds are available within 14 days."
    # without folding, a singular query misses the plural in the text
    assert keyword_score(text, "refund") == 0.0
    assert keyword_score(text, "refund", fold_plurals=True) == 1.0
    # conservative: "status"/"class" are not naively de-pluralized
    assert keyword_score("status page", "status", fold_plurals=True) == 1.0
    assert keyword_score("policies apply", "policy", fold_plurals=True) == 1.0


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


def test_fan_out_sources_as_artifacts_then_materialize_doc(tmp_path):
    """`as_artifacts=True` gives handles that materialize_doc can link directly."""

    class ScoutResolve(Step[SourceRef]):
        produces = (SourceRef, Doc)
        consumes = (Consume(Job),)

        async def run(self, call: StepCall) -> None:
            handles = await fan_out_sources(
                call.context,
                "prevents colds",
                owner_id="handles",
                limit=1,
                as_artifacts=True,
            )
            assert handles
            await materialize_doc(
                call.context,
                handles[0],
                doc_type=Doc,
                text_field="content",
                field_map={"path": "locator"},
            )
            return None

    ctx = Context(resources=RuntimeResources(sources={"papers": build_pages(tmp_path)}))
    ctx.create(Job(query_id="handles", status="pending"))
    runtime = Runtime(ctx, steps=[ScoutResolve()])
    asyncio.run(runtime.arun())

    docs = ctx.list_artifacts(Doc)
    assert len(docs) == 1
    assert docs[0].data.query_id == "handles"
    assert docs[0].data.content
    assert ctx.related(docs[0].id, relation="materialized_from")


class CountingSource(Source):
    def __init__(self) -> None:
        super().__init__("count")
        self.calls = 0

    async def resolve(self, ref: SourceRef) -> str:
        self.calls += 1
        return "payload"


def test_materialize_doc_reuses_an_existing_document():
    source = CountingSource()

    class First(Step[Doc]):
        produces = (SourceRef, Doc)
        consumes = (Consume(Job),)

        async def run(self, call: StepCall) -> None:
            await materialize_doc(
                call.context,
                SourceRef(source_id="count", locator="x"),
                doc_type=Doc,
                text_field="content",
                field_map={"path": "locator"},
                refresh=False,
            )
            return None

    class Again(Step[Doc]):
        produces = (SourceRef, Doc)
        consumes = (Consume(Doc),)

        async def run(self, call: StepCall) -> None:
            await materialize_doc(
                call.context,
                SourceRef(source_id="count", locator="x"),
                doc_type=Doc,
                text_field="content",
                field_map={"path": "locator"},
                refresh=False,
            )
            return None

    ctx = Context(resources=RuntimeResources(sources={"count": source}))
    ctx.create(Job(query_id="reuse", status="pending"))
    asyncio.run(Runtime(ctx, steps=[First(), Again()]).arun())

    assert source.calls == 1
    assert len(ctx.list_artifacts(Doc)) == 1


def test_materialize_doc_reports_a_missing_source():
    errors: list[tuple[SourceRef, BaseException | None]] = []

    class Missing(Step[Doc]):
        produces = (Doc,)
        consumes = (Consume(Job),)

        async def run(self, call: StepCall) -> None:
            result = await materialize_doc(
                call.context,
                SourceRef(source_id="missing", locator="x"),
                doc_type=Doc,
                text_field="content",
                on_error=lambda ref, exc: errors.append((ref, exc)),
            )
            assert result is None
            return None

    ctx = Context(resources=RuntimeResources())
    ctx.create(Job(query_id="missing", status="pending"))
    asyncio.run(Runtime(ctx, steps=[Missing()]).arun())

    assert errors
    assert errors[0][0].source_id == "missing"
    assert errors[0][1] is None


def test_status_machine_from_funcs_and_state_status():
    transitions: list[tuple[str, str, str]] = []
    machine = StatusMachine.from_funcs(
        artifact_type=Job,
        terminal={"done"},
        next_status=lambda state: "done" if state.status == "pending" else None,
        on_transition=lambda ctx, key, old, new: transitions.append((key, old, new)),
    )
    group = create_step_group("status", consumes=[Consume(Job)], steps=[machine])
    ctx = Context()
    ctx.create(Job(query_id="func", status="pending"))
    asyncio.run(Runtime(ctx, groups=[group]).arun())

    assert ctx.list_artifacts(Job)[0].data.status == "done"
    assert transitions == [("func", "pending", "done")]


def test_status_machine_advances_the_latest_target_for_a_key():
    machine = StatusMachine.from_funcs(
        artifact_type=Job,
        next_status=lambda state: (
            "done" if state.target.data.text == "newest" else None
        ),
    )
    group = create_step_group("status", consumes=[Consume(Job)], steps=[machine])
    ctx = Context()
    first = ctx.create(Job(query_id="same", status="pending", text="old"))
    second = ctx.create(Job(query_id="same", status="pending", text="newest"))
    asyncio.run(Runtime(ctx, groups=[group]).arun())

    assert ctx.get(first.id).data.status == "pending"
    assert ctx.get(second.id).data.status == "done"


def test_search_sources_is_pure(tmp_path):
    ctx = Context(resources=RuntimeResources(sources={"papers": build_pages(tmp_path)}))

    refs = asyncio.run(search_sources(ctx, "vitamin D", owner_id="job1", limit=2))

    assert refs
    assert all(ref.metadata.get("owner_id") == "job1" for ref in refs)
    assert ctx.list_artifacts(SourceRef) == []  # no effects written


def test_source_ref_id_is_stable(tmp_path):
    ref = SourceRef(source_id="papers", locator="a.md")
    assert source_ref_id(ref, "job1") == f"ref:{ref.stable_id()}:job1"


def test_fan_out_sources_accepts_an_explicit_effect_slot(tmp_path):
    ctx = Context(resources=RuntimeResources(sources={"papers": build_pages(tmp_path)}))
    slot = Effects(ctx)

    refs = asyncio.run(fan_out_sources(ctx, "vitamin D", owner_id="job1", effects=slot))

    assert refs
    assert slot.operations  # the refs were queued in the explicit slot
