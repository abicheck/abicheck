<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **Graph reconciliation no longer mislabels coordinate-only shifts as
  `declaration_identity_reconciled`** — `graph_reconcile._classify_outcome`
  used `OUTCOME_RECONCILED` as a catch-all for both "name and location both
  changed" and "neither changed" (pure `:line:col` coordinate churn), so a
  reconciled pair whose identity was actually unchanged after
  `closure_location_free_identity` normalization was rendered with prose
  asserting the opposite of what happened and inherited RISK severity via
  `ChangeKind.DECLARATION_IDENTITY_RECONCILED`. The "neither changed" case
  is now its own outcome (`declaration_coordinates_shifted`), reported at
  `COMPATIBLE` severity since the model's own normalized identity says the
  declaration did not change at all; `declaration_identity_reconciled` is
  now reserved for the genuine "both name and location changed" case.

