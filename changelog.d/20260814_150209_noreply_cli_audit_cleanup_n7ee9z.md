<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Added

- **`--compiler`/`--compiler-prefix`/`--compiler-option`** are new, neutral
  spellings for `compare`/`dump`/`scan`'s cross-toolchain flags, replacing
  `--gcc-path`/`--gcc-prefix`/`--gcc-option` (which always accepted a Clang
  binary too, despite the name). The old flags stay fully functional — just
  hidden from `--help`/`--help-all` since they're superseded, not removed —
  and print a one-line deprecation note to stderr when used; a new-spelling
  scalar value wins if both `--compiler`/`--gcc-path` (or
  `--compiler-prefix`/`--gcc-prefix`) are given for the same setting, while
  `--compiler-option`/`--gcc-option` (both repeatable) cannot be mixed in
  the same invocation — combining them is a usage error, since no merge or
  argv-order-recovery rule can be correct across two independently-collected
  option tuples (see the later `--gcc-options` removal fragment below for
  the follow-up: that whitespace-split scalar flag is gone, not merely
  deprecated, as of this release).

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
