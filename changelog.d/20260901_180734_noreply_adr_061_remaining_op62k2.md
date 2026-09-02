<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **`--demangle` now reaches the `--used-by`/`--required-symbol(s)`
  scoped-gate section and the `--audit-suppressions` section of a
  markdown/text/review report.** Both are appended after the report body's
  own demangle pass and previously stayed mangled regardless of
  `--demangle`; missing-symbol names, missing-entrypoint names, scoped-only
  change descriptions, and a suppression rule's own `symbol=`/selector
  label are now demangled consistently with the rest of the report.
