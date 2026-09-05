// case109 v1 — feature lives in lib::experimental:: only.
//
// Header-only / template-library shape: the public-API author wants to
// publish a stable name later. For now the only spelling is
// `lib::experimental::sort`.
#pragma once

namespace lib {
namespace experimental {

void sort();
void other_fn();

} // namespace experimental
} // namespace lib
