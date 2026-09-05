// case96 consumer — uses ADL on the hidden friend `operator==`.
//
// Compiles against v1.h. Against v2.h, the `a == b` expression no
// longer has a viable overload (ADL has nothing to find), so the same
// app.cpp fails to compile. The library's .so file is unchanged at the
// binary level — the break is source-only.
#include "v1.h"
#include <cstdio>

int main() {
    mylib::point a(1, 2);
    mylib::point b(1, 2);

    // *** Source-break demonstration ***
    // The next line is what breaks under v2.h: `operator==` was
    // declared as a hidden friend of `point`, found via ADL on the
    // argument types. v2.h removes the friend declaration entirely,
    // so this exact source line fails to compile against v2.h.
    bool eq = (a == b);

    std::printf("a == b → %s (expect true)\n", eq ? "true" : "false");
    return 0;
}
