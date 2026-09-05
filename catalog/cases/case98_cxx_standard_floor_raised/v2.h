// case98 v2 — header is byte-identical to v1.h *at the declaration
// level*. The C++ standard floor signal does not show up in a per-
// binary/header diff (the symbol/type set is unchanged) — it surfaces
// at L3 build-context comparison, which records std:CXX changing from
// gnu++17 to c++20 and reports abi_relevant_build_flag_changed
// (COMPATIBLE_WITH_RISK). An L0-L2 scan alone still reports NO_CHANGE;
// that is a documented detection gap from insufficient evidence, not
// the case's canonical verdict.
//
// The CMakeLists.txt builds v2 with -std=c++20 so the *.so embeds a
// post-C++17 contract via build configuration, but the public
// declaration set is identical to v1.
#pragma once

namespace lib {

template <typename T>
T identity(T x) { return x; }

void print_int(int x);

} // namespace lib
