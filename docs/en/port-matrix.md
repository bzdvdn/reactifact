# Port matrix — canonical agent patterns on reactifact

Which classic example from LangGraph / LangChain / CrewAI / AutoGen / Haystack
/ DSPy we express, and how. Each row maps a canonical idea to our idiom and to
a concrete example (`examples/`).

| Canonical idea | Showed by | Our idiom | Example |
| --- | --- | --- | --- |
| ReAct tools loop | LangGraph, LangChain | `LLMAgent`/`HITLLMAgent` + `ToolUse`/`ToolUseHITL`, `FunctionTool` | `devops` |
| HITL tool approval (interrupt/gate) | LangGraph, CrewAI | `effects.ask` → `effects.resume` (§60) | `devops`, `repo_agent` |
| Time-travel / checkpoint branching | LangGraph | `Context.branch()`, parallel runtimes, three-way `merge()` | `forklab` |
| RAG (retrieve→augment→generate) | LangChain, Haystack, LlamaIndex | sources + `fan_out_sources` + `materialize_doc` + evidence→claims | `knowledge` |
| Structured output / extraction / router | LangChain | `StructuredLLM` / `PromptTemplate` / `llm_reply` | everywhere |
| Staged pipeline w/ replanning (a `stage`-driven state machine) | LangGraph | `Project.stage`-guarded steps + `changed_fields`/`earliest_stage`/`downstream_fields` (change→rebuild) | `repair` |
| Evaluation-driven dev (DSPy) | DSPy | `reactifact.eval` multi-level metrics (§56) | tests + kept examples |
| Tool budget / honesty on failure | — | `Budget` + deterministic fallbacks, `None` paths (§59) | `devops`, `repair` |
| External side effects (outbox) | — | `effects.act` + `Runtime(dispatcher=...)`, stable id, replay-safe state | `outbox` |
| Framework primitives (versions, selectors, durability) | — | `version_history`/`diff`/`compact`, `Latest`/`Wakes`/`debounce`, `SessionStore`/replay | `tour` |

Everything above runs **offline** (deterministic fallbacks) and, with a model
via `.env`, uses the real LLM — see `docs/en/effects.md` for the mental model,
and `docs/en/recipes.md` for the building blocks.
