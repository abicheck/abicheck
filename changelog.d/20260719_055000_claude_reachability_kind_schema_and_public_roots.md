<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- `compare_report.schema.json`'s `reachability_kind` enum gained
  `"public_source_abi_surface"` (report schema `2.9` → `2.10`, additive) for
  the impact-analysis-layer P0 tri-state reachability work: an L4/L5
  source-graph finding whose kind is public by construction (e.g.
  `public_typedef_removed`) is now tagged with this value instead of one
  established by the public-surface layout/call-graph walk.

### Fixed

- `MarkReachability`'s call-graph trust check now also requires the graph to
  carry at least one real public declaration/type (mirroring
  `source_graph_findings._has_internal_reach_coverage`'s own requirement),
  not just that both extractor passes completed — a graph that finished both
  passes but captured no public closure at all has nothing to seed
  `compute_call_graph_leak_paths`'s walk with, which is indistinguishable
  from "walked thoroughly and found nothing" but is actually "never walked".
