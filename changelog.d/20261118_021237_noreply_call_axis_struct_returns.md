### Added

- **The ADR-094 `call` axis classifies struct returns from header layout** —
  `abicheck.model.sysv_aggregate.struct_returns_in` derives a struct return's
  x86-64 SysV shape (one or two INTEGER or SSE eightbytes, or caller memory)
  and its flattened member layout from the L2 record facts (no DWARF), and
  `assess_call_interface` now compares two struct returns of the same class
  member by member instead of answering UNKNOWN: `{long, long}` →
  `{int, int, long}` is BROKEN, `{int}` → `{int, long}` is PRESERVED, a memory
  result that grows is BROKEN, and a shape-only change where the new result
  leaves a register the old caller reads unset (`rax:rdx` → `rax`) is BROKEN.
  Lean (`formal/lean/AbiLean/StructRetFragment.lean`) proves, over its model
  of `int`/`long`/`double` structs and for every nonempty old member list,
  that the model's comparison predicate is equivalent to the member-read
  oracle (old member layout a sub-layout of the new one, both or neither in
  memory, memory not grown); the Python implementation is checked against
  that model and against execution on gcc and clang over 1190 layout changes
  (`formal/experiments/struct_ret_fragment.py`). Unions, mixed INTEGER/SSE
  structs, `float`/`long double`/`_Bool` members, fundamental arrays, records
  over 256 scalar members, and C++ records whose triviality for calls is not
  proved (any header backend but castxml) stay UNKNOWN. The axis
  still decides no verdict.
