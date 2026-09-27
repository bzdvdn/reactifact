# Tools reference

A `Tool` is a contract for "do this external operation". For an LLM agent it
also carries a JSON schema of its arguments, from which the model chooses the
operation and its args.

## Defining a tool

```python
# not-run: illustrative
from reactifact.tools import ToolOutput, tool


@tool
async def search(query: str) -> str:
    """Search the knowledge base for `query`."""
    return await lookup(query)


@tool(destructive=True)
async def send_email(to: str, body: str) -> ToolOutput:
    """Send an email (requires human approval)."""
    await smtp_send(to, body)
    return ToolOutput(text=f"sent to {to}")
```

- The JSON schema is derived from the function signature (`str`, `int`, model
  fields, defaults); the docstring becomes the description the model sees.
- A tool returns a `str`, a `dict`, a `ToolOutput`, or anything (stringified).
  Failure is `ToolOutput(error=...)` or an exception — not a crash of the run.
- `destructive=True` routes the call through the human-approval gate
  (`PendingQuestion`), so a side effect waits for a human.

`FunctionTool(fn, ...)` is the same without the decorator; subclass `Tool` and
implement `async execute(args) -> ToolOutput` when you need full control, and set
`schema` yourself.

## ToolOutput

```python
# not-run: illustrative
ToolOutput(text="...", data={"rows": [...]}, error="")
```

`text` is what the model sees; `data` is structured payload for your code;
`error`, when set, marks the call failed (the loop still continues).

## Driving a tool loop

`LLMAgent` is the ready-made container. Set `system`, `tools`, and `consumes`;
the model calls tools until it produces a final answer. `max_steps` caps the
loop, `temperature`/`max_tokens` override provider defaults, and
`deferred_tool_groups` (list of `DeferredToolGroup`) keep a group of tools out
of the first steps' schema and reveal them later — a simple two-stage
toolset.

`ToolUse` (a `Produce[ToolAnswer]`) is the underlying produce if you want to
compose the loop yourself; `ToolUseHITL` is the reactive variant that can pause
for a human. Their artifacts — `ToolAnswer`, `Observation`, `PendingQuestion` —
are ordinary types you can consume downstream.

## Human in the loop

```python
# not-run: illustrative
from reactifact.llm_agent import HITLLMAgent

class Assistant(HITLLMAgent):
    system = "Ask a clarifying question when the request is ambiguous."
    tools = [search]
    consumes = [Consume(Question)]
```

The model can emit `type:"ask"`, creating a `PendingQuestion`; the run pauses
and resumes when a human answers (`ToolUseHITL` consumes the resolved question).
This is the same `PendingQuestion` mechanism the destructive-tool gate uses.

## Testing tools

`reactifact.testing.ScenarioLab` records every call (`result.tools`), and can
fault or stub a tool for one run (`lab.fail("search", TimeoutError())`,
`lab.stub_tool("search", text="cached")`) with no monkeypatching. See the
`reactifact-testing` skill.
