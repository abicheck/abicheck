### Added

- **Two new L5 reconciliation outcomes, `declaration_identity_unchanged`
  (COMPATIBLE) and `declaration_identity_reconciled_unresolved`
  (COMPATIBLE_WITH_RISK).** `declaration_identity_reconciled` was the
  fall-through for every graph-reconciled pair that was neither renamed,
  moved, nor a coordinate-only shift, while its own catalog `impact` text
  claims both the qualified name and the declaring-file evidence changed
  together. Measured on a oneTBB header graph, all 234 reconciled calls
  were pairs whose qualified name, `source_relative` and two-sided
  declaring file were byte-identical, so the claim was false for the whole
  population. `declaration_identity_reconciled` now means only the
  combined rename-and-move; a pair where no identity dimension differs and
  the classifier's dimensions are exhaustive for its kind (a type) reports
  `declaration_identity_unchanged`, and a pair with a real but
  unattributable difference (a `source_decl`, an ambiguous marker
  basename, a template-argument permutation, a differing signature tail)
  reports `declaration_identity_reconciled_unresolved`, keeping its RISK
  tier. Catalog case197's expected kind moves accordingly; its verdict is
  unchanged.
