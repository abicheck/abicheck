### Added

- **`compare` now runs the compiler-free lexical ABI-risk pre-scan and the S2
  preprocessor pre-scan automatically, on both sides** — the last two of
  `scan`'s scan-only capabilities (`docs/contribute/plans/
  one-comparison-product.md` §3 rows 6/8, Phase 2b). No new flag: both are
  evidence-gated and always run, attaching each side's result to the JSON
  report as new, additive `pattern_prescan`/`preprocessor_prescan` objects
  (report schema 3.12) — advisory only, never folded into `changes`, the
  verdict, or the exit code.
