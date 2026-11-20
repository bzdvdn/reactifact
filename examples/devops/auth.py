"""Demo identities, capabilities and per-user resources for the devops app.

`X-User` is demo authentication, not proof of identity: anyone can claim it.
A real app resolves the principal from a verified session/JWT and maps roles to
capabilities; the `PermissionPolicy` below is the part that stays.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from examples.devops.guardrails import devops_guardrail_policy
from reactifact import RuntimeResources
from reactifact.authz import PermissionPolicy, Principal
from reactifact.checkpoints import FileKVBackend
from reactifact.recipes import MemoryStore

ROOT = Path(__file__).resolve().parent

PRINCIPALS: dict[str, Principal] = {
    "admin1": Principal(id="admin1", capabilities=("admin",)),
    "admin2": Principal(id="admin2", capabilities=("analyst",)),
}

POLICY = PermissionPolicy(
    grants={
        "admin": {"*"},
        # admin2 can chat, inspect k8s/GitLab and use memory, but cannot run
        # Ansible (the route refuses before the agent even starts).
        "analyst": {
            "run:route",
            "run:k8s",
            "run:gitlab",
            "run:render",
            "run:memory",
            "read:*",
            "create:*",
            "update:*",
            "execute:kubectl_get",
            "execute:gitlab_search",
            "execute:gitlab_pipeline",
        },
    }
)


def principal_for(user: str) -> Principal:
    try:
        return PRINCIPALS[user]
    except KeyError as exc:
        raise ValueError(f"unknown devops user {user!r}") from exc


def user_from_request(request: Mapping[str, Any] | None) -> str:
    """Reads the demo identity from a per-turn `request=` mapping."""
    if request:
        user = request.get("user")
        if isinstance(user, str) and user in PRINCIPALS:
            return user
    return "admin1"


def build_resources(
    llm: Any | None,
    user: str = "admin1",
    memory_dir: str | Path | None = None,
) -> RuntimeResources:
    """Per-user resources: authz principal + namespaced long-term memory."""
    memory = MemoryStore(
        FileKVBackend(str(Path(memory_dir) if memory_dir else ROOT / "memory")),
        namespace=f"user:{user}",
    )
    resources = RuntimeResources(
        llm=llm,
        guardrails=devops_guardrail_policy(),
        principal=principal_for(user),
        authorizer=POLICY,
    )
    resources.register(MemoryStore, memory)
    return resources


__all__ = [
    "POLICY",
    "PRINCIPALS",
    "build_resources",
    "principal_for",
    "user_from_request",
]
