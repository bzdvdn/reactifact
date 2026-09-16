# incident_commander — a full agent harness, composed

An SRE incident-commander demo that wires together every harness-level piece
built on top of `reactifact`'s core primitives, in one realistic scenario
instead of five separate toy demos:

- **Branch & merge** (`Context.branch()`/`merge()`, §39-§40) — an incident
  may touch several places (k8s, the database); deterministic keyword
  classification (`classify_targets`, `pipeline.py`) decides which are
  actually relevant, not "always fork everything" — each relevant place is
  investigated on its own fork, independently, then merged back into one
  timeline.
- **Sub-agent delegation** (`reactifact.agent_tool.AgentAsTool`) — the
  database fork doesn't guess about the database itself: it asks a DBA
  specialist sub-agent (`ask_dba`) for a second opinion. The sub-agent runs
  in its own isolated `Context`/`Runtime` and reports back as an ordinary
  tool result.
- **Context Builder** (`reactifact.context_builder.TokenBudgetContextBuilder`) —
  the root-cause synthesis (`SynthesizeRootCause`) only ever sees a
  token-bounded, most-recent slice of the (now merged) `Evidence`, and the
  produce itself has zero awareness of this — it just reads `inputs`.
- **Destructive-tool approval gate** (`reactifact.tool_use.ToolUseHITL`) —
  once investigation is done and merged, the Commander's actual fix
  (`rollback_deploy`) pauses for a human's yes/no instead of running the
  instant the model decides on it.
- **Inline verification** (`reactifact.verify.Verify`) — before the
  incident is considered resolved, the final `Answer` is scored on
  `eval.py`'s `core_metrics`, with `provenance_grounded` required — a
  resolution not backed by the evidence actually gathered never quietly
  closes the ticket.

```bash
uv run python -m examples.incident_commander.main
uv run python -m examples.incident_commander.main --context-max-tokens 30   # force heavier truncation
uv run python -m examples.incident_commander.main \
  --text "checkout-api pods stuck in CrashLoopBackOff after the latest deploy"  # k8s only — no db fork at all
```

Without an LLM key, a fixed script (`fake_llm.py`) drives both the
commander's and the DBA sub-agent's decisions — `ToolUseHITL`'s own decision
loop has no offline fallback (unlike a bare `structured_llm` call
elsewhere in this repo), so without a script an unconfigured run would just
answer "Could not reach a decision." on the first step. With a key
(`OPENROUTER_API_KEY`/`OPENAI_BASE_URL` in `.env`) both conversations go
through the real model instead.

## Structure

```
incident_commander/
├── models.py     # IncidentReport, InvestigationTask/Complete (fork triggers),
│                 #   Evidence, RootCauseHypothesis, Answer — Evidence/Answer
│                 #   are named that way on purpose: eval.py's core_metrics
│                 #   match artifact classes by name, so Verify scores them
│                 #   with zero custom metrics
├── tools.py      # check_pods/check_recent_deploys/check_db_locks (read-only),
│                 #   restart_pod/rollback_deploy (destructive=True)
├── fake_llm.py   # ScriptedLLM + the offline script for both conversations
├── agents.py     # build_commander() / build_ask_dba() / build_verifier() / dba_agent_factory()
├── produce.py    # K8sInvestigator, DBInvestigator (per-fork), SynthesizeRootCause,
│                 #   BuildAnswer + their thin container Agents
├── pipeline.py   # classify → fork → investigate → merge → decide/gate → verify
│                 #   (orchestration; same split as examples/forklab)
└── main.py       # CLI only — parses arguments, calls pipeline.run(), prints
```

## The flow

```
IncidentReport
      │
  classify_targets (deterministic keywords, §67 — never dead-ends)
      │
      ├─────────────┬─────────────┐         only the relevant fork(s)
      ▼             ▼                        are created at all
  fork "k8s"    fork "db"
  check_pods    ask_dba (AgentAsTool → DBA specialist sub-agent)
  check_recent_deploys
      │             │
      └──────┬──────┘
             ▼
        ctx.merge()                  (§40 — three-way, no silent choice;
             │                        skipped entirely if only one fork exists)
             │
     InvestigationComplete           (explicit wake-up: Context.merge()
             │                        doesn't emit events for merged-in
             ▼                        artifacts, unlike merge_from())
        Commander                    (decide + remediate)
             │
     rollback_deploy (destructive) ──▶ [approve gate] ──▶ yes/no
             │
          Answer ──supported_by──▶ Evidence (both forks)
             │
          Verify                    (provenance_grounded required)
```

## Reading the output

```
[approve gate] 'Approve running destructive tool ...' -> yes
[evidence] ...                                              <- one per finding, from either fork
[hypothesis, context budget 50 tokens -> 1/3 evidence items reached the synthesis] ...
[answer] ...
[verification: PASSED] overall=1.00 threshold=0.75 metrics={...}
```

The `1/3` in the hypothesis line is the context builder visibly truncating —
at least one piece of evidence always reaches the synthesis
(`TokenBudgetContextBuilder`'s guarantee), even at an absurdly tight budget
(try `--context-max-tokens 1`); raise `--context-max-tokens` to let more
through. The final `Answer` stays fully grounded regardless: it's built
from *all* matching `Evidence` directly (`BuildAnswer`, `produce.py`), not
from the (deliberately) bounded synthesis — the budget shapes what a
bystander agent free-associates over, not the provenance chain `Verify`
checks.

## Tests

`tests/test_incident_commander.py` runs the full scripted scenario and
asserts on all five pieces: both forks' tools actually ran and their
evidence survived the merge, the destructive tool only ran after an
approval, the synthesis saw a bounded slice of evidence, and the final
`Answer` passed verification fully grounded — plus `classify_targets`'s
own cases (k8s-only, db-only, both, and the "match neither → investigate
everywhere" fallback) and an end-to-end run confirming a k8s-only incident
never even creates the database fork.
