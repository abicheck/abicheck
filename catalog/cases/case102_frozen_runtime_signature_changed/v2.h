// case120 v2 — the frozen runtime entry widens its parameter.
//
// `mylib::detail::r1::dispatch(int)` is changed to take `long`. extern
// "C" symbol name is unchanged, so the binary still links — but every
// consumer compiled against v1 pushes an `int` into what is now a
// `long` parameter slot. On most ABIs this is silent corruption.
//
// This violates the documented r1-append-only contract. The
// frozen-namespace policy turns the existing FUNC_PARAMS_CHANGED finding
// into a flagged contract violation that cannot be silently downgraded
// via policy_override.
//
// The migration path the library author SHOULD have taken: add a new
// entry `r2::dispatch(long)` and keep `r1::dispatch(int)` alive.
#pragma once

namespace mylib {
namespace detail {
namespace r1 {

// Parameter widened in place — contract violation.
extern "C" long dispatch(long concurrency);

} // namespace r1
} // namespace detail

inline long run(long concurrency) {
    return detail::r1::dispatch(concurrency);
}

} // namespace mylib
