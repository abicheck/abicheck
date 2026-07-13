#include <cstdio>
#include "v1.h"

// Compiled once against v1's Diamond (two virtual-inheritance legs). If the
// running library was rebuilt with a third leg (v2), any code that embeds a
// Diamond by value, copies one, or allocates one with the app's own
// sizeof(Diamond) is silently using the wrong size — a deterministic,
// checkable mismatch, independent of whether it happens to crash.
int main() {
    unsigned long app_size = sizeof(Diamond);
    unsigned long lib_size = diamond_size();
    printf("app compiled with sizeof(Diamond) = %lu\n", app_size);
    printf("loaded library reports sizeof(Diamond) = %lu\n", lib_size);
    if (app_size != lib_size) {
        printf("MISMATCH: the app's compile-time Diamond layout disagrees "
               "with the library's actual layout.\n");
        return 1;
    }
    printf("layouts agree\n");
    return 0;
}
