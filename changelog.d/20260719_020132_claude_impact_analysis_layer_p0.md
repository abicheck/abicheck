<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **Tri-state reachability for suppression** (impact-analysis-layer P0):
  `Change.public_reachable` was a boolean, so "the reachability walk
  positively proved this change unreachable" and "no walk ever reached a
  verdict at all" both collapsed to the same `False` — indistinguishable to
  a broad suppression rule. `Change.reachability_state` now distinguishes
  `PROVEN_REACHABLE`/`PROVEN_UNREACHABLE`/`UNKNOWN`; `MarkReachability` sets
  it to `UNKNOWN` when the only remaining signal for a change (the optional
  L5 call/type graph) is itself flagged narrowed/degraded, instead of
  silently treating an unexamined change as proven-unreachable. A new
  suppression `reachability: proven-unreachable-only` value opts a rule into
  refusing to match on `UNKNOWN` (with `allow_unknown_reachability: true` as
  the explicit bypass), reporting a new `suppression_reachability_unknown`
  diagnostic when withheld. The existing `unreachable-only` default keeps
  its original boolean semantics unchanged — no behavior change for
  existing suppression files. See
  [Suppressions § Proven vs. unknown reachability](../docs/user-guide/suppressions.md)
  and [Graph Coverage & Negative Evidence](../docs/concepts/graph-coverage.md).
