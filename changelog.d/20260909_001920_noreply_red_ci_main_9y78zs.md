### Fixed

- **The self-healed clang dump's cache fix no longer breaks the in-process
  AST handoff to header-graph attachment, or leaves pre-existing on-disk
  entries stale.** Writing a self-healed dump's cache entry under the
  post-retry key (the immediately preceding fix) also moved the
  in-process AST *memo* to that same key — but the memo is a one-shot,
  same-thread handoff to `service._attach_header_graph`'s own follow-up
  call, which independently recomputes the original pre-retry lookup key
  and has no way to know a self-heal happened. That miss repeated both
  clang attempts and leaked the handed-off AST in the thread's memo slot
  indefinitely. The memo write now stays keyed by the pre-retry key
  (safe: that consumer discards the resolved compile-language bit
  entirely), while the disk-cache write keeps using the corrected
  post-retry key. Separately, the clang AST cache key now folds in a
  schema version, so a pre-existing on-disk entry an older binary wrote
  under the stale pre-retry key for a self-healed dump can no longer stay
  silently reachable and reintroduce the exact bug the key fix closes.
