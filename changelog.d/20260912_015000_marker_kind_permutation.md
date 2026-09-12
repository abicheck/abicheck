### Fixed

- A pair of closure/anonymous-tag markers of *different* kinds each moving to
  the other's declaring file is reported as `declaration_moved` again, instead
  of being absorbed by the reordered-markers rule and reported as
  `declaration_identity_reconciled`.
