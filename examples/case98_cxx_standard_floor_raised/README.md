# case98 — C++ standard floor raised (per-binary: NO_CHANGE)

## What this case demonstrates

`v1.h` and `v2.h` declare an identical public surface. The difference
is the build contract: `v2` is compiled with `-std=c++20` (via
`V2_COMPILE_OPTIONS` in `CMakeLists.txt`), so the `.so` was produced
under a higher C++ standard floor than `v1`. Consumers still building
with C++17 see no symbol churn but a freshly compiled TU against the
new headers will hit any C++20-only constructs the library starts to
require.

## Why per-binary detection cannot see this

The symbol set, vtable, and declared interface are unchanged between
v1 and v2, so every conventional ABI diff returns `NO_CHANGE`. The
break-grade signal only appears when abicheck is fed a *matrix* of
per-configuration snapshots (one per consumer toolchain): the
`CXX_STANDARD_FLOOR_RAISED` detector compares the minimum `cxx_std`
across configurations and emits the finding when the floor moves up.

The probe-harness mechanism that produces matrix snapshots lives in
`abicheck.probe_harness`; it is not yet wired into the default
`compare` CLI (deferred follow-up — see PR #247).

## Expected verdict

`NO_CHANGE` — single-config diff is correctly silent. The case is a
fixture for the matrix-detector channel.
