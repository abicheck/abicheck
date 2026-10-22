<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **Suppressions that hide never-analysed changes are now visible** — a
  suppression rule on the default `unreachable-only` gate still hides a change
  whose reachability graph coverage never established, and its behaviour is
  unchanged, so existing policy files keep working. The report now adds one
  informational `graph_coverage_insufficient_for_suppression` record per rule,
  carrying the count and the suppressed change ids: "N suppressed by rule X
  without graph coverage". The record never changes the verdict or the exit
  code. Add `reachability: proven-unreachable-only` to a rule to require proof
  instead.
