<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **`BuildSourcePack.content_hash()` is now stable across replay runner
  cache warmth/wall time for the on-disk `source_abi.json` artifact too.**
  Its coverage dict carries the same replay timing/cache-hit fields
  (`cache_lookup_s`, `extract_s`, `link_s`, `elapsed_s`, `extractor_jobs`,
  `cache_misses`, `cache_hits`) as the manifest coverage rows fixed
  previously, so two packs with identical source facts collected under
  different cache/timing conditions still produced different artifact
  digests. **The GitHub Action's `abi-baseline` auto-fetch no longer
  crashes under macOS's stock bash 3.2** when `GITHUB_REPOSITORY` is
  unset — expanding an empty array as `"${arr[@]}"` is itself an
  unbound-variable reference on bash < 4.4, so the `-R` flag array now
  uses the same `${arr[@]+"${arr[@]}"}` guard the file already relies on
  elsewhere.

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
