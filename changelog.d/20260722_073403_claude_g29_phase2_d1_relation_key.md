<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Changed

- **L5 graph edges gain a role-aware identity alongside their existing
  coarse one** (ADR-046 D1, G29 Phase 2 slice 3): `GraphEdge.relation_key()`
  returns `(src, dst, kind, role)` so two structurally different
  dependencies that share `(src, dst, kind)` — e.g. a type used as a
  function's return type on one edge and as a parameter type on another,
  both `DECL_HAS_TYPE` — stay distinguishable to code that needs that
  distinction. `GraphEdge.key()` keeps its exact shape and remains used by
  the role-blind `diff_source_graph` comparison; `SourceGraphSummary.add_edge()`
  deduplicates on the role-aware `relation_key()` instead (a same-PR
  follow-up fix — deduping on `key()` alone silently folded two real,
  role-distinct edges into one).
  `GraphNode`/`GraphEdge` also moved from `abicheck/buildsource/source_graph.py`
  to `abicheck/buildsource/graph_facts.py` (both re-exported from the old
  location) to keep `source_graph.py` under the project's file-size cap;
  purely a module reorganization, no import path most callers use changes.

<!--
### Added

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
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
<!--
### Fixed

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
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
