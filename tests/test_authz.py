"""Authorization: principal × permission policy (§57)."""

import asyncio

import pytest
from pydantic import BaseModel
from reactifact import (
    Agent,
    Consume,
    Context,
    Runtime,
    RuntimeResources,
    Step,
)
from reactifact.authz import (
    Access,
    AuthorizationError,
    PermissionPolicy,
    Principal,
)


class Question(BaseModel):
    text: str


class Note(BaseModel):
    text: str


class Rewrite(Step[Note]):
    artifact_type = Note

    async def run(self, call):
        call.effects.create(Note(text=call.trigger.data.text))


def _run(resources):
    ctx = Context(resources=resources)
    ctx.create(Question(text="hi"))
    agent = Agent("rewrite", consumes=[Consume(Question)], steps=[Rewrite()])
    asyncio.run(Runtime(ctx, groups=[agent]).arun())
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


def test_access_defaults_to_allow_without_an_authorizer():
    access = RuntimeResources().access
    assert isinstance(access, Access)
    assert access.can("run", "planner")
    access.require("run", "planner")  # no raise
    assert access.principal is None


def test_access_can_and_require_reflect_the_policy():
    resources = RuntimeResources(
        principal=_principal("reader"),
        authorizer=PermissionPolicy({"reader": {"run:*", "create:Note"}}),
    )
    access = resources.access
    assert access.principal == _principal("reader")
    assert access.can("run", "planner")
    assert access.can("create", "Note")
    assert not access.can("create", "Answer")
    with pytest.raises(AuthorizationError, match="create"):
        access.require("create", "Answer")


def test_access_is_injectable_into_a_step():
    class Probe(Step):
        consumes = (Question,)
        produces = (Note,)

        async def run(self, access: Access) -> Note:
            return Note(text="yes" if access.can("run", "rewrite") else "no")

    resources = RuntimeResources(
        principal=_principal("reader"),
        authorizer=PermissionPolicy({"reader": {"run:rewrite", "create:Note"}}),
    )
    context = Context(resources=resources)
    context.create(Question(text="hi"))
    agent = Agent("rewrite", steps=[Probe()])
    asyncio.run(Runtime(context, groups=[agent]).arun())
    assert context.latest(Note).data.text == "yes"
