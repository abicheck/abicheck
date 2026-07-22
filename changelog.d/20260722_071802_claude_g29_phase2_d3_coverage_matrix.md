<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Changed

- **L5 type-graph coverage tracking is now per-(kind, role), not just
  per-pass** (ADR-046 D3, G29 Phase 2 slice 2): a confirmed
  `inline_graph_fold.fold_type_graph` pass now also records which specific
  role (`base`/`field`/`alias`/`var`/`return`/`param`/`ref`) of each
  dependency edge kind it examined, alongside the existing per-pass
  `extractor_passes`/`narrowed_passes` flag. Internal graph plumbing only —
  no `ChangeKind`, CLI flag, or JSON/SARIF field changes; a `.abi.json`/
  build-info pack written by an older abicheck still loads unchanged (the
  finer keys are additive).

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
