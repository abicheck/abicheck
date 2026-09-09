### Changed

- **Internal:** the engine primitives that survive `scan`'s eventual
  retirement (`docs/contribute/plans/one-comparison-product.md` Phase 6,
  §3 #34) are renamed off the `scan` identity ahead of the command's own
  deletion: `buildsource/crosscheck.py`/`crosscheck_base.py`/
  `crosscheck_coherence.py` → `cross_source_checks.py`/
  `cross_source_checks_base.py`/`cross_source_checks_coherence.py`,
  `buildsource/pattern_scan.py`/`preprocessor_scan.py` →
  `pattern_facts.py`/`preprocessor_facts.py`, and
  `buildsource/scan_levels.py` → `model/evidence_depth_levels.py`. No
  behavior, CLI flag, JSON schema, or public Python API change — internal
  module paths and a few internal symbol names only
  (`scan_files`/`PatternScanResult` → `find_pattern_facts`/
  `PatternFactsResult`; `run_preprocessor_scan`/`PreprocessorScanResult` →
  `collect_preprocessor_facts`/`PreprocessorFactsResult`).
