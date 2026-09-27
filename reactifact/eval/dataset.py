"""Datasets — curated examples to evaluate a pipeline against (§56).

A `Dataset` is a list of `Example`s: an `inputs` mapping fed to the target, an
optional `reference_outputs` mapping used only by evaluators (never passed to
the target), and free-form `metadata`. This mirrors the offline-evaluation
model (dataset of input/reference pairs, run a target over each, score the
outputs) without any hosted store: a dataset is a plain, hashable value you can
keep in the repo, version by content, and load from JSON/JSONL.

    ds = Dataset.from_file("evals/qa.json")
    ds.version              # content hash — pin a run to a dataset revision
    for example in ds:
        example.inputs, example.reference_outputs
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _fingerprint(payload: Any) -> str:
    """A stable short hash of any JSON-ish payload (sorted keys)."""
    blob = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


@dataclass
class Example:
    """One evaluation example: inputs + optional reference outputs + metadata.

    `id` is derived from the content (inputs + reference outputs) when not
    given, so two structurally identical examples collapse to the same id and a
    dataset's `version` changes only when its content does.
    """

    inputs: Mapping[str, Any]
    reference_outputs: Mapping[str, Any] | None = None
    metadata: Mapping[str, Any] | None = None
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = "ex-" + _fingerprint(
                {"inputs": self.inputs, "reference_outputs": self.reference_outputs}
            )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "inputs": dict(self.inputs)}
        if self.reference_outputs is not None:
            out["reference_outputs"] = dict(self.reference_outputs)
        if self.metadata is not None:
            out["metadata"] = dict(self.metadata)
        return out

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> Example:
        """Builds an `Example` from a mapping (e.g. one JSON row).

        Accepts LangSmith-style keys (`inputs`, `outputs`, `reference_outputs`,
        `metadata`) as well as a bare input mapping — a row without any of those
        keys is treated as the `inputs` itself, so `{"question": "…"}` works.
        """
        known = {"id", "inputs", "outputs", "reference_outputs", "metadata"}
        if not (known & set(row)):
            return cls(inputs=dict(row))
        reference = row.get("reference_outputs", row.get("outputs"))
        return cls(
            id=str(row.get("id", "")),
            inputs=dict(row.get("inputs", {})),
            reference_outputs=dict(reference) if reference is not None else None,
            metadata=dict(row["metadata"]) if row.get("metadata") else None,
        )


@dataclass
class Dataset:
    """An ordered, versioned collection of `Example`s."""

    examples: list[Example] = field(default_factory=list)
    name: str = ""

    def __post_init__(self) -> None:
        self.examples = list(self.examples)

    def __len__(self) -> int:
        return len(self.examples)

    def __iter__(self) -> Iterator[Example]:
        return iter(self.examples)

    def __getitem__(self, key: int | str) -> Example:
        if isinstance(key, int):
            return self.examples[key]
        for example in self.examples:
            if example.id == key:
                return example
        raise KeyError(key)

    @property
    def version(self) -> str:
        """Content hash of every example — pin a run to a dataset revision."""
        return _fingerprint([e.to_dict() for e in self.examples])

    @classmethod
    def from_list(
        cls, rows: Iterable[Example | Mapping[str, Any]], *, name: str = ""
    ) -> Dataset:
        examples = [
            row if isinstance(row, Example) else Example.from_dict(row) for row in rows
        ]
        return cls(examples=examples, name=name)

    @classmethod
    def from_file(cls, path: str | Path) -> Dataset:
        """Loads `.json` (`{"examples": [...]}` or a bare list) or `.jsonl`."""
        file = Path(path)
        text = file.read_text(encoding="utf-8")
        if file.suffix in (".jsonl", ".ndjson"):
            rows = [json.loads(line) for line in text.splitlines() if line.strip()]
            return cls.from_list(rows, name=file.stem)
        data = json.loads(text)
        if isinstance(data, Mapping):
            rows = data.get("examples", [])
            return cls.from_list(rows, name=str(data.get("name", file.stem)))
        return cls.from_list(data, name=file.stem)

    def to_list(self) -> list[dict[str, Any]]:
        return [e.to_dict() for e in self.examples]

    def to_json(self, path: str | Path | None = None) -> str:
        """Serializes the dataset; writes to `path` too when given."""
        payload = {"name": self.name, "examples": self.to_list()}
        text = json.dumps(payload, indent=2, ensure_ascii=False)
        if path is not None:
            Path(path).write_text(text + "\n", encoding="utf-8")
        return text


__all__ = ["Dataset", "Example"]
