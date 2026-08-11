<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **A pre-schema-v20 direct-clang snapshot's non-literal `constant_changed`
  findings could be fabricated wholesale** (`diff_symbols._diff_constants`):
  `dumper_clang.py`'s `parse_constants()` calls the same
  `_initializer_value()`/`_expr_fingerprint()` machinery as
  `Param.default`/`TypeField.default`, so a compound constant expression's
  value is a structural fingerprint (`"expr:" + sha256(...)[:16]`) whose
  exact algorithm was stabilized at schema v20 — the same instability
  `clang_field_initializer_facts_reliable`/
  `default_value_fingerprint_comparison_unreliable` already guard for the
  other two fact kinds. `_diff_constants` never consulted that guard at
  all, so comparing a pre-v20 clang-producer baseline against a fresh dump
  of *unchanged* headers could report every non-literal constant as
  `CONSTANT_CHANGED` purely from the fingerprint algorithm change — measured
  against a real corpus, 173 of 440 findings in one v18-vs-v24 comparison
  were exactly this (one constant's stale, collision-prone fingerprint
  shared by 36 unrelated constants). Added
  `constant_value_fingerprint_comparison_unreliable`
  (`diff_default_value_reliability.py`) and wired it into
  `_diff_constants`'s `CONSTANT_CHANGED` branch — scoped to `ast_producer
  not in ("castxml", "hybrid")` rather than the broader `!= "castxml"`
  check the default-value case uses, since `dumper_hybrid.merge_snapshots`
  keeps `constants` verbatim from its castxml base and never takes a
  hybrid merge's clang-only-append path the way `TypeField.default` can.
  Deliberately not an exact `== "clang"` match either: `ast_producer`
  itself wasn't tracked before schema v10, eight versions before this
  fingerprint risk even existed, so a snapshot straddling that boundary
  reads `ast_producer=None` — treated as possibly-clang, not excluded,
  mirroring `default_value_representation_unreliable`'s own handling of
  the identical gap.
