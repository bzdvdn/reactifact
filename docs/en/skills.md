# Agent skills

reactifact ships **Agent Skills** — `SKILL.md` files that coding agents
(Claude Code, Codex, Cursor, Gemini CLI, GitHub Copilot, …) load on demand to
write reactifact code the way it is meant to be written. They live inside the
package (`reactifact/skills/`), so the skills you install always match the
version of reactifact you have.

This matters more here than for a typical library: reactifact's paradigm
(artifacts, produce/consume, effects, provenance) is unlike mutation-style agent
frameworks, and a model trained on those will otherwise write stateful,
imperative code that fights the runtime. The skills encode the mental model and
the failure modes — and every runnable code block in them is executed in CI, so
they cannot silently rot.

## Install

```bash
python -m reactifact skills list                      # what is available
python -m reactifact skills install                    # -> .agents/skills/
python -m reactifact skills install --target both      # also .claude/skills/
python -m reactifact skills install reactifact-agents  # one skill
python -m reactifact skills show reactifact            # print a SKILL.md
```

`.agents/skills/` is the format Codex, Cursor, Gemini CLI and Copilot read;
Claude Code also reads `.claude/skills/`. Commit the installed folder so your
whole team gets the same guidance.

## The skills

| Skill | Use it for |
| --- | --- |
| `reactifact` | The entry point: the mental model, the three invariants, the working loop (design → implement → wire → test → evaluate), a routing table, and the top mistakes. Read this first. |
| `reactifact-agents` | Authoring agents and produces deeply: `Consume` variants (`by_status`, `by_field`, `JoinConsume`, `AbsentConsume`, `CorrelatedConsume`), every effect, ids/idempotency, provenance, scheduling. |
| `reactifact-llm` | LLM steps: providers, structured output into typed artifacts, `StructuredGenerateAgent`/`LLMAgent`/HITL, `@tool`, prompts, and token/cost budgets. |
| `reactifact-rag` | Retrieval: sources (filesystem, CSV, web, embeddings), `fan_out_sources`, `materialize_doc`, and the ready-made recipes (router, plan-execute, reflection, memory). |
| `reactifact-testing` | Deterministic tests with `ScenarioLab`: assertions, fault injection, record/replay, golden snapshots, the pytest plugin and the `scenario` CLI. |
| `reactifact-eval` | Quality measurement over a dataset: multi-level metrics, typed trajectory matching, LLM-as-judge, and the CI gate. |
| `reactifact-observability` | Traces and `TraceStore`, Prometheus metrics, sessions/durability, deterministic replay and branching, and the graph/trace CLI. |
| `reactifact-from-langchain` | Porting LangChain/LangGraph code: the concept map and a step-by-step migration recipe. |

## Structure

Each skill is `reactifact/skills/<name>/SKILL.md` with YAML frontmatter
(`name`, `description`) followed by the workflow; larger lookup material lives
in the skill's `references/` folder and is read only when needed. A fenced
```` ```python ```` block runs in CI unless it begins with `# not-run`
(illustrative fragments), so the examples are always valid against the current
API.
