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
### Changed

- **`internal_leak.py` no longer depends on `buildsource`, `impact` or `policy`** — the L5 dependency-path formatter and graph label/kind maps moved to `model/source_graph_query.py`, the leak findings' ImpactAssessment is cached by `post_processing_reachability.cache_impact_assessments`, and `layout_proven_invisible` moved to `compare/layout_visibility.py`. No finding, report, or exit code changes; the module's physical move into `compare/` is the next step.
- **`--use-cases` reachability no longer takes an injected `explain` port** — `explain_use_case_impact` moved from `impact/use_case_reachability.py` to `policy/use_case_reachability.py`, and its call-graph walk moved to `compare/call_graph_walk.py`, so the policy layer no longer depends on the unclassified `internal_leak` module. Same attribution; `build_use_case_impact` and `attach_use_case_impact` lose their `explain` keyword.
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
