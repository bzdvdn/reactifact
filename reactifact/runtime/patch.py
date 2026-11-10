"""Patch validation and application for the runtime (effects → commit)."""

from __future__ import annotations

from ..agents import Agent
from ..artifacts import artifact_content_hash
from ..commit import Commit, Write
from ..context import Context
from ..guardrails import GuardrailViolation
from ..patches import Create, Delete, Link, Patch, Unlink, Update


class PatchApplicator:
    """Validate, authorize, guardrail and apply a patch to a Context."""

    def __init__(self, context: Context) -> None:
        self.context = context

    def validate_types(self, patch: Patch, agent: Agent) -> None:
        """Check that every Create matches the agent steps' `produces`."""
        if agent.steps is None:
            return
        allowed_types: set[type] = set()
        for step in agent.steps:
            if step.produces:
                allowed_types.update(step.produces)
            else:
                if step.artifact_type is not None:
                    allowed_types.add(step.artifact_type)
                allowed_types.update(step.also_creates)
        if not allowed_types:
            return
        for op in patch.operations:
            if isinstance(op, Create) and type(op.data) not in allowed_types:
                raise ValueError(
                    f"Agent '{agent.name}' created artifact of type "
                    f"{type(op.data).__name__}, which is not declared in "
                    f"produces: {[t.__name__ for t in allowed_types]}. Declare "
                    "it via the step's `produces=(...)` (or `also_creates`)."
                )

    def enforce_authorization(self, patch: Patch) -> None:
        """Gate each Create/Update/Delete against the principal's policy (§57)."""
        resources = self.context.resources
        if resources.authorizer is None:
            return
        for op in patch.operations:
            if isinstance(op, Create):
                resources.require_authorized("create", type(op.data).__name__)
            elif isinstance(op, Update):
                resources.require_authorized("update", type(op.new_data).__name__)
            elif isinstance(op, Delete):
                resources.require_authorized(
                    "delete", getattr(op, "data_type", "") or "artifact"
                )

    def apply_guardrails(self, patch: Patch) -> Patch:
        """Check/rewrite Create/Update; block the turn on a violation (§57)."""
        policy = self.context.resources.guardrails
        if policy is None:
            return patch
        metrics = self.context.resources.metrics
        for op in patch.operations:
            if isinstance(op, Create):
                data = op.data
            elif isinstance(op, Update):
                data = op.new_data
            else:
                continue
            decision = policy.evaluate(data, self.context)
            if decision.action == "allow":
                continue
            resource = type(data).__name__
            metrics.increment(
                "reactifact_guardrail_triggered_total",
                guardrail=decision.guardrail or "guardrail",
                action=decision.action,
                type=resource,
            )
            if decision.action == "redact" and decision.data is not None:
                if isinstance(op, Create):
                    op.data = decision.data
                else:
                    op.new_data = decision.data
                continue
            if decision.action in ("redact", "flag"):
                continue
            raise GuardrailViolation(
                decision.guardrail or "guardrail", decision.reason, resource
            )
        return patch

    def apply(self, patch: Patch, commit: Commit) -> list[Write]:
        """Apply a patch's operations to the Context and return its writes."""
        writes: list[Write] = []
        for op in patch.operations:
            if isinstance(op, Create):
                if op.id is not None and self.context.get(op.id) is not None:
                    upserted = self.context.update(op.id, op.data)
                    assert upserted is not None
                    op.artifact_id = op.id
                    writes.append(
                        Write(
                            op.id,
                            upserted.version,
                            "update",
                            artifact_content_hash(op.data),
                        )
                    )
                else:
                    created = self.context.create(op.data, id=op.id)
                    op.artifact_id = created.id
                    writes.append(
                        Write(
                            op.artifact_id,
                            created.version,
                            "create",
                            artifact_content_hash(op.data),
                        )
                    )
            elif isinstance(op, Update):
                updated = self.context.update(op.artifact_id, op.new_data)
                if updated is not None:
                    writes.append(
                        Write(
                            op.artifact_id,
                            updated.version,
                            "update",
                            artifact_content_hash(op.new_data),
                        )
                    )
            elif isinstance(op, Delete):
                removed = self.context.get(op.artifact_id)
                version = removed.version if removed is not None else 0
                self.context.delete(op.artifact_id)
                writes.append(Write(op.artifact_id, version, "delete"))
            elif isinstance(op, Link):
                self.context.link(op.artifact_id, op.relation, op.target_id)
            elif isinstance(op, Unlink):
                self.context.unlink(op.artifact_id, op.relation, op.target_id)
            else:
                raise ValueError(f"Unknown operation: {op}")
        return writes


__all__ = ["PatchApplicator"]
