### Changed

- **`func_virtual_became_pure` and `var_value_changed` declare where they are observable (ADR-093).** A pure virtual has no body and no symbol, so only the public header declares it: `{B,H}`. A const initializer change is read from the binary's section bytes, but only for a variable a header or DWARF types: `{B,D}`, `{B,H}`, not `{B}` (a stripped binary alone emits nothing). Both leave the UNCLASSIFIED baseline (133 -> 131).
