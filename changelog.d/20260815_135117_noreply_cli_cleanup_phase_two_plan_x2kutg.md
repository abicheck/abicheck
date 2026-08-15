<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Removed

- **`compare --stat` and `compare --recommend` are gone.** CLI cleanup phase
  two, PR 1 (ADR-037). Manual human `--stat` use moves to `--format review`;
  the built-in `--profile quick` keeps its own internal one-line renderer,
  but is also an *analysis* profile (`--depth binary`), so it is not a
  behavior-preserving substitute for `--stat` on its own — pair it with an
  explicit `--depth` to keep the same evidence level. `--stat --format
  json` moves to plain `--format json` in full report mode, which already
  carries the same `summary` object alongside the full `changes` array
  (`--report-mode leaf` intentionally omits `binary_compatibility_pct`/
  `affected_pct`). `--recommend` is gone because the release recommendation
  is now unconditional: always in JSON's `release_recommendation` field
  (already true before this change) and now always rendered in
  `markdown`/`review` output too. An explicit `--format` on the command
  line always overrides `--profile quick`'s injected format, so `--profile
  quick --format json` now returns the full JSON report rather than a
  summary-only shape — a deliberate refinement over the old `--stat`
  boolean's behavior. Neither flag gets a deprecation alias; both exit `64`
  with `No such option`.

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
