<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **Suppressions that hide never-analysed changes are now visible** — a
  suppression rule on the default `unreachable-only` gate still hides a change
  whose reachability graph coverage never established, and its behaviour is
  unchanged, so existing policy files keep working. The report now adds a
  `graph_coverage_insufficient_for_suppression` finding (risk, advisory) for
  each such change, naming the rule and the symbol: "N suppressed by rule X
  without graph coverage". A change the walk proved unreachable records nothing
  new. The exit code does not change, but the rendered verdict of an affected
  run reads `COMPATIBLE_WITH_RISK`. Add `reachability: proven-unreachable-only`
  to a rule to require proof instead.
