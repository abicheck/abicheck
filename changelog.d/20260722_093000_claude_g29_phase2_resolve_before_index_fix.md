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

- **`SourceGraphSummary.add_edge`/`__post_init__` now resolve an edge's
  facts before computing its dedup key, not after** (Codex review): an edge
  whose role lives only in `facts` (not yet mirrored into `attrs`) had its
  `relation_key()` computed against an empty `attrs`/`resolved` view,
  indexing it under the wrong blank-role key instead of its true,
  post-resolution one. No current producer hits this (the real role-emitting
  call site already puts role in `attrs`), but it was a latent correctness
  gap for any future producer or constructor-seeded graph that populates
  `facts` directly.

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
