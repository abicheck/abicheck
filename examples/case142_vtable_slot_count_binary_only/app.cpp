// Consumer compiled against the v1 header. It calls `perimeter()` through a
// Shape*. The compiler emits a virtual call through a *fixed vtable slot index*
// (slot 1 under v1). Insert `rotate()` ahead of `perimeter()` in v2 and slot 1
// now holds `rotate()`, so the same binary silently misdispatches.
#include "v1.h"
#include <cstdio>

int main() {
    Shape* s = make_shape();

    int perimeter = s->perimeter();  // v1: slot 1 -> perimeter() -> 20
    std::printf("perimeter() = %d (expected 20)\n", perimeter);

    delete s;

    if (perimeter != 20) {
        std::printf("MISDISPATCH: vtable slot order changed\n");
        return 1;
    }
    return 0;
}
