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

- **A `#if __cplusplus`/`#ifdef __cplusplus`/`defined(__cplusplus)`-guarded
  C++20 construct no longer by itself promotes an auto-detected header
  from C to C++ mode.** In C mode `__cplusplus` is undefined, so that
  guard's content was never actually reachable there — but the language-
  mode auto-detector reused the same "always true" treatment the C++20
  *dialect* decision correctly uses once C++ mode is already chosen,
  wrongly forcing C++ (and then C++20) mode purely because such a guard
  existed. This then turned an active, unguarded use of the same word as
  an ordinary C identifier elsewhere in the header into a reserved-word
  parse error. The C++20-dialect decision (once already parsing as C++)
  is unaffected and still treats these guards as unconditionally live.

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
