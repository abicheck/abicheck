#pragma once

namespace lib {
enum class Mode : unsigned int { Fast, Exact };
int run(Mode m);
}  // namespace lib
