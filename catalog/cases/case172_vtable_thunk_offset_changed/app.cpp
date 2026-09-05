#include <cstdio>
#include "v1.h"

// Compiled once against v1's layout (Base2 at offset 16 within Derived). The
// static_cast below is NOT a virtual operation — the compiler bakes the
// Base1->Base2 this-adjustment in as a compile-time constant (16) directly
// into this translation unit's machine code, exactly the offset the v1
// thunk uses.
int main() {
    Derived* d = make_derived();
    Base2* b2 = static_cast<Base2*>(d);
    printf("f2() via Base2* = %d (expected 20)\n", b2->f2());
    delete d;
    return 0;
}
