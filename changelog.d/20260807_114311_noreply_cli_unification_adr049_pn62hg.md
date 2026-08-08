<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **`aggregate`'s `finding_matrix` now carries each affected profile's own
  ADR-049 contract decision** (CLI-audit P1, aggregate semantic matrix,
  schema `1.4`): `scope`/`affected_profiles` answer *whether* a profile has
  a finding, not *why* compatibility policy did or didn't act on it — two
  profiles compared under different `--contract` domains can report the
  same finding while one's evidence proves it's `IN_CONTRACT` (gating) and
  the other's evidence leaves it `UNKNOWN_UNRESOLVED` (not gating), which
  `scope: all_profiles` alone would flatten into "uniformly understood".
  When at least one affected profile ran `--contract-evaluation`, its
  `finding_matrix` entry gains a `profile_contract` array — one entry per
  affected profile, each with that profile's own `contract_relevance`,
  `compatibility_evaluation_status`, `compatibility_decision`, and
  `gate_contribution`, read back verbatim from its own report. Purely
  additive and inert for any comparison that never evaluated a contract —
  the field is omitted entirely, so an ADR-049-unaware CI matrix's
  `finding_matrix` renders exactly as before.
