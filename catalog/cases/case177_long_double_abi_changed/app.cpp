#include <cstdio>
#include "v1.hpp"

// Compiled once against v1's `compute(long double)`, which the linker
// resolves against the mangled symbol `_Z7computee`.
int main() {
    long double result = compute(21.0L);
    printf("compute(21.0) = %Lf (expected 42.0)\n", result);
    return 0;
}
