// G32 Phase 0 "scope drift" fixture, new side (see ../README.md, Fixture 4).
// The one extra TU that makes new/ a superset of old/ -- a manifest/CLI-flag
// drift between two extraction runs, not a real API change. Used to assert
// the Phase A comparability gate hard-fails `not_comparable` on this pair
// by default, and that the `--diagnostic-comparison` opt-in downgrades it
// to a tentative, assurance: "none"-stamped diff instead.
void widget_extra_feature(void);
