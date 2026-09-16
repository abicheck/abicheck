### Fixed

- `compare`'s stored-`BundleFacts`-vs-live-directory route
  (`bundle_side_input.compare_release_against_bundle_facts`) now resolves its
  members inside one request-local AST acquisition scope, and assembles the NEW
  bundle topology from the per-member evidence it already resolved
  (`build_bundle_snapshot_mixed`'s `resolved_evidence`) instead of re-parsing
  every resolved member's ELF a second time. Members of one release that share
  a public header and compile context previously re-acquired, re-decoded and
  re-normalized the identical header AST once per member. Measured on a
  six-DSO C++ release: effective frontend acquisitions 6 -> 1 per backend,
  neutral normalizations 6 -> 1, bundle-topology ELF re-reads 6 -> 0, with
  byte-identical findings, verdicts, scope record and analysis errors. The
  scope is opened by the workflow, so a typed/Python-API caller gets the same
  reuse as the CLI; a member with genuinely missing evidence still takes the
  documented live-parse fallback, and differing per-library
  headers/includes/compile context still resolve under separate keys.
