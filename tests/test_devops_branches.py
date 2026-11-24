"""The devops branch/merge prototype: same work, two orchestration shapes."""

from __future__ import annotations

from examples.devops.branches import (
    clean_scenario,
    conflict_scenario,
    reactive_scenario,
)
from examples.devops.models import AnsibleReport, ChatReply, GitlabReport, K8sReport
from reactifact.tracing import Tracer


def test_reactive_fan_in_composes_all_reports():
    context = reactive_scenario(Tracer())
    reply = context.latest(ChatReply)
    assert reply is not None
    assert "k8s:" in reply.data.text
    assert "gitlab:" in reply.data.text
    assert "ansible:" in reply.data.text
    assert {type(a.data).__name__ for a in context.list_artifacts()} >= {
        K8sReport.__name__,
        GitlabReport.__name__,
        AnsibleReport.__name__,
    }


def test_clean_branch_merge_composes_and_links_provenance():
    context, _ = clean_scenario(Tracer())
    reply = context.latest(ChatReply)
    assert reply is not None
    linked = {
        type(artifact.data).__name__
        for artifact in context.related(reply.id, "supported_by")
    }
    assert linked == {"K8sReport", "GitlabReport", "AnsibleReport"}


def test_conflict_is_resolved_by_an_explicit_policy():
    context = conflict_scenario(Tracer())
    note = context.latest(
        __import__("examples.devops.branches", fromlist=["Note"]).Note
    )
    assert note is not None
    assert note.data.text == "left edit + right edit"
