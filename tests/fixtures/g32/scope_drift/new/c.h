// G32 Phase 0 "scope drift" fixture, new side (see ../README.md, Fixture 4).
// The one extra TU that makes new/ a superset of old/ -- a manifest/CLI-flag
// drift between two extraction runs, not a real API change. Used (via
// compute_extraction_contract/check_contracts_comparable/compare(), not the
// CLI -- see ../README.md) to assert the Phase A comparability gate
// hard-fails ScopeMismatchError on this pair by default, and that
// compare(diagnostic_comparison=True) downgrades it to a tentative,
// assurance: "none"-stamped diff instead.
void widget_extra_feature(void);
