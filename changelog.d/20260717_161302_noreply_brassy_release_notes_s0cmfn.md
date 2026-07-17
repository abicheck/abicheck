<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **Fragment-based changelog workflow (`scriv`)**: `CHANGELOG.md`'s
  `## [Unreleased]` section was hand-edited by nearly every PR, causing
  frequent merge conflicts on the same lines. PRs that touch
  `abicheck/**/*.py` now add a small `changelog.d/<name>.md` fragment
  (`scriv create`) instead; `scriv collect` compiles them into a dated
  section at release time. See `changelog.d/README.md` and the
  "Changelog entries" section of `CONTRIBUTING.md`. A new CI check
  (`.github/workflows/changelog-check.yml`,
  `scripts/check_changelog_fragment.py`) fails a PR missing a fragment
  unless labeled `skip-changelog`.
