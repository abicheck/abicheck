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
### Fixed

- **`.abicheck.yml`'s `source:` block no longer silently accepts a `graph`
  sub-key** — it belonged to the plural `sources:` block (`sources.graph`,
  the actual L5 source-graph detail knob) and was never read from `source:`
  (singular); a config that used the wrong block name was accepted and
  silently ignored instead of erroring. `source: {graph: ...}` now raises
  the same "unknown key" error as any other typo.
- **A `-H`/`--header`/`--devel-pkg` directory input no longer spuriously
  fails `compare` with `ScopeMismatchError`** — the comparability contract
  (ADR-050) fingerprinted a directory the same way as an individual header
  file, corrupting `scope_fingerprint`'s common-root computation whenever
  the directory sat shallower than the declared headers under it (e.g. a
  `--devel-pkg`-extracted package root passed alongside headers discovered
  several directories below it). Two extractions of byte-identical headers
  into two differently-named temp directories — including the exact case a
  real-world `.deb` self-comparison hit in CI — could fingerprint as a
  scope mismatch even though nothing about the declared surface differed.

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
