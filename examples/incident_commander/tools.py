"""incident_commander demo: fake tools — simulated output, no real systems.

Diagnostic tools are read-only; `restart_pod`/`rollback_deploy` are
`destructive=True` — routed through `ToolUseHITL`'s approval gate
(`tool_use.py`) instead of running immediately.
"""

from __future__ import annotations

from typing import Any

from reactifact import tool
from reactifact.tools import FunctionTool

#: Calls recorded per tool name, for tests/inspection — same convention as
#: `examples/devops/tools.py`.
CALLS: dict[str, list[dict[str, Any]]] = {}


@tool
async def check_pods(namespace: str) -> str:
    """Check the status of pods in a k8s namespace."""
    CALLS.setdefault("check_pods", []).append({"namespace": namespace})
    return f"namespace={namespace}: checkout-7f9c9 0/1 CrashLoopBackOff (14 restarts in 10m)"


@tool
async def check_recent_deploys() -> str:
    """List recent deployments across services."""
    CALLS.setdefault("check_recent_deploys", []).append({})
    return "checkout-api deploy #42 (image checkout:1.9.3) rolled out 6 minutes ago"


@tool
async def check_db_locks() -> str:
    """Check the database for lock contention or slow queries (DBA tool)."""
    CALLS.setdefault("check_db_locks", []).append({})
    return "no long-running locks; connection pool at 40% utilization; queries nominal"


async def _restart_pod(name: str) -> str:
    """Restart a k8s pod."""
    CALLS.setdefault("restart_pod", []).append({"name": name})
    return f"pod {name} restarted"


async def _rollback_deploy(deploy_id: str) -> str:
    """Roll back a deployment to its previous version."""
    CALLS.setdefault("rollback_deploy", []).append({"deploy_id": deploy_id})
    return f"deploy {deploy_id} rolled back to the previous image"


# Built via the `FunctionTool` constructor directly, not `@tool(destructive=True)`:
# `tool()`'s decorator-factory form types as `-> Any` (it has to, to support both
# `@tool` and `@tool(...)`), which strict mypy flags as an untyped decorator —
# `examples/` is checked with `strict = true` (`pyproject.toml`), unlike `tests/`.
restart_pod = FunctionTool(_restart_pod, name="restart_pod", destructive=True)
rollback_deploy = FunctionTool(
    _rollback_deploy, name="rollback_deploy", destructive=True
)
