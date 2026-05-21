// case114 v1 — `lib::sort` is a free function.
//
// Consumer code that takes `decltype(lib::sort)` gets a function type.
#pragma once

namespace lib {

void sort(int* first, int* last);

} // namespace lib
