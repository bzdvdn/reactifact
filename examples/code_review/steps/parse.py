"""Parse a unified diff into one `ChangedFile` per path (deterministic)."""

from __future__ import annotations

import re
from collections.abc import Iterator

from reactifact import Artifact, Effects, Step

from ..models import ChangedFile, ReviewRequest

_GIT_HEADER = re.compile(r"^diff --git a/(.+) b/(.+)$")
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def split_diff(diff: str) -> list[tuple[str, str]]:
    """Split a unified diff into `(path, patch)` blocks.

    A plain diff without `diff --git` headers still becomes one block under
    `(input)`, so the pipeline always has something to review.
    """
    blocks: list[tuple[str, str]] = []
    path: str | None = None
    lines: list[str] = []

    def flush() -> None:
        if path is not None and lines:
            blocks.append((path, "\n".join(lines)))

    for line in diff.splitlines():
        match = _GIT_HEADER.match(line)
        if match is not None:
            flush()
            path = match.group(2).strip()
            lines = [line]
            continue
        if path is None and line.startswith("+++ "):
            candidate = line[4:].strip()
            path = candidate[2:] if candidate.startswith("b/") else candidate
            lines = [line]
            continue
        if path is not None:
            lines.append(line)
    flush()
    if not blocks:
        blocks.append(("(input)", diff))
    return blocks


def added_lines(patch: str) -> Iterator[tuple[int, str]]:
    """Yield `(new_file_line, text)` for added lines, using hunk headers."""
    new_line = 0
    for line in patch.splitlines():
        hunk = _HUNK.match(line)
        if hunk is not None:
            new_line = int(hunk.group(1))
            continue
        if line.startswith(("+++", "---", "diff --git")):
            continue
        if line.startswith("+"):
            yield new_line, line[1:]
            new_line += 1
        elif line.startswith(" "):
            new_line += 1


class ParseDiffStep(Step):
    consumes = (ReviewRequest,)
    produces = (ChangedFile,)

    async def run(self, request: Artifact[ReviewRequest], effects: Effects) -> None:
        for index, (path, patch) in enumerate(split_diff(request.data.diff)):
            effects.create(
                ChangedFile(request_id=request.id, path=path, patch=patch),
                id=f"file:{request.id}:{index}",
            )
        return None


__all__ = ["ParseDiffStep", "added_lines", "split_diff"]
