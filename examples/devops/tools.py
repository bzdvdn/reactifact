"""Fake devops demo tools.

They don't really call anything — they return plausible «simulated» answers
to show how the framework drives LLM + tools without external systems.

Mandatory parameters (namespace / project / role) force the LLM agent
to ask the user for missing context (HITL, type:"ask").
"""

from reactifact import tool

#: Tool call counter — for tests and observing the framework.
CALLS: dict[str, int] = {}


@tool
async def kubectl_get(resource: str, namespace: str) -> str:
    """Get the state of a k8s resource (pods, deployments, logs) in a namespace."""
    CALLS["kubectl_get"] = CALLS.get("kubectl_get", 0) + 1
    target = resource.lower()
    if "log" in target or "describe" in target or "worker-2f1" in target:
        return (
            f"namespace={namespace} pod=worker-2f1\n"
            "Last State: Terminated (OOMKilled)\n"
            "Reason: Back-off restarting failed container\n"
            "Events: Warning BackOff  kubelet  Back-off restarting failed container"
        )
    if "pod" in target:
        return (
            f"namespace={namespace}\n"
            "NAME       READY  STATUS            RESTARTS\n"
            "api-7c9d   1/1    Running           0\n"
            "worker-2f1 0/1    CrashLoopBackOff  3"
        )
    return f"{resource} ({namespace}): ready, 3 replicas, stable"


@tool
async def gitlab_search(query: str, project: str) -> str:
    """Find MRs and commits by text in a GitLab repository."""
    CALLS["gitlab_search"] = CALLS.get("gitlab_search", 0) + 1
    return (
        f"{project}: MR !1842 'fix payments timeout' — merged\n"
        f"{project}: MR !1901 'add rate-limit to api' — open"
    )


@tool
async def gitlab_pipeline(project: str) -> str:
    """Status of the latest CI pipeline of a GitLab repository."""
    CALLS["gitlab_pipeline"] = CALLS.get("gitlab_pipeline", 0) + 1
    return f"{project}: pipeline #4821 — failed on stage 'deploy' (job 'kubectl-apply')"


@tool
async def ansible_run(playbook: str, hosts: str, role: str) -> str:
    """Simulate running ansible-playbook (read-only dry-run) for a role."""
    CALLS["ansible_run"] = CALLS.get("ansible_run", 0) + 1
    return (
        f"PLAY RECAP — playbook {playbook}, role {role}, hosts: {hosts}\n"
        "ok=7  changed=1  failed=1  unreachable=0"
    )
