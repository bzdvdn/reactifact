"""Authorization — a principal and a permission policy for a run (§57).

reactifact has no server, so unlike a hosted platform it can enforce access in
the library itself, at the points that actually matter: which artifact types an
agent may create/update/delete, which agent may run, and which tool may
execute. `RuntimeResources(principal=..., authorizer=...)` turns it on.

    from reactifact.authz import PermissionPolicy, Principal

    resources = RuntimeResources(
        principal=Principal("alice", capabilities=("analyst",)),
        authorizer=PermissionPolicy(grants={"analyst": {"run:*", "create:Answer"}}),
    )

A principal is whoever the run acts for (an end user, a tenant, a service).
The default is `None`, and with no authorizer every action is allowed — authz
is opt-in and does not change existing behavior. Denials raise
`AuthorizationError` from `Runtime.arun()` (or that agent's generation is
dropped when `isolate_errors=True`).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class Principal(BaseModel):
    """Who a run acts for: an id plus the capabilities it holds.

    Kept a plain model so it composes with artifacts and sessions (it can be
    seeded as an artifact, loaded from a session, or restored by a `SeedIdentity`
    recipe) rather than living only in `RuntimeResources`.
    """

    id: str
    capabilities: tuple[str, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)


class AuthorizationError(PermissionError):
    """Raised when a principal may not perform an action on a resource."""

    def __init__(
        self, action: str, resource: str, principal: Principal | None = None
    ) -> None:
        self.action = action
        self.resource = resource
        self.principal = principal
        who = principal.id if principal is not None else "anonymous"
        super().__init__(f"principal {who!r} may not {action} {resource!r}")


@runtime_checkable
class Authorizer(Protocol):
    """Structural interface: `authorize(principal, action, resource) -> bool`."""

    def authorize(
        self, principal: Principal | None, action: str, resource: str
    ) -> bool: ...


def _permission_matches(pattern: str, action: str, resource: str) -> bool:
    """Matches `"action:resource"`, `"action:*"`, `"*:resource"` or `"*"`."""
    if pattern == "*":
        return True
    if ":" not in pattern:
        return pattern == action
    allowed_action, _, allowed_resource = pattern.partition(":")
    return (allowed_action in ("*", action)) and (allowed_resource in ("*", resource))


@dataclass
class PermissionPolicy:
    """Capability-based permissions: a capability grants `action:resource` patterns.

    `grants` maps a capability name to the patterns it allows, e.g.
    `{"analyst": {"run:*", "create:Answer"}, "admin": {"*"}}`. A principal is
    allowed an action when any of its capabilities grants a matching pattern.
    With `default_allow=False` (the default) an anonymous/unknown principal is
    denied; set it `True` to allow by default and use the grants as the
    allowlist an audit needs.
    """

    grants: Mapping[str, Iterable[str]] = field(default_factory=dict)
    default_allow: bool = False

    def authorize(
        self, principal: Principal | None, action: str, resource: str
    ) -> bool:
        if principal is None:
            return self.default_allow
        for capability in principal.capabilities:
            for pattern in self.grants.get(capability, ()):
                if _permission_matches(pattern, action, resource):
                    return True
        return self.default_allow

    def require(self, principal: Principal | None, action: str, resource: str) -> None:
        if not self.authorize(principal, action, resource):
            raise AuthorizationError(action, resource, principal)


__all__ = [
    "AuthorizationError",
    "Authorizer",
    "PermissionPolicy",
    "Principal",
]
