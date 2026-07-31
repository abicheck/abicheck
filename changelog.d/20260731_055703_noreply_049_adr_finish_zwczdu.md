### Added

- **ADR-049 `contract=exports` is implemented in the shadow contract
  evaluator, backed by a new export-rooted evidence provider** —
  `abicheck/export_surface.py` (`compute_export_surface`) resolves a
  snapshot's ABI surface from the binary's *observed* export table (ELF
  `.dynsym`, the PE export directory, the Mach-O export trie) plus the
  closure over the raw record/enum/typedef graph. That is a different
  evidence domain than `surface.py`'s header-derived public surface, with no
  header-origin demotion anywhere: a private-header type reached from a real
  export is inside this contract, and an unexported public-header
  declaration is not. `evaluate_change_contract_relevance` now implements
  all three `ContractMode` values instead of raising `NotImplementedError`
  for `EXPORTS`, taking the new `exports_old`/`exports_new` surfaces —
  required for that mode rather than approximated from the header surface.
  Still shadow-only and not selectable from any command, so no verdict, exit
  code, or report changes, until ADR-049 Phase 6 exposes
  `--contract public|exports|all`.
