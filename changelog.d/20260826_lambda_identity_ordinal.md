### Fixed

- **Lambda-closure identity no longer embeds its source `:line:col`**: a
  castxml/clang closure-parameterized type/function (e.g.
  `raii_guard<(lambda at task_group.h:522:26)>`) previously compared as
  removed-plus-added whenever an *unrelated* edit earlier in the same header
  shifted the lambda to a new line -- reported against real oneTBB
  2021.13.0 -> 2022.3.0 binaries as a spurious `type_removed`/`type_added`
  pair, a paired `func_removed`/`func_added` on every ctor/dtor/method of
  the instantiation, and a `declaration_renamed` RISK finding whose entire
  content was the line-number text. `AbiSnapshot.
  renumber_anonymous_closure_identities()` now replaces the
  `:<line>:<col>` discriminator with a stable ordinal -- "the Nth lambda of
  this marker kind declared in this header" -- computed once per snapshot,
  mirroring GCC/DWARF's own per-scope `{lambda(...)#1}` numbering. As long
  as an edit doesn't reorder or add/remove same-header, same-kind lambdas
  relative to each other, both sides of a comparison now assign the
  identical ordinal to the identical closure, eliminating all three noise
  classes for that case.
