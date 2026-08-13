### Fixed

- **`func_visibility_changed` now stamps `Change.symbol_binding`.** A public
  export demoted to `HIDDEN` previously left `symbol_binding` unset, unlike
  the four removal kinds — so a `binding: weak` selector (`suppression.py`'s
  or `reclassify.py`'s ``ReclassifyRule``) matched none of these findings,
  forcing a project onto a kind-global `overrides:` entry instead of a
  symbol-scoped rule. The old side's real ELF linkage is now stamped the
  same way it already is for `func_removed`/`var_removed`/
  `func_deleted_elf_fallback`.

### Added

- **`scan --format json` now discloses active policy rules.** The always-on
  `scan --against` summary gains the optional `policy_overrides`/
  `policy_reclassify`/`policy_file` keys (mirroring `compare`'s own JSON
  report, `reporter._add_policy_overrides`), and each finding dict
  (`findings`/`additions`/`quality`/`suppressed`) gains an optional
  `reclassified_by` key naming which `reclassify:` rule actually decided its
  verdict. Previously a `scan --format json` reviewer saw a downgraded
  verdict with no way to tell which rule produced it (`SCAN_SCHEMA_VERSION`
  1.13 → 1.14).
