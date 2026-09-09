<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **`compare` on a directory/package input now applies `--view show=...`
  inside each renderer instead of to one shared upstream projection** — a
  secondary `--write` report was incorrectly inheriting the primary
  `--format`'s own `--view show=...` filter (contradicting `--write`'s
  documented always-full contract), the JUnit primary format silently
  ignored `--view show=...` entirely, and the release-global bundle/matrix
  findings (JSON and Markdown) bypassed the filter outright. `--write` is
  now always full/unfiltered; JUnit, JSON, and Markdown now agree on which
  findings a given `--view show=...` selection keeps, bundle/matrix
  findings included.
