#include "lib.h"

/* foo_worker() and its FOO_INTERNAL_1 version node are gone entirely --
 * inlined into public_api(). The public FOO_1.0 node is untouched.
 */
int public_api(int x) { return x * 2 + 1; }
