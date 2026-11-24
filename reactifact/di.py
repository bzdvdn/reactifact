"""Explicit dependency markers for tools and other injectable parameters.

`RuntimeResources` resolves a plain-class annotation automatically. Use
`Inject()` when the type would otherwise look like data (a dataclass, a
protocol, a datetime) or when the intent should be obvious:

    k8s: Annotated[K8sClient, Inject()]

The runtime strips the marker before resolving and the parameter never enters
the model's JSON schema.

`Depends` is a subclass kept for FastAPI familiarity; prefer `Inject`, which
describes what actually happens (a registered collaborator is injected, not a
provider callable being invoked).
"""

from __future__ import annotations


class Inject:
    """Marks an `Annotated[T, Inject()]` parameter as an injected collaborator."""

    __slots__ = ()


class Depends(Inject):
    """FastAPI-style alias for `Inject`; both mean "inject the registered type"."""

    __slots__ = ()


__all__ = ["Depends", "Inject"]
