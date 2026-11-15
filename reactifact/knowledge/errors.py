"""Knowledge-layer exceptions."""

from __future__ import annotations


class KnowledgeError(Exception):
    """Base class for knowledge pipeline failures."""


class SourceError(KnowledgeError):
    """Raised when a connector fails to list or fetch documents.

    Connectors should wrap provider errors in this type: the pipeline treats
    it as a hard failure *before* reconciliation, so a transient outage never
    makes the corpus look deleted.
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class PipelineError(KnowledgeError):
    """Raised when a stage fails during a pipeline run, with stage context."""


class ConfigError(KnowledgeError):
    """Raised for an invalid pipeline configuration."""


__all__ = ["ConfigError", "KnowledgeError", "PipelineError", "SourceError"]
