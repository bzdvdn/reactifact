# repo_agent — a coding agent behind a destructive-action approval gate

An LLM + tools agent over a tiny repository. Safe tools (`read_file`,
`run_tests`) run immediately; `git_commit` is declared `@tool(destructive=True)`,
so the model may *decide* to call it but the runtime turns that decision into a
`PendingQuestion(kind="approve")` and only executes the commit once a human
approves (§60). A denied call never runs.

Offline: a scripted provider stands in for the model, and the tools are local
(one reads from `sample_repo/`, one returns a canned test summary, the commit is
recorded in memory) — no network, no real git.

## Run

```bash
.venv/bin/python -m examples.repo_agent.main
```

```
>>> safe tool: the agent runs the tests with no approval needed
report: Tests pass (5).  commits=[]

>>> destructive tool: the commit is gated behind approval
pending approval: 'Approve running destructive tool 'git_commit' with args …'
commits so far: []  (nothing ran yet)

after approval — commits: ['fix add()']
[confirmed] the destructive action ran only after a human said yes.
```

## Why this matters

An autonomous coding agent that can commit, delete, or deploy is one prompt
injection away from damage. Making "destructive" a property of the `Tool` — not
of the prompt, not of a review of the model's output — means the gate is
structural: the runtime will not execute a destructive tool without an explicit
human `resume`, regardless of what the model decides. The same agent, with no
destructive tools, runs unattended.

## Structure

```
repo_agent/
├── sample_repo/calculator.py   # the toy repository
└── main.py                     # tools, RepoAgent, and the two scenarios
```

## See also

- `examples/devops` — the fuller HITL tool agent (multiple specialists, a router,
  a trace dashboard).
- `reactifact.tool_use.ToolUseHITL` — the approval loop, including `max_approvals`.
- `docs/en/patterns.md` — "Tool agents: LLM + tools (blocking or HITL)".
