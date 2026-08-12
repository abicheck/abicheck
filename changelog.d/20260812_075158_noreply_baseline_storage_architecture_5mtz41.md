<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **New reusable workflow `protect-committed-baseline.yml`** — closes a
  self-approval gap in a committed baseline: an ordinary PR that both
  changes the compared binary/headers and updates the baseline file it's
  compared against can otherwise make an incompatible change look
  compatible. The new workflow fails any ordinary PR that touches a
  configured `protected-paths` glob unless it carries an explicit
  human-reviewed `bypass-label`. `docs/use/baseline-storage.md`'s
  Git-committed-baseline recipe also gained the complementary fix (read the
  baseline from the PR's base commit via `git show`, not the working tree).
