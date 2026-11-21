"""Per-turn resources composition (LLM, embedder, principal, authz)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from reactifact import RuntimeResources

from app.authz import POLICY, principal_for
from app.config import Settings
from app.llm import build_llm


def build_resources(
    settings: Settings,
    request: Mapping[str, Any] | None,
) -> RuntimeResources:
    user = request.get("user") if request else None
    if user is None:
        raise ValueError("build_resources needs request['user'] (the principal)")
    return RuntimeResources(
        llm=build_llm(settings),
        principal=principal_for(user),
        authorizer=POLICY,
    )


__all__ = ["build_resources"]
