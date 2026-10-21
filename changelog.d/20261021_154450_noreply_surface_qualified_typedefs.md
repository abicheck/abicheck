<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **Public-surface closure follows colliding member typedefs** — two classes that each declare a member alias such as `value_type` shared one bare key in `AbiSnapshot.declarations.typedefs`, so the closure followed only the surviving target and a real break behind the other class's alias could read `NO_CHANGE`. The closure now resolves typedef spellings through `typedefs_qualified` (schema v25): a bare spelling follows every qualified typedef with that `::`-tail, and only a unique match is recorded as an exact identity. Pre-v25 snapshots resolve exactly as before.
