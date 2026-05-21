// case112 v1 — public API lives under `inline namespace _V1`.
//
// The library author uses a versioned inline namespace to manage ABI
// transitions. From the consumer's point of view the namespace is
// invisible — they spell `lib::sort` — but the mangled symbol carries
// the `_V1` segment.
#pragma once

namespace lib {
inline namespace _V1 {

void sort();
void unique();

} // namespace _V1
} // namespace lib
