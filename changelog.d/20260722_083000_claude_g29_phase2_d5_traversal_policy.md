<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->
<!--
### Added

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->

### Changed

- **The call-graph leak walk's edge-kind/stop/confidence rules are now a
  named, reusable `TraversalPolicy`** (ADR-046 D5, partial, G29 Phase 2
  slice 5): `internal_leak.CALL_GRAPH_TRAVERSAL_POLICY` reifies what
  `compute_call_graph_leak_paths` previously hard-coded inline. Behavior is
  unchanged for existing callers; a future walk can now construct its own
  policy (including a `minimum_confidence` floor, which is real, wired edge
  filtering) instead of re-deriving the same rules by hand.

<!--
### Deprecated

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
<!--
### Removed

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->

### Fixed

- **`SourceGraphSummary.compute_graph_id()` now hashes edges on the
  role-aware `relation_key()` instead of the coarse `key()`** (Codex
  review): since `add_edge` started deduping on `relation_key()` (ADR-046
  D1 follow-up), two graphs that differ only by an edge's role — e.g. the
  same `DECL_HAS_TYPE` edge changing from `role="return"` to
  `role="param"` — are genuinely different graph content, but the coarse
  key hashed them identically, silently hiding a real change from anything
  keyed on `graph_id`.

<!--
### Performance

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
<!--
### Security

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
<!--
### Documentation

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
