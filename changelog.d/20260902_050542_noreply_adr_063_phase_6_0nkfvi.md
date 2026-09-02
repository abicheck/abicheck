<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **A closure marker present only in a hybrid-merge conflict's discarded
  value no longer shifts a real declaration's ordinal** — assigning that
  marker's ordinal by folding it into the same sorted coordinate pool real
  declarations are numbered from meant a backend disagreement's mere
  presence (or absence) between two otherwise-identical snapshots could
  relabel real closures and manufacture spurious removals/additions in a
  comparison. A conflict-only marker's ordinal is now assigned separately,
  as a pure continuation appended after every ordinal its group already
  has, never inserted into the sequence real declarations are numbered
  from.
