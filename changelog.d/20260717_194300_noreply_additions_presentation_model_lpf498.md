<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **Sticky PR comment no longer reports a policy-gated COMPATIBLE finding as
  "ABI BREAKING"** — with a severity category (e.g. `severity-addition:
  error`) promoting a COMPATIBLE addition or quality finding into the
  Breaking bucket to match the now-red check, the comment headline
  previously read "ABI BREAKING" regardless of *why* the bucket was
  non-empty. `pr_comment.py` now tracks each finding's severity-config
  category and only shows "ABI BREAKING" when the bucket holds a genuine
  `abi_breaking`/`potential_breaking` finding; a policy-only block now reads
  "Public API expansion requires approval" / "Quality policy violation" /
  "Source API break blocks this PR", with an added note explaining that
  compatibility and the CI gate are separate axes (ADR-042). The GitHub
  Action's Job Summary similarly names the blocking `severity.category`
  (from the JSON report) instead of a generic "Severity-level issue
  detected" for the `SEVERITY_ERROR` verdict.

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
