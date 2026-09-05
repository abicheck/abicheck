#include "old/lib.hpp"   /* MUST use v1's header: fast_hash is inline here */
#include <cstdio>

/* DEMO EXPLANATION:
 * This app is compiled against v1.hpp where fast_hash() is INLINE.
 * The compiler bakes the function body directly into this binary — no symbol needed from .so.
 *
 * With v1.so: works (inline call, no .so symbol needed)
 * With v2.so swapped in: still works (inline call still baked in — COMPAT)
 *
 * The BREAK only affects NEW consumers:
 *   - Code compiled against v2.hpp expects fast_hash to be in libv2.so
 *   - If they try to link against libv1.so => linker error: undefined symbol fast_hash
 * This is a LINK-TIME break for new consumers, not a runtime crash for old ones.
 */
int main() {
    std::printf("fast_hash(42) = %d\n", fast_hash(42));
    return 0;
}
