/* Witness: a consumer that relies on interposing the library's hook.
 *
 * The executable defines its own hook_point(). Because the library
 * references hook_point, the linker exports the executable's definition,
 * and the dynamic loader searches the executable first -- the same lookup
 * order an LD_PRELOAD shim uses. The consumer's contract is that the
 * library's compute() goes through the installed hook.
 *   v1 (DEFAULT visibility): compute(5) calls the app's hook -> 100*5+1 = 501, exit 0
 *   v2 (PROTECTED):          compute(5) binds to the library's own copy
 *                            -> 2*5+1 = 11, exit 1
 * A consumer that never interposes is unaffected by the same change, which
 * is why the finding is a conditional risk rather than a break. */
#include "old/lib.h"
#include <stdio.h>

int hook_point(int x) { return 100 * x; }

int main(void) {
    int got = compute(5);
    printf("compute(5) = %d (expected 501: the installed hook is used)\n", got);
    if (got != 501) {
        printf("INTERPOSITION LOST: the library no longer calls the installed hook\n");
        return 1;
    }
    return 0;
}
