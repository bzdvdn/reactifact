"""incident_commander demo: artifact schemas."""

from __future__ import annotations

from pydantic import BaseModel


class IncidentReport(BaseModel):
    text: str = ""


class InvestigationTask(BaseModel):
    """Per-fork investigation trigger (`main.py` creates one on each branch
    right after `Context.branch()` — the branch's own artifacts are cloned,
    but *events* are not, so each fork needs a fresh one of these to react
    to, same idiom as `examples/forklab`'s `Strategy`)."""

    incident_id: str = ""
    target: str = ""  # "k8s" | "db"


class InvestigationComplete(BaseModel):
    """Created once, right after both forks are merged back — the explicit
    trigger for the decide-and-remediate phase (Commander) and the final
    root-cause synthesis, instead of relying on merge-emitted events for
    artifact types that may or may not have changed on this side.

    `text` is Commander's actual goal prompt (`ToolUseHITL._goal` reads
    `text` off whatever artifact triggered it, §_goal convention) — carries
    the original incident description forward since Commander no longer
    consumes `IncidentReport` directly."""

    incident_id: str = ""
    text: str = ""


class Evidence(BaseModel):
    """An investigation finding. Named `Evidence` on purpose (not a
    domain-specific name) — `reactifact.eval.core_metrics` matches artifact
    classes *by name*, so `Verify` scores this for free, no custom metrics."""

    query_id: str = ""
    text: str = ""
    score: float = 0.0


class RootCauseHypothesis(BaseModel):
    """A rolling synthesis over the Evidence gathered so far — bounded by
    `context_builder.TokenBudgetContextBuilder` as the investigation grows."""

    query_id: str = ""
    text: str = ""
    evidence_seen: int = 0


class Answer(BaseModel):
    """The commander's resolution. Named `Answer` for the same reason as
    `Evidence` — `Verify`'s `core_metrics` (`answer_present`,
    `provenance_grounded`, ...) match this class by name out of the box."""

    query_id: str = ""
    text: str = ""
