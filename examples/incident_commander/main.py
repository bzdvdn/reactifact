"""incident_commander — the full harness, composed: classified investigation
forks (branch & merge, §39-§40), a token-budgeted rolling synthesis, a
destructive-tool approval gate, and inline verification before the incident
is considered resolved.

Five pieces, one scenario:

- **Branch & merge** (`Context.branch()`/`merge()`, §39-§40) — an incident
  may touch several places (k8s, the database); `classify_targets`
  (`pipeline.py`) decides which are actually relevant — deterministic
  keyword overlap, not "always fork everything" — and each relevant place
  is investigated on its own fork, independently, then merged back into one
  timeline. Try `--text` with a k8s-only-worded incident to see the
  database fork not even created.
- **Sub-agent delegation** (`agent_tool.AgentAsTool`) — the database fork
  doesn't guess about the database itself: it asks a DBA specialist
  sub-agent (`ask_dba`).
- **Context Builder** (`context_builder.TokenBudgetContextBuilder`) — the
  root-cause synthesis only ever sees a token-bounded, most-recent slice of
  the (now merged) Evidence, transparently — no code in `produce.py` knows
  the builder exists.
- **Destructive-tool approval gate** (`tool_use.ToolUseHITL`) — once
  investigation is done and merged, the Commander's actual fix
  (`rollback_deploy`) pauses for a human's yes/no.
- **Inline verification** (`verify.Verify`) — before the incident is
  considered resolved, the final `Answer` is scored on `eval.py`'s
  `core_metrics` (with `provenance_grounded` required) — a resolution that
  isn't backed by evidence from *both* forks never quietly resolves the ticket.

The orchestration itself lives in `pipeline.py` (same split as
`examples/forklab`); this file only parses arguments and prints.

    uv run python -m examples.incident_commander.main
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from reactifact import PendingQuestion
from reactifact.providers import LLMProvider
from reactifact.verify import VerificationFailed, VerificationResult

from .models import Answer, Evidence, RootCauseHypothesis
from .pipeline import DEFAULT_INCIDENT, classify_targets, run


def build_llm() -> LLMProvider | None:
    """Explicit provider for this demo: OpenRouter (default) or a local
    OpenAI-compatible endpoint; `None` when no key is configured -> the
    scripted offline provider takes over (`fake_llm.py`)."""
    import os

    from reactifact.providers import openai_llm, openrouter_llm

    if os.getenv("OPENROUTER_API_KEY"):
        return openrouter_llm(max_tokens=2048)
    if os.getenv("OPENAI_BASE_URL"):
        return openai_llm(
            base_url=os.getenv("OPENAI_BASE_URL"),
            api_key=os.getenv("OPENAI_API_KEY"),
            model=os.getenv("OPENAI_MODEL"),
            max_tokens=2048,
        )
    return None


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m examples.incident_commander.main")
    parser.add_argument("--text", default=DEFAULT_INCIDENT)
    parser.add_argument("--context-max-tokens", type=int, default=50)
    args = parser.parse_args()

    ctx = asyncio.run(
        run(
            incident_text=args.text,
            llm=build_llm(),
            context_max_tokens=args.context_max_tokens,
        )
    )

    targets = classify_targets(args.text)
    print("incident_commander · classify → fork → investigate → merge → gate → verify")
    print(f"\n[incident] {args.text}")
    print(f"[classified targets] {sorted(targets)}\n")

    for q in ctx.list_artifacts(PendingQuestion):
        print(f"  [{q.data.kind} gate] {q.data.question!r} -> {q.data.resolution}")

    all_evidence = ctx.list_artifacts(Evidence)
    for e in all_evidence:
        print(f"  [evidence] {e.data.text}")
    for h in ctx.list_artifacts(RootCauseHypothesis):
        print(
            f"\n[hypothesis, context budget {args.context_max_tokens} tokens -> "
            f"{h.data.evidence_seen}/{len(all_evidence)} evidence items reached the synthesis] "
            f"{h.data.text}"
        )
    for a in ctx.list_artifacts(Answer):
        print(f"\n[answer] {a.data.text}")
    for v in ctx.list_artifacts(VerificationResult):
        status = "PASSED" if v.data.passed else "FAILED"
        print(
            f"[verification: {status}] overall={v.data.overall:.2f} "
            f"threshold={v.data.threshold:.2f} metrics={v.data.metrics}"
        )
    for f in ctx.list_artifacts(VerificationFailed):
        print(f"[verification failed marker] overall={f.data.overall:.2f}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
