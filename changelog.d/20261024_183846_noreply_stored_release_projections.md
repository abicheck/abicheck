<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Added

- **A stored `BundleFacts` comparison renders JUnit and `oneline`** — `compare OLD.bundle-facts.json NEW -o junit=...` and `-o oneline=...` now go through the same release renderer a directory/package comparison uses, so the stored operand's verdict, exit code, members and findings match the live operand's for the same release. JSON and Markdown keep their stored shape, and HTML and `--view show=` remain unsupported for a stored OLD_INPUT (the stored JUnit also omits the passing cases for unchanged symbols, which need the old-side inventory). The supported stored formats are now `json`, `markdown`, `junit` and `oneline`.
