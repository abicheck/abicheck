### Fixed

- **The Clang and native GCC header backends report what CastXML reports for
  the same change** — found by running the generated edit corpus and the
  example catalog under every backend:
  - Clang now records a static data member's access, and the access detector
    accepts Clang and native GCC snapshots, so narrowing or widening it reports
    `var_access_changed`/`var_access_widened` instead of nothing.
  - Clang now records the ABI fact of a return type spelled through a typedef,
    so removing the typedef reads as an ABI-equivalent change, not a
    `func_return_changed` break.
  - Native GCC now records record/enum/field deprecation, an incomplete
    (opaque) record, hidden friends, a class's declared vtable slots and its
    vptr, a const array's constness and a parameter's `restrict`; spells an
    unbounded array `T[]` and `_Atomic(T)` like the other backends; and parses
    a header as C or C++ by the same rule as CastXML and Clang when no language
    is configured (a C header was parsed as C++).
  - Extractor generation 54 and snapshot cache salt 48. A stored native GCC
    snapshot from generation 53 should be re-dumped; an older Clang snapshot
    never compares variable access.
