// case116 — consumer that calls the basic public function. The
// breakage is at compile time when the consumer's toolchain still
// targets C++17 and v2.h's `requires` clause fails to parse.
#include "v1.h"

int main() {
    lib::print_int(lib::identity(42));
    return 0;
}
