### Fixed

- A directory/package `compare` now narrows a release-level
  `public_not_exported` finding the same way the per-member check does: a
  declaration outside the run's declared `-H` surface (reached only through
  `#include` and an `-I` root, e.g. a sibling library's header in a shared
  include tree) is reported at LOW confidence and worded as not established,
  instead of at full strength. The release JSON's
  `public_surface_reconciliation.missing_exports[]` entries gain a
  `confidence` field, and obligations in `missing_from_bundle` gain
  `outside_declared_surface` (release schema 1.13). No verdict or exit code
  changes.
