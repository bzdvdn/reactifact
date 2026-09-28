"""Authorization: principal × permission policy (§57)."""

import asyncio

import pytest
from pydantic import BaseModel
from reactifact import (
    Consume,
    Context,
    Produce,
    Runtime,
    RuntimeResources,
    create_agent,
)
from reactifact.authz import (
    AuthorizationError,
    PermissionPolicy,
    Principal,
)


class Question(BaseModel):
    text: str


class Note(BaseModel):
    text: str


class Rewrite(Produce[Note]):
    artifact_type = Note

    async def produce(self, call):
        call.effects.create(Note(text=call.trigger.data.text))


def _run(resources):
    ctx = Context(resources=resources)
    ctx.create(Question(text="hi"))
    agent = create_agent("rewrite", consumes=[Consume(Question)], produces=[Rewrite()])
    asyncio.run(Runtime(ctx, agents=[agent]).arun())
    return ctx


def _principal(*capabilities: str) -> Principal:
    return Principal(id="alice", capabilities=tuple(capabilities))


# --------------------------------------------------------------------------- #
# Policy
# --------------------------------------------------------------------------- #


def test_permission_matches_wildcards_and_specifics():
    policy = PermissionPolicy(
        {
            "admin": {"*"},
            "analyst": {"run:*", "create:Answer"},
            "tooler": {"execute:search"},
        }
    )
    assert policy.authorize(_principal("admin"), "delete", "anything")
    assert policy.authorize(_principal("analyst"), "run", "planner")
    assert policy.authorize(_principal("analyst"), "create", "Answer")
    assert not policy.authorize(_principal("analyst"), "create", "Note")
    assert policy.authorize(_principal("tooler"), "execute", "search")
    assert not policy.authorize(_principal("tooler"), "execute", "delete")


def test_anonymous_and_default_allow():
    strict = PermissionPolicy({"admin": {"*"}})
    assert not strict.authorize(None, "run", "x")
    assert not strict.authorize(_principal("nobody"), "run", "x")
    open_policy = PermissionPolicy(default_allow=True)
    assert open_policy.authorize(None, "run", "x")


def test_require_raises_authorization_error():
    policy = PermissionPolicy({"admin": {"*"}})
    with pytest.raises(AuthorizationError) as excinfo:
        policy.require(_principal("nobody"), "execute", "deploy")
    assert "deploy" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# Resources + runtime
# --------------------------------------------------------------------------- #


def test_resources_allow_without_authorizer():
    resources = RuntimeResources()
    assert resources.authorize("create", "Note")
    resources.require_authorized("create", "Note")  # no raise


def test_runtime_denies_unpermitted_artifact_type():
    resources = RuntimeResources(
        principal=_principal("reader"),
        authorizer=PermissionPolicy({"reader": {"run:*"}}),
    )
    with pytest.raises(AuthorizationError):
        _run(resources)


def test_runtime_allows_when_capability_grants_the_write():
    resources = RuntimeResources(
        principal=_principal("writer"),
        authorizer=PermissionPolicy({"writer": {"run:*", "create:Note"}}),
    )
    ctx = _run(resources)
    assert ctx.latest(Note) is not None


def test_runtime_denies_the_agent_run_itself():
    resources = RuntimeResources(
        principal=_principal("reader"),
        authorizer=PermissionPolicy({"reader": {"create:*"}}),
    )
    with pytest.raises(AuthorizationError):
        _run(resources)
