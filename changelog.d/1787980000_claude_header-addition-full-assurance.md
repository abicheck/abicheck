### Fixed

- **A public-header addition no longer reduces analysis assurance, wherever
  the new header sorts.** `header_sequence` records declared-header order, and
  a header whose name sorted into the middle of the discovered public-header
  set (e.g. `pvxs/json.h` landing between `data.h` and `log.h`) was treated as
  an extraction-context divergence: first as a hard `ProfileMismatchError`, and
  then as a bounded mismatch that marked the `declaration` and `layout`
  dimensions unverified — which reads through to
  `analysis_assurance.status = "partial"` ("extraction contexts were not
  provably identical") and floors the exit code of any run configured with
  `assurance.require_complete`. A declared header's *content* is not part of
  `profile_fingerprint` at all, so the macro/pragma-leak hazard that reduction
  priced is the same one an edit to an existing header carries, and that is
  compared at full assurance by design. Scope-confirmed declared-header growth
  is now waived by the existing `header_sequence` carve-out regardless of
  placement. A reordered *existing* header, growth the declared surface does
  not confirm as new, and any other diverging profile field (compiler, target,
  language standard, macros, pass-through flags, include topology) still refuse
  the comparison exactly as before.
