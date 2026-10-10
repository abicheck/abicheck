### Fixed

- **A stored package produced under different extractor/resolver semantics is now reported** — packages written by this build record `EXTRACTOR_GENERATION`/`RESOLVER_GENERATION` (ADR-062 D2), both package readers state this build's generations to `check_reader_compatibility`, and a `compare` run that loads a drifted `ProjectSnapshot` package adds a `coverage_warnings` notice naming the side, axis and both generations. Informational only: the verdict and exit code are unchanged, and a legacy package with no recorded generation reads as unknown, not drifted.
