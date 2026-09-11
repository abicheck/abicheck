### Fixed

- **`compare X X` on a stale-schema stored snapshot no longer reports
  `assurance.status: "partial"`.** `schema_staleness_status`'s self-diff
  early return tested Python object identity, so the same stored snapshot
  file loaded twice — `compare baseline.abi.json baseline.abi.json`, or a
  CI job re-checking an unchanged cached baseline — was reported as
  `schema_staleness_status: degraded`, flipping `assurance.status` from
  `complete` to `partial`. The return is widened to provable *content*
  identity (structural `AbiSnapshot` equality, evaluated only once a
  degraded fact has already been found, so a clean comparison pays
  nothing). Two snapshots that genuinely differ — including two
  extractions of one binary at different schema vintages — still report
  `degraded`.
