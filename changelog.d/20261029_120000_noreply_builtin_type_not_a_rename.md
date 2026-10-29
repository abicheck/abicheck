<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **A builtin type change is a break, not a declaration rename** — graph reconciliation paired any two type nodes sitting in the same parent position as one renamed declaration. `float _Complex` -> `double _Complex` (a parameter, field or return growing 8 -> 16 bytes) read `COMPATIBLE_WITH_RISK` with a single `declaration_renamed`. A fundamental spelling (`int`, `float _Complex`, `__int128`, vector and SIMD types, and their pointer, cv and array forms) is now never a rename candidate, so the change is reported as the break it is (exit 4). castxml also spelled `_Complex` and `_BitInt(N)` types as the one literal `Unimplemented`, so the default header lane reported such a change as clean; the clang AST join that already covered vectors now spells them too. The extractor generation moves to 29 and the castxml cache schema to 9, so older baselines holding `Unimplemented` are re-dumped.
