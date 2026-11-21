"""Per-tick resources for the daemon."""

from __future__ import annotations

from reactifact import RuntimeResources
from reactifact.authz import Principal

from app.config import Settings
from app.llm import build_llm


def build_resources(settings: Settings) -> RuntimeResources:
    return RuntimeResources(
        llm=build_llm(settings),
        principal=Principal(id=settings.principal_id, capabilities=("service",)),
    )


__all__ = ["build_resources"]
