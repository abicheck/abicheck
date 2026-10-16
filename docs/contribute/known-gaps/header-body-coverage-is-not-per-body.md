---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Header-pass body coverage is one blanket `header_inline_bodies` unit, not per body

Index: [Known gaps](../known-gaps.md)

**Status (updated 2026-10-14 at `54ab356ea`): PARTIAL, narrowed.** Recorded 2026-10-11 at `2a1ccb0c8` as OPEN: `model/source_graph_coverage.py` gave a header-only pass exactly one covered unit, `HEADER_INLINE_BODIES`, for the body-dependent kinds (`DECL_CALLS_DECL`, `DECL_REFERENCES_DECL`), and no fact recorded *which* function bodies were fully seen on each side.

## What is fixed

- **Per-declaration body fact.** `call_graph.collect_function_bodies` lists every function declaration whose body (a `CompoundStmt`) the header AST carries, using the call walker's own scope tracking and identity. Both projection paths (`project_header_graph_ast`, the streamed `project_header_graph_ast_file`), the bisected-parse merge and the projection cache carry it as `HeaderGraphAstProjection.function_bodies`. `seed_ast_graph` marks each already-seeded `decl://` node with the `header_body` attribute (`model.source_graph_coverage.HEADER_BODY_ATTR`); it lives in the existing free-form node `attrs`, so no stored-schema field changed.
- **Per-body judgement.** `source_graph_coverage.bodies_seen` reads that fact: every body for a confirmed build-integrated pass, the marked nodes for a clean header-only pass, and nothing for a narrowed, degraded or absent one. `body_scoped_closures` admits a body-dependent kind that neither side trusts project-wide only for edges whose source body was seen on **both** sides, and drops that kind's other edges from both closures. `_internal_dependency_findings` (`PUBLIC_API_INTERNAL_DEPENDENCY_ADDED`) uses it, so a call or reference added inside an inline function visible on both sides is now reported, and one whose body is out of line on either side is still declined.
- Regression tests: `tests/test_header_body_coverage.py` (every seen/unseen body shape for one entry, an exhaustive two-entry domain, non-call references, producer and cache round-trips).

## What remains

`false-clean` — Two readers still use the blanket unit:

- `pass_coverage_records` (and so `compare.edge_query`'s `EdgeEvidence.query` / `source_graph_covers`) still gives a header pass the single `HEADER_INLINE_BODIES` unit for the body kinds, so an edge query about one body answers `unknown` even when that body is marked seen. Fixing it needs per-declaration units in `CoverageRecord`'s scope vocabulary (today compile-unit paths), which is a coverage-model design change, not a reader change.
- `_call_reachability_findings` (`CALL_GRAPH_PUBLIC_ENTRY_REACHABILITY_CHANGED`) still requires whole-project `DECL_CALLS_DECL` coverage on both sides. Its transitive reachability count would need every body along each path seen on both sides.

The fact records presence only, not the body's digest; ADR-077 D5's digest would let a reader tell "body unchanged" from "body changed" without the edges, and is not needed by the readers above. Owner: [ADR-077](../adr/077-exposure-modes-and-change-sensitive-propagation.md) D5.
