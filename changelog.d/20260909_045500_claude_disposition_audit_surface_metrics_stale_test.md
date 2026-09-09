<!--
A new scriv changelog fragment.

Uncomment the section that is right (remove the HTML comment wrapper).
-->
### Fixed

- `tests/test_disposition_audit_states.py::test_the_plugin_host_entry_point_closes_its_own_scope`
  had gone stale against `surface_metrics`'s own unconditional forcing at the
  shared Tier-2 `compare_snapshots` chokepoint (ADR-027 Phase 5): its fixture's
  two removed exports now always come with a `public_surface_shrank` roll-up
  finding too, so the ledger's `detected_total` is legitimately 3, not 2.
  Found by a full-suite run; no production behavior changed.
