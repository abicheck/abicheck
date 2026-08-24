<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **G38 bundle analysis: signature-change promotion now honors ADR-049
  contract-evaluation status, and a real caching bug is fixed.**
  `diff_change_is_breaking` previously reclassified a change's raw kind
  regardless of whether compatibility policy actually scored it — under
  `compare --contract ...`, a finding outside the selected contract's
  scope is stamped `compatibility_evaluation_status=NOT_EVALUATED` and
  stays in `diff.changes`, but is excluded from the per-library
  verdict/exit code. Promoting such a finding to a bundle-level BREAKING
  finding contradicted that already-scored result. Fixed by checking
  `contract_gating.is_evaluated(change)` first (defaults `True` for an
  unstamped finding, so a run with no `--contract` is unaffected).
  Separately, `_detect_intra_dep_signature_changed`'s reachability cache
  used `reachable_cache.setdefault(lib, _reachable_intra_libraries(...))`,
  which evaluates the default-value argument unconditionally regardless
  of cache hit — running the full `DT_NEEDED` BFS on every call and
  defeating the point of caching for a bundle with many changed symbols
  against the same consumer. Fixed via a new shared
  `bundle_resolution_reachability.cached_reachable_intra_libraries`
  helper with an explicit membership check.
