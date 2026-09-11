<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **Graph reconciliation no longer mislabels pure coordinate churn (e.g. a
  closure's shifting `:line:col`) as `declaration_identity_reconciled`** —
  `graph_reconcile._classify_outcome` used `OUTCOME_RECONCILED` as a
  catch-all for both "name and location both changed" and "neither
  changed", so a pair whose qualified name only differed by an embedded
  source coordinate (normalizing equal via `closure_location_free_identity`)
  was rendered with prose asserting the opposite of what happened and
  inherited RISK severity via `ChangeKind.DECLARATION_IDENTITY_RECONCILED`.
  That specific case — the raw qualified name provably differed but
  normalized away, with the declaring file also unchanged — is now its own
  outcome, `declaration_coordinates_shifted`, reported at `COMPATIBLE`
  severity since the reconciliation itself proves nothing materially
  changed. A pair whose raw qualified name and file were already identical
  (e.g. a private inline function whose signature — and therefore mangled
  name — changed while its name/header stayed fixed) still reports
  `declaration_identity_reconciled`, per ADR-048's original intent for a
  match with "no clean rename/move split" — an unchanged name/file proves
  nothing about whatever else may have changed.

