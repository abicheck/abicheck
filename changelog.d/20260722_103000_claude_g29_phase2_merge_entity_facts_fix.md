<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->
<!--
### Added

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
<!--
### Changed

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

### Fixed

- **`SourceGraphSummary.add_node`/`add_edge` now merge an already
  multi-fact incoming node/edge's full evidence, not just a single
  flattened fact** (Codex review): the duplicate-registration branch
  called `register_fact` with just the incoming entity's own top-level
  `provenance`/`confidence`/`attrs` — correct for a bare, single-producer
  construction, but wrong whenever the incoming node/edge already carried
  multiple facts of its own (e.g. re-added from an already evidence-merged
  graph), silently collapsing its accumulated per-producer facts and any
  recorded conflicts into one derived fact. New
  `graph_facts.merge_entity_facts` merges the incoming entity's whole
  `facts` list instead.

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
