# Roadmap

reactifact is pre-1.0, one maintainer. This page is the honest current state of
"what's next" — not a wishlist. See
[CHANGELOG.md](https://github.com/bzdvdn/reactifact/blob/master/CHANGELOG.md)
for what's already shipped, release by release.

## Now

- **Finish the Step-era API.** The reactive core is being consolidated around
  `Step`/`@step`, step-owned `consumes`/`produces`, explicit `effects:
  Effects`, typed inputs (`Artifact[T]`, `list[Artifact[T]]`,
  `Annotated[T, When(...)]`, `Latest[T]`) and `Agent.steps`. The goal is one
  obvious way to write a unit of work.
- **Content-addressed artifact state.** `Artifact` is a frozen handle over an
  append-only `ArtifactStore`; `Context.artifact_version` /
  `version_history` / `find_versions` are canonical, persistence is v2
  (`blobs` + version index + commit `data_ref`), and compaction is
  authoritative. Remaining work is closing compatibility edges and docs.
- **Keep the example set small.** The repo keeps the reference applications
  (`devops`, `knowledge`, `forklab`, `repair`, `medic_lab`, `fintech_audit`,
  `starter_app`, `chat`) instead of maintaining every historical pattern demo.
- **MCP hardening** (more transport coverage, more real-server testing) before
  adding new integration surfaces.

## Next

- **Stable 1.0** — freeze the public API surface, commit to semver guarantees,
  and close out breaking changes before they accumulate further. The surface
  has three stability tiers:
  - **Core** (frozen hardest): `Context`/`Artifact` (frozen handle + version
    store)/`Effects`/`Patch`/`Step`/`Agent`/`Source`/`Provenance`/HITL
    (`PendingQuestion`, `effects.ask`/`resume`), plus `RuntimeResources`'s own
    extension points — `context_builder` (`reactifact.context_builder`) and
    `verification_threshold` (`reactifact.verify`) count as core because
    they're config surface _on_ a core object, not a separable add-on.
  - **"In the box"** (stable, evolves faster than core): `Tool`/`ToolUse`/
    `ToolUseHITL` (including the destructive-tool approval gate),
    `native_tool_use`, `agent_tool.AgentAsTool`, `verify.Verify`'s metric
    surface, MCP, tracing/observability, providers. Same semver discipline, a
    lower bar for additive capability.
  - **Recipes** (`reactifact.recipes`, [docs/en/recipes.md](en/recipes.md)) —
    frozen at 1.0, explicitly the layer meant to keep growing fastest post-1.0;
    new recipes are additive, framework-internal moves stay off their public
    surface.

- **More `Source` integrations** — today's built-in sources are filesystem,
  CSV, embeddings and `WebSource` (see the `knowledge` example). The `Source`
  protocol (`reactifact/sources.py`) is intentionally small so this grows by
  adding implementations, not by changing the abstraction: direct API sources,
  SQL and keyword search are the concrete gaps.

## Non-goals (for now)

Carried over from
[docs/en/comparison.md](en/comparison.md#where-reactifact-is-not-the-right-choice)
— worth repeating here since a roadmap page is where people look for the
opposite promise:

- **No managed hosting / SaaS platform.** reactifact is a library; there is no
  hosted execution or UI for non-engineers planned.
- **No large pre-built agent/tool marketplace.** The `Source` and MCP
  abstractions stay small and composable rather than growing a plugin
  ecosystem to compete on integration count.

## Contributing to the direction

Roadmap changes and feature discussion happen in
[GitHub Discussions](https://github.com/bzdvdn/reactifact/discussions), not
silently in code. If something here looks wrong or missing, open one.
