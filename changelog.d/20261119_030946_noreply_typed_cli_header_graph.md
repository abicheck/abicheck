<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **Typed `dump()` header graph uses the dump's compile options** — the header graph a typed dump attaches is parsed with its `-D`, `-I`, sysroot and `nostdinc` context, so a declaration gated by a macro is in the graph exactly when the snapshot's own header parse sees it. Extractor generation 88 records this change; a snapshot dumped with options before it carries a graph without them.
