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

import hashlib
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable

from .errors import SourceError
from .models import DocumentMeta, RawDocument


@runtime_checkable
class Connector(Protocol):
    """Produces raw documents for a pipeline run."""

    source_id: str

    async def fetch(self) -> list[RawDocument]:
        """Fetch every document this connector currently exposes."""
        ...


@runtime_checkable
class ListingConnector(Protocol):
    """A connector that can cheaply list fingerprints before fetching bodies."""

    source_id: str

    async def list_documents(self) -> list[DocumentMeta]:
        """Return one meta entry per document, with a listing fingerprint."""
        ...

    async def fetch(self, *, uris: set[str] | None = None) -> list[RawDocument]:
        """Fetch only the given `uris`, or all documents when `uris` is None."""
        ...


class FilesystemConnector:
    """Ingest text files under a directory, with cheap incremental listing.

    The common built-in connector: no dependencies, deterministic URIs and
    content fingerprints, and a fail-fast missing-root check so a typo can
    never reconcile an entire corpus as deleted.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        source_id: str = "filesystem",
        extensions: Sequence[str] = (".md", ".txt"),
        include_hidden: bool = False,
        content_type: str = "text/markdown",
    ) -> None:
        self.root = Path(root)
        self.source_id = source_id
        self.extensions = tuple(ext.lower() for ext in extensions)
        self.include_hidden = include_hidden
        self.content_type = content_type

    def _paths(self) -> list[Path]:
        if not self.root.exists():
            raise SourceError(f"filesystem root does not exist: {self.root}")
        if not self.root.is_dir():
            raise SourceError(f"filesystem root is not a directory: {self.root}")
        paths: list[Path] = []
        for path in sorted(self.root.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix.lower() not in self.extensions:
                continue
            relative = path.relative_to(self.root)
            if not self.include_hidden and any(
                part.startswith(".") for part in relative.parts
            ):
                continue
            paths.append(path)
        return paths

    def _relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    async def list_documents(self) -> list[DocumentMeta]:
        documents: list[DocumentMeta] = []
        for path in self._paths():
            uri = self._relative(path)
            documents.append(
                DocumentMeta(
                    uri=uri,
                    step_id=uri,
                    fingerprint=hashlib.sha256(path.read_bytes()).hexdigest(),
                    content_type=self.content_type,
                )
            )
        return documents

    async def fetch(self, *, uris: set[str] | None = None) -> list[RawDocument]:
        documents: list[RawDocument] = []
        for path in self._paths():
            uri = self._relative(path)
            if uris is not None and uri not in uris:
                continue
            documents.append(
                RawDocument.from_text(
                    step_id=uri,
                    uri=uri,
                    text=path.read_text(encoding="utf-8", errors="replace"),
                    title=path.stem,
                    metadata={"sourceUrl": str(path), "title": path.stem},
                    content_type=self.content_type,
                )
            )
        return documents


def has_listing(connector: object) -> bool:
    """Whether `connector` implements the cheap-listing capability."""
    return isinstance(connector, ListingConnector)


__all__ = [
    "Connector",
    "FilesystemConnector",
    "ListingConnector",
    "has_listing",
]
