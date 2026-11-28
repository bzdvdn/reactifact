import asyncio

from pydantic import BaseModel
from reactifact import Agent, Consume, Context, Patch, Runtime
from reactifact.patches import Link, Relation, Unlink, operation_from_dict


class Claim(BaseModel):
    statement: str


class Evidence(BaseModel):
    content: str


class Linker(Agent):
    consumes = [Consume(Claim)]

    async def run(self, event, context):
        claim = context.get(event.artifact_id)
        evidence = context.create(Evidence(content=claim.data.statement))
        return Patch().link(claim.id, "supported_by", evidence.id)


def test_link_via_patch_applies_and_records_commit():
    ctx = Context()
    runtime = Runtime(ctx, agents=[Linker()])
    claim = ctx.create(Claim(statement="GPU costs rose"))
    asyncio.run(runtime.arun())

    rels = ctx.relations(source_id=claim.id)
    assert len(rels) == 1
    assert rels[0].relation == "supported_by"
    evidence = ctx.related(claim.id)[0]
    assert isinstance(evidence.data, Evidence)
    # the link operation landed in the commit (reproducibility)
    ops = ctx.history()[-1].operations
    assert any(isinstance(op, Link) for op in ops)


def test_patch_sugar_builds_ops():
    patch = Patch().link("a", "derived_from", "b").unlink("a", "derived_from", "b")
    assert isinstance(patch.operations[0], Link)
    assert isinstance(patch.operations[1], Unlink)


def test_unlink_variants():
    ctx = Context()
    for target in ("t1", "t2", "t3"):
        ctx.create(Evidence(content=target), id=target)
    ctx.link("a", "supported_by", "t1")
    ctx.link("a", "supported_by", "t2")
    ctx.link("a", "contradicted_by", "t3")

    # targeted removal
    assert ctx.unlink("a", "supported_by", "t1") == 1
    assert len(ctx.relations("a")) == 2

    # by relation (any target)
    assert ctx.unlink("a", "supported_by") == 1
    assert len(ctx.relations("a")) == 1

    # by target
    assert ctx.unlink("a", target_id="t3") == 1
    assert ctx.relations("a") == []


def test_relation_query_filters_and_incoming():
    ctx = Context()
    ctx.create(Claim(statement="c"), id="c")
    ctx.create(Evidence(content="e1"), id="e1")
    ctx.create(Evidence(content="e2"), id="e2")
    ctx.link("c", "supported_by", "e1")
    ctx.link("c", "supported_by", "e2")
    ctx.link("c", "contradicted_by", "e1")

    assert len(ctx.relations("c")) == 3
    assert len(ctx.relations(source_id="c", relation="supported_by")) == 2
    assert len(ctx.relations(target_id="e1")) == 2
    assert len(ctx.incoming("e1")) == 2
    assert len(ctx.incoming("e1", relation="supported_by")) == 1
    assert [r.target_id for r in ctx.relations("c", "supported_by")] == ["e1", "e2"]


def test_link_is_idempotent():
    ctx = Context()
    ctx.link("a", "rel", "b")
    ctx.link("a", "rel", "b")
    assert len(ctx.relations()) == 1


def test_related_skips_dangling_targets():
    ctx = Context()
    ctx.create(Claim(statement="c"), id="c")
    ctx.link("c", "supported_by", "missing")
    ctx.link("c", "supported_by", "c")
    targets = ctx.related("c")
    assert len(targets) == 1
    assert targets[0].id == "c"


def test_dangling_relations_detect_deleted_endpoints():
    ctx = Context()
    claim = ctx.create(Claim(statement="c"))
    evidence = ctx.create(Evidence(content="e"))
    ctx.link(claim.id, "supported_by", evidence.id)
    ctx.delete(evidence.id)
    dangling = ctx.dangling_relations()
    assert len(dangling) == 1
    assert dangling[0].target_id == evidence.id


def test_relations_survive_snapshot_roundtrip():
    ctx = Context()
    claim = ctx.create(Claim(statement="c"))
    ctx.link(claim.id, "supported_by", "some-evidence")
    data = ctx.to_dict()
    restored = Context.from_dict(data)
    rels = restored.relations(claim.id)
    assert len(rels) == 1
    assert rels[0].relation == "supported_by"
    assert rels[0].target_id == "some-evidence"


def test_checkout_rolls_back_committed_links_keeps_working_tree():
    ctx = Context()
    runtime = Runtime(ctx, agents=[Linker()])
    claim = ctx.create(Claim(statement="c"))
    asyncio.run(runtime.arun())
    assert len(ctx.relations(claim.id)) == 1  # via commit

    # working tree (outside commits)
    ctx.link(claim.id, "contradicted_by", "manual")

    ctx.checkout(ctx.version - 1)  # revert the linker's commit
    assert len(ctx.relations(claim.id)) == 1
    assert ctx.relations(claim.id)[0].relation == "contradicted_by"


def test_merge_from_unions_relations():
    left = Context()
    claim = left.create(Claim(statement="c"))
    left.link(claim.id, "supported_by", "e1")
    right = Context()
    right.link(claim.id, "contradicted_by", "e2")
    left.merge_from(right)
    rels = {r.relation for r in left.relations(claim.id)}
    assert rels == {"supported_by", "contradicted_by"}
    ops = left.history()[-1].operations
    assert any(isinstance(op, Link) for op in ops)


def test_merge_from_preserves_id_of_artifact_absent_from_target():
    left = Context()
    right = Context()
    right.create(Claim(statement="c"), id="claim-1")
    left.merge_from(right)
    merged = left.get("claim-1")
    assert merged is not None
    assert merged.data.statement == "c"


def test_link_unlink_dict_roundtrip():
    link = Link(artifact_id="a", relation="r", target_id="b")
    unlink = Unlink(artifact_id="a", relation="r")
    for op in (link, unlink):
        restored = operation_from_dict(op.to_dict())
        assert restored.to_dict() == op.to_dict()


def test_relation_from_dict():
    rel = Relation.from_dict({"source_id": "a", "relation": "r", "target_id": "b"})
    assert rel.to_dict() == {"source_id": "a", "relation": "r", "target_id": "b"}
