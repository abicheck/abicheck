<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **`compare --compiler`/`--compiler-prefix`/`--compiler-option` (added
  earlier in this release cycle) are now correctly rejected for
  directory/package (release) compares**, matching their legacy
  `--gcc-path`/`--gcc-prefix`/`--gcc-option` counterparts — previously they
  silently no-opped instead of raising a usage error, since the per-library
  fan-out doesn't thread an L2 compile context at all.
- **`--compiler-option`/`--gcc-option` no longer silently reorder flag/value
  pairs when both spellings are passed in the same invocation** (e.g.
  `--gcc-option=-include --compiler-option='some header.h'` could separate
  `-include` from its own operand). Whichever spelling is given now wins
  entirely, matching the precedence already used for the scalar
  `--compiler`/`--gcc-path` pair, instead of concatenating two
  independently-collected token lists with no record of their original
  relative order.

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
