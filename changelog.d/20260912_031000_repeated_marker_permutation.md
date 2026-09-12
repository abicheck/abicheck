### Fixed

- Two closure/anonymous-tag markers declared in the *same* header that swap
  places are reported as `declaration_identity_reconciled` again, rather than
  as a coordinate-only shift claiming no material identity change.
