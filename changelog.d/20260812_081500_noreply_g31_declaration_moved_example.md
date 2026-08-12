### Added

- **`declaration_moved` (G31 Phase B, ADR-048) gained example-catalog
  coverage** — `examples/case196_header_graph_move_reconciled/` demonstrates
  a real, production-reachable path to this reconciliation outcome (a
  declaration whose signature changes, moving its mangled name, in the same
  release its header moves — the qualified-name alias tier still pairs the
  two nodes, and the reconciler correctly classifies the pair as
  `declaration_moved`). The fixture is built by running real
  `SourceEntity`/`BuildEvidence` facts through the actual production fold
  (`source_graph.build_source_graph()`), not hand-assembled graph node ids.
  `graph_reconcile.py`'s own module docstring now documents which move
  shapes are reachable through a real evidence producer today (a compound
  move-plus-identity-changing edit) and which are not yet (a pure move with
  an unchanged signature).

### Fixed

- **`case196_header_graph_move_reconciled`'s canonical verdict was wrong** —
  a review round caught that the fixture's identity-perturbing edit landed
  on a *public* function, whose mangled-name-moving signature change is
  itself a real, independent BREAKING change (the old exported symbol
  disappears), contradicting `ground_truth.json`'s one-canonical-verdict
  invariant under a `COMPATIBLE_WITH_RISK` label. Redesigned so the edit
  lands on a **private** helper reached only through a public caller's
  dependency edge — `COMPATIBLE_WITH_RISK` is now the genuinely correct
  canonical verdict.
- **A follow-up review round caught the private-helper redesign's own new
  artifact**: adding the public caller's dependency edge to *both* the old
  and new graphs made `public_api_internal_dependency_added` fire on a
  raw-node-id mismatch rather than a genuinely new dependency (the
  detector compares raw target ids across versions, not
  graph-reconciliation-paired identities, so it read a pre-existing call
  relationship — reaching the helper's old mangled name — as newly added
  once the helper's node id moved). Fixed by restricting that call edge to
  the new side only, which still satisfies the reachability gate that keeps
  the reconciliation finding from being suppressed, without asserting a
  spurious new dependency. `case196` now reproduces a single RISK-tier L5
  `declaration_moved` finding.
