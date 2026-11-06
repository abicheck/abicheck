### Changed

- **One owner for unobserved assurance dimensions.** The analysis-assurance
  gaps (template defaults, concepts, header-scope fallback, depth projection,
  unreadable sections, export-table reads) now register with
  `policy/analysis_assurance/_unobserved.py`, which `compute_analysis_assurance`
  iterates for notes and the `partial` rollup and `attach_analysis_assurance`
  iterates for coverage warnings. A structural test fails on an unregistered
  gap module, so a gap can no longer be silently read as `complete`. Emitted
  notes and statuses are unchanged.
