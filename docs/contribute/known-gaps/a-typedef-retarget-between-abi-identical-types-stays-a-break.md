---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# A typedef retargeted between ABI-identical fundamentals stays a break; the clang frontend records no fundamental ABI fact

Index: [Known gaps](../known-gaps.md)

**Status: PARTIAL.** Schema v69 (generation 32) classifies a parameter, field,
return or variable retyped between ABI-identical fundamentals as
`abi_equivalent_type_changed` (compatible with risk). Two cases remain breaks,
by design of that change, and are recorded here rather than fixed in it.

## What remains

- **A typedef whose underlying type is retargeted** (`typedef long T;` becoming
  `typedef long long T;`) is reported as `typedef_base_changed`, which is
  `BREAKING`. The schema-v69 ABI fact is recorded on parameters, fields, returns
  and variables only. A typedef has no fact, so its retarget cannot be proven
  ABI-equal and keeps its break. The differential matrix
  (`tests/test_type_spelling_differential_matrix.py`) therefore leaves the typedef
  site out of the ABI-identical retype pairs.
- **The clang frontend (`--ast-frontend clang`) records no fundamental ABI fact.**
  Its JSON AST carries no size or alignment for a builtin, so a retype there keeps
  its name-based verdict. This is conservative (no false clean), not a missed
  break. Closing it needs a layout source for builtins (the optional layout tool
  or a sizeof probe), not a spelling table.

## Why this is not a spelling table

The fact is the compiler's answer (DWARF `DW_AT_encoding`/`DW_AT_byte_size`,
castxml's sized builtin nodes), not a mapping from a user's spelling. A new
fact for typedefs means a new persisted field on the snapshot's typedef map, which
is a schema change and was not in scope for this one.

## Tests that pin the rest

- `tests/test_abi_fundamental.py` — the equivalence rule over the full domain.
- `tests/test_abi_equivalent_retype.py` — the refinement, unit level.
- `tests/test_abi_retype_lanes.py` — the three evidence lanes end to end.
- `tests/test_type_spelling_differential_matrix.py` — the matrix, with the sign-
  extension probe that separates a break from a retype.
