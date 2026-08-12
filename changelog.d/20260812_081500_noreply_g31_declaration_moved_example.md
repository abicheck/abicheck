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
- **A third review round caught that the load-bearing dependency edge was
  fabricated after `build_source_graph()` already returned**, leaving the
  committed fixture's `graph_id`/`coverage.call_edges` inconsistent with its
  own serialized edges. Fixed by feeding the edge through
  `SourceAbiSurface.source_edges` (the real L4 wire format
  `build_source_graph()`'s own `fold_source_edges()` folds), and fixed the
  README's v1/v2 diff table, which still showed the caller invoking the
  helper on both sides.
- **A fourth review round caught that the public caller was an ordinary
  out-of-line function**, whose internal calls are compiled into the
  library's own binary only, never into a consumer's — risking a
  production false positive in `graph_reconcile`'s public-reachability
  gate. Fixed by making the caller inline (`SourceAbiSurface.
  reachable_inline_bodies`), so its call is genuinely consumer-visible
  under any reachability notion, independent of which predicate a given
  detector uses.
- **A fifth review round caught that the second round's fix (restricting
  the call edge to the new side) had gone one step too conservative**: the
  hand-built old graph never marked its call-graph extractor pass as having
  run, so `source_graph_findings._dependency_kinds_covered` read the old
  side's zero calls as "never collected" rather than "collected, confirmed
  zero" — silently suppressing a comparison the case's own narrative
  claims should be confirmed, rather than genuinely earning
  `public_api_internal_dependency_added`. Fixed by marking
  `extractor_passes["call_graph"] = True` on both graphs, re-finalizing
  (`graph_id` is unaffected, since it hashes only nodes/edges). This time
  `public_api_internal_dependency_added` fires as a genuinely new
  (confirmed zero → one) dependency, not the raw-node-id artifact the
  second round's finding was about — `case196` now reproduces both
  `declaration_moved` and `public_api_internal_dependency_added`.
- **A sixth review round caught that the fifth round's fix hand-forced
  `extractor_passes["call_graph"] = True` directly**, bypassing the real
  production certification gate (`source_graph.
  mark_source_edges_extractor_coverage()`) entirely — run against this
  exact surface data, that real helper would instead *degrade* the pass
  (an unconfirmed `source_edges` rollup, not a recognized full-walk
  producer), so a regression in real coverage propagation could never have
  failed this fixture. Fixed by populating each surface's
  `coverage.fact_set`/`fact_family_states` to name the real full-walk
  producer and calling the real certification helper instead of
  hand-setting the flag. `public_api_internal_dependency_added` still
  fires, now genuinely earned through the production coverage-propagation
  path this fixture is meant to exercise.
- **A seventh review round (CodeRabbit) caught a real linkability gap**:
  `demo::process` being inline (fourth round's fix) means its body —
  including its call to `detail::helper` — is emitted into every
  *consumer's own* translation unit, not just the library's. An ordinary
  out-of-line, non-exported `detail::helper` would leave that
  consumer-emitted call as an unresolved external symbol reference at link
  time — a real, consumer-visible link failure, not the purely internal,
  risk-only change this case's canonical verdict claims. Fixed by making
  `detail::helper` inline too (an ordinary header-only-library pattern: a
  private "detail" header providing an inline implementation, transitively
  included by the public header), so its own body is also emitted into the
  same consumer TU and the call resolves entirely locally with no external
  symbol needed — a real consumer program built against this exact
  scenario now genuinely links and runs cleanly, which is what makes
  `COMPATIBLE_WITH_RISK` correct rather than a modeling artifact.
