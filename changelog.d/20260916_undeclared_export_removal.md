### Fixed

- Removing an export that no public header declares is breaking again. The
  public-surface seeding that moves *property* churn on an undocumented export
  into the filtered audit ledger was also demoting the symbol's
  **disappearance**, which turned
  `catalog/cases/case182_accidental_export_removed_still_breaking` from
  BREAKING (exit 4) into a clean exit 0. Absence of a declaration proves the
  symbol was never part of the documented contract; it does not prove nobody
  depends on it — a consumer can bind it through `dlsym()`, a leaked internal
  header, or a hand-written prototype, and fails at lookup time once it is
  gone. The exemption is scoped to the export-table-only symbols the seeding
  itself added, so a privately *declared* symbol's removal keeps its existing
  treatment and the noise reduction is unaffected.
