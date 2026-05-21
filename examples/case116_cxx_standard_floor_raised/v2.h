// case116 v2 — header is byte-identical to v1.h *at the declaration
// level*. The C++ standard floor signal does not show up in a per-
// binary diff (the symbol/type set is unchanged); it only surfaces
// when the probe harness records each consumer build's cxx_std and
// compares the floors.
//
// The CMakeLists.txt builds v2 with -std=c++20 so the *.so embeds a
// post-C++17 contract via build configuration, but the public
// declaration set is identical to v1. The case is preserved as a
// fixture for whenever the dumper threads -std=c++20 through to
// castxml; for now, the per-binary verdict is correctly NO_CHANGE.
#pragma once

namespace lib {

template <typename T>
T identity(T x) { return x; }

void print_int(int x);

} // namespace lib
