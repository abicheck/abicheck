### Changed

- **`CompareRequest` gained a `collapse_versioned_symbols` field**
  (`abicheck.api_types`), closing a real gap a field-by-field audit of
  `ScanRequest`/`ScanResult` against `CompareRequest`/`CompareResult`
  found (ADR-055 amendment; plan
  `docs/contribute/plans/one-comparison-product.md` Phase 4 commit 2):
  `checker.compare()`/`compare_snapshots()` have accepted this parameter
  since G15, and `ScanRequest` could already reach it, but `CompareRequest`
  had no field for it at all. Forwarded through
  `service_compare_pipeline.classify_compare_pair`; `False` by default, so
  every pre-existing request is unchanged. The rest of `ScanRequest`'s
  fields already have a real `CompareRequest`/`InputSpec` equivalent (no
  new field needed) or are genuine, still-open scan-only gaps (the
  `budget`/exit-5 axis, the `--crosscheck KEY=error` per-check
  selection/severity syntax, the Bazel build-query axis, `risk_rules_path`,
  the `--artifact-set` bundle allow-list, `--build-target`, and the scan
  JSON baseline summary's truncation cap) left on `ScanRequest`/`ScanResult`
  rather than force-absorbed — see ADR-055's 2026-09-08 progress note for
  the full field-by-field accounting.
