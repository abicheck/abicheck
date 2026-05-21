// case109 v2 — feature graduated to lib::sort while the experimental
// alias is intentionally retained for backward compatibility.
//
// This is the friendly graduation pattern: every consumer of v1 keeps
// compiling, and new consumers are nudged toward the stable name.
#pragma once

namespace lib {

void sort();

namespace experimental {

void sort();
void other_fn();

} // namespace experimental
} // namespace lib
