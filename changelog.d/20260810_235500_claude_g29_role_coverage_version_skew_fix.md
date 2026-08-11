<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`PUBLIC_API_INTERNAL_DEPENDENCY_ADDED` false positive on a source-graph
  collector upgrade** (G29, Codex review on PR #712): comparing an "old"
  persisted graph collected before a `type_graph.py` role was added (e.g.
  G29 Phase 5 item 5's `template_param`/`default_template_arg`/
  `enum_underlying`) against a "new" graph collected after could report a
  brand-new dependency edge riding that role as a real ABI-surface change,
  even with no source change at all. `_common_dependency_edge_kinds()`
  trusted an entire edge-kind family (`TYPE_HAS_FIELD_TYPE`/`DECL_HAS_TYPE`)
  once both sides confirmed the coarse `extractor_passes["type_graph"]`
  flag, with no awareness that a role *within* that kind might not have
  existed in the older collector's own producer code — and
  `role_pass_covered()` (ADR-046 D3's per-role coverage matrix, added for
  exactly this purpose) was never consulted by any production comparison
  path, only ever stamped. Fixed with a new `_role_coverage_disagrees()`
  check: a kind is now only trusted when both sides' `extractor_passes`/
  `narrowed_passes` agree, role key by role key, on which
  `ROLE_COVERAGE_MATRIX` roles were actually examined — checked directly,
  with no family-flag fallback, since the fallback is exactly what let a
  pre-upgrade graph's absent role key silently read as "covered." Applied
  as a final, monotonic (subtraction-only) filter over
  `_common_dependency_edge_kinds()`'s existing result, so it can only ever
  *remove* a kind neither the whole-family-widening nor per-kind-fallback
  path would otherwise have admitted, never add a false exclusion of its
  own, and is a no-op for the overwhelming common case where both sides
  were collected by the same abicheck version. A second review round found
  the same false positive still reachable through `header_graph.py`'s
  no-build header-only pass (ADR-041 header-only-graph addendum): it reuses
  the identical `type_graph.parse_clang_ast_types()` walker but never
  stamped a `ROLE_COVERAGE_MATRIX` key at all, so two header-only-collected
  graphs compared before/after the same abicheck upgrade read as vacuous
  agreement (both sides permanently absent) rather than "coverage unknown."
  `build_header_only_graph` now stamps role coverage under its own
  `header_type_graph` pass alias, and `_role_coverage_disagrees()` checks
  both the build-integrated and header-only key for each side.
