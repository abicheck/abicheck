<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **The direct-clang header-AST backend (`--ast-frontend clang`) now
  populates `vtable`/`vptr_offset_bits`, instead of always reporting an
  empty vtable and an unknown vptr offset.** Previously `dumper_clang.py`
  hardcoded `vtable=[]` unconditionally and never set `vptr_offset_bits` at
  all, which silently disabled `VPTR_INTRODUCED` and `TYPE_VTABLE_CHANGED`
  for every clang-only comparison. Reconstructing this correctly needed
  more than copying castxml's own `virtual="1"` XML attribute check: clang's
  `-ast-dump=json` output does not reliably mark a re-declaration that
  overrides a base's virtual method without repeating `virtual` or writing
  `override` (a common real-world style) as virtual at all. The new
  `dumper_clang_vtable.py` reconstructs virtuality via signature matching —
  a method is virtual if explicitly marked, or if its (name, parameter
  types, const-qualifier) identity matches an inherited virtual slot — with
  a destructor handled separately (implicitly virtual whenever any base has
  a virtual destructor, regardless of keyword). Matches castxml's existing
  `0`-if-polymorphic `vptr_offset_bits` heuristic; real multi-inheritance
  secondary-vtable placement remains a known gap on both backends (G31
  Phase C).
