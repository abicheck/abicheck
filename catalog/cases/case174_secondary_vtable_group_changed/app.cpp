#include <cstdio>
#include "v1.h"

// Compiled once against v1's Derived (Base2 non-polymorphic, no vtable
// group of its own). Deterministically checks whether the running library
// agrees on sizeof(Derived) -- it will not once Base2 gains a vtable
// pointer underneath an unchanged Derived declaration.
int main() {
    unsigned long app_size = sizeof(Derived);
    unsigned long lib_size = derived_size();
    printf("app compiled with sizeof(Derived) = %lu\n", app_size);
    printf("loaded library reports sizeof(Derived) = %lu\n", lib_size);
    if (app_size != lib_size) {
        printf("MISMATCH: Base2 gained a vtable pointer underneath an "
               "unchanged Derived declaration -- Derived's own diff is a "
               "no-op, yet its layout changed.\n");
        return 1;
    }
    printf("layouts agree\n");
    return 0;
}
