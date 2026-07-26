### Fixed

- **A BREAKING/API_BREAK verdict co-occurring with an evidence-coherence RISK
  finding no longer produces a confident SONAME/MAJOR recommendation.**
  `semver.recommend_release()` previously only checked `Verdict` and
  `DiffResult.evidence_tiers` (real binary evidence present/absent) before
  recommending a SONAME bump — it never consulted the two evidence-coherence
  cross-checks (AC-008 `compile_context_conflict`, AC-009
  `source_surface_dso_mismatch`) that already exist specifically to flag
  mis-scoped/inconsistent build or source evidence. A comparison could carry
  a `BREAKING` verdict backed by real ELF/DWARF evidence *and* a
  `compile_context_conflict`/`source_surface_dso_mismatch` finding — meaning
  the build/source context behind the analysis was known to be internally
  inconsistent — and still get an unqualified "bump your SONAME". Both kinds
  now downgrade the recommendation to `ReleaseRecommendationState.UNAVAILABLE`
  (`SonameAction.NOT_DETERMINED` for `BREAKING`) or `UNAVAILABLE` (for
  `API_BREAK`, previously always `REVIEW`), with a rationale naming the
  specific coherence finding(s). A `COMPATIBLE_WITH_RISK` verdict — which
  never makes a MAJOR/SONAME claim in the first place — is unaffected.
