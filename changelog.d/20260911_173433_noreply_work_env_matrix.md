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

- **`resolve_compare_request()` now resolves `env_matrix_path` itself, before side acquisition** — a bad or missing environment matrix used to be validated only inside `run_compare_request`'s own wrapper-level early check, so a documented two-phase caller invoking `resolve_compare_request()` directly (per `workflows/AGENTS.md`'s two-phase split, e.g. the native `compare` CLI's ADR-049 `resolve_and_apply` flow) still ran full side acquisition/extraction before any matrix was read, and a matrix edited between resolution and classification could change an already-resolved pair's outcome. The resolved matrix is now a real field, `ResolvedComparePair.resolved_env_matrix`, so every caller of `resolve_compare_request()` gets the same early failure and `classify_compare_pair()` reads an immutable value that travels with the frozen pair instead of re-reading the file or requiring an out-of-band parameter.

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
