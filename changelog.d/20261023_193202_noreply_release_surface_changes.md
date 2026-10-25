<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **A directory/package `compare` now reports its surface changes.** The
  release JSON carries a `surface_changes` block (release schema 2.2): each
  compared member's additions, removals and modifications through the same
  grouping the single-pair report uses, each entry naming its `library`, plus
  the aggregate counts and `libraries[].surface_changes` per member. The
  release Markdown and HTML pages render the same entries from that one block.
  A library present on one side only is listed with its ADR-065 state:
  `removed`/`added` only when the inventory proves it, otherwise `unselected`,
  `expected_not_produced`, `failed` or `unproven`, so an absence is never
  reported as a removal without proof. A fully compatible release still
  itemizes its additions.
