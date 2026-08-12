<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **`reclassify:` policy rules can now select on ELF symbol `binding` too.**
  `ReclassifyRule` reuses `Suppression`'s selector grammar (`symbol`,
  `symbol_pattern`, `namespace`, ...), but the new `binding` selector
  (see the entry above) was wired onto `Suppression` without a matching
  `reclassify:` update, so a rule like `{kind: func_removed, binding: weak,
  to: risk}` was rejected as an unknown key. `binding` is now accepted in
  `reclassify:` entries the same way, including in policy audit output
  (`describe()`/`to_report_dict()`) — conjunctive-only, same caveat as the
  `suppress:` selector it mirrors.
