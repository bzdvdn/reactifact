"""Fake devops demo tools — typed results and injected clients.

They don't really call anything: `K8sClient`/`GitLabClient`/`AnsibleClient`
return plausible «simulated» answers, so the demo shows the framework driving
LLM + tools without external systems.

The clients are registered on `RuntimeResources` (`register_clients`), and each
`@tool` injects the one it needs. That keeps the tool signature about the
*operation* (`resource`, `namespace`) while the collaborator stays a normal
application resource — easy to replace with a real client, and easy to fake in
tests. Tools return pydantic models (`ToolOutput.data`), not formatted strings;
`retry=`/`timeout=` are declared where a real API would need them.

Mandatory parameters (namespace / project / role) still force the LLM agent to
ask the user for missing context (HITL, type:"ask").
"""

from __future__ import annotations

from reactifact import RetryPolicy, RuntimeResources, tool

from .models import (
    K8sSnapshot,
    MergeRequest,
    MergeRequests,
    PipelineState,
    PlayRecap,
    PodState,
)

#: Tool call counter — for tests and observing the framework.
CALLS: dict[str, int] = {}

#: A simulated real-world API call would be retried once, with a small backoff.
API_RETRY = RetryPolicy(max_attempts=2, backoff=0.1)


class K8sClient:
    """A fake Kubernetes API client (the real one would use `kubectl`/`kubernetes`)."""

    async def get(self, resource: str, namespace: str) -> K8sSnapshot:
        CALLS["kubectl_get"] = CALLS.get("kubectl_get", 0) + 1
        target = resource.lower()
        if "log" in target or "describe" in target or "worker-2f1" in target:
            pod = PodState(
                name="worker-2f1",
                namespace=namespace,
                status="CrashLoopBackOff",
                restarts=3,
                last_state="Terminated (OOMKilled)",
                reason="Back-off restarting failed container",
                events=[
                    "Warning BackOff: Back-off restarting failed container",
                    "Normal Pulled: Container image already present",
                ],
            )
            return K8sSnapshot(
                namespace=namespace,
                resource=resource,
                summary="worker-2f1 keeps restarting after OOMKilled",
                pods=[pod],
            )
        if "pod" in target:
            return K8sSnapshot(
                namespace=namespace,
                resource=resource,
                summary="2 pods: 1 Running, 1 CrashLoopBackOff",
                pods=[
                    PodState(
                        name="api-7c9d",
                        namespace=namespace,
                        status="Running",
                    ),
                    PodState(
                        name="worker-2f1",
                        namespace=namespace,
                        status="CrashLoopBackOff",
                        restarts=3,
                    ),
                ],
            )
        return K8sSnapshot(
            namespace=namespace,
            resource=resource,
            summary=f"{resource} is ready: 3 replicas, stable",
        )


class GitLabClient:
    """A fake GitLab API client."""

    async def search(self, query: str, project: str) -> MergeRequests:
        CALLS["gitlab_search"] = CALLS.get("gitlab_search", 0) + 1
        return MergeRequests(
            project=project,
            query=query,
            items=[
                MergeRequest(iid="!1842", title="fix payments timeout", state="merged"),
                MergeRequest(iid="!1901", title="add rate-limit to api", state="open"),
            ],
        )

    async def pipeline(self, project: str) -> PipelineState:
        CALLS["gitlab_pipeline"] = CALLS.get("gitlab_pipeline", 0) + 1
        return PipelineState(
            project=project,
            pipeline_id="#4821",
            status="failed",
            stage="deploy",
            job="kubectl-apply",
        )


class AnsibleClient:
    """A fake Ansible runner (read-only dry-run)."""

    async def dry_run(self, playbook: str, hosts: str, role: str) -> PlayRecap:
        CALLS["ansible_run"] = CALLS.get("ansible_run", 0) + 1
        return PlayRecap(
            playbook=playbook,
            role=role,
            hosts=hosts,
            ok=7,
            changed=1,
            failed=1,
        )


def register_clients(resources: RuntimeResources) -> RuntimeResources:
    """Registers the demo clients; the app swaps them for real ones here."""
    resources.register(K8sClient, K8sClient())
    resources.register(GitLabClient, GitLabClient())
    resources.register(AnsibleClient, AnsibleClient())
    return resources


@tool(retry=API_RETRY, timeout=10)
async def kubectl_get(resource: str, namespace: str, k8s: K8sClient) -> K8sSnapshot:
    """Get the state of a k8s resource (pods, deployments, logs) in a namespace."""
    return await k8s.get(resource, namespace)


@tool(retry=API_RETRY, timeout=10)
async def gitlab_search(
    query: str, project: str, gitlab: GitLabClient
) -> MergeRequests:
    """Find MRs and commits by text in a GitLab repository."""
    return await gitlab.search(query, project)


@tool(retry=API_RETRY, timeout=10)
async def gitlab_pipeline(project: str, gitlab: GitLabClient) -> PipelineState:
    """Status of the latest CI pipeline of a GitLab repository."""
    return await gitlab.pipeline(project)


@tool(retry=API_RETRY, timeout=10)
async def ansible_run(
    playbook: str, hosts: str, role: str, ansible: AnsibleClient
) -> PlayRecap:
    """Simulate running ansible-playbook (read-only dry-run) for a role."""
    return await ansible.dry_run(playbook, hosts, role)


__all__ = [
    "API_RETRY",
    "AnsibleClient",
    "CALLS",
    "GitLabClient",
    "K8sClient",
    "ansible_run",
    "gitlab_pipeline",
    "gitlab_search",
    "kubectl_get",
    "register_clients",
]
