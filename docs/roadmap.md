# Roadmap

reactifact is pre-1.0 (`0.7.x`), one maintainer. This page is the honest
current state of "what's next" — not a wishlist. See [CHANGELOG.md](../CHANGELOG.md)
for what's already shipped, release by release.

## Now

- API is stabilizing around the primitives in the README's
  ["Core primitives"](../README.md#core-primitives) section
  (`Context`, `Artifact`, `Effects`, `Patch`, `Agent`, `Source`, `Provenance`).
  `0.5.0` already trimmed the public surface down to this set.
- MCP support (`reactifact.mcp`, shipped in `0.6.1`) is the newest primitive —
  hardening it (more transport coverage, more real-server testing) comes
  before adding new integration surfaces.

## Next

- **Stable 1.0** — freeze the public API surface, commit to semver
  guarantees, and close out breaking changes before they accumulate further
  (see the `### Breaking` entries already in `CHANGELOG.md` — the goal is
  for `1.0` to be the last one of those for a while). That surface has
  three stability tiers, not one:
  - **Core** (frozen hardest): `Context`/`Artifact`/`Effects`/`Patch`/
    `Agent`/`Source`/`Provenance`/HITL (`PendingQuestion`,
    `effects.ask`/`resume`), plus `RuntimeResources`'s own extension
    points — `context_builder` (`reactifact.context_builder`) and
    `verification_threshold` (`reactifact.verify`) count as core because
    they're config surface _on_ a core object (`RuntimeResources`), not a
    separable add-on; a breaking change there is a breaking change to
    `RuntimeResources` itself.
  - **"In the box"** (stable, evolves faster than core): `Tool`/`ToolUse`/
    `ToolUseHITL` (including the destructive-tool approval gate),
    `agent_tool.AgentAsTool`, `verify.Verify`'s metric surface (`core_metrics`
    may grow), MCP, tracing/observability, providers. Same semver
    discipline, but a lower bar for adding (not breaking) new capability
    between minors.
  - **Recipes** (`reactifact.recipes`, [docs/en/recipes.md](en/recipes.md)) —
    frozen at 1.0 too (every recipe there is a committed public API, not a
    demo), but explicitly the layer meant to keep growing fastest post-1.0:
    new recipes are additive, framework-internal moves stay off their
    public surface.

  This tiering isn't new policy so much as making explicit what the
  "Core primitives" section of the README already implied by omission —
  `Tool`, MCP, and recipes were never in that list either.

- **More `Source` integrations** — today's built-in sources are filesystem,
  CSV, embeddings, and `WebSource` (see the `research` example). The
  `Source` protocol (`reactifact/sources.py`) is intentionally small so this
  grows by adding new implementations, not by changing the abstraction:
  direct API sources, SQL, and keyword search are the concrete gaps between
  what's documented as "equally first-class" in the README and what actually
  ships today.

## Non-goals (for now)

Carried over from [docs/en/comparison.md](en/comparison.md#where-reactifact-is-not-the-right-choice) —
worth repeating here since a roadmap page is where people look for the
opposite promise:

- **No managed hosting / SaaS platform.** reactifact is a library; there is no
  hosted execution or UI for non-engineers planned.
- **No large pre-built agent/tool marketplace.** The `Source` and MCP
  abstractions stay small and composable rather than growing a plugin
  ecosystem to compete with LangGraph/CrewAI's integration count.

## Contributing to the direction

Roadmap changes and feature discussion happen in
[GitHub Discussions](https://github.com/bzdvdn/reactifact/discussions), not
silently in code. If something here looks wrong or missing, open one.
