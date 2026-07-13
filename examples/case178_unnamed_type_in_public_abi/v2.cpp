#include "v2.h"

// A small "policy" helper template -- a common header-only convenience
// pattern. Kept `static` so only the lambda's own call operator (not this
// wrapper) survives to the exported symbol table, isolating the leak this
// case is about.
template <typename Cmp>
static int apply_cmp(int a, int b, Cmp cmp) { return cmp(a, b) ? a : b; }

// A namespace-scope lambda used as a default comparison policy. Because it
// is declared outside any function, its closure type has external linkage
// -- unlike a lambda defined inside a function body, which has no linkage
// at all and could never reach the exported symbol table in the first
// place.
inline auto descending = [](int a, int b) { return a > b; };
template int apply_cmp<decltype(descending)>(int, int, decltype(descending));

int pick_larger(int a, int b) {
    return a > b ? a : b;
}

int pick_by_policy(int a, int b) {
    return apply_cmp(a, b, descending);
}
