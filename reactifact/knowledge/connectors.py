"""Connector contracts — the code-first replacement for alvis source configs.

A connector is an ordinary object; there are no type strings, factories or
plugin entry points to register. The pipeline needs two shapes:

- `Connector` — fetch everything on every run.
- `ListingConnector` — additionally fingerprint documents from listing data
  so incremental runs skip downloading unchanged bodies.

Both are protocols, so a project's existing provider wrappers only need a
public `source_id` attribute and the matching `fetch` signature.
"""

from __future__ import annotations

from typing import Protocol

from .models import DocumentMeta, RawDocument


class Connector(Protocol):
    """Produces raw documents for a pipeline run."""

    source_id: str

    async def fetch(self) -> list[RawDocument]:
        """Fetch every document this connector currently exposes."""
        ...


class ListingConnector(Protocol):
    """A connector that can cheaply list fingerprints before fetching bodies."""

    source_id: str

    async def list_documents(self) -> list[DocumentMeta]:
        """Return one meta entry per document, with a listing fingerprint."""
        ...

    async def fetch(self, *, uris: set[str] | None = None) -> list[RawDocument]:
        """Fetch only the given `uris`, or all documents when `uris` is None."""
        ...


def has_listing(connector: object) -> bool:
    """Whether `connector` implements the cheap-listing capability."""
    return callable(getattr(connector, "list_documents", None))


__all__ = ["Connector", "ListingConnector", "has_listing"]
