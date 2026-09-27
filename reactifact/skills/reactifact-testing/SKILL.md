---
name: reactifact-testing
description: Test reactifact agent pipelines deterministically with ScenarioLab, provenance assertions, golden snapshots, fault injection, record/replay and the pytest plugin. Use whenever the user wants to test, assert on, debug, or stabilise a reactifact run — checking which artifacts were produced, which tools ran, the agent path, LLM usage, or errors — or when a reactifact test is flaky near an LLM or a tool.
---

# Testing reactifact pipelines

reactifact tests assert on the **artifact graph a run produced**, not on a
returned value. `reactifact.testing.ScenarioLab` seeds artifacts, runs the
agents to a fixpoint, and hands back a `ScenarioResult` with chainable
assertion groups. Because the runtime is deterministic and the provider is
injectable, the whole pipeline runs with no network.

## A scenario in one call

```python
from pydantic import BaseModel
from reactifact import Consume, Produce, create_agent
from reactifact.testing import ScenarioLab


class Question(BaseModel):
    text: str


class Answer(BaseModel):
    text: str


class Answerer(Produce[Answer]):
    artifact_type = Answer

    async def produce(self, call):
        call.effects.create(Answer(text=call.trigger.data.text.upper()))


agent = create_agent("answerer", consumes=[Consume(Question)], produces=[Answerer()])

result = ScenarioLab([agent]).run_sync(Question(text="hi"))

result.artifacts(Answer).exists()          # an Answer was produced (returns its data)
result.path.contains("answerer")           # the agent ran
result.path.exact_sequence("answerer")     # ... exactly once, in this order
result.tools.never_called("search")        # no tool was invoked
assert result.errors.count() == 0           # no agent raised
```

Use `run(...)`/`turn(...)` (async) in async tests, `run_sync(...)`/
`turn_sync(...)` in plain pytest tests.

## Assertion groups on `ScenarioResult`

| Group | Answers |
| --- | --- |
| `result.artifacts(T)` | `.exists()`, `.count(n)`, `.matches(regex)`, `.equals(**fields)`, `.linked(rel, T)` |
| `result.path` | agent path — `.contains`, `.sequence`, `.exact_sequence`, `.times` |
| `result.relations` | provenance edges — `.has(source=…, relation=…, target=…)`, `.none(...)` |
| `result.tools` | `.called(name)`, `.called_with(name, **args)`, `.called_times`, `.never_called` |
| `result.llm` | call/token counts — `.calls`, `.tokens`, `.max_calls(n)`, `.by_agent(name)` |
| `result.events` | progress messages from `context.announce()` — `.contains(regex, kind=…)` |
| `result.errors` | isolated agent errors — `.none()`, `.count()`, `.expected(agent)` |

Every failure raises `AssertionFailure` with the observed data inlined, so the
pytest output is the debug report. `result.explain()` prints artifacts, the
relation graph, the agent path, tool/LLM calls and errors as one string.

## Faults and stubs

Queue one-shot faults for the next run — no monkeypatching:

```python
# not-run: illustrative — reuse the agent defined above
lab = ScenarioLab([agent])
lab.fail("search", TimeoutError("boom"), times=1)     # tool raises once
lab.stub_tool("search", text="cached result")          # tool returns a canned value
lab.fail_resource("llm", ConnectionError("offline"))   # the LLM resource fails
lab.stub_resource("embedder", returns=[[0.0] * 8])     # any resource, stubbed
result = lab.run_sync(Question(text="hi"))
result.errors.count() == 1
```

Because reactifact isolates agent errors (`isolate_errors=True` in the harness),
a crashed agent shows up on `result.errors` instead of failing the test; assert
on it.

## Record and replay

`ScenarioLab(..., mode="record")` wraps the LLM in `ReplayLLM` and appends every
call to a JSONL recording; `mode="replay"` answers from it with no network and
raises `ReplayMiss` on divergence. `mode` can come from
`REACTIFACT_SCENARIO_MODE` via `mode_from_env()`. This is what makes an
LLM-in-the-loop test deterministic in CI.

## Golden snapshots

```
result.assert_golden("tests/golden/answerer.json")   # fingerprint: context hash + prompt hashes
```

A missing file (or `REACTIFACT_GOLDEN_UPDATE=1`) records the snapshot; later
drift fails. Use it to catch unintended changes to the whole run shape.

## pytest plugin and CLI

- `pytest_plugins = ["reactifact.testing.pytest_plugin"]` adds a `scenario_lab`
  factory fixture and runs `async def` scenario tests.
- `python -m reactifact scenario <module>` runs `@scenario`-decorated functions
  outside pytest, with a pytest-style failure report and the run state.

## Where to look

- `docs/en/testing.md` for the full harness.
- `docs/en/replay.md` for record/replay and deterministic state replay.
- Score quality (not just structure) with the `reactifact-eval` skill.
