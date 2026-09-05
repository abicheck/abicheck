// Demonstrates the source-break behavior of case106.
// The app uses the v1 implicit-conversion shape; it links against either
// version's .so since the mangled name of `operator int() const` is
// unchanged.
#include "v1.h"
#include <cstdio>

int main() {
    mylib::task_arena ta(4);

    // *** Source-break demonstration ***
    // The next line is what breaks under v2: implicit conversion from
    // `mylib::task_arena` to `int` via the user-defined conversion
    // operator. v1 allows it; v2 marks the operator `explicit`, so this
    // exact source line fails to compile against v2.h. Mangled name
    // unchanged ⇒ a binary built against v1 still loads against v2.so,
    // but anyone recompiling sees the break.
    int n = ta;
    std::printf("concurrency = %d (expect 4)\n", n);
    return 0;
}
