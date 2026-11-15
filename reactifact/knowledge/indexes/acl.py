"""ACL visibility for Python-side indexes (memory/sqlite)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence


def acl_visible(
    metadata: Mapping[str, object], principals: Sequence[str] | None
) -> bool:
    """Whether a point is visible to `principals`.

    `principals=None` means no identity was supplied: no filtering happens.
    A point with no `acl` metadata is public.
    """
    if principals is None:
        return True
    acl = metadata.get("acl")
    if not isinstance(acl, (list, tuple)) or not acl:
        return True
    return bool({str(p) for p in acl} & {str(p) for p in principals})


__all__ = ["acl_visible"]
