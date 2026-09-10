### Fixed

- **The directory/package `compare` release JSON envelope had no schema-
  version field at all**, so a consumer could not distinguish a release
  document written before the `compatible_additions` semantic correction
  (additions only, excluding `quality_issues` — see the scalar report's own
  `report_schema_version` 4.0 correction) from one written after it; both
  used the identical per-library field name under two different meanings
  with nothing to signal which. Added `release_schema_version` (a new
  top-level string, `RELEASE_SCHEMA_VERSION = "1.0"` in `abicheck.schemas`,
  registered in the `schemas.current()` lookup facade alongside `compare`/
  `scan`/`aggregate`/etc.) to every release JSON document going forward.
  Purely additive — no existing consumer reads or expects this key, so
  nothing that already worked can break; a release document with no such
  key predates the correction and may carry either meaning, with no
  reliable way to tell from the payload alone.
