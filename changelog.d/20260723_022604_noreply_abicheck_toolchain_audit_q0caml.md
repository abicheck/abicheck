<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
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

- **`consteval`/`constinit`/`concept`/`requires` type-shadow detection now
  covers `union`/`enum`, not just `struct`/`class`.** A pre-C++20 header
  declaring one of these future keywords as a `union` or `enum` type
  (`union concept { ... };`) is exactly as legal as the `struct`/`class`
  form, but the shadow patterns only recognized the latter, so the header
  was wrongly forced into C++20 mode.
- **C++20 detection no longer forces `-std=gnu++20` based on content
  guarded by `#if __cplusplus >= ...` or a standard feature-test macro
  (`__cpp_concepts`, ...).** Such a guard is self-consistent under
  whichever `-std=` this heuristic ends up choosing — the guard's own
  condition, driven by that same choice, is what makes its content
  reachable — so the content behind it must never drive the choice
  itself. Forcing C++20 mode purely because a guarded block contains
  C++20 syntax doesn't just needlessly activate that block; it also turns
  every *unguarded* pre-C++20 use of these keywords as an ordinary
  identifier elsewhere in the same header into a reserved-word parse
  error. Guarded content is now masked the same way `#if 0` is.

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
