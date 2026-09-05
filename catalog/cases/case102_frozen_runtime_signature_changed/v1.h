// case120 v1 — `extern "C"` symbol in a frozen runtime namespace.
//
// Mirrors oneTBB's contract: any function exported under
// `tbb::detail::r1::*` is APPEND-ONLY. Once shipped, the signature is
// fixed forever; incompatible changes must go into a NEW namespace
// (`r2`), keeping the old `r1` symbol available indefinitely.
//
// Here, `mylib::detail::r1::dispatch(int)` is an exported runtime entry
// point. The library author has declared `**::detail::r1::*` frozen via
// the policy file (see policy.yaml in this directory).
#pragma once

namespace mylib {
namespace detail {
namespace r1 {

// Public-but-frozen runtime entry. extern "C" so the mangling
// stays stable across renaming considerations.
extern "C" int dispatch(int concurrency);

} // namespace r1
} // namespace detail

// Header-only inline that calls into the runtime. Consumers compile
// this body into their own binaries, baking in the `int` parameter
// shape of `r1::dispatch`.
inline int run(int concurrency) {
    return detail::r1::dispatch(concurrency);
}

} // namespace mylib
