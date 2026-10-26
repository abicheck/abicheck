#pragma once

namespace lib {
enum class Mode : unsigned char { Fast, Exact };
int run(Mode m);
}  // namespace lib
