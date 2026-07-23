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

- **C++20 detection no longer masks `#if defined(__cplusplus)`/bare
  `#if __cplusplus` guards.** These are semantically identical to
  `#ifdef __cplusplus` — unconditionally true for every `-std=` this
  heuristic could pick — but the general `__cplusplus`/feature-test-macro
  guard pattern didn't distinguish them from a genuine version comparison
  and masked them too, hiding the only C++20 signal in the header. A
  combined condition with a real comparison attached
  (`defined(__cplusplus) && __cplusplus >= 202002L`) is still genuinely
  circular and stays masked, as does the negated `#if !defined(__cplusplus)`
  form. An `#elif defined(__cplusplus)` arm, once reached, now settles the
  chain the same way `#elif 1` does, marking any later sibling arm dead.

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
