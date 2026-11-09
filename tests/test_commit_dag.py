from pydantic import BaseModel
from reactifact import Context
from reactifact.commit import Commit
from reactifact.operations import Create


class Note(BaseModel):
    text: str


def test_normal_commits_have_one_parent_and_merge_commits_have_two():
    context = Context()
    context.apply_commit(
        Commit(
            author="a",
            message="one",
            operations=[Create(Note(text="one"), id="one")],
        )
    )
    assert context.history()[-1].parent_ids == []

    context.apply_commit(
        Commit(
            author="a",
            message="two",
            operations=[Create(Note(text="three"), id="three")],
        )
    )
    target_head = context.head_id
    assert context.history()[-1].parent_ids == [context.history()[-2].id]

    branch = context.branch(name="b")
    branch.apply_commit(
        Commit(
            author="b",
            message="three",
            operations=[Create(Note(text="four"), id="four")],
        )
    )
    branch_head = branch.head_id

    context.merge(branch)
    merge_commit = context.history()[-1]
    assert set(merge_commit.parent_ids) == {target_head, branch_head}
    assert merge_commit.parent_id in merge_commit.parent_ids

    graph = context.commit_graph()
    assert graph[merge_commit.id] == merge_commit.parent_ids

    restored = Context.from_dict(context.to_dict())
    assert restored.history()[-1].parent_ids == merge_commit.parent_ids


def test_commit_ids_are_content_addressed_and_deterministic():
    def make(message: str) -> Context:
        context = Context()
        context.apply_commit(
            Commit(
                author="a",
                message=message,
                operations=[Create(Note(text="x"), id="x")],
            )
        )
        return context

    first = make("same")
    second = make("same")
    assert first.head_id == second.head_id
    assert len(first.head_id or "") == 64

    different = make("different")
    assert different.head_id != first.head_id
