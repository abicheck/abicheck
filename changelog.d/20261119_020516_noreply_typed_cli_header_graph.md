<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **Typed hybrid dumps apply the compile options to the header graph** — the one graph a typed hybrid dump attaches to its merged snapshot now parses with the same `-D`, `-I`, sysroot and `nostdinc` context as its header legs, so a macro-dependent header no longer yields a declaration-only graph on that route. The direct typed route is unchanged for now.
