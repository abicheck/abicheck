<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`canonical_finding_id` is now backend-stable for `ATOMIC_QUALIFIER_CHANGED`,
  `CHAR8T_MIGRATION`, and `BIT_INT_WIDTH_CHANGED`.** These three type-slot
  detectors were missing from the canonical-id type-canonicalization
  allowlist, so CastXML's and Clang's differing type spellings (e.g.
  `char const*` vs. `char const *`) for the identical finding hashed to two
  different `canonical_finding_id` values — silently breaking the
  cross-backend `finding_id:` suppression contract for these three kinds.
