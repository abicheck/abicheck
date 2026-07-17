<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`--ast-frontend hybrid`** (G28 Phase 3 follow-ups): the ctor/dtor
  synthetic-key reconciliation only normalized the innermost scope
  component when stripping template arguments, so a nested class inside a
  template (e.g. `ns::Outer<int>::Inner`) still failed to reconcile against
  its real clang-mangled ctor/dtor — CastXML spells the enclosing scope in
  source form (`ns::Outer<int>`) while clang's Itanium-mangled scope
  encodes it as `ns::OuterIiE`. Every scope component is now normalized,
  not just the last one.
- A clang-only function (one CastXML never produced at all) could be
  misread by the `param_defaults` detector as having lost every default
  argument, since the clang header backend doesn't populate
  `Param.default`. Hybrid merges now record per-function provenance for
  this fact, and the detector skips a function pair on a hybrid snapshot
  when either side's defaults aren't confirmed CastXML-backed.
