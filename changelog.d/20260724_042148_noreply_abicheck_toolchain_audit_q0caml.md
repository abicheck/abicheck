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

- **C++20 detection no longer treats `#if defined(__cplusplus) &&
  !defined(SWIG)` as a circular dialect/feature-test guard.** This is a
  common real-world idiom wrapping a header's C++-only section from a
  non-C++ consumer (SWIG, Doxygen, ...), not a genuine dialect check —
  the second conjunct doesn't reference `__cplusplus`/`__cpp_*`, and this
  heuristic never *is* one of those non-C++ consumers, so the whole
  condition is unconditionally true here just like the bare
  `defined(__cplusplus)` form already was. Previously the general
  `__cplusplus`-guard branch masked it as if circular, wrongly
  suppressing a valid C++20 declaration inside and causing the header to
  be parsed without `-std=gnu++20`. A genuinely dialect-related second
  conjunct (e.g. `!defined(__cpp_concepts)`) still stays mask-worthy.

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
