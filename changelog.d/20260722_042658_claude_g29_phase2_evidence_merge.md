<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Changed

- **L5 source-graph node/edge merge is now evidence-preserving, not
  first-writer-wins** (ADR-046 D2, G29 Phase 2 slice 1): when two producers
  (e.g. the build-integrated call/type graph and the header-only graph)
  register the same graph node or edge, the second producer's facts used to
  be silently dropped. `SourceGraphSummary.add_node`/`add_edge` now fold
  every producer's `attrs` into an order-independent `resolved` merge
  (`abicheck/buildsource/graph_facts.py`'s new `GraphFact`/`FactConflict`),
  recording a genuine cross-producer disagreement instead of silently
  picking one value. Internal graph plumbing only — no `ChangeKind`, CLI
  flag, or top-level report/SARIF field changes; graph/build-info
  serialization gains additive `facts`/`resolved`/`conflicts` fields, and a
  `.abi.json`/build-info pack written by an older abicheck still loads
  unchanged (the merge synthesizes a single fact from its existing
  `attrs`/`provenance`/`confidence`).

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
