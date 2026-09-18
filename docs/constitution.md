# Constitution of the Artifact-Driven Agent Runtime

> **Status:** Foundational design document, aligned with the public `reactifact` release
> **Version:** 0.2
> **Purpose:** Define the architectural philosophy, invariants, terminology, design rules, examples, and decision criteria for a new agent framework based on evolving typed artifacts and context rather than explicit execution graphs.

---

## 0. Executive Summary

This project is an agent runtime built around one central idea:

> **An agent does not primarily execute a workflow. An agent observes and transforms an evolving Context containing typed Artifacts.**

The framework is intentionally different from graph-first agent frameworks.

Instead of asking developers to describe:

```text
A → B → C → D
```

we ask them to describe:

```text
What data exists?
What kinds of artifacts exist?
What can an agent do with those artifacts?
What conditions make an artifact useful, invalid, incomplete, or ready?
```

The runtime then coordinates execution around state changes.

The fundamental execution model is:

```text
                  ┌──────────────────┐
                  │     Context      │
                  │                  │
                  │ Artifacts        │
                  │ References       │
                  │ Claims           │
                  │ Tasks            │
                  │ Events           │
                  └────────┬─────────┘
                           │
                           │ observed
                           ▼
                    ┌─────────────┐
                    │    Agent    │
                    └──────┬──────┘
                           │
                           │ produces
                           ▼
                    ┌─────────────┐
                    │    Patch    │
                    └──────┬──────┘
                           │
                           │ applied
                           ▼
                  ┌──────────────────┐
                  │   Context v+1    │
                  └──────────────────┘
                           │
                           └───────↺
```

The core primitives are:

```text
Context
Artifact
Reference
Agent
Patch
Event
Runtime
Source
Capability
```

The framework may internally use graphs, queues, DAGs, state machines, schedulers, vector indexes, databases, or LLM planners.

Those are **implementation mechanisms**.

They are not the primary programming model.

---

# 1. Project Thesis

## 1.1 The problem

Modern agent frameworks commonly expose abstractions such as:

```text
Chain
Graph
Node
Tool
Agent
Message
Memory
Workflow
```

These abstractions are useful, but complex applications often become difficult to express when the problem is not naturally a fixed workflow.

Knowledge-oriented agents are especially problematic.

A user can ask:

> Why did infrastructure costs increase in Q2?

The answer may require:

- Confluence documentation
- GitLab Markdown
- GitLab merge requests
- GitLab commits
- CSV files
- XLS/XLSX spreadsheets
- local Markdown
- public landing pages
- direct API calls
- calculations
- source verification
- contradictory evidence
- follow-up questions

There is no universal fixed graph.

One question may require:

```text
Confluence → GitLab → CSV → answer
```

Another:

```text
GitLab → GitLab → verification → answer
```

Another:

```text
XLS → calculation → Confluence → GitLab → verification
```

Another may need only one source.

The framework therefore should not make the developer encode the exact execution path.

---

# 2. Core Principle

## 2.1 Agent as a state transformer

The canonical model is:

```text
Agent(Context) → Effects      # self.effects.create/update/link/ask
                   │
                   ▼  runtime compiles (§24)
               Patch (atomic) → Context v+1
```

not:

```text
Agent(Input) → Output
```

and not:

```text
Node → Node
```

An agent reads a relevant view of the current Context and proposes changes.

Example:

```python
class Researcher(Produce[Evidence]):
    async def produce(self, call: ProduceCall) -> None:
        self.effects.create(Evidence(...))
        self.effects.update(task_id, status="researching")
        return None   # nothing applied until the runtime compiles the effects
```

The produce describes what should change (effects); the runtime compiles the
effect set into one atomic `Patch`:

```text
Context v12
   │
   │ Researcher (effects)
   ▼
Effects ──compile──▶ Patch ──apply──▶ Context v13
```

This distinction is foundational.

---

# 3. Why Context Exists

A Context is the current working state of a reasoning process.

It is not merely:

```python
dict
```

It is not merely:

```python
messages
```

It is not merely:

```python
prompt_context
```

It represents the evolving state of a task.

Conceptually:

```text
Context
├── Task
├── Questions
├── References
├── Artifacts
├── Evidence
├── Claims
├── Hypotheses
├── Calculations
├── Decisions
├── Events
└── Answers
```

A Context can evolve:

```text
Context v1
   ↓
question added
   ↓
Context v2
   ↓
references discovered
   ↓
Context v3
   ↓
documents materialized
   ↓
Context v4
   ↓
claims extracted
   ↓
Context v5
   ↓
claims verified
   ↓
Context v6
```

The history is part of the system's observability.

---

# 4. Artifact

## 4.1 Definition

An Artifact is a typed object representing something meaningful to the task.

Examples:

```text
Question
Task
Document
ConfluencePage
GitLabFile
GitLabMergeRequest
GitLabCommit
Spreadsheet
SpreadsheetRange
MarkdownDocument
WebPage
Evidence
Claim
Hypothesis
Finding
Calculation
Report
Answer
```

Artifacts are first-class objects.

---

## 4.2 Artifact is not necessarily text

This is critical.

A spreadsheet should not automatically become:

```text
"CSV converted to text"
```

A GitLab merge request should not automatically become:

```text
"chunk of text"
```

A Confluence page should not automatically become:

```text
"embedding"
```

Instead:

```text
GitLabRepository
├── File
├── Commit
├── MergeRequest
├── Issue
└── Discussion

Workbook
├── Sheet
│   ├── Columns
│   └── Rows
└── NamedRanges

ConfluenceSpace
└── Page
    ├── Section
    ├── Table
    └── Link
```

Structure is preserved whenever practical. Falling back to flattened text is acceptable only as a stopgap before a proper typed Artifact exists for a source — it must not become the permanent representation for a domain the framework already models structurally (GitLab, Confluence, Spreadsheets).

---

# 5. Reference vs Artifact

This distinction is fundamental.

A **Reference** identifies something in an external system.

An **Artifact** is a materialized representation available for reasoning.

Example:

```text
ConfluencePageRef
        │
        │ resolve()
        ▼
ConfluencePage
```

A reference may contain:

```python
ConfluencePageRef(
    space="ENGINEERING",
    page_id="12345",
    title="Authentication Architecture",
)
```

It does not need to contain the entire page.

The page can be fetched later.

This makes References cheap and lazy.

---

# 6. Lazy Artifacts

External knowledge should not be eagerly loaded.

Example:

```python
page = ConfluencePageRef(
    page_id="12345"
)
```

At this point:

```text
metadata = available
content = not materialized
```

When needed:

```python
page.resolve()
```

the connector accesses Confluence.

Conceptually:

```text
Reference
├── locator
├── metadata
└── resolver
       │
       ▼
   external system
       │
       ▼
   Artifact
```

This is important because not every source is indexed or embedded.

---

# 7. Source

A Source describes an external system from which References or Artifacts can be obtained.

Examples:

```text
GitLab
Confluence
Filesystem
Website
Database
S3
REST API
```

A Source is not an Agent.

A Source answers:

> How can information be located or materialized?

An Agent answers:

> What should be reasoned about or changed?

Example:

```python
gitlab = GitLabSource(...)
confluence = ConfluenceSource(...)
filesystem = FileSystemSource(...)
```

The core ships *reference sources* demonstrating three retrieval strategies
(§8-§9): `FileSystemSource` (keyword/full-text), `CSVSource` (structured tables,
§29) and `EmbeddingSource` (optional vector). GitLab / Confluence / S3 and other
enterprise systems are **domain-specific connectors**: application code on top
of the `Source` API (typically under `examples/`), not core — the framework must
not become a catalog of integrations (§61).

---

# 8. Retrieval is a Source Capability

The framework must not assume that every Source uses embeddings.

Possible strategies:

```text
Vector search
Keyword search
GitLab API search
Confluence API search
SQL
Filesystem traversal
Regex
AST search
HTTP fetch
Browser navigation
Direct object lookup
```

A source may use:

```python
ConfluenceSource(
    retrieval="direct_api"
)
```

while another uses:

```python
MarkdownSource(
    retrieval="vector"
)
```

and another:

```python
SpreadsheetSource(
    retrieval="sql"
)
```

The Agent should not care.

The Agent asks for knowledge.

The Source decides how to obtain it.

---

# 9. Embeddings are optional

Embeddings are a capability, not an architectural requirement.

The framework must support:

```text
Source A → vector search
Source B → direct API
Source C → SQL
Source D → filesystem
Source E → browser
Source F → GitLab search
```

A system can therefore combine:

```text
                 Knowledge Runtime
                        │
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
     Vector           Direct          Structured
      Search           API              Query
        │               │               │
        ▼               ▼               ▼
     Markdown       Confluence        XLS/CSV
```

This is one of the project's deliberate departures from embedding-first RAG.

---

# 10. Agent

An Agent is a component capable of interpreting Context and describing a
change-set: produces write `self.effects` (§24); the runtime compiles them into
a `Patch`.

Minimal conceptual interface:

```python
class Agent:
    async def produce(self, call: ProduceCall) -> None:
        ...
```

An Agent may use:

- an LLM
- deterministic code
- SQL
- APIs
- tools
- other agents
- external programs
- statistical methods
- search
- tests

An Agent is therefore not synonymous with "LLM".

---

# 11. Agent Input and Output Contracts

Agents should declare what they understand.

Example:

```python
class Verifier(Agent):
    consumes = [Claim, Evidence]
    produces = [VerifiedClaim, MissingEvidence]
```

Another:

```python
class DataAnalyst(Agent):
    consumes = [Spreadsheet, Hypothesis]
    produces = [Calculation, Finding]
```

Another:

```python
class Researcher(Agent):
    consumes = [Question, Reference]
    produces = [Evidence, Claim]
```

These declarations are hints and contracts.

They are not necessarily a rigid execution graph.

---

# 12. Patch

A Patch is the *compiled* change-set the runtime applies. The authoring
surface is `self.effects` (§24): a produce writes `create/update/link/ask` and
returns `None`; the runtime compiles the effect set into one atomic `Patch`
(commit, events, validation, trace). `Patch` is transport — applications rarely
build one directly (exceptions: the `Agent.run` escape hatch and advanced
assembly).

The compiled operations:

```text
ADD
UPDATE
REMOVE
LINK
UNLINK
```

Example:

```python
Patch(
    AddArtifact(
        Evidence(
            source=ref,
            content="...",
        )
    ),
    UpdateArtifact(
        claim_id,
        confidence=0.82,
    ),
    Link(
        claim_id,
        evidence_id,
        relation="supported_by",
    ),
)
```

The runtime validates and applies the patch.

---

# 13. Why Changes Are Patches (effects → Patch)

Returning arbitrary objects creates weak composition:

```python
result = agent.run(...)
```

What does `result` mean?

How do we:

- merge it?
- inspect it?
- undo it?
- version it?
- audit it?
- compare two agent executions?
- replay it?
- detect conflicts?

A Patch solves these problems.

```text
Context v10
     │
     ▼
 Agent
     │
     ▼
 Patch
     │
 ├── ADD Claim
 ├── ADD Evidence
 ├── UPDATE confidence
 └── LINK Claim → Evidence
     │
     ▼
Context v11
```

---

# 14. Immutable Revisions

The public programming model may feel mutable:

```python
claim.confidence = 0.91
```

Internally, state should be revisioned:

```text
Claim v1
   ↓
Claim v2
   ↓
Claim v3
```

The Context similarly has versions:

```text
Context v1
Context v2
Context v3
...
```

This enables:

```python
context.history()
context.diff(v4, v9)
context.rollback(v7)
context.snapshot()
```

The initial implementation may use SQLite or another simple persistence layer.

Do not prematurely build a distributed database.

---

# 15. Artifact Graph vs Execution Graph

This distinction is one of the central architectural decisions.

An execution graph says:

```text
A → B → C → D
```

An Artifact Graph says:

```text
Claim
├── supported_by → Evidence
├── derived_from → Document
└── contradicted_by → Evidence
```

The framework primarily models the second.

Execution may emerge from artifact dependencies and events.

---

# 16. Example: Knowledge Chat

Suppose the user asks:

> Why did infrastructure costs increase in Q2?

Available sources:

```text
GitLab
Confluence
CSV
XLSX
Markdown
Website
```

The Context starts as:

```text
Context
└── Question
    └── "Why did infrastructure costs increase in Q2?"
```

The runtime discovers useful References:

```text
Question
├── GitLabRef
├── ConfluenceRef
├── CSVRef
└── XLSXRef
```

The researcher materializes selected data:

```text
Evidence
├── Confluence: Infrastructure Costs
├── GitLab MR !1842
├── XLSX: GPU Costs / May
└── CSV: cloud_usage.csv
```

The analyst creates:

```text
Calculation
└── GPU costs increased by 43%
```

The investigator creates:

```text
Finding
└── GPU inference workers deployed May 14
```

The verifier creates:

```text
VerifiedClaim
└── GPU workload growth is strongly associated with Q2 cost increase
```

The Answer agent produces:

```text
Answer
├── Claim
│   ├── Evidence
│   └── Evidence
├── Claim
│   └── Evidence
└── Confidence
```

The user receives a human-readable answer.

But internally the answer remains structured.

---

# 17. Answer as Artifact

An Answer is not merely:

```python
str
```

It should contain claims and provenance.

Conceptually:

```text
Answer
├── text
├── claims
│   ├── Claim #1
│   │   ├── Evidence #4
│   │   └── Evidence #8
│   └── Claim #2
│       └── Evidence #12
├── confidence
└── generated_at
```

This enables a UI such as:

```text
Infrastructure costs increased by 43%.

[Why?]

Evidence:
  XLSX / GPU Costs / May
  GitLab MR !1842
  Confluence / Infrastructure Migration
```

---

# 18. Evidence

Evidence is an explicit Artifact.

It should preserve provenance.

Example:

```python
Evidence(
    source=gitlab_mr,
    location="MR !1842",
    excerpt="...",
)
```

For spreadsheets:

```python
Evidence(
    source=workbook,
    sheet="Infrastructure",
    range="B182:F214",
)
```

For Confluence:

```python
Evidence(
    source=page,
    section="GPU Migration",
)
```

Evidence should point back to the source.

The framework should avoid creating unsupported claims.

---

# 19. Claims

A Claim is a proposition that can be evaluated.

Example:

```python
Claim(
    statement="GPU infrastructure costs increased by 43%",
    confidence=0.91,
)
```

A Claim can have relationships:

```text
Claim
├── supported_by
├── contradicted_by
├── derived_from
└── verified_by
```

Claims are useful because reasoning becomes inspectable.

---

# 20. Hypotheses

A Hypothesis is a candidate explanation.

Example:

```text
Hypothesis A:
GPU workload increased.

Hypothesis B:
Cloud provider pricing changed.

Hypothesis C:
Kubernetes migration increased infrastructure overhead.
```

The system can investigate these independently.

This gives the runtime a natural way to perform parallel reasoning without requiring the developer to manually build a graph.

---

# 21. Reactive Execution

Agents can react to Context changes.

Example:

```text
ClaimAdded
      │
      ▼
Verifier
      │
      ▼
VerifiedClaim
      │
      ▼
Answerer
```

If evidence is missing:

```text
Verifier
   │
   ▼
MissingEvidence
   │
   ├── GitLabResearcher
   ├── ConfluenceResearcher
   └── WebResearcher
```

This creates loops naturally:

```text
Research
  ↓
Evidence
  ↓
Claim
  ↓
Verification
  ↓
Missing Evidence
  ↓
Research
  ↺
```

The loop does not need to be encoded as a graph by the developer.

---

# 22. Events

An Event describes a meaningful change.

Examples:

```text
ArtifactAdded
ArtifactUpdated
ClaimCreated
EvidenceAdded
EvidenceMissing
ClaimVerified
ClaimRejected
ConfidenceChanged
TaskBlocked
TaskCompleted
```

Events are derived from patches or emitted by the runtime.

They can trigger Agents.

---

# 23. Runtime

The Runtime coordinates:

- Context
- Agents
- Sources
- Events
- Patches
- scheduling
- validation
- persistence
- observability

Conceptually:

```text
                  Runtime
                     │
       ┌─────────────┼─────────────┐
       ▼             ▼             ▼
    Context        Agents        Sources
       │             │             │
       │             │             │
       └─────────────┼─────────────┘
                     ▼
                  Scheduler
                     │
                     ▼
            Effects → Patch
                     │
                     ▼
               Context v+1
```

The Runtime is allowed to use a graph internally.

The public API should not require one.

---

# 24. Scheduling Philosophy

The scheduler should answer:

> Which Agent has useful work to perform given the current Context?

Not:

> Which node is next in my graph?

Possible signals:

```text
Artifact type
Artifact state
Events
Agent capabilities
Missing information
Priority
Budget
Confidence
Dependencies
User intent
```

Example:

```text
Context:
  4 Claims
  2 unverified
  1 missing evidence

Scheduler:
  Verifier has work
  Researcher has work
  Answerer is blocked
```

---

# 25. Agent Capability Model

Agents should expose capabilities.

Example:

```python
Verifier.capabilities = {
    "verify_claim",
    "detect_contradiction",
}
```

A Data Analyst:

```python
DataAnalyst.capabilities = {
    "query_table",
    "calculate",
    "aggregate",
    "compare",
}
```

A Researcher:

```python
Researcher.capabilities = {
    "search_sources",
    "extract_evidence",
    "form_hypothesis",
}
```

Capabilities can later be used by an LLM planner or deterministic scheduler.

In the shipped runtime the capability contract is declarative and explicit:
`consumes`/`produces` artifact types (§10-§11, `Consume`/`Produce`). The
scheduler and the LLM router derive work from these declarations, and the
runtime validates that an agent only creates artifacts it declared in
`produces`.

---

# 26. Deterministic vs LLM Scheduling

The framework should support both.

Deterministic:

```text
if unverified_claim exists:
    run Verifier
```

LLM-driven:

```text
Given the current Context, decide which capability would most reduce uncertainty.
```

Hybrid:

```text
                 Scheduler
                    │
          ┌─────────┴─────────┐
          ▼                   ▼
   deterministic         LLM policy
          │                   │
          └─────────┬─────────┘
                    ▼
               Agent choice
```

The framework should not assume that every orchestration decision requires an LLM.

---

# 27. Context Views

Agents should not automatically receive the entire Context.

Instead:

```python
view = context.view(...)
```

Example:

```text
Researcher sees:
  Question
  References
  relevant metadata

Analyst sees:
  Question
  Spreadsheet
  Hypotheses

Verifier sees:
  Claims
  Evidence
  Source provenance

Answerer sees:
  Verified Claims
  Evidence
  User constraints
```

This protects token budgets and improves reasoning quality.

---

# 28. Context is not Prompt

A Context is structured state.

A Prompt is a serialization of some Context view for a model.

Therefore:

```text
Context
   │
   ▼
Context View
   │
   ▼
Prompt / Tool Calls
   │
   ▼
LLM
   │
   ▼
Patch
```

The framework should never make the mistake of equating:

```text
Context == prompt string
```

---

# 29. Structured Data Must Remain Structured

For CSV/XLS/XLSX:

Do not default to:

```text
file → chunks → embeddings
```

Prefer:

```text
Workbook
├── Sheet
│   ├── Schema
│   ├── Row
│   └── Range
```

Agents may use:

```text
SQL
DuckDB
Pandas
Python
spreadsheet formulas
```

Example:

```python
table = ctx.get(Spreadsheet, "costs.xlsx")

result = table.query("""
    SELECT month, SUM(gpu_cost)
    FROM costs
    GROUP BY month
""")
```

The result becomes:

```text
CalculationArtifact
```

with provenance.

---

# 30. Markdown and GitLab

Markdown documents should remain documents.

GitLab entities should remain structured where possible.

Example:

```text
GitLabRepository
├── File
├── Commit
├── MergeRequest
├── Issue
├── Discussion
└── Pipeline
```

A Markdown file may be materialized as:

```python
MarkdownDocument(
    repository="backend",
    path="docs/auth.md",
    revision="abc123",
)
```

This allows agents to reason about both content and provenance.

---

# 31. Confluence

Confluence should be treated as a live external source.

A page can be:

```text
ConfluencePageRef
        ↓
ConfluencePage
        ↓
Sections
        ↓
Evidence
        ↓
Claims
```

The framework should support direct access without requiring indexing.

This is a first-class use case, not a fallback.

---

# 32. Web / Landing Pages

A website can similarly produce:

```text
WebPageRef
    ↓
WebPage
    ├── title
    ├── sections
    ├── links
    └── content
```

A source may support:

```text
URL lookup
crawl
search
browser interaction
```

Again, embedding is optional.

---

# 33. Derived Artifacts

Not every Artifact comes from an external Source.

Some are produced by reasoning.

Examples:

```text
Document → Claim
Spreadsheet → Calculation
Claim[] → Finding
Finding[] → Conclusion
Evidence[] → VerifiedClaim
Claims[] → Answer
```

These are Derived Artifacts.

They should retain provenance:

```text
Calculation
├── derived_from
│   ├── SpreadsheetRange A1:F42
│   └── SpreadsheetRange G1:K42
└── operation
    └── SUM + GROUP BY
```

---

# 34. Provenance is First-Class

Every important derived object should answer:

> Why does this exist?

For example:

```text
Answer
  ↓ derived_from
Claim
  ↓ supported_by
Evidence
  ↓ extracted_from
ConfluencePage
  ↓ fetched_from
Confluence API
```

This creates an evidence graph.

---

# 35. Confidence

Confidence belongs to claims and conclusions, not blindly to entire answers.

Example:

```text
Answer
├── Claim A
│   └── confidence = 0.96
├── Claim B
│   └── confidence = 0.72
└── Claim C
    └── confidence = 0.48
```

The answer can therefore communicate uncertainty precisely.

---

# 36. Contradictions

Contradictions are first-class.

Example:

```text
Claim A:
"Migration completed in May."

Evidence:
Confluence → May 12

Contradicting Evidence:
GitLab → final migration commit June 3
```

The runtime should not silently choose one.

Instead:

```text
Claim
├── supported_by Evidence A
└── contradicted_by Evidence B
```

A verifier can investigate.

---

# 37. Follow-up Questions

A chat session should reuse Context.

Example:

```text
User:
Why did costs increase?

Context v20
   ↓
Answer v20
```

Then:

```text
User:
How much of that was inference?

Context v21
```

The new question is added to the existing Context.

Previously materialized artifacts can be reused.

The system should not restart from zero.

---

# 38. Incremental Reasoning

A follow-up should only perform necessary work.

Example:

```text
Existing:
GPU costs
GitLab MR
Confluence migration plan

New question:
How much was inference?

Required:
Inference usage data
```

The runtime should avoid repeating:

```text
GitLab search
Confluence search
GPU calculation
```

unless evidence is stale or insufficient.

---

# 39. Branching

Contexts should be branchable.

```python
branch_a = context.branch()
branch_b = context.branch()
```

Example:

```text
                   Context v10
                    /       \
                   /         \
          Hypothesis A     Hypothesis B
              │                 │
           research          research
              │                 │
          evidence A        evidence B
                   \         /
                    evaluator
                       │
                       ▼
                  merged state
```

This is not primarily an execution graph.

It is alternative state exploration.

---

# 40. Merge

Branches should be mergeable when changes do not conflict.

Example:

```text
Branch A:
+ Evidence A

Branch B:
+ Evidence B

Merge:
+ Evidence A
+ Evidence B
```

Conflicting updates must be explicit:

```text
Branch A:
Claim.confidence = 0.8

Branch B:
Claim.confidence = 0.4

Merge conflict
```

The framework must not silently choose.

Today this is enforced directly: `Context.merge()` raises `MergeConflict` and applies nothing when branches touch the same field with different values; resolving it (picking a value, or writing a merge policy) is the caller's responsibility.

A verifier or merge policy can resolve it.

---

# 41. Transactions

Patch application should be atomic.

Either:

```text
ADD Claim
ADD Evidence
LINK Claim → Evidence
```

all succeed,

or the patch does not become visible.

This makes reasoning reproducible.

---

# 42. Idempotency

Agents may be retried.

A repeated execution should not create uncontrolled duplicates.

For example:

```text
Researcher runs twice
```

should not necessarily produce:

```text
Evidence #1
Evidence #2
Evidence #3
Evidence #4
```

for the same source.

Artifacts should have stable identities. In core, this is unconditional: `SourceRef.stable_id()` derives an id from `sha1(source_id:locator)`, so re-running a Source against the same locator resolves to the same Artifact rather than creating a duplicate.

---

# 43. Staleness

External artifacts can become stale.

Example:

```text
ConfluencePage
revision=17
```

Later:

```text
revision=18
```

The framework should be able to mark:

```text
Artifact:
  stale=True
```

or materialize a new revision.

Derived claims can then be invalidated or re-evaluated.

---

# 44. Invalidations

If a source changes:

```text
Source changed
    ↓
Artifact stale
    ↓
Derived artifacts affected
    ↓
Claims marked stale
    ↓
Verification scheduled
```

This creates a dependency-aware knowledge system.

---

# 45. Memory

Memory should not be a single bucket.

Distinguish:

```text
Context State
Task Artifacts
Persistent Knowledge
Conversation History
Agent Experience
Source Cache
```

Do not create a vague:

```python
memory = [...]
```

and put everything there.

---

# 46. Tool

A Tool performs an operation.

Examples:

```text
GitLab search
Confluence fetch
SQL query
Python execution
HTTP request
File read
```

A Tool is not necessarily an Agent.

A useful distinction:

```text
Tool:
"Do this operation."

Agent:
"Decide what operation or reasoning is useful."
```

An Agent may use tools.

A Source may expose tools.

---

# 47. Source vs Tool

Example:

```text
Confluence Source
├── search_pages
├── get_page
└── get_children
```

These are tools/capabilities of the Source.

An Agent decides:

```text
I need the Authentication Architecture page.
```

The runtime or agent invokes:

```text
Confluence.get_page(...)
```

The result becomes an Artifact.

---

# 48. Agent Composition

Composition should happen through artifacts.

Not:

```python
agent_a >> agent_b >> agent_c
```

Prefer:

```text
Agent A
  produces Claim

Agent B
  consumes Claim

Agent C
  consumes VerifiedClaim
```

The runtime connects them through state.

This allows multiple producers and consumers.

---

# 49. One Artifact, Many Agents

Example:

```text
Claim #42
   │
   ├── Verifier
   ├── ContradictionDetector
   ├── Summarizer
   └── Answerer
```

No explicit graph edge is required.

Each agent declares interest in the Artifact type/state.

---

# 50. One Agent, Many Artifact Types

Example:

```python
DataAnalyst(
    consumes=[
        Spreadsheet,
        CSV,
        Hypothesis,
    ],
    produces=[
        Calculation,
        Finding,
    ],
)
```

The runtime creates the relevant Context view.

---

# 51. The Agent Should Not Own the State

Bad:

```python
agent.memory = ...
agent.state = ...
```

Preferred:

```text
Context owns state.
Agent operates on a Context view.
Runtime owns execution.
```

This makes agents composable.

---

# 52. User Interaction

The chat layer should be thin.

Conceptually:

```text
User message
     ↓
Question Artifact
     ↓
Context
     ↓
Runtime
     ↓
Agents
     ↓
Artifacts
     ↓
Answer Artifact
     ↓
UI
```

The UI should render structured artifacts.

---

# 53. Streaming

The runtime should be able to stream events:

```text
Searching GitLab...
Found MR !1842
Reading Confluence page...
Analyzing spreadsheet...
Verified claim...
Generating answer...
```

These are runtime events, not necessarily LLM tokens.

The UI can separately stream model output.

---

# 54. Observability

Every meaningful operation should be inspectable.

At minimum:

```text
Run
Agent
Input Context version
Output Patch
Tool calls
Artifacts created
Artifacts modified
Latency
Tokens
Cost
Errors
```

Example:

```text
Run #184

Agent: Verifier
Context: v47
Duration: 8.2s

Reads:
  Claim #12
  Evidence #91
  Evidence #93

Produces:
  Claim #12 v4

Patch:
  confidence 0.72 → 0.91
```

This is essential for debugging agent systems.

---

# 55. Replay

Given:

```text
Context v20
Agent execution
Patch
```

the system should eventually support replay.

This allows:

```text
Why did the agent produce this answer?
```

to be answered concretely.

---

# 56. Evaluation

Because state is structured, evaluation can happen at multiple levels.

Instead of only:

```text
answer == expected_answer
```

evaluate:

```text
Evidence quality
Claim correctness
Provenance correctness
Calculation correctness
Confidence calibration
Answer quality
Source coverage
```

Example:

```text
Research quality: 0.91
Claim verification: 0.96
Calculation: 1.00
Final answer: 0.89
```

---

# 57. Security

**Status: planned, not yet implemented.** The rules below describe the target design — no Artifact currently carries `owner`/`permissions`/`classification`, and Context views do not yet enforce access control.

Artifacts may contain sensitive information.

Every Artifact should eventually support:

```text
owner
permissions
source
classification
visibility
```

A Context view should enforce access control.

An Agent must not receive artifacts it is not allowed to see.

---

# 58. Cost and Budget

Budget belongs to Runtime, not directly to the LLM.

Possible dimensions:

```text
tokens
time
tool calls
network requests
money
parallel agents
```

Example:

```python
Budget(
    max_tokens=100_000,
    max_time=120,
    max_tool_calls=30,
)
```

The runtime may terminate or downgrade expensive strategies.

---

# 59. Failure Model

Failures should become state, not just exceptions.

Examples:

```text
ToolFailed
SourceUnavailable
EvidenceMissing
ArtifactInvalid
AgentFailed
VerificationFailed
BudgetExceeded
```

Some failures should trigger recovery.

Example:

```text
Confluence unavailable
       ↓
SourceUnavailable
       ↓
Try cached artifact
       ↓
Try GitLab
       ↓
Report uncertainty
```

---

# 60. Human-in-the-loop

Humans should interact with Context and Artifacts.

Possible operations:

```text
Approve Claim
Reject Claim
Edit Artifact
Add Evidence
Resolve Conflict
Approve Action
```

Human changes should produce normal Patches.

This keeps the state model unified.

---

# 61. What the Framework Is Not

The project is not:

### Another LangChain

It does not primarily provide a large catalog of integrations.

### Another LangGraph

It does not require users to describe execution as a graph.

### Another CrewAI

It does not primarily model teams of role-playing agents.

### Another DSPy

It does not primarily optimize prompts/programs.

### Another vector database

Embeddings are optional.

### Another workflow engine

Workflows may exist internally, but they are not the central developer abstraction.

---

# 62. Relationship to Existing Frameworks

A conceptual comparison:

| System         | Primary abstraction                  |
| -------------- | ------------------------------------ |
| LangChain      | Chains, tools, agents                |
| LangGraph      | Explicit stateful graph              |
| CrewAI         | Role-based multi-agent collaboration |
| DSPy           | Optimizable LM programs              |
| This framework | Evolving typed Context + Artifacts   |

The goal is not to prove that the new abstraction is universally better.

The goal is to make it especially strong for:

- knowledge agents
- enterprise assistants
- research agents
- coding agents
- data analysis
- multi-source investigation
- long-running reasoning
- artifact-producing agents

---

# 63. Design Rule: Prefer State Semantics Over Execution Semantics

When designing an API, ask:

> Can this be expressed as a property of Context and Artifacts?

before asking:

> Which node should run next?

Example.

Instead of:

```python
graph.add_edge(
    researcher,
    verifier
)
```

prefer:

```python
Verifier(consumes=[Consume(UnverifiedClaim)])
```

The runtime derives execution.

---

# 64. Design Rule: Preserve Information Structure

Do not flatten structured data unnecessarily.

Prefer:

```text
Spreadsheet → Table → Range
```

over:

```text
Spreadsheet → Text chunks
```

Prefer:

```text
GitLab MR → commits → files
```

over:

```text
GitLab MR → text chunk
```

Prefer:

```text
Confluence Page → sections
```

over:

```text
Confluence Page → embedding
```

Embeddings can coexist with structure.

They should not destroy it.

---

# 65. Design Rule: Every Derived Artifact Should Have Provenance

If an agent creates:

```text
Finding
```

we should eventually be able to answer:

```text
What created it?
From which artifacts?
Using which tools?
At which Context version?
```

This is mandatory for trustworthy systems.

---

# 66. Design Rule: Agents Should Be Replaceable

A Context should not depend on a specific LLM.

This should be possible:

```text
GPT Agent
   ↓
Patch
```

and:

```text
Claude Agent
   ↓
Patch
```

and:

```text
Local Model Agent
   ↓
Patch
```

If they respect the same contracts, the Runtime should not care.

---

# 67. Design Rule: Deterministic Work Should Not Use an LLM

If something can be safely done with deterministic code:

```text
CSV aggregation
JSON parsing
schema validation
diff
sorting
filtering
SQL
```

do not require an LLM.

The LLM should handle ambiguity, interpretation, planning, and reasoning.

---

# 68. Design Rule: The LLM Should Not Own Truth

The model may propose:

```text
Claim
```

but the system should preserve:

```text
Evidence
Provenance
Verification
Confidence
```

The model is a reasoning component, not the source of truth.

---

# 69. Design Rule: Make Illegal States Visible

If an Answer contains a Claim with no Evidence, this should be detectable.

If a Calculation references a deleted Spreadsheet Range, this should be detectable.

If a Claim is contradicted, this should be represented.

Do not hide these conditions inside strings.

---

# 70. Design Rule: Prefer Explicit Uncertainty

Bad:

```text
The migration caused the increase.
```

Better internal representation:

```text
Claim:
  statement = "Migration caused the increase"
  confidence = 0.61
  supported_by = [...]
  contradicted_by = [...]
```

The UI may still render a concise answer.

---

# 71. Initial Python API

The primary programming model, as shipped:

```python
from pydantic import BaseModel

from reactifact import (
    Agent,
    Budget,
    Consume,
    Context,
    Patch,
    Produce,
    Runtime,
    RuntimeResources,
)
from reactifact.sources import FileSystemSource, SourceRef


class Question(BaseModel):
    text: str


ctx = Context(
    resources=RuntimeResources(
        sources={"docs": FileSystemSource("./docs")},
    )
)
ctx.create(Question(text="How is authentication implemented?"))
```

Agents are thin containers (§48): all logic lives in a `Produce` class that
writes `self.effects.create/update/link/ask` and returns `None`; the runtime
compiles the effects into one `Patch`. `consumes`/`produces` are the artifact
contracts that drive the scheduler:

```python
class Researcher(Produce[Evidence]):
    artifact_type = Evidence

    async def produce(self, call: ProduceCall) -> None:
        ...  # writes self.effects.create/update/link/ask, returns None


class Verifier(Produce[VerifiedClaim]):
    artifact_type = VerifiedClaim

    async def produce(self, call: ProduceCall) -> None:
        ...


class Answerer(Produce[Answer]):
    artifact_type = Answer

    async def produce(self, call: ProduceCall) -> None:
        ...


class ResearcherAgent(Agent):
    name = "researcher"
    consumes = [Consume(Question), Consume(SourceRef)]
    produces = [Researcher()]


runtime = Runtime(
    ctx,
    agents=[ResearcherAgent(), VerifierAgent(), AnswererAgent()],
    budget=Budget(max_runs=80),
)
runtime.run()
```

`Agent.run` in the framework's terminology is `Produce.produce`:
interpret a relevant Context view, describe changes via `self.effects`, and let
the runtime compile + apply them.

---

# 72. Target Knowledge Chat API

As built in `examples/knowledge` (imports elided — they mirror the demo):

```python
resources = RuntimeResources(
    llm=llm,
    sources={
        "guide": FileSystemSource("./docs/guide"),
        "pricing": FileSystemSource("./docs/pricing"),
        "costs": CSVSource("./docs/costs"),  # §29: structure, not text
    },
)
session = SessionStore(FileKVBackend("./sessions")).open("knowledge", resources=resources)

runtime = Runtime(
    session.context,
    agents=[
        Planner(), SearchScout(), ResolverAgent(), TableResolver(),
        EvidenceBuilder(), VerifierAgent(), CalculatorAgent(),
        ProgressEvaluator(), AnswerBuilder(),
    ],
    budget=Budget(max_runs=80),
)

query = session.context.create(UserQuery(text="Why did infrastructure costs increase in Q2?"))
runtime.run()
```

The developer does not describe a graph. Agents react to artifact types
(`Consume`), and the runtime derives execution from state changes. The chat
layer is thin (§52): a question enters the Context, agents produce evidence
and verified claims, an answer emerges with provenance.

---

# 73. Internal Execution Example

The user sees:

```text
Why did infrastructure costs increase in Q2?

Infrastructure costs increased by 43%, primarily due to
increased GPU inference workloads introduced during Q2.
```

Internally:

```text
Question #1
   ↓
References discovered
   ↓
ConfluencePage #18
GitLabMR #1842
Spreadsheet #77
   ↓
Evidence #91
Evidence #92
Evidence #93
   ↓
Calculation #12
   ↓
Hypothesis #7
   ↓
Claim #22
   ↓
Verification
   ↓
VerifiedClaim #23
   ↓
Answer #5
```

---

# 74. MVP Roadmap

## Phase 1 — Core state model

Implement only:

```text
Artifact
Reference
Context
Patch
Revision
```

No autonomous agents.

Goal:

```text
create → update → diff → rollback
```

---

## Phase 2 — Reference sources

Reference sources shipped in the core, each demonstrating a retrieval strategy
(§8-§9):

```text
Filesystem   keyword / full-text  (FileSystemSource)
CSV          structured tables    (CSVSource → Spreadsheet/Calculation)
Vector       optional embedding   (EmbeddingSource)
```

Goal:

```text
Reference → Artifact
```

Support lazy resolution.

GitLab, Confluence and similar enterprise systems are **domain-specific
connectors**: application code on top of the `Source` API (typically under
`examples/`), so the core does not accumulate a catalog of integrations.

---

## Phase 3 — Agent contract

Implement:

```python
Produce.produce(Context) -> None          # writes self.effects.*; runtime compiles
# Effects(create/update/link/ask) -▶ Patch: the atomic, validated change
# plus the thin Agent container: consumes / produces declarations
```

Add:

```text
consumes
produces
```

---

## Phase 4 — Reactive Runtime

Implement:

```text
Event
Scheduler
Agent selection
Patch application
```

Goal:

```text
Artifact changes → relevant Agent executes
```

---

## Phase 5 — Knowledge Chat

Implement:

```text
Question
Research
Evidence
Claim
Verification
Answer
```

Goal:

> Ask a question over GitLab + Confluence + files and receive an evidence-backed answer.

---

## Phase 6 — Structured data

Add:

```text
CSV
XLS
XLSX
DuckDB
Python execution
```

Goal:

> Agents can calculate instead of hallucinating calculations.

---

## Phase 7 — Provenance and UI

Add:

```text
Evidence graph
Artifact history
Diff
Sources
Claim inspection
```

---

## Phase 8 — Branching and advanced reasoning

Add:

```text
Context branching
Parallel hypotheses
Merge
Conflict resolution
```

---

## Phase 9 — Adaptive scheduler

Add:

```text
budget
priority
uncertainty reduction
LLM scheduling
```

Only after the core model is proven.

---

# 75. What Not to Build First

Do not start with:

```text
Multi-agent teams
Long-term memory
Vector database
Autonomous planning
Browser agents
Distributed execution
Complex DAG engine
Agent marketplace
Fine-tuning
```

These are secondary.

The first question is:

> **Is Context + Artifact + Patch genuinely a better primitive for building agents?**

Everything else depends on this answer.

---

# 76. First Technical Prototype

The first prototype should be able to execute this:

```python
ctx = Context(resources=RuntimeResources(sources={...}))

question = ctx.create(Question(text="Why did costs increase?"))

ctx.create(ConfluencePageRef(...))
ctx.create(GitLabFileRef(...))
ctx.create(SpreadsheetRef(...))

runtime.run(ctx)
```

And produce:

```text
Context v0
    Question
    References

Context v1
    Documents

Context v2
    Evidence
    Claims

Context v3
    Calculations
    Findings

Context v4
    Verified Claims

Context v5
    Answer
```

Then:

```python
ctx.history()
```

should show the evolution.

---

# 77. The First Demo Should Be Extremely Concrete

Use one real problem:

> "Why did our infrastructure costs increase in Q2?"

Provide:

```text
GitLab:
  docs/
  merge requests
  commits

Confluence:
  architecture documentation

CSV:
  cloud costs

XLSX:
  infrastructure budget
```

The system should:

1. understand the question;
2. locate relevant sources;
3. lazily materialize only useful artifacts;
4. extract evidence;
5. calculate values from structured data;
6. form hypotheses;
7. verify claims;
8. produce an answer;
9. show provenance.

If this works elegantly, the architecture is validated.

---

# 78. Architectural North Star

The long-term architecture should look like:

```text
                         USER
                           │
                           ▼
                    ┌─────────────┐
                    │    CHAT     │
                    └──────┬──────┘
                           │
                           ▼
                    ┌─────────────┐
                    │   CONTEXT   │
                    └──────┬──────┘
                           │
        ┌──────────────────┼──────────────────┐
        │                  │                  │
        ▼                  ▼                  ▼
   REFERENCES          ARTIFACTS           EVENTS
        │                  │                  │
        │                  │                  ▼
        │                  │              SCHEDULER
        │                  │                  │
        ▼                  ▼                  ▼
    SOURCES            KNOWLEDGE          AGENTS
        │                  │                  │
        └──────────────────┼──────────────────┘
                           │
                           ▼
                         PATCH
                           │
                           ▼
                     CONTEXT v+1
                           │
                           └───────────────────↺
```

---

# 79. The Core Mental Model

When designing a new feature, think in this order:

```text
1. What Artifact does this represent?
2. What is its provenance?
3. Who can create it?
4. Who can modify it?
5. What makes it valid?
6. What events does its creation/update produce?
7. Which Agents can consume it?
8. Which Agents can produce derived artifacts?
9. What happens if the source changes?
10. How can we inspect and reproduce the result?
```

Do not start with:

```text
Which node should I add?
```

---

# 80. Final Constitution

The project follows these foundational principles:

### Principle 1

**State is primary. Execution is derived.**

### Principle 2

**Artifacts are first-class.**

### Principle 3

**References and materialized Artifacts are different things.**

### Principle 4

**External knowledge does not have to be embedded.**

### Principle 5

**Agents transform Context through Patches.**

### Principle 6

**Context is versioned.**

### Principle 7

**Derived information preserves provenance.**

### Principle 8

**Structured data remains structured.**

### Principle 9

**The LLM is a reasoning component, not the source of truth.**

### Principle 10

**Deterministic computation should remain deterministic.**

### Principle 11

**Uncertainty and contradictions are explicit state.**

### Principle 12

**Agents should react to state rather than require manually authored execution graphs.**

### Principle 13

**The framework may use graphs internally, but graphs are not the primary developer abstraction.**

### Principle 14

**Every important decision should be explainable through Context history, Artifacts, Events, and Patches.**

### Principle 15

**The simplest useful system is the goal; sophistication must emerge from the primitives rather than from framework ceremony.**

---

# 81. One-Sentence Definition

If the project needs a single sentence:

> **A framework for building agents as reactive, stateful processes that transform versioned, typed, provenance-aware Artifacts inside an evolving Context.**

And the shortest mental model is:

```text
                 AGENT
                   │
                   ▼
             ┌───────────┐
             │  CONTEXT  │
             └─────┬─────┘
                   │
                 PATCH
                   │
                   ▼
             ┌───────────┐
             │  CONTEXT' │
             └───────────┘
```

Everything else — tools, RAG, APIs, planners, schedulers, multi-agent execution, memory, verification, branching — is built around these primitives.

---

# Appendix — Implementation Status

State of the public `reactifact` codebase, aligned with this constitution (ver 0.3).
Verification: 620 tests (+2 skipped without `TEST_PG_DSN`); mypy (strict) and ruff clean.

| Area | Section(s) | Status |
|---|---|---|
| Context / Artifact / Patch / Revision (git-like) | §4, §12, §14 | implemented (create/update/delete/link, history, diff, checkout, snapshot); `Context` composes a `RelationGraph` and a `CommitLog` internally (extracted for testability, no API change) |
| Relations & provenance edges | §15, §33-§34, §36 | implemented (`Link`, `derived_from`, `supported_by`, `contradicted_by`) |
| Context views (token-budgeted projections) | §27, §28 | implemented (`context.view` + `tokens_estimate`); `TokenBudgetContextBuilder(min_keep={type: count})` reserves a costed type's top-ranked instances a slice of the budget before the shared greedy fill runs, so a high-volume type (many `Evidence`) can't crowd out a low-volume one that still has real content (the triggering `Question`) — complements `exempt_types`, which is for a type with no real content at all |
| Reference sources (filesystem / CSV / vector) | §7-§9, §74 P2 | implemented in core |
| GitLab / Confluence / S3 connectors | §74 P2 | domain examples, not core (planned as `examples/` connectors) |
| Agent contract (Produce / Consume containers) | §10-§13, §63 | implemented; `Consume(wakes=False)` reads a type as input without waking on it (the declarative alternative to a hand-synced `Agent.triggers=` override); `Consume(debounce=True)` collapses several same-generation events into one run, costing one against `Budget(max_runs=...)`; `reactifact.consume.CorrelatedConsume`/`JoinConsume`/`AbsentConsume` correlate across two artifact types by a shared key (join / absence-gate) instead of one type's own matching instances alone |
| Reactive runtime, events, budget | §21-§24, §58 | implemented (subscriptions, outcomes, replan); opt-in per-agent error isolation (`Runtime(isolate_errors=True, on_agent_error=...)`) — default stays fail-loud (§69) |
| Provider reliability (retries, HTTP client lifecycle) | §69 | implemented — `with_retry` (429/5xx/transport errors, exponential backoff, never on 4xx) on every provider's network call; `RuntimeResources.aclose()`, auto-closed per turn by `ChatAssistant` for a callable `resources=` |
| Tools / tool loop / HITL tool use | §46-§47, §60 | implemented (`tools`, `ToolUse`, `ToolUseHITL`) |
| HITL (approvals, questions) | §60 | implemented (`PendingQuestion`, `InterruptPatch`) |
| Knowledge chat: Evidence → Claim → Verification → Answer | §16-§19, §34-§36 | implemented (`examples/knowledge`, English) |
| Structured-data calculation | §29, §33, §67 | implemented (`CSVSource → Spreadsheet → Calculation`) |
| Confidence / contradictions as state | §35-§36 | implemented (deterministic, §67) |
| Idempotency (stable ids, create-or-refresh) | §42 | implemented — `effects.create_once(id=...)` folds the "already done" guard into the call; `effects.upsert(id=...)` names the create-or-refresh case explicitly |
| Staleness / invalidation from recorded reads | §43-§44 | implemented (`stale_artifacts`; reactive via `EventType.ARTIFACT_STALE`, not just polling) |
| Produce authoring — Effects (§24) | §12, §24 | implemented — `self.effects.create/update/link/ask`, the runtime compiles the slot into one atomic `Patch` (transport); two canonical authoring styles (subclass, `@produce` function), both taking exactly one argument, `call: ProduceCall` (`.context`/`.inputs`/`.event`/`.trigger`/`.effects` — replaced the earlier individually-recognized `(context, inputs, event=None)`/by-name-sniffed parameters, one discoverable object instead of a growing parameter list); `.trigger` (the artifact behind `.event`) is a guaranteed non-`None` live artifact when the produce also declares `reacts_to=(Type, …)` — which itself restricts *which* triggering event a produce runs on, for an agent whose several produces don't all care about the same one; `Produce(factory=...)` deprecated, `Agent.run()` override documented as a low-level escape hatch, not a third style |
| Conversation memory via views | §37-§38 | implemented (`context.view` based chat memory) |
| Turn lifecycle / honest fallbacks | §24, §59, §69 | implemented in demos (outcomes, linguistic fallbacks) |
| Branching (`context.branch()`) | §39-§40 | implemented — three-way `merge()` with `MergeConflict`, `BranchStore` over KV, CLI |
| Replay (§55) | §55 | implemented — `ReplayLLM` record/replay, state replay + `python -m reactifact replay` |
| Evaluation harness | §56 | implemented — `reactifact.eval`: multi-level metrics (evidence/claim/provenance/calc/answer/sources) over the final state |
| Security / access control | §57 | planned — no built-in authorization primitive; the host application is responsible for gating which produce/agent may create/update which artifact types (this includes the MCP server, §mcp: it exposes `Context` as read-only resources, but any connected tool-calling LLM can still invoke mutating `Tool`s) |
| Adaptive / uncertainty-driven scheduling | §26, §24 | implemented — hybrid scheduler (`reactifact.scheduler`, `examples/adaptive`: rule filters + deterministic rank + optional LLM tie-break + `rank_limit`); `relation_balance_metric` is the built-in uncertainty-driven `Metric` — ranks a candidate by supports/contradicts relation balance on its artifact (the structural signal `examples/medic_lab` used to compute by hand for reporting only, now generic and wired into `medic_lab`'s own `Runtime(scheduler=...)` so it drives execution order, not just the report) |
| Behavioral testing harness | §69 | implemented — `reactifact.testing` (`ScenarioLab`/`Scenario`, tool/resource fault injection, record/replay, `reactifact scenario` CLI) |
| MCP integration | — (post-constitution addition) | implemented — `reactifact.mcp`: `mcp_stdio_tools`/`mcp_http_tools` (HTTP with optional auth `headers=`) consume an external server's tools as ordinary `Tool`s; `create_mcp_server` exposes reactifact `Tool`s and, with `context=`, read-only `Context` resources, as an MCP server. Verified over the real protocol (in-memory transport) |

Demos shipped in the repo (not in the wheel):

- `examples/knowledge` — English multi-source chat: search → evidence → claim
  verification → answer, plus CSV calculation and a web dashboard.
- `examples/research` — English research agent that *goes to the web*
  (`WebSource`, live HTTP pages): lazy Reference → Artifact, verified claims,
  answer with URL provenance (§32, §77).
- `examples/medic-lab` — English **hypothesis laboratory** (§20, §36, §60):
  a question spawns competing `Hypothesis` artifacts, each investigated over an
  evidence pool in a per-hypothesis channel (`hypothesis_id`-tagged refs and
  facts), scored deterministically by support/contradiction, cross-checked for
  contradictions, and closed by a human steering pass that either deepens a
  hypothesis or produces an honest ranked report.
- `examples/devops` — English ops assistant: HITL tool agents, LLM tool router,
  run-trace dashboard with auth.
- `examples/repair` — budget-aware replanning demo. Its **chat and data are
  intentionally Russian** (a deliberate product choice, §68-adjacent); code and
  comments are English.

Plus canonical ports of classic agent-framework patterns (`reflection`,
`map_reduce`, `supervisor`, `summarize`, `time_travel`, `plan_execute`, each
runnable offline as `python -m examples.<name>.main`; see
[port-matrix](en/port-matrix.md)), `forklab` (branch/merge, §39-40), `ledger`
(reactive recompute, §42-44), and `adaptive` (hybrid scheduling) — fifteen
example applications total.

Roadmap direction: domain connectors as examples, the evidence graph is now in
the trace UI (§34, §54), an evaluation harness landed (§56), the testing
harness and MCP integration landed since (rows above), and stronger
adaptive scheduling.
