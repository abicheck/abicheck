### Fixed

- **`bases`/`virtual_bases`/`is_va_list` findings no longer fabricate a
  change from an incomplete evidence gap.** `_diff_type_bases` (base-class
  add/remove/reorder/virtual-toggle) and `param_va_list_changes`
  (`PARAM_BECAME_VA_LIST`/`PARAM_LOST_VA_LIST`) previously read each side's
  `Fact[...]` through `resolved_fact_value(fact, [])`/an equivalent
  present-or-default collapse, so a `NOT_COLLECTED`/`FAILED`/`UNSUPPORTED`
  fact on either side (e.g. a shallower evidence depth, or a per-parameter
  extraction failure) silently read as "this side has no bases"/"not
  `va_list`" — fabricating a finding against real evidence on the other
  side purely from the capture gap. Both detectors now gate each comparison
  through a new shared primitive, `abicheck.compare.fact_comparison.compare_facts`
  (ADR-063 Phase 5B's first `FactStatus`-aware detector cohort), and decline
  to compare when either side's evidence is incomplete instead. A pair with
  fully complete evidence on both sides (`PRESENT`, confirmed-empty/
  confirmed-`False` included) compares exactly as before. `bases`/
  `virtual_bases` additionally decline on a `PARTIAL` fact on either side
  (a base absent from a partially-covered list may simply live in the
  uncovered part, so treating it as complete could fabricate a finding the
  same way an incomplete fact could); `is_va_list`, a single-parameter bool
  rather than a list with an unobserved remainder, still compares normally
  under `PARTIAL`.
