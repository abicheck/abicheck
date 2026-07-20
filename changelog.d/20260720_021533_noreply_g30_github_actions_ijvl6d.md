<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Documentation

- **Canonical multi-DSO recipe, with a scope caveat** — `github-action-source-scans.md`'s
  "Recommended flow: a multi-library release with one shared facts pack"
  section is now explicitly marked as the canonical recipe other pages link
  to, and states plainly what it does and doesn't prove: it supports a
  build-wide source audit and per-target header-depth checks, but not an
  independently-proven per-target *source*-depth claim from one shared
  `abicheck_inputs/` pack (G30 P0.4).
