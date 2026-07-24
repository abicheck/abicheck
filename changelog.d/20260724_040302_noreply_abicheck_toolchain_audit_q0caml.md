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

- **C++20 detection's shadow-name scan no longer lets a pre-C++20
  compatibility shim confined to a `#if __cplusplus < 202002L`/
  `#ifndef __cpp_x`/`#if !defined(__cpp_x)` fallback suppress a genuine
  C++20 declaration elsewhere in the header set.** The shadow scan
  reused the requirements scan's masking, which treats that guarded arm
  as live — correct for deciding what's reachable if C++20 is *not*
  forced, but backwards for the shadow scan's question of whether a
  `struct concept {};`-style shim would still exist once C++20 *is*
  chosen. It never would (that's the point of the fallback), so it must
  not shadow a real `concept`/`requires`/`consteval`/`constinit`
  declaration in a different file of the same aggregate — previously
  this kept the whole translation unit off `-std=gnu++20` and rejected
  an otherwise valid C++20 header set.

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
