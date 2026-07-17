<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Added

- **New public-API additions get their own reporting sections and reviewer
  guidance** — the sticky PR comment now renders new public-API surface
  (`func_added`, `type_added`, `enum_member_added`, etc.) as its own "➕
  Public API additions" table (kind/symbol/detail/location), separate from
  the generic "✅ Safe" quality-findings list. The full Markdown report's
  Additions section now shows the same per-finding detail (kind, location,
  impact) that Breaking findings already get, instead of a bare description.
  Each finding with `recommended_action: "no_action_required"` now also
  carries a new, additive `reviewer_action` JSON field
  (`review_exhaustive_switches` / `document_stable_replacement` /
  `confirm_public_api_intent`) distinguishing "nothing for the old binary
  consumer to do" from "here's what a human reviewing this PR should check"
  (report schema bumped to `2.6`, additive). The "`COMPATIBLE` = only
  additions" legend text (Markdown report, MCP server docstring, and docs)
  is corrected to note that COMPATIBLE also covers quality findings.

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
