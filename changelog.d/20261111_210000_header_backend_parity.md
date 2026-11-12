### Fixed

- **The Clang and native GCC header backends report what CastXML reports for
  the same change** — found by running the generated edit corpus and the
  example catalog under every backend:
  - Clang now records the ABI fact of a return type spelled through a typedef
    (the nearest declared typedef decides), so removing the typedef reads as an
    ABI-equivalent change, not a `func_return_changed` break.
  - The variable-access detector also accepts native GCC snapshots, which
    always recorded access, so narrowing or widening a static member reports
    `var_access_changed`/`var_access_widened` under native GCC too.
  - Native GCC now records record/enum/field deprecation, an incomplete
    (opaque) record, hidden friends, a class's declared vtable slots and its
    vptr, a const array's constness and a parameter's `restrict`; spells an
    unbounded array `T[]` and `_Atomic(T)` like the other backends; and parses
    a header as C or C++ by the same rule as CastXML and Clang when no language
    is configured (a C header was parsed as C++).
  - Extractor generation 55 and snapshot cache salt 49. A stored native GCC
    snapshot from generations 53-54 should be re-dumped.
