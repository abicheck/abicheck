<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **Narrowing a base-class access specifier is now reported** — changing a
  base from `public` to `protected` or `private` (or `protected` to `private`)
  used to report NO_CHANGE. Code that converts a derived pointer to the base
  or reaches the base's members through the derived type no longer compiles,
  so it is now the new `BASE_CLASS_ACCESS_NARROWED` kind with verdict
  `API_BREAK`, from castxml and clang header evidence. Widening stays silent,
  and a snapshot without the access evidence (older snapshots, DWARF) reads as
  unknown, never as a change.
