### Performance

- **`serialize` scaling-benchmark scenario: real ~2x regression from ADR-063
  Phase 5's growing `Fact[T]` sibling surface, root-caused and partially
  fixed.** `qualified_name_segments_walk._collect_strings` — the closure/
  anonymous-identity walk's own "cheap no-op when nothing embeds a marker"
  common-case check, run on every snapshot load — now recurses into every
  reachable `Fact[...]`'s `status` field, which is structurally guaranteed
  to never hold a string (`FactStatus` is a plain `enum.Enum`, not a
  `(str, Enum)`); skipping it (recognized the same structural way
  `_walk_rewrite_strings`' own `is_fact_value_field` already recognizes a
  `Fact`, without importing it) removes a real, measured slice of the added
  cost, confirmed via `cProfile` (fewer `_collect_strings` calls, no
  `dataclasses.fields()` call doubled). The remainder is genuinely new work
  (encoding/decoding ~27 more `Fact[...]` siblings per declaration as of
  this PR), not avoidable waste — `scripts/benchmark_scaling.py`'s
  `Scenario` gained a per-scenario `regress_tolerance`/
  `regress_min_delta_seconds` override (mirroring the existing
  `gate_exponent` per-scenario opt-out), scoped to `serialize` alone with
  its cause documented inline, rather than raising the blanket 15%/100ms
  "stable synthetic PR scenario" tolerance for every scenario.
