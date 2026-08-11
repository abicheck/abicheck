<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **Version-skew role-coverage disagreement discarded an entire dependency
  edge kind, not just the disputed role** (G29 Phase 5 item 5, Codex
  review): `_common_dependency_edge_kinds()`'s role-coverage filter
  (added earlier this PR to stop a collector upgrade — e.g. G29 Phase 5
  item 5's `enum_underlying`/`template_param`/`default_template_arg`
  roles — from manufacturing a false `PUBLIC_API_INTERNAL_DEPENDENCY_ADDED`)
  discarded the *whole* kind (`TYPE_HAS_FIELD_TYPE`/`DECL_HAS_TYPE`) from
  the trusted set the moment **any one** of its several
  `ROLE_COVERAGE_MATRIX` roles disagreed between old and new — even when
  both sides fully agreed on every *other* role sharing that kind
  (`field`/`alias`/`var`/`return`/`param`). A version-skew comparison
  where OLD predates the newly-added roles but agrees with NEW on the
  established ones then silently missed a genuine new `field`- or
  `alias`-role dependency, purely because of an unrelated role
  disagreement. Fixed by computing the disagreeing roles themselves
  (`_untrusted_dependency_roles()`) and excluding only their own edges
  from the version-diff closure, leaving every role both sides agree on
  fully trusted.
