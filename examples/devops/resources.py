"""Per-user resource composition for the devops app.

Policy lives in `policy.py`; this module wires it to concrete resources: the
guardrail policy, the authz principal and a namespaced `MemoryStore`. Keeping
the two apart lets policy be tested without a filesystem or a provider.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from examples.devops.guardrails import devops_guardrail_policy
from examples.devops.policy import POLICY, principal_for
from examples.devops.tools import register_clients
from reactifact import RuntimeResources
from reactifact.checkpoints import FileKVBackend
from reactifact.recipes import MemoryStore

ROOT = Path(__file__).resolve().parent


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
    return register_clients(resources)


__all__ = ["build_resources"]
