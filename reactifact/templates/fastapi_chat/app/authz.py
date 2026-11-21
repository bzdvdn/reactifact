"""Authorization policy (authn lives in `app.security`, authz stays pure)."""

from __future__ import annotations

from reactifact.authz import PermissionPolicy, Principal

from app.models import User

POLICY = PermissionPolicy(
    grants={
        "admin": {"*"},
        "user": {
            "run:*",
            "create:*",
            "update:*",
            "delete:*",
            "read:*",
            "execute:*",
        },
    }
)


def principal_for(user: User) -> Principal:
    return Principal(id=user.id, capabilities=user.capabilities)


__all__ = ["POLICY", "principal_for"]
