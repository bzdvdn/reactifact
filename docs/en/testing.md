# Testing agent pipelines

`reactifact.testing` is a behavioral harness for agent pipelines: **seed some
artifacts, run the agents, assert on what happened**. It is separate from
`pytest` on purpose — scenarios can run live, record a real model, or replay a
recording — but it plugs into `pytest` when you want it to.

```python
from reactifact.testing import ScenarioLab

lab = ScenarioLab([my_agent], resources=lambda: build_resources())
result = await lab.run(Question(text="what's the refund policy?"))

result.artifacts(Answer).exists()
result.artifacts(Answer).linked("supported_by", Evidence)
result.tools.called("search")
result.path.contains("answer")
result.llm.max_calls(3)
result.errors.none()
```

A fresh `Context`/`Runtime` is built on every `run()`, so a queued fault or the
tool-call recorder can't leak between runs.

## Sync or async

`run()`/`turn()` are async; `run_sync()`/`turn_sync()` are the same thing for
plain (non-async) pytest tests:

```python
result = lab.run_sync(Seed(n=21))          # asyncio.run(lab.run(...))
```

## Assertion groups

Everything a scenario needs to assert is a property on `ScenarioResult`
(aggregated variants on `Scenario`):

| Group | What it checks |
| --- | --- |
| `result.artifacts(Type)` | fields on the latest/any artifact: `exists`, `none`, `count`, `contains`, `matches` (regex), `equals(**fields)`, `field_in`, and `linked(relation, Type)` for provenance |
| `result.relations` | the artifact graph's edges: `has`/`none`/`count` (any filter may be a wildcard), `outgoing`/`incoming` |
| `result.tools` | recorded tool calls: `called`, `never_called`, `called_times`, `called_with`, `called_any`, `call_order` |
| `result.path` | the agent execution path: `contains`, `not_contains`, `sequence`, `exact_sequence`, `any_of`, `times` |
| `result.llm` | call/token counts: `calls`, `prompt_tokens`, `completion_tokens`, `tokens`, `max_calls`, `max_tokens`, `by_agent` |
| `result.errors` | isolated agent errors (`isolate_errors=True`, the harness default): `none`, `count`, `expected(agent)` |
| `result.events` | `context.announce()` progress events: `contains`/`not_contains` (regex), `messages(kind=…)`, `count`, `min_count` |

Failures raise `AssertionFailure` with the observed data inlined, so a failing
test is debuggable from the pytest output alone.

### Provenance is an assertion, not a convention

The typed artifact graph is the reason to use reactifact, so the harness asserts
on it directly:

```python
answer = result.artifacts(Answer).exists()
evidence = result.artifacts(Answer).linked("supported_by", Evidence)
result.relations.has(source=answer.id, relation="supported_by")
result.relations.none(relation="contradicted_by")
```

## Multi-turn scenarios

`lab.scenario()` reuses one `Context` across `.turn()` calls, so a later turn
sees everything an earlier one produced — for flows that need more than one
round of input to reach the state under test:

```python
convo = lab.scenario()
await convo.turn(UserMsg(text="вот мои пожелания"))
await convo.turn(UserMsg(text="1"))          # pick design option 1
await convo.turn(UserMsg(text="да, согласен"))  # approve

convo.tools.called_times("estimate", 1)      # aggregated across every turn
convo.path.contains("repair_flow")
```

## Faults and stubs

Everything below is **one-shot**: queued on the lab, consumed by the next
`run()`/`.turn()`, never carried over.

```python
# make a tool raise (times=N: only the first N, then delegate)
lab.fail("search", ConnectionError("unreachable"), times=1)
lab.fail("kubectl", RuntimeError("boom"), when=lambda args: args["resource"] == "pods")
lab.fail("slow_tool", TimeoutError(), delay=5.0)   # slow-then-fail

# make a tool return a canned output instead of running
lab.stub_tool("search", text="canned result")

# the same for any non-tool resource — "llm", "embedder", a source id, a name
# set via resources.set(...), or a typed Type/ResourceKey
lab.fail_resource("llm", ConnectionError("model down"))
lab.fail_resource("embedder", RuntimeError("no vectors"), method="embed")
lab.stub_resource("catalog", returns={"sku": "A1"})
lab.stub_resource("catalog", side_effect=[{"sku": "A1"}, KeyError("missing")])
```

`stub_resource(side_effect=[…])` follows `unittest.mock` semantics: an item
that is an `Exception` is raised, anything else is returned. `when(args)` /
`when(args, kwargs)` narrow a fault to matching calls; `delay` sleeps first
(async resources yield; sync ones block).

Tool faults are one caveat: `ToolUse` catches a tool exception and hands the
model a `"Tool 'x' failed: …"` string, so an injected fault usually does **not**
abort the run — the agent sees it like a real transient failure and may retry.
`result.tools.called("x")[0].error` shows the failed call while
`result.errors.none()` can still pass.

## Record / replay

`mode=` controls the LLM behind `resources.llm`:

- `"live"` (default) — the real provider;
- `"record"` — wraps it, appending every call to `recording_path`;
- `"replay"` — no network; a call that diverges from the recording raises
  `ReplayMiss` instead of guessing (§59).

```python
lab = ScenarioLab(agents, resources=resources,
                  mode=mode_from_env(),              # live/record/replay
                  recording_path="scenarios/data/calls.jsonl")
```

`mode_from_env()` reads `$REACTIFACT_SCENARIO_MODE`, which `reactifact scenario
--mode …` sets — so the same scenario runs live, records, or replays depending
only on how it was invoked.

## Golden snapshots

Freeze a run's fingerprint (`context_hash` + the trace's prompt hashes) and
fail when it drifts. A missing file — or `update=True` /
`$REACTIFACT_GOLDEN_UPDATE=1` — **writes** the snapshot instead of failing, so
the first run seeds it:

```python
result.assert_golden("scenarios/data/gpu.golden.json")
# later: REACTIFACT_GOLDEN_UPDATE=1 pytest   # rewrite after an intended change
```

Pair record/replay with golden for a fully offline regression on a real run.

## Debugging a run

`result.explain()` (and `scenario.explain()`) dumps everything the run produced
— artifacts by type, relations, path, tool/LLM calls, errors — for a failing
test's output or a quick `print()`:

```python
print(result.explain())
```

## Running tests

Under pytest, opt in to the plugin so `async def` scenarios run without
`pytest-asyncio` and `ScenarioSkip` becomes a skip:

```python
# tests/conftest.py
pytest_plugins = ["reactifact.testing.pytest_plugin"]
```

It also provides a `scenario_lab` factory fixture that defaults `mode=` to
`$REACTIFACT_SCENARIO_MODE`:

```python
def test_refund_policy(scenario_lab):
    lab = scenario_lab([my_agent], resources=build_resources)
    result = lab.run_sync(Question(text="refund policy?"))
    result.artifacts(Answer).contains("14 days")
```

Outside pytest, register scenarios with `@scenario` and run them with the CLI —
useful for live/record runs a normal `pytest` session must not touch:

```bash
reactifact scenario examples.repair.scenarios
reactifact scenario examples.knowledge.scenarios --mode replay
```

For *scoring* a pipeline's output (evidence quality, provenance grounding,
calculation correctness) rather than asserting on behavior, see
[Evaluation](eval.md).
