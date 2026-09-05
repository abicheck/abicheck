### Fixed

- **Release fan-out gap-fills on top of ADR-065 S2.** A directory/package
  `compare`'s stranded-library resolver (`--bundle-facts-out`'s degrade-on-
  failure fallback) now marks its snapshot `elf_only_mode=True`, so a later
  comparison against it sees the same reduced evidence tier every other
  ELF-only snapshot declares instead of silently reading it as fully-dumped.
  `actions/check-target`'s `_classify_verdict` now also recognizes a
  release/bundle fan-out's `run_outcome.operational ==
  "no_comparison_completed"` outcome (ADR-065 D7), so `gate-mode:
  advisory`/`deferred` no longer misreads a release that completed zero
  comparisons as an ordinary clean compatibility pass.

