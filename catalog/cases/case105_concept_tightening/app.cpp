// case105 consumer — instantiates `sum<wrapped>` where `wrapped` is a
// type with NO default constructor.
//
// v1: `Addable` requires only `a + b`. `wrapped` satisfies it because
// it overloads `operator+`. The instantiation `sum<wrapped>` compiles.
// (We then link against libv1.so's pre-shipped `sum<int>` for the
// runtime check, since the library only exports the int form.)
//
// v2: `Addable` additionally requires `T()`. `wrapped` has no default
// constructor, so `sum<wrapped>` no longer satisfies the concept and
// the same app.cpp fails to compile against v2.h.
//
// The library's `.so` is unchanged across versions — only consumer
// source breaks.
#include "v1.h"
#include <cstdio>

struct wrapped {
    int v;
    wrapped(int x) : v(x) {}  // intentionally non-default-constructible
    wrapped operator+(const wrapped& other) const { return wrapped(v + other.v); }
};

// Make `wrapped + wrapped → wrapped` available to the concept check.
// (`std::same_as` is in <concepts>, but the same_as constraint is
// satisfied by the operator's return type.)

int main() {
    // *** Source-break demonstration ***
    // Under v1.h, `wrapped` satisfies `Addable` and the next line
    // compiles. Under v2.h, `Addable` additionally requires `T()`
    // and `wrapped` is not default-constructible, so the same line
    // fails to compile:
    //
    //   error: static assertion failed
    //   note: the expression ‘T()’ would be ill-formed
    static_assert(mylib::Addable<wrapped>);

    // Runtime: call the int instantiation that the library exports.
    int r = mylib::sum(2, 3);
    std::printf("sum<int>(2, 3) = %d (expect 5)\n", r);
    return 0;
}
