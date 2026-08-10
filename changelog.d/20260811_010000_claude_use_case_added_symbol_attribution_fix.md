<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`project validate-use-cases --against-new` never attributed an added
  symbol to a use case** (G29 Phase 4, Codex review): use-case attribution
  resolved only against the OLD snapshot's own source graph, but a symbol
  *added* on the NEW side — including a use case's own entrypoint just
  introduced — never existed in OLD's graph at all, so it silently read as
  unattributed regardless of what NEW's graph could prove. Fixed by
  explaining against both sides' own graphs and unioning the per-symbol
  result, mirroring `post_processing_reachability.MarkReachability`'s own
  `old_paths + new_paths` merge for the identical old/new asymmetry.
