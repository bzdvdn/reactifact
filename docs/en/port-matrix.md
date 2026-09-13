# Port matrix — canonical agent patterns on reactifact

Which classic example from LangGraph / LangChain / CrewAI / AutoGen / Haystack
/ DSPy we express, and how. Each row maps a canonical idea to our idiom and to
a concrete example (`examples/`).

| Canonical idea | Showed by | Our idiom | Example |
| --- | --- | --- | --- |
| ReAct tools loop | LangGraph, LangChain | `LLMAgent`/`HITLLMAgent` + `ToolUse`/`ToolUseHITL`, `FunctionTool` | `devops` |
| HITL tool approval (interrupt/gate) | LangGraph, CrewAI | `effects.ask` → `effects.resume` (§60) | `devops`, `supervisor` |
| Reflection (generate→critique→regenerate) | LangGraph | produces mutate a `Draft` artifact with guards | `reflection` |
| Map-reduce (fan-out then aggregate) | LangChain/LangGraph, Haystack | chunk artifacts → per-chunk produces → combine guard | `map_reduce` |
| Router / supervisor / multi-role agents | CrewAI, AutoGen | routing via `StructuredLLM` + specialist produces | `supervisor` |
| Conversation memory summarization | LangChain, LangGraph | `Msg` artifacts + `context.view` + summarizer produce | `summarize` |
| Time-travel / checkpoint branching | LangGraph | `Context.branch()`, parallel runtimes, three-way `merge()` | `time_travel` |
| RAG (retrieve→augment→generate) | LangChain, Haystack, LlamaIndex | sources + `fan_out_sources` + `materialize_doc` + evidence→claims | `knowledge`, `research` |
| Structured output / extraction / router | LangChain | `StructuredLLM` / `PromptTemplate` / `llm_reply` | everywhere |
| Plan-and-execute | LangGraph | stage produce + `StatusMachine` lifecycle | `repair` |
| Plan-and-execute (canonical port) | LangChain/AutoGPT | planner produce drafts ordered steps; executor produce runs one step per generation, gated on the previous step's result | `plan_execute` |
| Evaluation-driven dev (DSPy) | DSPy | `reactifact.eval` multi-level metrics (§56) | `examples` + tests |
| Tool budget / honesty on failure | — | `Budget` + deterministic fallbacks, `None` paths (§59) | `devops`, `repair` |

Everything above runs **offline** (deterministic fallbacks) and, with a model
via `.env`, uses the real LLM — see `docs/en/effects.md` for the mental model,
and `docs/en/recipes.md` for the building blocks.