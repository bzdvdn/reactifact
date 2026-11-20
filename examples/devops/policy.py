"""Demo identities and authorization policy for the devops app.

`X-User` is demo authentication, not proof of identity: anyone can claim it.
A real app resolves the principal from a verified session/JWT and maps roles to
capabilities; the `PermissionPolicy` below is the part that stays.

This module is deliberately pure policy — no storage, providers or resource
composition (that lives in `resources.py`).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from reactifact.authz import PermissionPolicy, Principal

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


__all__ = ["POLICY", "PRINCIPALS", "principal_for", "user_from_request"]
