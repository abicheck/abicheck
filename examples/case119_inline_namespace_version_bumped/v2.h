// case112 v2 — inline namespace bumped from `_V1` to `_V2`.
//
// Declarations look identical to the consumer (`lib::sort`, `lib::unique`)
// so source keeps compiling, but every TU recompiled against v2 produces
// mangled symbols carrying `_V2`. Old TUs in the same program ODR-
// violate against new TUs — the classic silent inline-namespace failure.
#pragma once

namespace lib {
inline namespace _V2 {

void sort();
void unique();

} // namespace _V2
} // namespace lib
