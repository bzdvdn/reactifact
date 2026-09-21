# fintech_audit — an auditable, reproducible finance answer

A finance question — *"what is the Q2 cloud spend variance, and does policy
require approval?"* — answered from a transactions CSV, a budget CSV and a
policy document. Two things make it a **fintech** demo rather than just a RAG
one:

1. **The number is computed, not generated.** `Spend` and `Variance` are plain
   Python arithmetic over a materialized `Table` (§67) — the model is never the
   source of truth. The answer *states* the figures; it does not invent them.
2. **The answer is auditable and reproducible.** Every derived artifact links
   to what it came from (`calculated_from`, `materialized_from`,
   `supported_by`), so `reactifact.audit` renders a report with a content hash
   per artifact and a `context_sha256` for the whole run. Running the same
   pipeline twice hashes identically — and the hash is what
   `reactifact replay … --verify <hash>` checks against a saved session.

No API key needed: nothing here calls a model.

![fintech_audit demo: the variance and its audit report, then the reproducibility check — a second run prints the same context hash.](../../docs/img/fintech-audit-demo.gif)

## Run

```bash
.venv/bin/python -m examples.fintech_audit.main
```

```
computed figures (plain Python — not the model):
  cloud spend: $45,000  by month: {'2026-04': 12000.0, '2026-05': 15000.0, '2026-06': 18000.0}
  variance vs budget: +12.5% (threshold 10%, within policy: False)

answer: Q2 cloud spend was $45,000 against a $40,000 budget (+12.5%) — exceeds
the 10% policy threshold. CFO approval is required before the quarter is closed.
citations: ['budget.csv', 'transactions.csv', 'policy.md']

# Audit report
- context version: 7
- context sha256: `f38c6a42…`
...
[confirmed] the second run hashes identically — the answer is reproducible.
```

`python -m examples.fintech_audit.main` exits non-zero if the two runs ever
diverge, so it doubles as a smoke test of determinism.

## Verify a saved run

```bash
reactifact replay sessions.sqlite3 --session <id> --verify <context_sha256>
```

`--hash` alone prints the digest; `--verify` exits non-zero on a mismatch. The
digest is the one `reactifact.audit.context_hash` produces (and the example
prints it), so a reviewer can compare the state behind an answer against a
recorded fingerprint without re-reading the code.

## What it demonstrates

- **Deterministic figures** — `compute_spend`/`compute_variance` are pure
  consumes→produce arithmetic; `Artifact.version` proves only the real
  dependents recompute (see `examples/ledger` for that story in isolation).
- **Provenance as structure** — `reactifact.audit.build_report` walks the
  relation graph to every contributing artifact with its hash, author and
  version, and lists the source locators the answer rests on.
- **Reproducibility** — `context_hash` excludes timestamps and depends only on
  ids + content + relations, so stable-id runs are comparable run to run.

## Structure

```
fintech_audit/
├── data/             # transactions.csv, budget.csv, policy.md
├── models.py         # Question, Table, Policy, Spend, Variance, AuditAnswer
├── produce.py        # search → materialize → compute_spend → compute_variance → answer
├── agents.py         # AGENTS list
└── main.py           # run, audit report, reproducibility check
```

## See also

- `reactifact/audit.py` — `build_report`, `context_hash`, `report_to_markdown`.
- `examples/ledger` — the dependency-precision argument, with `Artifact.version`.
- `docs/en/observability.md` — trace redaction (`RuntimeResources(redactor=…)`),
  so a demo like this can be shipped to a sink without the raw amounts/PII.
