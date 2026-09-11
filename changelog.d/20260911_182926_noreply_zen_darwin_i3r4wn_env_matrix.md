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

- **`classify_compare_pair()` no longer drops a caller-built pair's own environment-matrix intent** — a Tier-2 caller that constructs `ResolvedComparePair` directly (never through `resolve_compare_request()`) previously left `resolved_env_matrix` at its bare `None` default, which `classify_compare_pair()` read as "resolved, and there genuinely is no matrix" — silently ignoring any `CompareRequest.env_matrix`/`env_matrix_path` intent the request itself still carried, so a real runtime-floor violation could regress from `BREAKING` to a lower verdict. `resolved_env_matrix` now defaults to a dedicated `_UNRESOLVED_ENV_MATRIX` sentinel instead of `None`, distinguishing "never resolved" from "resolved, confirmed no matrix"; `classify_compare_pair()` falls back to `request.effective_env_matrix()` only in the former case. The normal `resolve_compare_request()` path, which always stores a real value (including an explicit `None`), is unaffected.

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
