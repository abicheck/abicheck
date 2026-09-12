### Fixed

- `compare` no longer reports degraded schema-staleness assurance for two
  snapshots that differ only in precision the codec rounds away (for example
  a sub-millisecond `LayerCoverage.elapsed_s`), which made
  `--require-complete-analysis` fail on a pair whose canonical content
  digests are identical.
