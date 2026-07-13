#include "v2.h"

// A namespace-scope lambda used as a default comparison policy -- a common
// header-only convenience pattern. Because it is declared outside any
// function, its closure type has external linkage -- unlike a lambda
// defined inside a function body, which has no linkage at all and could
// never reach the exported symbol table in the first place.
//
// Storing it in a plain function-pointer variable (rather than calling it
// through a template, which an optimizing build can fully inline away,
// eliminating the very symbol this case exists to demonstrate) forces the
// compiler to materialize a real, addressable out-of-line function for the
// lambda at every optimization level: its Itanium-mangled "_FUN" static
// invoker (`descending::{lambda(int, int)#1}::_FUN`) survives at -O0 and
// -O2 alike, because its address is observably stored in `descending`.
inline bool (*descending)(int, int) = [](int a, int b) { return a > b; };

int pick_larger(int a, int b) {
    return a > b ? a : b;
}

int pick_by_policy(int a, int b) {
    return descending(a, b) ? a : b;
}
