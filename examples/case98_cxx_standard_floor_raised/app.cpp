// case98 — consumer that calls the basic public function. v2.h's
// declaration set is identical to v1.h's, so this consumer compiles
// unchanged under -std=c++17 against *either* header (verified with
// GCC and Clang) — there is no consumer-visible language-floor break
// here. The only real difference between v1 and v2 is that the
// library's own .so is built with -std=c++20 (see CMakeLists.txt),
// a build-configuration change that abicheck detects at L3 as
// abi_relevant_build_flag_changed.
#include "v1.h"

int main() {
    lib::print_int(lib::identity(42));
    return 0;
}
