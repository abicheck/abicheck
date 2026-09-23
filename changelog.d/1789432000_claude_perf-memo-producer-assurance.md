### Fixed

- A `--diagnostic-comparison` run across two header-AST producers (castxml
  vs. clang) no longer reports `comparability_assurance.declaration`/`source`
  as `trusted`. The producer is now recognised as a declaration/source-level
  difference, not only the layout/runtime one its `compiler_family`/
  `compiler_version` fields imply.

### Performance

- `depth_aware_bare_name` is memoized (4.2M calls over 1,682 distinct inputs,
  ~40 s, on a oneDAL comparison).
- `directly_referenced_stdlib_types` is reused within one detector pass
  (`abicheck/compare/detection_memo.py`) instead of being recomputed by every
  detector that asks (34 calls over 4 distinct inputs, ~22 s).
