### Added

- **The ADR-094 `call` axis classifies struct returns from header layout** —
  `abicheck.model.sysv_aggregate.struct_return_of` derives a struct return's
  x86-64 SysV shape (one or two INTEGER or SSE eightbytes, or caller memory)
  and its flattened member layout from the L2 record facts (no DWARF), and
  `assess_call_interface` now compares two struct returns of the same class
  member by member instead of answering UNKNOWN: `{long, long}` →
  `{int, int, long}` is BROKEN, `{int}` → `{int, long}` is PRESERVED, a memory
  result that grows is BROKEN. Proved in Lean for any member list
  (`formal/lean/AbiLean/StructRetFragment.lean`: the comparison equals the
  member-read oracle, which holds iff the old member layout is a sub-layout
  of the new one, both or neither in memory and memory not grown) and checked
  against execution on gcc and clang over 1190 layout changes
  (`formal/experiments/struct_ret_fragment.py`). Unions,
  mixed INTEGER/SSE structs, `float`/`long double` members, fundamental
  arrays and C++ records not proved trivially copyable stay UNKNOWN. The axis
  still decides no verdict.
