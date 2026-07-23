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

- **Reachability proof paths now prefer value-propagating evidence over a
  shorter pointer/indirection path** (ADR-046 D6, partial, G29 Phase 2
  slice 4): the layout-walk reachability proof path (`MarkReachability` in
  `post_processing.py`) previously picked the shortest path regardless of
  whether it crossed a pointer, which could show a weaker "reached through
  a pointer" proof even when a stronger value-propagating path also
  existed. `internal_leak.select_preferred_path` now prefers a
  value-propagating path first, falling back to shortest-within-tier — the
  shown path always matches the strongest evidence actually available.

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
