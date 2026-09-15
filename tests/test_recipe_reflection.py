import asyncio

from pydantic import BaseModel
from reactifact import Agent, Consume, Context, Runtime
from reactifact.recipes import ReflectionLoop


class Topic(BaseModel):
    text: str


class Draft(BaseModel):
    topic: str = ""
    round: int = 0
    status: str = "draft"
    text: str = ""


class Review(BaseModel):
    topic: str = ""
    round: int = 0
    score: float = 0.0
    feedback: str = ""


class Final(BaseModel):
    topic: str = ""
    text: str = ""
    rounds: int = 0


# score sequence per topic text: round 0 fails, round 1 passes (unless topic
# text says "always low", which never passes -> exhausts max_rounds)
SCORES = {0: 0.4, 1: 0.9}


class MyLoop(ReflectionLoop[Topic, Draft, Review, Final]):
    topic_type = Topic
    draft_type = Draft
    review_type = Review
    final_type = Final
    accept_at = 0.8
    max_rounds = 2

    async def draft(self, context, topic):
        return Draft(text=f"v0: {topic.data.text}")

    async def critique(self, context, draft):
        if "always low" in draft.data.text:
            return 0.1, "still not good enough"
        score = SCORES.get(draft.data.round, 0.9)
        return score, f"feedback for round {draft.data.round}"

    async def rewrite(self, context, draft, feedback):
        return Draft(text=f"{draft.data.text} + fix({feedback})")

    async def finish(self, context, draft):
        return Final(text=draft.data.text, rounds=draft.data.round)


def make_runtime(ctx: Context) -> Runtime:
    class Flow(Agent):
        consumes = [Consume(Topic), Consume(Draft), Consume(Review)]
        produces = MyLoop().produces()

    return Runtime(ctx, agents=[Flow()])


def test_reflection_accepts_after_one_rewrite_round():
    ctx = Context()
    runtime = make_runtime(ctx)

    ctx.create(Topic(text="hydropower"))
    asyncio.run(runtime.arun())

    drafts = ctx.list_artifacts(Draft)
    assert len(drafts) == 1
    assert drafts[0].data.status == "accepted"
    assert drafts[0].data.round == 1

    reviews = ctx.list_artifacts(Review)
    assert sorted(r.data.round for r in reviews) == [0, 1]

    finals = ctx.list_artifacts(Final)
    assert len(finals) == 1
    assert finals[0].data.rounds == 1
    assert finals[0].data.text == "v0: hydropower + fix(feedback for round 0)"

    # provenance: final -> based_on -> the accepted draft
    linked = ctx.related(finals[0].id, relation="based_on")
    assert [a.id for a in linked] == [drafts[0].id]


def test_reflection_stops_at_max_rounds_when_never_accepted():
    ctx = Context()
    runtime = make_runtime(ctx)

    ctx.create(Topic(text="always low quality topic"))
    asyncio.run(runtime.arun())

    drafts = ctx.list_artifacts(Draft)
    assert drafts[0].data.status == "draft"  # never accepted
    assert drafts[0].data.round == 2  # capped at max_rounds

    reviews = ctx.list_artifacts(Review)
    assert sorted(r.data.round for r in reviews) == [0, 1, 2]

    finals = ctx.list_artifacts(Final)
    assert len(finals) == 1
    assert finals[0].data.rounds == 2


def test_reflection_supports_two_concurrent_topics():
    ctx = Context()
    runtime = make_runtime(ctx)

    ctx.create(Topic(text="alpha"))
    ctx.create(Topic(text="beta"))
    asyncio.run(runtime.arun())

    finals = {f.data.topic: f for f in ctx.list_artifacts(Final)}
    assert len(finals) == 2
    for f in finals.values():
        assert f.data.rounds == 1
