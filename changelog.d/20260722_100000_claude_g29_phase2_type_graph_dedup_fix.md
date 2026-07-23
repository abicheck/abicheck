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

- **`type_graph.py`'s own upstream `TypeEdge` deduplication now preserves
  role-distinct relations** (Codex review): a function that both returns
  and takes the same private type (`detail::Impl foo(detail::Impl)`) emits
  two real, role-distinct `DECL_HAS_TYPE` edges sharing `(src, dst, kind)`
  — `_dedupe_edges` (per compile unit) and the cross-translation-unit merge
  in `ClangTypeGraphExtractor.extract_from_build` both deduplicated on that
  coarse triple, without role, dropping the second role before it ever
  reached `SourceGraphSummary.add_edge`. No amount of role-awareness in the
  graph layer could recover evidence already dropped one level upstream.
  Both now key on `(src, dst, kind, role)`.

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
