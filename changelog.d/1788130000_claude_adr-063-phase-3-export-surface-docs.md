### Documentation

- **Fixed stale `export_surface.py` docstrings left over from ADR-063 Phase
  3's public-surface migration.** Several module/class/function docstrings
  still claimed the shared type-closure walk performs "a real
  `AbiSnapshot.surface_graph` traversal" and pointed Sphinx cross-references
  at `abicheck.surface.*` symbols that had already moved to
  `abicheck.policy.public_surface`/`abicheck.policy.public_surface_closure`
  (or were deleted from `surface.py` entirely). Both misstatements
  contradicted ADR-063's own recorded final design — the closure walk
  deliberately never reads `AbiSnapshot.surface_graph`/`GraphNode.attrs`,
  per three review rounds documented in `docs/contribute/known-gaps.md` —
  so the docstrings now describe what actually ships. No behavior change.
