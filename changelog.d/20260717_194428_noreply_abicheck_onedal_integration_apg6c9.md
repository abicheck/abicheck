<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **The GitHub Action now fails fast on unsupported mode/input combinations
  instead of failing late or silently doing the wrong thing.** A new
  `Validate mode/input combination` step runs before Python setup, system
  dependency installation, and `pip install abicheck`, and rejects: a
  directory/package passed to `dump`/`scan`'s `new-library` (neither mode
  has `compare`'s per-library fan-out — this previously surfaced as a
  confusing failure deep inside the tool, after a full toolchain install);
  `format: sarif`/`html` on `scan`/`deps-tree`/`deps-compare` (previously a
  silent fallback to a supported format with only a `::warning::`, which
  combined with `upload-sarif: true` produced neither an error nor a SARIF
  report); and `upload-sarif: true` without `mode: compare` + `format:
  sarif`. `action/run.sh` re-checks the same rules as defense in depth.

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
