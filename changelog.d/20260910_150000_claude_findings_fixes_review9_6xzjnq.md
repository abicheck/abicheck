### Fixed

- **A stored-bundle-facts `compare` (`compare OLD.bundlefacts.json NEW_DIR`)
  and a `compare-release` build-configuration matrix finding
  (`--probe-matrix old=/new=`) both bypassed `.abicheck.yml`'s
  `policy.overrides` project-config fold entirely.** Both dispatchers
  bypass the ordinary `run_compare`/`classify_compare_pair` machinery that
  applies the fold, and each independently built its own `PolicyFile`
  without it — a project override that made an equivalent ordinary
  `compare` exit `0` still left the stored-bundle comparison `BREAKING`
  (exit `4`), and a matrix finding like `cxx_standard_floor_raised` never
  honored a project override the per-library and bundle-result findings in
  the same release already did. Fixed by threading the same
  `PROJECT_CONFIG`-tier fold every other route (scalar `compare`, `scan
  --against`, the release fan-out, the typed API) already applies: the
  stored-bundle-facts dispatcher now folds its own already-loaded
  `.abicheck.yml` `policy.overrides` right after loading its base
  `PolicyFile`; the matrix path now goes through
  `pack_application.resolve_bundle_policy_file` (the shared resolver its
  own sibling bundle-result call already used) instead of hand-rolling an
  incomplete fold. A systematic sweep of every remaining
  `_load_suppression_and_policy`-derived `PolicyFile` construction site
  found no further gaps of this shape.
