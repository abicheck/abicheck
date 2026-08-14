<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Changed

- **`scan --against`'s `--strict-suppressions`, `--public-symbol`, and
  `--public-symbols-list` are now hidden** (still fully functional), each
  already read from `.abicheck.yml` (`suppression.strict`/
  `scope.public_symbols`) with CLI-beats-config precedence — matching
  `compare`'s own already-demoted equivalents (ADR-037 D4). `--exit-code-scheme`
  stays visible on both commands, unchanged, as a deliberate coarse override.
- **`dump --help`/`scan --help`'s "N advanced option(s) hidden" footer no
  longer overcounts options `--help-all` can't actually show** — a Click-
  hidden option not listed in any help panel (a deprecated no-op shim, a
  superseded alias like the now-hidden `--gcc-path`) never renders even in
  `--help-all`; the count now only includes options that genuinely appear
  there.

<!--
### Added

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
