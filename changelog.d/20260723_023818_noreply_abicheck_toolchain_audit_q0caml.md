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

- **C++20 detection now masks the `#ifdef`/`#ifndef` shorthand form of a
  feature-test guard, not just the explicit `#if defined(...)` form.**
  `#ifdef __cpp_consteval` (or `#ifndef __cpp_concepts`) is exactly as
  circular as an explicit `#if defined(__cpp_consteval)` comparison — the
  macro is only defined once that C++20 feature is already enabled — so
  content behind it must not itself force that same enablement.
  `#ifndef __cplusplus` is masked too, since castxml/clang always parse
  these headers in a C++-ish mode and never actually reach that branch.
  `#ifdef __cplusplus` is deliberately left unmasked: unlike a
  version/feature-test comparison it is unconditionally true for every
  `-std=` this heuristic could pick, so its content is a genuine signal.

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
