### Fixed

- **Action `scan` mode's `public-header-dir` forwarding could parse the
  `--against` baseline binary through the candidate's own header tree.**
  `action/run.sh` forwards `public-header-dir` a second time as a bare
  (unsided) `-H` root so scan's own header extraction matches a fresh
  `dump`'s. For an audit-only scan or a `new-library-set` audit there is
  only one side, so bare is correct — but for a scalar `scan --against`
  comparison, a bare `-H` root is side-*both* (ADR-040 L1), and
  `_resolve_baseline_header_scope()` then reads the candidate's
  `public-header-dir` tree into the *baseline* side too, even when an
  explicit `old-header` was also given. This could hide a declaration the
  candidate removed (still reachable via its own tree) or fabricate a
  difference from a candidate-only header the baseline never had. Fixed by
  forwarding it as `-H new=...` (sided) for a scalar scan with a resolved
  baseline, keeping the bare form for audit-only and `--artifact-set` scans.
- **`estimate_scan()`'s per-layer `CostEstimate` rows didn't reflect
  `ScanRequest.build_targets` scoping — only `scan --dry-run`'s CLI
  renderer did.** A Python-API caller constructing
  `ScanRequest(build_targets=...)` directly and reading
  `estimate_scan()`/`ScanResult.estimate` saw workspace-wide TU counts with
  no signal that the real run's Bazel collection scopes to the requested
  root target(s) and typically touches fewer TUs (a pre-captured Bazel
  aquery/cquery jsonproto is never filtered by `targets` — `BazelAdapter`
  only scopes a *live* query). Fixed by baking the same caveat into each
  affected row's own `note` (`L3_build`, `L4_source_abi`, `L5_source_graph`)
  so every API caller sees it, not only the CLI's rendered dry-run text.
