<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **ADR-049 Phase 3 shadow evaluator: three more loader/deployment-packaging
  findings are now `NOT_APPLICABLE`** (`contract_evaluation.py`; opt-in via
  `compare(..., contract_evaluation=True)`; no default-path behavior
  change): a PE import's eager/delay-load mode
  (`pe_import_load_mode_changed`, a link-time property of the import table
  entry, not a change to the imported function itself), ELF's SysV/GNU
  symbol-hash style (`hash_style_removed`, a synthetic `.hash`/`.gnu.hash`
  subject), and a musllinux-tagged wheel actually depending on a
  glibc-versioned symbol (`musllinux_glibc_dependency_detected`, a synthetic
  `symbol="<platform-baseline>"` sentinel) were all missing from
  `_NOT_APPLICABLE_KIND_SLUGS`, so each fell through to ordinary
  header-surface classification and came back `UNKNOWN_UNRESOLVED`
  (`PUBLIC` mode) or `IN_CONTRACT` (`ALL` mode) instead of the correct
  non-entity verdict.
