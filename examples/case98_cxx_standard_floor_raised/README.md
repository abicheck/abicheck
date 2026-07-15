# case98 — C++ standard floor raised (build-context risk)

## What this case demonstrates

`v1.h` and `v2.h` declare an identical public surface and neither uses any
post-C++17 construct — a consumer TU including `v2.h` compiles unchanged
under `-std=c++17` with both GCC and Clang (verified directly). The
difference is entirely in the *library's own* build contract: `v2` is
compiled with `-std=c++20` (via `V2_COMPILE_OPTIONS` in `CMakeLists.txt`),
so the `.so` was produced under a higher C++ standard floor than `v1`
while its public declaration set stays byte-identical.

## Why build context changes the verdict

The symbol set, vtable, and declared interface are unchanged between
v1 and v2, so a deliberately context-free per-binary ABI diff returns
`NO_CHANGE`. The example validation supplies each side's compile database
through the public `abicheck` CLI. L3 build evidence then records
`std:CXX` changing from `gnu++17` to `c++20`, and comparison emits
`abi_relevant_build_flag_changed`.

The case has one ground-truth verdict at every scan depth:
`COMPATIBLE_WITH_RISK`. L3 is the first sufficient evidence layer; L4/L5
source replay is not required. An L0–L2 scan that reports `NO_CHANGE` has
missed the risk because it lacks the required evidence. That is a documented
detection gap, not a second expected verdict.

## Expected verdict

`COMPATIBLE_WITH_RISK` with
`abi_relevant_build_flag_changed` — the binary ABI is unchanged, but the
producer's ABI-relevant C++ dialect changed and consumers should review
the compatibility implications.
