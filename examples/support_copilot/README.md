# support_copilot — answer from the docs, or escalate, never hallucinate

A support agent over a help corpus with exactly two honest outcomes:

- **the docs cover it** → a grounded reply made of the matched document's own
  text, linked `supported_by` that document (citations come from provenance, not
  from a prompt);
- **nothing matches** → the runtime **escalates to a human** with
  `effects.ask(...)` (a `PendingQuestion`); when the human answers, that answer
  becomes the reply. The agent never invents an answer to a question it can't
  ground.

No model is required — the pipeline is retrieval + a deterministic reply. With a
provider configured the same structure could paraphrase, but the citation and
the escalation gate are structural, not prompt-dependent.

## Run

```bash
.venv/bin/python -m examples.support_copilot.main
```

```
question: how do refunds work?
reply: Refunds are issued within 14 days of purchase. …
citations: ['faq.md']  escalated=False

>>> a question the docs do NOT cover — the agent escalates:
pending question: 'No help article matched this question. How should I answer?' (kind=escalate)
no reply was invented — a human answer is required.

>>> a human answers the escalation, and the reply is finalized:
reply: We don't accept bitcoin yet.  escalated=True
```

## Why this matters

The failure mode support bots are notorious for is answering anyway — inventing
a refund window or a policy that doesn't exist. Here "I don't know" is a
first-class state (`PendingQuestion`), not a fallback string: the runtime pauses
on it, a human resolves it, and the resolved answer is itself an artifact with
provenance. The same `kind="escalate"` vocabulary works for approvals and
clarifications elsewhere.

## Structure

```
support_copilot/
├── data/faq.md      # the help corpus
├── models.py        # Question, Doc, SearchDone, Reply
├── produce.py       # search → resolve → answer, plus escalate / finalize
├── agents.py        # AGENTS list
└── main.py          # grounded run, uncovered run (escalate), human answer
```

## See also

- `examples/knowledge` — the same retrieval→evidence→answer shape with
  verification and claims.
- `examples/devops` — HITL chat over tools, the other side of `PendingQuestion`.
- `docs/en/patterns.md` — the HITL pattern ("activate stage → immediately ask").
