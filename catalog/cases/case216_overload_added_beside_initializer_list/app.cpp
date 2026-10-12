/* Witness: pick({}) selects the initializer_list overload against v1 and,
   rebuilt against v2, still does (GCC and Clang): prints 2, exit 0 both
   ways. The new pick(double) creates no ambiguity. */
#include "v1.h"
#include <cstdio>

int main() {
    int r = mylib::pick({});
    std::printf("pick({}) = %d (expected 2)\n", r);
    return r == 2 ? 0 : 1;
}
