<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **castxml header backend now reports the record traits clang reports** --
  `is_standard_layout` was never derived from castxml evidence, so a record
  that loses standard layout (for example, mixed access control on its data
  members) was missed as `STANDARD_LAYOUT_LOST`. The trait is now computed from
  the C++ rules over the fields, access specifiers, bases and virtual members
  castxml emits, and stays unknown when they cannot decide it. A user-provided
  copy constructor declared on the same line as a defaulted default
  constructor no longer hides it from `TRIVIALLY_COPYABLE_LOST`, and a
  first-declaration `= default` special member now counts as trivial when its
  subobjects are.
