<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **`BuildSourcePack.content_hash()` is now stable across replay runner
  cache warmth/wall time.** Its coverage rows previously hashed the raw
  `detail`/`elapsed_s` fields (e.g. "cache 2/3 hit (67%), 1.80s"), so two
  packs with identical evidence collected under different cache/timing
  conditions produced different content hashes. **The GitHub Action's
  `abi-baseline: latest-release`/tagged-release auto-fetch now passes
  `-R "$GITHUB_REPOSITORY"` to `gh release download`**, so it works in a
  job that never ran `actions/checkout` (e.g. comparing downloaded release
  artifacts only), matching the `-R`-when-known convention the Action's
  PR-comment posting already used.

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
