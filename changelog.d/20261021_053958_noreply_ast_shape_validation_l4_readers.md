<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **A corrupt clang AST fails the source-ABI extraction instead of reading as an empty surface** — `source_abi_from_clang_ast` now refuses a tree whose `inner` is not a list, or holds a non-object child, at any depth, raising `SourceExtractionError`. The translation unit is recorded as a failed extraction, rather than contributing an empty, clean-looking L4 source surface.
