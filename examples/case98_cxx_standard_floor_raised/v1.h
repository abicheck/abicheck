// case116 v1 — header works under C++17 (uses no post-17 facilities).
#pragma once

namespace lib {

template <typename T>
T identity(T x) { return x; }

void print_int(int x);

} // namespace lib
